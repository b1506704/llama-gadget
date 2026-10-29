"""Semantic dark/light colour tokens, the app stylesheet built from them, and the theme toggle."""
from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass(frozen=True)
class ThemeTokens:
    name: str
    window: str        # outer window / dialog background
    surface: str       # inputs, lists, transcript
    raised: str        # buttons, hover rows
    raised_hover: str
    border: str
    divider: str
    text: str          # body text on window/surface
    text_strong: str   # input text
    text_muted: str    # captions, hints
    text_disabled: str
    selection: str     # selected rows / tabs
    selection_text: str
    badge: str         # shortcut / category badges
    badge_text: str


DARK = ThemeTokens(
    name='dark', window='#14161a', surface='#1b1e24', raised='#2a2e35', raised_hover='#363b44',
    border='#2a2e35', divider='#23272e', text='#d0d4da', text_strong='#e6e8eb', text_muted='#8b949e',
    text_disabled='#6b7078', selection='#2a3140', selection_text='#e6e8eb', badge='#2a2e35',
    badge_text='#c9d1d9')

LIGHT = ThemeTokens(
    name='light', window='#f6f8fa', surface='#ffffff', raised='#e7ebef', raised_hover='#d8dee4',
    border='#d0d7de', divider='#e5e9ee', text='#24292f', text_strong='#1f2328', text_muted='#57606a',
    text_disabled='#8c959f', selection='#dbe9ff', selection_text='#1f2328', badge='#e7ebef',
    badge_text='#24292f')

THEMES = {DARK.name: DARK, LIGHT.name: LIGHT}


def _luminance(color):
    channels = [int(color.lstrip('#')[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg):
    """WCAG 2.1 contrast ratio between two #rrggbb colours."""
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def stylesheet(t):
    return f"""
QWidget#root {{ background: {t.window}; border: 1px solid {t.border}; border-radius: 10px; }}
QDialog {{ background: {t.window}; }}
QLabel, QCheckBox {{ color: {t.text}; }}
QLabel[muted="true"] {{ color: {t.text_muted}; font-size: 11px; }}
QTextBrowser, QTextEdit, QPlainTextEdit, QLineEdit, QListWidget, QListView, QTreeWidget {{
    background: {t.surface}; color: {t.text_strong}; border: 1px solid {t.border}; border-radius: 6px; padding: 4px; }}
QComboBox {{ background: {t.surface}; color: {t.text_strong}; border: 1px solid {t.border}; border-radius: 6px; padding: 4px 8px; }}
QComboBox QAbstractItemView {{ background: {t.surface}; color: {t.text_strong}; selection-background-color: {t.selection}; }}
QListWidget::item {{ padding: 4px; border-bottom: 1px solid {t.divider}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: {t.selection}; color: {t.selection_text}; }}
QHeaderView::section {{ background: {t.raised}; color: {t.text}; border: none; padding: 4px; }}
QPushButton {{ background: {t.raised}; color: {t.text_strong}; border: none; border-radius: 6px; padding: 5px 10px; }}
QPushButton:hover {{ background: {t.raised_hover}; }}
QPushButton:disabled {{ color: {t.text_disabled}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: {t.surface}; color: {t.text_muted}; padding: 5px 12px; border-radius: 6px; margin-right: 3px; }}
QTabBar::tab:selected {{ background: {t.selection}; color: {t.selection_text}; }}
QMenu {{ background: {t.surface}; color: {t.text_strong}; border: 1px solid {t.border}; }}
QMenu::item:selected {{ background: {t.selection}; }}
"""


def transcript_stylesheet(t):
    return f'QScrollArea, QScrollArea > QWidget > QWidget {{ background: {t.surface}; }}'


class ThemeController(QObject):
    """Current theme mode, persisted in QSettings under ``theme``."""
    changed = pyqtSignal(object)

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        mode = settings.value('theme', DARK.name) if settings is not None else DARK.name
        self.mode = mode if mode in THEMES else DARK.name

    @property
    def tokens(self):
        return THEMES[self.mode]

    def set_mode(self, mode):
        if mode not in THEMES:
            raise ValueError(f'unknown theme: {mode}')
        if mode == self.mode:
            return
        self.mode = mode
        if self.settings is not None:
            self.settings.setValue('theme', mode)
        self.changed.emit(self.tokens)

    def toggle(self):
        self.set_mode(LIGHT.name if self.mode == DARK.name else DARK.name)
