import time
from tempfile import TemporaryDirectory
import unittest

from PyQt6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

from llama_gadget.layout import COMPACT_BREAKPOINT_PX, PANEL_ZONES, LayoutController, WorkspaceSplitter
from tests.gadget_support import make_gadget, pump

FRAME_BUDGET = 0.016


def timed(action):
    start = time.perf_counter()
    action()
    pump()
    return time.perf_counter() - start


def other(zone):
    return 'inspector' if zone == 'sidebar' else 'sidebar'


class LayoutControllerTests(unittest.TestCase):
    def setUp(self):
        self.field = QLineEdit()
        sidebar = QWidget()
        QVBoxLayout(sidebar).addWidget(self.field)
        self.canvas = QLineEdit()
        self.splitter = WorkspaceSplitter(sidebar, self.canvas, QWidget())
        self.addCleanup(self.splitter.deleteLater)
        self.splitter.resize(1200, 600)
        self.splitter.show()
        pump()
        self.controller = LayoutController(self.splitter, focus_target=self.canvas)

    def test_toggle_collapses_and_restores_each_panel_independently(self):
        for zone in PANEL_ZONES:
            with self.subTest(zone=zone):
                self.splitter.moveSplitter(300 if zone == 'sidebar' else 1200 - 330, 1 if zone == 'sidebar' else 2)
                width, opposite = self.splitter.zone_width(zone), self.splitter.zone_width(other(zone))
                self.assertTrue(self.controller.toggle_panel(zone))
                self.assertEqual(self.splitter.zone_width(zone), 0)
                self.assertTrue(self.controller.is_panel_collapsed(zone))
                self.assertTrue(self.controller.user_collapsed[zone])
                self.assertEqual(self.splitter.zone_width(other(zone)), opposite)
                self.assertFalse(self.controller.toggle_panel(zone))
                self.assertEqual(self.splitter.zone_width(zone), width)
                self.assertFalse(self.controller.user_collapsed[zone])
                self.assertEqual(self.splitter.zone_width(other(zone)), opposite)

    def test_compact_mode_restores_only_auto_collapsed_panels(self):
        widths = {zone: self.splitter.zone_width(zone) for zone in PANEL_ZONES}
        self.controller.toggle_panel('inspector')
        self.controller.evaluate_viewport(COMPACT_BREAKPOINT_PX - 1)
        self.assertTrue(self.controller.is_compact)
        self.assertTrue(all(map(self.controller.is_panel_collapsed, PANEL_ZONES)))
        self.assertEqual(self.controller.user_collapsed, {'sidebar': False, 'inspector': True})
        self.controller.evaluate_viewport(COMPACT_BREAKPOINT_PX)
        self.assertFalse(self.controller.is_compact)
        self.assertFalse(self.controller.is_panel_collapsed('sidebar'))
        self.assertEqual(self.splitter.zone_width('sidebar'), widths['sidebar'])
        self.assertTrue(self.controller.is_panel_collapsed('inspector'))
        self.assertEqual(self.controller.panel_width('inspector'), widths['inspector'])

    def test_manual_changes_in_compact_mode_survive_widening(self):
        self.controller.evaluate_viewport(800)
        self.controller.toggle_panel('sidebar')  # user opens the sidebar while compact
        self.assertFalse(self.controller.is_panel_collapsed('sidebar'))
        self.controller.toggle_panel('sidebar')  # ...and closes it again
        self.controller.evaluate_viewport(1200)
        self.assertTrue(self.controller.is_panel_collapsed('sidebar'))
        self.assertFalse(self.controller.is_panel_collapsed('inspector'))

    def test_collapsing_focused_panel_moves_focus_to_canvas(self):
        self.splitter.activateWindow()
        self.field.setFocus()
        pump()
        if not self.field.hasFocus():
            self.skipTest('platform did not grant focus')
        self.controller.toggle_panel('sidebar')
        pump()
        self.assertTrue(self.canvas.hasFocus())

    def test_transitions_fit_frame_budget(self):
        timed(lambda: self.controller.toggle_panel('sidebar'))  # warm-up
        timed(lambda: self.controller.toggle_panel('sidebar'))
        for zone in PANEL_ZONES * 2:
            self.assertLessEqual(timed(lambda: self.controller.toggle_panel(zone)), FRAME_BUDGET)
        for width in (800, 1200, 800, 1200):
            self.assertLessEqual(timed(lambda: self.controller.evaluate_viewport(width)), FRAME_BUDGET)


class GadgetPanelControlTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = make_gadget(self, temp.name)
        self.app.resize(1200, 720)
        pump()
        self.controller = self.app.layout_controller

    def assert_controls_synced(self):
        for zone in PANEL_ZONES:
            self.assertEqual(self.app.panel_buttons[zone].isChecked(), not self.controller.is_panel_collapsed(zone))

    def test_buttons_and_shortcuts_toggle_one_panel(self):
        for zone in PANEL_ZONES:
            for trigger in (self.app.panel_buttons[zone].click, self.app.panel_shortcuts[zone].activated.emit):
                with self.subTest(zone=zone, trigger=trigger):
                    width = self.app.zones.zone_width(zone)
                    opposite = self.app.zones.zone_width(other(zone))
                    trigger()
                    self.assertTrue(self.controller.is_panel_collapsed(zone))
                    self.assertEqual(self.app.zones.zone_width(zone), 0)
                    self.assertEqual(self.app.zones.zone_width(other(zone)), opposite)
                    self.assert_controls_synced()
                    trigger()
                    self.assertFalse(self.controller.is_panel_collapsed(zone))
                    self.assertEqual(self.app.zones.zone_width(zone), width)
                    self.assert_controls_synced()
        self.assertNotEqual(self.app.panel_shortcuts['sidebar'].key(), self.app.panel_shortcuts['inspector'].key())

    def test_window_resize_crosses_breakpoint(self):
        cases = {(): (False, False), ('inspector',): (False, True), ('sidebar', 'inspector'): (True, True)}
        for manual, expected in cases.items():
            with self.subTest(manual=manual):
                for zone in PANEL_ZONES:  # start with both open
                    if self.controller.is_panel_collapsed(zone):
                        self.controller.toggle_panel(zone)
                widths = {zone: self.app.zones.zone_width(zone) for zone in PANEL_ZONES}
                for zone in manual:
                    self.app.panel_buttons[zone].click()
                self.app.resize(800, 720)
                pump()
                self.assertTrue(self.controller.is_compact)
                self.assertTrue(all(map(self.controller.is_panel_collapsed, PANEL_ZONES)))
                self.assert_controls_synced()
                self.app.resize(1200, 720)
                pump()
                self.assertEqual(tuple(map(self.controller.is_panel_collapsed, PANEL_ZONES)), expected)
                for zone in PANEL_ZONES:
                    self.assertEqual(self.controller.panel_width(zone), widths[zone])
                self.assert_controls_synced()

    def test_toggle_and_breakpoint_resizes_fit_frame_budget(self):
        timed(lambda: self.app.resize(800, 720))  # warm-up
        timed(lambda: self.app.resize(1200, 720))
        for zone in PANEL_ZONES * 2:
            self.assertLessEqual(timed(self.app.panel_buttons[zone].click), FRAME_BUDGET)
        for width in (800, 1200, 800, 1200):
            self.assertLessEqual(timed(lambda: self.app.resize(width, 720)), FRAME_BUDGET)


if __name__ == '__main__':
    unittest.main()
