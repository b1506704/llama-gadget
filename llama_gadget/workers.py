"""Shared background QThread dispatch so blocking work never runs on the GUI thread."""
import inspect
from collections.abc import Iterator
from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal


class BackgroundWorkerSignals(QObject):
    # Emitted from the worker thread; Qt queues delivery onto the receiver's (GUI) thread.
    token_emitted = pyqtSignal(str)
    progress_emitted = pyqtSignal(dict)
    job_completed = pyqtSignal(object)
    job_failed = pyqtSignal(object)


class BackgroundJob(QThread):
    """Runs one callable. Generator/iterator results stream: ``str`` items emit
    ``token_emitted``, ``dict`` items emit ``progress_emitted``, and the generator's
    return value becomes the ``job_completed`` payload."""

    def __init__(self, job_fn: Callable[..., Any], args=(), kwargs=None, parent=None):
        super().__init__(parent)
        self.signals = BackgroundWorkerSignals(self)
        self._job = (job_fn, tuple(args), dict(kwargs or {}))

    def run(self):
        job_fn, args, kwargs = self._job
        try:
            result = job_fn(*args, **kwargs)
            if inspect.isgenerator(result):
                result = self._drain_generator(result)
            elif isinstance(result, Iterator):
                for item in result:
                    self._emit_item(item)
                result = None
        except Exception as exc:  # noqa: BLE001 - thread boundary
            self.signals.job_failed.emit(exc)
            return
        self.signals.job_completed.emit(result)

    def _drain_generator(self, generator):
        while True:
            try:
                item = next(generator)
            except StopIteration as stop:
                return stop.value
            self._emit_item(item)

    def _emit_item(self, item):
        if isinstance(item, str):
            self.signals.token_emitted.emit(item)
        elif isinstance(item, dict):
            self.signals.progress_emitted.emit(item)
        else:
            raise TypeError(f'Background jobs may only yield str or dict, got {type(item).__name__}')


class WorkerQueue(QObject):
    """Single dispatch point for model loading, service calls, streaming and tool jobs.

    ``submit`` must be called from the GUI thread. The thread starts on the next event
    loop pass, so callers can connect ``thread.signals`` before any emission. Finished
    threads are released with ``deleteLater``."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = set()
        self._closed = False

    def submit(self, job_fn: Callable[..., Any], *args: Any, **kwargs: Any) -> QThread:
        if self._closed:
            raise RuntimeError('Worker queue is shut down')
        thread = BackgroundJob(job_fn, args, kwargs, parent=self)
        self._active.add(thread)
        thread.finished.connect(lambda: self._release(thread))
        QTimer.singleShot(0, lambda: self._start(thread))
        return thread

    def _start(self, thread):
        if thread in self._active and not self._closed:
            thread.start()

    def active(self):
        return frozenset(self._active)

    def _release(self, thread):
        if thread in self._active:
            self._active.discard(thread)
            thread.deleteLater()

    def shutdown(self, timeout_ms=2000):
        """Wait briefly for running jobs. Jobs blocked in external processes (e.g. systemctl)
        are detached rather than destroyed while running, which would abort the process."""
        self._closed = True
        for thread in list(self._active):
            if not thread.isRunning() and not thread.isFinished():
                self._active.discard(thread)  # never started: dropping it is safe
                thread.deleteLater()
            elif not thread.wait(timeout_ms):
                self._active.discard(thread)
                thread.setParent(None)
                sip.transferto(thread, None)
