"""Single representation for backend, model and tool failures shown in the UI.

A ``UiError`` carries a plain-language ``summary`` (never a raw traceback or exception
text) plus the raw context in ``diagnostics``. ``sanitize_diagnostics`` is the only way
that context leaves the app: it redacts secrets, bearer tokens, API keys, credentials in
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

REDACTED = '<REDACTED>'
USER_HOME = '<USER_HOME>'


@dataclass(frozen=True)
class UiError:
    code: str
    summary: str
    recoverable: bool
    retry_callback: Optional[Callable[[], None]] = None
    settings_section: Optional[str] = None
    diagnostics: Union[dict, str] = field(default='')

    @property
    def can_retry(self) -> bool:
        return self.recoverable and self.retry_callback is not None


# ---------------------------------------------------------------- redaction
_SECRET_NAME = (r'(?:api[_-]?key|apikey|access[_-]?key|private[_-]?key|client[_-]?secret|'
                r'token|secret|password|passwd|pwd|authorization|credentials?|cookie|session[_-]?id)')
_SECRET_KEY = re.compile(r'(?i)^[\w.-]*' + _SECRET_NAME + r'[\w.-]*$')
_TEXT_RULES = (
    # Authorization headers first, so the assignment rule below never leaves the token behind.
    (re.compile(r'\b((?i:bearer)|Basic)\s+(?!<REDACTED>)[A-Za-z0-9\-._~+/]{6,}=*'), r'\1 ' + REDACTED),
    (re.compile(r'(?i)\b([\w.-]*' + _SECRET_NAME + r'[\w.-]*)(\\?["\']?\s*[:=]\s*\\?["\']?)'
                r'(?!(?:bearer|basic)\s+<REDACTED>)(?!<REDACTED>)([^\s"\'&,;}\]\\]+)'), r'\1\2' + REDACTED),
    (re.compile(r'\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}'), REDACTED),
    (re.compile(r'\bkey-[A-Za-z0-9_\-]{8,}'), REDACTED),
    (re.compile(r'\b(?:gh[pousr]|github_pat|hf|glpat|xox[abprs])[_-][A-Za-z0-9_\-]{10,}'), REDACTED),
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'), REDACTED),
    (re.compile(r'\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}'), REDACTED),  # JWT
    (re.compile(r'(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s@:]+:[^/\s@]+@'), r'\1' + REDACTED + '@'),  # URL user:pass@
)
_HOME_RULES = (
    re.compile(r'/(?:home|Users)/[^/\s"\'\\:]+'),
    re.compile(r'(?<![\w/])/root\b'),
    re.compile(r'(?i)\b[A-Z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/)[^\\/\s"\':]+'),
)


def _home_literals():
    try:
        home = str(Path.home())
    except (RuntimeError, OSError):
        return ()
    if len(home.strip('/\\')) < 2:
        return ()
    escaped = json.dumps(home)[1:-1]
    return tuple(dict.fromkeys((escaped, home)))


def sanitize_text(text: str, secrets=()) -> str:
    """Redact known secret values (reusing the MCP redactor), token/credential patterns
    and user home paths from free text."""
    text = redact(str(text), secrets).replace('[redacted]', REDACTED)
    for pattern, replacement in _TEXT_RULES:
        text = pattern.sub(replacement, text)
    for literal in _home_literals():
        text = text.replace(literal, USER_HOME)
    for pattern in _HOME_RULES:
        text = pattern.sub(USER_HOME, text)
    return text


def sanitize_value(value: Any, secrets=()) -> Any:
    """Structural redaction: secret-named mapping keys lose their value entirely."""
    if isinstance(value, Mapping):
        return {sanitize_text(key, secrets): REDACTED if _SECRET_KEY.match(str(key)) and item not in (None, '')
                else sanitize_value(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitize_value(item, secrets) for item in value]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return sanitize_text(value, secrets)


def _detail_lines(diagnostics) -> list:
    if isinstance(diagnostics, Mapping):
        lines = []
        for key in sorted(diagnostics, key=str):
            value = diagnostics[key]
            if not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            lines.append(f'  {key}: ' + value.replace('\n', '\n    '))
        return lines
    text = str(diagnostics or '').strip()
    return ['  ' + line for line in text.splitlines()] if text else []


def sanitize_diagnostics(error: UiError) -> str:
    """Deterministic, redacted diagnostic report for ``error``."""
    details = _detail_lines(sanitize_value(error.diagnostics))
    lines = ['Llama Gadget diagnostics',
             f'code: {error.code}',
             f'summary: {error.summary}',
             f'recoverable: {"yes" if error.recoverable else "no"}']
    if error.settings_section:
        lines.append(f'settings_section: {error.settings_section}')
    lines.append('details:')
    if details:
        lines.extend(details)
    else:
        lines.append('  (none)')
    return '\n'.join(lines)


# ----------------------------------------------------------- failure mapping
_CHAT_RULES = (
    (r'context (?:limit|size|overflow|window|length)|exceeds? the available context|maximum context',
     'context_overflow',
     'The conversation is too long for the model’s context window. Start a new session or uncheck large files.',
     None, False),
    (r'Connection ?refused|ConnectionRefused|unreachable|no route|name or service|getaddrinfo|nodename|reset by peer|broken pipe',
     'backend_unreachable',
     "The model server isn't reachable. Check that the profile is running and the endpoint URL is correct.",
     SETTINGS_ENDPOINT, True),
    (r'timed? ?out', 'backend_timeout',
     'The model server stopped responding before the reply finished.', SETTINGS_ENDPOINT, True),
    (r'endpoint must be', 'invalid_endpoint',
     "The endpoint URL isn't a valid HTTP or HTTPS address.", SETTINGS_ENDPOINT, True),
    (r'^HTTP (\d{3})', 'backend_http_error',
     'The model server returned an error (HTTP {0}).', SETTINGS_ENDPOINT, True),
    (r'tool execution limit|round limit', 'tool_limit',
     'The assistant reached the tool-call limit for one turn. Retry to let it continue.', None, True),
    (r'tool', 'tool_protocol_error',
     'The model sent a tool request the app could not run.', None, True),
)


def _message_of(error) -> str:
    if isinstance(error, BaseException):
        return str(error) or type(error).__name__
    return str(error or '')


def chat_failure(error, *, retry=None, context=None) -> UiError:
    """Map a chat streaming / model / in-turn tool failure (``ChatWorker.failed`` text or
    an exception) to a UiError with a plain-language summary."""
    raw = _message_of(error)
    code, summary, section, recoverable = ('chat_failed', "The reply couldn't be completed.",
                                           SETTINGS_ENDPOINT, True)
    for pattern, rule_code, rule_summary, rule_section, rule_recoverable in _CHAT_RULES:
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            code, section, recoverable = rule_code, rule_section, rule_recoverable
            summary = rule_summary.format(*match.groups())
            break
    else:
        if re.search(r'json|expecting|decode|incomplete|completion|assistant text', raw, re.IGNORECASE):
            code, summary = 'model_stream_error', "The model's response stream ended unexpectedly."
    if isinstance(error, BaseException):
        context = dict(context or {}, exception=type(error).__name__)
    return UiError(code, summary, recoverable, retry if recoverable else None, section,
                   dict(context or {}, error=raw))


def model_failure(profile_label: str, error, *, stopping=False, retry=None, context=None) -> UiError:
    """Map a profile start/stop (model load) failure to a UiError."""
    action = 'stop' if stopping else 'start'
    summary = (f'Couldn\'t {action} “{profile_label}”. The model service reported an error; '
               'check the profile and endpoint, then retry.')
    return UiError(f'model_{action}_failed', summary, True, retry, SETTINGS_PROFILE,
                   dict(context or {}, operation=f'profile {action}', profile=profile_label,
                        error=_message_of(error)))


def mcp_provider_failure(failed: Mapping, *, error=None, retry=None, secrets=()) -> Optional[UiError]:
    """Map failed MCP providers (``{name: error text}``) and/or a failed provider operation
    to a UiError; None when nothing failed. Known secret values are redacted up front."""
    if not failed and error is None:
        return None
    names = sorted(failed)
    if names:
        shown = ', '.join(names[:3]) + (f' and {len(names) - 3} more' if len(names) > 3 else '')
        summary = (f"Couldn't connect to MCP provider{'s' if len(names) > 1 else ''} {shown}. "
                   'Check the MCP configuration, then retry.')
    else:
        summary = "The MCP providers couldn't be refreshed. Check the MCP configuration, then retry."
    diagnostics = {'operation': 'mcp connect/refresh',
                   'providers': {name: redact(str(failed[name] or 'failed'), secrets) for name in names}}
    if error is not None:
        diagnostics['error'] = redact(_message_of(error), secrets)
        if isinstance(error, BaseException):
            diagnostics['exception'] = type(error).__name__
    return UiError('mcp_provider_failed', summary, True, retry, SETTINGS_MCP, diagnostics)


def mcp_config_failure(error, *, retry=None) -> UiError:
    """The MCP configuration could not be loaded or validated."""
    return UiError('mcp_config_invalid', "The MCP configuration couldn't be loaded. Open Settings to fix it, "
                   'then retry.', True, retry, SETTINGS_MCP,
                   {'operation': 'mcp load config', 'error': _message_of(error),
                    'exception': type(error).__name__ if isinstance(error, BaseException) else ''})


__all__ = ['UiError', 'sanitize_diagnostics', 'sanitize_text', 'sanitize_value', 'chat_failure',
           'model_failure', 'mcp_provider_failure', 'mcp_config_failure', 'SETTINGS_ENDPOINT',
           'SETTINGS_PROFILE', 'SETTINGS_DATABASE', 'SETTINGS_MCP', 'REDACTED', 'USER_HOME']
