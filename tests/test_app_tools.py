import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QSettings, QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QTextEdit
from llama_gadget.app import Gadget
from llama_gadget.chat import ChatWorker
from llama_gadget import profiles
from tests.support import StreamingServer, call_delta, chunk

APPLICATION = QApplication.instance() or QApplication([])


def pump_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APPLICATION.processEvents()
        if predicate():
            return True
        time.sleep(.005)
    return False


class AppToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        settings.setValue('db_path', str(self.root / 'chat.db'))
        settings.setValue('profile', 'agent-gpu0')
        with patch('llama_gadget.app.QSettings', return_value=settings), patch.object(Gadget, '_poll'), \
                patch('llama_gadget.app.default_preferences_path', return_value=self.root / 'preferences.json'):
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
        APPLICATION.processEvents()

    def setup_tools(self):
        self.app.workspace = str(self.root)
        self.app._update_controls()
        self.app.tools_cb.setChecked(True)

    def server(self, responses):
        server = StreamingServer(responses)
        self.addCleanup(server.close)
        self.app.settings.setValue('endpoint/agent-gpu0', server.url)
        # Patch the accessor rather than relying on a persisted endpoint's key format.
        patcher = patch.object(self.app, 'current_base_url', return_value=server.url)
        patcher.start()
        self.addCleanup(patcher.stop)
        return server

    def test_controls_require_workspace_and_agent(self):
        self.assertFalse(self.app.tools_cb.isEnabled())
        self.setup_tools()
        self.assertTrue(self.app.tools_cb.isChecked())
        self.app.profile = profiles.PROFILES['vision']
        self.app._update_controls()
        self.assertFalse(self.app.tools_cb.isEnabled())
        self.assertFalse(self.app.tools_cb.isChecked())

    def test_payload_replays_metadata_and_attachments(self):
        self.app.msgs = [
            {'role': 'user', 'content': 'Hi', 'attachments': [{'name': 'note', 'text': 'hello'}]},
            {'role': 'assistant', 'content': '', 'metadata': {'tool_calls': [{'id': 'call_1'}]}},
            {'role': 'tool', 'content': '{"ok":true}', 'metadata': {'tool_call_id': 'call_1', 'name': 'read_file'}},
        ]
        payload = self.app._payload()
        self.assertEqual(payload[-2]['tool_calls'], [{'id': 'call_1'}])
        self.assertEqual(payload[-1]['tool_call_id'], 'call_1')
        self.assertIn('hello', payload[1]['content'])

    def test_actual_read_loop_persisted_and_reopened(self):
        self.setup_tools()
        (self.root / 'note.txt').write_text('hello')
        server = self.server([
            [chunk({'content': 'Checking'}), chunk(call_delta()), chunk(finish='tool_calls'), '[DONE]'],
            [chunk({'content': 'Final answer'}), chunk(finish='stop'), '[DONE]'],
        ])
        self.app.input.setPlainText('Read note')
        self.app.send()
        self.assertTrue(pump_until(lambda: self.app.worker is None))
        saved = self.app.db.load_messages(self.app.session_id)
        self.assertEqual([m['role'] for m in saved], ['user', 'assistant', 'tool', 'assistant'])
        self.assertEqual(saved[-1]['content'], 'Final answer')
        self.assertEqual(saved[1]['content'], 'Checking')
        self.assertEqual(server.requests[1]['messages'][-1]['tool_call_id'], 'call_1')
        self.assertNotIn('read_file', self.app.view.toPlainText())
        before = self.app._payload()
        self.app.show_tools_cb.setChecked(True)
        self.assertIn('read_file', self.app.view.toPlainText())
        self.assertEqual(before, self.app._payload())
        self.app.msgs = saved
        self.assertEqual(self.app._payload()[-2]['tool_call_id'], 'call_1')

    def test_dialog_dismissal_denies_without_write(self):
        self.setup_tools()
        self.server([
            [chunk(call_delta(name='write_file', arguments='{"path":"new", "content":"new"}')),
             chunk(finish='tool_calls'), '[DONE]'],
            [chunk({'content': 'Denied'}), chunk(finish='stop'), '[DONE]'],
        ])
        def reject_dialog():
            if self.app._approval_dialog:
                self.app._approval_dialog.reject()
            else:
                QTimer.singleShot(5, reject_dialog)
        QTimer.singleShot(5, reject_dialog)
        self.app.input.setPlainText('Write new')
        self.app.send()
        self.assertTrue(pump_until(lambda: self.app.worker is None))
        self.assertFalse((self.root / 'new').exists())
        self.assertIn('denied', self.app.msgs[-2]['content'].lower())

    def test_failure_after_side_effect_retains_user_and_exchange(self):
        self.setup_tools()
        self.server([
            [chunk(call_delta(name='write_file', arguments='{"path":"new", "content":"new"}')),
             chunk(finish='tool_calls'), '[DONE]'],
            [chunk({'content': 'Interrupted'})],
        ])
        def approve_dialog():
            if self.app._approval_dialog:
                self.app._approval_dialog.accept()
            else:
                QTimer.singleShot(5, approve_dialog)
        QTimer.singleShot(5, approve_dialog)
        self.app.input.setPlainText('Write new')
        self.app.send()
        self.assertTrue(pump_until(lambda: self.app.worker is None))
        self.assertTrue((self.root / 'new').exists())
        self.assertEqual([m['role'] for m in self.app.msgs], ['user', 'assistant', 'tool'])
        self.assertTrue(self.app.last_error)

    def test_new_session_cancels_approval_and_retains_thread(self):
        self.setup_tools()
        self.server([[chunk(call_delta(name='bash_command', arguments='{"command":"touch no"}')),
                      chunk(finish='tool_calls'), '[DONE]']])
        session_ids = []
        def new_session():
            if self.app._approval_dialog:
                session_ids.append(self.app.session_id)
                self.app.new_session()
            else:
                QTimer.singleShot(5, new_session)
        QTimer.singleShot(5, new_session)
        self.app.input.setPlainText('Run')
        self.app.send()
        self.assertTrue(pump_until(lambda: self.app.worker is None and self.app.session_id is None))
        self.assertFalse((self.root / 'no').exists())
        saved = self.app.db.load_messages(session_ids[0])
        self.assertEqual([m['role'] for m in saved], ['user', 'assistant', 'tool'])

    def test_plain_chat_still_works(self):
        server = self.server([[chunk({'content': 'Hi'}), chunk(finish='stop'), '[DONE]']])
        self.app.input.setPlainText('Hi')
        self.app.send()
        self.assertTrue(pump_until(lambda: self.app.worker is None))
        self.assertEqual([m['role'] for m in self.app.msgs], ['user', 'assistant'])
        self.assertNotIn('tools', server.requests[0])

    def test_tools_button_opens_inspector(self):
        self.app.tools_btn.click()
        self.assertIsNotNone(self.app._tools_inspector_dialog)
        self.assertTrue(self.app._tools_inspector_dialog.isVisible())
        self.app._tools_inspector_dialog.close()

    def test_mcp_only_turn_without_workspace(self):
        from unittest.mock import MagicMock
        from llama_gadget.tool_catalog import ToolCatalog

        self.app.workspace = ''
        mock_manager = MagicMock()
        tool_entry = {
            'server': 'demo_server', 'original_name': 'demo_tool', 'description': 'Demo',
            'input_schema': {'type': 'object', 'properties': {'x': {'type': 'number'}}}, 'generation': 1
        }
        mock_manager.snapshot.return_value = [tool_entry]
        self.app.mcp_manager = mock_manager
        self.app._update_controls()

        # Tools can be enabled without workspace because MCP tools are present
        self.assertTrue(self.app.tools_cb.isEnabled())
        self.app.tools_cb.setChecked(True)

        catalog = ToolCatalog(None, mock_manager, [tool_entry])
        model_tool_name = catalog.schemas()[0]['function']['name']

        server = self.server([
            [chunk(call_delta(name=model_tool_name, arguments='{"x":42}')), chunk(finish='tool_calls'), '[DONE]'],
            [chunk({'content': 'Done'}), chunk(finish='stop'), '[DONE]'],
        ])

        # Verify approval dialog displays original tool and server name
        approval_seen = []
        def handle_dialog():
            if self.app._approval_dialog:
                dlg = self.app._approval_dialog
                approval_seen.append({
                    'title': dlg.windowTitle(),
                    'label': [c.text() for c in dlg.findChildren(QLabel)],
                    'preview': [c.toPlainText() for c in dlg.findChildren(QTextEdit)],
                })
                mock_manager.call.return_value = {'ok': True, 'content': [{'type': 'text', 'text': '42'}]}
                dlg.accept()
            else:
                QTimer.singleShot(5, handle_dialog)
        QTimer.singleShot(5, handle_dialog)

        self.app.input.setPlainText('Call demo')
        self.app.send()
        self.assertTrue(pump_until(lambda: self.app.worker is None))

        # Check payload has no local-workspace instructions
        first_req = server.requests[0]
        sys_msg = first_req['messages'][0]['content']
        self.assertNotIn('workspace:', sys_msg.lower())
        self.assertIn('External MCP tool calls require user approval', sys_msg)

        # Check approval dialog content
        self.assertTrue(approval_seen)
        self.assertIn('demo_tool', approval_seen[0]['title'])
        labels_joined = ' '.join(approval_seen[0]['label'])
        self.assertIn('demo_server', labels_joined)
        self.assertIn('demo_tool', labels_joined)
        self.assertIn('42', approval_seen[0]['preview'][0])

    def test_yolo_mode_auto_approves_without_dialog(self):
        self.setup_tools()
        self.app.yolo_cb.setChecked(True)
        self.server([
            [chunk(call_delta(name='write_file', arguments='{"path":"yolo_test.txt", "content":"auto approved!"}')),
             chunk(finish='tool_calls'), '[DONE]'],
            [chunk({'content': 'Done'}), chunk(finish='stop'), '[DONE]'],
        ])
        self.app.input.setPlainText('Write yolo')
        self.app.send()
        self.assertTrue(pump_until(lambda: self.app.worker is None))
        self.assertIsNone(self.app._approval_dialog)
        self.assertTrue((self.root / 'yolo_test.txt').exists())
        self.assertEqual((self.root / 'yolo_test.txt').read_text(), 'auto approved!')

    def test_usage_chunk_updates_stats_without_error(self):
        # llama.cpp ends every streamed turn with a usage chunk; a handler with a
        # mismatched signature aborts the whole process when the signal is queued.
        self.setup_tools()
        self.server([
            [chunk({'content': 'Hi'}), chunk(finish='stop'),
             {'usage': {'prompt_tokens': 120, 'completion_tokens': 45, 'total_tokens': 165},
              'timings': {'prompt_per_second': 890.5, 'predicted_per_second': 42.7}}, '[DONE]'],
        ])
        self.app.input.setPlainText('Hello')
        self.app.send()
        self.assertTrue(pump_until(lambda: self.app.worker is None))
        self.assertEqual(self.app.last_error, '')
        self.assertTrue(self.app.ctx_exact)
        self.assertEqual(self.app.ctx_used, 165)
        self.assertIn('42.7 tok/s', self.app.stats_lbl.text())

    def test_clip_menu_lists_history_without_unpack_error(self):
        # list_clips returns plain texts; unpacking 3-tuples aborted the app on click.
        self.app.db.add_clip('first clip')
        self.app.db.add_clip('second clip that is long enough to be trimmed with…')
        menus = []
        from PyQt6.QtWidgets import QMenu
        orig_exec = QMenu.exec

        def fake_exec(menu_self, pos):
            menus.append([a.text() for a in menu_self.actions() if a.text()])
            return None
        with patch.object(QMenu, 'exec', fake_exec):
            self.app._show_clip_menu()
        self.assertEqual(len(menus), 1)
        self.assertTrue(any('first clip' in t for t in menus[0]))
        self.assertTrue(any('Clear clipboard history' in t for t in menus[0]))

    def test_attach_image_file_records_path_and_dedupes(self):
        # Regression: encode_image() got an unexpected keyword argument 'path'
        from PyQt6.QtGui import QColor, QImage
        from llama_gadget.images import load_image
        self.app.profile = profiles.PROFILES['vision']
        jpeg = self.root / 'HTxn11yaEAABpRH.jpeg'
        img = QImage(32, 16, QImage.Format.Format_RGB32)
        img.fill(QColor('red'))
        self.assertTrue(img.save(str(jpeg), 'JPEG'))

        self.app._add_attachment(str(jpeg))
        self.assertEqual(len(self.app.attachments), 1)
        att = self.app.attachments[0]
        self.assertEqual(att['name'], 'HTxn11yaEAABpRH.jpeg')
        self.assertEqual(att['path'], str(jpeg))
        self.assertIn('data:image/jpeg;base64,', att['data'])

        self.app._add_attachment(str(jpeg))  # same file twice: silently deduplicated
        self.assertEqual(len(self.app.attachments), 1)

    def test_image_pasted_accepts_qimage_and_preencoded(self):
        # Regression: drag-drop emitted a pre-encoded dict into the encode path.
        from PyQt6.QtGui import QColor, QImage
        from llama_gadget.images import load_image
        self.app.profile = profiles.PROFILES['vision']

        img = QImage(32, 16, QImage.Format.Format_RGB32)
        img.fill(QColor('blue'))
        self.app._on_image_pasted(img)
        self.assertEqual(len(self.app.attachments), 1)
        self.assertTrue(self.app.attachments[0]['name'].startswith('Pasted-Image-'))

        png = self.root / 'x.png'
        img2 = QImage(16, 16, QImage.Format.Format_RGB32)
        img2.fill(QColor('green'))
        self.assertTrue(img2.save(str(png), 'PNG'))
        dropped = load_image(str(png))
        self.app._on_image_pasted(dropped)
        self.assertEqual(len(self.app.attachments), 2)
        self.assertIs(self.app.attachments[1], dropped)
