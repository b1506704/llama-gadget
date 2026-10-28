from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton, QVBoxLayout

from llama_gadget.app import Gadget

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
        with patch('llama_gadget.app.QSettings', return_value=settings), patch.object(Gadget, '_poll'):
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


if __name__ == '__main__':
    unittest.main()
