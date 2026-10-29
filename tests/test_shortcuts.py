from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QWidget

from llama_gadget import shortcuts, theme
from llama_gadget.app import Gadget
from llama_gadget.shortcuts import (CONTEXTUAL, GLOBAL, PRIMARY_ACTIONS, ActionRegistry, CommandSpec,
                                    coverage, format_shortcut_hint, register_primary_actions,
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


if __name__ == '__main__':
    unittest.main()
