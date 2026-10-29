"""UiError contract, failure mappers and diagnostic redaction."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from llama_gadget.errors import (REDACTED, SETTINGS_ENDPOINT, SETTINGS_MCP, SETTINGS_PROFILE, USER_HOME,
                                 UiError, chat_failure, mcp_config_failure, mcp_provider_failure,
                                 model_failure, sanitize_diagnostics)

SECRETS = ('abcDEF123456tok', 'sk-live1234567890abcd', 'XYZ-api-value', 'hunter2pass', 'ghp_abcdefghijklmnopqrstuv',
           'envsecretvalue', 'eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4')
HOMES = ('/home/alice', '/Users/bob', 'C:\\Users\\carol', '/custom/home/dave')


def leaks(report):
    found = [s for s in SECRETS if s in report]
    found += [h for h in HOMES if h in report or json.dumps(h)[1:-1] in report]
    return found + [n for n in ('alice', 'bob', 'carol', 'dave') if n in report]


class UiErrorTests(unittest.TestCase):
    def test_contract_fields_and_defaults(self):
        calls = []
        error = UiError('code', 'Plain summary.', True, retry_callback=lambda: calls.append(1),
                        settings_section=SETTINGS_ENDPOINT, diagnostics={'error': 'raw'})
        self.assertEqual((error.code, error.summary, error.recoverable, error.settings_section),
                         ('code', 'Plain summary.', True, SETTINGS_ENDPOINT))
        self.assertTrue(error.can_retry)
        error.retry_callback()
        self.assertEqual(calls, [1])
        bare = UiError('c', 's', False)
        self.assertIsNone(bare.retry_callback)
        self.assertIsNone(bare.settings_section)
        self.assertEqual(bare.diagnostics, '')
        self.assertFalse(bare.can_retry)

    def test_chat_failures_map_to_plain_language_without_raw_text(self):
        retry = lambda: None
        traceback = 'Traceback (most recent call last):\n  File "/home/alice/x.py", line 1\nConnectionRefusedError'
        cases = {
            '[Errno 111] Connection refused': ('backend_unreachable', SETTINGS_ENDPOINT, True),
            'timed out': ('backend_timeout', SETTINGS_ENDPOINT, True),
            'HTTP 500: {"error": "boom"}': ('backend_http_error', SETTINGS_ENDPOINT, True),
            'Endpoint must be an HTTP or HTTPS URL': ('invalid_endpoint', SETTINGS_ENDPOINT, True),
            'HTTP 400: the request exceeds the available context size': ('context_overflow', None, False),
            'Tool execution limit reached (32)': ('tool_limit', None, True),
            'Endpoint requested tools while tools are disabled': ('tool_protocol_error', None, True),
            'Incomplete completion (finish reason: None)': ('model_stream_error', SETTINGS_ENDPOINT, True),
            traceback: ('backend_unreachable', SETTINGS_ENDPOINT, True),
        }
        for raw, (code, section, recoverable) in cases.items():
            with self.subTest(raw=raw):
                error = chat_failure(raw, retry=retry, context={'endpoint': 'http://127.0.0.1:8080'})
                self.assertEqual((error.code, error.settings_section, error.recoverable), (code, section, recoverable))
                self.assertIs(error.retry_callback, retry if recoverable else None)
                self.assertNotIn(raw, error.summary)
                self.assertNotIn('Traceback', error.summary)
                self.assertNotIn('Errno', error.summary)
                self.assertEqual(error.diagnostics['error'], raw)
                self.assertEqual(error.diagnostics['endpoint'], 'http://127.0.0.1:8080')
        self.assertIn('HTTP 500', chat_failure('HTTP 500: boom').summary)

    def test_exception_mapping_records_type(self):
        error = chat_failure(ConnectionRefusedError(111, 'Connection refused'))
        self.assertEqual(error.code, 'backend_unreachable')
        self.assertEqual(error.diagnostics['exception'], 'ConnectionRefusedError')

    def test_model_and_mcp_mappers(self):
        error = model_failure('Agent 27B', RuntimeError('Failed to start unit: /home/alice/x'), retry=print)
        self.assertEqual((error.code, error.settings_section), ('model_start_failed', SETTINGS_PROFILE))
        self.assertIn('Agent 27B', error.summary)
        self.assertNotIn('/home/alice', error.summary)
        self.assertEqual(model_failure('P', 'x', stopping=True).code, 'model_stop_failed')

        self.assertIsNone(mcp_provider_failure({}))
        failed = mcp_provider_failure({'github': 'auth failed for envsecretvalue', 'fs': 'timeout'},
                                      retry=print, secrets=('envsecretvalue',))
        self.assertEqual((failed.code, failed.settings_section, failed.retry_callback),
                         ('mcp_provider_failed', SETTINGS_MCP, print))
        self.assertIn('fs, github', failed.summary)
        self.assertNotIn('envsecretvalue', json.dumps(failed.diagnostics))
        whole = mcp_provider_failure({}, error=RuntimeError('MCP connections are closed'))
        self.assertEqual(whole.diagnostics['exception'], 'RuntimeError')
        config = mcp_config_failure(ValueError('bad json'))
        self.assertEqual((config.code, config.settings_section), ('mcp_config_invalid', SETTINGS_MCP))


class SanitizeTests(unittest.TestCase):
    def payloads(self):
        backend = ('POST /v1/chat/completions failed\nAuthorization: Bearer abcDEF123456tok\n'
                   'url=http://user:hunter2pass@127.0.0.1:8080/v1?api_key=XYZ-api-value&x=1\n'
                   'File "/home/alice/llama/start-server.sh"; key sk-live1234567890abcd')
        model = {'error': 'systemd-run failed in /Users/bob/models', 'unit': 'llama-agent',
                 'env': {'HF_TOKEN': 'abcDEF123456tok', 'nested': ['C:\\Users\\carol\\x.gguf', 'jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4']}}
        mcp = {'providers': {'github': 'Authorization: token ghp_abcdefghijklmnopqrstuv rejected',
                             'local': 'spawn /custom/home/dave/bin/server failed; password=hunter2pass'},
               'config': json.dumps({'headers': {'Authorization': 'Bearer abcDEF123456tok'},
                                     'env': {'API_KEY': 'XYZ-api-value', 'secret': 'envsecretvalue'},
                                     'cwd': 'C:\\Users\\carol\\mcp'})}
        return {'backend': backend, 'model': model, 'mcp': mcp}

    def test_no_secret_token_or_home_path_survives(self):
        with patch.object(Path, 'home', return_value=Path('/custom/home/dave')):
            for name, diagnostics in self.payloads().items():
                with self.subTest(payload=name):
                    report = sanitize_diagnostics(UiError(name, 'Summary.', True, diagnostics=diagnostics))
                    self.assertEqual(leaks(report), [], report)
                    self.assertIn(REDACTED, report)
                    self.assertIn('code: ' + name, report)

    def test_home_paths_become_placeholder(self):
        with patch.object(Path, 'home', return_value=Path('/custom/home/dave')):
            report = sanitize_diagnostics(UiError('c', 's', False, diagnostics={
                'a': '/home/alice/x', 'b': '/Users/bob/y', 'c': 'C:\\Users\\carol\\z', 'd': '/custom/home/dave/w',
                'e': '/root/.config/llama-gadget/mcp.json'}))
        for line in ('a: <USER_HOME>/x', 'b: <USER_HOME>/y', 'c: <USER_HOME>\\z', 'd: <USER_HOME>/w',
                     'e: <USER_HOME>/.config/llama-gadget/mcp.json'):
            self.assertIn(line, report)

    def test_report_is_deterministic_and_keeps_useful_context(self):
        error = UiError('backend_http_error', 'The model server returned an error (HTTP 500).', True,
                        settings_section=SETTINGS_ENDPOINT,
                        diagnostics={'profile': 'agent-gpu0', 'endpoint': 'http://127.0.0.1:8080', 'error': 'HTTP 500'})
        report = sanitize_diagnostics(error)
        self.assertEqual(report, sanitize_diagnostics(error))
        self.assertEqual(report.splitlines()[:6], [
            'Llama Gadget diagnostics', 'code: backend_http_error',
            'summary: The model server returned an error (HTTP 500).', 'recoverable: yes',
            'settings_section: endpoint', 'details:'])
        self.assertLess(report.index('endpoint: http://127.0.0.1:8080'), report.index('error: HTTP 500'))
        self.assertIn('profile: agent-gpu0', report)
        self.assertIn('(none)', sanitize_diagnostics(UiError('c', 's', False)))

    def test_string_diagnostics_with_traceback_are_redacted(self):
        text = ('Traceback (most recent call last):\n  File "/home/alice/app.py", line 3\n'
                'RuntimeError: token=abcDEF123456tok')
        report = sanitize_diagnostics(UiError('c', 's', False, diagnostics=text))
        self.assertEqual(leaks(report), [])
        self.assertIn('File "<USER_HOME>/app.py"', report)


if __name__ == '__main__':
    unittest.main()
