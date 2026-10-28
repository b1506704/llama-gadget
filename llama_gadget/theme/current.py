"""Process-wide active theme for widgets that have no ThemeManager reference.

`ThemeManager` publishes every applied palette here; reusable widgets read the active
adapter at build/render time and register a refresh method with `bind_theme`.
"""
import weakref

from PyQt6 import sip
from PyQt6.QtCore import QObject, pyqtSignal

from .adapter import ThemeAdapter
from .palettes import DARK_TOKENS


class ThemeHub(QObject):
    theme_changed = pyqtSignal(object)  # ThemeTokens


_state = {'adapter': ThemeAdapter(DARK_TOKENS), 'hub': None}


def active_adapter() -> ThemeAdapter:
    return _state['adapter']


def active_tokens():
    return _state['adapter'].tokens


def theme_hub() -> ThemeHub:
    if _state['hub'] is None:
        _state['hub'] = ThemeHub()
    return _state['hub']


def set_active_adapter(adapter: ThemeAdapter):
    """Publish `adapter` as the active theme and refresh every bound widget."""
    _state['adapter'] = adapter
    theme_hub().theme_changed.emit(adapter.tokens)


def bind_theme(method):
    """Call `method(adapter)` now and after every theme change while its QObject owner lives.

    `method` must be a bound method of a QObject; the binding holds it weakly and drops
    itself once the owner is garbage collected or its C++ object is deleted.
    """
    if not isinstance(getattr(method, '__self__', None), QObject):
        raise TypeError('bind_theme expects a bound method of a QObject')
    ref, hub = weakref.WeakMethod(method), theme_hub()

    def refresh(_tokens):
        fn = ref()
        if fn is None or sip.isdeleted(fn.__self__):
            hub.theme_changed.disconnect(refresh)
            return
        fn(active_adapter())

    hub.theme_changed.connect(refresh)
    method(active_adapter())
