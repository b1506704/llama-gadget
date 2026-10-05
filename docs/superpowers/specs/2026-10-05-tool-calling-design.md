# Tool calling for Llama Gadget

## Outcome

Implement the requested tools registry, streamed tool-call support, and execution
loop. A user can ask an agent to inspect files, edit files, or run a command. The
agent receives each execution result and continues until it produces a final
answer. Approval decisions happen in the desktop app.

This is a proposed design for review. Product code has not changed.

## Existing flow

`chat.py` sends one streaming completion request and emits text deltas.
`app.py` builds context, renders a single streamed reply, and saves that reply.
`db.py` stores role, content, attachments, and timestamps, but no tool metadata.
New-session cancellation currently drops the worker reference while it may still
be running. That lifecycle needs to be corrected when adding approval waits.

## Approach

Keep the request and execution loop in `ChatWorker`, with a separate registry in
`llama_gadget/tools.py`. Keep dialogs and SQLite writes on the UI thread. Use
signals for approval requests and completed protocol messages; use a
thread-safe response mechanism for approval decisions.

Alternatives considered:

- A UI-owned loop would reuse the existing one-request worker, but split turn
  state and cancellation across several workers and UI callbacks.
- An external agent framework would provide orchestration, but add a dependency
  and replace more of this small application's existing chat flow.

The worker-owned loop fits the current app with the fewest moving parts.

## Registry and execution policy

Each registered tool has an OpenAI-compatible function schema, an argument
validator, an executor, and an approval policy. Validate JSON and arguments before
requesting approval or touching the filesystem. Unknown tools and invalid
arguments return an error result to the model without execution.

Initial tools:

| Tool | Arguments | Approval |
| --- | --- | --- |
| `read_file` | `path` | Automatic within workspace |
| `list_directory` | `path` | Automatic within workspace |
| `write_file` | `path`, `content` | Every invocation |
| `bash_command` | `command` | Every invocation |

Tools are opt-in through an Enable tools control and a workspace chooser. Agent
profiles can use them; ordinary chat and vision remain usable with tools disabled.
Require an explicitly selected workspace before enabling tools. Relative paths
resolve against that workspace. Resolve symlinks and reject file-tool paths
outside it. Show the workspace in the UI and include it in model instructions.

The approval dialog shows the exact command or resolved write path and proposed
content, with Approve and Deny. Closing the dialog means deny. No shell command is
classified as safe by inspecting its text. Approved bash commands run with the
workspace as their working directory, but are not filesystem-sandboxed.

Read UTF-8 text files only, limit reads and tool output to 64 KiB, and report
truncation explicitly. Directory listings are nonrecursive and capped at 1,000
entries. Reject writes larger than 1 MiB. Writes replace file contents atomically
and do not create missing parent directories. Commands have a 60-second timeout;
capture bounded stdout/stderr and include exit status in the result.

## Completion loop

1. Send messages and tool schemas when tools are enabled.
2. Stream text while accumulating tool-call fragments by delta index. Assemble
   call IDs, function names, and JSON arguments across chunks; support multiple
   calls in one assistant message.
3. At the end of the response, validate its termination and complete call
   structure. Never execute an incomplete or interrupted streamed call.
4. Append the assistant message containing its content and `tool_calls` before
   executing calls sequentially in their received order.
5. Request approval where required. Append a `role: tool` message with the
   matching `tool_call_id` for every success, denial, or execution error.
6. Send the updated context and repeat. A response without tool calls completes
   the turn. Limit each turn to 16 rounds and 32 executions, reporting a clear
   limit error if the model continues beyond those limits.

Use structured JSON result content with success/error, output, and relevant
metadata. A denial is a result the model can explain or respond to. Transport or
protocol errors stop the turn; they must not silently look like successful final
answers. Preserve usage reporting on every completion request.

## UI, cancellation, and history

Show tool activity and results separately from assistant prose. Preserve
intermediate assistant text rather than merging it into the final answer.
Approval dialogs run on the UI thread and carry a worker/call identity so a stale
approval cannot authorize a later call.

Add Stop for an active turn. Cancellation wakes approval waits, prevents further
requests and executions, and terminates a running command's process group.
Retain each worker until its thread finishes. New session, opening history, and
application exit use the same cancellation lifecycle and ignore stale signals.

Add a JSON metadata column to messages using a backward-compatible migration.
Store assistant `tool_calls` and tool `tool_call_id` and name there. Persist
completed exchanges on the UI thread, and replay their exact protocol fields in
`_payload()`. Existing attachment and folder-context behavior stays intact.
Group transcript persistence so incomplete call/result sets are not replayed.
On cancellation or failure after execution, retain completed tool exchanges and
the user turn; never undo history as though a side effect did not happen.

## Verification

Use temporary directories and a local fake streaming endpoint, without a GPU:

- Fragmented arguments, multiple calls, text plus calls, usage-only chunks, and
  a final answer after two tool rounds.
- Missing call IDs, invalid JSON, unknown tools, interrupted streams, and limits.
- Automatic reads, write/shell approval, denial, and no execution before approval.
- Path traversal, symlinks outside the workspace, bounded output, atomic writes,
  command timeout, and command cancellation.
- Approval-wait cancellation, stale worker signals, and worker lifetime.
- Existing-database migration and reopening a session with tool exchanges.
- Offscreen Qt checks for approval/result integration and ordinary chat behavior.

Follow with a desktop smoke check for the workspace chooser, approval dialog,
Stop, and reopened history. If a running tool-capable llama.cpp endpoint is
available, verify one read-to-final-answer turn against it.
