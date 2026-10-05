"""Workspace-scoped local tools and their OpenAI-compatible schemas.

Approval is enforced by ChatWorker; execute() is the low-level executor.
Approved bash commands run as the desktop user and are not sandboxed.
"""
import codecs
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import time
from contextlib import contextmanager

OUTPUT_LIMIT = 64 * 1024
WRITE_LIMIT = 1024 * 1024
DIRECTORY_LIMIT = 1000

# name: (description, required string arguments, requires approval)
DEFINITIONS = {
    'read_file': ('Read a UTF-8 text file within the workspace (up to 64 KiB).', ('path',), False),
    'list_directory': ('List up to 1000 entries in a workspace directory, nonrecursively.', ('path',), False),
    'write_file': ('Replace a workspace file with UTF-8 content. Requires user approval.', ('path', 'content'), True),
    'bash_command': ('Run a bash command in the workspace. Requires user approval; not sandboxed.', ('command',), True),
}


def bounded_text(text, limit=OUTPUT_LIMIT):
    raw = text.encode('utf-8', 'replace')
    return raw[:limit].decode('utf-8', 'ignore'), len(raw) > limit


def tool_error(exc):
    error, truncated = bounded_text(str(exc))
    return dict(ok=False, error=error, truncated=truncated)


class ToolRegistry:
    def __init__(self, workspace):
        self.workspace = Path(workspace).expanduser().resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError('Workspace must be a directory')
        self.command_timeout = 60

    def schemas(self):
        return [dict(type='function', function=dict(
            name=name, description=description,
            parameters=dict(type='object', properties={key: {'type': 'string'} for key in keys},
                            required=list(keys), additionalProperties=False)))
                for name, (description, keys, _) in DEFINITIONS.items()]

    def requires_approval(self, name):
        return DEFINITIONS[name][2]

    def prepare(self, name, arguments):
        if name not in DEFINITIONS:
            raise ValueError(f'Unknown tool: {name}')
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (ValueError, TypeError) as exc:
                raise ValueError('Arguments must be valid JSON') from exc
        keys = DEFINITIONS[name][1]
        if not isinstance(arguments, dict) or set(arguments) != set(keys):
            raise ValueError(f'{name} requires exactly: {", ".join(keys)}')
        if any(not isinstance(value, str) or '\x00' in value for value in arguments.values()):
            raise ValueError('Arguments must be strings without NUL characters')
        args = dict(arguments)
        if 'path' in args:
            if not args['path'].strip():
                raise ValueError('Path cannot be empty')
            path = Path(args['path']).expanduser()
            if not path.is_absolute():
                path = self.workspace / path
            path = path.resolve()
            if not path.is_relative_to(self.workspace):
                raise ValueError('Path is outside the selected workspace')
            args['path'] = str(path)
        if name == 'write_file' and len(args['content'].encode('utf-8')) > WRITE_LIMIT:
            raise ValueError('Write exceeds 1 MiB')
        if name == 'bash_command' and not args['command'].strip():
            raise ValueError('Command cannot be empty')
        return args

    @contextmanager
    def _directory(self, path):
        """Walk with directory descriptors so replaced symlinks cannot escape."""
        relative = Path(path).relative_to(self.workspace).parts
        # O_NOFOLLOW on the absolute workspace alone would still follow ancestor
        # symlinks. Walk from / so every component is protected against swaps.
        parts = self.workspace.parts[1:] + relative
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            yield fd
        finally:
            os.close(fd)

    def execute(self, name, arguments, cancel_event, *, prepared=False):
        try:
            args = self.prepare(name, arguments)
            if prepared and args != arguments:
                raise ValueError('Tool target changed after approval; execution refused')
            if cancel_event.is_set():
                return dict(ok=False, error='Cancelled', cancelled=True)
            if name == 'bash_command':
                return self._command(args['command'], cancel_event)
            path = Path(args['path'])
            if name == 'list_directory':
                with self._directory(path) as fd, os.scandir(fd) as entries:
                    names = []
                    truncated = False
                    for entry in entries:
                        if len(names) == DIRECTORY_LIMIT:
                            truncated = True
                            break
                        names.append(entry.name + ('/' if entry.is_dir(follow_symlinks=False) else ''))
                    output, clipped = bounded_text('\n'.join(sorted(names)))
                    return dict(ok=True, output=output, truncated=truncated or clipped)
            with self._directory(path.parent) as fd:
                if name == 'read_file':
                    file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    with os.fdopen(file_fd, 'rb') as handle:
                        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                            raise ValueError('Only regular text files can be read')
                        raw = handle.read(OUTPUT_LIMIT + 1)
                    truncated = len(raw) > OUTPUT_LIMIT
                    raw = raw[:OUTPUT_LIMIT]
                    if b'\0' in raw:
                        raise ValueError('Binary files are not supported')
                    # An incomplete UTF-8 character at the truncation boundary is harmless.
                    content = codecs.getincrementaldecoder('utf-8')().decode(raw, final=not truncated)
                    return dict(ok=True, output=content, truncated=truncated)
                return self._write(fd, path.name, args['content'], cancel_event)
        except Exception as exc:  # Local tool boundary: failures are model-visible results.
            return tool_error(exc)

    def _write(self, directory_fd, name, content, cancel_event):
        import secrets
        temporary = '.llama-write-' + secrets.token_hex(12)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(fd, 'wb') as handle:
                try:
                    old = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if not stat.S_ISREG(old.st_mode):
                        raise ValueError('Only regular files can be replaced')
                    os.fchmod(handle.fileno(), stat.S_IMODE(old.st_mode))
                except FileNotFoundError:
                    pass
                data = content.encode('utf-8')
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if cancel_event.is_set():
                return dict(ok=False, error='Cancelled', cancelled=True)
            os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            return dict(ok=True, output=f'Wrote {len(data)} bytes', bytes_written=len(data))
        finally:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass

    def _command(self, command, cancel_event):
        process = subprocess.Popen(['bash', '-c', command], cwd=self.workspace,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        output = {'stdout': bytearray(), 'stderr': bytearray()}
        truncated = timed_out = cancelled = False
        deadline = time.monotonic() + self.command_timeout
        try:
            with selectors.DefaultSelector() as selector:
                for label, pipe in [('stdout', process.stdout), ('stderr', process.stderr)]:
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ, label)
                while selector.get_map() or process.poll() is None:
                    cancelled = cancel_event.is_set()
                    timed_out = time.monotonic() >= deadline
                    if cancelled or timed_out:
                        break
                    for key, _ in selector.select(.05):
                        chunk = os.read(key.fileobj.fileno(), 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        room = OUTPUT_LIMIT - sum(len(value) for value in output.values())
                        output[key.data].extend(chunk[:room])
                        truncated |= len(chunk) > room
        finally:
            # Also clean up background descendants which inherited pipe descriptors.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()
            process.stderr.close()
        decoded = {}
        room = OUTPUT_LIMIT
        for key, value in output.items():
            decoded[key], clipped = bounded_text(value.decode('utf-8', 'replace'), room)
            room -= len(decoded[key].encode('utf-8'))
            truncated |= clipped
        return dict(ok=process.returncode == 0 and not cancelled and not timed_out,
                    **decoded, exit_code=process.returncode, truncated=truncated,
                    timed_out=timed_out, cancelled=cancelled)
