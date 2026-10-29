"""Runtime theme state: user preference + OS appearance -> the active ThemeTokens palette."""
from enum import Enum

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication

from .palettes import DARK_TOKENS, LIGHT_TOKENS
from .tokens import ThemeTokens


class ThemePreference(str, Enum):
    DARK = 'dark'
    LIGHT = 'light'
    SYSTEM = 'system'

    @classmethod
    def default(cls):
        return cls.SYSTEM


class SystemAppearance(str, Enum):
    DARK = 'dark'
    LIGHT = 'light'

    @classmethod
    def default(cls):
        return cls.DARK


_PALETTES = {SystemAppearance.DARK: DARK_TOKENS, SystemAppearance.LIGHT: LIGHT_TOKENS}


class ThemeController:
    """Holds the selected preference and last known OS appearance; resolves the shared palette."""

    def __init__(self, preference=ThemePreference.SYSTEM, system_appearance=SystemAppearance.DARK):
        self._preference = ThemePreference(preference)
        self._system_appearance = SystemAppearance(system_appearance)

    def preference(self) -> ThemePreference:
        return self._preference

    def system_appearance(self) -> SystemAppearance:
        return self._system_appearance

    def set_preference(self, preference) -> ThemeTokens:
        self._preference = ThemePreference(preference)
        return self.active_tokens()

    def set_system_appearance(self, appearance) -> ThemeTokens:
        self._system_appearance = SystemAppearance(appearance)
        return self.active_tokens()

    def active_appearance(self) -> SystemAppearance:
        if self._preference is ThemePreference.SYSTEM:
            return self._system_appearance
        return SystemAppearance(self._preference.value)

    def active_tokens(self) -> ThemeTokens:
        return _PALETTES[self.active_appearance()]


def appearance_from_color_scheme(scheme) -> SystemAppearance:
    """Map a Qt color scheme (incl. the colorSchemeChanged payload) to SystemAppearance."""
    return SystemAppearance.LIGHT if scheme == Qt.ColorScheme.Light else SystemAppearance.DARK


def detect_system_appearance() -> SystemAppearance:
    """Current OS appearance; Unknown, no application, or any backend failure -> Dark."""
    if QGuiApplication.instance() is None:
        return SystemAppearance.default()
    try:
        return appearance_from_color_scheme(QGuiApplication.styleHints().colorScheme())
    except Exception:  # noqa: BLE001 - detection must never break startup
        return SystemAppearance.default()
