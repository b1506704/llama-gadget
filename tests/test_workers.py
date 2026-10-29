"""Headless checks for background workers and 60Hz token coalescing (QT_QPA_PLATFORM=offscreen)."""
import statistics
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEventLoop, QSettings, Qt, QThread, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from llama_gadget.app import Gadget
from llama_gadget.chat_ui import TokenFrameCoalescer
from llama_gadget.workers import WorkerQueue
from tests.support import StreamingServer, chunk

APP = QApplication.instance() or QApplication([])
FRAME_MS = 16


def spin(predicate, timeout=5):
    """Run a real event loop (no sleeps) until predicate() holds."""
    deadline = time.monotonic() + timeout
    loop = QEventLoop()
    check = QTimer(interval=1, timeout=lambda: loop.quit() if predicate() or time.monotonic() > deadline else None)
    check.start()
    if not predicate():
        loop.exec()
    check.stop()
    return predicate()


def on_gui_thread():
    return QThread.currentThread() is APP.thread()


class Recorder:
    def __init__(self, job):
        self.tokens, self.progress, self.completed, self.failed = [], [], [], []
        job.signals.token_emitted.connect(self.tokens.append)
        job.signals.progress_emitted.connect(self.progress.append)
        job.signals.job_completed.connect(self.completed.append)
        job.signals.job_failed.connect(self.failed.append)

    def done(self):
        return bool(self.completed or self.failed)


class WorkerQueueTests(unittest.TestCase):
    def setUp(self):
        self.queue = WorkerQueue()
        self.addCleanup(self.queue.shutdown)

    def test_model_load_runs_off_gui_thread_and_releases_thread(self):
        def load_model(path, *, layers):
            time.sleep(.02)  # stands in for a blocking systemctl / model init call
            return {'path': path, 'layers': layers, 'gui': on_gui_thread()}
        job = self.queue.submit(load_model, '/models/q.gguf', layers=99)
        self.assertIsInstance(job, QThread)
        rec = Recorder(job)
        self.assertTrue(spin(rec.done))
        self.assertEqual(rec.completed, [{'path': '/models/q.gguf', 'layers': 99, 'gui': False}])
        self.assertTrue(spin(lambda: not self.queue.active()))

    def test_streaming_generator_emits_tokens_progress_and_result(self):
        threads = set()
        def stream():
            threads.add(on_gui_thread())
            yield {'loaded': 1.0}
            for word in ('Hello', ' ', 'world'):
                yield word
            return 'stop'
        rec = Recorder(self.queue.submit(stream))
        self.assertTrue(spin(rec.done))
        self.assertEqual(threads, {False})
        self.assertEqual(rec.tokens, ['Hello', ' ', 'world'])
        self.assertEqual(rec.progress, [{'loaded': 1.0}])
        self.assertEqual(rec.completed, ['stop'])
        self.assertEqual(rec.failed, [])

    def test_plain_iterator_streams_tokens(self):
        rec = Recorder(self.queue.submit(lambda: iter(['a', 'b'])))
        self.assertTrue(spin(rec.done))
        self.assertEqual(rec.tokens, ['a', 'b'])
        self.assertEqual(rec.completed, [None])

    def test_mcp_tool_call_off_gui_thread_and_failure_reported(self):
        def call_tool(server, tool, arguments):
            if on_gui_thread():
                raise AssertionError('MCP tool executed on the GUI thread')
            if tool == 'broken':
                raise RuntimeError('server went away')
            return {'ok': True, 'server': server, 'echo': arguments}
        ok = Recorder(self.queue.submit(call_tool, 'files', 'read', {'path': 'a'}))
        bad = Recorder(self.queue.submit(call_tool, 'files', 'broken', {}))
        self.assertTrue(spin(lambda: ok.done() and bad.done()))
        self.assertEqual(ok.completed, [{'ok': True, 'server': 'files', 'echo': {'path': 'a'}}])
        self.assertEqual(bad.completed, [])
        self.assertIsInstance(bad.failed[0], RuntimeError)
        self.assertEqual(str(bad.failed[0]), 'server went away')
        self.assertTrue(spin(lambda: not self.queue.active()))

    def test_invalid_yield_fails_job(self):
        def stream():
            yield 42
        rec = Recorder(self.queue.submit(stream))
        self.assertTrue(spin(rec.done))
        self.assertIsInstance(rec.failed[0], TypeError)

    def test_shutdown_drops_unstarted_jobs_and_rejects_new_ones(self):
        ran = threading.Event()
        self.queue.submit(ran.set)
        self.queue.shutdown()  # before the event loop starts it
        APP.processEvents()
        self.assertFalse(ran.is_set())
        self.assertFalse(self.queue.active())
        with self.assertRaises(RuntimeError):
            self.queue.submit(ran.set)

    def test_gui_event_loop_keeps_ticking_while_job_blocks(self):
        release = threading.Event()
        rec = Recorder(self.queue.submit(lambda: release.wait(5)))
        ticks = []
        timer = QTimer(interval=FRAME_MS, timeout=lambda: ticks.append(time.perf_counter()))
        timer.start()
        self.assertTrue(spin(lambda: len(ticks) >= 10))
        timer.stop()
        self.assertFalse(rec.done())  # the job is still blocked while the GUI kept running
        release.set()
        self.assertTrue(spin(rec.done))


class CoalescerTests(unittest.TestCase):
    def test_burst_within_one_frame_flushes_once(self):
        coalescer = TokenFrameCoalescer()
        flushes = []
        coalescer.flushed.connect(flushes.append)
        chunks = [f'tok{i} ' for i in range(150)]
        for c in chunks:  # synchronous: all arrive inside a single 16ms window
            coalescer.enqueue(c)
        self.assertEqual(flushes, [])
        self.assertTrue(spin(lambda: flushes))
        self.assertEqual(flushes, [''.join(chunks)])
        self.assertFalse(coalescer.timer.isActive())  # idle: zero timer overhead

    def test_stop_drains_remaining_chunks_immediately(self):
        coalescer = TokenFrameCoalescer()
        flushes = []
        coalescer.flushed.connect(flushes.append)
        coalescer.enqueue('tail')
        coalescer.enqueue('')
        coalescer.stop()
        self.assertEqual(flushes, ['tail'])
        self.assertFalse(coalescer.timer.isActive())
        self.assertEqual(coalescer.flush(), '')
        self.assertEqual(flushes, ['tail'])

    def test_background_burst_flushes_at_most_once_per_frame(self):
        queue = WorkerQueue()
        self.addCleanup(queue.shutdown)
        coalescer = TokenFrameCoalescer()
        flushes = []
        coalescer.flushed.connect(lambda text: flushes.append((time.perf_counter(), text)))
        chunks = [f'{i},' for i in range(400)]
        def stream():
            for c in chunks:
                yield c
                time.sleep(.0002)  # < 1ms apart
        job = queue.submit(stream)
        job.signals.token_emitted.connect(coalescer.enqueue)
        rec = Recorder(job)
        self.assertTrue(spin(rec.done))
        coalescer.stop()
        self.assertEqual(''.join(text for _, text in flushes), ''.join(chunks))
        self.assertLess(len(flushes), len(chunks) // 4)
        timed = [t for t, _ in flushes[:-1]]  # the final stop() drain is not timer driven
        gaps = [b - a for a, b in zip(timed, timed[1:])]
        if gaps:
            self.assertGreaterEqual(min(gaps) * 1000, FRAME_MS - 1)


class GadgetFrameTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
        settings.setValue('db_path', str(root / 'chat.db'))
        settings.setValue('profile', 'agent-gpu0')
        with patch('llama_gadget.app.QSettings', return_value=settings), patch.object(Gadget, '_poll'):
            self.app = Gadget()
        self.app.timer.stop()
        self.app.state = 'online'
        self.addCleanup(self.cleanup_app)

    def cleanup_app(self):
        self.app._shutdown_workers()
        self.app.tray.hide()
        self.app.clipboard.dataChanged.disconnect(self.app._on_clipboard)
        self.app.db.conn.close()
        self.app.hide()
        self.app.deleteLater()
        APP.processEvents()

    def start_live_turn(self):
        """Put the window in an active-streaming state without a network turn."""
        self.app.worker = object()
        self.app.reply = ''
        self.addCleanup(setattr, self.app, 'worker', None)

    def count_frame_work(self):
        appends, scrolls = [], []
        original_append = self.app.view.append_live
        bar = self.app.view.verticalScrollBar()
        original_set = bar.setValue
        def append(text):
            appends.append(text)
            return original_append(text)
        def set_value(value):
            scrolls.append(value)
            original_set(value)
        patch.object(self.app.view, 'append_live', append).start()
        patch.object(bar, 'setValue', set_value).start()
        self.addCleanup(patch.stopall)
        return appends, scrolls, bar

    def test_burst_produces_one_append_and_one_scroll_per_tick(self):
        self.start_live_turn()
        self.app.msgs = [dict(id=i, role='user', content='line\n' * 20, attachments=[], ts='') for i in range(10)]
        self.app._on_token('first ')
        self.assertTrue(spin(lambda: self.app.reply == 'first '))  # first frame creates the live card
        appends, scrolls, bar = self.count_frame_work()
        burst = [f'w{i} ' for i in range(120)]
        for t in burst:
            self.app._on_token(t)
        self.assertTrue(spin(lambda: appends))
        self.assertEqual(appends, [''.join(burst)])
        spin(lambda: False, timeout=.05)  # let the frame's layout + scroll pass run
        self.assertEqual(appends, [''.join(burst)])
        self.assertLessEqual(len(scrolls), 1)
        self.assertEqual(self.app.reply, 'first ' + ''.join(burst))
        self.assertIn(''.join(burst), self.app.view.cards['live'].body.toPlainText())

    def test_scrolling_up_pauses_auto_scroll(self):
        self.app.resize(420, 500)
        self.app.show()
        self.start_live_turn()
        bar = self.app.view.verticalScrollBar()
        self.app._on_token('first\n')
        self.assertTrue(spin(lambda: 'live' in self.app.view.cards))  # first frame creates the live card
        self.app._on_token('start\n' * 200)
        self.assertTrue(spin(lambda: bar.maximum() > 60 and bar.value() == bar.maximum()))
        bar.setValue(0)  # user scrolls up
        self.app._on_token('more\n' * 50)
        grown = bar.maximum()
        self.assertTrue(spin(lambda: bar.maximum() > grown))
        self.assertEqual(bar.value(), 0)
        bar.setValue(bar.maximum())  # back at the bottom: following resumes
        self.app._on_token('again\n' * 50)
        grown = bar.maximum()
        self.assertTrue(spin(lambda: bar.maximum() > grown and bar.value() == bar.maximum()))

    def test_flush_work_per_frame_stays_within_budget(self):
        self.start_live_turn()
        self.app._on_token('warm ')
        self.app.token_coalescer.flush()
        cpu_ms, wall_ms = [], []
        for frame in range(40):
            for i in range(150):
                self.app._on_token(f'f{frame}t{i} ' + ('\n' if i % 15 == 14 else ''))  # prose has line breaks
            wall, cpu = time.perf_counter(), time.thread_time()
            self.app.token_coalescer.flush()
            wall_ms.append((time.perf_counter() - wall) * 1000)
            cpu_ms.append((time.thread_time() - cpu) * 1000)
        # Thread CPU time excludes preemption on a loaded box; wall time is checked on the median.
        self.assertLessEqual(max(cpu_ms), FRAME_MS, cpu_ms)
        self.assertLessEqual(statistics.median(wall_ms), FRAME_MS, wall_ms)

    def test_streaming_turn_coalesces_tokens_end_to_end(self):
        tokens = [f'tok{i} ' for i in range(200)]
        server = StreamingServer([[*(chunk({'content': t}) for t in tokens), chunk(finish='stop'), '[DONE]']])
        self.addCleanup(server.close)
        patch.object(self.app, 'current_base_url', return_value=server.url).start()
        self.addCleanup(patch.stopall)
        frames = []
        self.app.token_coalescer.flushed.connect(frames.append)
        self.app.input.setPlainText('Go')
        self.app.send()
        self.assertTrue(spin(lambda: self.app.worker is None, timeout=10))
        self.assertEqual(''.join(frames), ''.join(tokens))
        self.assertLess(len(frames), len(tokens))
        self.assertEqual(self.app.msgs[-1]['content'], ''.join(tokens))

    def test_interactions_stay_responsive_during_background_work(self):
        self.app.show()
        self.start_live_turn()
        stop = threading.Event()
        def load_model():
            stop.wait(5)
            return 'loaded'
        def stream():
            i = 0
            while not stop.is_set():
                yield f'x{i} '
                i += 1
                time.sleep(.001)
        def mcp_tool():
            stop.wait(5)
            return {'ok': True}
        jobs = [self.app.jobs.submit(load_model), self.app.jobs.submit(stream), self.app.jobs.submit(mcp_tool)]
        jobs[1].signals.token_emitted.connect(self.app._on_token)
        bar = self.app.view.verticalScrollBar()
        sizes = [(400, 600), (460, 640)]
        interactions = [
            lambda i: bar.setValue(bar.maximum() // 2),                       # scrolling
            lambda i: self.app.resize(*sizes[i % 2]),                          # resizing
            lambda i: QTest.keyClick(self.app.input, Qt.Key.Key_A),            # typing
            lambda i: self.app.tabs.setCurrentIndex(1 if i % 2 else 0),        # navigation
        ]
        latencies, cpu_ms = [], []
        def interact(i, posted):
            latencies.append((time.perf_counter() - posted) * 1000)
            cpu = time.thread_time()
            interactions[i % len(interactions)](i)
            cpu_ms.append((time.thread_time() - cpu) * 1000)
        def post(i):
            posted = time.perf_counter()
            QTimer.singleShot(0, Qt.TimerType.PreciseTimer, lambda: interact(i, posted))
        for i in range(40):
            QTimer.singleShot(5 * i, Qt.TimerType.PreciseTimer, lambda i=i: post(i))
        try:
            self.assertTrue(spin(lambda: len(latencies) == 40, timeout=10))
        finally:
            stop.set()
        self.assertTrue(spin(lambda: not self.app.jobs.active()))
        self.app.tabs.setCurrentIndex(0)
        self.assertTrue(self.app.input.toPlainText().startswith('a'))
        self.assertTrue(self.app.reply.startswith('x0 '))
        self.assertLessEqual(max(cpu_ms), FRAME_MS, cpu_ms)
        self.assertLessEqual(statistics.median(latencies), FRAME_MS, latencies)


if __name__ == '__main__':
    unittest.main()
