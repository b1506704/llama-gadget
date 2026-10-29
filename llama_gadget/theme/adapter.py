"""Turns ThemeTokens into Qt stylesheets so widgets never carry raw color or size literals."""
from .tokens import ThemeTokens

FOCUSABLE = ('QLineEdit', 'QTextEdit', 'QListWidget', 'QComboBox', 'QPushButton', 'QCheckBox')


class ThemeAdapter:
    @staticmethod
    def splitter_stylesheet(tokens: ThemeTokens) -> str:
        c, s, r = tokens.colors, tokens.spacing, tokens.radii
        line = f'{s.hairline}px solid'
        focus = ', '.join(f'QWidget[zone] {kind}:focus' for kind in FOCUSABLE)
        return (
            f'QSplitter#workspace::handle {{ background: {c.divider}; margin: {s.md}px 0; '
            f'border-radius: {r.sm}px; }}\n'
            f'QSplitter#workspace::handle:hover {{ background: {c.handle_hover}; }}\n'
            f'QSplitter#workspace::handle:pressed {{ background: {c.handle_active}; }}\n'
            f'QWidget[zone="sidebar"], QWidget[zone="inspector"] {{ background: {c.surface_raised}; '
            f'border: {line} {c.border}; border-radius: {r.md}px; }}\n'
            f'QWidget[zone="canvas"] {{ background: {c.surface}; }}\n'
            f'{focus} {{ border: {line} {c.focus_ring}; }}\n')

    @staticmethod
    def handle_width(tokens: ThemeTokens) -> int:
        return tokens.spacing.xs
