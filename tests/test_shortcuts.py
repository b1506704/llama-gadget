from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QSettings, Qt, QTimer
from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QListWidget, QVBoxLayout, QWidget

from llama_gadget import profiles, shortcuts, theme
from llama_gadget.app import Gadget
from llama_gadget.shortcuts import (CONTEXTUAL, GLOBAL, PRIMARY_ACTIONS, ActionRegistry, CommandSpec,
                                    ShortcutCheatSheetDialog, coverage, install_list_wrap, format_shortcut_hint, register_primary_actions,
                                    resolve_key_sequence)

APPLICATION = QApplication.instance() or QApplication([])


def pump(seconds=0.05):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        APPLICATION.processEvents()
        time.sleep(.005)


def show_and_focus(window, widget=None):
    window.show()
    window.activateWindow()
    QTest.qWaitForWindowExposed(window)
    if widget is not None:
        widget.setFocus()
    pump()


def when_modal(callback):
    """Run ``callback(dialog)`` once a modal dialog is showing (exec() blocks the test otherwise)."""
    def poll():
        dialog = QApplication.activeModalWidget()
        if dialog is None or not dialog.isActiveWindow():
            QTimer.singleShot(10, poll)
        else:
            callback(dialog)
    QTimer.singleShot(0, poll)


class GadgetCase(unittest.TestCase):
    """Builds a real Gadget against a temp settings file and database."""

    def make_gadget(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
        settings.setValue('db_path', str(root / 'chat.db'))
        with patch('llama_gadget.app.QSettings', return_value=settings), patch.object(Gadget, '_poll'):
            gadget = Gadget()
        gadget.timer.stop()
        self.addCleanup(self._cleanup, gadget)
        return gadget

    @staticmethod
    def _cleanup(gadget):
        gadget._shutdown_workers()
        gadget.tray.hide()
        gadget.clipboard.dataChanged.disconnect(gadget._on_clipboard)
        gadget.db.conn.close()
        gadget.hide()
        gadget.deleteLater()
        APPLICATION.processEvents()


class RegistryTests(unittest.TestCase):
    def test_register_get_unregister_and_categories(self):
        reg = ActionRegistry()
        reg.register(CommandSpec('a.one', 'One', 'Alpha', 'Cmd+1'))
        reg.register(CommandSpec('b.two', 'Two', 'Beta', 'Cmd+2'))
        reg.register(CommandSpec('a.three', 'Three', 'Alpha'))
        self.assertEqual(reg.get('b.two').title, 'Two')
        self.assertEqual(reg.categories(), ['Alpha', 'Beta'])
        self.assertEqual([s.id for s in reg.list_by_category('Alpha')], ['a.one', 'a.three'])
        self.assertEqual(list(reg.list_by_category()), ['Alpha', 'Beta'])
        reg.unregister('a.one')
        self.assertIsNone(reg.get('a.one'))
        self.assertEqual(len(reg.get_all()), 2)

    def test_duplicate_ids_and_bad_specs_are_rejected(self):
        reg = ActionRegistry()
        reg.register(CommandSpec('x', 'X', 'Cat'))
        with self.assertRaises(ValueError):
            reg.register(CommandSpec('x', 'Other', 'Cat'))
        with self.assertRaises(ValueError):
            CommandSpec('', 'No id', 'Cat')
        with self.assertRaises(ValueError):
            CommandSpec('y', 'Bad chord', 'Cat', 'Hyper+K')

    def test_dispatch_calls_handler(self):
        handler = MagicMock()
        reg = ActionRegistry()
        reg.register(CommandSpec('go', 'Go', 'Cat', 'Cmd+G', handler=handler))
        reg.dispatch('go')
        handler.assert_called_once_with()
        with self.assertRaises(KeyError):
            reg.dispatch('missing')


class PlatformChordTests(unittest.TestCase):
    def test_cmd_resolves_to_qt_primary_modifier(self):
        # Qt's CTRL is the Command key on macOS and Control on Windows/Linux.
        seq = resolve_key_sequence('Cmd+Shift+K')
        combo = seq[0]
        self.assertEqual(combo.key(), Qt.Key.Key_K)
        self.assertEqual(combo.keyboardModifiers(),
                         Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(seq, QKeySequence('Ctrl+Shift+K'))
        self.assertEqual(shortcuts.PRIMARY_MODIFIER, Qt.Modifier.CTRL)

    def test_special_keys_resolve(self):
        self.assertEqual(resolve_key_sequence('Cmd+,'), QKeySequence('Ctrl+,'))
        self.assertEqual(resolve_key_sequence('Cmd+/'), QKeySequence('Ctrl+/'))
        self.assertEqual(resolve_key_sequence('Cmd+Return')[0].key(), Qt.Key.Key_Return)
        self.assertEqual(resolve_key_sequence('Cmd+Alt+T'), QKeySequence('Ctrl+Alt+T'))

    def test_hints_are_platform_aware(self):
        self.assertEqual(format_shortcut_hint('Cmd+Shift+K', 'darwin'), 'Cmd+Shift+K')
        for platform in ('linux', 'win32'):
            self.assertEqual(format_shortcut_hint('Cmd+Shift+K', platform), 'Ctrl+Shift+K')
        self.assertEqual(format_shortcut_hint('Cmd+Alt+T', 'darwin'), 'Cmd+Option+T')
        reg = ActionRegistry()
        reg.register(CommandSpec('t', 'T', 'Cat', 'Cmd+Shift+T', alt_chords=('Cmd+Alt+T',)))
        self.assertEqual(reg.hint('t', 'linux'), 'Ctrl+Shift+T / Ctrl+Alt+T')

    def test_bind_shortcuts_uses_configured_context(self):
        handler = MagicMock()
        reg = ActionRegistry()
        reg.register(CommandSpec('g', 'G', 'Cat', 'Cmd+G', handler=handler))
        reg.register(CommandSpec('c', 'C', 'Cat', 'Cmd+J', CONTEXTUAL, handler=handler, scope='chat'))
        reg.register(CommandSpec('n', 'No handler', 'Cat', 'Cmd+H'))
        widget = QWidget()
        self.addCleanup(widget.deleteLater)
        bound = reg.bind_shortcuts(widget)
        self.assertEqual([(s.objectName(), s.context()) for s in bound], [('g', GLOBAL)])
        chat = reg.bind_shortcuts(widget, scope='chat')
        self.assertEqual([(s.objectName(), s.context()) for s in chat], [('c', CONTEXTUAL)])
        bound[0].activated.emit()
        handler.assert_called_once_with()


class PrimaryActionTests(GadgetCase):
    def test_catalog_covers_primary_actions_with_mock_receivers(self):
        gadget, controller = MagicMock(), MagicMock()
        reg = register_primary_actions(ActionRegistry(), gadget, controller)
        self.assertGreaterEqual(coverage(reg), 0.9)
        self.assertGreaterEqual(len(PRIMARY_ACTIONS), 10)
        for spec in reg:
            self.assertTrue(spec.chords, spec.id)
            spec.handler()
        gadget.cycle_model.assert_any_call(1)
        gadget.cycle_model.assert_any_call(-1)
        gadget.new_session.assert_called()
        gadget.clear_chat.assert_called()
        gadget.focus_prompt.assert_called()
        gadget.copy_last_response.assert_called()
        gadget.toggle_tool_panel.assert_called()
        gadget.toggle_sidebar.assert_called()
        gadget.show_tab.assert_any_call(2)
        gadget.focus_parameters.assert_called()
        controller.toggle.assert_called_once_with()

    def test_brief_chords_and_contexts(self):
        reg = register_primary_actions(ActionRegistry(), MagicMock(), MagicMock())
        expect = {'model.next': ('Cmd+M',), 'model.prev': ('Cmd+Shift+M',), 'panels.toggle_sidebar': ('Cmd+B',),
                  'settings.open': ('Cmd+,', 'Cmd+3'), 'tools.toggle_panel': ('Cmd+Shift+T', 'Cmd+Alt+T'),
                  'tools.run_active': ('Cmd+Return',), 'tools.clear_session': ('Cmd+Shift+K',),
                  'theme.toggle': ('Cmd+Shift+D',), 'params.focus': ('Cmd+Shift+P',)}
        for action_id, chords in expect.items():
            self.assertEqual(reg.get(action_id).chords, chords, action_id)
        self.assertEqual(reg.get('tools.run_active').context, CONTEXTUAL)
        self.assertEqual(reg.get('tools.run_active').scope, 'tools')
        self.assertEqual(reg.get('chat.clear_session').context, CONTEXTUAL)
        self.assertEqual(reg.get('model.next').context, GLOBAL)
        # Every chord is unique so no shortcut is ambiguous.
        seqs = [resolve_key_sequence(c).toString() for s in reg for c in s.chords]
        self.assertEqual(len(seqs), len(set(seqs)))

    def test_gadget_wires_handlers(self):
        app = self.make_gadget()
        self.assertGreaterEqual(coverage(app.actions), 0.9)
        names = {s.objectName() for s in app._shortcuts}
        self.assertIn('model.next', names)
        self.assertIn('chat.clear_session', names)

        start = app.profile_cb.currentIndex()
        with patch.object(app, '_poll'):
            app.actions.dispatch('model.next')
            self.assertEqual(app.profile_cb.currentIndex(), (start + 1) % app.profile_cb.count())
            app.actions.dispatch('model.prev')
            self.assertEqual(app.profile_cb.currentIndex(), start)

        visible = app.sidebar.isVisibleTo(app)
        app.actions.dispatch('panels.toggle_sidebar')
        self.assertNotEqual(app.sidebar.isVisibleTo(app), visible)

        app.actions.dispatch('settings.open')
        self.assertEqual(app.tabs.currentIndex(), 2)

        app.msgs = [{'role': 'assistant', 'content': 'answer text'}]
        app.actions.dispatch('chat.copy_last_response')
        self.assertEqual(QApplication.clipboard().text(), 'answer text')

        app.input.setPlainText('draft')
        app.actions.dispatch('chat.clear_session')
        self.assertEqual(app.msgs, [])
        self.assertEqual(app.input.toPlainText(), '')

        mode = app.theme.mode
        app.actions.dispatch('theme.toggle')
        self.assertNotEqual(app.theme.mode, mode)
        self.assertIn(app.theme.tokens.window, app.styleSheet())
        app.actions.dispatch('theme.toggle')
        self.assertEqual(app.theme.mode, mode)

        with patch.object(app, '_show_tools_inspector') as show:
            app.actions.dispatch('tools.toggle_panel')
            show.assert_called_once_with()

    def test_shortcut_keypress_dispatches_and_context_isolation(self):
        app = self.make_gadget()
        show_and_focus(app, app.input)
        app.actions.dispatch('nav.history')
        self.assertEqual(app.tabs.currentIndex(), 1)
        app.input.setFocus()
        pump()
        QTest.keyClick(app.input, Qt.Key.Key_I, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(app.tabs.currentIndex(), 0)
        self.assertIs(QApplication.focusWidget(), app.input)

        # Contextual chat shortcut fires inside the chat view…
        app.msgs = [{'role': 'assistant', 'content': 'x'}]
        QTest.keyClick(app.input, Qt.Key.Key_X,
                       Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(app.msgs, [])
        # …but not when focus is in another view.
        app.show_tab(2)
        app.user_edit.setFocus()
        pump()
        app.msgs = [{'role': 'assistant', 'content': 'x'}]
        QTest.keyClick(app.user_edit, Qt.Key.Key_X,
                       Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(len(app.msgs), 1)


class ThemeTests(unittest.TestCase):
    def test_controller_toggles_and_persists(self):
        with TemporaryDirectory() as tmp:
            settings = QSettings(str(Path(tmp) / 's.ini'), QSettings.Format.IniFormat)
            ctl = theme.ThemeController(settings)
            seen = []
            ctl.changed.connect(seen.append)
            self.assertEqual(ctl.mode, 'dark')
            ctl.toggle()
            self.assertEqual(ctl.mode, 'light')
            self.assertEqual(seen, [theme.LIGHT])
            self.assertEqual(theme.ThemeController(settings).mode, 'light')
            with self.assertRaises(ValueError):
                ctl.set_mode('neon')

    def test_text_tokens_meet_wcag_aa(self):
        for t in theme.THEMES.values():
            for fg, bg in ((t.text, t.window), (t.text_strong, t.surface), (t.text_muted, t.window),
                           (t.text_muted, t.surface), (t.badge_text, t.badge), (t.selection_text, t.selection),
                           (t.text_strong, t.raised)):
                self.assertGreaterEqual(theme.contrast_ratio(fg, bg), 4.5, (t.name, fg, bg))


class CheatSheetTests(GadgetCase):
    def registry(self):
        reg = ActionRegistry()
        reg.register(CommandSpec('a', 'Alpha one', 'First', 'Cmd+1', handler=MagicMock()))
        reg.register(CommandSpec('b', 'Beta', 'Second', 'Cmd+Shift+B', hint_extra='?'))
        reg.register(CommandSpec('c', 'Alpha two', 'First'))
        return reg

    def test_groups_match_registry(self):
        dlg = ShortcutCheatSheetDialog(self.registry(), theme.DARK, platform='darwin')
        self.addCleanup(dlg.deleteLater)
        self.assertEqual(dlg.groups(), {'First': [('Alpha one', 'Cmd+1'), ('Alpha two', '—')],
                                        'Second': [('Beta', 'Cmd+Shift+B / ?')]})
        linux = ShortcutCheatSheetDialog(self.registry(), theme.LIGHT, platform='linux')
        self.addCleanup(linux.deleteLater)
        self.assertEqual(linux.groups()['Second'], [('Beta', 'Ctrl+Shift+B / ?')])

    def test_gadget_cheat_sheet_lists_every_action(self):
        app = self.make_gadget()
        dlg = ShortcutCheatSheetDialog(app.actions, app.theme.tokens, app)
        self.addCleanup(dlg.deleteLater)
        groups = dlg.groups()
        self.assertEqual(list(groups), app.actions.categories())
        self.assertEqual(sum(map(len, groups.values())), len(app.actions))
        self.assertIn(('Keyboard shortcuts', 'Ctrl+/ / ?' if sys.platform != 'darwin' else 'Cmd+/ / ?'),
                      groups['General'])

    def test_focus_is_trapped_and_esc_restores_focus(self):
        host = QWidget()
        self.addCleanup(host.deleteLater)
        QVBoxLayout(host).addWidget(before := QLineEdit())
        show_and_focus(host, before)
        dlg = ShortcutCheatSheetDialog(self.registry(), theme.DARK, host)
        self.addCleanup(dlg.deleteLater)
        seen = []

        def drive(d):
            seen.append(QApplication.focusWidget())
            for _ in range(4):
                QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
                seen.append(QApplication.focusWidget())
            QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
            seen.append(QApplication.focusWidget())
            QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
        when_modal(drive)
        dlg.exec_and_restore_focus()
        self.assertEqual(seen, [dlg.tree, dlg.close_btn, dlg.tree, dlg.close_btn, dlg.tree, dlg.close_btn])
        pump()
        self.assertIs(QApplication.focusWidget(), before)

    def test_ctrl_slash_and_question_mark_triggers(self):
        app = self.make_gadget()
        show_and_focus(app, app.input)
        opened = []

        def close(d):
            opened.append((type(d).__name__, d))
            QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)

        # Ctrl+/ works even from the prompt text input.
        when_modal(close)
        QTest.keyClick(app.input, Qt.Key.Key_Slash, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual([name for name, _ in opened], ['ShortcutCheatSheetDialog'])
        pump()
        self.assertIs(QApplication.focusWidget(), app.input)

        # '?' inside a text input is just a character.
        QTest.keyClick(app.input, Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(app.input.toPlainText(), '?')
        app.show_tab(2)
        app.user_edit.setFocus()
        pump()
        QTest.keyClicks(app.user_edit, '?')
        self.assertTrue(app.user_edit.text().endswith('?'))
        self.assertEqual(len(opened), 1)

        # '?' on a non-text control opens the cheat sheet and focus returns there.
        app.show_tab(1)
        app.hist_list.setFocus()
        pump()
        when_modal(close)
        QTest.keyClick(app.hist_list, Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(len(opened), 2)
        pump()
        self.assertIs(QApplication.focusWidget(), app.hist_list)


class FocusNavigationTests(GadgetCase):
    def test_tab_order_follows_visual_layout(self):
        app = self.make_gadget()
        app.workspace = '/tmp'
        app.profile_cb.blockSignals(True)
        app.profile_cb.setCurrentIndex(app.profile_cb.findData('agent-gpu0'))
        app.profile_cb.blockSignals(False)
        app.profile = profiles.PROFILES['agent-gpu0']
        app._update_controls()
        show_and_focus(app, app.power_btn)
        expected = [w for w in [app.power_btn, app.close_btn, app.profile_cb, app.edit_ep_btn, app.gadget_cb,
                                app.tabs.tabBar()] + [w for c in app._transcript_cards() for w in (c.copy_btn, c.body)]
                    + app._chat_chain
                    if w.isVisible() and w.isEnabled() and w.focusPolicy() & Qt.FocusPolicy.TabFocus]
        self.assertIn(app.input, expected)
        self.assertIn(app.tools_cb, expected)
        seen = [QApplication.focusWidget()]
        for _ in range(len(expected) - 1):
            QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
            seen.append(QApplication.focusWidget())
        self.assertEqual(seen, expected)
        # Shift+Tab walks the same chain backwards.
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
        self.assertIs(QApplication.focusWidget(), expected[-2])

    def test_settings_tab_order(self):
        app = self.make_gadget()
        show_and_focus(app)
        app.show_tab(2)
        pump()
        self.assertIs(QApplication.focusWidget(), app.user_edit)
        seen = [app.user_edit]
        for _ in range(len(app._settings_chain) - 1):
            QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
            seen.append(QApplication.focusWidget())
        self.assertEqual(seen, app._settings_chain)

    def test_list_navigation_wraps(self):
        lst = QListWidget()
        self.addCleanup(lst.deleteLater)
        lst.addItems(['one', 'two', 'three'])
        install_list_wrap(lst)
        lst.setCurrentRow(2)
        QTest.keyClick(lst, Qt.Key.Key_Down)
        self.assertEqual(lst.currentRow(), 0)
        QTest.keyClick(lst, Qt.Key.Key_Up)
        self.assertEqual(lst.currentRow(), 2)
        QTest.keyClick(lst, Qt.Key.Key_Up)
        self.assertEqual(lst.currentRow(), 1)

    def test_history_list_wraps(self):
        app = self.make_gadget()
        for title in ('a', 'b'):
            app.db.create_session(app.user, title)
        app._refresh_history()
        app.hist_list.setCurrentRow(app.hist_list.count() - 1)
        QTest.keyClick(app.hist_list, Qt.Key.Key_Down)
        self.assertEqual(app.hist_list.currentRow(), 0)


if __name__ == '__main__':
    unittest.main()
