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


def contrast_ratio(color1, color2):
    """WCAG 2.1 contrast ratio between two colors: in range [1.0, 21.0]."""
    l1 = luminance(color1)
    l2 = luminance(color2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


class ThemeAdapter:
    """Translates ThemeTokens into Qt QPalette, QFont, and stylesheet declarations."""

    def __init__(self, tokens: ThemeTokens):
        self.tokens = tokens

    def palette(self) -> QPalette:
        p = QPalette()
        c = self.tokens.colors
        p.setColor(QPalette.ColorRole.Window, QColor(c.surface_background))
        p.setColor(QPalette.ColorRole.WindowText, QColor(c.text_primary))
        p.setColor(QPalette.ColorRole.Base, QColor(c.surface_primary))
        p.setColor(QPalette.ColorRole.AlternateBase, QColor(c.surface_elevated))
        p.setColor(QPalette.ColorRole.ToolTipBase, QColor(c.surface_overlay))
        p.setColor(QPalette.ColorRole.ToolTipText, QColor(c.text_primary))
        p.setColor(QPalette.ColorRole.Text, QColor(c.text_primary))
        p.setColor(QPalette.ColorRole.Button, QColor(c.surface_elevated))
        p.setColor(QPalette.ColorRole.ButtonText, QColor(c.text_primary))
        p.setColor(QPalette.ColorRole.Highlight, QColor(c.accent))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor(self.on_color(c.accent)))
        p.setColor(QPalette.ColorRole.PlaceholderText, QColor(c.text_muted))
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c.text_muted))
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(c.text_muted))
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(c.text_muted))
        return p

    def font(self, role: str = 'body') -> QFont:
        t: TypeToken = getattr(self.tokens.typography, role)
        f = QFont(t.font_family, int(t.font_size))
        f.setWeight(QFont.Weight(t.font_weight))
        if role == 'monospace':
            f.setStyleHint(QFont.StyleHint.Monospace)
        return f

    def on_color(self, fill_hex: str) -> str:
        """Pick text_primary or background to guarantee WCAG AA on a given fill."""
        return self.tokens.colors.background if luminance(fill_hex) > ON_COLOR_THRESHOLD else self.tokens.colors.text_primary

    def meets_wcag_aa(self, fg: str, bg: str, large_text: bool = False) -> bool:
        threshold = 3.0 if large_text else 4.5
        return contrast_ratio(fg, bg) >= threshold

    def highlight_color(self, alpha: float = 0.2) -> str:
        q = QColor(self.tokens.colors.accent)
        return f'rgba({q.red()}, {q.green()}, {q.blue()}, {alpha:.2f})'

    def stylesheet(self, scale: float = 1.0, base_pt: float = 10.0) -> str:
        """Build the complete application stylesheet with token variables and component styles."""
        t = self.tokens
        c = t.colors
        r = t.radii
        f = self.font()
        sel = self.highlight_color(0.2)
        font_pt = int(f.pointSizeF() * scale) if f.pointSizeF() > 0 else int(base_pt * scale)

        return '\n'.join((
            f'QWidget {{ font-family: "{f.family()}"; font-size: {font_pt}pt; }}',
            f'*[keyboardFocus="true"] {{ border: 2px solid {c.focus_ring}; outline: none; }}',
            f'QMainWindow, QDialog {{ background: {c.surface_background}; color: {c.text_primary}; }}',
            f'QSplitter::handle {{ background: {c.border}; }}',
            f'QLineEdit, QTextEdit, QPlainTextEdit {{ background: {c.surface_primary}; color: {c.text_primary}; '
            f'border: 1px solid {c.border}; border-radius: {r.md}px; padding: 4px; }}',
            f'QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {c.focus_ring}; }}',
            f'QPushButton {{ background: {c.surface_elevated}; color: {c.text_primary}; '
            f'border: 1px solid {c.border}; border-radius: {r.md}px; padding: 4px 12px; }}',
            f'QPushButton:hover {{ background: {self.highlight_color(0.15)}; }}',
            f'QPushButton:pressed {{ background: {self.highlight_color(0.25)}; }}',
            f'QScrollBar:vertical {{ background: {c.surface_background}; width: 10px; margin: 0; }}',
            f'QScrollBar::handle:vertical {{ background: {c.border}; min-height: 20px; border-radius: {r.sm}px; }}',
            f'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}',
            f'QMenu {{ background: {c.surface_overlay}; color: {c.text_primary}; '
            f'border: 1px solid {c.border}; }}',
            f'QMenu::item:selected {{ background: {sel}; }}',
            f'QToolTip {{ background: {c.surface_overlay}; color: {c.text_primary}; '
            f'border: 1px solid {c.border}; }}',
        )) + '\n'

    def app_stylesheet(self, scale: float = 1.0, base_pt: float = 10.0) -> str:
        """Alias for stylesheet() to maintain backward compatibility."""
        return self.stylesheet(scale=scale, base_pt=base_pt)
