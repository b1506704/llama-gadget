import re
import socket
import sys
import threading
import time
from datetime import datetime
from html import escape
from pathlib import Path

from PyQt6.QtCore import QObject, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                             QMessageBox, QPushButton, QSystemTrayIcon, QTabWidget, QTextBrowser,
                             QTextEdit, QVBoxLayout, QWidget)

from . import profiles, service
from .chat import ChatWorker
from .db import DEFAULT_DB, Database
from .images import encode_image, is_image, load_image
from .files import (UnsupportedFile, build_folder_context, parse_file, read_candidates,
                    scan_folder)

DEFAULT_USER = socket.gethostname()  # PC name
COLORS = {"online": "#3fb950", "loading": "#d29922", "offline": "#f85149"}


def clean_endpoint(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    url = re.sub(r"/v1(/chat/completions)?/?$", "", url)
    return url.rstrip("/")

STYLE = """
QWidget#root { background: #14161a; border: 1px solid #2a2e35; border-radius: 10px; }
QLabel, QCheckBox { color: #d0d4da; }
QTextBrowser, QTextEdit, QLineEdit, QListWidget { background: #1b1e24; color: #e6e8eb;
    border: 1px solid #2a2e35; border-radius: 6px; padding: 4px; }
QComboBox { background: #1b1e24; color: #e6e8eb; border: 1px solid #2a2e35; border-radius: 6px; padding: 4px 8px; }
QComboBox QAbstractItemView { background: #1b1e24; color: #e6e8eb; selection-background-color: #2a3140; }
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
    if m["role"] == "context":
        return out
    for a in m.get("attachments", []):
        if a.get("kind") == "image":
            continue
        note = " (truncated)" if a.get("truncated") else ""
        out += f"\n\n[Attached file: {a['name']}{note}]\n~~~~\n{a['text']}\n~~~~"
    return out.strip()


def model_content(m: dict, vision: bool):
    """Message content for the API: plain string, or text+image_url parts for the vision profile."""
    text = model_text(m)
    imgs = [a for a in m.get("attachments", []) if a.get("kind") == "image"]
    if not imgs:
        return text
    if not vision:
        return text + "\n\n[Image(s) attached but this profile has no vision: " + ", ".join(
            a["name"] for a in imgs) + "]"
    return [{"type": "text", "text": text or "Analyze the attached image(s)."}] + [
        {"type": "image_url", "image_url": {"url": a["data"]}} for a in imgs]


class Bridge(QObject):
    status = pyqtSignal(str)
    gpus = pyqtSignal(list)
    ctx = pyqtSignal(object)
    model = pyqtSignal(object)
    error = pyqtSignal(str)


class InputBox(QTextEdit):
    submit = pyqtSignal()
    clip_menu = pyqtSignal()
    image_pasted = pyqtSignal(object)

    def keyPressEvent(self, e):
        mods = e.modifiers()
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (mods & Qt.KeyboardModifier.ShiftModifier):
            self.submit.emit()
        elif (e.key() == Qt.Key.Key_V and mods & Qt.KeyboardModifier.ControlModifier
              and mods & Qt.KeyboardModifier.ShiftModifier):
            self.clip_menu.emit()
        else:
            super().keyPressEvent(e)

    def insertFromMimeData(self, source):  # plain text only; images go to the attachment list
        if source.hasImage() and not source.hasText():
            self.image_pasted.emit(source.imageData())
        elif source.hasText():
            self.insertPlainText(source.text())


class FolderDialog(QDialog):
    """Review which files from a scanned folder go into the session context."""

    def __init__(self, parent, root, files, total_seen, budget):
        super().__init__(parent)
        self.setWindowTitle(f"Folder context — {Path(root).name}")
        self.setStyleSheet("QDialog{background:#14161a;}" + STYLE)
        self.resize(460, 520)
        self.files, self.budget = files, budget
        lay = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.list = QListWidget()
        used = 0
        for f in files:  # auto-select by priority until the budget is full
            item = QListWidgetItem(f"{f['rel']}   ({f['chars']:,}{'+' if f['truncated'] else ''})")
            fits = used + f["chars"] <= budget
            if fits:
                used += f["chars"]
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if fits else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.itemChanged.connect(self._update)
        lay.addWidget(self.list, 1)
        self.total_seen = total_seen
        row = QHBoxLayout()
        for text, state in (("Select all", Qt.CheckState.Checked), ("Select none", Qt.CheckState.Unchecked)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, st=state: self._set_all(st))
            row.addWidget(b)
        row.addStretch()
        ok, cancel = QPushButton("Add to session"), QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self._update()

    def _set_all(self, st):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(st)
        self.list.blockSignals(False)
        self._update()

    def selected(self):
        return [f for i, f in enumerate(self.files) if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def _update(self, *_):
        sel = self.selected()
        chars = sum(f["chars"] for f in sel)
        over = chars > self.budget
        color = "#f85149" if over else "#3fb950"
        self.info.setText(
            f"{self.total_seen} files seen · {len(self.files)} text files ranked (docs/config/source first).<br>"
            f'Selected {len(sel)} files · <span style="color:{color}">{chars:,} chars (~{chars // 3:,} tokens)</span>'
            f" of ~{self.budget:,} chars budget" + (" — <b>over budget</b>" if over else ""))


class Gadget(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("llama-gadget", "llama-gadget")
        self.state = "offline"
        self.worker = None
        self.n_ctx = 0
        self.model_name = ""
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
        key = self.settings.value("profile", profiles.DEFAULT_PROFILE)
        self.profile = profiles.PROFILES.get(key, profiles.PROFILES[profiles.DEFAULT_PROFILE])
        self.system_prompt = self._load_prompt()

        self.setWindowTitle("Llama Gadget")
        self.setAcceptDrops(True)
        self.resize(420, 660)
        self._build_ui()
        self.setStyleSheet(STYLE)

        self.bridge = Bridge()
        self.bridge.status.connect(self._set_status)
        self.bridge.gpus.connect(self._set_gpus)
        self.bridge.model.connect(self._set_model)
        self.bridge.error.connect(lambda m: QMessageBox.warning(self, 'Profile error', m))
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

        prow = QHBoxLayout()
        prow.addWidget(QLabel("Profile"))
        self.profile_cb = QComboBox()
        for p in profiles.PROFILES.values():
            self.profile_cb.addItem(p.label, p.key)
        self.profile_cb.setCurrentIndex(self.profile_cb.findData(self.profile.key))
        self.profile_cb.currentIndexChanged.connect(self._on_profile)
        prow.addWidget(self.profile_cb, 1)
        lay.addLayout(prow)

        erow = QHBoxLayout()
        self.endpoint_lbl = QLabel()
        self.endpoint_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.endpoint_lbl.setStyleSheet("color:#8b949e; font-size:11px;")
        self.endpoint_lbl.setText(f"Endpoint: {self.current_base_url()}/v1\nModel: –")
        self.edit_ep_btn = QPushButton("✎")
        self.edit_ep_btn.setFixedSize(26, 26)
        self.edit_ep_btn.setToolTip("Edit endpoint URL")
        self.edit_ep_btn.clicked.connect(self._prompt_edit_endpoint)
        erow.addWidget(self.endpoint_lbl, 1)
        erow.addWidget(self.edit_ep_btn)
        lay.addLayout(erow)

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

        vbar = self.view.verticalScrollBar()
        vbar.valueChanged.connect(self._on_scroll)
        vbar.sliderReleased.connect(self._schedule_render)

        srow = QHBoxLayout()
        self.stats_lbl = QLabel()
        self.stats_lbl.setStyleSheet("color:#8b949e; font-size:11px;")
        self.scroll_bottom_btn = QPushButton("↓ Bottom")
        self.scroll_bottom_btn.setStyleSheet(
            "background: #238636; color: #ffffff; font-weight: bold; border-radius: 4px; "
            "padding: 2px 8px; font-size: 11px;")
        self.scroll_bottom_btn.setToolTip("Jump to latest messages")
        self.scroll_bottom_btn.clicked.connect(self._scroll_to_bottom)
        self.scroll_bottom_btn.hide()
        srow.addWidget(self.stats_lbl, 1)
        srow.addWidget(self.scroll_bottom_btn)
        lay.addLayout(srow)

        self.gpu_lbl = QLabel("GPU: …")
        self.gpu_lbl.setStyleSheet("color:#8b949e; font-size:11px;")
        lay.addWidget(self.gpu_lbl)

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
        self.input.image_pasted.connect(self._paste_image)
        lay.addWidget(self.input)

        row = QHBoxLayout()
        self.refresh_btn = QPushButton("New session")
        self.refresh_btn.clicked.connect(self.new_session)
        self.attach_btn = QPushButton("📎 File")
        self.attach_btn.setToolTip("Attach text files (txt, md, code, json, csv, html, docx, pdf…); in the Image "
                                   "analysis profile also images (or paste / drop them)")
        if self.profile.vision:
            self.attach_btn.setText("📎 File / 🖼 Image")
        self.attach_btn.clicked.connect(self._pick_files)
        self.folder_btn = QPushButton("📁 Folder")
        self.folder_btn.setToolTip("Add a folder's text files as context for this session (git-aware, "
                                   "skips binaries/node_modules) — or drop a folder on the window")
        self.folder_btn.clicked.connect(self._pick_folder)
        self.clip_btn = QPushButton("📋 Clips")
        self.clip_btn.setToolTip("Clipboard memory (Ctrl+Shift+V)")
        self.clip_btn.clicked.connect(self._show_clip_menu)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send)
        for b in (self.refresh_btn, self.attach_btn, self.folder_btn, self.clip_btn):
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

        lay.addWidget(QLabel("<b>System prompt</b> (saved per user and profile)"))
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

        lay.addWidget(QLabel("<b>Endpoint URL</b> (OpenAI-compatible /v1)"))
        erow = QHBoxLayout()
        self.endpoint_edit = QLineEdit(self.current_base_url())
        apply_ep = QPushButton("Apply")
        apply_ep.clicked.connect(self._apply_endpoint)
        self.endpoint_edit.returnPressed.connect(self._apply_endpoint)
        reset_ep = QPushButton("Reset to default")
        reset_ep.clicked.connect(self._reset_endpoint)
        erow.addWidget(self.endpoint_edit, 1)
        erow.addWidget(apply_ep)
        erow.addWidget(reset_ep)
        lay.addLayout(erow)
        self.endpoint_msg = QLabel()
        self.endpoint_msg.setStyleSheet("color:#8b949e; font-size:11px;")
        lay.addWidget(self.endpoint_msg)

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
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        self._add_files([p for p in paths if not Path(p).is_dir()])
        for d in (p for p in paths if Path(p).is_dir()):
            self._add_folder(d)
        self.tabs.setCurrentIndex(0)

    # ---------------------------------------------------------------- service / stats
    def current_base_url(self) -> str:
        saved = self.settings.value(f"endpoint_{self.profile.key}", None, str)
        return saved or self.profile.base_url

    def _set_endpoint(self, url: str):
        cleaned = clean_endpoint(url)
        self.settings.setValue(f"endpoint_{self.profile.key}", cleaned)
        if hasattr(self, "endpoint_edit"):
            self.endpoint_edit.setText(cleaned)
        if hasattr(self, "endpoint_msg"):
            self.endpoint_msg.setText(f"Updated for {self.profile.key}: {cleaned} · {time.strftime('%H:%M:%S')}")
        self.endpoint_lbl.setText(f"Endpoint: {cleaned}/v1 ({self.profile.unit})\nModel: –")
        self.n_ctx = 0
        self._set_model(None)
        self._poll()

    def _apply_endpoint(self):
        txt = self.endpoint_edit.text().strip()
        if txt:
            self._set_endpoint(txt)

    def _reset_endpoint(self):
        self.settings.remove(f"endpoint_{self.profile.key}")
        default_url = self.profile.base_url
        if hasattr(self, "endpoint_edit"):
            self.endpoint_edit.setText(default_url)
        if hasattr(self, "endpoint_msg"):
            self.endpoint_msg.setText(f"Reset to default: {default_url}")
        self.endpoint_lbl.setText(f"Endpoint: {default_url}/v1 ({self.profile.unit})\nModel: –")
        self.n_ctx = 0
        self._set_model(None)
        self._poll()

    def _prompt_edit_endpoint(self):
        current = self.current_base_url()
        text, ok = QInputDialog.getText(
            self, "Edit Endpoint URL",
            f"Endpoint URL for {self.profile.label}:",
            text=current
        )
        if ok and text.strip():
            self._set_endpoint(text.strip())

    def _poll(self):
        prof = self.profile
        base_url = self.current_base_url()

        def job():
            st = service.status(base_url, prof.unit)
            self.bridge.status.emit(st)
            self.bridge.gpus.emit(service.gpu_stats())
            if st == "offline":
                self.bridge.model.emit(None)
                return
            if not self.n_ctx:
                self.bridge.ctx.emit(service.n_ctx(base_url))
            self.bridge.model.emit(service.model_info(base_url))
        threading.Thread(target=job, daemon=True).start()

    def _set_model(self, info):
        if not info:
            self.n_ctx = 0  # re-read context size when a (possibly different) model comes back
        name, params = info or ("not loaded", "")
        ctx = f" · {self.n_ctx:,} ctx" if self.n_ctx and info else ""
        self.model_name = name
        base_url = self.current_base_url()
        self.endpoint_lbl.setText(f"Endpoint: {base_url}/v1 ({self.profile.unit})\n"
                                  f"Model: {name}" + (f" · {params}" if params else "") + ctx)
        self.endpoint_lbl.setToolTip(f"{base_url}/v1/chat/completions · {self.profile.gpus}")

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

    def _on_profile(self, _i):
        key = self.profile_cb.currentData()
        if key == self.profile.key:
            return
        if self.worker is not None:
            self.profile_cb.blockSignals(True)
            self.profile_cb.setCurrentIndex(self.profile_cb.findData(self.profile.key))
            self.profile_cb.blockSignals(False)
            return
        self.profile = profiles.PROFILES[key]
        self.settings.setValue("profile", key)
        self.n_ctx = 0
        self.model_name = ""
        self.state = "offline"
        self.endpoint_lbl.setText(f"Endpoint: {self.current_base_url()}/v1\nModel: –")
        if hasattr(self, "endpoint_edit"):
            self.endpoint_edit.setText(self.current_base_url())
        # profile-specific system prompt + UI hints
        self.system_prompt = self._load_prompt()
        self.prompt_edit.setPlainText(self.system_prompt)
        self.attach_btn.setText("📎 File / 🖼 Image" if self.profile.vision else "📎 File")
        self._set_status("offline")
        self._poll()

    def toggle_service(self):
        if self._busy_toggle:
            return
        prof = self.profile
        want_stop = self.state != "offline"
        if not want_stop:
            conflicts = profiles.active_conflicts(prof)
            if conflicts:
                names = "\n".join(f"• {c.label}" for c in conflicts)
                if QMessageBox.question(
                        self, "Switch profile",
                        f"Starting “{prof.label}” needs the GPU/port used by:\n{names}\n\nThey will be stopped. Continue?"
                ) != QMessageBox.StandardButton.Yes:
                    return
        self._busy_toggle = True
        self.power_btn.setEnabled(False)
        self.status_lbl.setText("stopping…" if want_stop else "starting…")

        def job():
            err = ""
            try:
                if want_stop:
                    profiles.stop(prof)
                    # vision parked the always-on tier: bring it back
                    if prof.key == "vision" and self.settings.value("parked_services", False, bool):
                        self.settings.setValue("parked_services", False)
                        profiles.start(profiles.PROFILES["services"])
                else:
                    for c in profiles.active_conflicts(prof):
                        if prof.key == "vision" and c.key == "services":
                            self.settings.setValue("parked_services", True)
                        profiles.stop(c)
                    if prof.key == "services":
                        self.settings.setValue("parked_services", False)
                    profiles.start(prof)
            except Exception as e:  # noqa: BLE001
                err = str(e)
            self._busy_toggle = False
            self.bridge.status.emit(service.status(self.current_base_url(), prof.unit))
            if err:
                self.bridge.error.emit(f"{prof.label}: {err}")
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
        if bar.isSliderDown():
            # User is actively dragging the scrollbar slider; defer render so drag isn't interrupted
            self._render_pending = True
            return

        prev_val = bar.value()
        prev_max = bar.maximum()
        stick = (prev_val >= prev_max - 48) if prev_max > 0 else True

        parts = []
        for m in self.msgs:
            parts.append(self._md_context(m) if m["role"] == "context" else
                         self._md_message(m["role"], m["content"], m["ts"], m.get("attachments", [])))
        if self.worker is not None:
            parts.append(self._md_message("assistant", self.reply or "_…_", time.strftime("%Y-%m-%d %H:%M:%S"), []))
        if self.last_error:
            parts.append(f"> ⚠️ **Error:** {self.last_error}")
        if not parts:
            parts.append("_Start chatting — responses render as Markdown._")
        self.view.setMarkdown("\n\n---\n\n".join(parts))

        if stick:
            bar.setValue(bar.maximum())
            if hasattr(self, "scroll_bottom_btn"):
                self.scroll_bottom_btn.hide()
        else:
            bar.setValue(min(prev_val, bar.maximum()))
            if hasattr(self, "scroll_bottom_btn") and bar.maximum() > 60:
                self.scroll_bottom_btn.show()

    def _scroll_to_bottom(self):
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())
        if hasattr(self, "scroll_bottom_btn"):
            self.scroll_bottom_btn.hide()

    def _on_scroll(self, val):
        bar = self.view.verticalScrollBar()
        at_bottom = (val >= bar.maximum() - 48) if bar.maximum() > 0 else True
        if hasattr(self, "scroll_bottom_btn"):
            self.scroll_bottom_btn.setVisible(not at_bottom and bar.maximum() > 60)

    @staticmethod
    def _md_context(m):
        files = m.get("attachments", [])
        chars = sum(a["chars"] for a in files)
        shown = "\n".join(f"- `{a['name']}`" for a in files[:25])
        more = f"\n- … +{len(files) - 25} more" if len(files) > 25 else ""
        return (f"**📁 Folder context** · *{fmt_ts(m['ts'])}*\n\n{m['content'].splitlines()[0][2:]}\n\n"
                f"{len(files)} files · {chars:,} chars added to this session's context\n\n{shown}{more}")

    @staticmethod
    def _md_message(role, content, ts, attachments):
        who = "🧑 You" if role == "user" else "🤖 Assistant"
        body = content.replace("\n", "  \n") if role == "user" else content
        files = "".join(
            f"\n\n🖼 `{a['name']}` ({a.get('w', '?')}×{a.get('h', '?')})" if a.get("kind") == "image" else
            f"\n\n📎 `{a['name']}` ({a['chars']:,} chars{', truncated' if a.get('truncated') else ''})"
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

        payload = self._payload()
        self.worker = ChatWorker(payload, base_url=self.current_base_url(), parent=self)
        self.worker.token.connect(self._on_token)
        self.worker.stats.connect(self._on_stats)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_fail)
        self.worker.start()
        self._update_controls()
        self._update_stats()
        self._render()

    def _payload(self):
        """System prompt + folder contexts merged into ONE system message (Qwen templates want that)."""
        system = self.system_prompt
        ctxs = [m["content"] for m in self.msgs if m["role"] == "context"]
        if ctxs:
            system += ("\n\nThe user loaded the following folder(s) as reference context for this "
                       "conversation. Use it to answer questions about the files.\n\n" + "\n\n".join(ctxs))
        return [{"role": "system", "content": system}] + [
            {"role": m["role"], "content": model_content(m, self.profile.vision)}
            for m in self.msgs if m["role"] != "context"]

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
                if is_image(p):
                    if not self.profile.vision:
                        raise UnsupportedFile("images need the “Image analysis” profile (select it above)")
                    self.attachments.append(load_image(p))
                    continue
                self.attachments.append(parse_file(p))
            except (UnsupportedFile, OSError, Exception) as e:  # noqa: BLE001
                errs.append(f"{Path(p).name}: {e}")
        if errs:
            QMessageBox.warning(self, "Could not read file", "\n".join(errs))
        self._update_attachments()

    def _paste_image(self, img):
        if not self.profile.vision:
            QMessageBox.information(self, "Image paste", "Select the “Image analysis” profile to send images.")
            return
        try:
            self.attachments.append(encode_image(img, f"pasted-{time.strftime('%H%M%S')}.jpg"))
        except ValueError as e:
            QMessageBox.warning(self, "Image paste", str(e))
        self._update_attachments()

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add folder as session context", str(Path.home()))
        if d:
            self._add_folder(d)

    def _add_folder(self, root):
        if self.worker is not None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            entries, seen = scan_folder(root)
            files = read_candidates(entries)
        finally:
            QApplication.restoreOverrideCursor()
        if not files:
            QMessageBox.information(self, "Folder context", f"No text files found in\n{root}")
            return
        # ~60% of the free window, ~3 chars/token, leaving room for the conversation itself
        if self.n_ctx:
            budget = max(8000, int((self.n_ctx - self.ctx_used - 4096) * 0.6 * 3))
        else:
            budget = 50000
        dlg = FolderDialog(self, root, files, seen, budget)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.selected():
            return
        picked = dlg.selected()
        text = build_folder_context(root, picked, [e["rel"] for e in entries])
        if self.session_id is None:
            self.session_id = self.db.create_session(self.user, f"📁 {Path(root).name}")
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        meta = [dict(name=f["rel"], chars=f["chars"], truncated=f["truncated"]) for f in picked]
        mid = self.db.add_message(self.session_id, "context", text, meta, ts)
        self.msgs.append(dict(id=mid, role="context", content=text, ts=ts, attachments=meta))
        self.ctx_used += len(text) // 3
        self.ctx_exact = False
        self._update_stats()
        self._render()
        self._refresh_history()
        self._refresh_db_status()

    def _clear_attachments(self):
        self.attachments = []
        self._update_attachments()

    def _update_attachments(self):
        self.attach_lbl.setText("  ".join(
            f"🖼 {a['name']} ({a['w']}×{a['h']})" if a.get("kind") == "image"
            else f"📎 {a['name']} ({a['chars']:,}{'+' if a['truncated'] else ''})" for a in self.attachments))
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
        self.ctx_used = sum(len(model_text(m)) for m in self.msgs) // 4 + 1500 * sum(
            1 for m in self.msgs for a in m.get("attachments", []) if a.get("kind") == "image")
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
    def _prompt_key(self):
        """users-table key: plain user name for the default profile, user@profile otherwise."""
        return self.user if self.profile.key == profiles.DEFAULT_PROFILE else f"{self.user}@{self.profile.key}"

    def _load_prompt(self):
        return self.db.get_prompt(self._prompt_key()) or self.profile.default_prompt

    def _apply_user(self):
        name = self.user_edit.text().strip() or DEFAULT_USER
        self.user_edit.setText(name)
        self.user = name
        self.settings.setValue("user", name)
        self.system_prompt = self._load_prompt()
        self.prompt_edit.setPlainText(self.system_prompt)
        self.prompt_msg.setText(f"Loaded prompt for {name}")
        self.new_session()
        self._refresh_history()

    def _save_prompt(self):
        text = self.prompt_edit.toPlainText().strip() or self.profile.default_prompt
        self.db.set_prompt(self._prompt_key(), text)
        self.system_prompt = text
        self.prompt_msg.setText(f"Saved for {self.user} / {self.profile.key} · {datetime.now():%H:%M:%S}")
        self._refresh_db_status()

    def _reset_prompt(self):
        self.db.reset_prompt(self._prompt_key())
        self.system_prompt = self.profile.default_prompt
        self.prompt_edit.setPlainText(self.system_prompt)
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
        self.system_prompt = self._load_prompt()
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
