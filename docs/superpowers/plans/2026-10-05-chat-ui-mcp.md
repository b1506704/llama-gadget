# Chat controls and MCP implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan inline, task by task. The user previously selected inline execution; preserve that choice. A fresh read-only review follows implementation.

**Goal:** Make tool activity optional in the transcript, add response copying and image previews, and expose configurable MCP tools through the existing approved execution loop.

**Architecture:** A focused chat UI module owns message controls and previews. Separate configuration and MCP-client modules own persisted settings and background connections. A frozen composite registry combines available MCP tools with workspace tools for each turn.

**Tech stack:** Python 3.10+, PyQt6, SQLite, unittest, official MCP Python SDK 2.3, and jsonschema 4.

**Spec:** [Approved design](../specs/2026-10-05-chat-ui-mcp-design.md).

## Global constraints

- Tool details start hidden; their visibility never changes persisted messages or model context.
- Copy controls copy exact assistant text, including Markdown/code, without headers or tool data.
- Image previews use stored data URLs, work for clipboard/history attachments, and display the image actually sent to the model.
- No configured server starts or connects merely because the app launches or its editor opens.
- MCP calls require approval regardless of server annotations; discovery requires an explicit Connect/Refresh action.
- MCP-only turns need no workspace; built-in tools retain the selected-workspace requirement and their existing approval policies.
- Freeze available schemas and routing for each active turn. Disable configuration mutation/discovery during that turn.
- Support stdio and Streamable HTTP tools. OAuth, legacy HTTP+SSE, server installation, resources, and prompts are outside this iteration.
- MCP config uses `mcpServers` entries, app-owned `mcp.json`, atomic saves, owner-only permissions, and `${env:NAME}` expansion in env/headers.
- Connection/call timeout defaults to 60 seconds, accepted range 1–300. Bound model-facing MCP output to 64 KiB and label truncation.
- Never expose config secrets, transport dumps, or subprocess stderr to the model or inspector error summaries.
- Preserve existing cancellation, completed exchanges, history replay, profile behavior, and ordinary chat.
- Keep changes in the shared working tree. Do not commit, deploy, or modify imported config files as part of this task.

## Review focus

- Model-generated links resembling internal UI actions must not copy another response or open another attachment.
- Queued image actions after attachment removal/session switching must not target a different image.
- A canceled discovery or failed server must not erase healthy providers or leave an SDK task/process running.
- A config replacement must not send a pending approval to a different provider with the same name.
- Server exceptions/results containing configured secrets must be redacted before reaching history or the model.

## Task 1: Chat presentation, hidden tool details, and Copy

**Files:** Create `llama_gadget/chat_ui.py` and `tests/test_chat_ui.py`; modify rendering/control integration in `llama_gadget/app.py` and affected assertions in `tests/test_app_tools.py`.

**Interfaces:** `ChatTranscript(QWidget)` presents messages and exposes `verticalScrollBar()`, `toPlainText()`, `set_messages(messages, live_reply='', activity='', error='', show_tools=False)`, and `image_requested(dict)`. Each message has a stable identity derived from its saved ID or an explicit transient identity. `copy_response(identity)` uses the exact source message content. Keep message formatting functions separate from UI action dispatch.

- [x] Write failing offscreen tests showing that default rendering contains assistant prose/final text but neither request JSON nor tool result JSON. Assert visibility changes leave `_payload()` and SQLite records identical.

```python
before = gadget._payload()
gadget.show_tools_cb.setChecked(True)
self.assertIn('read_file', gadget.view.toPlainText())
gadget.show_tools_cb.setChecked(False)
self.assertNotIn('"tool_call_id"', gadget.view.toPlainText())
self.assertEqual(gadget._payload(), before)
```

- [x] Add failing tests with two assistant messages containing different code blocks. Click their actual Copy buttons and assert `QApplication.clipboard().text()` equals the selected message's original `content`; ensure user/tool-only messages have no Copy control.
- [x] Run `QT_QPA_PLATFORM=offscreen /usr/bin/python -m unittest tests.test_chat_ui -v`; confirm failures name missing controls/presentation behavior.
- [x] Implement message widgets with header Copy buttons and Qt Markdown bodies inside one scrollable transcript. Preserve the gadget's existing `verticalScrollBar()` usage and bottom-follow policy. Update the live body in place instead of recreating every saved message for each token. Recompute body height on resize; do not introduce nested vertical scrolling.
- [x] Keep assistant prose from call-bearing messages visible while hiding protocol details. When enabled, show tool name, full request arguments, result, and status with safe code fences. Escape user-provided labels. Internal Copy/image controls use widget signals bound to source identities; model Markdown links must not dispatch app actions.
- [x] Add Show tool details, persist it with QSettings default `False`, and preserve scroll position when toggled. Copy feedback uses a timer and does not mutate history. Keep approvals and compact running/waiting activity visible when details are hidden.
- [x] Test streaming updates, stale Copy identities, malicious action-like links, toggle persistence, reopened sessions, scroll lock while dragging, jump-to-bottom behavior, and plain chat/attachment regressions. Run the complete suite.

## Task 2: Composer and conversation image previews

**Files:** Extend `llama_gadget/chat_ui.py` and `llama_gadget/images.py`; modify composer attachment integration in `llama_gadget/app.py`; extend `tests/test_chat_ui.py` and create `tests/test_image_previews.py`.

**Interfaces:** `decode_attachment_image(attachment) -> QImage` validates the stored image data URL. `ImagePreviewDialog(attachment, parent)` supports fit-to-window and actual size. `AttachmentStrip.set_attachments(list)` creates source-bound thumbnail buttons; conversation message widgets emit `image_requested` with their own attachment snapshot.

- [x] Write failing tests using `encode_image(QImage(...), 'pasted.jpg')` and a saved/reloaded image message. Trigger composer and conversation thumbnail buttons and inspect the preview's actual image size/content.

```python
image = QImage(32, 16, QImage.Format.Format_RGB32)
image.fill(QColor('red'))
attachment = encode_image(image, 'pasted.jpg')
self.assertEqual(decode_attachment_image(attachment).size(), image.size())
```

- [x] Run image-preview tests and confirm missing decoder/dialog behavior fails.
- [x] Decode only supported image data URLs with validated base64 and readable image bytes. Show a plain error for malformed attachments. Use stored data rather than filesystem paths; preserve existing image payload construction and its downscaled/re-encoded contents.
- [x] Implement thumbnail buttons with filename/dimensions and a resizable dark-theme preview. Fit preserves aspect ratio; actual-size mode uses scrolling. Text attachment labels and Clear files retain their behavior.
- [x] Connect thumbnails to snapshots/stable identities, not mutable list indices. Test clipboard images, missing original files, reopened history, cleared attachments, session changes, corrupt base64, and repeated preview opening/closing. Inspect offscreen rendered previews and run the full suite.

## Task 3: MCP config parsing, persistence, and redaction

**Files:** Create `llama_gadget/mcp_config.py`, `tests/test_mcp_config.py`, and a disabled-only `docs/mcp.example.json`.

**Interfaces:** `parse_config(text) -> dict`, `load_config(path) -> dict`, `save_config(path, config)`, `resolve_server(entry, environ) -> dict`, and `redact(text, secret_values) -> str`. `default_config_path()` uses the Qt application config directory. Configuration parsing itself performs no network/subprocess work.

- [x] Write failing tests for both transport shapes, disabled entries, and atomic save/reload with file mode `0o600`. Preserve a valid file when validation fails and leave imported files untouched.

```python
config = parse_config('{"mcpServers":{"demo":{"command":"python","args":["server.py"],"disabled":true}}}')
save_config(path, config)
self.assertEqual(load_config(path), config)
self.assertEqual(path.stat().st_mode & 0o777, 0o600)
```

- [x] Run `/usr/bin/python -m unittest tests.test_mcp_config -v`; confirm missing parser/persistence failures.
- [x] Validate root/server shapes, exact allowed fields, name and transport requirements, string lists/maps, booleans, HTTP/HTTPS URLs, optional local `cwd`, and finite numeric timeout in 1–300. Reject credential-bearing URLs; use headers for auth. Treat timeout booleans as invalid numbers. Reject fields belonging to the other transport.
- [x] Expand `${env:NAME}` only in env/header values at connection time. Missing variables raise a sanitized per-server error; never overwrite the persisted template with expanded secrets. Combine explicit local env overrides with the intended subprocess environment without logging either.
- [x] Save using a temporary sibling with `0o600`, flush/fsync, `os.replace`, and cleanup on failure. Create the app-owned config location if needed; loading a missing file returns an empty server set. Redact resolved env/header values from bounded error summaries.
- [x] Add tests for invalid JSON/types, unknown fields, empty command, invalid URL, header injection, missing env references, permissions on replacement, malformed existing files, and secret-containing exceptions. Run the full suite.

## Task 4: SDK-backed MCP connection ownership and discovery

**Files:** Create `llama_gadget/mcp_client.py`, `tests/fixtures/mcp_server.py`, and `tests/test_mcp_client.py`; modify `requirements.txt`.

**Interfaces:** `MCPManager` owns a background asyncio loop. `connect(config) -> concurrent.futures.Future`, `discover() -> Future`, `disconnect() -> Future`, `snapshot() -> list[dict]`, and `close()` expose bounded lifecycle operations. `call(server, tool, arguments, cancel_event) -> dict` bridges a worker thread to an async invocation. Snapshots include generation, server, original name, description, input schema, annotations, and status without secret config fields.

- [x] Create a test environment using system Python with PyQt6 available. Add `mcp>=2.3.0,<3` and `jsonschema>=4.23,<5` to requirements; install into an isolated environment with system site packages for testing. Verify installed SDK signatures before using them. Document this setup rather than modifying system packages.
- [x] Write fixture servers and failing stdio/HTTP tests for explicit connection, discovery, typed invocation, errors, and disconnect. The fixtures record invocations and process IDs in test-owned locations and expose a controllable slow tool.

```python
manager = MCPManager()
manager.connect(config).result(timeout=5)
tools = manager.snapshot()
self.assertEqual(tools[0]['original_name'], 'add')
result = manager.call('fixture', 'add', {'a': 2, 'b': 3}, Event())
self.assertTrue(result['ok'])
self.assertEqual(result['structured_content']['result'], 5)
manager.close()
```

- [x] Confirm tests fail before client implementation.
- [x] Use `from mcp import Client, StdioServerParameters`. For HTTP, use `streamable_http_client(url, http_client=...)` with an SDK-compatible async HTTP client carrying resolved headers. SDK 2.3 uses `tool.input_schema`, `page.next_cursor`, `result.structured_content`, and `result.is_error`.
- [x] Start one actor task per provider. Each actor enters and exits its `async with Client(...)` context in the same task, processes requests through a queue, and owns closure. Call cancellation may cancel an invocation task but must not exit the transport from another task. Create/use/close HTTP clients within the owning loop.
- [x] Discovery loops `list_tools(cursor=...)` until `next_cursor is None`; reject repeated cursors and cap pathological discovery to 100 pages/1,000 tools per server with an explicit error. Refresh bypasses the SDK response cache. Isolate failures per server and retain tools from healthy providers.
- [x] Normalize text/structured/error results. Strip binary payloads from nontext content and represent their type/metadata without auto-fetching links. Bound the combined model-facing result to 64 KiB while retaining valid JSON and accurate success/error/truncation state. Redact configured secrets from error/result text before return.
- [x] Apply configured timeouts and cancel-event checks. Close pending requests and local subprocesses on disconnect/shutdown; bound shutdown waits and report errors plainly. Refuse SDK sampling/elicitation requests rather than allowing additional unapproved model or user actions.
- [x] Test stdio and HTTP, pagination, malformed schemas, unsupported features, one failed plus one healthy provider, output bounds, redaction, timeout, invocation cancellation, disconnect during discovery, and no subprocess after shutdown. Confirm construction/config loading starts no server. Run the full suite in the isolated environment.

## Task 5: Frozen composite registry and approval-loop integration

**Files:** Create `llama_gadget/tool_catalog.py` and `tests/test_tool_catalog.py`; extend integration cases in `tests/test_chat.py`; modify `llama_gadget/chat.py` only if needed for provider descriptions.

**Interfaces:** `ToolCatalog(local_registry, manager, snapshot)` implements existing worker methods `schemas()`, `prepare(name, arguments)`, `requires_approval(name)`, and `execute(name, arguments, cancel_event, prepared=False)`. `describe(name) -> dict` supplies sanitized provider/original-name/policy information to the UI; `workspace` is optional. The catalog stores an immutable generation-bound routing map for the turn.

- [x] Write failing tests for local plus MCP tools, MCP-only use, duplicate names across providers, non-ASCII/long names, native schema validation, and unavailable providers.

```python
catalog = ToolCatalog(None, manager, manager.snapshot())
name = catalog.schemas()[0]['function']['name']
self.assertTrue(catalog.requires_approval(name))
self.assertEqual(catalog.prepare(name, '{"a":2,"b":3}'), {'a': 2, 'b': 3})
self.assertRaises(ValueError, catalog.prepare, name, '{"a":"bad","b":3}')
```

- [x] Confirm tests fail with the current workspace-only registry.
- [x] Derive stable MCP function names from server and original tool names using a readable ASCII prefix plus a deterministic hash; enforce `[A-Za-z0-9_-]` and at most 64 characters. Check for actual collisions and fail affected entries clearly. Keep originals in inspector/approval data.
- [x] Validate input schemas with jsonschema and reject invalid/non-object input definitions before advertising. Validate JSON arguments against the full schema without imposing built-in required-string rules or resolving external schema references over the network.
- [x] Delegate local methods unchanged. Require approval for every MCP tool, including those marked read-only by annotations. Dispatch to the exact captured server/tool/generation; a retired generation returns an unavailable-tool result rather than rerouting or reconnecting silently.
- [x] Test through ChatWorker with the fake completion endpoint and fixture MCP server: no invocation before approval, denial produces a result without invocation, approved typed call feeds its output into the next request, canceled call preserves complete exchanges, and replacing config cannot reroute a pending call. Run the full suite.

## Task 6: Tool inspector, MCP editor, and gadget lifecycle

**Files:** Create `llama_gadget/tool_ui.py` and `tests/test_tool_ui.py`; modify `llama_gadget/app.py`; extend `tests/test_app_tools.py`.

**Interfaces:** `ToolInspectorDialog` shows built-in/MCP metadata and provider status, emits explicit Connect/Refresh/Disconnect requests, and updates from sanitized snapshots. `MCPConfigDialog` validates/imports/atomically saves configuration and emits `config_saved(dict)` only on successful save. Qt callbacks receive results through signals; future callbacks never access widgets directly from the background thread.

- [x] Write failing offscreen tests for inspection before chat, disabled providers, malformed config feedback, valid Save, preserved imported files, and available MCP tools without a workspace. Assert opening either dialog alone starts no provider process.
- [x] Confirm failures before wiring the new dialogs and controls.
- [x] Add a Tools button and Settings MCP configuration button. The inspector shows name, provider, description, argument schema, availability, and approval policy, with configured/disabled/connecting/connected/failed statuses. Refresh explicitly connects enabled providers and fetches metadata; Disconnect closes them. Keep long schemas selectable in a scrollable detail pane.
- [x] Use a multiline JSON editor with Validate, Save, Load from file, and example insertion. Saving changes config but does not connect servers automatically. Disable Save/discovery/routing changes while a turn is active, including dialogs opened before the turn began.
- [x] Lazily create MCPManager on explicit connection. Build a ToolCatalog on Send from the local workspace registry if selected and the current connected MCP snapshot. Enable agent tools when either provider class is available. Avoid adding local-workspace instructions to MCP-only turns.
- [x] Extend the existing approval dialog to display original MCP server/tool names and pretty JSON arguments. Retain resolved path/content for writes and exact command/workspace for bash. Use existing unique approval identities and denial on dialog dismissal.
- [x] Close old provider generations on valid config changes while idle. Preserve completed exchanges on cancellation/failure and shutdown. Cancel/join ChatWorkers before closing MCPManager; finish SDK contexts/subprocesses before stopping its loop. Guard callbacks after widget deletion or config replacement.
- [x] Test Qt responsiveness during slow discovery, healthy-provider visibility after a failure, config mutation guards, stale callbacks, hidden details with visible approvals, MCP-only turns, history replay, disconnect/shutdown cleanup, and original local tool behavior. Run the full suite.

## Task 7: Verification, screenshots, and usage docs

**Files:** Update `README.md`, the disabled config example, plan checkboxes, and a task-specific execution record under `docs/superpowers/execution/`.

- [x] Document Show tool details, exact-text copying, stored-image previews, tool inspection, explicit provider connection, config path/schema, stdio/HTTP examples, env interpolation, MCP approvals, limits, and best-effort remote cancellation. Explain the tools-only scope and unsupported OAuth/legacy transport clearly.
- [x] Run `QT_QPA_PLATFORM=offscreen <test-python> -m unittest discover -v`, `<test-python> -m compileall -q llama_gadget tests`, and `git diff --check`. Existing 36 tests must stay green, with updated rendering assertions for intentionally hidden details.
- [x] Render and inspect screenshots for default chat, expanded details, Copy feedback, composer thumbnail, historical image preview, inspector, and config validation errors. Verify readable contrast and usable controls at the gadget's small default size and a resized window.
- [x] If the local endpoint is available, complete a model-to-fixture-MCP-tool-to-final-answer smoke test with approval. Use temporary data; do not connect unrelated user servers. Check real desktop interaction if a display is available; report if only offscreen UI verification was possible.
- [x] Request one fresh read-only review of the final change, including both transports, generation/approval races, secrets in output, and image/control identities. Reproduce material findings with failing tests and fix them before the final full-suite run.
- [x] Record test commands/results, dependency setup, any design deviations, and remaining limitations. Mark tasks complete only after their verification passes. Report changed behavior and usage; leave code/config examples uncommitted for user review.

## Verified SDK references

The published `mcp` 2.3.0 wheel was inspected without installing product dependencies during planning. Its `Client`/stdio/HTTP signatures match the approach above. Recheck the installed wheel during Task 4 and keep within the declared major version.

- [MCP 2.3.0 package](https://pypi.org/project/mcp/2.3.0/)
- [Official client lifecycle and tool API](https://py.sdk.modelcontextprotocol.io/client/)
- [Official SDK source](https://github.com/modelcontextprotocol/python-sdk)
