"""Shared theme tokens: the only place raw colors and sizes for themed widgets are defined."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SemanticColors:
    surface: str
    surface_raised: str
    border: str
    divider: str
    handle_hover: str
    handle_active: str
    focus_ring: str
    text: str
    text_muted: str


@dataclass(frozen=True)
class SpacingScale:
    hairline: int
    xs: int
    sm: int
    md: int


@dataclass(frozen=True)
class CornerRadii:
    sm: int
    md: int


@dataclass(frozen=True)
class ThemeTokens:
    name: str
    colors: SemanticColors
    spacing: SpacingScale
    radii: CornerRadii


SPACING = SpacingScale(hairline=1, xs=4, sm=6, md=10)
RADII = CornerRadii(sm=6, md=10)

# Dark mirrors the palette of the app's existing STYLE sheet.
DARK_TOKENS = ThemeTokens('dark', SemanticColors(
    surface='#14161a', surface_raised='#1b1e24', border='#2a2e35', divider='#23272e',
    handle_hover='#363b44', handle_active='#2f81f7', focus_ring='#2f81f7',
    text='#e6e8eb', text_muted='#8b949e'), SPACING, RADII)

LIGHT_TOKENS = ThemeTokens('light', SemanticColors(
    surface='#ffffff', surface_raised='#f6f8fa', border='#d0d7de', divider='#e3e7eb',
    handle_hover='#c4ccd5', handle_active='#0969da', focus_ring='#0969da',
    text='#1f2328', text_muted='#59636e'), SPACING, RADII)
