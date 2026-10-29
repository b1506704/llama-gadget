import time
from tempfile import TemporaryDirectory
import unittest

from PyQt6.QtWidgets import QSplitter, QWidget

from llama_gadget.layout import DEFAULT_BOUNDS, WorkspaceSplitter, WorkspaceZone
from llama_gadget.theme import DARK_TOKENS, LIGHT_TOKENS, ThemeAdapter
from llama_gadget.theme.lint import lint
from tests.gadget_support import make_gadget, pump

FRAME_BUDGET = 0.016
SIDEBAR, CANVAS, INSPECTOR = DEFAULT_BOUNDS[WorkspaceZone.SIDEBAR], DEFAULT_BOUNDS[WorkspaceZone.CANVAS], \
    DEFAULT_BOUNDS[WorkspaceZone.INSPECTOR]


def timed(action):
    start = time.perf_counter()
    action()
    pump()
    return time.perf_counter() - start


class WorkspaceSplitterTests(unittest.TestCase):
    def setUp(self):
        self.zones = [QWidget(), QWidget(), QWidget()]
        self.splitter = WorkspaceSplitter(*self.zones)
        self.addCleanup(self.splitter.deleteLater)
        self.splitter.resize(1100, 600)
        self.splitter.show()
        pump()

    def test_three_zones_in_order_with_stretch_priority(self):
        self.assertEqual(self.splitter.count(), 3)
        for index, zone in enumerate(WorkspaceZone):
            self.assertIs(self.splitter.widget(index), self.zones[index])
            self.assertIs(self.splitter.zone_widget(zone), self.zones[index])
            self.assertEqual(self.zones[index].property('zone'), zone.value)
        self.assertTrue(all(width > 0 for width in self.splitter.sizes()))
        self.assertGreaterEqual(CANVAS.stretch_factor, 1)
        self.assertEqual((SIDEBAR.stretch_factor, INSPECTOR.stretch_factor), (0, 0))
        self.assertFalse(self.splitter.childrenCollapsible())

    def test_set_sizes_and_drags_clamp_side_panels(self):
        self.splitter.setSizes([10, 600, 2000])
        sidebar, canvas, inspector = self.splitter.sizes()
        self.assertGreaterEqual(sidebar, SIDEBAR.min_width)
        self.assertLessEqual(inspector, INSPECTOR.max_width)
        self.assertGreaterEqual(canvas, CANVAS.min_width)
        self.splitter.moveSplitter(0, 1)  # drag the left divider fully left
        self.assertEqual(self.splitter.zone_width(WorkspaceZone.SIDEBAR), SIDEBAR.min_width)
        self.splitter.moveSplitter(self.splitter.width(), 1)  # and fully right
        self.assertEqual(self.splitter.zone_width(WorkspaceZone.SIDEBAR), SIDEBAR.max_width)
        self.splitter.moveSplitter(self.splitter.width(), 2)
        self.assertEqual(self.splitter.zone_width(WorkspaceZone.INSPECTOR), INSPECTOR.min_width)
        self.splitter.moveSplitter(0, 2)
        self.assertEqual(self.splitter.zone_width(WorkspaceZone.INSPECTOR), INSPECTOR.max_width)
        self.assertGreaterEqual(self.splitter.zone_width(WorkspaceZone.CANVAS), CANVAS.min_width)

    def test_extra_width_goes_to_canvas(self):
        before = self.splitter.sizes()
        self.splitter.resize(1500, 600)
        pump()
        after = self.splitter.sizes()
        self.assertEqual((after[0], after[2]), (before[0], before[2]))
        self.assertEqual(after[1] - before[1], 400)

    def test_apply_theme_restyles_in_place(self):
        children = list(self.zones)
        for tokens in (DARK_TOKENS, LIGHT_TOKENS):
            self.splitter.apply_theme(tokens)
            sheet = self.splitter.styleSheet()
            self.assertEqual(sheet, ThemeAdapter.splitter_stylesheet(tokens))
            self.assertIn(tokens.colors.divider, sheet)
            self.assertIn(tokens.colors.focus_ring, sheet)
            self.assertIn(tokens.colors.border, sheet)
            self.assertEqual(self.splitter.handleWidth(), tokens.spacing.xs)
            self.assertEqual([self.splitter.widget(i) for i in range(3)], children)
        self.assertNotEqual(ThemeAdapter.splitter_stylesheet(DARK_TOKENS),
                            ThemeAdapter.splitter_stylesheet(LIGHT_TOKENS))

    def test_layout_and_adapter_have_no_raw_literals(self):
        self.assertEqual(lint(), [])

    def test_divider_and_resize_passes_fit_frame_budget(self):
        timed(lambda: self.splitter.moveSplitter(300, 1))  # warm-up
        for position in (200, 260, 320, 280):
            self.assertLessEqual(timed(lambda: self.splitter.moveSplitter(position, 1)), FRAME_BUDGET)
            self.assertLessEqual(timed(lambda: self.splitter.moveSplitter(position + 500, 2)), FRAME_BUDGET)
        for width in (1200, 1000, 1400, 1100):
            self.assertLessEqual(timed(lambda: self.splitter.resize(width, 600)), FRAME_BUDGET)


class GadgetWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = make_gadget(self, temp.name)
        self.app.resize(1200, 720)
        pump()

    def test_window_hosts_three_zones(self):
        zones = self.app.zones
        self.assertIsInstance(zones, QSplitter)
        self.assertIs(zones.zone_widget(WorkspaceZone.CANVAS), self.app.canvas)
        self.assertTrue(zones.zone_widget(WorkspaceZone.SIDEBAR).isAncestorOf(self.app.hist_list))
        self.assertTrue(self.app.canvas.isAncestorOf(self.app.input))
        self.assertTrue(zones.zone_widget(WorkspaceZone.INSPECTOR).isAncestorOf(self.app.profile_cb))
        self.assertTrue(zones.zone_widget(WorkspaceZone.INSPECTOR).isAncestorOf(self.app.tools_cb))
        sidebar, canvas, inspector = (w.geometry() for w in map(zones.zone_widget, WorkspaceZone))
        self.assertLess(sidebar.right(), canvas.left())
        self.assertLess(canvas.right(), inspector.left())
        self.assertTrue(all(width > 0 for width in zones.sizes()))
        self.assertEqual(zones.styleSheet(), ThemeAdapter.splitter_stylesheet(DARK_TOKENS))

    def test_navigation_switches_canvas_and_keeps_draft(self):
        self.app.input.setPlainText('half-written')
        self.app.nav.setCurrentRow(1)
        self.assertEqual(self.app.canvas.currentIndex(), 1)
        self.app.nav.setCurrentRow(0)
        self.assertEqual(self.app.canvas.currentIndex(), 0)
        self.assertEqual(self.app.input.toPlainText(), 'half-written')

    def test_session_switch_restores_draft_and_highlights_active(self):
        first = self.app.db.create_session(self.app.user, 'first')
        second = self.app.db.create_session(self.app.user, 'second')
        self.app._refresh_history()
        self.app._load_session(first)
        self.app.input.setPlainText('draft for first')
        self.app._load_session(second)
        self.assertEqual(self.app.input.toPlainText(), '')
        self.assertEqual(self.app.hist_list.currentItem().data(0x0100), second)
        self.assertTrue(self.app.hist_list.currentItem().text().startswith('▶ '))
        self.app.nav.setCurrentRow(1)
        self.app._load_session(first)
        self.assertEqual(self.app.input.toPlainText(), 'draft for first')
        self.assertEqual(self.app.canvas.currentIndex(), 0)
        self.assertEqual(self.app.hist_list.currentItem().data(0x0100), first)
        self.assertEqual(sum(self.app.hist_list.item(i).text().startswith('▶ ')
                             for i in range(self.app.hist_list.count())), 1)

    def test_window_resize_passes_fit_frame_budget(self):
        timed(lambda: self.app.resize(1300, 720))  # warm-up
        for width in (1250, 1400, 1150, 1300):
            self.assertLessEqual(timed(lambda: self.app.resize(width, 720)), FRAME_BUDGET)
        for position in (200, 300, 250):
            self.assertLessEqual(timed(lambda: self.app.zones.moveSplitter(position, 1)), FRAME_BUDGET)


if __name__ == '__main__':
    unittest.main()
