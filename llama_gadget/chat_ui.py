"""Message controls, exact-text copying, and stored-image previews."""
import copy
import json
import math
import re

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon, QPixmap
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget)

from .images import decode_attachment_image
from .theme import active_tokens, bind_theme


def code_block(text):
    fence = '`' * max(3, max((len(s) for s in re.findall(r'`+', text)), default=0) + 1)
    return f'{fence}\n{text}\n{fence}'


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
        self.document().setTextWidth(max(50, self.viewport().width()))
        height = math.ceil(self.document().size().height()) + active_tokens().spacing.sm
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
        gap = active_tokens().spacing.xxs
        layout.setContentsMargins(0, gap, 0, gap)
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
        self.body.setMarkdown(content)
        self.body.setVisible(bool(content))
        self.attachments.set_attachments(message.get('attachments', []) if role != 'context' else [])


class ChatTranscript(QScrollArea):
    image_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        container = QWidget()
        self.column = QVBoxLayout(container)
        gap = active_tokens().spacing.xxs
        self.column.setContentsMargins(gap, gap, gap, gap)
        self.column.addStretch()
        self.setWidget(container)
        self.cards = {}
        bind_theme(self.apply_theme)

    def apply_theme(self, adapter):
        self.setStyleSheet(f"QScrollArea, QScrollArea > QWidget > QWidget {{ background: {adapter.surface('primary')}; }}")

    def set_messages(self, messages, live_reply='', activity='', error='', show_tools=False):
        visible = []
        for index, message in enumerate(messages):
            if message['role'] == 'tool' and not show_tools:
                continue
            if message['role'] == 'assistant' and message.get('metadata', {}).get('tool_calls') and not show_tools and not message['content']:
                continue
            visible.append((('saved', message.get('id', index)), message))
        if live_reply or activity:
            visible.append(('live', dict(role='assistant' if live_reply else 'notice', content=live_reply or activity)))
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
        self.column.activate()

    def toPlainText(self):
        return '\n\n'.join(self.column.itemAt(i).widget().body.toPlainText()
                            for i in range(self.column.count()-1))
