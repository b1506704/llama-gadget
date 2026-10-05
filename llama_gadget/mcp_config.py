"""Strict MCP configuration persistence and secret-safe connection settings."""
import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from PyQt6.QtCore import QStandardPaths

EXAMPLE = {'mcpServers': {
    'local-example': {'command': 'python', 'args': ['/absolute/path/to/server.py'], 'disabled': True},
    'remote-example': {'url': 'http://127.0.0.1:8000/mcp',
                       'headers': {'Authorization': 'Bearer ${env:MCP_TOKEN}'}, 'disabled': True}}}


def default_config_path():
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericConfigLocation)) / 'llama-gadget' / 'mcp.json'


def parse_config(text):
    try:
        config = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid MCP JSON configuration') from exc
    if not isinstance(config, dict) or set(config) != {'mcpServers'} or not isinstance(config['mcpServers'], dict):
        raise ValueError('Configuration must contain only an mcpServers object')
    for name, entry in config['mcpServers'].items():
        if not isinstance(name, str) or not name.strip() or len(name) > 128 or any(ord(c)<32 for c in name):
            raise ValueError('Server names must be nonempty strings of at most 128 characters')
        if not isinstance(entry, dict) or (('command' in entry) == ('url' in entry)):
            raise ValueError(f'{name}: choose exactly one command or url')
        fields = {'command','args','env','cwd','disabled','timeout'} if 'command' in entry else {'url','headers','disabled','timeout'}
        if set(entry) - fields:
            raise ValueError(f'{name}: unsupported configuration fields')
        timeout = entry.get('timeout', 60)
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 1 <= timeout <= 300:
            raise ValueError(f'{name}: timeout must be between 1 and 300 seconds')
        if type(entry.get('disabled', False)) is not bool:
            raise ValueError(f'{name}: disabled must be true or false')
        key = 'command' if 'command' in entry else 'url'
        if not isinstance(entry[key], str) or not entry[key].strip() or '\0' in entry[key]:
            raise ValueError(f'{name}: invalid {key}')
        if key == 'url':
            url = urlsplit(entry['url'])
            try:
                valid = url.scheme in ('http','https') and url.hostname and not url.username and not url.password and not url.fragment
                url.port
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(f'{name}: url must be HTTP/HTTPS without embedded credentials or fragments')
        if 'args' in entry and (not isinstance(entry['args'], list) or any(not isinstance(a,str) or '\0' in a for a in entry['args'])):
            raise ValueError(f'{name}: args must be a list of strings')
        if 'cwd' in entry and (not isinstance(entry['cwd'], str) or not entry['cwd'].strip() or '\0' in entry['cwd']):
            raise ValueError(f'{name}: cwd must be a nonempty path string')
        for key in ('env','headers'):
            if key not in entry:
                continue
            values = entry[key]
            if not isinstance(values, dict) or any(not isinstance(k,str) or not k or not isinstance(v,str) or '\0' in k+v for k,v in values.items()):
                raise ValueError(f'{name}: {key} must map names to strings')
            if key == 'headers' and any(not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", k) or '\r' in v or '\n' in v for k,v in values.items()):
                raise ValueError(f'{name}: invalid HTTP header')
            if key == 'env' and any('=' in k for k in values):
                raise ValueError(f'{name}: invalid environment name')
    return config


def load_config(path):
    try:
        return parse_config(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'mcpServers': {}}


def save_config(path, config):
    validated = parse_config(json.dumps(config))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.mcp-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(validated, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def resolve_server(entry, environ=None):
    environ = os.environ if environ is None else environ
    resolved = copy.deepcopy(entry)
    secrets = set()
    def expand(match):
        name = match.group(1)
        if name not in environ:
            raise ValueError(f'Missing environment variable: {name}')
        value = environ[name]
        if value:
            secrets.add(value)
        return value
    for field in ('env','headers'):
        for key, value in resolved.get(field, {}).items():
            value = re.sub(r'\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}', expand, value)
            if '\0' in value or (field == 'headers' and ('\r' in value or '\n' in value)):
                raise ValueError('Expanded connection settings contain invalid characters')
            resolved[field][key] = value
            if value:
                secrets.add(value)
    resolved['_secrets'] = tuple(sorted(secrets, key=len, reverse=True))
    resolved.setdefault('timeout', 60)
    return resolved


def redact(text, secret_values):
    for secret in sorted(set(secret_values), key=len, reverse=True):
        if secret:
            text = text.replace(secret, '[redacted]')
    return text
