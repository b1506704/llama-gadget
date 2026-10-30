"""Runtime theme state: user preference + OS appearance -> the active ThemeTokens palette."""
from enum import Enum

from PyQt6.QtCore import QObject, QSettings, Qt, pyqtSignal
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


class ThemeController(QObject):
    """Holds the selected preference and last known OS appearance; resolves the shared palette."""
    changed = pyqtSignal(object)

    def __init__(self, preference=ThemePreference.SYSTEM, system_appearance=SystemAppearance.DARK, parent=None):
        super().__init__(parent)
        if isinstance(preference, QSettings):
            self.settings = preference
            stored = self.settings.value('theme', 'dark')
            self.mode = stored if stored in ('dark', 'light') else 'dark'
            self._preference = ThemePreference.DARK if self.mode == 'dark' else ThemePreference.LIGHT
            self._system_appearance = SystemAppearance.DARK
        else:
            self.settings = None
            if isinstance(preference, str) and preference in ('dark', 'light', 'system'):
                self._preference = ThemePreference(preference)
            elif isinstance(preference, ThemePreference):
                self._preference = preference
            else:
                self._preference = ThemePreference.SYSTEM
            self._system_appearance = SystemAppearance(system_appearance)
            self.mode = self._preference.value if self._preference is not ThemePreference.SYSTEM else 'dark'

    @property
    def tokens(self) -> ThemeTokens:
        return self.active_tokens()

    def preference(self) -> ThemePreference:
        return self._preference

    def system_appearance(self) -> SystemAppearance:
        return self._system_appearance

    def set_preference(self, preference) -> ThemeTokens:
        self._preference = ThemePreference(preference)
        if self._preference is not ThemePreference.SYSTEM:
            self.mode = self._preference.value
        toks = self.active_tokens()
        self.changed.emit(toks)
        return toks

    def set_system_appearance(self, appearance) -> ThemeTokens:
        self._system_appearance = SystemAppearance(appearance)
        toks = self.active_tokens()
        self.changed.emit(toks)
        return toks

    def set_mode(self, mode: str) -> ThemeTokens:
        if mode not in ('dark', 'light'):
            raise ValueError(f'unknown theme: {mode}')
        self.mode = mode
        self._preference = ThemePreference(mode)
        toks = self.active_tokens()
        if self.settings is not None:
            self.settings.setValue('theme', mode)
        self.changed.emit(toks)
        return toks

    def toggle(self) -> ThemeTokens:
        new_mode = 'light' if self.mode == 'dark' else 'dark'
        return self.set_mode(new_mode)

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
