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
from .current import (active_adapter, active_tokens, bind_theme, set_active_adapter, theme_hub)

DARK = DARK_TOKENS
LIGHT = LIGHT_TOKENS


def stylesheet(tokens=None, scale=1.0, base_pt=10.0):
    """Return the application stylesheet for the given tokens (or current active theme)."""
    toks = tokens or active_tokens()
    adapter = ThemeAdapter(toks)
    if hasattr(adapter, 'stylesheet'):
        return adapter.stylesheet(scale=scale, base_pt=base_pt)
    return adapter.app_stylesheet(scale=scale, base_pt=base_pt)


def toggle():
    """Toggle between light and dark themes using the runtime theme manager."""
    mgr = ThemeManager()
    mgr.toggle()
    return mgr.current_tokens


__all__ = ['Color', 'CornerRadii', 'DEFAULT_RADII', 'DEFAULT_SPACING', 'DEFAULT_TYPOGRAPHY',
           'DARK_TOKENS', 'LIGHT_TOKENS', 'JsonThemePreferenceStore', 'SemanticColors',
           'SpacingScale', 'SystemAppearance', 'ThemeAdapter', 'ThemeController',
           'ThemeManager', 'ThemePersistError', 'ThemePreference', 'ThemePreferenceStore', 'ThemeTokens',
           'TypeToken', 'TypographyScale', 'appearance_from_color_scheme',
           'active_adapter', 'active_tokens', 'bind_theme', 'default_preferences_path',
           'detect_system_appearance', 'set_active_adapter', 'theme_hub',
           'DARK', 'LIGHT', 'stylesheet', 'toggle']
