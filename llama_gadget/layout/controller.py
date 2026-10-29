"""Side-panel collapse state machine and the compact-mode viewport breakpoint."""
from typing import Literal

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from .splitter import ZONE_INDEX, WorkspaceZone

COMPACT_BREAKPOINT_PX: int = 900
PanelZone = Literal['sidebar', 'inspector']
PANEL_ZONES: tuple[PanelZone, ...] = ('sidebar', 'inspector')


class LayoutController(QObject):
    """Collapses side panels by hiding them (splitter width 0) while remembering expanded widths.

    `saved_widths` follows divider drags only, so a window squeezing a panel never shrinks the
    width it is restored to.

    `user_collapsed` is the user's explicit choice; compact mode never changes it. Panels that
    compact mode hid are tracked in `auto_collapsed`, so widening restores only those, and a
    manual toggle while compact counts as the user's choice.
    """
    layout_changed = pyqtSignal()

    def __init__(self, splitter, focus_target=None, parent=None):
        super().__init__(parent)
        self.splitter = splitter
        self.focus_target = focus_target
        self.user_collapsed = {zone: False for zone in PANEL_ZONES}
        self.saved_widths = {zone: splitter.bounds[WorkspaceZone(zone)].default_width for zone in PANEL_ZONES}
        self.auto_collapsed = set()
        self.is_compact = False
        splitter.splitterMoved.connect(self._on_moved)

    def is_panel_collapsed(self, zone: PanelZone) -> bool:
        return self.splitter.zone_widget(zone).isHidden()

    def toggle_panel(self, zone: PanelZone) -> bool:
        collapsed = not self.is_panel_collapsed(zone)
        self.auto_collapsed.discard(zone)
        self.user_collapsed[zone] = collapsed
        self._set_collapsed(zone, collapsed)
        self.layout_changed.emit()
        return collapsed

    def evaluate_viewport(self, width: int) -> None:
        compact = width < COMPACT_BREAKPOINT_PX
        if compact == self.is_compact:
            return
        self.is_compact = compact
        if compact:
            for zone in PANEL_ZONES:
                if not self.is_panel_collapsed(zone):
                    self.auto_collapsed.add(zone)
                    self._set_collapsed(zone, True)
        else:
            for zone in PANEL_ZONES:
                if zone in self.auto_collapsed:
                    self._set_collapsed(zone, False)
            self.auto_collapsed.clear()
        self.layout_changed.emit()

    def panel_width(self, zone: PanelZone) -> int:
        """The width the panel has (or returns to) when expanded."""
        return self.saved_widths[zone]

    def restore(self, widths, collapsed):
        """Seed remembered widths and the user's collapsed flags (e.g. from saved preferences)."""
        for zone in PANEL_ZONES:
            self.saved_widths[zone] = self.splitter.bounds[WorkspaceZone(zone)].clamp(widths[zone])
            self.user_collapsed[zone] = bool(collapsed[zone])
        self.auto_collapsed.clear()
        self.is_compact = False
        for zone in PANEL_ZONES:
            self.splitter.zone_widget(zone).setHidden(self.user_collapsed[zone])
        self.splitter.refresh()
        self._apply_widths()

    def _set_collapsed(self, zone, collapsed):
        widget = self.splitter.zone_widget(zone)
        if collapsed:
            focus = QApplication.focusWidget()
            # Never leave keyboard focus on a control that is about to disappear.
            if focus is not None and widget.isAncestorOf(focus) and self.focus_target is not None:
                self.focus_target.setFocus()
            widget.hide()
            self.splitter.refresh()  # relayout now rather than on the next posted LayoutRequest
        else:
            widget.show()
            self.splitter.refresh()
            self._apply_widths()

    def _apply_widths(self):
        sizes = self.splitter.sizes()
        canvas = ZONE_INDEX[WorkspaceZone.CANVAS]
        total = sum(sizes)
        for zone in PANEL_ZONES:
            index = ZONE_INDEX[WorkspaceZone(zone)]
            sizes[index] = 0 if self.is_panel_collapsed(zone) else self.saved_widths[zone]
        others = sum(size for index, size in enumerate(sizes) if index != canvas)
        sizes[canvas] = max(total - others, self.splitter.bounds[WorkspaceZone.CANVAS].min_width)
        self.splitter.setSizes(sizes)

    def _on_moved(self, *_):
        for zone in PANEL_ZONES:
            if not self.is_panel_collapsed(zone):
                self.saved_widths[zone] = self.splitter.zone_width(zone)
        self.layout_changed.emit()
