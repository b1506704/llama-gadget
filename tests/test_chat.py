import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from llama_gadget.chat import ChatWorker
from llama_gadget.tools import ToolRegistry
from tests.support import StreamingServer, call_delta, chunk

APPLICATION = QApplication.instance() or QApplication([])


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'note.txt').write_text('hello')
        self.registry = ToolRegistry(self.root)

    def worker(self, responses, registry=True, **kwargs):
        self.server = StreamingServer(responses)
        self.addCleanup(self.server.close)
        worker = ChatWorker([{'role': 'user', 'content': 'Read note.txt'}],
                            base_url=self.server.url, registry=self.registry if registry else None, **kwargs)
        self.errors, self.exchanges, self.finals, self.tokens, self.stats = [], [], [], [], []
        for signal, values in [(worker.failed, self.errors), (worker.exchange_completed, self.exchanges),
                               (worker.message_completed, self.finals), (worker.token, self.tokens),
                               (worker.stats, self.stats)]:
            signal.connect(values.append, Qt.ConnectionType.DirectConnection)
        return worker

    def test_fragmented_call_then_final(self):
        worker = self.worker([
            [chunk({'content': 'Checking.'}), chunk(call_delta(arguments='{"pa')),
             chunk(call_delta(id=None, name=None, arguments='th":"note.txt"}')),
             chunk(finish='tool_calls'), {'choices': [], 'usage': {'total_tokens': 10}}, '[DONE]'],
            [chunk({'content': 'It says hello.'}), chunk(finish='stop'), '[DONE]'],
        ])
        worker.run()
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.exchanges), 1)
        self.assertEqual(self.exchanges[0][0]['content'], 'Checking.')
        result = self.server.requests[1]['messages'][-1]
        self.assertEqual(result['role'], 'tool')
        self.assertEqual(result['tool_call_id'], 'call_1')
        self.assertEqual(json.loads(result['content'])['output'], 'hello')
        self.assertIn('tools', self.server.requests[0])
        self.assertEqual(self.finals[-1]['content'], 'It says hello.')
        self.assertEqual(self.stats, [{'total_tokens': 10}])

    def test_multiple_calls_and_two_rounds(self):
        worker = self.worker([
            [chunk(call_delta()), chunk(call_delta(index=1, id='call_2', name='list_directory', arguments='{"path":"."}')),
             chunk(finish='tool_calls'), '[DONE]'],
            [chunk(call_delta(id='call_3')), chunk(finish='tool_calls'), '[DONE]'],
            [chunk({'content': 'Done'}), chunk(finish='stop'), '[DONE]'],
        ])
        worker.run()
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.exchanges), 2)
        self.assertEqual([m['tool_call_id'] for m in self.exchanges[0][1:]], ['call_1', 'call_2'])

    def test_approval_and_denial(self):
        for approved in [False, True]:
            with self.subTest(approved=approved):
                worker = self.worker([
                    [chunk(call_delta(name='write_file', arguments='{"path":"new", "content":"new content"}')),
                     chunk(finish='tool_calls'), '[DONE]'],
                    [chunk({'content': 'Done'}), chunk(finish='stop'), '[DONE]'],
                ])
                requests = []
                def decide(cid, name, args):
                    requests.append(args)
                    self.assertFalse((self.root / 'new').exists())
                    worker.decide(cid, approved)
                worker.approval_requested.connect(decide, Qt.ConnectionType.DirectConnection)
                worker.run()
                self.assertEqual(self.errors, [])
                self.assertEqual(requests[0]['path'], str(self.root / 'new'))
                self.assertEqual((self.root / 'new').exists(), approved)
                result = json.loads(self.exchanges[0][-1]['content'])
                self.assertEqual(result['ok'], approved)

    def test_invalid_arguments_and_unknown_tools_are_results(self):
        for name, arguments in [('missing', '{}'), ('read_file', '{broken')]:
            worker = self.worker([[chunk(call_delta(name=name, arguments=arguments)), chunk(finish='tool_calls'), '[DONE]'],
                                  [chunk({'content': 'Could not do that'}), chunk(finish='stop'), '[DONE]']])
            worker.run()
            self.assertEqual(self.errors, [])
            self.assertFalse(json.loads(self.exchanges[0][-1]['content'])['ok'])

    def test_interrupted_or_incomplete_calls_never_execute(self):
        for events in [[chunk(call_delta(name='write_file', arguments='{"path":"new", "content":"x"}'))],
                       [chunk(call_delta(id=None)), chunk(finish='tool_calls'), '[DONE]'],
                       [chunk(call_delta()), chunk(finish='length'), '[DONE]']]:
            worker = self.worker([events])
            worker.run()
            self.assertTrue(self.errors)
            self.assertEqual(self.exchanges, [])
            self.assertFalse((self.root / 'new').exists())

    def test_cancellation_wakes_approval_and_stale_decision_ignored(self):
        worker = self.worker([[chunk(call_delta(name='bash_command', arguments='{"command":"touch no"}')),
                               chunk(finish='tool_calls'), '[DONE]']])
        pending = threading.Event()
        worker.approval_requested.connect(lambda *args: pending.set(), Qt.ConnectionType.DirectConnection)
        thread = threading.Thread(target=worker.run)
        thread.start()
        self.assertTrue(pending.wait(2))
        worker.decide('wrong_call', True)
        self.assertTrue(thread.is_alive())
        worker.cancel()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse((self.root / 'no').exists())
        self.assertEqual(self.errors, [])
        self.assertFalse(json.loads(self.exchanges[0][-1]['content'])['ok'])

    def test_execution_and_round_limits(self):
        worker = self.worker([[chunk(call_delta()), chunk(call_delta(index=1, id='call_2')),
                               chunk(finish='tool_calls'), '[DONE]']], max_executions=1)
        worker.run()
        self.assertTrue(self.errors)
        self.assertEqual(self.exchanges, [])
        worker = self.worker([[chunk(call_delta()), chunk(finish='tool_calls'), '[DONE]']], max_rounds=1)
        worker.run()
        self.assertTrue(self.errors)
        self.assertEqual(len(self.exchanges), 1)

    def test_plain_chat_no_tools(self):
        worker = self.worker([[chunk({'content': 'Hi'}), chunk(finish='stop'), '[DONE]']], registry=False)
        worker.run()
        self.assertEqual(self.errors, [])
        self.assertNotIn('tools', self.server.requests[0])
        self.assertEqual(self.tokens, ['Hi'])
        self.assertEqual(self.finals, [{'role': 'assistant', 'content': 'Hi'}])

    def test_cancel_unblocks_silent_stream(self):
        release = threading.Event()
        self.addCleanup(release.set)
        worker = self.worker([[release, chunk(finish='stop'), '[DONE]']], registry=False)
        worker.start()
        self.assertTrue(self.server.response_started.wait(2))
        worker.cancel()
        self.assertTrue(worker.wait(2000))
        self.assertEqual(self.errors, [])
        self.assertEqual(self.finals, [])

    def test_duplicate_ids_rejected(self):
        worker = self.worker([[chunk(call_delta()), chunk(call_delta(index=1)), chunk(finish='tool_calls'), '[DONE]']])
        worker.run()
        self.assertTrue(self.errors)
        self.assertEqual(self.exchanges, [])

    def test_prepare_exception_after_write_preserves_complete_exchange(self):
        worker = self.worker([
            [chunk(call_delta(name='write_file', arguments='{"path":"written", "content":"yes"}')),
             chunk(call_delta(index=1, id='call_2', arguments='{"path":"~llama_gadget_missing_user_938224/file"}')),
             chunk(finish='tool_calls'), '[DONE]'],
            [chunk({'content': 'Write succeeded; read failed'}), chunk(finish='stop'), '[DONE]'],
        ])
        worker.approval_requested.connect(lambda cid, *args: worker.decide(cid, True), Qt.ConnectionType.DirectConnection)
        worker.run()
        self.assertEqual((self.root / 'written').read_text(), 'yes')
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.exchanges), 1)
        self.assertEqual(len(self.exchanges[0]), 3)
        self.assertTrue(json.loads(self.exchanges[0][1]['content'])['ok'])
        self.assertFalse(json.loads(self.exchanges[0][2]['content'])['ok'])

    def test_reused_protocol_id_has_unique_approval_identity(self):
        events = [chunk(call_delta(name='write_file', arguments='{"path":"written", "content":"yes"}')),
                  chunk(finish='tool_calls'), '[DONE]']
        second = [chunk(call_delta(name='write_file', arguments='{"path":"second", "content":"yes"}')),
                  chunk(finish='tool_calls'), '[DONE]']
        worker = self.worker([events, second, [chunk({'content': 'Done'}), chunk(finish='stop'), '[DONE]']])
        approval_ids = []
        waiting = threading.Event()
        def decide(cid, *args):
            approval_ids.append(cid)
            if len(approval_ids) == 1:
                worker.decide(cid, True)
            else:
                waiting.set()
        worker.approval_requested.connect(decide, Qt.ConnectionType.DirectConnection)
        thread = threading.Thread(target=worker.run)
        thread.start()
        self.addCleanup(worker.cancel)
        self.assertTrue(waiting.wait(2))
        worker.decide(approval_ids[0], True)
        thread.join(.1)
        self.assertTrue(thread.is_alive())
        self.assertFalse((self.root / 'second').exists())
        worker.decide(approval_ids[1], False)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.errors, [])
        self.assertEqual(len(approval_ids), 2)
        self.assertNotEqual(approval_ids[0], approval_ids[1])
        self.assertFalse((self.root / 'second').exists())
