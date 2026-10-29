"""Tool inspection and MCP configuration dialogs."""
import copy
import json
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QTextBrowser, QTreeWidget, QTreeWidgetItem,
    QSplitter, QFileDialog, QWidget
)

from .chat_ui import ErrorRecoveryBanner, LifecycleStatusBar, SkeletonPlaceholder
from .lifecycle import SurfaceLifecycleController, SurfaceState
from .mcp_config import EXAMPLE, parse_config, save_config, load_config
from .tools import DEFINITIONS


class MCPConfigDialog(QDialog):
    config_saved = pyqtSignal(dict)

    def __init__(self, config_path, current_config=None, parent=None):
        super().__init__(parent)
        self.config_path = Path(config_path)
        self.setWindowTitle("MCP Configuration")
        self.resize(600, 500)

        lay = QVBoxLayout(self)

        lay.addWidget(QLabel("<b>MCP Servers Configuration (JSON)</b>"))
        desc = QLabel("Configure local (stdio) and remote (Streamable HTTP) Model Context Protocol servers. "
                      "Saving changes configuration atomically. Changes take effect on next Connect/Refresh.")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        self.editor = QPlainTextEdit()
        if current_config is not None:
            text = json.dumps(current_config, indent=2)
        else:
            try:
                text = json.dumps(load_config(self.config_path), indent=2)
            except Exception:
                text = json.dumps(EXAMPLE, indent=2)
        self.editor.setPlainText(text)
        lay.addWidget(self.editor, 1)

        self.status_lbl = QLabel()
        self.status_lbl.setWordWrap(True)
        lay.addWidget(self.status_lbl)

        btn_row = QHBoxLayout()
        self.validate_btn = QPushButton("Validate")
        self.validate_btn.clicked.connect(self._validate)
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._save)
        self.load_btn = QPushButton("Load from file…")
        self.load_btn.clicked.connect(self._load)
        self.example_btn = QPushButton("Insert example")
        self.example_btn.clicked.connect(self._insert_example)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)

        btn_row.addWidget(self.validate_btn)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.load_btn)
        btn_row.addWidget(self.example_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.close_btn)
        lay.addLayout(btn_row)

    def _validate(self):
        try:
            parse_config(self.editor.toPlainText())
            self.status_lbl.setStyleSheet("color: #2ea043;")
            self.status_lbl.setText("Configuration is valid.")
            return True
        except Exception as exc:
            self.status_lbl.setStyleSheet("color: #f85149;")
            self.status_lbl.setText(f"Error: {exc}")
            return False

    def _save(self):
        try:
            config = parse_config(self.editor.toPlainText())
            save_config(self.config_path, config)
            self.status_lbl.setStyleSheet("color: #2ea043;")
            self.status_lbl.setText("Configuration Saved successfully.")
            self.config_saved.emit(config)
        except Exception as exc:
            self.status_lbl.setStyleSheet("color: #f85149;")
            self.status_lbl.setText(f"Error: {exc}")

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load MCP configuration", str(Path.home()), "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8")
            config = parse_config(content)
            self.editor.setPlainText(json.dumps(config, indent=2))
            self.status_lbl.setStyleSheet("color: #2ea043;")
            self.status_lbl.setText(f"Loaded from {Path(path).name}.")
        except Exception as exc:
            self.status_lbl.setStyleSheet("color: #f85149;")
            self.status_lbl.setText(f"Error loading file: {exc}")

    def _insert_example(self):
        self.editor.setPlainText(json.dumps(EXAMPLE, indent=2))
        self.status_lbl.setStyleSheet("color: #8b949e;")
        self.status_lbl.setText("Inserted example configuration.")

    def set_active_turn(self, active: bool):
        self.save_btn.setEnabled(not active)
        self.load_btn.setEnabled(not active)
        if active:
            self.status_lbl.setStyleSheet("color: #8b949e;")
            self.status_lbl.setText("Turn active. Configuration cannot be saved or loaded during a turn.")


class ToolInspectorDialog(QDialog):
    refresh_requested = pyqtSignal()
    disconnect_requested = pyqtSignal()
    open_settings_requested = pyqtSignal(str)

    def __init__(self, workspace=None, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self._mcp_tools = []
        self._statuses = {}
        self.setWindowTitle("Tool Inspector")
        self.resize(750, 520)

        lay = QVBoxLayout(self)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("<b>Available Tools and MCP Providers</b>"))
        top_row.addStretch()
        self.refresh_btn = QPushButton("Connect / Refresh")
        self.refresh_btn.setToolTip("Connect enabled MCP providers or refresh tool metadata")
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setToolTip("Disconnect all active MCP providers")
        self.disconnect_btn.clicked.connect(self.disconnect_requested.emit)
        top_row.addWidget(self.refresh_btn)
        top_row.addWidget(self.disconnect_btn)
        lay.addLayout(top_row)

        # Provider catalog lifecycle (Loading while Connect / Refresh / Disconnect runs).
        self.lifecycle = SurfaceLifecycleController(self)
        self.provider_status = LifecycleStatusBar("Providers")
        self.provider_status.bind(self.lifecycle)
        lay.addWidget(self.provider_status)

        # Provider failures recover inline (Retry / Open Settings / Copy Diagnostics).
        self.error_banner = ErrorRecoveryBanner()
        self.error_banner.open_settings_requested.connect(self.open_settings_requested.emit)
        self.error_banner.dismissed.connect(self.lifecycle.dismiss_error)
        lay.addWidget(self.error_banner)

        # Active chat turn (shared controller from the main window) with its own Stop.
        turn_row = QHBoxLayout()
        self.turn_status = LifecycleStatusBar("Turn")
        self.turn_activity = QLabel()
        self.turn_activity.setTextFormat(Qt.TextFormat.PlainText)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setToolTip("Stop the active turn (completed tool results are kept)")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_turn)
        turn_row.addWidget(self.turn_status)
        turn_row.addWidget(self.turn_activity, 1)
        turn_row.addWidget(self.stop_btn)
        lay.addLayout(turn_row)
        self.turn_controller = None

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_widget = QWidget()
        left_lay = QVBoxLayout(left_widget)
        left_lay.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Tools & Providers", "Status / Type"])
        self.tree.setColumnWidth(0, 200)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        self.skeleton = SkeletonPlaceholder(lines=6)
        left_lay.addWidget(self.tree, 1)
        left_lay.addWidget(self.skeleton)
        left_lay.addStretch()
        splitter.addWidget(left_widget)
        self.lifecycle.state_changed.connect(self._on_provider_state)

        right_widget = QWidget()
        right_lay = QVBoxLayout(right_widget)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.addWidget(QLabel("<b>Tool / Provider Details</b>"))
        self.detail_view = QTextBrowser()
        self.detail_view.setOpenExternalLinks(False)
        self.detail_view.setReadOnly(True)
        right_lay.addWidget(self.detail_view, 1)
        splitter.addWidget(right_widget)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        lay.addWidget(splitter, 1)

        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        bottom_row.addWidget(self.close_btn)
        lay.addLayout(bottom_row)

        self.update_snapshot((), {}, workspace=self.workspace)

    def set_active_turn(self, active: bool):
        self.refresh_btn.setEnabled(not active)
        self.disconnect_btn.setEnabled(not active)
        self.error_banner.set_retry_blocked(active)

    def bind_turn(self, controller):
        """Mirror the main window's turn lifecycle here; Stop cancels it."""
        if self.turn_controller is not None:
            self.turn_controller.state_changed.disconnect(self._on_turn_state)
        self.turn_controller = controller
        self.turn_status.bind(controller)
        controller.state_changed.connect(self._on_turn_state)
        self._on_turn_state(controller.state)

    def _on_turn_state(self, state):
        active = state in (SurfaceState.LOADING, SurfaceState.STREAMING)
        self.stop_btn.setEnabled(active)
        if not active:
            self.turn_activity.clear()

    def _stop_turn(self):
        if self.turn_controller is not None:
            self.turn_controller.cancel_active()

    def set_turn_activity(self, text):
        self.turn_activity.setText(text)

    def begin_provider_refresh(self):
        """Connect / Refresh / Disconnect started: skeleton replaces the tree until the snapshot arrives."""
        self.error_banner.clear_error()  # a new attempt supersedes the previous failure
        return self.lifecycle.start_operation()

    def fail_provider_refresh(self, error=None):
        """Provider operation failed: Loading -> Error, with the UiError shown inline."""
        self.lifecycle.fail()
        if error is not None:
            self.error_banner.present_error(error)

    def _on_provider_state(self, state):
        loading = state is SurfaceState.LOADING
        self.skeleton.setVisible(loading)
        self.tree.setVisible(not loading)

    def showEvent(self, event):
        super().showEvent(event)
        self.provider_status.refresh()  # show the latest progress when re-opened
        self.turn_status.refresh()

    def update_snapshot(self, tools=(), statuses=(), workspace=None):
        if workspace is not None:
            self.workspace = workspace
        self._mcp_tools = copy.deepcopy(tools)
        self._statuses = copy.deepcopy(statuses)

        self.tree.clear()

        # 1. Built-in tools
        builtins_root = QTreeWidgetItem(self.tree, ["Built-in Tools", f"{len(DEFINITIONS)} tools"])
        builtins_root.setExpanded(True)
        for name, (desc, keys, req_appr) in DEFINITIONS.items():
            avail = "Available" if self.workspace else "Needs workspace"
            item = QTreeWidgetItem(builtins_root, [name, avail])
            item.setData(0, Qt.ItemDataRole.UserRole, {"type": "builtin", "name": name})

        # 2. MCP Tools
        mcp_root = QTreeWidgetItem(self.tree, ["MCP Tools", f"{len(self._mcp_tools)} tools"])
        mcp_root.setExpanded(True)
        for tool in self._mcp_tools:
            item = QTreeWidgetItem(mcp_root, [tool["original_name"], tool["server"]])
            item.setData(0, Qt.ItemDataRole.UserRole, {"type": "mcp", "tool": tool})

        # 3. MCP Providers
        providers_root = QTreeWidgetItem(self.tree, ["MCP Providers", f"{len(self._statuses)} servers"])
        providers_root.setExpanded(True)
        for name, st in sorted(self._statuses.items()):
            status_text = st.get("status", "configured")
            item = QTreeWidgetItem(providers_root, [name, status_text])
            item.setData(0, Qt.ItemDataRole.UserRole, {"type": "provider", "name": name, "status": st})
        self.lifecycle.complete()  # content is ready: skeleton gives way to the tree

    def _on_tree_selection(self):
        items = self.tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        dtype = data.get("type")
        if dtype == "builtin":
            self.show_tool_detail(data["name"], server="Built-in")
        elif dtype == "mcp":
            tool = data["tool"]
            self.show_tool_detail(tool["original_name"], server=tool["server"])
        elif dtype == "provider":
            self._show_provider_detail(data["name"], data["status"])

    def show_tool_detail(self, name, server="Built-in"):
        if server == "Built-in" and name in DEFINITIONS:
            desc, keys, req_appr = DEFINITIONS[name]
            schema = {
                "type": "object",
                "properties": {k: {"type": "string"} for k in keys},
                "required": list(keys),
                "additionalProperties": False,
            }
            avail = ("Available in workspace: " + str(self.workspace)) if self.workspace else "Unavailable (requires selected workspace)"
            appr = "Approval required" if req_appr else "Automatic in workspace"
            text = (
                f"### Built-in Tool: `{name}`\n\n"
                f"- **Provider:** Built-in\n"
                f"- **Approval policy:** {appr}\n"
                f"- **Availability:** {avail}\n"
                f"- **Description:** {desc}\n\n"
                f"#### Argument Schema\n```json\n{json.dumps(schema, indent=2)}\n```"
            )
            self.detail_view.setMarkdown(text)
            return

        for tool in self._mcp_tools:
            if tool["server"] == server and tool["original_name"] == name:
                desc = tool.get("description", "No description provided.")
                schema = tool.get("input_schema", {})
                avail = "Available"
                appr = "Always requires approval"
                text = (
                    f"### MCP Tool: `{name}`\n\n"
                    f"- **Provider:** {server}\n"
                    f"- **Approval policy:** {appr}\n"
                    f"- **Availability:** {avail}\n"
                    f"- **Description:** {desc}\n\n"
                    f"#### Argument Schema\n```json\n{json.dumps(schema, indent=2)}\n```"
                )
                self.detail_view.setMarkdown(text)
                return

        # If not found in snapshot
        self.detail_view.setMarkdown(f"Tool `{name}` ({server}) is currently unavailable.")

    def _show_provider_detail(self, name, status_info):
        status = status_info.get("status", "configured")
        error = status_info.get("error", "")
        error_section = f"\n- **Error:** {error}\n" if error else ""
        text = (
            f"### MCP Provider: `{name}`\n\n"
            f"- **Status:** {status}"
            f"{error_section}"
        )
        self.detail_view.setMarkdown(text)
