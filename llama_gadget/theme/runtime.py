"""Qt glue: applies the active palette app-wide and broadcasts theme changes."""
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from .adapter import ThemeAdapter
from .controller import (SystemAppearance, ThemeController, ThemePreference,
                         appearance_from_color_scheme, detect_system_appearance)


class ThemeManager(QObject):
    """Owns the ThemeController, persists preference changes, and restyles the QApplication.

    `theme_changed(ThemeTokens)` is the single hook for widgets that build their own
    token-derived styles; it fires after the app-wide palette and stylesheet are applied.
    """
    theme_changed = pyqtSignal(object)

    def __init__(self, store, detector=detect_system_appearance, app=None, parent=None):
        super().__init__(parent)
        self.store = store
        self._detector = detector
        self._app = app or QApplication.instance()
        self.controller = ThemeController(store.load_theme_preference(), detector())
        self.adapter = ThemeAdapter(self.controller.active_tokens())
        self._app.styleHints().colorSchemeChanged.connect(self._on_color_scheme_changed)

    def preference(self) -> ThemePreference:
        return self.controller.preference()

    def tokens(self):
        return self.controller.active_tokens()

    def apply(self):
        """Restyle every open window in place; widgets and their state are untouched."""
        tokens = self.controller.active_tokens()
        self.adapter = ThemeAdapter(tokens)
        self._app.setPalette(self.adapter.palette())
        self._app.setStyleSheet(self.adapter.stylesheet())
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
