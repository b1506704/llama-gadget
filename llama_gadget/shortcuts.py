"""Central action registry: every keyboard-reachable command, its chords, and its handler.

Chords are written canonically with ``Cmd`` as the primary modifier ("Cmd+Shift+K").
``Cmd`` always resolves to ``Qt.Modifier.CTRL``: Qt itself maps that modifier to the
Command key on macOS and to Control on Windows/Linux, so one binding serves every
platform. Only the human-readable hint differs per platform.
"""
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QApplication, QComboBox, QDialog,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                             QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .theme import stylesheet

GLOBAL = Qt.ShortcutContext.ApplicationShortcut
CONTEXTUAL = Qt.ShortcutContext.WidgetWithChildrenShortcut

PRIMARY_MODIFIER = Qt.Modifier.CTRL
_KEY_ALIASES = {'return': 'Return', 'enter': 'Enter', 'comma': ',', 'slash': '/', 'period': '.',
                'esc': 'Esc', 'escape': 'Esc', 'space': 'Space', 'tab': 'Tab'}
_MAC_NAMES = {'Cmd': 'Cmd', 'Ctrl': 'Ctrl', 'Shift': 'Shift', 'Alt': 'Option', 'Meta': 'Ctrl'}
_OTHER_NAMES = {'Cmd': 'Ctrl', 'Ctrl': 'Ctrl', 'Shift': 'Shift', 'Alt': 'Alt', 'Meta': 'Meta'}


def is_mac(platform=None):
    return (platform or sys.platform) == 'darwin'


def _split(chord):
    parts = [p.strip() for p in chord.replace('++', '+Plus').split('+') if p.strip()]
    if not parts:
        raise ValueError('empty chord')
    *mods, key = parts
    mods = [m.capitalize() if m.lower() != 'cmd' else 'Cmd' for m in mods]
    for m in mods:
        if m not in _OTHER_NAMES:
            raise ValueError(f'unknown modifier {m!r} in {chord!r}')
    key = {'Plus': '+'}.get(key, _KEY_ALIASES.get(key.lower(), key.upper() if len(key) == 1 else key))
    return mods, key


def resolve_key_sequence(chord):
    """Canonical chord -> QKeySequence; ``Cmd`` becomes the platform primary modifier (Qt CTRL)."""
    mods, key = _split(chord)
    portable = '+'.join(['Ctrl' if m == 'Cmd' else m for m in mods] + [key])
    seq = QKeySequence(portable, QKeySequence.SequenceFormat.PortableText)
    if seq.isEmpty() or seq[0].key() == Qt.Key.Key_unknown:
        raise ValueError(f'unparseable chord: {chord!r}')
    return seq


def format_shortcut_hint(chord, platform=None):
    """Human-readable hint: 'Cmd+Shift+K' on macOS, 'Ctrl+Shift+K' on Windows/Linux."""
    mods, key = _split(chord)
    names = _MAC_NAMES if is_mac(platform) else _OTHER_NAMES
    return '+'.join([names[m] for m in mods] + [key])


@dataclass
class CommandSpec:
    id: str
    title: str
    category: str
    default_chord: str = ''
    context: Qt.ShortcutContext = GLOBAL
    handler: Optional[Callable[[], None]] = None
    description: str = ''
    alt_chords: tuple = ()
    scope: str = 'window'  # which widget the shortcut is bound to: 'window', 'chat', 'tools', ...
    hint_extra: str = ''   # non-chord trigger shown in hints, e.g. '?'

    def __post_init__(self):
        if not self.id or not self.title or not self.category:
            raise ValueError('CommandSpec needs id, title and category')
        for chord in self.chords:
            resolve_key_sequence(chord)

    @property
    def chords(self):
        return tuple(c for c in (self.default_chord, *self.alt_chords) if c)


class ActionRegistry:
    def __init__(self):
        self._specs = {}

    def register(self, spec):
        if spec.id in self._specs:
            raise ValueError(f'duplicate action id: {spec.id}')
        self._specs[spec.id] = spec

    def unregister(self, action_id):
        self._specs.pop(action_id, None)

    def get(self, action_id):
        return self._specs.get(action_id)

    def get_all(self):
        return list(self._specs.values())

    def __iter__(self):
        return iter(self._specs.values())

    def __len__(self):
        return len(self._specs)

    def categories(self):
        return list(dict.fromkeys(spec.category for spec in self._specs.values()))

    def list_by_category(self, category=None):
        if category is not None:
            return [s for s in self._specs.values() if s.category == category]
        grouped = {}
        for spec in self._specs.values():
            grouped.setdefault(spec.category, []).append(spec)
        return grouped

    def dispatch(self, action_id):
        spec = self._specs.get(action_id)
        if spec is None:
            raise KeyError(action_id)
        if spec.handler is not None:
            spec.handler()

    resolve_key_sequence = staticmethod(resolve_key_sequence)
    format_shortcut_hint = staticmethod(format_shortcut_hint)

    def hint(self, action_id, platform=None):
        spec = self._specs[action_id]
        hints = [format_shortcut_hint(c, platform) for c in spec.chords]
        if spec.hint_extra:
            hints.append(spec.hint_extra)
        return ' / '.join(hints)

    def global_sequences(self):
        """Key sequences of application-wide shortcuts (they win over text-widget key bindings)."""
        return {resolve_key_sequence(c).toString(QKeySequence.SequenceFormat.PortableText)
                for s in self._specs.values() if s.context == GLOBAL for c in s.chords}

    def bind_shortcuts(self, parent_widget, scope='window'):
        """Attach a QShortcut per chord of every handler-backed action in ``scope`` to ``parent_widget``."""
        shortcuts = []
        for spec in self._specs.values():
            if spec.scope != scope or spec.handler is None:
                continue
            for chord in spec.chords:
                sc = QShortcut(resolve_key_sequence(chord), parent_widget)
                sc.setContext(spec.context)
                sc.setObjectName(spec.id)
                sc.activated.connect(spec.handler)
                shortcuts.append(sc)
        return shortcuts


def restore_focus_on_close(dialog, target=None):
    """Return focus to ``target`` (default: the current focus widget) when ``dialog`` finishes."""
    target = target if target is not None else QApplication.focusWidget()

    def restore(*_):
        try:
            if target is not None and target.isVisible():
                target.activateWindow()
                target.setFocus(Qt.FocusReason.PopupFocusReason)
        except RuntimeError:  # the invoking control was deleted meanwhile
            pass
    dialog.finished.connect(restore)
    return target


def is_text_input(widget):
    if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
        return not (isinstance(widget, (QTextEdit, QPlainTextEdit)) and widget.isReadOnly())
    return isinstance(widget, QComboBox) and widget.isEditable()


class ShortcutPriorityFilter(QObject):
    """Application event filter for ``window``: global chords (e.g. Cmd+K) win over text-widget key
    bindings, and a bare '?' calls ``question_handler`` unless a text input has focus."""

    def __init__(self, registry, window, question_handler=None):
        super().__init__(window)
        self.window = window
        self.sequences = registry.global_sequences()
        self.question_handler = question_handler

    def _owns_focus(self):
        if QApplication.activeModalWidget() is not None:
            return None
        focused = QApplication.focusWidget()
        return focused if focused is not None and focused.window() is self.window else None

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.ShortcutOverride and (focused := self._owns_focus()) is not None:
            seq = QKeySequence(event.keyCombination()).toString(QKeySequence.SequenceFormat.PortableText)
            if seq in self.sequences:
                event.ignore()  # not overridden: the shortcut map handles it
                return True
        elif (kind == QEvent.Type.KeyPress and self.question_handler is not None and event.text() == '?'
              and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                                           | Qt.KeyboardModifier.MetaModifier)):
            focused = self._owns_focus()
            if focused is not None and not is_text_input(focused):
                self.question_handler()
                return True
        return False


def set_tab_chain(widgets):
    """Explicit Tab / Shift+Tab order through ``widgets`` (None entries are skipped)."""
    widgets = [w for w in widgets if w is not None]
    for first, second in zip(widgets, widgets[1:]):
        QWidget.setTabOrder(first, second)


class ListWrapFilter(QObject):
    """Up on the first row selects the last one and Down on the last row selects the first."""

    def eventFilter(self, view, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) \
                and not event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            model, rows = view.model(), view.model().rowCount(view.rootIndex()) if view.model() else 0
            row = view.currentIndex().row()
            if rows and ((event.key() == Qt.Key.Key_Down and row == rows - 1) or
                         (event.key() == Qt.Key.Key_Up and row == 0)):
                view.setCurrentIndex(model.index(0 if row == rows - 1 else rows - 1, 0, view.rootIndex()))
                return True
        return False


def install_list_wrap(view):
    view._wrap_filter = ListWrapFilter(view)
    view.installEventFilter(view._wrap_filter)
    return view


class ShortcutCheatSheetDialog(QDialog):
    """Read-only overlay listing every registered action and its shortcut, grouped by category."""

    def __init__(self, registry, tokens, parent=None, platform=None):
        super().__init__(parent)
        self.setWindowTitle('Keyboard shortcuts')
        self.setModal(True)
        self.setStyleSheet(stylesheet(tokens) + f"""
            QTreeWidget#shortcuts {{ font-size: 12px; }}
            QLabel#title {{ color: {tokens.text_strong}; font-size: 14px; font-weight: bold; }}""")
        self.resize(520, 560)
        layout = QVBoxLayout(self)
        title = QLabel('Keyboard shortcuts', objectName='title')
        layout.addWidget(title)
        self.tree = QTreeWidget(objectName='shortcuts')
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(['Action', 'Shortcut'])
        self.tree.setRootIsDecorated(False)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setAccessibleName('Keyboard shortcuts by category')
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        badge_bg, badge_fg = QColor(tokens.badge), QColor(tokens.badge_text)
        for category, specs in registry.list_by_category().items():
            group = QTreeWidgetItem([category])
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            group.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(group)
            group.setFirstColumnSpanned(True)
            for spec in specs:
                row = QTreeWidgetItem([spec.title, registry.hint(spec.id, platform) or '—'])
                row.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                row.setToolTip(0, spec.description or spec.title)
                row.setBackground(1, badge_bg)
                row.setForeground(1, badge_fg)
                group.addChild(row)
            group.setExpanded(True)
        layout.addWidget(self.tree, 1)
        row = QHBoxLayout()
        hint = QLabel('Esc to close')
        hint.setProperty('muted', True)
        row.addWidget(hint)
        row.addStretch()
        self.close_btn = QPushButton('Close')
        self.close_btn.clicked.connect(self.reject)
        row.addWidget(self.close_btn)
        layout.addLayout(row)
        set_tab_chain([self.tree, self.close_btn])
        first = self.tree.topLevelItem(0)
        if first is not None and first.childCount():
            self.tree.setCurrentItem(first.child(0))

    def groups(self):
        """{category: [(title, hint), ...]} as displayed."""
        out = {}
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            out[group.text(0)] = [(group.child(j).text(0), group.child(j).text(1)) for j in range(group.childCount())]
        return out

    def showEvent(self, event):
        super().showEvent(event)
        self.activateWindow()
        self.tree.setFocus(Qt.FocusReason.PopupFocusReason)

    def exec_and_restore_focus(self):
        restore_focus_on_close(self, QApplication.focusWidget())
        return self.exec()


# Primary workflows from the brief; coverage is measured against this list.
PRIMARY_ACTIONS = (
    'model.next', 'model.prev', 'chat.new_session', 'chat.clear_session', 'chat.focus_prompt',
    'chat.copy_last_response', 'chat.stop', 'tools.toggle_panel', 'tools.toggle_enabled',
    'tools.run_active', 'tools.clear_session', 'panels.toggle_sidebar', 'settings.open',
    'params.focus', 'params.toggle_tool_details', 'theme.toggle', 'service.toggle', 'app.command_palette',
)


def register_primary_actions(registry, gadget, theme_controller=None):
    """Register the primary command catalog, wiring each handler to ``gadget`` / the theme controller."""
    theme = theme_controller if theme_controller is not None else gadget.theme
    g = gadget
    specs = [
        CommandSpec('help.shortcuts', 'Keyboard shortcuts', 'General', 'Cmd+/', hint_extra='?',
                    handler=lambda: g.show_shortcut_cheat_sheet(), description='Show this cheat sheet'),
        CommandSpec('app.command_palette', 'Command palette', 'General', 'Cmd+K',
                    handler=lambda: g.open_command_palette(), description='Search and run any command'),
        # Models
        CommandSpec('model.next', 'Next model', 'Models', 'Cmd+M', handler=lambda: g.cycle_model(1),
                    description='Switch to the next model profile'),
        CommandSpec('model.prev', 'Previous model', 'Models', 'Cmd+Shift+M', handler=lambda: g.cycle_model(-1),
                    description='Switch to the previous model profile'),
        CommandSpec('service.toggle', 'Start / stop model server', 'Models', 'Cmd+Shift+S',
                    handler=lambda: g.toggle_service()),
        # Chat
        CommandSpec('chat.new_session', 'New chat session', 'Chat', 'Cmd+N', handler=lambda: g.new_session()),
        CommandSpec('chat.clear_session', 'Clear chat session', 'Chat', 'Cmd+Shift+X', CONTEXTUAL,
                    handler=lambda: g.clear_chat(), scope='chat',
                    description='Clear the conversation, prompt and attachments'),
        CommandSpec('chat.focus_prompt', 'Focus prompt input', 'Chat', 'Cmd+I', handler=lambda: g.focus_prompt()),
        CommandSpec('chat.copy_last_response', 'Copy last response', 'Chat', 'Cmd+Shift+C', CONTEXTUAL,
                    handler=lambda: g.copy_last_response(), scope='chat'),
        CommandSpec('chat.stop', 'Stop generation', 'Chat', 'Cmd+.', handler=lambda: g.stop_turn()),
        CommandSpec('chat.attach_files', 'Attach files…', 'Chat', 'Cmd+O', handler=lambda: g._pick_files()),
        CommandSpec('chat.add_folder', 'Add folder context…', 'Chat', 'Cmd+Shift+O', handler=lambda: g._pick_folder()),
        # Tools
        CommandSpec('tools.toggle_panel', 'Toggle tool panel', 'Tools', 'Cmd+Shift+T', alt_chords=('Cmd+Alt+T',),
                    handler=lambda: g.toggle_tool_panel()),
        CommandSpec('tools.toggle_enabled', 'Start / stop tool session', 'Tools', 'Cmd+Shift+E',
                    handler=lambda: g.toggle_tools_enabled(), description='Enable or disable tool calling'),
        CommandSpec('tools.run_active', 'Run active tool (connect / refresh MCP)', 'Tools', 'Cmd+Return', CONTEXTUAL,
                    handler=lambda: g._on_mcp_refresh(), scope='tools'),
        CommandSpec('tools.clear_session', 'Clear tool session (disconnect MCP)', 'Tools', 'Cmd+Shift+K', CONTEXTUAL,
                    handler=lambda: g._on_mcp_disconnect(), scope='tools'),
        # View & navigation
        CommandSpec('panels.toggle_sidebar', 'Toggle sidebar panel', 'View', 'Cmd+B',
                    handler=lambda: g.toggle_sidebar(), description='Show or hide the profile / endpoint panel'),
        CommandSpec('nav.chat', 'Go to Chat', 'Navigation', 'Cmd+1', handler=lambda: g.show_tab(0)),
        CommandSpec('nav.history', 'Go to History', 'Navigation', 'Cmd+2', handler=lambda: g.show_tab(1)),
        CommandSpec('settings.open', 'Open settings', 'Navigation', 'Cmd+,', alt_chords=('Cmd+3',),
                    handler=lambda: g.show_tab(2)),
        # Parameters
        CommandSpec('params.focus', 'Focus parameter controls', 'Parameters', 'Cmd+Shift+P',
                    handler=lambda: g.focus_parameters()),
        CommandSpec('params.toggle_tool_details', 'Toggle tool details', 'Parameters', 'Cmd+Shift+H',
                    handler=lambda: g.show_tools_cb.toggle()),
        # Theme
        CommandSpec('theme.toggle', 'Toggle dark / light theme', 'Theme', 'Cmd+Shift+D',
                    handler=lambda: theme.toggle()),
    ]
    for spec in specs:
        registry.register(spec)
    return registry


def coverage(registry, actions=PRIMARY_ACTIONS):
    """Fraction of ``actions`` registered with at least one chord and a handler."""
    covered = [a for a in actions if (s := registry.get(a)) is not None and s.chords and s.handler is not None]
    return len(covered) / len(actions)
