"""Bounded three-zone workspace: navigation sidebar, primary canvas, contextual inspector."""
from dataclasses import dataclass
from enum import Enum

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSplitter

from ..theme import ThemeAdapter, ThemeTokens


class WorkspaceZone(str, Enum):
    SIDEBAR = 'sidebar'
    CANVAS = 'canvas'
    INSPECTOR = 'inspector'


ZONE_INDEX = {zone: index for index, zone in enumerate(WorkspaceZone)}


@dataclass(frozen=True)
class PanelBounds:
    min_width: int
    max_width: int | None
    default_width: int
    stretch_factor: int

    def clamp(self, width):
        width = max(self.min_width, int(width))
        return width if self.max_width is None else min(self.max_width, width)


# Side panels never stretch; spare width always goes to the canvas.
DEFAULT_BOUNDS = {
    WorkspaceZone.SIDEBAR: PanelBounds(min_width=180, max_width=360, default_width=240, stretch_factor=0),
    WorkspaceZone.CANVAS: PanelBounds(min_width=320, max_width=None, default_width=560, stretch_factor=1),
    WorkspaceZone.INSPECTOR: PanelBounds(min_width=220, max_width=420, default_width=280, stretch_factor=0),
}


class WorkspaceSplitter(QSplitter):
    """Horizontal QSplitter hosting (sidebar, canvas, inspector) at indices (0, 1, 2)."""

    def __init__(self, sidebar, canvas, inspector, bounds=None, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.bounds = dict(bounds or DEFAULT_BOUNDS)
        self.setObjectName('workspace')
        # Native opaque resizing, no animations: each drag is one synchronous layout pass.
        self.setOpaqueResize(True)
        # Dragging can't collapse a zone; collapsing is an explicit LayoutController action.
        self.setChildrenCollapsible(False)
        for zone, widget in zip(WorkspaceZone, (sidebar, canvas, inspector)):
            bound = self.bounds[zone]
            widget.setProperty('zone', zone.value)
            widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            widget.setMinimumWidth(bound.min_width)
            if bound.max_width is not None:
                widget.setMaximumWidth(bound.max_width)
            self.addWidget(widget)
            self.setStretchFactor(ZONE_INDEX[zone], bound.stretch_factor)
        self.setSizes([self.bounds[zone].default_width for zone in WorkspaceZone])

    def setSizes(self, sizes):
        # QSplitter only honours max widths while dragging; clamp programmatic sizes too.
        super().setSizes([size if self.widget(index).isHidden() else self.bounds[zone].clamp(size)
                          for (index, zone), size in zip(enumerate(WorkspaceZone), sizes)])

    def zone_widget(self, zone):
        return self.widget(ZONE_INDEX[WorkspaceZone(zone)])

    def zone_width(self, zone):
        return self.sizes()[ZONE_INDEX[WorkspaceZone(zone)]]

    def apply_theme(self, tokens: ThemeTokens):
        """Re-style handles, zone borders, and focus rings in place; child widgets are untouched."""
        self.setHandleWidth(ThemeAdapter.handle_width(tokens))
        self.setStyleSheet(ThemeAdapter.splitter_stylesheet(tokens))
