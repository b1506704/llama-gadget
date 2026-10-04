"""Llama Gadget - tiny desktop chat + service toggle for the always-on text LLM."""
import sys
import threading

from PyQt6.QtCore import QSettings, Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QTextCursor
from PyQt6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel, QMenu,
                             QPushButton, QSystemTrayIcon, QTextBrowser, QTextEdit,
                             QVBoxLayout, QWidget)

from . import service
from .chat import ChatWorker

SYSTEM_PROMPT = "You are a concise, helpful assistant."
COLORS = {"online": "#3fb950", "loading": "#d29922", "offline": "#f85149"}

STYLE = """
QWidget#root { background: #14161a; border: 1px solid #2a2e35; border-radius: 10px; }
QLabel, QCheckBox { color: #d0d4da; }
QTextBrowser, QTextEdit { background: #1b1e24; color: #e6e8eb; border: 1px solid #2a2e35;
                          border-radius: 6px; padding: 4px; }
QPushButton { background: #2a2e35; color: #e6e8eb; border: none; border-radius: 6px; padding: 5px 10px; }
QPushButton:hover { background: #363b44; }
QPushButton:disabled { color: #6b7078; }
"""


def make_icon(color: str) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(6, 6, 52, 52)
    p.end()
    return QIcon(pm)


class Bridge(QObject):
    status = pyqtSignal(str)


class InputBox(QTextEdit):
    submit = pyqtSignal()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.submit.emit()
        else:
            super().keyPressEvent(e)


class Gadget(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("llama-gadget", "llama-gadget")
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.worker = None
        self.state = "offline"
        self._drag = None
        self._busy_toggle = False

        self.setWindowTitle("Llama Gadget")
        self.setWindowFlag(Qt.WindowType.Tool, False)
        self.resize(380, 520)
        self._build_ui()
        self.setStyleSheet(STYLE)

        self.bridge = Bridge()
        self.bridge.status.connect(self._set_status)
        self.timer = QTimer(self, interval=3000, timeout=self._poll)
        self.timer.start()
        self._poll()

        self.tray = QSystemTrayIcon(make_icon(COLORS["offline"]), self)
        menu = QMenu()
        menu.addAction("Show / Hide", self.toggle_visible)
        self.tray_toggle = QAction("Start service", menu, triggered=self.toggle_service)
        menu.addAction(self.tray_toggle)
        menu.addAction("Quit", QApplication.instance().quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.toggle_visible() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

        self.gadget_cb.setChecked(self.settings.value("gadget", False, bool))
        self._apply_gadget(self.gadget_cb.isChecked())

    # ---- UI ----
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = QWidget(objectName="root")
        outer.addWidget(root)
        lay = QVBoxLayout(root)

        top = QHBoxLayout()
        self.dot = QLabel("●")
        self.status_lbl = QLabel("offline")
        self.power_btn = QPushButton("Start")
        self.power_btn.clicked.connect(self.toggle_service)
        top.addWidget(self.dot)
        top.addWidget(self.status_lbl)
        top.addStretch()
        top.addWidget(self.power_btn)
        lay.addLayout(top)

        opts = QHBoxLayout()
        self.gadget_cb = QCheckBox("Gadget mode (frameless, on top)")
        self.gadget_cb.toggled.connect(self._apply_gadget)
        opts.addWidget(self.gadget_cb)
        opts.addStretch()
        close = QPushButton("✕")
        close.setFixedWidth(30)
        close.setToolTip("Hide to tray")
        close.clicked.connect(self.hide)
        opts.addWidget(close)
        lay.addLayout(opts)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        lay.addWidget(self.view, 1)

        self.input = InputBox()
        self.input.setPlaceholderText("Message… (Enter to send, Shift+Enter for newline)")
        self.input.setFixedHeight(64)
        self.input.submit.connect(self.send)
        lay.addWidget(self.input)

        row = QHBoxLayout()
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send)
        self.refresh_btn = QPushButton("New session")
        self.refresh_btn.clicked.connect(self.new_session)
        row.addWidget(self.refresh_btn)
        row.addStretch()
        row.addWidget(self.send_btn)
        lay.addLayout(row)
        self._update_controls()

    def _apply_gadget(self, on):
        self.settings.setValue("gadget", on)
        flags = Qt.WindowType.Window
        if on:
            flags |= (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        vis = self.isVisible()
        self.setWindowFlags(flags)
        if vis or True:
            self.show()

    def toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    # Drag the frameless window by clicking anywhere not on a child widget.
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    # ---- service ----
    def _poll(self):
        threading.Thread(target=lambda: self.bridge.status.emit(service.status()), daemon=True).start()

    def _set_status(self, s):
        self.state = s
        self.dot.setStyleSheet(f"color: {COLORS[s]}; font-size: 16px;")
        self.status_lbl.setText(s)
        self.tray.setIcon(make_icon(COLORS[s]))
        self.tray.setToolTip(f"Llama service: {s}")
        self.power_btn.setText("Stop" if s != "offline" else "Start")
        self.tray_toggle.setText("Stop service" if s != "offline" else "Start service")
        self.power_btn.setEnabled(not self._busy_toggle)
        self._update_controls()

    def toggle_service(self):
        if self._busy_toggle:
            return
        self._busy_toggle = True
        want_stop = self.state != "offline"
        self.power_btn.setEnabled(False)
        self.status_lbl.setText("stopping…" if want_stop else "starting…")

        def job():
            service.stop() if want_stop else service.start()
            self._busy_toggle = False
            self.bridge.status.emit(service.status())
        threading.Thread(target=job, daemon=True).start()

    def _update_controls(self):
        ready = self.state == "online" and self.worker is None
        self.send_btn.setEnabled(ready)

    # ---- chat ----
    def _append(self, who, text, color):
        self.view.moveCursor(QTextCursor.MoveOperation.End)
        self.view.insertHtml(f'<p><b style="color:{color}">{who}</b><br>')
        self.view.insertPlainText(text)
        self.view.insertHtml("</p>")

    def send(self):
        text = self.input.toPlainText().strip()
        if not text or self.worker is not None or self.state != "online":
            return
        self.input.clear()
        self.history.append({"role": "user", "content": text})
        self.view.append("")
        self._append("You", text, "#58a6ff")
        self.view.append("")
        self.view.insertHtml('<b style="color:#3fb950">Assistant</b><br>')
        self.reply = ""
        self.worker = ChatWorker(list(self.history), parent=self)
        self.worker.token.connect(self._on_token)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_fail)
        self.worker.start()
        self._update_controls()

    def _on_token(self, t):
        self.reply += t
        self.view.moveCursor(QTextCursor.MoveOperation.End)
        self.view.insertPlainText(t)
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def _finish(self):
        self.worker = None
        self._update_controls()

    def _on_done(self):
        if self.reply:
            self.history.append({"role": "assistant", "content": self.reply})
        self._finish()

    def _on_fail(self, err):
        self.view.append(f'<span style="color:#f85149">Error: {err}</span>')
        self.history.pop()  # drop the unanswered user turn
        self._finish()

    def new_session(self):
        if self.worker:
            self.worker.cancel()
            self.worker.wait(2000)
            self.worker = None
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.view.clear()
        self._update_controls()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setDesktopFileName("llama-gadget")
    w = Gadget()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
