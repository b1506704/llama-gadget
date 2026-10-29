"""Headless ErrorRecoveryBanner and inline error recovery on the chat and tool surfaces
(QT_QPA_PLATFORM=offscreen)."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import concurrent.futures
import copy
import re
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PyQt6.QtCore import QEventLoop, QSettings, QTimer
from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QPushButton

from llama_gadget import profiles
from llama_gadget.app import Gadget
from llama_gadget.chat import ChatWorker
from llama_gadget.chat_ui import ErrorRecoveryBanner
from llama_gadget.errors import SETTINGS_ENDPOINT, SETTINGS_MCP, UiError
from llama_gadget.lifecycle import SurfaceState
from llama_gadget.theme import TOKENS, ThemeTokens
from llama_gadget.tool_ui import ToolInspectorDialog

APP = QApplication.instance() or QApplication([])
S = SurfaceState
HOME = Path('/home/alice')
SECRET = 'sk-live1234567890abcdef'
RAW_FAILURE = (f'Traceback (most recent call last):\n  File "{HOME}/llama/run.py", line 9, in stream\n'
               f'ConnectionRefusedError: [Errno 111] Connection refused (api_key={SECRET})')


def spin(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    loop = QEventLoop()
    check = QTimer(interval=1, timeout=lambda: loop.quit() if predicate() or time.monotonic() > deadline else None)
    check.start()
    if not predicate():
        loop.exec()
    check.stop()
    return predicate()


def shown(widget):
    return not widget.isHidden()


def assert_sanitized(test, report):
    test.assertNotIn(SECRET, report)
    test.assertNotIn(str(HOME), report)
    test.assertNotIn('alice', report)
    test.assertIn('<REDACTED>', report)


class ScriptedWorker(ChatWorker):
    """Each run consumes one outcome: an error string fails the turn, None completes it."""
    outcomes = []
    payloads = []

    def run(self):
        ScriptedWorker.payloads.append(copy.deepcopy(self.messages))
        outcome = ScriptedWorker.outcomes.pop(0) if ScriptedWorker.outcomes else None
        self.token.emit('Par')
        time.sleep(.02)
        if outcome:
            self.failed.emit(outcome)
            return
        self.message_completed.emit(dict(role='assistant', content='Recovered answer'))
        self.finished_ok.emit()


class BannerTests(unittest.TestCase):
    def setUp(self):
        self.banner = ErrorRecoveryBanner()
        self.addCleanup(self.banner.deleteLater)
        self.retries = []
        self.error = UiError('backend_unreachable', "The model server isn't reachable.", True,
                             retry_callback=lambda: self.retries.append(self.banner.isHidden()),
                             settings_section=SETTINGS_ENDPOINT,
                             diagnostics={'error': RAW_FAILURE, 'endpoint': 'http://u:pw123456@127.0.0.1:8080'})

    def test_hidden_until_presented_with_summary_and_actions(self):
        self.assertTrue(self.banner.isHidden())
        self.banner.present_error(self.error)
        self.assertTrue(shown(self.banner))
        self.assertEqual(self.banner.summary_lbl.text(), "The model server isn't reachable.")
        self.assertEqual([(b.text(), shown(b)) for b in (self.banner.retry_btn, self.banner.settings_btn,
                                                          self.banner.copy_btn)],
                         [('Retry', True), ('Open Settings', True), ('Copy Diagnostics', True)])
        self.banner.present_error(UiError('context_overflow', 'Too long.', False))
        self.assertFalse(shown(self.banner.retry_btn))  # not recoverable
        self.assertFalse(shown(self.banner.settings_btn))  # no settings section
        self.assertTrue(shown(self.banner.copy_btn))
        self.banner.present_error(UiError('x', 'No callback.', True))
        self.assertFalse(shown(self.banner.retry_btn))

    def test_retry_clears_then_invokes_callback_and_signal(self):
        emitted = []
        self.banner.retry_requested.connect(emitted.append)
        self.banner.present_error(self.error)
        self.banner.retry_btn.click()
        self.assertEqual(self.retries, [True])  # banner already dismissed when the callback runs
        self.assertEqual(emitted, [self.error])
        self.assertTrue(self.banner.isHidden())
        self.assertIsNone(self.banner.error)

    def test_retry_blocked_while_surface_busy(self):
        self.banner.present_error(self.error)
        self.banner.set_retry_blocked(True)
        self.assertFalse(self.banner.retry_btn.isEnabled())
        self.banner.retry()
        self.assertEqual(self.retries, [])
        self.banner.set_retry_blocked(False)
        self.banner.retry_btn.click()
        self.assertEqual(self.retries, [True])

    def test_open_settings_emits_section_and_keeps_banner(self):
        sections = []
        self.banner.open_settings_requested.connect(sections.append)
        self.banner.present_error(self.error)
        self.banner.settings_btn.click()
        self.assertEqual(sections, [SETTINGS_ENDPOINT])
        self.assertTrue(shown(self.banner))
        self.assertIs(self.banner.error, self.error)
        self.assertEqual(self.retries, [])

    def test_copy_diagnostics_is_sanitized_confirms_inline_and_keeps_banner(self):
        self.banner.present_error(self.error)
        self.assertTrue(self.banner.copied_lbl.isHidden())
        with patch.object(Path, 'home', return_value=HOME):
            self.banner.copy_btn.click()
        report = APP.clipboard().text()
        self.assertTrue(report.startswith('Llama Gadget diagnostics\ncode: backend_unreachable'))
        assert_sanitized(self, report)
        self.assertNotIn('pw123456', report)
        self.assertTrue(shown(self.banner.copied_lbl))
        self.assertIn('clipboard', self.banner.copied_lbl.text())
        self.assertTrue(shown(self.banner))
        self.assertTrue(self.banner.confirm_timer.isActive())
        self.banner.confirm_timer.timeout.emit()  # confirmation is brief
        self.assertTrue(self.banner.copied_lbl.isHidden())
        self.assertTrue(shown(self.banner))

    def test_dismiss_clears_and_signals(self):
        dismissed = []
        self.banner.dismissed.connect(lambda: dismissed.append(1))
        self.banner.present_error(self.error)
        self.banner.dismiss_btn.click()
        self.assertEqual(dismissed, [1])
        self.assertTrue(self.banner.isHidden())

    def test_styles_come_only_from_theme_tokens(self):
        custom = ThemeTokens(error='#aa0001', error_surface='#aa0002', text='#aa0003', success='#aa0004')
        banner = ErrorRecoveryBanner(tokens=custom)
        self.addCleanup(banner.deleteLater)
        sheets = ' '.join(w.styleSheet() for w in [banner] + banner.findChildren((QLabel, QPushButton, QFrame)))
        colours = set(re.findall(r'#[0-9a-fA-F]{3,8}\b', sheets))
        self.assertEqual(colours, {'#aa0001', '#aa0002', '#aa0003', '#aa0004'})
        self.assertNotRegex(sheets, r'rgba?\(')
        self.assertIn(f'border-radius: {custom.radius}px', sheets)
        self.assertIn(TOKENS.error_surface, self.banner.styleSheet())


class GadgetCase(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        settings.setValue('db_path', str(self.root / 'chat.db'))
        settings.setValue('profile', 'agent-gpu0')
        with patch('llama_gadget.app.QSettings', return_value=settings), patch.object(Gadget, '_poll'):
            self.app = Gadget()
        self.app.timer.stop()
        self.app.state = 'online'
        self.app._poll = lambda: None  # no background status probes flipping the state mid-test
        self.app.mcp_config_path = self.root / 'mcp.json'
        self.addCleanup(self.cleanup_app)
        patcher = patch('llama_gadget.app.ChatWorker', ScriptedWorker)
        patcher.start()
        self.addCleanup(patcher.stop)
        ScriptedWorker.outcomes, ScriptedWorker.payloads = [], []
        home = patch.object(Path, 'home', return_value=HOME)
        home.start()
        self.addCleanup(home.stop)

    def cleanup_app(self):
        self.app._shutdown_workers()
        self.app.tray.hide()
        self.app.clipboard.dataChanged.disconnect(self.app._on_clipboard)
        self.app.db.conn.close()
        for dialog in (self.app._tools_inspector_dialog, self.app._mcp_config_dialog):
            if dialog is not None:
                dialog.hide()
        self.app.hide()
        self.app.deleteLater()
        APP.processEvents()


class ChatRecoveryTests(GadgetCase):
    def fail_turn(self, error=RAW_FAILURE):
        ScriptedWorker.outcomes = [error]
        self.app.input.setPlainText('Hi')
        self.app.send()
        self.assertTrue(spin(lambda: self.app.worker is None))
        self.assertIs(self.app.lifecycle.state, S.ERROR)
        return self.app.chat_banner

    def saved(self):
        return [(m['role'], m['content']) for m in self.app.db.load_messages(self.app.session_id)]

    def test_failure_renders_banner_instead_of_traceback(self):
        banner = self.fail_turn()
        self.assertTrue(shown(banner))
        self.assertEqual(banner.error.code, 'backend_unreachable')
        self.assertIn("isn't reachable", banner.summary_lbl.text())
        transcript = self.app.view.toPlainText()
        for raw in ('Traceback', 'Errno', 'ConnectionRefusedError', SECRET, 'Error:'):
            self.assertNotIn(raw, transcript)
            self.assertNotIn(raw, banner.summary_lbl.text())
        self.assertEqual(self.app.chat_status.badge.text(), 'Error')
        # Not frozen: the request stays visible and the composer is usable.
        self.assertEqual([(m['role'], m['content']) for m in self.app.msgs], [('user', 'Hi')])
        self.assertEqual(self.saved(), [('user', 'Hi')])
        self.assertTrue(self.app.input.isEnabled())
        self.assertTrue(banner.retry_btn.isEnabled())
        self.assertTrue(self.app.send_btn.isEnabled())

    def test_retry_restarts_in_place_without_duplicating_request(self):
        banner = self.fail_turn()
        banner.retry_btn.click()
        self.assertIs(self.app.lifecycle.state, S.LOADING)  # Error -> Loading synchronously
        self.assertTrue(banner.isHidden())
        self.assertTrue(spin(lambda: self.app.worker is None))
        self.assertIs(self.app.lifecycle.state, S.IDLE)
        self.assertEqual(len(ScriptedWorker.payloads), 2)
        self.assertEqual(ScriptedWorker.payloads[1], ScriptedWorker.payloads[0])  # same request, re-run
        self.assertEqual([m['content'] for m in ScriptedWorker.payloads[1] if m['role'] == 'user'], ['Hi'])
        self.assertEqual(self.saved(), [('user', 'Hi'), ('assistant', 'Recovered answer')])
        self.assertEqual(self.app.view.toPlainText().count('Hi'), 1)

    def test_retry_that_fails_again_shows_a_fresh_banner(self):
        banner = self.fail_turn()
        ScriptedWorker.outcomes = ['HTTP 503: loading model']
        banner.retry_btn.click()
        self.assertTrue(spin(lambda: self.app.worker is None))
        self.assertIs(self.app.lifecycle.state, S.ERROR)
        self.assertEqual(banner.error.code, 'backend_http_error')
        self.assertIn('HTTP 503', banner.summary_lbl.text())
        self.assertEqual(self.saved(), [('user', 'Hi')])

    def test_open_settings_jumps_to_panel_and_keeps_conversation_and_banner(self):
        self.app.show()
        banner = self.fail_turn()
        banner.settings_btn.click()
        self.assertIs(self.app.tabs.currentWidget(), self.app.settings_tab)
        # The window's focus child, not QApplication.focusWidget(): that one also depends on
        # the offscreen platform activating the window, which is timing-sensitive.
        self.assertIs(self.app.focusWidget(), self.app.endpoint_edit)
        self.assertIs(banner.error.settings_section, SETTINGS_ENDPOINT)
        self.assertFalse(banner.isHidden())
        self.assertIs(self.app.lifecycle.state, S.ERROR)  # still actionable; nothing was dismissed
        self.assertEqual([m['content'] for m in self.app.msgs], ['Hi'])
        # Fix the configuration, come back and retry from the same banner.
        self.app.endpoint_edit.setText('http://127.0.0.1:8081')
        self.app._apply_endpoint()
        self.app.tabs.setCurrentIndex(0)
        self.assertTrue(banner.isVisible())
        banner.retry_btn.click()
        self.assertIs(self.app.lifecycle.state, S.LOADING)
        self.assertTrue(spin(lambda: self.app.worker is None))
        self.assertEqual(self.saved(), [('user', 'Hi'), ('assistant', 'Recovered answer')])

    def test_new_message_after_failure_replaces_unanswered_request(self):
        self.fail_turn()  # banner left up: typing a new message instead of clicking Retry
        self.app.input.setPlainText('Hello again')
        self.app.send()
        self.assertTrue(spin(lambda: self.app.worker is None))
        roles = [m['role'] for m in ScriptedWorker.payloads[-1] if m['role'] != 'system']
        self.assertEqual(roles, ['user'])  # never two 'user' messages in a row
        self.assertEqual(self.saved(), [('user', 'Hello again'), ('assistant', 'Recovered answer')])

    def test_copy_diagnostics_copies_sanitized_report_and_keeps_banner(self):
        self.app._set_endpoint('http://admin:hunter2pass@127.0.0.1:8080')
        banner = self.fail_turn()
        banner.copy_btn.click()
        report = APP.clipboard().text()
        assert_sanitized(self, report)
        self.assertNotIn('hunter2pass', report)
        self.assertIn('profile: agent-gpu0', report)
        self.assertIn('operation: chat turn', report)
        self.assertTrue(shown(banner.copied_lbl))
        self.assertTrue(shown(banner))
        self.assertIs(self.app.lifecycle.state, S.ERROR)

    def test_dismiss_returns_to_idle_and_new_send_supersedes_banner(self):
        banner = self.fail_turn()
        banner.dismiss_btn.click()
        self.assertIs(self.app.lifecycle.state, S.IDLE)
        self.assertTrue(banner.isHidden())
        self.fail_turn('timed out')
        self.assertTrue(shown(banner))
        ScriptedWorker.outcomes = [None]
        self.app.input.setPlainText('Next')
        self.app.send()
        self.assertTrue(banner.isHidden())
        self.assertTrue(spin(lambda: self.app.worker is None))
        self.app.new_session()
        self.assertTrue(banner.isHidden())

    def test_model_start_failure_recovers_inline(self):
        prof = self.app.profile
        self.app.state = 'offline'
        with patch('llama_gadget.app.QMessageBox.warning') as warning:
            self.app._toggle_done(prof, 'offline', f'Job failed. See {HOME}/.cache/log token={SECRET}')
        warning.assert_not_called()
        banner = self.app.chat_banner
        self.assertTrue(shown(banner))
        self.assertEqual(banner.error.code, 'model_start_failed')
        self.assertIn(prof.label, banner.summary_lbl.text())
        self.assertNotIn('Job failed', banner.summary_lbl.text())
        banner.copy_btn.click()
        assert_sanitized(self, APP.clipboard().text())
        banner.settings_btn.click()
        self.assertIs(self.app.tabs.currentWidget(), self.app.settings_tab)
        self.assertTrue(shown(banner))
        with patch.object(self.app, 'toggle_service') as toggle:
            banner.retry_btn.click()
        toggle.assert_called_once_with()
        self.assertTrue(banner.isHidden())

    def test_model_retry_skipped_when_no_longer_needed(self):
        prof = self.app.profile
        self.app._toggle_done(prof, 'online', 'systemd-run failed')
        self.app.state = 'online'  # the service came up after all
        with patch.object(self.app, 'toggle_service') as toggle:
            self.app.chat_banner.retry_btn.click()
        toggle.assert_not_called()
        other = profiles.PROFILES['services'] if prof.key != 'services' else profiles.PROFILES['agent-gpu0']
        self.app.state = 'offline'
        self.app._toggle_done(other, 'offline', 'systemd-run failed')
        with patch.object(self.app, 'toggle_service') as toggle:
            self.app.chat_banner.retry_btn.click()
        toggle.assert_not_called()  # a different profile is selected now


class FakeMCPManager:
    def __init__(self, statuses):
        self._statuses = statuses
        self.connects = 0

    def snapshot(self):
        return []

    def statuses(self):
        return copy.deepcopy(self._statuses)

    def _done(self):
        future = concurrent.futures.Future()
        future.set_result(self.statuses())
        return future

    def connect(self, config):
        self.connects += 1
        return self._done()

    def disconnect(self):
        return self._done()

    def close(self):
        pass


class ToolRecoveryTests(GadgetCase):
    def open_inspector(self, statuses):
        self.app.mcp_manager = FakeMCPManager(statuses)
        self.app._show_tools_inspector()
        return self.app._tools_inspector_dialog

    def test_provider_failure_banner_retry_settings_and_diagnostics(self):
        dlg = self.open_inspector({'github': {'status': 'failed', 'error': f'401 for Bearer abcdef123456 at {HOME}/x'},
                                   'fs': {'status': 'connected', 'error': ''}})
        dlg.refresh_btn.click()
        banner = dlg.error_banner
        self.assertTrue(shown(banner))
        self.assertIs(dlg.lifecycle.state, S.ERROR)
        self.assertEqual(dlg.provider_status.badge.text(), 'Error')
        self.assertEqual(banner.error.code, 'mcp_provider_failed')
        self.assertIn('github', banner.summary_lbl.text())
        self.assertNotIn('401', banner.summary_lbl.text())
        # Copy Diagnostics: sanitized, banner stays.
        banner.copy_btn.click()
        report = APP.clipboard().text()
        self.assertNotIn('abcdef123456', report)
        self.assertNotIn('alice', report)
        self.assertIn('github', report)
        self.assertTrue(shown(banner) and shown(banner.copied_lbl))
        # Open Settings: MCP configuration opens, the banner stays for a retry.
        banner.settings_btn.click()
        self.assertIsNotNone(self.app._mcp_config_dialog)
        self.assertTrue(self.app._mcp_config_dialog.isVisible())
        self.assertTrue(shown(banner))
        # Retry re-runs Connect / Refresh in place (Error -> Loading -> settled).
        states = []
        dlg.lifecycle.state_changed.connect(states.append)
        self.app.mcp_manager._statuses['github'] = {'status': 'connected', 'error': ''}
        banner.retry_btn.click()
        self.assertEqual(self.app.mcp_manager.connects, 2)
        self.assertEqual(states[0], S.LOADING)
        self.assertIs(dlg.lifecycle.state, S.IDLE)
        self.assertTrue(banner.isHidden())

    def test_invalid_mcp_config_recovers_inline_without_crashing(self):
        dlg = self.open_inspector({})
        Path(self.app.mcp_config_path).write_text('{not json', encoding='utf-8')
        dlg.refresh_btn.click()
        self.assertTrue(shown(dlg.error_banner))
        self.assertEqual(dlg.error_banner.error.code, 'mcp_config_invalid')
        self.assertIs(dlg.lifecycle.state, S.ERROR)
        self.assertEqual(self.app.mcp_manager.connects, 0)
        Path(self.app.mcp_config_path).write_text('{"mcpServers": {}}', encoding='utf-8')
        dlg.error_banner.retry_btn.click()
        self.assertEqual(self.app.mcp_manager.connects, 1)
        self.assertIs(dlg.lifecycle.state, S.IDLE)
        self.assertTrue(dlg.error_banner.isHidden())

    def test_failure_reported_once_and_dismiss_returns_to_idle(self):
        dlg = self.open_inspector({'github': {'status': 'failed', 'error': 'boom'}})
        dlg.refresh_btn.click()
        dlg.error_banner.dismiss_btn.click()
        self.assertIs(dlg.lifecycle.state, S.IDLE)
        self.app.mcp_snapshot_updated.emit()  # a late duplicate notification
        self.assertTrue(dlg.error_banner.isHidden())
        self.assertIs(dlg.lifecycle.state, S.IDLE)

    def test_disconnect_does_not_report_stale_provider_failures(self):
        dlg = self.open_inspector({'github': {'status': 'failed', 'error': 'boom'}})
        dlg.disconnect_btn.click()
        self.assertTrue(dlg.error_banner.isHidden())
        self.assertIs(dlg.lifecycle.state, S.IDLE)

    def test_retry_blocked_during_active_turn(self):
        dlg = ToolInspectorDialog()
        self.addCleanup(dlg.deleteLater)
        calls = []
        dlg.begin_provider_refresh()
        dlg.fail_provider_refresh(UiError('mcp_provider_failed', 'Failed.', True, retry_callback=lambda: calls.append(1),
                                          settings_section=SETTINGS_MCP))
        dlg.set_active_turn(True)
        self.assertFalse(dlg.error_banner.retry_btn.isEnabled())
        dlg.set_active_turn(False)
        dlg.error_banner.retry_btn.click()
        self.assertEqual(calls, [1])


if __name__ == '__main__':
    unittest.main()
