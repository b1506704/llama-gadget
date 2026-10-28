"""Keyboard focus rings and overlay focus trapping for the gadget and its dialogs."""
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QWidget

FOCUS_PROPERTY = 'keyboardFocus'
NAV_KEYS = {Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left,
            Qt.Key.Key_Right, Qt.Key.Key_Escape}
KEYBOARD_REASONS = {Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                    Qt.FocusReason.ShortcutFocusReason}


def alive(widget):
    return widget is not None and not sip.isdeleted(widget)


def focusable(widget):
    return (alive(widget) and widget.isVisible() and widget.isEnabled() and widget.focusProxy() is None
            and bool(widget.focusPolicy() & Qt.FocusPolicy.TabFocus))


def repolish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class KeyboardFocusTracker(QObject):
    """App-wide: mark the focused widget for the focus-ring stylesheet only when focus arrived by keyboard."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.navigated_by_keyboard = False
        self.focused = None
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.KeyPress and event.key() in NAV_KEYS:
            self.navigated_by_keyboard = True
        elif kind == QEvent.Type.MouseButtonPress:
            self.navigated_by_keyboard = False
        elif kind == QEvent.Type.FocusIn and isinstance(obj, QWidget):
            if event.reason() in KEYBOARD_REASONS:
                self.navigated_by_keyboard = True
            elif event.reason() == Qt.FocusReason.MouseFocusReason:
                self.navigated_by_keyboard = False
            self._mark(obj)
        elif kind == QEvent.Type.FocusOut and obj is self.focused:
            self._mark(None)
        return False

    def ring_visible(self, widget):
        return alive(widget) and widget.property(FOCUS_PROPERTY) is True

    def _mark(self, widget):
        if alive(self.focused) and self.focused is not widget and self.focused.property(FOCUS_PROPERTY):
            self.focused.setProperty(FOCUS_PROPERTY, False)
            repolish(self.focused)
        self.focused = widget
        if alive(widget) and bool(widget.property(FOCUS_PROPERTY)) != self.navigated_by_keyboard:
            widget.setProperty(FOCUS_PROPERTY, self.navigated_by_keyboard)
            repolish(widget)


class OverlayFocusTrap(QObject):
    """Confine Tab/Shift+Tab to the topmost open dialog and restore focus to its trigger when it closes.

    Every QDialog shown in the application is trapped automatically; ``push_overlay`` lets non-dialog
    overlays (e.g. a command palette widget) opt in explicitly.
    """

    def __init__(self, default_target=None, parent=None):
        super().__init__(parent)
        self.default_target = default_target  # callable -> widget used when the trigger is gone
        self.scopes = []  # [(overlay, return_target)], topmost last
        QApplication.instance().installEventFilter(self)

    def active(self):
        self.scopes = [(o, t) for o, t in self.scopes if alive(o)]
        return self.scopes[-1][0] if self.scopes else None

    def push_overlay(self, overlay, return_target=None):
        if any(o is overlay for o, _ in self.scopes):
            return
        self.scopes.append((overlay, return_target))

    def pop_overlay(self, overlay=None):
        """Drop ``overlay`` (default: topmost) and return the widget focus should go back to."""
        for i in range(len(self.scopes) - 1, -1, -1):
            if overlay is None or self.scopes[i][0] is overlay:
                _, target = self.scopes.pop(i)
                break
        else:
            return None
        if focusable(target) and not any(o.isAncestorOf(target) for o, _ in self.scopes if alive(o)):
            return target
        top = self.active()
        if top is not None:
            items = self.focusables(top)
            return items[0] if items else None
        fallback = self.default_target() if self.default_target else None
        return fallback if focusable(fallback) else None

    def focusables(self, overlay):
        """Focusable descendants of ``overlay`` in the window's Tab chain order."""
        out, seen, widget = [], set(), overlay.nextInFocusChain()
        while alive(widget) and widget is not overlay and id(widget) not in seen:
            seen.add(id(widget))
            if overlay.isAncestorOf(widget) and focusable(widget):
                out.append(widget)
            widget = widget.nextInFocusChain()
        return out

    def handle_tab(self, current, backward=False):
        overlay = self.active()
        if overlay is None:
            return None
        items = self.focusables(overlay)
        if not items:
            return None
        if current not in items:
            return items[-1] if backward else items[0]
        return items[(items.index(current) + (-1 if backward else 1)) % len(items)]

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.Show and isinstance(obj, QDialog) and obj.isWindow():
            trigger = QApplication.focusWidget()
            self.push_overlay(obj, trigger if alive(trigger) and not obj.isAncestorOf(trigger) else None)
        elif kind == QEvent.Type.Hide and any(o is obj for o, _ in self.scopes):
            target = self.pop_overlay(obj)
            if target is not None:
                # Defer until Qt has re-activated the parent window, which would otherwise restore its own focus.
                QTimer.singleShot(0, lambda: self._restore(target))
        elif (kind == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
              and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
              and isinstance(obj, QWidget)):
            overlay = self.active()
            if overlay is not None and (obj is overlay or overlay.isAncestorOf(obj)):
                backward = event.key() == Qt.Key.Key_Backtab
                nxt = self.handle_tab(QApplication.focusWidget(), backward)
                if nxt is not None:
                    nxt.setFocus(Qt.FocusReason.BacktabFocusReason if backward else Qt.FocusReason.TabFocusReason)
                return True
        return False

    def _restore(self, target):
        if not focusable(target):
            return
        window = target.window()
        if not window.isActiveWindow():
            window.activateWindow()
        target.setFocus(Qt.FocusReason.OtherFocusReason)
