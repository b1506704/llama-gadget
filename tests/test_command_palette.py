import time
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from llama_gadget import theme
from llama_gadget.command_palette import (CommandPaletteDialog, FuzzyMatcher, MRUHistoryTracker, PaletteItem,
                                          fuzzy_score)
from tests.test_shortcuts import GadgetCase, pump, show_and_focus

APPLICATION = QApplication.instance() or QApplication([])


def items(*specs):
    return [PaletteItem(i, title, cat, hint, MagicMock()) for i, title, cat, hint in specs]


SAMPLE = items(('chat.new', 'New chat session', 'Chat', 'Ctrl+N'),
               ('theme.toggle', 'Toggle dark / light theme', 'Theme', 'Ctrl+Shift+D'),
               ('settings.open', 'Open settings', 'Navigation', 'Ctrl+,'),
               ('model:vision', 'Switch model: Image analysis', 'Models', ''),
               ('tools.panel', 'Toggle tool panel', 'Tools', 'Ctrl+Shift+T'))


def when_modal(callback):
    """Run ``callback(dialog)`` once a modal dialog is showing (exec() blocks the test otherwise)."""
    def poll():
        dialog = QApplication.activeModalWidget()
        if dialog is None or not dialog.isActiveWindow():
            QTimer.singleShot(10, poll)
        else:
            callback(dialog)
    QTimer.singleShot(0, poll)


class MatcherTests(unittest.TestCase):
    def test_scores_subsequences_and_rejects_non_matches(self):
        self.assertIsNotNone(fuzzy_score('nws', 'new chat session'))
        self.assertIsNone(fuzzy_score('xyz', 'new chat session'))
        self.assertGreater(fuzzy_score('new', 'new chat session'), fuzzy_score('nws', 'new chat session'))

    def test_ranks_best_match_first_and_filters(self):
        matcher = FuzzyMatcher()
        self.assertEqual([i.id for i in matcher.match('theme', SAMPLE)], ['theme.toggle'])
        ranked = [i.id for i in matcher.match('toggle', SAMPLE)]
        self.assertEqual(set(ranked), {'theme.toggle', 'tools.panel'})
        self.assertEqual(matcher.match('qqq', SAMPLE), [])
        # Category text is searchable too, and model names are matched.
        self.assertEqual([i.id for i in matcher.match('navigation', SAMPLE)], ['settings.open'])
        self.assertEqual(matcher.match('image', SAMPLE)[0].id, 'model:vision')

    def test_empty_query_lists_recent_first(self):
        mru = MRUHistoryTracker(limit=3)
        for item_id in ('settings.open', 'tools.panel', 'settings.open'):
            mru.record_execution(item_id)
        self.assertEqual(mru.get_recent_items(), ['settings.open', 'tools.panel'])
        ordered = [i.id for i in FuzzyMatcher(mru).match('', SAMPLE)]
        self.assertEqual(ordered[:2], ['settings.open', 'tools.panel'])
        self.assertEqual(len(ordered), len(SAMPLE))
        for item_id in ('a', 'b', 'c', 'd'):
            mru.record_execution(item_id)
        self.assertEqual(mru.get_recent_items(), ['d', 'c', 'b'])

    def test_latency_under_two_ms(self):
        catalog = [PaletteItem(f'id{i}', f'Command number {i} {word}', cat, 'Ctrl+K')
                   for i, (word, cat) in enumerate([('toggle', 'View'), ('switch', 'Models'),
                                                    ('focus', 'Chat'), ('open', 'Navigation')] * 40)]
        matcher = FuzzyMatcher(MRUHistoryTracker())
        for query in ('tog', 'swmod', 'command 12', 'nav'):
            best = min(self._time(matcher, query, catalog) for _ in range(5))
            self.assertLess(best, 0.002, query)

    @staticmethod
    def _time(matcher, query, catalog):
        start = time.perf_counter()
        matcher.match(query, catalog)
        return time.perf_counter() - start


class DialogTests(unittest.TestCase):
    def setUp(self):
        self.host = QWidget()
        lay = QVBoxLayout(self.host)
        self.before = QLineEdit()
        lay.addWidget(self.before)
        self.addCleanup(self.host.deleteLater)
        show_and_focus(self.host, self.before)

    def palette(self, mru=None):
        sample = items(*[(i.id, i.title, i.category, i.hint) for i in SAMPLE])
        return CommandPaletteDialog(sample, theme.LIGHT, mru, self.host), sample

    def test_typing_filters_and_shows_empty_state(self):
        dlg, _ = self.palette()
        self.addCleanup(dlg.deleteLater)
        dlg.show()
        self.assertEqual(dlg.model.rowCount(), len(SAMPLE))
        self.assertEqual(dlg.list.currentIndex().row(), 0)
        QTest.keyClicks(dlg.search, 'theme')
        self.assertEqual([i.id for i in dlg.results], ['theme.toggle'])
        QTest.keyClicks(dlg.search, 'zzz')
        self.assertTrue(dlg.empty.isVisibleTo(dlg))
        self.assertFalse(dlg.list.isVisibleTo(dlg))
        # Enter on no results does nothing and keeps the dialog open.
        QTest.keyClick(dlg.search, Qt.Key.Key_Return)
        self.assertIsNone(dlg.chosen)

    def test_arrows_wrap_around(self):
        dlg, _ = self.palette()
        self.addCleanup(dlg.deleteLater)
        QTest.keyClick(dlg.search, Qt.Key.Key_Up)
        self.assertEqual(dlg.list.currentIndex().row(), len(SAMPLE) - 1)
        QTest.keyClick(dlg.search, Qt.Key.Key_Down)
        self.assertEqual(dlg.list.currentIndex().row(), 0)
        QTest.keyClick(dlg.search, Qt.Key.Key_Down)
        self.assertEqual(dlg.list.currentIndex().row(), 1)

    def test_enter_executes_highlighted_item_and_restores_focus(self):
        mru = MRUHistoryTracker()
        dlg, sample = self.palette(mru)
        self.addCleanup(dlg.deleteLater)
        seen = {}

        def drive(d):
            seen['focus'] = QApplication.focusWidget()
            QTest.keyClicks(d.search, 'settings')
            QTest.keyClick(d.search, Qt.Key.Key_Return)
        when_modal(drive)
        chosen = dlg.exec_and_restore_focus(self.host)
        self.assertIs(seen['focus'], dlg.search)
        self.assertEqual(chosen.id, 'settings.open')
        sample[2].run.assert_called_once_with()
        self.assertEqual(mru.get_recent_items(), ['settings.open'])
        pump()
        self.assertIs(QApplication.focusWidget(), self.before)

    def test_escape_dismisses_without_running(self):
        dlg, sample = self.palette()
        self.addCleanup(dlg.deleteLater)
        when_modal(lambda d: QTest.keyClick(d.search, Qt.Key.Key_Escape))
        self.assertIsNone(dlg.exec_and_restore_focus(self.host))
        for item in sample:
            item.run.assert_not_called()
        pump()
        self.assertIs(QApplication.focusWidget(), self.before)


class GadgetPaletteTests(GadgetCase):
    def test_ctrl_k_opens_palette_from_prompt_and_restores_focus(self):
        app = self.make_gadget()
        show_and_focus(app, app.input)
        seen = {}

        def drive(d):
            seen['dialog'] = d
            seen['focus'] = QApplication.focusWidget()
            seen['titles'] = [i.title for i in d.results]
            QTest.keyClick(d.search, Qt.Key.Key_Escape)
        when_modal(drive)
        QTest.keyClick(app.input, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
        self.assertIsInstance(seen.get('dialog'), CommandPaletteDialog)
        self.assertIs(seen['focus'], seen['dialog'].search)
        self.assertIn('Go to History', seen['titles'])
        self.assertTrue(any(t.startswith('Switch model:') for t in seen['titles']))
        self.assertNotIn('Command palette', seen['titles'])
        pump()
        self.assertIs(QApplication.focusWidget(), app.input)

    def test_ctrl_k_wins_over_line_edit_bindings(self):
        app = self.make_gadget()
        show_and_focus(app)
        app.show_tab(2)
        app.user_edit.setFocus()
        pump()
        seen = []
        when_modal(lambda d: (seen.append(d), QTest.keyClick(d.search, Qt.Key.Key_Escape)))
        QTest.keyClick(app.user_edit, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(seen), 1)
        pump()
        self.assertIs(QApplication.focusWidget(), app.user_edit)

    def test_palette_runs_navigation_and_model_switch(self):
        app = self.make_gadget()
        show_and_focus(app, app.input)
        when_modal(lambda d: (QTest.keyClicks(d.search, 'go to history'), QTest.keyClick(d.search, Qt.Key.Key_Return)))
        chosen = app.open_command_palette()
        self.assertEqual(chosen.id, 'nav.history')
        self.assertEqual(app.tabs.currentIndex(), 1)
        self.assertEqual(app.palette_mru.get_recent_items(), ['nav.history'])

        when_modal(lambda d: (QTest.keyClicks(d.search, 'image analysis'), QTest.keyClick(d.search, Qt.Key.Key_Return)))
        with patch.object(app, '_poll'):
            app.open_command_palette()
        self.assertEqual(app.profile.key, 'vision')

        # Recently used commands are listed first on an empty query.
        when_modal(lambda d: (setattr(self, 'first', d.results[0].id), QTest.keyClick(d.search, Qt.Key.Key_Escape)))
        app.open_command_palette()
        self.assertEqual(self.first, 'model:vision')


if __name__ == '__main__':
    unittest.main()
