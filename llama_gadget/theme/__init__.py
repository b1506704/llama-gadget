"""Shared semantic style tokens for status feedback widgets.

Values mirror the colours the app stylesheet (``app.STYLE``) already uses so new
status widgets match the existing dark UI from a single source."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeTokens:
    surface: str = '#14161a'
    surface_raised: str = '#1b1e24'
    border: str = '#2a2e35'
    text: str = '#e6e8eb'
    text_muted: str = '#8b949e'
    skeleton: str = '#262a31'
    idle: str = '#8b949e'
    loading: str = '#d29922'
    streaming: str = '#3fb950'
    error: str = '#f85149'
    error_surface: str = '#2a1719'
    success: str = '#3fb950'
    spacing: int = 4
    radius: int = 6
    font_small: int = 11

    def state_color(self, state) -> str:
        """Semantic colour for a lifecycle state (enum member or its value, e.g. 'Streaming')."""
        key = str(getattr(state, 'value', state)).lower()
        return {'idle': self.idle, 'loading': self.loading,
                'streaming': self.streaming, 'error': self.error}.get(key, self.text_muted)


TOKENS = ThemeTokens()

__all__ = ['ThemeTokens', 'TOKENS']
