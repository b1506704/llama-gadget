# Tool calling implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task. Execute inline unless the user chooses delegation.

**Goal:** Let desktop chat execute approved local tools and continue to a final answer.

**Architecture:** A workspace-scoped registry owns validation and execution. ChatWorker owns streaming and the execution loop. The UI owns approval dialogs, transcript rendering, and SQLite persistence.

**Tech stack:** Python standard library, PyQt6, unittest, SQLite.

**Spec:** [Approved design](../specs/2026-10-05-tool-calling-design.md).

## Global constraints

- Tools are opt-in and require an explicitly selected workspace.
- File tools reject resolved paths outside the workspace; approved shell commands are not sandboxed.
- Read files and command output are capped at 64 KiB; listings at 1,000 entries; writes at 1 MiB.
- Every shell command and write requires approval; dialog dismissal denies.
- Commands time out after 60 seconds and cancellation terminates their process group.
- Each turn allows at most 16 completion rounds and 32 tool executions.
- SQLite operations and dialogs run on the UI thread; no GPU is required for automated tests.

## Review focus

- A write through a symlink must not escape the selected workspace.
- An interrupted stream must not execute an otherwise plausible partial tool call.
- Canceling an approval must wake the worker without approving a later request.
- An error after a side effect must retain the user turn and completed exchanges.
- Reopened history must preserve tool-call IDs without treating results as assistant prose.

## Task 1: Workspace-scoped tools registry

**Files:** Create `llama_gadget/tools.py` and `tests/test_tools.py`.

**Interfaces:** `ToolRegistry(workspace)` exposes `schemas() -> list`, `prepare(name, arguments) -> dict`, `requires_approval(name) -> bool`, and `execute(name, arguments, cancel_event) -> dict`. Preparation validates and resolves arguments; execution revalidates paths immediately before use. Results contain `ok`, output/error, and tool-specific metadata.

- [x] Write failing unittest cases using `TemporaryDirectory` for actual reads, writes, listings, invalid argument JSON, missing/extra keys, traversal, symlink escape, and oversized inputs.

```python
with TemporaryDirectory() as root:
    registry = ToolRegistry(root)
    Path(root, 'note.txt').write_text('hello')
    result = registry.execute('read_file', {'path': 'note.txt'}, Event())
    self.assertTrue(result['ok'])
    self.assertEqual(result['output'], 'hello')
    self.assertTrue(registry.requires_approval('write_file'))
    self.assertTrue(registry.requires_approval('bash_command'))
```

- [x] Run `python -m unittest discover -s tests -p test_tools.py -v` and confirm missing registry behavior fails.
- [x] Implement the four function schemas. Reject unknown arguments, non-string values, binary/non-UTF-8 reads, and outside-workspace paths. Bound file reads before decoding; sort bounded nonrecursive listings; use a temporary sibling and `os.replace` for writes with cleanup on failure.
- [x] Implement bash with `Popen(['bash', '-c', command], cwd=workspace, start_new_session=True)`. Drain pipes without accumulating unbounded output. Poll cancellation and timeout; kill the process group and reap the child. Return bounded stdout/stderr, exit code, truncation, and cancellation/timeout status.
- [x] Add actual command tests for success, failure, output beyond 64 KiB, cancellation, timeout with a short test timeout, and no orphan process after cancellation. Re-run registry tests to green.

## Task 2: Streamed calls and worker execution loop

**Files:** Modify `llama_gadget/chat.py`; create `tests/test_chat.py` and a reusable local HTTP streaming fixture in `tests/support.py`.

**Interfaces:** Preserve `token`, `stats`, `finished_ok`, and `failed`. Add `approval_requested(call_id, name, arguments)`, `exchange_completed(list)` for an assistant call message plus all results, and `message_completed(dict)` for final assistant messages. Add `decide(call_id, approved)` and a cancellation signal. Optional registry constructor argument keeps ordinary chat tools disabled.

- [x] Write a fake-endpoint test that records request bodies, streams a fragmented read call, receives its result, and returns a final answer.

```python
worker = ChatWorker([{'role': 'user', 'content': 'Read note.txt'}],
                    base_url=server.url, registry=ToolRegistry(root))
worker.run()
self.assertEqual(server.requests[1]['messages'][-1]['role'], 'tool')
self.assertEqual(server.requests[1]['messages'][-1]['tool_call_id'], 'call_1')
self.assertIn('tools', server.requests[0])
```

- [x] Confirm this fails before upgrading the worker.
- [x] Accumulate calls by index across SSE chunks. Require complete call IDs/names/arguments and an acceptable finish reason before executing. Reject duplicate IDs, malformed data, missing termination, and truncated tool calls. Keep text and usage streaming working with tools disabled.
- [x] Implement sequential tool rounds. Append the exact assistant call message, execute or deny each call, and append matching tool messages. Emit only complete call/result groups for persistence. Enforce round and execution limits before exceeding them.
- [x] Implement approval with a condition/event and call identity, waking on `decide` or `cancel`. Cancellation prevents new requests/executions and propagates to the executor. Bound network waits and retain cancellation checks while reading.
- [x] Add tests for multiple calls, two rounds, denial without a write, approval followed by a write, malformed JSON, unknown tools, usage-only chunks, normal chat, abrupt EOF, limits, and cancellation while awaiting approval. Run all tests to green.

## Task 3: Durable protocol history

**Files:** Modify `llama_gadget/db.py`; create `tests/test_db.py`.

**Interfaces:** Extend `add_message` with optional metadata; `load_messages` returns metadata. Add `add_exchange(sid, messages) -> list` to persist an assistant call and its tool results atomically. Existing callers and attachment records remain compatible.

- [x] Create an old-format SQLite file and assert opening it migrates without losing old records. Write failing round-trip and transaction tests.

```python
saved = db.add_exchange(sid, [
    {'role': 'assistant', 'content': '', 'metadata': {'tool_calls': calls}},
    {'role': 'tool', 'content': result, 'metadata': {'tool_call_id': 'call_1'}},
])
self.assertEqual(db.load_messages(sid)[-1]['metadata']['tool_call_id'], 'call_1')
```

- [x] Run database tests to confirm missing metadata/group persistence fails.
- [x] Add the metadata column only when missing, use JSON defaults for old records, and insert exchanges within one SQLite transaction with rollback on failure. Return saved IDs/timestamps for UI use.
- [x] Verify reopen, migration idempotence, and rollback with the full suite.

## Task 4: Desktop controls, approval, and lifecycle

**Files:** Modify `llama_gadget/app.py`; create `tests/test_app_tools.py`.

**Interfaces:** The gadget creates a registry from the selected workspace only when tools are enabled. Worker callbacks carry worker identity; stale callbacks cannot write history or approve calls. `_payload` replays tool metadata alongside role/content.

- [x] Write offscreen Qt tests for protocol replay, denial dialog behavior, and cancellation retention before changing the UI. Use temporary QSettings and SQLite storage; disable monitoring in the test fixture rather than introducing production test flags.
- [x] Add Enable tools, workspace chooser/display, and Stop controls. Disable workspace/tool changes during a turn and require agent profiles plus a workspace for enabling tools. Add workspace/tool instructions to the system message when enabled.
- [x] Connect approval requests to a UI-thread dialog with exact resolved path/content or command and Approve/Deny. Default to Deny. Capture the originating worker and call identity; close pending dialogs on cancellation.
- [x] Persist and render complete exchanges separately from assistant text. Clear intermediate reply buffers when an assistant call message is committed. Persist final text once. Replay assistant `tool_calls` and tool `tool_call_id`/name after history reload.
- [x] Replace unconditional user-turn deletion on failure with logic that retains completed exchanges and user turns after tool execution. Before any completed exchange, preserve existing retry behavior.
- [x] Keep workers referenced until QThread.finished. Stop/new session/history opening cancel the worker, disconnect stale turn callbacks, and wake approval waits. Wire application shutdown to cancel and join outstanding workers safely.
- [x] Run `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v` to green, including ordinary chat/attachment payload regressions and stale approval checks.

## Task 5: Verification and usage documentation

**Files:** Modify `README.md`; review all changed source and tests.

- [x] Document enabling tools, workspace scope, approval policy, Stop, bounded output, and unsandboxed approved shell execution.
- [x] Run the full offscreen suite, `python -m compileall -q llama_gadget tests`, and `git diff --check`.
- [x] Inspect the diff for schema compatibility, cancellation races, loss of transcript after side effects, and accidental unbounded reads/output.
- [x] Smoke-test the real desktop controls when a display is available. Check a tool-capable local endpoint if running; report explicitly if either integration check is unavailable.
- [x] Update plan checkboxes and report changed behavior, test results, and any remaining limitations. Do not deploy or commit without a user request.
