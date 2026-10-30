"""Design token schema: the single styling contract for every surface and control."""
import re
from dataclasses import dataclass, field, fields

Color = str  # opaque '#rrggbb' hex; alpha is intentionally not representable
_HEX = re.compile(r'#[0-9a-f]{6}')


@dataclass(frozen=True)
class SemanticColors:
    background: Color
    surface_primary: Color
    surface_elevated: Color
    surface_overlay: Color
    text_primary: Color
    text_secondary: Color
    text_muted: Color
    accent: Color
    border: Color
    focus_ring: Color
    status_success: Color
    status_warning: Color
    status_error: Color
    status_info: Color

    def __post_init__(self):
        for f in fields(self):
            value = getattr(self, f.name)
            if not isinstance(value, str) or not _HEX.fullmatch(value):
                raise ValueError(f'{f.name} must be a lowercase opaque #rrggbb color, got {value!r}')

    @property
    def divider(self) -> Color:
        return self.border

    @property
    def surface(self) -> Color:
        return self.background

    @property
    def surface_raised(self) -> Color:
        return self.surface_primary

    @property
    def surface_background(self) -> Color:
        return self.background

    @property
    def text(self) -> Color:
        return self.text_primary

    @property
    def handle_hover(self) -> Color:
        return self.surface_overlay

    @property
    def handle_active(self) -> Color:
        return self.focus_ring


DEFAULT_COLORS = SemanticColors(
    background='#14161a', surface_primary='#1b1e24', surface_elevated='#2a2e35',
    surface_overlay='#2a3140', text_primary='#e6e8eb', text_secondary='#d0d4da',
    text_muted='#8b949e', accent='#238636', border='#2a2e35', focus_ring='#388bfd',
    status_success='#3fb950', status_warning='#d29922', status_error='#f85149',
    status_info='#58a6ff')


@dataclass(frozen=True)
class SpacingScale:
    xxs: int
    xs: int
    sm: int
    md: int
    lg: int
    xl: int

    def __post_init__(self):
        values = [getattr(self, f.name) for f in fields(self)]
        if any(not isinstance(v, int) or v <= 0 or v % 4 for v in values):
            raise ValueError(f'spacing must be positive multiples of 4px, got {values}')

    @property
    def hairline(self) -> int:
        return 1

    def __int__(self) -> int:
        return self.xxs

    def __mul__(self, other):
        return self.xxs * other

    def __rmul__(self, other):
        return other * self.xxs


@dataclass(frozen=True)
class TypeToken:
    family: str
    size: int  # pixels
    line_height: int  # pixels
    weight: int = 400  # CSS-style weight (400 regular, 600 semibold)


@dataclass(frozen=True)
class TypographyScale:
    heading: TypeToken
    body: TypeToken
    monospace: TypeToken
    caption: TypeToken


@dataclass(frozen=True)
class CornerRadii:
    none: float
    sm: float
    md: float
    lg: float
    pill: float


SANS = '"Noto Sans", "Cantarell", sans-serif'
MONO = '"JetBrains Mono", "Noto Sans Mono", monospace'

DEFAULT_SPACING = SpacingScale(xxs=4, xs=8, sm=12, md=16, lg=24, xl=32)
DEFAULT_TYPOGRAPHY = TypographyScale(
    heading=TypeToken(SANS, 16, 22, 600),
    body=TypeToken(SANS, 13, 18),
    monospace=TypeToken(MONO, 12, 16),
    caption=TypeToken(SANS, 11, 15))
DEFAULT_RADII = CornerRadii(none=0.0, sm=4.0, md=6.0, lg=10.0, pill=999.0)


@dataclass(frozen=True)
class ThemeTokens:
    colors: SemanticColors = None
    spacing: SpacingScale = None
    typography: TypographyScale = None
    radii: CornerRadii = None

    def __init__(self, colors=None, spacing=None, typography=None, radii=None, **kwargs):
        if colors is None:
            colors = DEFAULT_COLORS
        if spacing is None:
            spacing = DEFAULT_SPACING
        if typography is None:
            typography = DEFAULT_TYPOGRAPHY
        if radii is None:
            radii = DEFAULT_RADII
        object.__setattr__(self, 'colors', colors)
        object.__setattr__(self, 'spacing', spacing)
        object.__setattr__(self, 'typography', typography)
        object.__setattr__(self, 'radii', radii)
        object.__setattr__(self, '_overrides', dict(kwargs))

    def __eq__(self, other):
        if not isinstance(other, ThemeTokens):
            return False
        return (self.colors, self.spacing, self.typography, self.radii, getattr(self, '_overrides', {})) == (
            other.colors, other.spacing, other.typography, other.radii, getattr(other, '_overrides', {}))

    @property
    def selection(self) -> str:
        return self.colors.surface_elevated

    @property
    def selection_text(self) -> str:
        return self.colors.text_primary

    @property
    def badge(self) -> str:
        return self.colors.surface_elevated

    @property
    def badge_text(self) -> str:
        return self.colors.text_secondary

    @property
    def text_strong(self) -> str:
        return self.colors.text_primary

    @property
    def border(self) -> str:
        return self._overrides.get('border', self.colors.border)

    @property
    def divider(self) -> str:
        return self._overrides.get('divider', self.colors.border)

    @property
    def text_disabled(self) -> str:
        return self.colors.text_muted

    @property
    def on_accent(self) -> str:
        return self.colors.text_primary

    @property
    def name(self) -> str:
        return 'dark' if self.colors.background == '#0d1117' else 'light'

    @property
    def surface(self) -> str:
        return self._overrides.get('surface', self.colors.background)

    @property
    def surface_raised(self) -> str:
        return self._overrides.get('surface_raised', self.colors.surface_primary)

    @property
    def text(self) -> str:
        return self._overrides.get('text', self.colors.text_primary)

    @property
    def text_muted(self) -> str:
        return self._overrides.get('text_muted', self.colors.text_muted)

    @property
    def skeleton(self) -> str:
        return self._overrides.get('skeleton', self.colors.surface_elevated)

    @property
    def idle(self) -> str:
        return self._overrides.get('idle', self.colors.text_muted)

    @property
    def loading(self) -> str:
        return self._overrides.get('loading', self.colors.status_warning)

    @property
    def streaming(self) -> str:
        return self._overrides.get('streaming', self.colors.status_success)

    @property
    def error(self) -> str:
        return self._overrides.get('error', self.colors.status_error)

    @property
    def error_surface(self) -> str:
        return self._overrides.get('error_surface', '#2a1719')

    @property
    def success(self) -> str:
        return self._overrides.get('success', self.colors.status_success)

    @property
    def radius(self) -> int:
        return self._overrides.get('radius', 6)

    @property
    def font_small(self) -> int:
        return self._overrides.get('font_small', 11)

    def state_color(self, state) -> str:
        key = str(getattr(state, 'value', state)).lower()
        return {'idle': self.idle, 'loading': self.loading,
                'streaming': self.streaming, 'error': self.error}.get(key, self.text_muted)

    def __getattr__(self, name: str):
        if '_overrides' in self.__dict__ and name in self._overrides:
            return self._overrides[name]
        is_dark = getattr(self.colors, 'background', '#14161a') == '#14161a'
        shortcuts = {
            'name': 'dark' if is_dark else 'light',
            'window': self.colors.background,
            'surface': self.colors.surface_primary,
            'raised': '#2a2e35' if is_dark else '#e7ebef',
            'raised_hover': '#363b44' if is_dark else '#d8dee4',
            'border': self.colors.border,
            'divider': '#23272e' if is_dark else '#e5e9ee',
            'text': '#d0d4da' if is_dark else '#24292f',
            'text_strong': self.colors.text_strong if hasattr(self.colors, 'text_strong') else ('#e6e8eb' if is_dark else '#1f2328'),
            'text_muted': '#8b949e' if is_dark else '#57606a',
            'text_disabled': '#6b7078' if is_dark else '#8c959f',
            'selection': '#2a3140' if is_dark else '#dbe9ff',
            'selection_text': '#e6e8eb' if is_dark else '#1f2328',
            'badge': '#2a2e35' if is_dark else '#e7ebef',
            'badge_text': '#c9d1d9' if is_dark else '#24292f',
        }
        if name in shortcuts:
            return shortcuts[name]
        try:
            return getattr(self.colors, name)
        except AttributeError:
            raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}") from None



