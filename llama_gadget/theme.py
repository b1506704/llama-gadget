"""Theme color tokens, WCAG 2.1 contrast audit, and the token-driven Qt stylesheet."""
from dataclasses import dataclass

TEXT_MIN = 4.5   # WCAG AA normal text
UI_MIN = 3.0     # WCAG AA large text and active UI components (SC 1.4.11)

TEXT_ROLES = ('text_primary', 'text_secondary', 'text_muted')
UI_ROLES = ('accent', 'border', 'focus_ring', 'status_success', 'status_warning', 'status_error', 'status_info')
SURFACES = ('background', 'surface_primary', 'surface_elevated', 'surface_overlay')


@dataclass(frozen=True)
class ThemeTokens:
    background: str        # window root
    surface_primary: str   # inputs, lists, transcript
    surface_elevated: str  # buttons, dialogs' controls
    surface_overlay: str   # hover, selection, selected tab
    text_primary: str
    text_secondary: str
    text_muted: str
    text_disabled: str     # disabled controls are exempt from WCAG contrast; not audited
    accent: str            # primary call-to-action background
    on_accent: str         # text drawn on accent
    border: str            # input/control outlines
    divider: str           # decorative separators; not audited
    focus_ring: str
    status_success: str
    status_warning: str
    status_error: str
    status_info: str


DARK_TOKENS = ThemeTokens(
    background='#14161a', surface_primary='#1b1e24', surface_elevated='#2a2e35', surface_overlay='#2a3140',
    text_primary='#e6e8eb', text_secondary='#d0d4da', text_muted='#9ea6b0', text_disabled='#6b7078',
    accent='#3fb950', on_accent='#0d1117', border='#808893', divider='#2a2e35', focus_ring='#58a6ff',
    status_success='#3fb950', status_warning='#d29922', status_error='#f85149', status_info='#58a6ff')

LIGHT_TOKENS = ThemeTokens(
    background='#ffffff', surface_primary='#f6f8fa', surface_elevated='#eaeef2', surface_overlay='#dde4ec',
    text_primary='#1f2328', text_secondary='#31373d', text_muted='#525a64', text_disabled='#8c959f',
    accent='#1a7f37', on_accent='#ffffff', border='#6a737d', divider='#d0d7de', focus_ring='#0550ae',
    status_success='#1a7f37', status_warning='#8a5a00', status_error='#c4202b', status_info='#0550ae')


def _rgb(color):
    c = color.lstrip('#')
    if len(c) == 3:
        c = ''.join(ch * 2 for ch in c)
    if len(c) != 6:
        raise ValueError(f'expected #rrggbb color, got {color!r}')
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def relative_luminance(color):
    """WCAG 2.1 relative luminance of an sRGB hex color, in [0, 1]."""
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground, background):
    """WCAG 2.1 contrast ratio in [1, 21]; symmetric in its arguments."""
    hi, lo = sorted((relative_luminance(foreground), relative_luminance(background)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@dataclass(frozen=True)
class ContrastViolation:
    pairing: str
    foreground: str
    background: str
    ratio: float
    required: float

    def __str__(self):
        return (f'{self.pairing}: {self.foreground} on {self.background} = {self.ratio:.2f}:1 '
                f'(needs >= {self.required}:1)')


def contrast_pairings(tokens):
    """(name, foreground, background, required) for every audited token pairing."""
    out = [(f'{fg}/{bg}', getattr(tokens, fg), getattr(tokens, bg), TEXT_MIN) for fg in TEXT_ROLES for bg in SURFACES]
    out += [(f'{fg}/{bg}', getattr(tokens, fg), getattr(tokens, bg), UI_MIN) for fg in UI_ROLES for bg in SURFACES]
    out.append(('on_accent/accent', tokens.on_accent, tokens.accent, TEXT_MIN))
    return out


def audit_theme_contrast(tokens):
    return [ContrastViolation(name, fg, bg, ratio, required) for name, fg, bg, required in contrast_pairings(tokens)
            if (ratio := contrast_ratio(fg, bg)) < required]


def stylesheet(t=DARK_TOKENS, scale=1.0, base_pt=10.0):
    """Global stylesheet for the gadget and its dialogs, scaled by the UI zoom factor."""
    def px(n):
        return max(1, round(n * scale))
    pt, small = f'{base_pt * scale:.1f}pt', f'{base_pt * 0.85 * scale:.1f}pt'
    return f"""
QWidget {{ font-size: {pt}; }}
QWidget#root {{ background: {t.background}; border: 1px solid {t.divider}; border-radius: {px(10)}px; }}
QDialog {{ background: {t.background}; }}
QLabel, QCheckBox {{ color: {t.text_secondary}; }}
QLabel[muted="true"] {{ color: {t.text_muted}; font-size: {small}; }}
QTextBrowser, QTextEdit, QPlainTextEdit, QLineEdit, QListWidget, QTreeWidget {{ background: {t.surface_primary};
    color: {t.text_primary}; border: 1px solid {t.border}; border-radius: {px(6)}px; padding: {px(4)}px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: {t.surface_primary}; }}
QComboBox {{ background: {t.surface_primary}; color: {t.text_primary}; border: 1px solid {t.border};
    border-radius: {px(6)}px; padding: {px(4)}px {px(8)}px; }}
QComboBox QAbstractItemView {{ background: {t.surface_primary}; color: {t.text_primary};
    selection-background-color: {t.surface_overlay}; selection-color: {t.text_primary}; }}
QListWidget::item {{ padding: {px(4)}px; border-bottom: 1px solid {t.divider}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: {t.surface_overlay}; color: {t.text_primary}; }}
QPushButton {{ background: {t.surface_elevated}; color: {t.text_primary}; border: 2px solid transparent;
    border-radius: {px(6)}px; padding: {px(3)}px {px(8)}px; }}
QPushButton:hover {{ background: {t.surface_overlay}; }}
QPushButton:disabled {{ color: {t.text_disabled}; }}
QPushButton[accent="true"] {{ background: {t.accent}; color: {t.on_accent}; font-weight: bold; font-size: {small}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: {t.surface_primary}; color: {t.text_secondary}; padding: {px(5)}px {px(12)}px;
    border: 2px solid transparent; border-radius: {px(6)}px; margin-right: {px(3)}px; }}
QTabBar::tab:selected {{ background: {t.surface_overlay}; color: {t.text_primary}; }}
QMenu {{ background: {t.surface_primary}; color: {t.text_primary}; border: 1px solid {t.border}; }}
QMenu::item:selected {{ background: {t.surface_overlay}; }}
*[keyboardFocus="true"] {{ border: 2px solid {t.focus_ring}; }}
QTextBrowser[keyboardFocus="true"], QTextEdit[keyboardFocus="true"], QPlainTextEdit[keyboardFocus="true"],
QLineEdit[keyboardFocus="true"], QListWidget[keyboardFocus="true"], QTreeWidget[keyboardFocus="true"],
QComboBox[keyboardFocus="true"] {{ border: 2px solid {t.focus_ring}; padding: {px(4) - 1}px; }}
QComboBox[keyboardFocus="true"] {{ padding: {px(4) - 1}px {px(8) - 1}px; }}
QTabBar[keyboardFocus="true"] {{ border: none; }}
QTabBar[keyboardFocus="true"]::tab:selected {{ border: 2px solid {t.focus_ring}; }}
"""
