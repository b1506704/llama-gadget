"""Central design tokens and theming: the single source of truth for GUI styling."""
from .tokens import (DEFAULT_RADII, DEFAULT_SPACING, DEFAULT_TYPOGRAPHY, Color, CornerRadii,
                     SemanticColors, SpacingScale, ThemeTokens, TypeToken, TypographyScale)
from .palettes import DARK_TOKENS, LIGHT_TOKENS
from .adapter import ThemeAdapter
from .controller import (SystemAppearance, ThemeController, ThemePreference,
                         appearance_from_color_scheme, detect_system_appearance)
from .store import (JsonThemePreferenceStore, ThemePersistError, ThemePreferenceStore,
                    default_preferences_path)
from .runtime import ThemeManager
from .current import (active_adapter, active_tokens, bind_theme, default_preferences_path,
                      set_active_adapter, theme_hub)

DARK = DARK_TOKENS
LIGHT = LIGHT_TOKENS


def stylesheet(tokens=None):
    from .adapter import ThemeAdapter
    toks = tokens or active_tokens()
    return ThemeAdapter(toks).app_stylesheet()


def toggle():
    hub = theme_hub()
    if hub is not None:
        hub.toggle()


__all__ = ['Color', 'CornerRadii', 'DARK_TOKENS', 'JsonThemePreferenceStore', 'LIGHT_TOKENS',
           'SemanticColors', 'SpacingScale', 'SystemAppearance', 'ThemeAdapter', 'ThemeController',
           'ThemeManager', 'ThemePersistError', 'ThemePreference', 'ThemePreferenceStore',
           'ThemeTokens', 'TypeToken', 'TypographyScale', 'appearance_from_color_scheme',
           'active_adapter', 'active_tokens', 'bind_theme', 'default_preferences_path',
           'detect_system_appearance', 'set_active_adapter', 'theme_hub',
           'DARK', 'LIGHT', 'stylesheet', 'toggle']
