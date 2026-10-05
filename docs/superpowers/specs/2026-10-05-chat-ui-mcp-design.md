# Chat controls, image previews, and MCP tools

## Requested outcome

Extend the existing working tool-calling implementation with:

- Tool requests and responses hidden by default, with a user-controlled toggle.
- Inspection of available tools through the UI.
- MCP connections and editable, persistent MCP configuration.
- Clickable previews for image attachments before sending and in conversation history.
- Copy controls for assistant responses.

This document is a proposed design. No product changes for these additions have
been made yet. The previous tool-calling work remains in the working tree.

## Chat presentation

Add a Show tool details toggle, initially off. Remember the user's choice in
QSettings. When off, hide request arguments, result bodies, and tool metadata;
keep assistant prose, final answers, and ordinary messages visible. A compact
activity label can still say that a tool is running or awaiting approval.
Approvals remain visible regardless of this toggle.

Turning the toggle on reveals the complete request/result pairs already stored
in the session, including tool name, arguments, result, and success/error status.
Turning it off only changes presentation. It must not remove messages from
SQLite or the model payload. Reopened sessions respect the same preference.

Each assistant message has a Copy control alongside its header. Copy the exact
assistant response text, including Markdown/code, without timestamps, tool
details, or attachment labels. Include intermediate assistant prose where
present; do not offer an empty copy action on tool-only messages. Provide brief
Copied feedback without changing the stored message or losing scroll position.

Keep Qt Markdown rendering and the existing scrolling behavior. Put chat
presentation and per-message actions in a focused UI module rather than adding
all of their implementation to the already large `app.py`.

## Image previews

Show clickable image attachment controls in the composer, with filename and a
small thumbnail. Add equivalent clickable thumbnails to messages that contain
images, including messages loaded from history. Text attachments retain their
current labels.

Clicking opens a resizable preview dialog with the image scaled to fit, its name,
and dimensions. Allow viewing at actual size through a scrollable area. Closing
the dialog changes no attachments or history. Each control resolves its own
attachment identity; clearing attachments or switching sessions must not cause
an old control to display a different image.

Decode the image from its stored data URL, not the original file path. This makes
clipboard images and historical attachments work even after the original file
is moved. Preview the image actually attached to the model, which may have been
downscaled/re-encoded by `images.py`. Invalid image data shows an error and does
not crash rendering.

## Tool inspection

Add a Tools control opening an inspector for built-in and MCP tools. Show the
tool name, provider/server, description, argument JSON schema, availability, and
approval policy. Distinguish configured, disabled, connecting, connected, and
failed MCP servers. Provide Refresh discovery for enabled servers; show errors
per server without hiding healthy providers.

Inspection must be available without sending a chat message. Browsing built-in
tools does not execute them. Discovery is an explicit action; merely launching
the app or opening the config editor does not launch configured server commands.

The shared model registry includes only tools actually available for the turn.
MCP-only use does not require a local workspace. Built-in file and shell tools
still require a selected workspace. Enable tools remains limited to agent
profiles. Freeze the active turn's tool definitions and provider mapping so
refreshing or editing configuration cannot reroute an in-flight call.

## MCP configuration

Use the conventional JSON shape with a top-level `mcpServers` object. Provide a
Settings control opening an editor with Validate, Save, Load from file, and a
documented example. Show validation errors beside the editor; a rejected change
leaves the last valid configuration intact.

Persist the app-owned file at the Qt application config location, named
`mcp.json`. Save atomically with owner-only permissions. Saving imports JSON
into this app-owned file and does not rewrite the user's imported file.

Supported server entries:

```json
{
  "mcpServers": {
    "local-example": {
      "command": "python",
      "args": ["/absolute/path/to/server.py"],
      "env": {"EXAMPLE_KEY": "${env:EXAMPLE_KEY}"},
      "disabled": true
    },
    "remote-example": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {"Authorization": "Bearer ${env:MCP_TOKEN}"},
      "disabled": true
    }
  }
}
```

`command` identifies stdio; `url` identifies Streamable HTTP. An entry must select
exactly one transport. Validate server names, field types, nonempty commands,
HTTP/HTTPS URLs, argument lists, environment/header values, and timeout bounds.
Allow an optional `cwd` for local servers and `timeout` in seconds, defaulting to
60 with an accepted range of 1–300. Support `${env:NAME}` interpolation in env
and headers; unresolved references produce a per-server connection error.
Unknown fields are rejected with a clear message rather than silently ignored.

Never send config environment variables, command-line arguments, credentials,
headers, or expanded secret values to the model. Error summaries must redact
configured secret values. Discovery and config displays should show tool
metadata and connection status, not server stderr or transport request dumps.

Support tools in this iteration. OAuth login, legacy HTTP+SSE servers, MCP
resources/prompts, and server installation are outside this iteration. Explain
unsupported configurations plainly instead of treating them as connected.

## MCP client and registry integration

Use the official `mcp` Python SDK, with a tested dependency range, to initialize
sessions, list tools including paginated results, and call tools. Add focused
config/client modules and a registry adapter. Use a dedicated background asyncio
loop so Qt remains responsive; create, use, and close SDK sessions within that
loop. Do not restart a server for each tool call.

Give MCP tools collision-free model names using a deterministic server/tool
mapping restricted to function-name characters and length limits. Retain the
original server and tool name in the inspector and call routing. Preserve each
server's input schema rather than imposing the built-in registry's string-only
argument rules. Validate tool arguments against those schemas before dispatch.

All MCP tool calls require approval by default. Display server, original tool
name, and exact arguments. Treat server annotations as inspection information,
not authority to auto-run a call. Built-in read/list tools retain their workspace
auto-run policy. Discovery starts configured local commands or contacts remote
URLs only after the user explicitly connects/refreshes providers.

Normalize MCP results into the existing structured tool result format, including
`isError`, text content, and structured content. Bound model-facing result output
to 64 KiB and report truncation. For nontext content, provide type/metadata in
this first iteration; do not inline arbitrary binary content into the language
model context. Do not retrieve resource links automatically.

Tool timeouts, disconnected servers, invalid arguments, and MCP errors become
tool results so completed side effects remain in history. Denied calls never
reach the MCP server. Stop cancels pending client work using SDK-supported
cancellation, wakes approvals, and prevents further calls. Remote cancellation
is best effort and does not guarantee undoing a server-side action; disclose
that limitation in usage documentation.

Config changes disconnect affected providers and invalidate discovered tools
only when no turn is active. Discovery and disconnect operations run in the
background. Application exit closes sessions and local child processes before
destroying their event loop. Reuse the existing stale-worker/approval protection.

## Alternatives considered

The recommended implementation adds the SDK-backed providers to the current
registry and isolates new UI controls into focused modules. It preserves the
existing approval and history contracts.

A stdio-only first version would have fewer transport tests, but would exclude
remote MCP configurations. A custom JSON-RPC client would avoid an SDK
dependency, but would make the app responsible for protocol negotiation,
transport lifecycle, and cancellation. Neither tradeoff is compelling here.

## Verification

- Offscreen Qt tests for default-hidden tool details, toggling, history replay,
  visible approvals while details are hidden, and unchanged model payloads.
- Exact clipboard contents for multiple responses and feedback after Copy.
- Composer and conversation preview tests for files, pasted images, reopened
  history, missing original paths, malformed data, and attachment/session changes.
- Config parsing, atomic save/reload, permissions, disabled providers, unknown
  fields, interpolation failures, and secret redaction.
- Real fixture MCP servers over stdio and Streamable HTTP for initialize,
  discovery, pagination, invocation, typed schemas, collisions, structured/error
  results, and output bounds.
- Approval/denial and cancellation integration through ChatWorker, including no
  invocation before approval, no stale routing after configuration changes,
  responsive Qt during discovery, and provider shutdown without orphan processes.
- Existing tool-calling tests, ordinary chat, image payloads, scroll behavior,
  and SQLite migration/history regression checks.
- Render and inspect offscreen screenshots of chat controls, image preview, tool
  inspector, and MCP editor. Use a live model smoke test when available.

## Protocol references

- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP standard transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)

Implementation must check the installed SDK's actual supported interfaces and
pin the compatible range; upstream main may differ from published releases.
