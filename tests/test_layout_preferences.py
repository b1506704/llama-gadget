import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import time
import unittest

from PyQt6.QtWidgets import QApplication

from llama_gadget.layout import (DEFAULT_BOUNDS, LayoutPreferences, WorkspaceZone, load_layout_preferences,
                                 save_layout_preferences)
from tests.gadget_support import make_gadget, pump

SIDEBAR, INSPECTOR = DEFAULT_BOUNDS[WorkspaceZone.SIDEBAR], DEFAULT_BOUNDS[WorkspaceZone.INSPECTOR]


class LayoutPreferencesStoreTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'config' / 'preferences.json'

    def test_round_trip_keeps_other_preferences(self):
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({'theme': 'dark', 'layout': {'stale': 1}}))
        prefs = LayoutPreferences(1300, 800, 40, 30, 300, 260, True, False)
        save_layout_preferences(prefs, self.path)
        self.assertEqual(load_layout_preferences(self.path), prefs)
        data = json.loads(self.path.read_text())
        self.assertEqual(data['theme'], 'dark')
        self.assertNotIn('stale', data['layout'])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])  # no temp file left behind

    def test_missing_empty_or_malformed_files_give_defaults(self):
        self.assertEqual(load_layout_preferences(self.path), LayoutPreferences())
        self.path.parent.mkdir()
        for text in ('', '{not json', '[]', '{"layout": "wide"}', '{"layout": {"sidebar_collapsed": "yes"}}'):
            with self.subTest(text=text):
                self.path.write_text(text)
                self.assertEqual(load_layout_preferences(self.path), LayoutPreferences())

    def test_out_of_range_values_are_clamped(self):
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({'layout': {
            'window_width': -5, 'window_height': 10 ** 9, 'window_x': 12, 'window_y': None,
            'sidebar_width': -100, 'inspector_width': 99999, 'sidebar_collapsed': True}}))
        prefs = load_layout_preferences(self.path)
        self.assertEqual((prefs.window_width, prefs.window_height), (320, 16384))
        self.assertEqual((prefs.window_x, prefs.window_y), (None, None))
        self.assertEqual((prefs.sidebar_width, prefs.inspector_width), (SIDEBAR.min_width, INSPECTOR.max_width))
        self.assertTrue(prefs.sidebar_collapsed)
        self.assertFalse(prefs.inspector_collapsed)


class LayoutRestartTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / 'preferences.json'

    def launch(self):
        app = make_gadget(self, self.root, preferences=self.path)
        pump()
        return app

    def arrange(self, app):
        app.resize(1200, 720)
        app.move(30, 20)
        pump()
        app.zones.moveSplitter(300, 1)
        app.panel_buttons['inspector'].click()
        pump()

    def test_restart_restores_geometry_widths_and_collapsed_states(self):
        first = self.launch()
        self.arrange(first)
        first.close()
        saved = load_layout_preferences(self.path)
        self.assertEqual(saved, LayoutPreferences(1200, 720, 30, 20, 300, INSPECTOR.default_width, False, True))
        second = self.launch()
        self.assertEqual((second.width(), second.height(), second.x(), second.y()), (1200, 720, 30, 20))
        controller = second.layout_controller
        self.assertFalse(controller.is_panel_collapsed('sidebar'))
        self.assertEqual(second.zones.zone_width('sidebar'), 300)
        self.assertTrue(controller.is_panel_collapsed('inspector'))
        self.assertFalse(second.panel_buttons['inspector'].isChecked())
        second.panel_buttons['inspector'].click()
        self.assertEqual(second.zones.zone_width('inspector'), INSPECTOR.default_width)

    def test_layout_changes_are_saved_without_closing(self):
        app = self.launch()
        self.arrange(app)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not load_layout_preferences(self.path).inspector_collapsed:
            pump(.05)
        prefs = load_layout_preferences(self.path)
        self.assertTrue(prefs.inspector_collapsed)
        self.assertEqual(prefs.sidebar_width, 300)
        QApplication.instance().aboutToQuit.emit()
        self.assertEqual(load_layout_preferences(self.path).window_width, 1200)

    def test_closing_while_compact_keeps_pre_compact_arrangement(self):
        first = self.launch()
        first.resize(1200, 720)
        first.move(30, 20)
        pump()
        first.zones.moveSplitter(300, 1)
        first.resize(800, 720)
        pump()
        self.assertTrue(first.layout_controller.is_compact)
        first.close()
        saved = load_layout_preferences(self.path)
        self.assertEqual((saved.window_width, saved.sidebar_width), (800, 300))
        self.assertEqual((saved.sidebar_collapsed, saved.inspector_collapsed), (False, False))
        second = self.launch()
        controller = second.layout_controller
        self.assertTrue(controller.is_compact)
        self.assertTrue(controller.is_panel_collapsed('sidebar') and controller.is_panel_collapsed('inspector'))
        second.resize(1200, 720)
        pump()
        self.assertFalse(controller.is_panel_collapsed('sidebar') or controller.is_panel_collapsed('inspector'))
        self.assertEqual(second.zones.zone_width('sidebar'), 300)

    def assert_default_layout(self, app):
        controller = app.layout_controller
        self.assertEqual(controller.user_collapsed, {'sidebar': False, 'inspector': False})
        self.assertEqual((controller.panel_width('sidebar'), controller.panel_width('inspector')),
                         (SIDEBAR.default_width, INSPECTOR.default_width))
        screen = QApplication.primaryScreen().availableGeometry()
        self.assertTrue(screen.contains(app.geometry().center()))

    def test_corrupt_preferences_open_default_layout(self):
        self.path.write_text('{"layout": [')
        self.assert_default_layout(self.launch())

    def test_off_screen_position_opens_default_layout_on_display(self):
        save_layout_preferences(LayoutPreferences(1200, 720, -40000, -40000, 300, 400, True, True), self.path)
        self.assert_default_layout(self.launch())


if __name__ == '__main__':
    unittest.main()
