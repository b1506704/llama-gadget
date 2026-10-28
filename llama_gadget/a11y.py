"""Keyboard focus rings, overlay focus trapping, and assistive-technology metadata for the gadget."""
import re
from dataclasses import dataclass

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractScrollArea, QAbstractSlider,
                             QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog, QLineEdit,
                             QPlainTextEdit, QRadioButton, QTabBar, QTextEdit, QTreeView, QWidget)

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


# Mirrors the roles Qt's built-in accessibility factory reports to AT-SPI/UIA/NSAccessibility for each class
# (PyQt6 does not bind QAccessible, so this table is what tests and audits can see). Most specific first.
ROLES = ((QTabBar, 'PageTabList'), (QCheckBox, 'CheckBox'), (QRadioButton, 'RadioButton'),
         (QAbstractButton, 'Button'), (QComboBox, 'ComboBox'), (QLineEdit, 'EditableText'),
         (QTextEdit, 'EditableText'), (QPlainTextEdit, 'EditableText'), (QTreeView, 'Tree'),
         (QAbstractItemView, 'List'), (QAbstractSlider, 'Slider'), (QAbstractSpinBox, 'SpinBox'),
         (QAbstractScrollArea, 'ScrollArea'), (QDialog, 'Dialog'))


@dataclass(frozen=True)
class AccessibleControlMeta:
    id: str
    role: str
    label: str


def accessible_role(widget):
    return next((role for cls, role in ROLES if isinstance(widget, cls)), None)


def accessible_label(widget):
    """The name assistive technologies receive: the explicit accessible name, else a button's own text."""
    if widget.accessibleName().strip():
        return widget.accessibleName().strip()
    if isinstance(widget, QAbstractButton):
        return widget.text().replace('&', '').strip()
    return ''


def register_accessible_control(widget, label, identifier=None):
    """Expose ``widget`` to assistive technologies with a non-empty label and a stable identifier."""
    label = (label or '').strip()
    if not re.search(r'\w', label):
        raise ValueError(f'accessible label must contain readable text, got {label!r}')
    role = accessible_role(widget)
    if role is None:
        raise ValueError(f'{type(widget).__name__} has no accessible role mapping')
    identifier = identifier or widget.objectName() or re.sub(r'\W+', '-', label.lower()).strip('-')
    if not widget.objectName():
        widget.setObjectName(identifier)
    widget.setAccessibleIdentifier(identifier)
    widget.setAccessibleName(label)
    return AccessibleControlMeta(identifier, role, label)


def interactive(widget):
    if widget.focusProxy() is not None:  # e.g. QTabWidget -> its QTabBar, which is checked itself
        return False
    return isinstance(widget, QAbstractButton) or bool(widget.focusPolicy() & Qt.FocusPolicy.TabFocus)


def label_controls(root):
    """Give every unlabeled interactive control under ``root`` a label derived from its text or tooltip."""
    for widget in root.findChildren(QWidget):
        if not interactive(widget) or widget.window() is not root.window() or widget.accessibleName():
            continue
        if isinstance(widget, QAbstractButton) and re.search(r'\w', accessible_label(widget)):
            continue  # Qt reads the live button text, which stays correct when the text changes
        text = accessible_label(widget) or widget.toolTip() or getattr(widget, 'placeholderText', str)()
        text = re.sub(r'\s*\(.*$', '', text.replace('…', '')).strip()  # drop "(Ctrl+…)" hints
        if re.search(r'\w', text) and accessible_role(widget):
            register_accessible_control(widget, text)


def accessibility_gaps(root):
    """Interactive controls in ``root``'s window lacking a role or a readable label (for audits and tests)."""
    return [w for w in [root, *root.findChildren(QWidget)]
            if interactive(w) and w.window() is root.window()
            and (accessible_role(w) is None or not re.search(r'\w', accessible_label(w)))]
