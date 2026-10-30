"""Cmd+K command palette: fuzzy search over actions, models and navigation targets."""
from dataclasses import dataclass, field
from typing import Callable, Optional

from PyQt6.QtCore import QEvent, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (QApplication, QDialog, QLabel, QLineEdit, QListView, QStyle,
                             QStyledItemDelegate, QVBoxLayout)

from . import theme
from .shortcuts import restore_focus_on_close

ITEM_ROLE = Qt.ItemDataRole.UserRole + 1


@dataclass
class PaletteItem:
    id: str
    title: str
    category: str
    hint: str = ''
    run: Optional[Callable[[], None]] = None
    _title: str = field(init=False, repr=False, compare=False)
    _haystack: str = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        self._title = self.title.lower()
        self._haystack = f'{self.title} {self.category}'.lower()


def fuzzy_score(query, text):
    """Score ``query`` as an ordered subsequence of ``text`` (both lowercase); None if it doesn't match."""
    if not query:
        return 0.0
    pos = text.find(query)
    if pos >= 0:  # contiguous match: strongest, especially at a word start
        return 100.0 + (20 if pos == 0 else 10 if not text[pos - 1].isalnum() else 0) - pos * 0.1
    score, last, ti = 0.0, -1, 0
    for ch in query:
        ti = text.find(ch, ti)
        if ti < 0:
            return None
        score += 1
        if ti == last + 1:
            score += 3
        if ti == 0 or not text[ti - 1].isalnum():
            score += 4
        if last >= 0:
            score -= min(ti - last - 1, 10) * 0.2
        last = ti
        ti += 1
    # Scattered single-letter hits ("theme" inside "switch model: image") are noise, not matches.
    return score if score >= 2 * len(query) else None


class MRUHistoryTracker:
    """Most recently executed palette item ids, newest first, capped at ``limit``."""

    def __init__(self, limit=8):
        self.limit = limit
        self._ids = []

    def record_execution(self, item_id):
        if item_id in self._ids:
            self._ids.remove(item_id)
        self._ids.insert(0, item_id)
        del self._ids[self.limit:]

    def get_recent_items(self):
        return list(self._ids)


class FuzzyMatcher:
    def __init__(self, mru=None):
        self.mru = mru

    def match(self, query, items):
        """Matching items ranked best-first; an empty query lists recently used items first."""
        recent = self.mru.get_recent_items() if self.mru else []
        rank = {item_id: i for i, item_id in enumerate(recent)}
        query = ' '.join(query.lower().split())
        if not query:
            return sorted(items, key=lambda it: rank.get(it.id, len(rank)))
        scored = []
        for index, item in enumerate(items):
            best = fuzzy_score(query, item._title)
            if best is None:  # fall back to title + category, slightly discounted
                best = fuzzy_score(query, item._haystack)
                if best is None:
                    continue
                best -= 2
            bonus = 0.5 * (len(rank) - rank[item.id]) if item.id in rank else 0
            scored.append((-(best + bonus), index, item))
        scored.sort(key=lambda row: row[:2])
        return [item for *_, item in scored]


class CommandPaletteItemDelegate(QStyledItemDelegate):
    """Row: title on the left, category and shortcut badges on the right, coloured from theme tokens."""

    def __init__(self, tokens, parent=None):
        super().__init__(parent)
        self.tokens = tokens

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), QFontMetrics(option.font).height() + 14)

    def paint(self, painter, option, index):
        item = index.data(ITEM_ROLE)
        t = self.tokens
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(2, 1, -2, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.selection))
            painter.drawRoundedRect(rect, 6, 6)
        fm = QFontMetrics(option.font)
        right = rect.right() - 8
        for text in (item.hint, item.category):
            if not text:
                continue
            w = fm.horizontalAdvance(text) + 12
            badge = QRectF(right - w, rect.top() + 4, w, rect.height() - 8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.badge))
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QColor(t.badge_text))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
            right = badge.left() - 6
        painter.setPen(QColor(t.selection_text if selected else t.text_strong))
        title_rect = QRectF(rect.left() + 8, rect.top(), max(0, right - rect.left() - 12), rect.height())
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         fm.elidedText(item.title, Qt.TextElideMode.ElideRight, int(title_rect.width())))
        painter.restore()


class CommandPaletteDialog(QDialog):
    def __init__(self, items, tokens=theme.DARK, mru=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Command Palette')
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet(theme.stylesheet(tokens) + f'QDialog {{ border: 1px solid {tokens.border}; }}')
        self.items = list(items)
        self.mru = mru if mru is not None else MRUHistoryTracker()
        self.matcher = FuzzyMatcher(self.mru)
        self.chosen = None
        self.results = []

        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Type a command, model or view…')
        self.search.setAccessibleName('Command palette search')
        self.search.installEventFilter(self)
        self.search.textChanged.connect(self.refilter)
        layout.addWidget(self.search)
        self.model = QStandardItemModel(self)
        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setItemDelegate(CommandPaletteItemDelegate(tokens, self.list))
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # focus always stays in the search box
        self.list.setUniformItemSizes(True)
        self.list.clicked.connect(lambda _: self.execute_current())
        layout.addWidget(self.list, 1)
        self.empty = QLabel('No matching commands')
        self.empty.setProperty('muted', True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.hide()
        layout.addWidget(self.empty)
        self.resize(460, 380)
        self.refilter('')

    def showEvent(self, event):
        super().showEvent(event)
        self.activateWindow()
        QApplication.setActiveWindow(self)
        self.search.setFocus(Qt.FocusReason.PopupFocusReason)

    def refilter(self, text):
        self.results = self.matcher.match(text, self.items)
        self.model.clear()
        for item in self.results:
            row = QStandardItem(item.title)
            row.setData(item, ITEM_ROLE)
            row.setEditable(False)
            self.model.appendRow(row)
        self.list.setVisible(bool(self.results))
        self.empty.setVisible(not self.results)
        if self.results:
            self.list.setCurrentIndex(self.model.index(0, 0))

    def current_item(self):
        index = self.list.currentIndex()
        return self.results[index.row()] if index.isValid() and index.row() < len(self.results) else None

    def move_selection(self, step):
        n = len(self.results)
        if not n:
            return
        row = self.list.currentIndex().row()
        self.list.setCurrentIndex(self.model.index((row + step) % n if row >= 0 else 0, 0))

    def execute_current(self):
        item = self.current_item()
        if item is None:
            return
        self.chosen = item
        self.mru.record_execution(item.id)
        self.accept()

    def eventFilter(self, watched, event):
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self.move_selection(1)
                return True
            if key == Qt.Key.Key_Up:
                self.move_selection(-1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.execute_current()
                return True
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(watched, event)

    def place_over(self, window):
        if window is None:
            return
        width = max(360, min(560, window.width() - 32))
        self.resize(width, self.height())
        top_left = window.mapToGlobal(window.rect().topLeft())
        self.move(top_left.x() + (window.width() - width) // 2, top_left.y() + 48)

    def exec_and_restore_focus(self, parent_window=None):
        """Run modally, give focus back to the previously focused control, then run the chosen item."""
        restore_focus_on_close(self, QApplication.focusWidget())
        self.place_over(parent_window or self.parentWidget())
        self.exec()
        if self.chosen is not None and self.chosen.run is not None:
            self.chosen.run()
        return self.chosen
