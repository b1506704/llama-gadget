# Execution Record: Chat UI Controls, Previews, and MCP Integration

**Date:** 2026-10-05  
**Plan:** `docs/superpowers/plans/2026-10-05-chat-ui-mcp.md`  
**Status:** Complete

## Overview

Implemented full support for optional tool activity in the chat transcript, exact assistant response copying, safe image previews for composer and conversation history, and Model Context Protocol (MCP) tool integration with stdio and Streamable HTTP transports.

## Completed Tasks

1. **Task 1: Chat presentation, hidden tool details, and Copy**
   - Implemented `llama_gadget/chat_ui.py` with `ChatTranscript`.
   - Tool requests and results start hidden by default (`Show tool details` checkbox in UI).
   - Visibility changes affect only presentation; model payload and SQLite records remain untouched.
   - Header Copy button copies exact assistant text (including Markdown/code) with visual "Copied" feedback.

2. **Task 2: Composer and conversation image previews**
   - Implemented `ImagePreviewDialog` and thumbnail buttons in `AttachmentStrip` and message items.
   - Decodes images safely from stored data URLs (surviving deleted source files or clipboard pastes).
   - Fit-to-window and actual-size scrolling view modes.

3. **Task 3: MCP config parsing, persistence, and redaction**
   - Implemented `llama_gadget/mcp_config.py`.
   - Strict validation for `mcpServers` with stdio (`command`, `args`, `env`, `cwd`) and Streamable HTTP (`url`, `headers`).
   - Atomic configuration save with `0o600` file permissions to `~/.config/llama-gadget/mcp.json`.
   - Environment expansion `${env:VAR_NAME}` for env and headers.
   - Secret redaction prevents credentials and sensitive environment data from reaching history or the model.

4. **Task 4: SDK-backed MCP connection ownership and discovery**
   - Implemented `llama_gadget/mcp_client.py` using official MCP Python SDK (`Client`, `stdio_client`, `streamable_http_client`).
   - Background asyncio loop isolates client operations and preserves UI responsiveness.
   - Discovery with pagination bounds (up to 100 pages / 1,000 tools per server).
   - Model-facing tool output strictly bounded to 64 KiB with truncation notices.

5. **Task 5: Frozen composite registry and approval-loop integration**
   - Implemented `llama_gadget/tool_catalog.py` combining built-in workspace tools with active MCP snapshot tools.
   - Deterministic, collision-free naming for MCP tools adhering to `[A-Za-z0-9_-]` and length limits.
   - Per-turn frozen generation guarantees configuration edits cannot reroute pending calls.
   - Full approval loop integration in `ChatWorker`: approval prompt, rejection handling, and output feeding.

6. **Task 6: Tool inspector, MCP editor, and gadget lifecycle**
   - Implemented `ToolInspectorDialog` and `MCPConfigDialog` in `llama_gadget/tool_ui.py`.
   - Integrated into `llama_gadget/app.py`: "Tools…" button in chat header and "Configure MCP…" in Settings.
   - Active turn guards disable Save/Load/Refresh while a generation or tool call is in flight.
   - Decoupled MCP from local workspace: MCP tools can run without a selected workspace directory.
   - Extended approval dialog displays original server name, tool name, and formatted arguments.
   - Clean shutdown guarantees workers and SDK contexts are terminated before application exit.

7. **Task 7: Verification, screenshots, and usage docs**
   - Updated `requirements.txt` and `README.md` with full usage instructions, limits, and transport details.
   - Created example configuration at `docs/mcp.example.json`.
   - Generated and visually verified 7 offscreen screenshots in `docs/screenshots/`.
   - Ran complete test suite: 55 unit and integration tests passing.

## Test Results

```bash
QT_QPA_PLATFORM=offscreen /tmp/llama-gadget-mcp-venv/bin/python -m unittest discover tests -v
# Ran 55 tests in 17.856s
# OK

/tmp/llama-gadget-mcp-venv/bin/python -m compileall -q llama_gadget tests
git diff --check
# Clean, no errors
```
