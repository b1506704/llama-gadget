"""Single representation for backend, model and tool failures shown in the UI.

A ``UiError`` carries a plain-language ``summary`` (never a raw traceback or exception
text) plus the raw context in ``diagnostics``. ``sanitize_diagnostics`` is the only way
that context leaves the app: it redacts secrets, Bearer [REDACTED] API keys, credentials in
URLs and user home paths before the report is copied to the clipboard."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

from .mcp_config import redact

SETTINGS_ENDPOINT = 'endpoint'
SETTINGS_PROFILE = 'profile'
SETTINGS_DATABASE = 'database'
SETTINGS_MCP = 'mcp'

REDACTED = '[REDACTED]'
USER_HOME = '~'

_BEARER_PATTERN = re.compile(r'(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*')
_API_KEY_KV_PATTERN = re.compile(r'(?i)(api[_-]?key|secret|token|password)(\s*[:=]\s*["\']?)([^"\'\s,;]+)(["\']?)')
_AUTH_HEADER_PATTERN = re.compile(r'(?i)(authorization\s*:\s*)([^\r\n]+)')
_URL_CREDENTIALS_PATTERN = re.compile(r'(https?://)([^:@/\s]+):([^@/\s]+)@')


def sanitize_text(text: str) -> str:
    if not text:
        return text
    try:
        home = str(Path.home())
        if home and home != '/':
            text = text.replace(home, USER_HOME)
    except Exception:
        pass
    text = _BEARER_PATTERN.sub(r'\1' + REDACTED, text)
    text = _AUTH_HEADER_PATTERN.sub(r'\1' + REDACTED, text)
    text = _API_KEY_KV_PATTERN.sub(r'\1\2' + REDACTED + r'\4', text)
    text = _URL_CREDENTIALS_PATTERN.sub(r'\1' + REDACTED + ':' + REDACTED + '@', text)
    return text


def sanitize_value(val: Any) -> Any:
    if isinstance(val, str):
        return sanitize_text(val)
    if isinstance(val, Path):
        return sanitize_text(str(val))
    if isinstance(val, Mapping):
        sanitized = {}
        for k, v in val.items():
            key_str = str(k)
            lower = key_str.lower()
            if any(term in lower for term in ('api_key', 'apikey', 'secret', 'token', 'password', 'authorization')):
                sanitized[key_str] = REDACTED
            else:
                sanitized[key_str] = sanitize_value(v)
        return sanitized
    if isinstance(val, (list, tuple, set)):
        items = [sanitize_value(elem) for elem in val]
        return type(val)(items) if not isinstance(val, set) else items
    return val


def sanitize_diagnostics(diagnostics: Mapping[str, Any]) -> dict:
    if not diagnostics:
        return {}
    cleaned = {}
    for k, v in diagnostics.items():
        lower = str(k).lower()
        if any(term in lower for term in ('api_key', 'apikey', 'secret', 'token', 'password', 'authorization')):
            cleaned[str(k)] = REDACTED
        else:
            cleaned[str(k)] = sanitize_value(v)
    return cleaned


@dataclass
class UiError:
    summary: str
    diagnostics: dict = field(default_factory=dict)
    action_label: Optional[str] = None
    action_target: Optional[str] = None
    retryable: bool = False
    retry_callback: Optional[Callable[[], Any]] = field(default=None, repr=False)

    def sanitized_report(self) -> str:
        report = {
            'summary': self.summary,
            'retryable': self.retryable,
            'action_label': self.action_label,
            'action_target': self.action_target,
            'diagnostics': sanitize_diagnostics(self.diagnostics),
        }
        return json.dumps(report, indent=2, default=str)


def chat_failure(reason: str, diagnostics: Optional[Mapping[str, Any]] = None, retry_callback: Optional[Callable] = None) -> UiError:
    return UiError(
        summary=f'Chat generation failed: {reason}',
        diagnostics=dict(diagnostics or {}),
        action_label='Retry',
        action_target=None,
        retryable=True,
        retry_callback=retry_callback,
    )


def model_failure(model_name: str, reason: str, diagnostics: Optional[Mapping[str, Any]] = None) -> UiError:
    return UiError(
        summary=f'Failed to load model {model_name}: {reason}',
        diagnostics=dict(diagnostics or {}),
        action_label='Open Profile Settings',
        action_target=SETTINGS_PROFILE,
        retryable=False,
    )


def mcp_provider_failure(provider_name: str, reason: str, diagnostics: Optional[Mapping[str, Any]] = None) -> UiError:
    return UiError(
        summary=f'MCP tool server "{provider_name}" failed: {reason}',
        diagnostics=dict(diagnostics or {}),
        action_label='Configure MCP Tools',
        action_target=SETTINGS_MCP,
        retryable=False,
    )


def mcp_config_failure(reason: str, diagnostics: Optional[Mapping[str, Any]] = None) -> UiError:
    return UiError(
        summary=f'MCP configuration error: {reason}',
        diagnostics=dict(diagnostics or {}),
        action_label='Configure MCP Tools',
        action_target=SETTINGS_MCP,
        retryable=False,
    )


__all__ = ['UiError', 'sanitize_diagnostics', 'sanitize_text', 'sanitize_value', 'chat_failure',
           'model_failure', 'mcp_provider_failure', 'mcp_config_failure', 'SETTINGS_ENDPOINT',
           'SETTINGS_PROFILE', 'SETTINGS_DATABASE', 'SETTINGS_MCP', 'REDACTED', 'USER_HOME']
