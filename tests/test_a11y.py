from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog, QLabel, QLineEdit, QPushButton, QVBoxLayout

from llama_gadget.a11y import (AccessibleControlMeta, UiScaleAction, UiScaleState, accessibility_gaps, accessible_label,
                               accessible_role, register_accessible_control)
from llama_gadget.app import FolderDialog, Gadget
from llama_gadget.chat_ui import ImagePreviewDialog
from llama_gadget.images import encode_image
from llama_gadget.tool_ui import MCPConfigDialog, ToolInspectorDialog

APPLICATION = QApplication.instance() or QApplication([])


def pump_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APPLICATION.processEvents()
        if predicate():
            return True
        time.sleep(.005)
    return False


def ring_pixels(widget):
    from llama_gadget.theme import DARK_TOKENS
    ring, image = QColor(DARK_TOKENS.focus_ring), widget.grab().toImage()
    return sum(image.pixelColor(x, y) == ring for x in range(image.width()) for y in range(image.height()))


class GadgetCase(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
        settings.setValue('db_path', str(root / 'chat.db'))
        settings.setValue('profile', 'agent-gpu0')
        with patch('llama_gadget.app.QSettings', return_value=settings), patch.object(Gadget, '_poll'), \
                patch('llama_gadget.app.default_preferences_path', return_value=root / 'preferences.json'):
            self.app = Gadget()
        self.app.timer.stop()
        self.addCleanup(self.cleanup_app)
        self.app.activateWindow()
        self.assertTrue(pump_until(self.app.isActiveWindow))

    def cleanup_app(self):
        self.app._shutdown_workers()
        self.app.tray.hide()
        self.app.clipboard.dataChanged.disconnect(self.app._on_clipboard)
        self.app.db.conn.close()
        self.app.hide()
        self.app.deleteLater()
        APPLICATION.processEvents()

    def dialog(self, names=('first', 'middle', 'last')):
        dialog = QDialog(self.app)
        layout = QVBoxLayout(dialog)
        buttons = []
        for name in names:
            button = QPushButton(name)
            layout.addWidget(button)
            buttons.append(button)
        return dialog, buttons


class FocusRingTests(GadgetCase):
    def test_keyboard_focus_shows_ring_and_pointer_focus_hides_it(self):
        tracker = self.app.focus_tracker
        self.app.send_btn.setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        focused = QApplication.focusWidget()
        self.assertTrue(tracker.navigated_by_keyboard)
        self.assertTrue(tracker.ring_visible(focused))
        self.assertGreater(ring_pixels(focused), 0)

        QTest.keyClick(focused, Qt.Key.Key_Backtab)
        self.assertFalse(tracker.ring_visible(focused))
        self.assertTrue(tracker.ring_visible(QApplication.focusWidget()))

        QTest.mouseClick(self.app.show_tools_cb, Qt.MouseButton.LeftButton)
        self.assertIs(QApplication.focusWidget(), self.app.show_tools_cb)
        self.assertFalse(tracker.navigated_by_keyboard)
        self.assertFalse(tracker.ring_visible(self.app.show_tools_cb))
        self.assertEqual(ring_pixels(self.app.show_tools_cb), 0)

    def test_ring_uses_theme_focus_token(self):
        from llama_gadget.theme import DARK_TOKENS
        self.assertIn(f'[keyboardFocus="true"] {{ border: 2px solid {DARK_TOKENS.focus_ring}; }}', self.app.styleSheet())


class OverlayTrapTests(GadgetCase):
    def test_tab_wraps_inside_dialog_and_focus_returns_to_trigger(self):
        trigger = self.app.tools_btn
        trigger.setFocus(Qt.FocusReason.TabFocusReason)
        dialog, (first, middle, last) = self.dialog()
        dialog.open()
        self.assertTrue(pump_until(dialog.isActiveWindow))
        trap = self.app.overlay_trap
        self.assertIs(trap.active(), dialog)
        self.assertIs(trap.scopes[-1][1], trigger)

        last.setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.keyClick(last, Qt.Key.Key_Tab)
        self.assertIs(QApplication.focusWidget(), first)
        QTest.keyClick(first, Qt.Key.Key_Backtab)
        self.assertIs(QApplication.focusWidget(), last)
        for _ in range(7):  # never escapes to the background window
            QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
            self.assertIn(QApplication.focusWidget(), (first, middle, last))

        dialog.reject()
        self.assertTrue(pump_until(lambda: QApplication.focusWidget() is trigger))
        self.assertIsNone(trap.active())

    def test_tab_is_trapped_even_in_text_editors(self):
        dialog = QDialog(self.app)
        layout = QVBoxLayout(dialog)
        line, button = QLineEdit(), QPushButton('ok')
        layout.addWidget(line)
        layout.addWidget(button)
        dialog.open()
        self.assertTrue(pump_until(dialog.isActiveWindow))
        button.setFocus()
        QTest.keyClick(button, Qt.Key.Key_Tab)
        self.assertIs(QApplication.focusWidget(), line)
        dialog.reject()

    def test_missing_trigger_falls_back_to_default_control(self):
        trigger = QPushButton('temporary', self.app)
        trigger.show()
        trigger.setFocus()
        dialog, _ = self.dialog(('only',))
        dialog.open()
        self.assertTrue(pump_until(dialog.isActiveWindow))
        sip.delete(trigger)
        dialog.reject()
        self.assertTrue(pump_until(lambda: QApplication.focusWidget() is self.app.input))

    def test_nested_overlays_restore_in_order(self):
        outer, (outer_btn,) = self.dialog(('outer',))
        outer.open()
        self.assertTrue(pump_until(outer.isActiveWindow))
        outer_btn.setFocus()
        inner = QDialog(outer)
        QVBoxLayout(inner).addWidget(QPushButton('inner'))
        inner.open()
        self.assertTrue(pump_until(inner.isActiveWindow))
        self.assertIs(self.app.overlay_trap.active(), inner)
        inner.reject()
        self.assertTrue(pump_until(lambda: QApplication.focusWidget() is outer_btn))
        self.assertIs(self.app.overlay_trap.active(), outer)
        outer.reject()

    def test_handle_tab_contract(self):
        trap = self.app.overlay_trap
        self.assertIsNone(trap.handle_tab(None))
        dialog, (first, middle, last) = self.dialog()
        dialog.show()
        self.assertTrue(pump_until(lambda: trap.active() is dialog))
        self.assertIs(trap.handle_tab(None), first)
        self.assertIs(trap.handle_tab(None, backward=True), last)
        self.assertIs(trap.handle_tab(middle), last)
        self.assertIs(trap.handle_tab(self.app.send_btn), first)  # background controls are never candidates
        dialog.close()


class AccessibleMetadataTests(GadgetCase):
    def assert_complete(self, root):
        gaps = accessibility_gaps(root)
        self.assertEqual(gaps, [], [f'{type(w).__name__}:{w.objectName()}' for w in gaps])

    def test_register_contract(self):
        button = QPushButton('✕', self.app)
        meta = register_accessible_control(button, '  Hide to tray ')
        self.assertEqual(meta, AccessibleControlMeta('hide-to-tray', 'Button', 'Hide to tray'))
        self.assertEqual(button.accessibleName(), 'Hide to tray')
        self.assertEqual(button.accessibleIdentifier(), 'hide-to-tray')
        self.assertEqual(register_accessible_control(QLineEdit(self.app), 'Endpoint', 'endpoint').id, 'endpoint')
        self.assertEqual(accessible_role(QCheckBox()), 'CheckBox')
        for bad in ('', '   ', '✕'):
            with self.assertRaises(ValueError):
                register_accessible_control(QPushButton(), bad)
        with self.assertRaises(ValueError):
            register_accessible_control(QLabel('not interactive'), 'Label')

    def test_every_gadget_control_has_role_and_label(self):
        self.app.msgs = [{'role': 'user', 'content': 'hi', 'ts': ''},
                         {'role': 'assistant', 'content': 'hello', 'ts': ''}]
        self.app._render()
        self.assert_complete(self.app)
        self.assertEqual(accessible_label(self.app.edit_ep_btn), 'Edit endpoint URL')
        self.assertEqual(accessible_label(self.app.input), 'Message')
        self.assertEqual(accessible_label(self.app.tabs.tabBar()), 'Sections')
        self.assertEqual(accessible_label(self.app.send_btn), 'Send')  # live button text
        self.assertEqual(accessible_label(self.app.tools_cb), 'Enable tools')
        cards = list(self.app.view.cards.values())
        self.assertEqual([c.body.accessibleName() for c in cards], ['Your message', 'Assistant message'])
        self.assertEqual(cards[1].copy_btn.accessibleName(), 'Copy response')

    def test_every_dialog_control_has_role_and_label(self):
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(QColor('red'))
        files = [{'rel': 'a.txt', 'chars': 10, 'truncated': False}]
        dialogs = [FolderDialog(self.app, '/tmp/x', files, 1, 100), ImagePreviewDialog(encode_image(image, 'p.jpg'), self.app),
                   MCPConfigDialog(Path(self.temp.name) / 'mcp.json', parent=self.app), ToolInspectorDialog(parent=self.app)]
        for dialog in dialogs:
            self.addCleanup(dialog.deleteLater)
            self.assert_complete(dialog)

    def test_approval_dialog_controls_are_labelled(self):
        class Worker:
            cancel_event = __import__('threading').Event()
            registry = None
            decide = lambda self, *a: None
            cancel = lambda self: None
        worker = Worker()
        self.app.worker = worker
        self.addCleanup(setattr, self.app, 'worker', None)
        self.app.yolo_cb.setChecked(False)
        self.app._request_approval(worker, 'call_1:1', 'bash_command', {'command': 'ls'})
        dialog = self.app._approval_dialog
        self.assert_complete(dialog)
        names = {b.accessibleName() for b in dialog.findChildren(QPushButton)}
        self.assertEqual(names, {'Approve bash_command', 'Deny bash_command'})
        dialog.reject()


class UiScaleStateTests(unittest.TestCase):
    def test_default_step_reset_and_bounds(self):
        state = UiScaleState()
        self.assertEqual(state.scale_factor, 1.0)
        self.assertGreater(state.apply(UiScaleAction.INCREASE), 1.0)
        bigger = state.scale_factor
        self.assertLess(state.apply(UiScaleAction.DECREASE), bigger)
        self.assertEqual(state.apply(UiScaleAction.RESET), 1.0)
        for _ in range(3):
            state.apply(UiScaleAction.DECREASE)
        self.assertLess(state.scale_factor, 1.0)
        self.assertEqual(state.apply(UiScaleAction.RESET), 1.0)
        for _ in range(40):
            state.apply(UiScaleAction.INCREASE)
        self.assertEqual(state.scale_factor, UiScaleState.MAX)
        for _ in range(40):
            state.apply(UiScaleAction.DECREASE)
        self.assertEqual(state.scale_factor, UiScaleState.MIN)
        self.assertEqual(UiScaleState('garbage').scale_factor, 1.0)
        self.assertEqual(UiScaleState('9').scale_factor, UiScaleState.MAX)


class ZoomShortcutTests(GadgetCase):
    def press(self, key):
        QTest.keyClick(QApplication.focusWidget() or self.app, key, Qt.KeyboardModifier.ControlModifier)

    def test_ctrl_plus_minus_zero_scale_interface_and_typography(self):
        self.app.input.setFocus()
        base_pt, base_height = self.app.input.font().pointSizeF(), self.app.input.height()
        self.press(Qt.Key.Key_Equal)
        self.assertAlmostEqual(self.app.ui_scale.scale_factor, 1.1)
        self.assertGreater(self.app.input.font().pointSizeF(), base_pt)
        self.assertGreater(self.app.input.height(), base_height)
        self.assertEqual(float(self.app.settings.value('ui_scale')), 1.1)
        self.press(Qt.Key.Key_Plus)
        self.assertAlmostEqual(self.app.ui_scale.scale_factor, 1.2)

        self.press(Qt.Key.Key_0)
        self.assertEqual(self.app.ui_scale.scale_factor, 1.0)
        self.assertAlmostEqual(self.app.input.font().pointSizeF(), base_pt)

        self.press(Qt.Key.Key_Minus)
        self.assertAlmostEqual(self.app.ui_scale.scale_factor, 0.9)
        self.assertLess(self.app.send_btn.font().pointSizeF(), base_pt)
        self.assertLess(self.app.input.height(), base_height)
        self.press(Qt.Key.Key_0)
        self.assertEqual(self.app.ui_scale.scale_factor, 1.0)
        QTest.keyClick(self.app.input, Qt.Key.Key_Plus,
                       Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.KeypadModifier)
        self.assertAlmostEqual(self.app.ui_scale.scale_factor, 1.1)
        QTest.keyClick(self.app.input, Qt.Key.Key_0)  # plain keys still type
        self.assertEqual(self.app.input.toPlainText(), '0')

    def test_shortcuts_also_scale_open_dialogs(self):
        dialog, (button,) = self.dialog(('ok',))
        dialog.open()
        self.assertTrue(pump_until(dialog.isActiveWindow))
        base = button.font().pointSizeF()
        QTest.keyClick(button, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)
        self.assertGreater(button.font().pointSizeF(), base)
        dialog.reject()


if __name__ == '__main__':
    unittest.main()
