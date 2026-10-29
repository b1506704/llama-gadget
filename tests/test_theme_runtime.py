import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import QApplication, QDialog
from llama_gadget import theme
from llama_gadget.app import Gadget
from llama_gadget.theme import (DARK_TOKENS, LIGHT_TOKENS, JsonThemePreferenceStore, SystemAppearance,
                                ThemeAdapter, ThemeController, ThemeManager, ThemePersistError,
                                ThemePreference, appearance_from_color_scheme, detect_system_appearance)

APP = QApplication.instance() or QApplication([])
D, L, S = ThemePreference.DARK, ThemePreference.LIGHT, ThemePreference.SYSTEM


class MemoryStore:
    def __init__(self, preference=S, fail=False):
        self.preference, self.fail, self.saved = preference, fail, []

    def load_theme_preference(self):
        return self.preference

    def save_theme_preference(self, preference):
        if self.fail:
            raise ThemePersistError('disk full')
        self.saved.append(preference)
        self.preference = preference


class ControllerTests(unittest.TestCase):
    def test_enums_and_defaults(self):
        self.assertEqual([p.value for p in ThemePreference], ['dark', 'light', 'system'])
        self.assertEqual([a.value for a in SystemAppearance], ['dark', 'light'])
        self.assertIs(ThemePreference.default(), S)
        self.assertIs(SystemAppearance.default(), SystemAppearance.DARK)
        self.assertIs(ThemeController().preference(), S)
        for name in ('ThemeController', 'ThemePreference', 'SystemAppearance', 'ThemeManager',
                     'JsonThemePreferenceStore', 'detect_system_appearance'):
            self.assertIn(name, theme.__all__)

    def test_explicit_preferences_ignore_system_appearance(self):
        for appearance in SystemAppearance:
            self.assertIs(ThemeController(D, appearance).active_tokens(), DARK_TOKENS)
            self.assertIs(ThemeController(L, appearance).active_tokens(), LIGHT_TOKENS)

    def test_system_preference_tracks_appearance(self):
        c = ThemeController(S, SystemAppearance.DARK)
        self.assertIs(c.active_tokens(), DARK_TOKENS)
        self.assertIs(c.set_system_appearance(SystemAppearance.LIGHT), LIGHT_TOKENS)
        self.assertIs(c.active_tokens(), LIGHT_TOKENS)
        self.assertIs(c.set_system_appearance('dark'), DARK_TOKENS)
        self.assertIs(c.preference(), S)

    def test_appearance_change_does_not_override_explicit_choice(self):
        c = ThemeController(D, SystemAppearance.DARK)
        self.assertIs(c.set_system_appearance(SystemAppearance.LIGHT), DARK_TOKENS)
        self.assertIs(c.set_preference(S), LIGHT_TOKENS)
        self.assertIs(c.set_preference(L), LIGHT_TOKENS)
        with self.assertRaises(ValueError):
            c.set_preference('sepia')

    def test_os_detection_never_raises(self):
        self.assertIs(appearance_from_color_scheme(Qt.ColorScheme.Light), SystemAppearance.LIGHT)
        self.assertIs(appearance_from_color_scheme(Qt.ColorScheme.Dark), SystemAppearance.DARK)
        self.assertIs(appearance_from_color_scheme(Qt.ColorScheme.Unknown), SystemAppearance.DARK)
        self.assertIn(detect_system_appearance(), tuple(SystemAppearance))
        with patch('llama_gadget.theme.controller.QGuiApplication.instance', return_value=None):
            self.assertIs(detect_system_appearance(), SystemAppearance.DARK)
        with patch('llama_gadget.theme.controller.QGuiApplication.styleHints', side_effect=RuntimeError):
            self.assertIs(detect_system_appearance(), SystemAppearance.DARK)


class StoreTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'cfg' / 'preferences.json'
        self.store = JsonThemePreferenceStore(self.path)

    def test_round_trip_and_overwrite(self):
        for pref in ThemePreference:
            self.store.save_theme_preference(pref)
            self.assertIs(JsonThemePreferenceStore(self.path).load_theme_preference(), pref)
        self.assertEqual(json.loads(self.path.read_text()), {'theme': 'system'})
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ['preferences.json'])

    def test_fallbacks(self):
        self.assertIs(self.store.load_theme_preference(), S)  # missing
        self.path.parent.mkdir(parents=True)
        for text in ('{not json', '[]', '{"theme": "sepia"}', '{"theme": null}', '\xff'):
            self.path.write_text(text, encoding='latin-1')
            self.assertIs(self.store.load_theme_preference(), S, text)
        self.path.write_bytes(b'\xff\xfe')
        self.assertIs(self.store.load_theme_preference(), S)

    def test_preserves_unrelated_keys(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({'theme': 'dark', 'other': {'a': 1}}))
        self.store.save_theme_preference(L)
        self.assertEqual(json.loads(self.path.read_text()), {'theme': 'light', 'other': {'a': 1}})

    def test_unwritable_location_raises_persist_error(self):
        self.path.parent.parent.joinpath('cfg').write_text('a file, not a directory')
        with self.assertRaises(ThemePersistError):
            self.store.save_theme_preference(D)

    def test_default_path_is_under_llama_gadget_config(self):
        path = theme.default_preferences_path()
        self.assertEqual((path.parent.name, path.name), ('llama-gadget', 'preferences.json'))


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(APP.setStyleSheet, APP.styleSheet())
        self.addCleanup(APP.setPalette, APP.palette())
        self.appearance = SystemAppearance.DARK

    def manager(self, store):
        m = ThemeManager(store, detector=lambda: self.appearance)
        self.addCleanup(m.deleteLater)
        self.emitted = []
        m.theme_changed.connect(self.emitted.append)
        return m

    def test_set_preference_applies_persists_and_signals(self):
        store = MemoryStore(D)
        m = self.manager(store)
        self.assertIs(m.apply(), DARK_TOKENS)
        self.assertEqual(APP.styleSheet(), ThemeAdapter(DARK_TOKENS).stylesheet())
        self.assertIs(m.set_preference(L), LIGHT_TOKENS)
        self.assertEqual(store.saved, [L])
        self.assertEqual(APP.styleSheet(), ThemeAdapter(LIGHT_TOKENS).stylesheet())
        self.assertEqual(APP.palette().window().color().name(), LIGHT_TOKENS.colors.background)
        self.assertEqual(self.emitted, [DARK_TOKENS, LIGHT_TOKENS])

    def test_system_change_rerenders_without_persisting(self):
        store = MemoryStore(S)
        m = self.manager(store)
        self.assertIs(m.tokens(), DARK_TOKENS)
        m._on_color_scheme_changed(Qt.ColorScheme.Light)
        self.assertIs(m.tokens(), LIGHT_TOKENS)
        self.assertEqual(APP.styleSheet(), ThemeAdapter(LIGHT_TOKENS).stylesheet())
        self.assertEqual(self.emitted, [LIGHT_TOKENS])
        self.assertEqual(store.saved, [])
        self.assertIs(m.preference(), S)

    def test_system_change_ignored_for_explicit_choice(self):
        m = self.manager(MemoryStore(D))
        m.set_system_appearance(SystemAppearance.LIGHT)
        self.assertIs(m.tokens(), DARK_TOKENS)
        self.assertEqual(self.emitted, [])

    def test_selecting_system_redetects_os(self):
        m = self.manager(MemoryStore(D))
        self.appearance = SystemAppearance.LIGHT
        self.assertIs(m.set_preference(S), LIGHT_TOKENS)

    def test_persist_failure_still_applies(self):
        m = self.manager(MemoryStore(D, fail=True))
        with self.assertRaises(ThemePersistError):
            m.set_preference(L)
        self.assertIs(m.tokens(), LIGHT_TOKENS)
        self.assertEqual(APP.styleSheet(), ThemeAdapter(LIGHT_TOKENS).stylesheet())


class GadgetThemeTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.prefs = self.root / 'preferences.json'
        self.prefs.write_text(json.dumps({'theme': 'dark', 'keep': True}))
        self.addCleanup(APP.setStyleSheet, APP.styleSheet())
        self.addCleanup(APP.setPalette, APP.palette())
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
        self.app.deleteLater()
        APP.processEvents()

    def select(self, text):
        index = self.app.theme_cb.findText(text)
        self.app.theme_cb.setCurrentIndex(index)
        self.app.theme_cb.activated.emit(index)

    def test_startup_restores_saved_preference(self):
        self.assertEqual(self.app.theme_cb.currentText(), 'Dark')
        self.assertEqual([self.app.theme_cb.itemText(i) for i in range(3)], ['Dark', 'Light', 'System'])
        self.assertIs(self.app.theme.tokens(), DARK_TOKENS)
        self.assertEqual(APP.styleSheet(), ThemeAdapter(DARK_TOKENS).stylesheet())
        self.assertEqual(self.app.styleSheet(), '')  # no local override hiding the app-wide theme

    def test_switch_is_live_persisted_and_keeps_session_state(self):
        self.app.msgs = [{'role': 'user', 'content': 'hello'}]
        self.app.session_id = 42
        self.app.input.setPlainText('draft text')
        prompt_edit = self.app.prompt_edit
        dialog = QDialog(self.app)
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        refreshed = []
        self.app.theme.theme_changed.connect(refreshed.append)
        self.select('Light')
        self.assertIs(self.app.theme.tokens(), LIGHT_TOKENS)
        self.assertEqual(APP.styleSheet(), ThemeAdapter(LIGHT_TOKENS).stylesheet())
        self.assertEqual(json.loads(self.prefs.read_text()), {'theme': 'light', 'keep': True})
        self.assertEqual(refreshed, [LIGHT_TOKENS])
        self.assertEqual(self.app.msgs, [{'role': 'user', 'content': 'hello'}])
        self.assertEqual(self.app.session_id, 42)
        self.assertEqual(self.app.input.toPlainText(), 'draft text')
        self.assertIs(self.app.prompt_edit, prompt_edit)
        self.assertEqual(self.app.theme_msg.text(), '')
        self.assertEqual(dialog.palette().window().color().name(), LIGHT_TOKENS.colors.background)

    def test_save_failure_is_reported_not_raised(self):
        self.app.theme.store = MemoryStore(D, fail=True)
        self.select('Light')
        self.assertIs(self.app.theme.tokens(), LIGHT_TOKENS)
        self.assertIn('not saved', self.app.theme_msg.text())


if __name__ == '__main__':
    unittest.main()
