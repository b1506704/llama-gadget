# AGENTS.md

Guidance for coding agents working in this repository.

## What this is

PyQt6 desktop gadget for CachyOS/Linux that chats with, monitors, and toggles local `llama.cpp` inference profiles across two NVIDIA GPUs. It is machine-specific by design: it shells out to `systemctl --user`, `systemd-run`, and `nvidia-smi`, and `llama_gadget/profiles.py` hardcodes paths to the companion project `~/Production/Image Description GGUF` (llama.cpp build + `start-server.sh`), overridable via `LLAMA_PROJECT`, `QWEN_MODEL_DIR`, and `AGENT_GPU0_CTX` env vars. Don't "port" these to be cross-platform.

## Commands

```bash
./run.sh                                        # run the app (prefers .venv/bin/python)
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q                  # full test suite
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_tools.py -q     # one module
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v     # unittest also works
python -m compileall -q llama_gadget tests      # syntax check
./install.sh [--autostart]                      # desktop menu entry / autostart
```

- Use a Python interpreter with PyQt6 installed (the repo's `.venv`, or `sudo pacman -S python-pyqt6`). Dependencies: PyQt6, `mcp>=2.3.0,<3`, `jsonschema>=4.23,<5`.
- Tests need no GPU or model: `tests/support.py` ships a fake streaming OpenAI-compatible SSE server; tests use temp workspaces/databases and run under `QT_QPA_PLATFORM=offscreen`.
- Known-flaky MCP tests (`test_mcp_client.py`, `test_tool_catalog.py` MCP integration) can fail intermittently even on a clean tree — confirm against a stashed baseline before treating a failure as yours.
- There is no linter/formatter config; style is hand-maintained. Match the file you are editing.

## Architecture

Three threads cooperate: the Qt UI thread (`Gadget` in `app.py`), one `ChatWorker(QThread)` per turn (`chat.py`), and a dedicated asyncio thread inside `MCPManager` (`mcp_client.py`). Communication is Qt signals downward/strands upward, plus a condition variable for the approval round-trip.

**Turn lifecycle.** `Gadget.send()` builds the payload (system prompt, folder context, attachments) and starts a `ChatWorker`, which runs the agentic loop itself: stream SSE from `/v1/chat/completions` via raw `http.client` (no requests/aiohttp), collect streamed tool-call fragments, execute them, append `assistant`+`tool` messages, and repeat — bounded by `max_rounds=16` / `max_executions=32`. Protocol messages stream back to the UI via `exchange_completed`/`message_completed`; `Gadget._on_exchange` persists each complete exchange to SQLite in one transaction **on the UI thread** (`Database.add_exchange`). Tool protocol data lives in the `messages.metadata` JSON column.

**Approval bridge.** `ChatWorker._approve()` emits `approval_requested` and blocks on a condition variable; the UI's modal calls `worker.decide(approval_id, bool)`. Each approval gets a unique `call_id:sequence` identity so a late answer can't authorize a different invocation. YOLO mode auto-approves in the UI. Cancel propagates via `threading.Event` + socket shutdown; `Stop` never undoes completed side effects.

**Profiles → systemd.** `profiles.py` defines four frozen `Profile`s. `services` (Qwen3.5-9B, now text+vision via a Qwen3.5-9B mmproj that runs on CPU — `--no-mmproj-offload` keeps the 8 GB RTX 3050 inside its VRAM budget) is a persistent unit (`llama-services.service`, `managed=True`) installed by the companion project; the others run as transient `systemd-run --user` units. Each profile has a `conflicts` tuple: dual-GPU profiles (`agent-27b`, `vision`) park `services`/`agent-gpu0` and restore them when stopped — conflict handling is the caller's job (`Gadget._on_profile`/`toggle_service`). `service.py` provides health (`/health`), context size (`/props`), model info (`/v1/models`), and `nvidia-smi` stats polled by a UI timer. Vision support is purely the `Profile.vision` flag: the attach button, paste/drop handling, and `model_content()` image parts all key off it.

**Tool routing.** `ToolRegistry` (`tools.py`) = built-in workspace tools; `ToolCatalog` (`tool_catalog.py`) is the frozen per-turn router that wraps it plus MCP tools from an `MCPManager` snapshot. MCP tool names are rewritten to `mcp_<server>_<name>_<sha256-16>`; input schemas must be object schemas with local-only refs and are enforced with jsonschema. Approval policy lives in the catalog: built-ins per `DEFINITIONS`, every MCP call always. `ChatWorker` enforces approval and re-prepares arguments before execution (`prepared=True` refuses if the target changed between approval and execution) — the registry's `execute()` deliberately does not check approval itself.

**Workspace-tool hardening** (`tools.py` — don't weaken these): paths must resolve inside the workspace; file access walks components from `/` with `O_NOFOLLOW` directory descriptors so symlink swaps can't escape; writes are temp-file + `os.replace` atomic; output bounded to 64 KiB; bash runs in its own process group with 60s timeout and group `SIGKILL` (also kills background descendants holding pipes).

**MCP client.** `MCPManager` owns an asyncio loop on a daemon thread; one "actor" task per server (stdio or Streamable HTTP only). A `generation` counter stamped on tools/clients invalidates everything on reconnect — a call referencing a stale generation fails instead of hitting a server the user disconnected. Config is strictly validated, stored atomically at `~/.config/llama-gadget/mcp.json` (0o600), `${env:VAR}` expanded at resolve time, and resolved secret values redacted from tool descriptions, errors, and results before they can reach the model. Servers connect only on explicit Connect/Refresh, never on launch or config save.

**Persistence** (`db.py`): SQLite at repo root (`llama_gadget.db`, gitignored). Tables: `users` (system prompt keyed by hostname), `sessions`, `messages` (with `attachments` and `metadata` JSON), `clips` (50-entry clipboard ring buffer).

**Supporting modules.** `files.py` parses attachments (incl. `.docx` via zip+XML, `.pdf` via `pdftotext`/`pypdf`) and does the git-aware folder-context scan; `images.py` downscales vision attachments to ≤1568px, flattens alpha onto white, and embeds as JPEG data URLs; `chat_ui.py`/`tool_ui.py` hold dialogs and per-message widgets.

## Design docs

Feature specs, plans, and execution logs live in `docs/superpowers/{specs,plans,execution}/` — check the matching dated doc before extending tool calling or MCP behavior.
