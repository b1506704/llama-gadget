"""Qt glue: applies the active palette app-wide and broadcasts theme changes."""
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from .adapter import ThemeAdapter
from .current import set_active_adapter
from .controller import (SystemAppearance, ThemeController, ThemePreference,
                         appearance_from_color_scheme, detect_system_appearance)


class TokensProxy:
    def __init__(self, getter):
        self._getter = getter

    def __call__(self):
        return self._getter()

    def __getattr__(self, name):
        return getattr(self._getter(), name)

    def __eq__(self, other):
        if isinstance(other, TokensProxy):
            return self._getter() == other._getter()
        return self._getter() == other

    def __repr__(self):
        return repr(self._getter())


class ThemeManager(QObject):
    """Owns the ThemeController, persists preference changes, and restyles the QApplication.

    `theme_changed(ThemeTokens)` is the hook for widgets that build their own token-derived
    styles; it fires after the app-wide palette and stylesheet are applied and the palette is
    published to `theme.current` (which refreshes widgets registered with `bind_theme`).
    """
    theme_changed = pyqtSignal(object)

    def __init__(self, store, detector=detect_system_appearance, app=None, parent=None):
        super().__init__(parent)
        self.store = store
        self._detector = detector
        self._app = app or QApplication.instance()
        self.controller = ThemeController(store.load_theme_preference(), detector())
        self.adapter = ThemeAdapter(self.controller.active_tokens())
        set_active_adapter(self.adapter)
        self._app.styleHints().colorSchemeChanged.connect(self._on_color_scheme_changed)

    def preference(self) -> ThemePreference:
        return self.controller.preference()

    @property
    def tokens(self):
        return TokensProxy(lambda: self.controller.active_tokens())

    @property
    def mode(self) -> str:
        pref = self.controller.preference()
        if pref is ThemePreference.SYSTEM:
            return self.controller.system_appearance().value
        return pref.value

    @mode.setter
    def mode(self, val: str):
        self.set_preference(val)

    def toggle(self):
        current = self.mode
        new_mode = "light" if current == "dark" else "dark"
        self.set_preference(new_mode)
        return self.controller.active_tokens()

    def apply(self):
        """Restyle every open window in place; widgets and their state are untouched."""
        tokens = self.controller.active_tokens()
        self.adapter = ThemeAdapter(tokens)
        self._app.setPalette(self.adapter.palette())
        self._app.setStyleSheet(self.adapter.stylesheet())
        set_active_adapter(self.adapter)
        self.theme_changed.emit(tokens)
        return tokens

    def set_preference(self, preference):
        """Select a mode, apply it, then persist. Raises ThemePersistError only after applying."""
        preference = ThemePreference(preference)
        if preference is ThemePreference.SYSTEM:
            self.controller.set_system_appearance(self._detector())
        self.controller.set_preference(preference)
        tokens = self.apply()
        self.store.save_theme_preference(preference)
        return tokens

    def set_system_appearance(self, appearance):
        """OS appearance changed; only re-renders when following the system. Never persists."""
        before = self.controller.active_tokens()
        tokens = self.controller.set_system_appearance(SystemAppearance(appearance))
        if tokens is not before:
            self.apply()
        return tokens

    def _on_color_scheme_changed(self, scheme):
        self.set_system_appearance(appearance_from_color_scheme(scheme))
