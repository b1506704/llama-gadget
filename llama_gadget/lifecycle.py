"""Shared lifecycle state machine, token-rate telemetry and cooperative cancellation.

One ``SurfaceLifecycleController`` drives every surface that shows an operation
(chat transcript, tool inspector) so they enforce identical transitions:

    Idle -> Loading -> Streaming -> Idle
    Loading|Streaming -> Idle   (completion, or cancel_active() via Esc / Stop)
    Loading|Streaming -> Error  (failure; the error banner drives Error -> Loading|Idle)
"""
import threading
import time
from enum import Enum
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal


class SurfaceState(Enum):
    IDLE = 'Idle'
    LOADING = 'Loading'
    STREAMING = 'Streaming'
    ERROR = 'Error'


ACTIVE_STATES = frozenset({SurfaceState.LOADING, SurfaceState.STREAMING})
TRANSITIONS = {
    SurfaceState.IDLE: frozenset({SurfaceState.LOADING}),
    SurfaceState.LOADING: frozenset({SurfaceState.STREAMING, SurfaceState.IDLE, SurfaceState.ERROR}),
    SurfaceState.STREAMING: frozenset({SurfaceState.IDLE, SurfaceState.ERROR}),
    SurfaceState.ERROR: frozenset({SurfaceState.LOADING, SurfaceState.IDLE}),
}


class InvalidTransition(ValueError):
    pass


class CancellationToken:
    """Thread-safe cancel flag. ``cancel()`` runs registered detachment callbacks
    synchronously on the calling thread, exactly once, so the GUI can stop listening
    before the background worker has wound down."""

    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks = []

    def cancel(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            callback()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def register_on_cancel(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
        callback()  # already cancelled: detach immediately


class StreamTelemetry:
    """Live tokens/s since the first streamed token, from monotonic timestamps."""
    MIN_INTERVAL = 1e-3  # sub-millisecond spans give meaningless rates; keep the previous one

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.tokens = 0
        self.tokens_per_sec = 0.0
        self._first = None
        self._first_count = 0

    def record_tokens(self, count: int, now_monotonic: Optional[float] = None) -> float:
        now = time.perf_counter() if now_monotonic is None else now_monotonic
        if count <= 0:
            return self.tokens_per_sec
        self.tokens += count
        if self._first is None:
            # The first batch only starts the clock; rate covers tokens after it.
            self._first, self._first_count = now, count
            return self.tokens_per_sec
        elapsed = now - self._first
        if elapsed >= self.MIN_INTERVAL:
            self.tokens_per_sec = (self.tokens - self._first_count) / elapsed
        return self.tokens_per_sec


class SurfaceLifecycleController(QObject):
    """GUI-thread state machine for one operation at a time. ``state_changed`` carries the
    new ``SurfaceState``; ``last_cancel_ms`` is the time cancel_active() took to reach Idle."""
    state_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = SurfaceState.IDLE
        self._token = None
        self.telemetry = StreamTelemetry()
        self.last_cancel_ms = 0.0

    @property
    def state(self) -> SurfaceState:
        return self._state

    @property
    def token(self) -> Optional[CancellationToken]:
        return self._token

    def is_active(self) -> bool:
        return self._state in ACTIVE_STATES

    def transition_to(self, target: SurfaceState) -> SurfaceState:
        if target is self._state:
            return self._state  # no-op transitions are harmless
        if target not in TRANSITIONS[self._state]:
            raise InvalidTransition(f'{self._state.value} -> {target.value} is not a valid transition')
        self._state = target
        if target not in ACTIVE_STATES:
            self._token = None
        self.state_changed.emit(target)
        return target

    def start_operation(self) -> CancellationToken:
        """Begin a new operation (Idle/Error -> Loading) and return its cancel token."""
        if self.is_active():
            self.cancel_active()
        self.telemetry.reset()
        self._token = CancellationToken()
        self.transition_to(SurfaceState.LOADING)
        return self._token

    def mark_streaming(self) -> None:
        if self._state is SurfaceState.LOADING:
            self.transition_to(SurfaceState.STREAMING)

    def record_tokens(self, count: int, now_monotonic: Optional[float] = None) -> float:
        return self.telemetry.record_tokens(count, now_monotonic)

    def complete(self) -> None:
        """Operation finished normally: back to Idle (no-op unless active)."""
        if self.is_active():
            self.transition_to(SurfaceState.IDLE)

    def fail(self) -> None:
        """Operation failed: Loading|Streaming -> Error (no-op otherwise)."""
        if self.is_active():
            self.transition_to(SurfaceState.ERROR)

    def dismiss_error(self) -> None:
        if self._state is SurfaceState.ERROR:
            self.transition_to(SurfaceState.IDLE)

    def cancel_active(self) -> float:
        """Cancel the active operation and return to Idle; returns elapsed ms (0 when idle)."""
        if not self.is_active():
            return 0.0
        started = time.perf_counter()
        token = self._token
        if token is not None:
            token.cancel()  # detachment callbacks run synchronously here
        if self.is_active():  # a callback may already have moved the state on
            self.transition_to(SurfaceState.IDLE)
        self.last_cancel_ms = (time.perf_counter() - started) * 1000
        return self.last_cancel_ms
