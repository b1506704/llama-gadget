import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PyQt6.QtWidgets import QApplication, QPushButton
from PyQt6.QtGui import QImage, QColor
from PyQt6.QtCore import QSettings

from llama_gadget.app import Gadget
from llama_gadget.chat_ui import ImagePreviewDialog
from llama_gadget.tool_ui import ToolInspectorDialog, MCPConfigDialog
from llama_gadget.images import encode_image
from llama_gadget import profiles

app = QApplication.instance() or QApplication([])

out_dir = Path('docs/screenshots')
out_dir.mkdir(parents=True, exist_ok=True)

temp = TemporaryDirectory()
root = Path(temp.name)
settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
settings.setValue('db_path', str(root / 'chat.db'))
settings.setValue('profile', 'agent-gpu0')

gadget = Gadget()
gadget.timer.stop()
gadget.workspace = str(root)
gadget.profile = profiles.PROFILES['agent-gpu0']
gadget._update_controls()
gadget.tools_cb.setChecked(True)

# Sample image
sample_img = QImage(128, 96, QImage.Format.Format_RGB32)
sample_img.fill(QColor('#4a90e2'))
attachment = encode_image(sample_img, 'diagram.png')

# 1. Composer thumbnail
gadget.attachments.append(attachment)
gadget.attach_lbl.set_attachments(gadget.attachments)
gadget.attach_clear.setVisible(True)
gadget.resize(420, 660)
gadget.show()
app.processEvents()
gadget.grab().save(str(out_dir / '1_composer_thumbnail.png'))

# Add messages
gadget.msgs = [
    {'role': 'user', 'content': 'Please inspect our test files and generate a diagram.', 'ts': '2026-10-05 08:00:00', 'attachments': [attachment]},
    {'role': 'assistant', 'content': 'I will read the test file first.', 'ts': '2026-10-05 08:00:01', 'metadata': {'tool_calls': [{'id': 'call_1', 'function': {'name': 'read_file', 'arguments': '{"path": "test.txt"}'}}]}},
    {'role': 'tool', 'content': '{"ok": true, "output": "Test content file data."}', 'ts': '2026-10-05 08:00:02', 'metadata': {'tool_call_id': 'call_1', 'name': 'read_file'}},
    {'role': 'assistant', 'content': 'Here is the summary of the test file.\n```python\nprint("Done")\n```', 'ts': '2026-10-05 08:00:03', 'attachments': []},
]
gadget._render()
app.processEvents()

# 2. Default chat (tool details hidden)
gadget.show_tools_cb.setChecked(False)
gadget._render()
app.processEvents()
gadget.grab().save(str(out_dir / '2_default_chat_hidden_tools.png'))

# 3. Expanded details (Show tool details checked)
gadget.show_tools_cb.setChecked(True)
gadget._render()
app.processEvents()
gadget.grab().save(str(out_dir / '3_expanded_tool_details.png'))

# 4. Copy feedback
buttons = [b for b in gadget.view.findChildren(QPushButton) if b.text() == 'Copy']
if buttons:
    buttons[-1].click()
    app.processEvents()
    gadget.grab().save(str(out_dir / '4_copy_feedback.png'))

# 5. Historical image preview
preview_dlg = ImagePreviewDialog(attachment)
preview_dlg.resize(480, 360)
preview_dlg.show()
app.processEvents()
preview_dlg.grab().save(str(out_dir / '5_image_preview.png'))
preview_dlg.close()

# 6. Tool Inspector
inspector = ToolInspectorDialog(workspace=str(root))
mcp_tools = [
    {'server': 'sqlite_mcp', 'original_name': 'execute_query', 'description': 'Run read-only SQL query on database',
     'input_schema': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}, 'generation': 1}
]
statuses = {
    'sqlite_mcp': {'status': 'connected'},
    'web_search': {'status': 'configured'},
    'failing_remote': {'status': 'failed', 'error': 'Connection timed out after 30s'}
}
inspector.update_snapshot(mcp_tools, statuses, workspace=str(root))
inspector.show_tool_detail('read_file')
inspector.resize(750, 520)
inspector.show()
app.processEvents()
inspector.grab().save(str(out_dir / '6_tool_inspector.png'))
inspector.close()

# 7. MCP Config validation error
config_dlg = MCPConfigDialog(root / 'mcp.json')
config_dlg.editor.setPlainText('{\n  "mcpServers": {\n    "bad": {\n      "command": "python",\n      "timeout": 9999\n    }\n  }\n}')
config_dlg.validate_btn.click()
config_dlg.resize(600, 500)
config_dlg.show()
app.processEvents()
config_dlg.grab().save(str(out_dir / '7_config_validation_error.png'))
config_dlg.close()

# Clean up
gadget.close()
temp.cleanup()
print("Screenshots generated successfully in docs/screenshots/")
