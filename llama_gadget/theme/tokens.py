"""Design token schema: the single styling contract for every surface and control."""
import re
from dataclasses import dataclass, fields

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


@dataclass(frozen=True)
class ThemeTokens:
    colors: SemanticColors
    spacing: SpacingScale
    typography: TypographyScale
    radii: CornerRadii

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
        return self.colors.border

    @property
    def name(self) -> str:
        return 'dark' if self.colors.background == '#0d1117' else 'light'


SANS = '"Noto Sans", "Cantarell", sans-serif'
MONO = '"JetBrains Mono", "Noto Sans Mono", monospace'

DEFAULT_SPACING = SpacingScale(xxs=4, xs=8, sm=12, md=16, lg=24, xl=32)
DEFAULT_TYPOGRAPHY = TypographyScale(
    heading=TypeToken(SANS, 16, 22, 600),
    body=TypeToken(SANS, 13, 18),
    monospace=TypeToken(MONO, 12, 16),
    caption=TypeToken(SANS, 11, 15))
DEFAULT_RADII = CornerRadii(none=0.0, sm=4.0, md=6.0, lg=10.0, pill=999.0)
