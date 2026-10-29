"""Workspace layout section of ~/.config/llama-gadget/preferences.json."""
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import tempfile

from PyQt6.QtCore import QStandardPaths

from .splitter import DEFAULT_BOUNDS, WorkspaceZone

SECTION = 'layout'
WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT, WINDOW_MAX = 320, 240, 16384
POSITION_LIMIT = 100000
SIDEBAR, INSPECTOR = DEFAULT_BOUNDS[WorkspaceZone.SIDEBAR], DEFAULT_BOUNDS[WorkspaceZone.INSPECTOR]


def default_preferences_path():
    return Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericConfigLocation)) / 'llama-gadget' / 'preferences.json'


@dataclass(frozen=True)
class LayoutPreferences:
    window_width: int = 1120
    window_height: int = 720
    window_x: int | None = None
    window_y: int | None = None
    sidebar_width: int = SIDEBAR.default_width
    inspector_width: int = INSPECTOR.default_width
    sidebar_collapsed: bool = False
    inspector_collapsed: bool = False


def _number(value, default, low, high):
    if type(value) not in (int, float) or not math.isfinite(value):
        return default
    return min(high, max(low, int(value)))


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_layout_preferences(path=None) -> LayoutPreferences:
    """Read the layout section, clamping to valid bounds; anything missing or corrupt gets the default."""
    data = _read(path or default_preferences_path()).get(SECTION)
    default = LayoutPreferences()
    if not isinstance(data, dict):
        return default
    x = _number(data.get('window_x'), None, -POSITION_LIMIT, POSITION_LIMIT)
    y = _number(data.get('window_y'), None, -POSITION_LIMIT, POSITION_LIMIT)
    if x is None or y is None:
        x = y = None
    flag = lambda key: data[key] if type(data.get(key)) is bool else getattr(default, key)
    return LayoutPreferences(
        window_width=_number(data.get('window_width'), default.window_width, WINDOW_MIN_WIDTH, WINDOW_MAX),
        window_height=_number(data.get('window_height'), default.window_height, WINDOW_MIN_HEIGHT, WINDOW_MAX),
        window_x=x, window_y=y,
        sidebar_width=_number(data.get('sidebar_width'), default.sidebar_width, SIDEBAR.min_width, SIDEBAR.max_width),
        inspector_width=_number(data.get('inspector_width'), default.inspector_width,
                                INSPECTOR.min_width, INSPECTOR.max_width),
        sidebar_collapsed=flag('sidebar_collapsed'), inspector_collapsed=flag('inspector_collapsed'))


def save_layout_preferences(prefs: LayoutPreferences, path=None) -> None:
    """Merge the layout section into preferences.json, keeping other keys, via temp file + os.replace."""
    path = Path(path or default_preferences_path())
    data = _read(path)
    data[SECTION] = asdict(prefs)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.preferences-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
