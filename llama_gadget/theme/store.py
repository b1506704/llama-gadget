"""Persists the ThemePreference in a small JSON preferences file next to the MCP config."""
import json
import os
from pathlib import Path
import tempfile
from typing import Protocol

from PyQt6.QtCore import QStandardPaths

from .controller import ThemePreference

THEME_KEY = 'theme'


class ThemePersistError(Exception):
    pass


class ThemePreferenceStore(Protocol):
    def load_theme_preference(self) -> ThemePreference: ...

    def save_theme_preference(self, preference: ThemePreference) -> None: ...


def default_preferences_path():
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericConfigLocation)
    return Path(base) / 'llama-gadget' / 'preferences.json'


class JsonThemePreferenceStore:
    """`theme` key in a JSON object file; unrelated keys are preserved on save."""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_preferences_path()

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def load_theme_preference(self) -> ThemePreference:
        try:
            return ThemePreference(self._read().get(THEME_KEY))
        except ValueError:
            return ThemePreference.default()

    def save_theme_preference(self, preference) -> None:
        data = {**self._read(), THEME_KEY: ThemePreference(preference).value}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(prefix='.preferences-', dir=self.path.parent)
        except OSError as exc:
            raise ThemePersistError(f'cannot save theme preference: {exc}') from exc
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise ThemePersistError(f'cannot save theme preference: {exc}') from exc
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
