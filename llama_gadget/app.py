"""Llama Gadget - tiny desktop chat + service toggle for the always-on text LLM."""
import socket
import sys
import threading
import time
from datetime import datetime
from html import escape
from pathlib import Path

from PyQt6.QtCore import QObject, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
                             QSystemTrayIcon, QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget)

from . import service
from .chat import ChatWorker
from .db import DEFAULT_DB, Database
from .files import UnsupportedFile, parse_file

DEFAULT_PROMPT = "You are a concise, helpful assistant."
DEFAULT_USER = socket.gethostname()  # PC name
COLORS = {"online": "#3fb950", "loading": "#d29922", "offline": "#f85149"}

STYLE = """
QWidget#root { background: #14161a; border: 1px solid #2a2e35; border-radius: 10px; }
QLabel, QCheckBox { color: #d0d4da; }
QTextBrowser, QTextEdit, QLineEdit, QListWidget { background: #1b1e24; color: #e6e8eb;
    border: 1px solid #2a2e35; border-radius: 6px; padding: 4px; }
QListWidget::item { padding: 4px; border-bottom: 1px solid #23272e; }
QListWidget::item:selected { background: #2a3140; }
QPushButton { background: #2a2e35; color: #e6e8eb; border: none; border-radius: 6px; padding: 5px 10px; }
QPushButton:hover { background: #363b44; }
QPushButton:disabled { color: #6b7078; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: #1b1e24; color: #9aa0a8; padding: 5px 12px; border-radius: 6px; margin-right: 3px; }
QTabBar::tab:selected { background: #2a3140; color: #e6e8eb; }
QMenu { background: #1b1e24; color: #e6e8eb; border: 1px solid #2a2e35; }
QMenu::item:selected { background: #2a3140; }
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


def fmt_ts(ts: str) -> str:
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts
    return dt.strftime("%H:%M:%S") if dt.date() == datetime.now().date() else dt.strftime("%Y-%m-%d %H:%M")


def model_text(m: dict) -> str:
    """Message text as the LLM sees it (attached file contents appended)."""
    out = m["content"]
    for a in m.get("attachments", []):
        note = " (truncated)" if a.get("truncated") else ""
        out += f"\n\n[Attached file: {a['name']}{note}]\n~~~~\n{a['text']}\n~~~~"
    return out.strip()


class Bridge(QObject):
    status = pyqtSignal(str)
    gpus = pyqtSignal(list)
    ctx = pyqtSignal(object)
    model = pyqtSignal(object)


class InputBox(QTextEdit):
    submit = pyqtSignal()
    clip_menu = pyqtSignal()

    def keyPressEvent(self, e):
        mods = e.modifiers()
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (mods & Qt.KeyboardModifier.ShiftModifier):
            self.submit.emit()
        elif (e.key() == Qt.Key.Key_V and mods & Qt.KeyboardModifier.ControlModifier
              and mods & Qt.KeyboardModifier.ShiftModifier):
            self.clip_menu.emit()
        else:
            super().keyPressEvent(e)

    def insertFromMimeData(self, source):  # paste as plain text only
        if source.hasText():
            self.insertPlainText(source.text())


class Gadget(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("llama-gadget", "llama-gadget")
        self.state = "offline"
        self.worker = None
        self.n_ctx = 0
        self.ctx_used = 0
        self.ctx_exact = True
        self.last_speed = ""
        self.last_error = ""
        self.reply = ""
        self._ntok = 0
        self._render_pending = False
        self._drag = None
        self._busy_toggle = False
        self.msgs = []
        self.session_id = None
        self.attachments = []

        self.user = self.settings.value("user", DEFAULT_USER) or DEFAULT_USER
        self.db = Database()
        try:
            self.db.open(self.settings.value("db_path", str(DEFAULT_DB)))
        except Exception:  # noqa: BLE001 - fall back to default location
            self.db.open(DEFAULT_DB)
        self.system_prompt = self.db.get_prompt(self.user) or DEFAULT_PROMPT

        self.setWindowTitle("Llama Gadget")
        self.setAcceptDrops(True)
        self.resize(420, 660)
        self._build_ui()
        self.setStyleSheet(STYLE)

        self.bridge = Bridge()
        self.bridge.status.connect(self._set_status)
        self.bridge.gpus.connect(self._set_gpus)
        self.bridge.model.connect(self._set_model)
        self.bridge.ctx.connect(lambda n: setattr(self, "n_ctx", n or self.n_ctx))
        self.timer = QTimer(self, interval=3000, timeout=self._poll)
        self.timer.start()

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

        self.clipboard = QApplication.clipboard()
        self.clipboard.dataChanged.connect(self._on_clipboard)

        self._poll()
        self._render()
        self._refresh_history()
        self._refresh_db_status()
        self.gadget_cb.setChecked(self.settings.value("gadget", False, bool))
        self._apply_gadget(self.gadget_cb.isChecked())

    # ---------------------------------------------------------------- UI
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
        close = QPushButton("✕")
        close.setFixedWidth(30)
        close.setToolTip("Hide to tray")
        close.clicked.connect(self.hide)
        top.addWidget(self.dot)
        top.addWidget(self.status_lbl)
        top.addStretch()
        top.addWidget(self.power_btn)
        top.addWidget(close)
        lay.addLayout(top)

        self.endpoint_lbl = QLabel()
        self.endpoint_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.endpoint_lbl.setStyleSheet("color:#8b949e; font-size:11px;")
        self.endpoint_lbl.setText(f"Endpoint: {service.BASE_URL}/v1\nModel: –")
        lay.addWidget(self.endpoint_lbl)

        self.gadget_cb = QCheckBox("Gadget mode (frameless, on top)")
        self.gadget_cb.toggled.connect(self._apply_gadget)
        lay.addWidget(self.gadget_cb)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._chat_page(), "Chat")
        self.tabs.addTab(self._history_page(), "History")
        self.tabs.addTab(self._settings_page(), "Settings")
        self.tabs.currentChanged.connect(self._on_tab)
        lay.addWidget(self.tabs, 1)

        self.db_footer = QLabel()
        self.db_footer.setStyleSheet("color:#8b949e; font-size:11px;")
        lay.addWidget(self.db_footer)
        self._update_controls()

    def _chat_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        lay.addWidget(self.view, 1)

        self.stats_lbl = QLabel()
        self.gpu_lbl = QLabel("GPU: …")
        for l in (self.stats_lbl, self.gpu_lbl):
            l.setStyleSheet("color:#8b949e; font-size:11px;")
            lay.addWidget(l)

        self.attach_lbl = QLabel()
        self.attach_lbl.setWordWrap(True)
        self.attach_lbl.setStyleSheet("color:#58a6ff; font-size:11px;")
        self.attach_clear = QPushButton("Clear files")
        self.attach_clear.clicked.connect(self._clear_attachments)
        arow = QHBoxLayout()
        arow.addWidget(self.attach_lbl, 1)
        arow.addWidget(self.attach_clear)
        lay.addLayout(arow)
        self._update_attachments()

        self.input = InputBox()
        self.input.setPlaceholderText("Message… (Enter send · Ctrl+Shift+V clips)")
        self.input.setFixedHeight(64)
        self.input.submit.connect(self.send)
        self.input.clip_menu.connect(self._show_clip_menu)
        lay.addWidget(self.input)

        row = QHBoxLayout()
        self.refresh_btn = QPushButton("New session")
        self.refresh_btn.clicked.connect(self.new_session)
        self.attach_btn = QPushButton("📎 File")
        self.attach_btn.setToolTip("Attach text files (txt, md, code, json, csv, html, docx, pdf…) — or drop them on the window")
        self.attach_btn.clicked.connect(self._pick_files)
        self.clip_btn = QPushButton("📋 Clips")
        self.clip_btn.setToolTip("Clipboard memory (Ctrl+Shift+V)")
        self.clip_btn.clicked.connect(self._show_clip_menu)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send)
        for b in (self.refresh_btn, self.attach_btn, self.clip_btn):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(self.send_btn)
        lay.addLayout(row)
        self._update_stats()
        return w

    def _history_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        self.hist_user_lbl = QLabel()
        lay.addWidget(self.hist_user_lbl)
        self.hist_list = QListWidget()
        self.hist_list.itemDoubleClicked.connect(lambda _: self._open_selected())
        lay.addWidget(self.hist_list, 1)
        row = QHBoxLayout()
        for text, fn in (("Open", self._open_selected), ("Delete", self._delete_selected),
                         ("Refresh", self._refresh_history)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        return w

    def _settings_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)

        lay.addWidget(QLabel("<b>User name</b> (defaults to this PC's name)"))
        urow = QHBoxLayout()
        self.user_edit = QLineEdit(self.user)
        apply_u = QPushButton("Apply")
        apply_u.clicked.connect(self._apply_user)
        self.user_edit.returnPressed.connect(self._apply_user)
        urow.addWidget(self.user_edit, 1)
        urow.addWidget(apply_u)
        lay.addLayout(urow)

        lay.addWidget(QLabel("<b>System prompt</b> (saved per user)"))
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlainText(self.system_prompt)
        lay.addWidget(self.prompt_edit, 1)
        prow = QHBoxLayout()
        save_p = QPushButton("Save prompt")
        save_p.clicked.connect(self._save_prompt)
        reset_p = QPushButton("Reset to default")
        reset_p.clicked.connect(self._reset_prompt)
        self.prompt_msg = QLabel()
        self.prompt_msg.setStyleSheet("color:#8b949e; font-size:11px;")
        prow.addWidget(save_p)
        prow.addWidget(reset_p)
        prow.addWidget(self.prompt_msg, 1)
        lay.addLayout(prow)

        lay.addWidget(QLabel("<b>Database</b> (SQLite)"))
        self.db_status = QLabel()
        self.db_status.setWordWrap(True)
        self.db_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.db_status)
        drow = QHBoxLayout()
        browse = QPushButton("Browse / select…")
        browse.clicked.connect(self._browse_db)
        default = QPushButton("Use default")
        default.clicked.connect(lambda: self._switch_db(DEFAULT_DB))
        clear_clips = QPushButton("Clear clips")
        clear_clips.clicked.connect(self._clear_clips)
        for b in (browse, default, clear_clips):
            drow.addWidget(b)
        lay.addLayout(drow)
        return w

    def _apply_gadget(self, on):
        self.settings.setValue("gadget", on)
        flags = Qt.WindowType.Window
        if on:
            flags |= Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, bool(on))
        self.setWindowFlags(flags)
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

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self._add_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
        self.tabs.setCurrentIndex(0)

    # ---------------------------------------------------------------- service / stats
    def _poll(self):
        def job():
            self.bridge.status.emit(service.status())
            self.bridge.gpus.emit(service.gpu_stats())
            if not self.n_ctx:
                self.bridge.ctx.emit(service.n_ctx())
            self.bridge.model.emit(service.model_info())
        threading.Thread(target=job, daemon=True).start()

    def _set_model(self, info):
        if not info:
            self.n_ctx = 0  # re-read context size when a (possibly different) model comes back
        name, params = info or ("not loaded", "")
        ctx = f" · {self.n_ctx:,} ctx" if self.n_ctx and info else ""
        self.endpoint_lbl.setText(f"Endpoint: {service.BASE_URL}/v1 ({service.UNIT})\n"
                                  f"Model: {name}" + (f" · {params}" if params else "") + ctx)
        self.endpoint_lbl.setToolTip(f"{service.BASE_URL}/v1/chat/completions")

    def _set_gpus(self, gpus):
        self.gpu_lbl.setText("\n".join(
            f"GPU{g['index']} {g['name']}: {g['util']}% · {g['used']}/{g['total']} MiB · {g['temp']}°C"
            for g in gpus) or "GPU: n/a")

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

    def _update_stats(self):
        turns = sum(1 for m in self.msgs if m["role"] == "user")
        ctx = f"{'' if self.ctx_exact else '~'}{self.ctx_used}" + (f"/{self.n_ctx}" if self.n_ctx else "")
        pct = f" ({100 * self.ctx_used // self.n_ctx}%)" if self.n_ctx else ""
        self.stats_lbl.setText(f"Context: {ctx} tok{pct} · Turns: {turns}"
                               + (f"\n{self.last_speed}" if self.last_speed else ""))

    def _on_stats(self, st):
        self.ctx_used = st.get("total_tokens", self.ctx_used)
        self.ctx_exact = True
        if "predicted_per_second" in st:
            self.last_speed = (f"Gen: {st['predicted_per_second']:.1f} tok/s · "
                               f"Prompt: {st.get('prompt_per_second', 0):.0f} tok/s · "
                               f"Out: {st.get('completion_tokens', 0)} tok")
        self._update_stats()

    def _update_controls(self):
        self.send_btn.setEnabled(self.state == "online" and self.worker is None)

    # ---------------------------------------------------------------- markdown rendering
    def _render(self):
        bar = self.view.verticalScrollBar()
        stick = bar.value() >= bar.maximum() - 24
        parts = []
        for m in self.msgs:
            parts.append(self._md_message(m["role"], m["content"], m["ts"], m.get("attachments", [])))
        if self.worker is not None:
            parts.append(self._md_message("assistant", self.reply or "_…_", time.strftime("%Y-%m-%d %H:%M:%S"), []))
        if self.last_error:
            parts.append(f"> ⚠️ **Error:** {self.last_error}")
        if not parts:
            parts.append("_Start chatting — responses render as Markdown._")
        self.view.setMarkdown("\n\n---\n\n".join(parts))
        if stick:
            bar.setValue(bar.maximum())

    @staticmethod
    def _md_message(role, content, ts, attachments):
        who = "🧑 You" if role == "user" else "🤖 Assistant"
        body = content.replace("\n", "  \n") if role == "user" else content
        files = "".join(f"\n\n📎 `{a['name']}` ({a['chars']:,} chars{', truncated' if a.get('truncated') else ''})"
                        for a in attachments)
        return f"**{who}** · *{fmt_ts(ts)}*\n\n{body}{files}"

    def _schedule_render(self):
        if not self._render_pending:
            self._render_pending = True
            QTimer.singleShot(80, self._flush_render)

    def _flush_render(self):
        self._render_pending = False
        self._render()

    # ---------------------------------------------------------------- chat
    def send(self):
        text = self.input.toPlainText().strip()
        if (not text and not self.attachments) or self.worker is not None or self.state != "online":
            return
        if self.session_id is None:
            title = (text or self.attachments[0]["name"]).splitlines()[0][:48]
            self.session_id = self.db.create_session(self.user, title)
        msg = dict(role="user", content=text, ts=time.strftime("%Y-%m-%d %H:%M:%S"),
                   attachments=list(self.attachments))
        msg["id"] = self.db.add_message(self.session_id, "user", text, msg["attachments"], msg["ts"])
        self.msgs.append(msg)
        self.input.clear()
        self._clear_attachments()
        self.last_error = ""
        self.reply = ""
        self._ntok = 0
        self.last_speed = ""

        payload = [{"role": "system", "content": self.system_prompt}]
        payload += [{"role": m["role"], "content": model_text(m)} for m in self.msgs]
        self.worker = ChatWorker(payload, parent=self)
        self.worker.token.connect(self._on_token)
        self.worker.stats.connect(self._on_stats)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_fail)
        self.worker.start()
        self._update_controls()
        self._update_stats()
        self._render()

    def _on_token(self, t):
        self.reply += t
        self._ntok += 1
        if self._ntok == 1:
            self._t0 = time.monotonic()
        elif self._ntok % 5 == 0:
            self.last_speed = f"Gen: {(self._ntok - 1) / max(time.monotonic() - self._t0, 1e-6):.1f} tok/s (live)"
            self._update_stats()
        self._schedule_render()

    def _finish(self):
        self.worker = None
        self._update_controls()
        self._render()
        self._refresh_history()
        self._refresh_db_status()

    def _on_done(self):
        if self.reply:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            mid = self.db.add_message(self.session_id, "assistant", self.reply, [], ts)
            self.msgs.append(dict(id=mid, role="assistant", content=self.reply, ts=ts, attachments=[]))
        self._finish()

    def _on_fail(self, err):
        self.last_error = err
        last = self.msgs.pop()  # drop the unanswered user turn and give it back to the input box
        self.db.delete_message(last["id"])
        self.input.setPlainText(last["content"])
        self.attachments = last["attachments"]
        self._update_attachments()
        self._finish()

    def new_session(self):
        if self.worker:
            self.worker.cancel()
            self.worker.blockSignals(True)  # ignore anything the old stream still emits
            self.worker = None
        self.msgs, self.session_id = [], None
        self.ctx_used, self.ctx_exact, self.last_speed, self.last_error = 0, True, "", ""
        self._update_controls()
        self._update_stats()
        self._render()

    # ---------------------------------------------------------------- attachments
    def _pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files", str(Path.home()))
        self._add_files(paths)

    def _add_files(self, paths):
        errs = []
        for p in paths:
            try:
                self.attachments.append(parse_file(p))
            except (UnsupportedFile, OSError, Exception) as e:  # noqa: BLE001
                errs.append(f"{Path(p).name}: {e}")
        if errs:
            QMessageBox.warning(self, "Could not read file", "\n".join(errs))
        self._update_attachments()

    def _clear_attachments(self):
        self.attachments = []
        self._update_attachments()

    def _update_attachments(self):
        self.attach_lbl.setText("  ".join(
            f"📎 {a['name']} ({a['chars']:,}{'+' if a['truncated'] else ''})" for a in self.attachments))
        self.attach_lbl.setVisible(bool(self.attachments))
        self.attach_clear.setVisible(bool(self.attachments))

    # ---------------------------------------------------------------- clipboard memory
    def _on_clipboard(self):
        text = self.clipboard.text()
        if text and text.strip() and len(text) <= 20000:
            try:
                self.db.add_clip(text)
            except Exception:  # noqa: BLE001
                pass

    def _show_clip_menu(self):
        menu = QMenu(self)
        clips = self.db.list_clips()
        if not clips:
            menu.addAction("(clipboard memory is empty)").setEnabled(False)
        for c in clips:
            one = " ".join(c.split())
            act = menu.addAction(one[:70] + ("…" if len(one) > 70 else ""))
            act.setToolTip(c[:500])
            act.triggered.connect(lambda _=False, t=c: (self.input.insertPlainText(t), self.input.setFocus()))
        menu.setToolTipsVisible(True)
        menu.exec(self.clip_btn.mapToGlobal(self.clip_btn.rect().topLeft()))

    def _clear_clips(self):
        self.db.clear_clips()
        self._refresh_db_status()

    # ---------------------------------------------------------------- history
    def _on_tab(self, i):
        if i == 1:
            self._refresh_history()
        elif i == 2:
            self._refresh_db_status()

    def _refresh_history(self):
        self.hist_user_lbl.setText(f"Sessions for <b>{escape(self.user)}</b>")
        self.hist_list.clear()
        for sid, title, created, updated, n in self.db.list_sessions(self.user):
            item = QListWidgetItem(f"{'▶ ' if sid == self.session_id else ''}{title}\n"
                                   f"{fmt_ts(updated)} · {n} messages · started {fmt_ts(created)}")
            item.setData(Qt.ItemDataRole.UserRole, sid)
            self.hist_list.addItem(item)

    def _selected_sid(self):
        it = self.hist_list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _open_selected(self):
        sid = self._selected_sid()
        if sid is None:
            return
        self.new_session()
        self.session_id = sid
        self.msgs = self.db.load_messages(sid)
        self.ctx_used = sum(len(model_text(m)) for m in self.msgs) // 4
        self.ctx_exact = False
        self._update_stats()
        self._render()
        self.tabs.setCurrentIndex(0)
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def _delete_selected(self):
        sid = self._selected_sid()
        if sid is None:
            return
        if QMessageBox.question(self, "Delete session", "Delete this session permanently?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_session(sid)
        if sid == self.session_id:
            self.new_session()
        self._refresh_history()
        self._refresh_db_status()

    # ---------------------------------------------------------------- user / prompt
    def _apply_user(self):
        name = self.user_edit.text().strip() or DEFAULT_USER
        self.user_edit.setText(name)
        self.user = name
        self.settings.setValue("user", name)
        self.system_prompt = self.db.get_prompt(name) or DEFAULT_PROMPT
        self.prompt_edit.setPlainText(self.system_prompt)
        self.prompt_msg.setText(f"Loaded prompt for {name}")
        self.new_session()
        self._refresh_history()

    def _save_prompt(self):
        text = self.prompt_edit.toPlainText().strip() or DEFAULT_PROMPT
        self.db.set_prompt(self.user, text)
        self.system_prompt = text
        self.prompt_msg.setText(f"Saved for {self.user} · {datetime.now():%H:%M:%S}")
        self._refresh_db_status()

    def _reset_prompt(self):
        self.db.reset_prompt(self.user)
        self.system_prompt = DEFAULT_PROMPT
        self.prompt_edit.setPlainText(DEFAULT_PROMPT)
        self.prompt_msg.setText("Reset to default")

    # ---------------------------------------------------------------- database
    def _browse_db(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Select or create SQLite database", str(self.db.path),
            "SQLite (*.db *.sqlite *.sqlite3);;All files (*)",
            options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self._switch_db(path)

    def _switch_db(self, path):
        try:
            self.db.open(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Database error", f"Could not open {path}:\n{e}")
            self._refresh_db_status()
            return
        self.settings.setValue("db_path", str(self.db.path))
        self.system_prompt = self.db.get_prompt(self.user) or DEFAULT_PROMPT
        self.prompt_edit.setPlainText(self.system_prompt)
        self.new_session()
        self._refresh_history()
        self._refresh_db_status()

    def _refresh_db_status(self):
        st = self.db.status()
        name = Path(st["path"]).name
        if st["ok"]:
            kb = st["size"] / 1024
            size = f"{kb:.0f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB"
            html = (f'<span style="color:#3fb950">● Connected</span> · integrity {st["integrity"]} · writable<br>'
                    f'{escape(st["path"])}<br>{size} · {st["sessions"]} sessions · '
                    f'{st["messages"]} messages · {st["clips"]} clips')
            self.db_footer.setText(f"DB ● {name} · {st['sessions']} sessions · {st['messages']} msgs")
        else:
            reason = st.get("error") or ("read-only" if not st.get("writable", True) else st.get("integrity"))
            html = f'<span style="color:#f85149">● Problem: {escape(str(reason))}</span><br>{escape(st["path"])}'
            self.db_footer.setText(f"DB ✕ {name}")
        self.db_status.setText(html)
        self.db_footer.setToolTip(st["path"])


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setDesktopFileName("llama-gadget")
    w = Gadget()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
