"""Maps ThemeTokens onto Qt: a QPalette, QFonts, and QSS builders for surfaces and controls."""
from PyQt6.QtGui import QColor, QFont, QPalette

from .tokens import ThemeTokens, TypeToken

SURFACES = ('background', 'primary', 'elevated', 'overlay')
TEXT_ROLES = ('primary', 'secondary', 'muted')
TYPE_ROLES = ('heading', 'body', 'monospace', 'caption')
STATUSES = ('success', 'warning', 'error', 'info')
RADII = ('none', 'sm', 'md', 'lg', 'pill')
ON_COLOR_THRESHOLD = 0.35  # fills darker than this get a light foreground


def luminance(color):
    """WCAG relative luminance of a '#rrggbb' color."""
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    q = QColor(color)
    return 0.2126 * channel(q.red()) + 0.7152 * channel(q.green()) + 0.0722 * channel(q.blue())


def _pseudo(selector, state):
    """Apply a pseudo-state to every selector in a comma-separated group."""
    return ', '.join(f'{part.strip()}:{state}' for part in selector.split(','))


def _px(value):
    return f'{value:g}px'


class ThemeAdapter:
    def __init__(self, tokens: ThemeTokens):
        self.tokens = tokens

    @property
    def is_dark(self):
        return luminance(self.tokens.colors.background) < 0.5

    # -- color derivation -------------------------------------------------
    def surface(self, tier='primary'):
        if tier not in SURFACES:
            raise ValueError(f'unknown surface tier {tier!r}')
        c = self.tokens.colors
        return c.background if tier == 'background' else getattr(c, f'surface_{tier}')

    def text(self, role='primary'):
        if role not in TEXT_ROLES:
            raise ValueError(f'unknown text role {role!r}')
        return getattr(self.tokens.colors, f'text_{role}')

    def status_color(self, status):
        if status not in STATUSES:
            raise ValueError(f'unknown status {status!r}')
        return getattr(self.tokens.colors, f'status_{status}')

    def radius(self, size='md'):
        if size not in RADII:
            raise ValueError(f'unknown radius {size!r}')
        return getattr(self.tokens.radii, size)

    def hover(self, color):
        """Interaction state: lighten on dark themes, darken on light ones."""
        q = QColor(color)
        return (q.lighter(128) if self.is_dark else q.darker(108)).name()

    def on_color(self, color):
        """Readable foreground for text drawn on top of `color`."""
        c = self.tokens.colors
        light, dark = sorted((c.text_primary, c.background), key=luminance, reverse=True)
        return light if luminance(color) < ON_COLOR_THRESHOLD else dark

    # -- native Qt objects ------------------------------------------------
    def palette(self) -> QPalette:
        c, R, G = self.tokens.colors, QPalette.ColorRole, QPalette.ColorGroup
        pal = QPalette()
        for role, color in (
                (R.Window, c.background), (R.WindowText, c.text_primary),
                (R.Base, c.surface_primary), (R.AlternateBase, c.surface_elevated),
                (R.Text, c.text_primary), (R.PlaceholderText, c.text_muted),
                (R.Button, c.surface_elevated), (R.ButtonText, c.text_primary),
                (R.BrightText, c.status_error), (R.Highlight, c.surface_overlay),
                (R.HighlightedText, c.text_primary), (R.ToolTipBase, c.surface_overlay),
                (R.ToolTipText, c.text_primary), (R.Link, c.status_info),
                (R.LinkVisited, c.status_info), (R.Accent, c.accent),
                (R.Light, self.hover(c.surface_elevated)), (R.Midlight, c.surface_elevated),
                (R.Mid, c.border), (R.Dark, c.background), (R.Shadow, c.background)):
            pal.setColor(role, QColor(color))
        for role in (R.WindowText, R.Text, R.ButtonText):
            pal.setColor(G.Disabled, role, QColor(c.text_muted))
        return pal

    def font(self, role='body') -> QFont:
        token = self._type(role)
        f = QFont()
        f.setFamilies([part.strip().strip('"') for part in token.family.split(',')])
        f.setPixelSize(token.size)
        f.setWeight(QFont.Weight(token.weight))
        if role == 'monospace':
            f.setStyleHint(QFont.StyleHint.Monospace)
        return f

    def _type(self, role) -> TypeToken:
        if role not in TYPE_ROLES:
            raise ValueError(f'unknown type role {role!r}')
        return getattr(self.tokens.typography, role)

    # -- QSS builders -----------------------------------------------------
    def font_style(self, role='body'):
        t = self._type(role)
        return f'font-family: {t.family}; font-size: {t.size}px; font-weight: {t.weight};'

    def text_style(self, role='primary', type_role='body'):
        return f'color: {self.text(role)}; {self.font_style(type_role)}'

    def status_style(self, status, type_role='body'):
        return f'color: {self.status_color(status)}; {self.font_style(type_role)}'

    def status_badge_style(self, status):
        color, s = self.status_color(status), self.tokens.spacing
        return (f'background: {color}; color: {self.on_color(color)}; font-weight: 600; '
                f'border-radius: {_px(self.radius("sm"))}; padding: 2px {s.xs}px; '
                f'{self.font_style("caption")}')

    def surface_style(self, tier='primary', selector=None, radius='md', bordered=True):
        border = f'1px solid {self.tokens.colors.border}' if bordered else 'none'
        body = (f'background: {self.surface(tier)}; border: {border}; '
                f'border-radius: {_px(self.radius(radius))};')
        return f'{selector} {{ {body} }}' if selector else body

    def control_style(self, selector='QPushButton'):
        c, s, r = self.tokens.colors, self.tokens.spacing, _px(self.tokens.radii.md)
        return (
            f'{selector} {{ background: {c.surface_elevated}; color: {c.text_primary}; '
            f'border: 1px solid {c.surface_elevated}; border-radius: {r}; '
            f'padding: {s.xxs}px {s.sm}px; }}\n'
            f'{selector}:hover {{ background: {self.hover(c.surface_elevated)}; }}\n'
            f'{selector}:focus {{ border: 1px solid {c.focus_ring}; }}\n'
            f'{selector}:disabled {{ color: {c.text_muted}; }}')

    def accent_control_style(self, selector='QPushButton'):
        c, s, r = self.tokens.colors, self.tokens.spacing, _px(self.tokens.radii.sm)
        return (
            f'{selector} {{ background: {c.accent}; color: {self.on_color(c.accent)}; '
            f'font-weight: 600; border: 1px solid {c.accent}; border-radius: {r}; '
            f'padding: {s.xxs // 2}px {s.xs}px; }}\n'
            f'{selector}:hover {{ background: {self.hover(c.accent)}; }}\n'
            f'{selector}:focus {{ border: 1px solid {c.focus_ring}; }}')

    def input_style(self, selector='QLineEdit'):
        c, s, r = self.tokens.colors, self.tokens.spacing, _px(self.tokens.radii.md)
        return (f'{selector} {{ background: {c.surface_primary}; color: {c.text_primary}; '
                f'border: 1px solid {c.border}; border-radius: {r}; padding: {s.xxs}px; }}\n'
                f'{_pseudo(selector, "focus")} {{ border: 1px solid {c.focus_ring}; }}')

    def stylesheet(self):
        """App-wide stylesheet: the token-driven equivalent of the original app STYLE."""
        c, s, rad = self.tokens.colors, self.tokens.spacing, self.tokens.radii
        md, lg, sel = _px(rad.md), _px(rad.lg), c.surface_overlay
        inputs = 'QTextBrowser, QTextEdit, QPlainTextEdit, QLineEdit, QListWidget'
        return '\n'.join((
            f'QWidget#root {{ background: {c.background}; border: 1px solid {c.border}; '
            f'border-radius: {lg}; }}',
            f'QDialog {{ background: {c.background}; }}',
            f'QLabel, QCheckBox {{ color: {c.text_secondary}; }}',
            self.input_style(inputs),
            f'QComboBox {{ background: {c.surface_primary}; color: {c.text_primary}; '
            f'border: 1px solid {c.border}; border-radius: {md}; padding: {s.xxs}px {s.xs}px; }}',
            f'QComboBox:focus {{ border: 1px solid {c.focus_ring}; }}',
            f'QComboBox QAbstractItemView {{ background: {c.surface_primary}; '
            f'color: {c.text_primary}; selection-background-color: {sel}; }}',
            f'QListWidget::item {{ padding: {s.xxs}px; border-bottom: 1px solid {c.border}; }}',
            f'QListWidget::item:selected {{ background: {sel}; color: {c.text_primary}; }}',
            self.control_style('QPushButton'),
            'QTabWidget::pane { border: none; }',
            f'QTabBar::tab {{ background: {c.surface_primary}; color: {c.text_muted}; '
            f'padding: {s.xxs}px {s.sm}px; border-radius: {md}; margin-right: {s.xxs}px; }}',
            f'QTabBar::tab:selected {{ background: {sel}; color: {c.text_primary}; }}',
            f'QTabBar::tab:focus {{ border: 1px solid {c.focus_ring}; }}',
            f'QMenu {{ background: {c.surface_primary}; color: {c.text_primary}; '
            f'border: 1px solid {c.border}; }}',
            f'QMenu::item:selected {{ background: {sel}; }}',
            f'QToolTip {{ background: {c.surface_overlay}; color: {c.text_primary}; '
            f'border: 1px solid {c.border}; }}',
        )) + '\n'
