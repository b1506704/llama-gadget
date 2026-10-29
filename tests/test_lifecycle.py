"""Headless lifecycle, skeleton, telemetry and single-frame cancellation checks (QT_QPA_PLATFORM=offscreen)."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEventLoop, QSettings, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

from llama_gadget.app import Gadget
from llama_gadget.chat import ChatWorker
from llama_gadget.chat_ui import LifecycleBadge, LifecycleStatusBar
from llama_gadget.lifecycle import (CancellationToken, InvalidTransition, StreamTelemetry,
                                    SurfaceLifecycleController, SurfaceState)
from llama_gadget.theme import TOKENS
from llama_gadget.tool_ui import ToolInspectorDialog

APP = QApplication.instance() or QApplication([])
FRAME_MS = 16.0
S = SurfaceState


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


def timed(trigger):
    """Thread CPU ms spent in the synchronous trigger (excludes preemption on a loaded box)."""
    cpu = time.thread_time()
    trigger()
    return (time.thread_time() - cpu) * 1000


class SlowWorker(ChatWorker):
    """Streams ``pre_tokens`` 20ms apart, then idles until cancelled; winding down takes
    250ms and still emits a trailing token, like a slow network/C++ call closing."""
    pre_tokens = ('Hello ', 'partial ', 'reply ')
    fail_with = None
    release = None  # threading.Event: finish normally once set

    def run(self):
        for text in self.pre_tokens:
            if self.cancel_event.is_set():
                break
            self.token.emit(text)
            time.sleep(.02)
        if self.fail_with:
            self.failed.emit(self.fail_with)
            return
        if self.release is not None and self.release.wait(5) and not self.cancel_event.is_set():
            self.message_completed.emit(dict(role='assistant', content=''.join(self.pre_tokens)))
            self.finished_ok.emit()
            return
        self.cancel_event.wait(10)
        time.sleep(.25)
        self.token.emit('TRAILING')
        self.cancelled.emit()


class ControllerTests(unittest.TestCase):
    def test_full_state_diagram(self):
        c = SurfaceLifecycleController()
        seen = []
        c.state_changed.connect(seen.append)
        self.assertIs(c.state, S.IDLE)
        token = c.start_operation()
        self.assertIs(c.token, token)
        c.mark_streaming()
        c.complete()
        c.start_operation()
        c.fail()                         # Loading -> Error
        c.start_operation()              # Error -> Loading (retry)
        c.mark_streaming()
        c.fail()                         # Streaming -> Error
        c.dismiss_error()                # Error -> Idle
        self.assertEqual(seen, [S.LOADING, S.STREAMING, S.IDLE, S.LOADING, S.ERROR,
                                S.LOADING, S.STREAMING, S.ERROR, S.IDLE])
        self.assertIsNone(c.token)

    def test_invalid_and_noop_transitions(self):
        c = SurfaceLifecycleController()
        self.assertIs(c.transition_to(S.IDLE), S.IDLE)  # no-op is harmless
        for bad in (S.STREAMING, S.ERROR):
            with self.assertRaises(InvalidTransition):
                c.transition_to(bad)
        c.start_operation()
        c.mark_streaming()
        with self.assertRaises(InvalidTransition):
            c.transition_to(S.LOADING)
        self.assertIs(c.state, S.STREAMING)
        c.complete()
        c.complete(); c.fail(); c.mark_streaming()  # safe no-ops while Idle
        self.assertIs(c.state, S.IDLE)
        self.assertEqual(c.cancel_active(), 0.0)

    def test_cancel_from_loading_and_streaming(self):
        c = SurfaceLifecycleController()
        for streaming in (False, True):
            token = c.start_operation()
            detached = []
            token.register_on_cancel(lambda: detached.append(c.state))
            if streaming:
                c.mark_streaming()
            elapsed = c.cancel_active()
            self.assertIs(c.state, S.IDLE)
            self.assertTrue(token.is_cancelled())
            self.assertEqual(detached, [S.STREAMING if streaming else S.LOADING])  # detached before Idle
            self.assertLessEqual(elapsed, FRAME_MS)
            self.assertEqual(c.last_cancel_ms, elapsed)

    def test_new_operation_cancels_previous_and_resets_telemetry(self):
        c = SurfaceLifecycleController()
        first = c.start_operation()
        c.record_tokens(5, 1.0)
        c.record_tokens(5, 2.0)
        second = c.start_operation()
        self.assertTrue(first.is_cancelled())
        self.assertFalse(second.is_cancelled())
        self.assertEqual((c.telemetry.tokens, c.telemetry.tokens_per_sec), (0, 0.0))


class CancellationTokenTests(unittest.TestCase):
    def test_callbacks_run_synchronously_once_on_calling_thread(self):
        token = CancellationToken()
        calls = []
        token.register_on_cancel(lambda: calls.append(threading.current_thread()))
        self.assertFalse(token.is_cancelled())
        cpu_ms = timed(token.cancel)
        self.assertTrue(token.is_cancelled())
        self.assertEqual(calls, [threading.current_thread()])
        self.assertLessEqual(cpu_ms, FRAME_MS)
        token.cancel()
        self.assertEqual(len(calls), 1)
        token.register_on_cancel(lambda: calls.append('late'))  # already cancelled: runs now
        self.assertEqual(calls[-1], 'late')

    def test_concurrent_cancel_is_thread_safe(self):
        for _ in range(20):
            token = CancellationToken()
            calls = []
            token.register_on_cancel(lambda: calls.append(1))
            barrier = threading.Barrier(8)
            def hammer():
                barrier.wait()
                token.cancel()
            threads = [threading.Thread(target=hammer) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertTrue(token.is_cancelled())
            self.assertEqual(calls, [1])


class TelemetryTests(unittest.TestCase):
    def test_rate_from_monotonic_timestamps(self):
        t = StreamTelemetry()
        self.assertEqual(t.record_tokens(1, 10.0), 0.0)  # first token only starts the clock
        t.record_tokens(10, 10.5)
        self.assertAlmostEqual(t.record_tokens(10, 11.0), 20.0)
        self.assertEqual(t.tokens, 21)
        self.assertAlmostEqual(t.tokens_per_sec, 20.0)

    def test_sub_millisecond_intervals_never_divide_by_zero(self):
        t = StreamTelemetry()
        t.record_tokens(1, 5.0)
        self.assertEqual(t.record_tokens(3, 5.0), 0.0)
        self.assertEqual(t.record_tokens(3, 5.0002), 0.0)
        rate = t.record_tokens(4, 5.5)
        self.assertAlmostEqual(rate, 20.0)
        self.assertEqual(t.record_tokens(0, 5.5), rate)

    def test_reset(self):
        t = StreamTelemetry()
        t.record_tokens(1, 1.0)
        t.record_tokens(9, 2.0)
        t.reset()
        self.assertEqual((t.tokens, t.tokens_per_sec), (0, 0.0))
        t.record_tokens(1, 100.0)
        self.assertAlmostEqual(t.record_tokens(2, 101.0), 2.0)


class WidgetTests(unittest.TestCase):
    def test_badge_text_and_theme_colour_per_state(self):
        badge = LifecycleBadge()
        self.addCleanup(badge.deleteLater)
        for state in S:
            badge.set_state(state)
            self.assertEqual(badge.text(), state.value)
            self.assertIn(TOKENS.state_color(state), badge.styleSheet())
        self.assertEqual(len({TOKENS.state_color(s) for s in S}), 4)

    def test_status_bar_shows_live_rate_only_while_streaming(self):
        c = SurfaceLifecycleController()
        bar = LifecycleStatusBar()
        self.addCleanup(bar.deleteLater)
        bar.bind(c)
        bar.show()
        c.start_operation()
        self.assertEqual(bar.badge.text(), 'Loading')
        self.assertFalse(bar.progress.isVisible())
        c.mark_streaming()
        c.record_tokens(1, 1.0)
        c.record_tokens(24, 2.0)
        bar.refresh()
        self.assertTrue(bar.progress.isVisible())
        self.assertEqual(bar.progress.bar.maximum(), 0)  # indeterminate = animating
        self.assertEqual(bar.progress.rate_lbl.text(), '24.0 tok/s · 25 tok')
        c.cancel_active()
        self.assertEqual(bar.badge.text(), 'Idle')
        self.assertFalse(bar.progress.isVisible())


class ToolSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.dlg = ToolInspectorDialog(workspace='/tmp/ws')
        self.addCleanup(self.dlg.deleteLater)
        self.dlg.show()

    def test_provider_refresh_skeleton_and_states(self):
        dlg = self.dlg
        self.assertEqual(dlg.provider_status.badge.text(), 'Idle')
        self.assertFalse(dlg.skeleton.isVisible())
        dlg.begin_provider_refresh()
        self.assertEqual(dlg.provider_status.badge.text(), 'Loading')
        self.assertTrue(dlg.skeleton.isVisible())
        self.assertFalse(dlg.tree.isVisible())
        dlg.update_snapshot((), {'srv': {'status': 'connected'}})  # content ready
        self.assertIs(dlg.lifecycle.state, S.IDLE)
        self.assertFalse(dlg.skeleton.isVisible())
        self.assertTrue(dlg.tree.isVisible())
        dlg.begin_provider_refresh()
        dlg.fail_provider_refresh()
        dlg.update_snapshot((), {})
        self.assertEqual(dlg.provider_status.badge.text(), 'Error')
        self.assertFalse(dlg.skeleton.isVisible())
        dlg.begin_provider_refresh()  # Error -> Loading on retry
        self.assertEqual(dlg.provider_status.badge.text(), 'Loading')
        dlg.lifecycle.cancel_active()
        self.assertEqual(dlg.provider_status.badge.text(), 'Idle')

    def test_turn_states_mirror_shared_controller(self):
        turn = SurfaceLifecycleController()
        self.dlg.bind_turn(turn)
        self.assertFalse(self.dlg.stop_btn.isEnabled())
        turn.start_operation()
        self.assertEqual(self.dlg.turn_status.badge.text(), 'Loading')
        self.assertTrue(self.dlg.stop_btn.isEnabled())
        turn.mark_streaming()
        self.assertEqual(self.dlg.turn_status.badge.text(), 'Streaming')
        turn.fail()
        self.assertEqual(self.dlg.turn_status.badge.text(), 'Error')
        self.assertFalse(self.dlg.stop_btn.isEnabled())
        turn.start_operation()
        self.dlg.set_turn_activity('Running read_file…')
        turn.complete()
        self.assertEqual(self.dlg.turn_status.badge.text(), 'Idle')
        self.assertEqual(self.dlg.turn_activity.text(), '')

    def test_stop_button_cancels_within_one_frame(self):
        turn = SurfaceLifecycleController()
        self.dlg.bind_turn(turn)
        for streaming in (False, True):
            token = turn.start_operation()
            if streaming:
                turn.mark_streaming()
            cpu_ms = timed(self.dlg.stop_btn.click)
            self.assertIs(turn.state, S.IDLE)  # synchronously: no further event loop pass needed
            self.assertTrue(token.is_cancelled())
            self.assertLessEqual(cpu_ms, FRAME_MS)
            self.assertLessEqual(turn.last_cancel_ms, FRAME_MS)
            self.assertFalse(self.dlg.stop_btn.isEnabled())

    def test_escape_closes_inspector_overlay_without_stopping_turn(self):
        turn = SurfaceLifecycleController()
        self.dlg.bind_turn(turn)
        token = turn.start_operation()
        turn.mark_streaming()
        QTest.keyClick(self.dlg, Qt.Key.Key_Escape)
        self.assertFalse(self.dlg.isVisible())
        self.assertIs(turn.state, S.STREAMING)
        self.assertFalse(token.is_cancelled())


class ChatSurfaceTests(unittest.TestCase):
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
        patcher = patch('llama_gadget.app.ChatWorker', SlowWorker)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cleanup_app(self):
        self.app._shutdown_workers()
        self.app.tray.hide()
        self.app.clipboard.dataChanged.disconnect(self.app._on_clipboard)
        self.app.db.conn.close()
        self.app.hide()
        self.app.deleteLater()
        APP.processEvents()

    def send(self, pre_tokens=SlowWorker.pre_tokens, fail_with=None, release=None):
        SlowWorker.pre_tokens, SlowWorker.fail_with, SlowWorker.release = pre_tokens, fail_with, release
        self.addCleanup(setattr, SlowWorker, 'pre_tokens', ('Hello ', 'partial ', 'reply '))
        self.addCleanup(setattr, SlowWorker, 'fail_with', None)
        self.addCleanup(setattr, SlowWorker, 'release', None)
        self.app.input.setPlainText('Hi')
        self.app.send()
        return self.app.worker

    def stream_partial(self):
        worker = self.send()
        partial = ''.join(SlowWorker.pre_tokens)
        self.assertTrue(spin(lambda: self.app.reply == partial))
        return worker, partial

    def assert_cancelled_in_one_frame(self, trigger, worker, partial):
        live = self.app.view.cards['live']
        cpu_ms = timed(trigger)
        # Idle synchronously inside the trigger: no further event loop pass (frame) needed.
        self.assertIs(self.app.lifecycle.state, S.IDLE)
        self.assertLessEqual(cpu_ms, FRAME_MS)
        self.assertLessEqual(self.app.lifecycle.last_cancel_ms, FRAME_MS)
        self.assertTrue(worker.cancel_event.is_set())
        self.assertEqual(self.app.chat_status.badge.text(), 'Idle')
        self.assertFalse(self.app.stop_btn.isEnabled())
        self.assertEqual(live.body.toPlainText(), partial)  # partial left untouched
        # The worker still winds down for 250ms and emits a trailing token; it is ignored.
        self.assertTrue(worker.isRunning())
        self.assertTrue(spin(lambda: self.app.worker is None, timeout=5))
        self.assertEqual(self.app.msgs[-1]['role'], 'assistant')
        self.assertEqual(self.app.msgs[-1]['content'], partial)
        self.assertNotIn('TRAILING', self.app.view.toPlainText())
        self.assertIn(partial.strip(), self.app.view.toPlainText())
        saved = self.app.db.load_messages(self.app.session_id)
        self.assertEqual([(m['role'], m['content']) for m in saved], [('user', 'Hi'), ('assistant', partial)])
        self.assertIs(self.app.lifecycle.state, S.IDLE)

    def test_idle_loading_streaming_idle_with_skeleton_and_telemetry(self):
        self.app.show()
        self.assertEqual(self.app.chat_status.badge.text(), 'Idle')
        self.assertFalse(self.app.view.skeleton.isVisible())
        release = threading.Event()
        self.addCleanup(release.set)
        self.send(release=release)
        self.assertIs(self.app.lifecycle.state, S.LOADING)
        self.assertEqual(self.app.chat_status.badge.text(), 'Loading')
        self.assertTrue(self.app.view.skeleton.isVisible())
        self.assertNotIn('live', self.app.view.cards)  # skeleton stands in for the pending reply
        self.assertTrue(spin(lambda: self.app.reply == 'Hello partial reply '))
        self.assertIs(self.app.lifecycle.state, S.STREAMING)
        self.assertFalse(self.app.view.skeleton.isVisible())
        self.assertTrue(self.app.chat_status.progress.isVisible())
        self.assertGreater(self.app.lifecycle.telemetry.tokens_per_sec, 0)
        self.assertRegex(self.app.chat_status.progress.rate_lbl.text(), r'^\d+\.\d tok/s · 3 tok$')
        self.assertNotRegex(self.app.chat_status.progress.rate_lbl.text(), r'^0\.0 ')
        self.assertIn('tok/s (live)', self.app.stats_lbl.text())
        # Completion: Streaming -> Idle, final reply saved.
        release.set()
        self.assertTrue(spin(lambda: self.app.worker is None))
        self.assertIs(self.app.lifecycle.state, S.IDLE)
        self.assertEqual(self.app.msgs[-1]['content'], 'Hello partial reply ')
        self.assertEqual(self.app.chat_status.badge.text(), 'Idle')
        self.assertFalse(self.app.chat_status.progress.isVisible())

    def test_stop_button_cancels_streaming_within_one_frame(self):
        worker, partial = self.stream_partial()
        self.assertIs(self.app.lifecycle.state, S.STREAMING)
        self.assert_cancelled_in_one_frame(self.app.stop_btn.click, worker, partial)

    def test_escape_in_workspace_cancels_streaming_within_one_frame(self):
        self.app.show()
        self.app.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(self.app))
        self.app.input.setFocus()
        worker, partial = self.stream_partial()
        self.assert_cancelled_in_one_frame(lambda: QTest.keyClick(self.app.input, Qt.Key.Key_Escape),
                                           worker, partial)

    def test_cancel_while_loading_hides_skeleton(self):
        self.send(pre_tokens=())
        self.assertIs(self.app.lifecycle.state, S.LOADING)
        self.assertFalse(self.app.view.skeleton.isHidden())
        cpu_ms = timed(self.app.stop_btn.click)
        self.assertIs(self.app.lifecycle.state, S.IDLE)
        self.assertLessEqual(cpu_ms, FRAME_MS)
        self.assertTrue(self.app.view.skeleton.isHidden())
        self.assertTrue(spin(lambda: self.app.worker is None))
        self.assertEqual([m['role'] for m in self.app.msgs], ['user'])  # nothing streamed, nothing invented

    def test_escape_closes_modal_overlay_first_without_stopping(self):
        worker, partial = self.stream_partial()
        overlay = QDialog(self.app)
        overlay.setWindowModality(Qt.WindowModality.WindowModal)
        overlay.open()
        self.assertTrue(spin(lambda: QApplication.activeModalWidget() is overlay))
        self.app.cancel_shortcut.activated.emit()  # Esc reaching the workspace shortcut
        self.assertFalse(overlay.isVisible())
        self.assertIs(self.app.lifecycle.state, S.STREAMING)
        self.assertFalse(worker.cancel_event.is_set())
        # With the overlay gone, Esc in the workspace cancels.
        self.app.cancel_shortcut.activated.emit()
        self.assertIs(self.app.lifecycle.state, S.IDLE)
        self.assertTrue(worker.cancel_event.is_set())
        self.assertTrue(spin(lambda: self.app.worker is None))

    def test_failure_enters_error_then_retry_and_dismiss(self):
        self.send(pre_tokens=('Par',), fail_with='HTTP 500: boom')
        self.assertTrue(spin(lambda: self.app.worker is None))
        self.assertIs(self.app.lifecycle.state, S.ERROR)
        self.assertEqual(self.app.chat_status.badge.text(), 'Error')
        self.assertFalse(self.app.stop_btn.isEnabled())
        self.send(pre_tokens=())  # Error -> Loading (retry)
        self.assertIs(self.app.lifecycle.state, S.LOADING)
        self.app.stop_turn()
        self.assertTrue(spin(lambda: self.app.worker is None))
        self.send(pre_tokens=(), fail_with='HTTP 500: boom')
        self.assertTrue(spin(lambda: self.app.worker is None))
        self.assertIs(self.app.lifecycle.state, S.ERROR)
        self.app.new_session()  # Error -> Idle (dismiss)
        self.assertIs(self.app.lifecycle.state, S.IDLE)

    def test_switching_view_keeps_streaming_and_shows_latest_progress(self):
        worker, partial = self.stream_partial()
        self.app.tabs.setCurrentIndex(1)
        for t in ('more ', 'tokens '):
            worker.token.emit(t)
        self.assertTrue(spin(lambda: self.app.reply == partial + 'more tokens '))
        self.assertIs(self.app.lifecycle.state, S.STREAMING)
        self.app.tabs.setCurrentIndex(0)
        self.assertEqual(self.app.chat_status.badge.text(), 'Streaming')
        self.assertTrue(self.app.chat_status.progress.rate_lbl.text().endswith('· 5 tok'))
        self.assertEqual(self.app.view.cards['live'].body.toPlainText(), partial + 'more tokens ')
        self.app.stop_turn()
        self.assertTrue(spin(lambda: self.app.worker is None))

    def test_model_initialization_shows_skeleton_until_ready(self):
        self.assertFalse(self.app.msgs)
        self.app._set_status('loading')
        self.assertFalse(self.app.view.skeleton.isHidden())
        self.assertTrue(self.app.view.cards['empty'].isHidden())
        self.app._set_status('online')
        self.assertTrue(self.app.view.skeleton.isHidden())
        self.assertFalse(self.app.view.cards['empty'].isHidden())

    def test_tool_inspector_mirrors_turn_and_stops_it(self):
        self.app._show_tools_inspector()
        dlg = self.app._tools_inspector_dialog
        worker, partial = self.stream_partial()
        self.assertEqual(dlg.turn_status.badge.text(), 'Streaming')
        self.assertTrue(dlg.stop_btn.isEnabled())
        self.assert_cancelled_in_one_frame(dlg.stop_btn.click, worker, partial)
        self.assertEqual(dlg.turn_status.badge.text(), 'Idle')


if __name__ == '__main__':
    unittest.main()
