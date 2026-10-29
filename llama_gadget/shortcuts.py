"""Central action registry: every keyboard-reachable command, its chords, and its handler.

Chords are written canonically with ``Cmd`` as the primary modifier ("Cmd+Shift+K").
``Cmd`` always resolves to ``Qt.Modifier.CTRL``: Qt itself maps that modifier to the
Command key on macOS and to Control on Windows/Linux, so one binding serves every
platform. Only the human-readable hint differs per platform.
"""
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut

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


# Primary workflows from the brief; coverage is measured against this list.
PRIMARY_ACTIONS = (
    'model.next', 'model.prev', 'chat.new_session', 'chat.clear_session', 'chat.focus_prompt',
    'chat.copy_last_response', 'chat.stop', 'tools.toggle_panel', 'tools.toggle_enabled',
    'tools.run_active', 'tools.clear_session', 'panels.toggle_sidebar', 'settings.open',
    'params.focus', 'params.toggle_tool_details', 'theme.toggle', 'service.toggle',
)


def register_primary_actions(registry, gadget, theme_controller=None):
    """Register the primary command catalog, wiring each handler to ``gadget`` / the theme controller."""
    theme = theme_controller if theme_controller is not None else gadget.theme
    g = gadget
    specs = [
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
