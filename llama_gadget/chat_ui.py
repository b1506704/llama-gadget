"""Message controls, exact-text copying, and stored-image previews."""
import copy
import json
import math
import re

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut, QTextCursor
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar,
                             QPushButton, QScrollArea, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget)

from .images import decode_attachment_image
from .lifecycle import SurfaceState
from .theme import TOKENS


def code_block(text):
    fence = '`' * max(3, max((len(s) for s in re.findall(r'`+', text)), default=0) + 1)
    return f'{fence}\n{text}\n{fence}'


class TokenFrameCoalescer(QObject):
    """Batches streamed chunks so the UI does at most one append + scroll pass per frame.

    The single-shot timer only runs while chunks are buffered, so an idle window has no
    timer overhead. ``flushed`` fires once per non-empty flush with the joined text."""
    flushed = pyqtSignal(str)

    def __init__(self, interval_ms: int = 16, parent=None):
        super().__init__(parent)
        self._chunks = []
        self.timer = QTimer(self, interval=interval_ms, singleShot=True)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)  # coarse timers may fire up to 5% early
        self.timer.timeout.connect(self.flush)

    def enqueue(self, chunk: str) -> None:
        if not chunk:
            return
        self._chunks.append(chunk)
        if not self.timer.isActive():
            self.timer.start()

    def flush(self) -> str:
        self.timer.stop()
        if not self._chunks:
            return ''
        text, self._chunks = ''.join(self._chunks), []
        self.flushed.emit(text)
        return text

    def stop(self) -> None:
        """Drain anything still buffered (stream completion), then leave the timer idle."""
        self.flush()

    def discard(self) -> None:
        """Drop buffered chunks without rendering them (cancellation keeps only what is on screen)."""
        self.timer.stop()
        self._chunks = []


class LifecycleBadge(QLabel):
    """Pill showing a surface's SurfaceState in its semantic theme colour."""

    def __init__(self, parent=None, tokens=TOKENS):
        super().__init__(parent)
        self.tokens = tokens
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state(SurfaceState.IDLE)

    def set_state(self, state: SurfaceState) -> None:
        self.state = state
        color, t = self.tokens.state_color(state), self.tokens
        self.setText(state.value)
        self.setStyleSheet(f'color: {color}; border: 1px solid {color}; border-radius: {t.radius}px; '
                           f'padding: 0 {2 * t.spacing}px; font-size: {t.font_small}px; background: {t.surface_raised};')


class SkeletonPlaceholder(QWidget):
    """Placeholder bars shown in place of an empty pane until its content is ready."""

    def __init__(self, lines=3, parent=None, tokens=TOKENS):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.spacing, 2 * tokens.spacing, tokens.spacing, 2 * tokens.spacing)
        layout.setSpacing(2 * tokens.spacing)
        self.bars = []
        for index in range(lines):
            bar = QFrame()
            bar.setObjectName('skeletonBar')
            bar.setFixedHeight(12)
            bar.setStyleSheet(f'QFrame#skeletonBar {{ background: {tokens.skeleton}; border-radius: {tokens.radius}px; }}')
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(bar, 4 - index % 3)  # staggered widths read as text lines
            row.addStretch(1 + index % 3)
            layout.addLayout(row)
            self.bars.append(bar)
        self.setToolTip('Loading…')
        self.hide()


class StreamProgressIndicator(QWidget):
    """Busy bar plus live tokens/s; only visible while a surface is Streaming."""

    def __init__(self, parent=None, tokens=TOKENS):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedSize(60, 6)
        self.bar.setStyleSheet(f'QProgressBar {{ background: {tokens.border}; border: none; border-radius: 3px; }} '
                               f'QProgressBar::chunk {{ background: {tokens.streaming}; border-radius: 3px; }}')
        self.rate_lbl = QLabel()
        self.rate_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.rate_lbl.setStyleSheet(f'color: {tokens.text_muted}; font-size: {tokens.font_small}px;')
        layout.addWidget(self.bar)
        layout.addWidget(self.rate_lbl)
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        self.bar.setRange(0, 0 if active else 1)  # 0..0 is Qt's indeterminate (animated) mode
        self.setVisible(active)

    def update_telemetry(self, telemetry) -> None:
        self.rate_lbl.setText(f'{telemetry.tokens_per_sec:.1f} tok/s · {telemetry.tokens:,} tok')


class LifecycleStatusBar(QWidget):
    """Badge + streaming indicator bound to a SurfaceLifecycleController."""

    def __init__(self, label='', parent=None, tokens=TOKENS):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if label:
            caption = QLabel(label)
            caption.setStyleSheet(f'color: {tokens.text_muted}; font-size: {tokens.font_small}px;')
            layout.addWidget(caption)
        self.badge = LifecycleBadge(tokens=tokens)
        self.progress = StreamProgressIndicator(tokens=tokens)
        layout.addWidget(self.badge)
        layout.addWidget(self.progress)
        layout.addStretch()
        self.controller = None

    def bind(self, controller) -> None:
        if self.controller is not None:
            self.controller.state_changed.disconnect(self.set_state)
        self.controller = controller
        controller.state_changed.connect(self.set_state)
        self.refresh()

    def set_state(self, state: SurfaceState) -> None:
        self.badge.set_state(state)
        self.progress.set_active(state is SurfaceState.STREAMING)
        if state is SurfaceState.STREAMING and self.controller is not None:
            self.progress.update_telemetry(self.controller.telemetry)

    def refresh(self) -> None:
        """Re-read state and telemetry (called on the frame tick and when a view is re-shown)."""
        if self.controller is None:
            return
        if self.badge.state is not self.controller.state:
            self.set_state(self.controller.state)
        if self.controller.state is SurfaceState.STREAMING:
            self.progress.update_telemetry(self.controller.telemetry)


def install_cancel_shortcut(surface: QWidget, cancel) -> QShortcut:
    """Bind Esc in ``surface``'s window to ``cancel``. An open popup or modal overlay is
    closed first instead, so Esc never stops generation while an overlay is up."""
    shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), surface)
    shortcut.setContext(Qt.ShortcutContext.WindowShortcut)

    def on_escape():
        overlay = QApplication.activePopupWidget() or QApplication.activeModalWidget()
        if overlay is not None and overlay is not surface.window():
            overlay.close()
            return
        cancel()
    shortcut.activated.connect(on_escape)
    return shortcut


class MarkdownBody(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.document().contentsChanged.connect(self.fit_height)
        self.anchorClicked.connect(self.open_external)

    def open_external(self, url):
        # Model Markdown can never invoke application controls or open local files.
        if url.scheme().lower() in ('http', 'https', 'mailto'):
            QDesktopServices.openUrl(url)

    def fit_height(self):
        width = max(50, self.viewport().width())
        if self.document().textWidth() != width:  # re-setting the same width relayouts everything
            self.document().setTextWidth(width)
        height = math.ceil(self.document().size().height()) + 12
        if height != self.height():
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_height()


class AttachmentStrip(QWidget):
    image_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.row = QVBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self._attachments = None

    def set_attachments(self, attachments):
        if self._attachments == attachments:
            return
        self._attachments = copy.deepcopy(attachments)
        while self.row.count():
            widget = self.row.takeAt(0).widget()
            if isinstance(widget, QPushButton):
                widget.clicked.disconnect()
            widget.setParent(None)
            widget.deleteLater()
        for attachment in self._attachments:
            if attachment.get('kind') == 'image':
                button = QPushButton(f"{attachment['name']} · {attachment.get('w', '?')}×{attachment.get('h', '?')}")
                try:
                    image = decode_attachment_image(attachment)
                    button.setIcon(QIcon(QPixmap.fromImage(image)))
                    from PyQt6.QtCore import QSize
                    button.setIconSize(QSize(48, 36))
                except ValueError:
                    button.setToolTip('Image data is unavailable; click for details')
                button.clicked.connect(lambda checked=False, a=attachment: self.image_requested.emit(copy.deepcopy(a)))
                self.row.addWidget(button)
            else:
                label = QLabel(f"{attachment['name']} · {attachment.get('chars', 0):,} chars")
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setWordWrap(True)
                self.row.addWidget(label)
        self.setVisible(bool(attachments))


class ImagePreviewDialog(QDialog):
    def __init__(self, attachment, parent=None):
        super().__init__(parent)
        self.image = decode_attachment_image(attachment)
        self.setWindowTitle(attachment.get('name', 'Image preview'))
        self.setStyleSheet('QDialog { background: #14161a; } QLabel, QCheckBox { color: #d0d4da; }')
        self.resize(800, 600)
        layout = QVBoxLayout(self)
        label = QLabel(f"{attachment.get('name', 'Image')} · {self.image.width()}×{self.image.height()}")
        label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(label)
        self.actual_size_cb = QCheckBox('Actual size')
        self.actual_size_cb.toggled.connect(self.update_image)
        layout.addWidget(self.actual_size_cb)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label = QLabel()
        self.scroll.setWidget(self.label)
        layout.addWidget(self.scroll, 1)
        self.update_image()

    def update_image(self):
        pixmap = QPixmap.fromImage(self.image)
        if not self.actual_size_cb.isChecked():
            size = self.scroll.viewport().size()
            pixmap = pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.label.setPixmap(pixmap)
        self.label.resize(pixmap.size())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'scroll'):
            self.update_image()


class MessageCard(QWidget):
    image_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.signature = None
        self.source = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        header = QHBoxLayout()
        self.heading = QLabel()
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setWordWrap(True)
        header.addWidget(self.heading, 1)
        self.copy_btn = QPushButton('Copy')
        self.copy_btn.clicked.connect(self.copy_response)
        header.addWidget(self.copy_btn)
        layout.addLayout(header)
        self.body = MarkdownBody()
        layout.addWidget(self.body)
        self.attachments = AttachmentStrip()
        self.attachments.image_requested.connect(self.image_requested.emit)
        layout.addWidget(self.attachments)
        self.copy_timer = QTimer(self, interval=1200, singleShot=True)
        self.copy_timer.timeout.connect(lambda: self.copy_btn.setText('Copy'))

    def copy_response(self):
        QApplication.clipboard().setText(self.source['content'])
        self.copy_btn.setText('Copied')
        self.copy_timer.start()

    def append_text(self, text):
        # Incremental append: only the touched block is laid out, no Markdown re-parse.
        cursor = QTextCursor(self.body.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.source = dict(self.source, content=self.source.get('content', '') + text)
        self.signature = (dict(self.source), self.signature[1])
        self.copy_btn.setVisible(self.source.get('role') == 'assistant')
        self.body.setVisible(True)

    def update_message(self, message, show_tools):
        signature = (message, show_tools)
        if self.signature == signature:
            return
        self.signature = copy.deepcopy(signature)
        self.source = copy.deepcopy(message)
        role, content = message['role'], message.get('content', '')
        metadata = message.get('metadata', {})
        calls = metadata.get('tool_calls', [])
        heading = {'user': 'You', 'assistant': 'Assistant', 'tool': 'Tool result', 'context': 'Folder context'}.get(role, role)
        self.heading.setText(heading + (' · ' + message['ts'] if message.get('ts') else ''))
        self.copy_btn.setVisible(role == 'assistant' and bool(content))
        if role == 'tool':
            heading += ': ' + metadata.get('name', 'tool')
            self.heading.setText(heading)
            try:
                body = json.dumps(json.loads(content), indent=2, ensure_ascii=False)
            except ValueError:
                body = content
            content = code_block(body)
        elif role == 'context':
            files = message.get('attachments', [])
            content = content.splitlines()[0] if content else 'Folder context'
            content += '\n\n' + '\n'.join('- ' + a['name'] for a in files)
        elif calls and show_tools:
            content += '\n\nTool requests\n\n' + code_block(json.dumps(calls, indent=2, ensure_ascii=False))
        elif role == 'user':
            content = content.replace('\n', '  \n')
        if message.get('streaming'):
            self.body.setPlainText(content)
        else:
            self.body.setMarkdown(content)
        self.body.setVisible(bool(content))
        self.attachments.set_attachments(message.get('attachments', []) if role != 'context' else [])


class ChatTranscript(QScrollArea):
    image_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setStyleSheet('QScrollArea, QScrollArea > QWidget > QWidget { background: #1b1e24; }')
        container = QWidget()
        self.column = QVBoxLayout(container)
        self.column.setContentsMargins(4, 4, 4, 4)
        self.skeleton = SkeletonPlaceholder()
        self.column.addWidget(self.skeleton)  # always after the cards, before the stretch
        self.column.addStretch()
        self.setWidget(container)
        self.cards = {}
        self._skeleton = False

    def set_skeleton(self, visible: bool) -> None:
        """Show placeholder bars in place of the pending/empty content while initializing."""
        self._skeleton = bool(visible)
        self.skeleton.setVisible(self._skeleton)
        if 'empty' in self.cards:
            self.cards['empty'].setVisible(not self._skeleton)

    def set_messages(self, messages, live_reply='', activity='', error='', show_tools=False):
        visible = []
        for index, message in enumerate(messages):
            if message['role'] == 'tool' and not show_tools:
                continue
            if message['role'] == 'assistant' and message.get('metadata', {}).get('tool_calls') and not show_tools and not message['content']:
                continue
            visible.append((('saved', message.get('id', index)), message))
        if live_reply:
            # Plain text while streaming so frames can append incrementally (see append_live).
            visible.append(('live', dict(role='assistant', content=live_reply, streaming=True)))
        elif activity:
            visible.append(('live', dict(role='notice', content=activity)))
        if error:
            visible.append(('error', dict(role='notice', content='Error: ' + error)))
        if not visible:
            visible.append(('empty', dict(role='notice', content='Start chatting — responses render as Markdown.')))
        keys = {key for key, _ in visible}
        for key in list(self.cards):
            if key not in keys:
                card = self.cards.pop(key)
                self.column.removeWidget(card)
                card.setParent(None)
                card.deleteLater()
        for index, (key, message) in enumerate(visible):
            if key not in self.cards:
                card = MessageCard()
                card.image_requested.connect(self.image_requested.emit)
                self.cards[key] = card
                self.column.insertWidget(index, card)
            card = self.cards[key]
            if self.column.indexOf(card) != index:
                self.column.removeWidget(card)
                self.column.insertWidget(index, card)
            card.update_message(message, show_tools)
        if 'empty' in self.cards:
            self.cards['empty'].setVisible(not self._skeleton)
        self.column.activate()

    def append_live(self, text):
        """Append streamed text to the live reply card; False when a full render is needed."""
        card = self.cards.get('live')
        if card is None or not card.source.get('streaming'):
            return False
        card.append_text(text)
        # Apply the resulting geometry now so the caller's scroll pass sees the new range.
        card.layout().activate()
        self.column.activate()
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        return True

    def toPlainText(self):
        widgets = (self.column.itemAt(i).widget() for i in range(self.column.count()))
        return '\n\n'.join(w.body.toPlainText() for w in widgets if isinstance(w, MessageCard))
