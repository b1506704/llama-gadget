"""Streaming completions and approved local tool execution for llama.cpp."""
import copy
import http.client
import json
import socket
import threading
from urllib.parse import urlsplit

from PyQt6.QtCore import QThread, pyqtSignal

from .service import BASE_URL
from .tools import tool_error


class TurnCancelled(Exception):
    pass


class ChatWorker(QThread):
    token = pyqtSignal(str)
    stats = pyqtSignal(dict)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()
    approval_requested = pyqtSignal(str, str, dict)
    exchange_completed = pyqtSignal(list)
    message_completed = pyqtSignal(dict)
    tool_started = pyqtSignal(str, str)

    def __init__(self, messages, base_url=BASE_URL, parent=None, registry=None,
                 max_rounds=16, max_executions=32):
        super().__init__(parent)
        self.messages = copy.deepcopy(messages)
        self.base_url = base_url
        self.registry = registry
        self.max_rounds = max_rounds
        self.max_executions = max_executions
        self.cancel_event = threading.Event()
        self._condition = threading.Condition()
        self._pending_call = None
        self._decision = None
        self._approval_sequence = 0
        self._socket = None

    def cancel(self):
        self.cancel_event.set()
        with self._condition:
            self._condition.notify_all()
            if self._socket is not None:
                try:
                    self._socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def decide(self, call_id, approved):
        with self._condition:
            if self._pending_call == call_id and self._decision is None and not self.cancel_event.is_set():
                self._decision = bool(approved)
                self._condition.notify_all()

    def _approve(self, call_id, name, args):
        with self._condition:
            # Protocol IDs can repeat across rounds. Each approval gets its own
            # identity so a delayed decision cannot authorize another invocation.
            self._approval_sequence += 1
            approval_id = f'{call_id}:{self._approval_sequence}'
            self._pending_call, self._decision = approval_id, None
        self.approval_requested.emit(approval_id, name, args)
        with self._condition:
            self._condition.wait_for(lambda: self._decision is not None or self.cancel_event.is_set())
            approved = self._decision is True and not self.cancel_event.is_set()
            self._pending_call, self._decision = None, None
            return approved

    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise TurnCancelled()

    def _stream(self):
        body = dict(messages=self.messages, stream=True,
                    stream_options={'include_usage': True}, temperature=0.7,
                    chat_template_kwargs={'enable_thinking': False})
        if self.registry:
            body['tools'] = self.registry.schemas()
        url = urlsplit(self.base_url)
        if url.scheme not in ('http', 'https') or not url.hostname:
            raise ValueError('Endpoint must be an HTTP or HTTPS URL')
        connection_type = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
        connection = connection_type(url.hostname, url.port, timeout=10)
        content, calls, finish_reason = [], {}, None
        try:
            self._check_cancel()
            connection.connect()
            with self._condition:
                self._socket = connection.sock
                self._socket.settimeout(300)
            self._check_cancel()
            path = url.path.rstrip('/') + '/v1/chat/completions'
            connection.request('POST', path, json.dumps(body).encode(),
                               {'Content-Type': 'application/json', 'Accept': 'text/event-stream'})
            response = connection.getresponse()
            if response.status != 200:
                detail = response.read(4096).decode('utf-8', 'replace')
                raise RuntimeError(f'HTTP {response.status}: {detail}')
            with response:
                for raw in response:
                    self._check_cancel()
                    line = raw.decode('utf-8', 'strict').strip()
                    if not line.startswith('data:'):
                        continue
                    data = line[5:].strip()
                    if data == '[DONE]':
                        break
                    obj = json.loads(data)
                    if obj.get('error'):
                        raise RuntimeError(str(obj['error']))
                    if obj.get('usage'):
                        self.stats.emit({**obj['usage'], **obj.get('timings', {})})
                    choices = obj.get('choices', [])
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get('delta') or {}
                    if finish_reason is not None and (delta.get('content') or delta.get('tool_calls')):
                        raise ValueError('Received data after completion finish')
                    text = delta.get('content')
                    if text:
                        if not isinstance(text, str):
                            raise ValueError('Invalid assistant text')
                        content.append(text)
                        self.token.emit(text)
                    for fragment in delta.get('tool_calls') or []:
                        index = fragment.get('index')
                        if type(index) is not int or index < 0 or index >= self.max_executions:
                            raise ValueError('Invalid tool call index or too many calls')
                        if fragment.get('type', 'function') != 'function':
                            raise ValueError('Only function tools are supported')
                        call = calls.setdefault(index, dict(id='', type='function', function=dict(name='', arguments='')))
                        function = fragment.get('function') or {}
                        for target, key, value in [(call, 'id', fragment.get('id')),
                                                   (call['function'], 'name', function.get('name')),
                                                   (call['function'], 'arguments', function.get('arguments'))]:
                            if value is not None:
                                if not isinstance(value, str):
                                    raise ValueError('Invalid tool call fragment')
                                target[key] += value
                        if sum(len(c['function']['arguments']) for c in calls.values()) > 2 * 1024 * 1024:
                            raise ValueError('Tool arguments exceed 2 MiB')
                    if choice.get('finish_reason'):
                        finish_reason = choice['finish_reason']
            self._check_cancel()
            if finish_reason not in ('stop', 'tool_calls'):
                raise ValueError(f'Incomplete completion (finish reason: {finish_reason})')
            message = dict(role='assistant', content=''.join(content))
            if calls:
                if not self.registry:
                    raise ValueError('Endpoint requested tools while tools are disabled')
                ordered = [calls[index] for index in sorted(calls)]
                if sorted(calls) != list(range(len(calls))):
                    raise ValueError('Tool call indices are incomplete')
                ids = [call['id'] for call in ordered]
                if len(set(ids)) != len(ids) or any(not cid for cid in ids):
                    raise ValueError('Tool call IDs are missing or duplicated')
                if any(not call['function']['name'] or not call['function']['arguments'] for call in ordered):
                    raise ValueError('Incomplete tool call')
                message['tool_calls'] = ordered
            elif finish_reason == 'tool_calls':
                raise ValueError('Tool completion contains no calls')
            return message
        finally:
            with self._condition:
                self._socket = None
            connection.close()

    def _execute(self, call):
        name, arguments = call['function']['name'], call['function']['arguments']
        if self.cancel_event.is_set():
            return dict(ok=False, error='Cancelled', cancelled=True)
        try:
            args = self.registry.prepare(name, arguments)
            if self.registry.requires_approval(name) and not self._approve(call['id'], name, args):
                return dict(ok=False, error='Cancelled' if self.cancel_event.is_set() else 'User denied execution',
                            denied=True, cancelled=self.cancel_event.is_set())
            self.tool_started.emit(call['id'], name)
            return self.registry.execute(name, args, self.cancel_event, prepared=True)
        except Exception as exc:  # One failing tool must not discard preceding side effects.
            return tool_error(exc)

    def run(self):
        executed = 0
        try:
            for _ in range(self.max_rounds):
                self._check_cancel()
                message = self._stream()
                calls = message.get('tool_calls')
                if not calls:
                    self.messages.append(message)
                    self.message_completed.emit(message)
                    self.finished_ok.emit()
                    return
                if executed + len(calls) > self.max_executions:
                    raise ValueError(f'Tool execution limit reached ({self.max_executions})')
                exchange = [message]
                for call in calls:
                    result = self._execute(call)
                    exchange.append(dict(role='tool', tool_call_id=call['id'], name=call['function']['name'],
                                         content=json.dumps(result, ensure_ascii=False)))
                    executed += 1
                self.messages.extend(exchange)
                self.exchange_completed.emit(exchange)
                self._check_cancel()
            raise ValueError(f'Completion round limit reached ({self.max_rounds})')
        except Exception as exc:  # noqa: BLE001 - thread boundary
            if self.cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))
