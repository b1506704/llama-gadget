# Llama Gadget

Small PyQt6 desktop gadget and utility for CachyOS to chat with, monitor, and toggle local `llama.cpp` inference profiles across multiple GPUs.

## Profiles

Switch profiles instantly via the header dropdown:

- **Services (Default, always-on)**: Qwen3.5-9B IQ4_XS pinned to **GPU 1** (RTX 3050), port `8080`, **128K context**. Managed by `systemd --user` (`llama-services.service`).
- **Agent GPU0 (Fast agentic workflow)**: Qwen3.5-9B IQ4_XS pinned to **GPU 0** (RTX 5060 Ti), port `8081`, **256K context** (native), `--jinja` enabled for tool calling and agentic tasks. Runs alongside the services tier with ~68 tok/s generation.
- **Agent 27B (Deep reasoning)**: Qwen3.8-27B UD-IQ4_XS text-only dual-GPU layer split across GPU 0 + GPU 1, port `8080`, **128K context**, `--jinja` enabled. Maximum reasoning depth and coding capability (~19 tok/s). Automatically parks the services tier while active and restores it when stopped.
- **Image Analysis (Vision)**: Qwen3.8-27B UD-IQ4_XS + `mmproj-F16` dual-GPU layer split across GPU 0 + GPU 1, port `8080`, 32K context. Supports image file attachment, image drag & drop, and direct clipboard image pasting (`Ctrl+V`). Automatically parks the services tier while active and restores it when switched away.

## Features

- **Profile switching & service toggle**: Start/Stop button and system tray icon (green = online, amber = loading, red = offline).
- **Live monitoring**:
  - Live generation speed (tok/s during streaming and final prompt/generation stats from `llama.cpp`).
  - Context tokens used vs context window limit (`n_ctx`), plus turn count.
  - Per-GPU hardware monitoring: GPU utilization %, VRAM used/total, and temperatures via `nvidia-smi`.
  - Endpoint URL and loaded model metadata (`n_params`, context size).
- **Editable Endpoint**: Modify the OpenAI-compatible endpoint URL directly using the ✎ button in the header or via the Settings tab. Persisted per profile with automatic URL sanitization and one-click reset to default.
- **Rich formatting**: Markdown rendering (tables, code blocks, lists) with per-message timestamps.
- **Persistent Chat History**: SQLite storage (`llama_gadget.db` in project root, gitignored). Browse, open, and delete past sessions.
- **Folder Context**: Smart git-aware folder scanner (skips binaries, node_modules, lockfiles, virtual environments). Priority ranking (docs → config → source) and interactive selection dialog sized to the available context window.
- **File & Image attachments**:
  - Text: `.txt`, `.md`, `.py`, `.json`, `.csv`, `.html`, `.docx`, `.pdf` (via `pdftotext` or `pypdf`).
  - Vision mode: `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.gif`, clipboard image paste.
- **Clipboard memory**: Automatically saves text copies to an SQLite ring buffer (last 50 clips). Quick menu via `📋 Clips` button or `Ctrl+Shift+V`.
- **Customizable System Prompts**: Saved per user (defaults to PC hostname) and per profile.
- **Local agent tools & MCP**: Workspace file reads/listings, approved file writes and bash commands, Model Context Protocol (MCP) server integration (stdio and Streamable HTTP), streamed tool calls, and automatic continuation to a final answer. Tool requests and results persist in chat history.
- **Chat Controls & Previews**:
  - Show tool details toggle (initially off): hide protocol request/result JSON to keep transcripts clean while keeping assistant prose and approval dialogs visible.
  - Exact-text response copying: Copy button copies assistant Markdown/code directly to clipboard with brief visual confirmation.
  - Stored-image previews: clickable thumbnails in composer and historical messages open a resizable viewer with fit-to-window and actual-size modes, decoded safely from stored data.
  - Tool inspector: browse available built-in and MCP tools, schemas, and provider statuses.
- **Gadget mode**: Frameless, translucent background, stays-on-top, draggable. Closing window minimizes to the system tray.

## Run
```bash
sudo pacman -S python-pyqt6   # or: pip install -r requirements.txt
./run.sh
./install.sh [--autostart]    # add application menu entry or start at login
```

## Using agent tools & MCP

Select **Agent GPU0** or **Agent 27B**, then check **Enable tools**. Tools start disabled. Built-in workspace tools require selecting **Workspace…**, while MCP tools can be used independently without a local workspace directory.

### Built-in workspace tools
- `read_file(path)` and `list_directory(path)` run automatically within the selected workspace. Paths that resolve outside it are rejected, including symlinks.
- `write_file(path, content)` shows the destination and complete proposed content for approval. It replaces the file atomically.
- `bash_command(command)` shows the exact command for approval. Approved commands run as your desktop user with the workspace as their working directory. They are **not sandboxed**.

### Model Context Protocol (MCP) tools
Configure external tools via **Settings → Configure MCP…** or inspect them via **Tools…**:
- Supports **stdio** (`command`, `args`, optional `env`, `cwd`) and **Streamable HTTP** (`url`, optional `headers`).
- Configuration is stored atomically at `~/.config/llama-gadget/mcp.json` with owner-only permissions (`0o600`).
- Environment variable expansion is supported via `${env:VARIABLE_NAME}` in `env` and `headers`. Unresolved variables raise a sanitized error. Config secrets and credentials are redacted from model context and error summaries.
- Explicit connection: launch or config changes do not auto-start servers. Click **Connect / Refresh** in the tool inspector to connect enabled servers.
- **Approval policy**: All MCP tool invocations require explicit user approval regardless of annotations. The approval dialog displays the original provider name, tool name, and formatted arguments.
- **Scope & Limitations**: Tools-only scope. OAuth login, legacy HTTP+SSE, resources, prompts, and server auto-installation are unsupported. Remote call cancellation is best-effort and does not guarantee reversing external side effects.
- Output is bounded to 64 KiB with truncation notices. Default per-call timeout is 60s (configurable between 1–300s).

### Approvals and session control
Choose **Deny** or close the approval dialog to refuse a call. Its denial is sent
back to the agent. **Stop** cancels the turn and terminates any running command or
MCP call; it does not undo completed side effects. Completed tool exchanges remain
in history after cancellation or a later model error. Starting a new session or
opening history waits for the active worker to finish before switching.

The endpoint must support OpenAI-compatible tool calls. Both agent profiles use
`--jinja`, as required by the [llama.cpp function-calling documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md).

## Tests

Use a Python interpreter with PyQt6 installed:

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
python -m compileall -q llama_gadget tests
```

Tests use temporary workspaces/databases and a local fake streaming server;
they do not require a model or GPU.
