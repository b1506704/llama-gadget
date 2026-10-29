"""Screens and reusable widgets restyle from ThemeTokens in place on a Dark <-> Light switch."""
import gc
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QDialog, QLabel

from llama_gadget import theme
from llama_gadget.app import FolderDialog, Gadget
from llama_gadget.chat_ui import ChatTranscript, ImagePreviewDialog
from llama_gadget.theme import DARK_TOKENS, LIGHT_TOKENS, ThemeAdapter
from llama_gadget.tool_ui import MCPConfigDialog

APP = QApplication.instance() or QApplication([])
DARK, LIGHT = DARK_TOKENS.colors, LIGHT_TOKENS.colors


def activate(tokens):
    theme.set_active_adapter(ThemeAdapter(tokens))


class ThemeStateMixin:
    def keep_theme_state(self):
        self.addCleanup(theme.set_active_adapter, theme.active_adapter())
        self.addCleanup(APP.setStyleSheet, APP.styleSheet())
        self.addCleanup(APP.setPalette, APP.palette())


class BindThemeTests(ThemeStateMixin, unittest.TestCase):
    def setUp(self):
        self.keep_theme_state()
        activate(DARK_TOKENS)

    def test_bound_widget_follows_and_dead_widget_unbinds(self):
        seen = []

        class Probe(QLabel):
            def apply_theme(self, adapter):
                seen.append(adapter.tokens)

        probe = Probe()
        theme.bind_theme(probe.apply_theme)
        activate(LIGHT_TOKENS)
        self.assertEqual(seen, [DARK_TOKENS, LIGHT_TOKENS])
        del probe
        gc.collect()
        activate(DARK_TOKENS)  # the dead binding disconnects instead of raising
        self.assertEqual(seen, [DARK_TOKENS, LIGHT_TOKENS])
        with self.assertRaises(TypeError):
            theme.bind_theme(lambda adapter: None)


class ReusableWidgetTests(ThemeStateMixin, unittest.TestCase):
    def setUp(self):
        self.keep_theme_state()
        activate(DARK_TOKENS)

    def test_transcript_surface_switches_and_keeps_cards(self):
        view = ChatTranscript()
        self.addCleanup(view.deleteLater)
        view.set_messages([{'id': 1, 'role': 'user', 'content': 'hello'}])
        cards = dict(view.cards)
        self.assertIn(DARK.surface_primary, view.styleSheet())
        self.assertEqual(view.column.contentsMargins().left(), DARK_TOKENS.spacing.xxs)
        activate(LIGHT_TOKENS)
        self.assertIn(LIGHT.surface_primary, view.styleSheet())
        self.assertNotIn(DARK.surface_primary, view.styleSheet())
        self.assertEqual(view.cards, cards)
        self.assertIn('hello', view.toPlainText())

    def test_mcp_status_label_uses_status_tokens_and_keeps_editor(self):
        with TemporaryDirectory() as temp:
            dialog = MCPConfigDialog(Path(temp) / 'mcp.json', current_config={'servers': {}})
            self.addCleanup(dialog.deleteLater)
            dialog.editor.setPlainText('{not json')
            self.assertFalse(dialog._validate())
            self.assertIn(f'color: {DARK.status_error};', dialog.status_lbl.styleSheet())
            activate(LIGHT_TOKENS)
            self.assertIn(f'color: {LIGHT.status_error};', dialog.status_lbl.styleSheet())
            self.assertEqual(dialog.editor.toPlainText(), '{not json')
            self.assertTrue(dialog.status_lbl.text().startswith('Error:'))
            dialog._insert_example()
            self.assertIn(f'color: {LIGHT.text_muted};', dialog.status_lbl.styleSheet())
            dialog.set_active_turn(True)
            activate(DARK_TOKENS)
            self.assertIn(f'color: {DARK.text_muted};', dialog.status_lbl.styleSheet())
            self.assertIn('Turn active', dialog.status_lbl.text())

    def test_folder_dialog_budget_color_follows_theme(self):
        files = [{'rel': 'a.md', 'chars': 10, 'truncated': False},
                 {'rel': 'b.md', 'chars': 50, 'truncated': False}]
        dialog = FolderDialog(None, '/tmp/project', files, 2, budget=20)
        self.addCleanup(dialog.deleteLater)
        self.assertIn(DARK.status_success, dialog.info.text())
        dialog._set_all(Qt.CheckState.Checked)
        self.assertIn(DARK.status_error, dialog.info.text())
        activate(LIGHT_TOKENS)
        self.assertIn(LIGHT.status_error, dialog.info.text())
        self.assertEqual(len(dialog.selected()), 2)

    def test_dialogs_have_no_local_color_overrides(self):
        image = {'kind': 'image', 'name': 'x.jpg', 'data': 'data:image/jpeg;base64,'}
        with patch('llama_gadget.chat_ui.decode_attachment_image') as decode:
            decode.return_value = QImage(4, 4, QImage.Format.Format_RGB32)
            dialog = ImagePreviewDialog(image)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.styleSheet(), '')  # app-wide token stylesheet styles it


class GadgetThemeSwitchTests(ThemeStateMixin, unittest.TestCase):
    def setUp(self):
        self.keep_theme_state()
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.prefs = self.root / 'preferences.json'
        self.prefs.write_text(json.dumps({'theme': 'dark'}))
        settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        settings.setValue('db_path', str(self.root / 'chat.db'))
        with patch('llama_gadget.app.QSettings', return_value=settings), \
                patch('llama_gadget.app.default_preferences_path', return_value=self.prefs), \
                patch.object(Gadget, '_poll'):
            self.app = Gadget()
        self.app.timer.stop()
        self.addCleanup(self.cleanup_app)

    def cleanup_app(self):
        self.app._shutdown_workers()
        self.app.tray.hide()
        self.app.clipboard.dataChanged.disconnect(self.app._on_clipboard)
        self.app.db.conn.close()
        self.app.hide()
        self.app.deleteLater()
        APP.processEvents()

    def select(self, text):
        index = self.app.theme_cb.findText(text)
        self.app.theme_cb.setCurrentIndex(index)
        self.app.theme_cb.activated.emit(index)

    def tray_color(self):
        return self.app.tray.icon().pixmap(64, 64).toImage().pixelColor(32, 32).name()

    def assert_palette(self, tokens):
        c = tokens.colors
        self.assertIs(theme.active_tokens(), tokens)
        for lbl in (self.app.endpoint_lbl, self.app.db_footer, self.app.stats_lbl, self.app.gpu_lbl,
                    self.app.prompt_msg, self.app.endpoint_msg):
            self.assertIn(f'color: {c.text_muted};', lbl.styleSheet())
            self.assertIn(f'font-size: {tokens.typography.caption.size}px', lbl.styleSheet())
        self.assertIn(f'background: {c.accent};', self.app.scroll_bottom_btn.styleSheet())
        self.assertIn(f'color: {c.status_error};', self.app.dot.styleSheet())
        self.assertIn(f'font-size: {tokens.typography.heading.size}px', self.app.dot.styleSheet())
        self.assertEqual(self.tray_color(), c.status_error)
        self.assertIn(f'color:{c.status_success}', self.app.db_status.text())
        self.assertIn(c.surface_primary, self.app.view.styleSheet())
        self.assertEqual(self.app.styleSheet(), '')

    def test_startup_renders_dark_tokens(self):
        self.assert_palette(DARK_TOKENS)
        self.assertEqual(self.app.tabs.widget(0).layout().contentsMargins().top(), DARK_TOKENS.spacing.xxs)

    def test_switch_restyles_open_screens_in_place_and_keeps_state(self):
        self.app.msgs = [{'role': 'user', 'content': 'hello', 'attachments': []}]
        self.app.session_id = 7
        self.app.input.setPlainText('draft')
        self.app.prompt_edit.setPlainText('unsaved prompt')
        view, dot = self.app.view, self.app.dot
        self.app._set_status('loading')
        self.assertIn(f'color: {DARK.status_warning};', dot.styleSheet())
        self.app._set_status('offline')

        self.select('Light')
        self.assert_palette(LIGHT_TOKENS)
        self.assertEqual(json.loads(self.prefs.read_text()), {'theme': 'light'})
        self.assertIs(self.app.view, view)
        self.assertEqual(self.app.msgs, [{'role': 'user', 'content': 'hello', 'attachments': []}])
        self.assertEqual(self.app.session_id, 7)
        self.assertEqual(self.app.input.toPlainText(), 'draft')
        self.assertEqual(self.app.prompt_edit.toPlainText(), 'unsaved prompt')
        self.assertEqual(self.app.state, 'offline')

        self.app._set_status('online')
        self.assertIn(f'color: {LIGHT.status_success};', dot.styleSheet())
        self.select('Dark')
        self.assertIn(f'color: {DARK.status_success};', dot.styleSheet())
        self.assertEqual(self.tray_color(), DARK.status_success)

    def test_open_dialogs_follow_the_switch(self):
        dialog = QDialog(self.app)
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        self.select('Light')
        self.assertEqual(dialog.palette().window().color().name(), LIGHT.background)
        self.select('Dark')
        self.assertEqual(dialog.palette().window().color().name(), DARK.background)


if __name__ == '__main__':
    unittest.main()
