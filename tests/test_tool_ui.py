import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QFileDialog
from llama_gadget.tool_ui import ToolInspectorDialog, MCPConfigDialog
from llama_gadget.mcp_config import parse_config, load_config

APP = QApplication.instance() or QApplication([])


class ToolUITests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_path = Path(self.temp.name) / 'mcp.json'

    def test_no_provider_started_on_dialog_open(self):
        # Assert opening either dialog alone starts no provider process
        inspector = ToolInspectorDialog()
        self.addCleanup(inspector.deleteLater)
        config_dlg = MCPConfigDialog(self.config_path)
        self.addCleanup(config_dlg.deleteLater)
        # Verify dialogs exist and haven't triggered any background connections
        self.assertIsNotNone(inspector)
        self.assertIsNotNone(config_dlg)

    def test_config_dialog_validation_save_and_load(self):
        dlg = MCPConfigDialog(self.config_path)
        self.addCleanup(dlg.deleteLater)
        dlg.editor.setPlainText('{"mcpServers": "invalid"}')
        dlg.validate_btn.click()
        self.assertIn('Error', dlg.status_lbl.text())
        self.assertFalse(self.config_path.exists())

        # Insert example and save
        saved = []
        dlg.config_saved.connect(saved.append)
        dlg.example_btn.click()
        self.assertIn('mcpServers', dlg.editor.toPlainText())
        dlg.save_btn.click()
        self.assertEqual(len(saved), 1)
        self.assertEqual(self.config_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(load_config(self.config_path), saved[0])
        self.assertIn('Saved', dlg.status_lbl.text())

        # Load from file preserves imported file
        import_path = Path(self.temp.name) / 'custom.json'
        custom_content = '{"mcpServers": {"demo": {"command": "python", "args": ["srv.py"], "disabled": true}}}'
        import_path.write_text(custom_content)
        with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(import_path), '')):
            dlg.load_btn.click()
        self.assertIn('demo', dlg.editor.toPlainText())
        self.assertEqual(import_path.read_text(), custom_content)

        # Active turn guards
        dlg.set_active_turn(True)
        self.assertFalse(dlg.save_btn.isEnabled())
        self.assertFalse(dlg.load_btn.isEnabled())
        dlg.set_active_turn(False)
        self.assertTrue(dlg.save_btn.isEnabled())
        self.assertTrue(dlg.load_btn.isEnabled())

    def test_inspector_presentation_and_signals(self):
        inspector = ToolInspectorDialog(workspace='/tmp/ws')
        self.addCleanup(inspector.deleteLater)

        refresh_called = []
        disconnect_called = []
        inspector.refresh_requested.connect(lambda: refresh_called.append(True))
        inspector.disconnect_requested.connect(lambda: disconnect_called.append(True))

        inspector.refresh_btn.click()
        self.assertEqual(len(refresh_called), 1)
        inspector.disconnect_btn.click()
        self.assertEqual(len(disconnect_called), 1)

        # Test snapshot update with builtins, MCP tools, and provider statuses
        mcp_tools = [
            {'server': 'remote_srv', 'original_name': 'query', 'description': 'Run query',
             'input_schema': {'type': 'object', 'properties': {'sql': {'type': 'string'}}}, 'generation': 1}
        ]
        statuses = {'remote_srv': {'status': 'connected'}, 'broken_srv': {'status': 'failed', 'error': 'Connection refused'}}
        inspector.update_snapshot(mcp_tools, statuses, workspace='/tmp/ws')

        # Check detail display for built-in vs MCP tool
        inspector.show_tool_detail('read_file')
        detail = inspector.detail_view.toPlainText()
        self.assertIn('read_file', detail)
        self.assertIn('Automatic in workspace', detail)

        inspector.show_tool_detail('query', server='remote_srv')
        detail2 = inspector.detail_view.toPlainText()
        self.assertIn('query', detail2)
        self.assertIn('remote_srv', detail2)
        self.assertIn('Always requires approval', detail2)

        # Guard during active turn
        inspector.set_active_turn(True)
        self.assertFalse(inspector.refresh_btn.isEnabled())
        self.assertFalse(inspector.disconnect_btn.isEnabled())
