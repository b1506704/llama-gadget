"""Background MCP sessions. Each transport is entered/exited by its owner task."""
import asyncio
import concurrent.futures
import copy
import json
import logging
import os
import threading

from .mcp_config import parse_config, resolve_server, redact
from .tools import OUTPUT_LIMIT, bounded_text


def normalized_result(result, secrets):
    content = []
    for block in result.content:
        if block.type == 'text':
            content.append({'type':'text', 'text':block.text})
        else:
            content.append({'type':block.type, 'mime_type':getattr(block,'mime_type',None)})
    data = dict(ok=not result.is_error, content=content, structured_content=result.structured_content, truncated=False)
    # Redact both plain and JSON-escaped representations before decoding back.
    encoded = json.dumps(data, ensure_ascii=False)
    for secret in secrets:
        encoded = encoded.replace(json.dumps(secret, ensure_ascii=False)[1:-1], '[redacted]')
    data = json.loads(encoded)
    if len(encoded.encode()) > OUTPUT_LIMIT:
        # Keep valid JSON rather than slicing a structured value in the middle.
        text = json.dumps({'content':data['content'], 'structured_content':data['structured_content']}, ensure_ascii=False)
        preview, _ = bounded_text(text, 60000)
        data = dict(ok=data['ok'], output=preview, truncated=True)
        while len(json.dumps(data,ensure_ascii=False).encode()) > OUTPUT_LIMIT:
            data['output'] = data['output'][:max(0,len(data['output'])-4096)]
    return data


class MCPManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._tools = []
        self._statuses = {}
        self._clients = {}
        self._actors = {}
        self._active = set()
        self.generation = 0
        self._closed = False
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name='MCP connections', daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        self.loop.close()

    def _submit(self, coroutine):
        if self._closed:
            coroutine.close()
            future = concurrent.futures.Future()
            future.set_exception(RuntimeError('MCP connections are closed'))
            return future
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._tools)

    def statuses(self):
        with self._lock:
            return copy.deepcopy(self._statuses)

    def _status(self, name, status, error=''):
        with self._lock:
            self._statuses[name] = {'status':status, 'error':error}

    @staticmethod
    def _error(exc, secrets=()):
        # Flatten ExceptionGroup without transport dumps containing private config.
        if isinstance(exc, BaseExceptionGroup):
            text = '; '.join(MCPManager._error(e,secrets) for e in exc.exceptions)
        else:
            text = str(exc) or type(exc).__name__
        return bounded_text(redact(text,secrets),4096)[0]

    def connect(self, config):
        config = parse_config(json.dumps(config))
        return self._submit(self._connect(config))

    async def _connect(self, config):
        await self._disconnect()
        generation = self.generation
        self._statuses = {}
        waits = []
        for name, entry in config['mcpServers'].items():
            if entry.get('disabled',False):
                self._status(name,'disabled')
                continue
            self._status(name,'connecting')
            ready, stop = self.loop.create_future(), asyncio.Event()
            task = asyncio.create_task(self._actor(name,entry,generation,ready,stop))
            self._actors[name] = (task,stop)
            waits.append(self._wait_ready(name,entry,task,ready))
        await asyncio.gather(*waits)
        return self.statuses()

    async def _wait_ready(self,name,entry,task,ready):
        try:
            await asyncio.wait_for(asyncio.shield(ready),entry.get('timeout',60))
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
            self._status(name,'failed','MCP connection timed out or was cancelled')

    async def _list(self,client,name,generation,secrets):
        tools, cursors, cursor = [], set(), None
        for _ in range(100):
            page = await client.list_tools(cursor=cursor,cache_mode='refresh')
            for tool in page.tools:
                tools.append(dict(server=name, original_name=tool.name, description=redact(tool.description or '',secrets),
                                  input_schema=tool.input_schema,
                                  annotations=tool.annotations.model_dump(mode='json') if tool.annotations else {},
                                  generation=generation))
                if len(tools)>1000:
                    raise ValueError('MCP discovery exceeds 1000 tools')
            cursor = page.next_cursor
            if cursor is None:
                return tools
            if cursor in cursors:
                raise ValueError('MCP discovery returned a repeated cursor')
            cursors.add(cursor)
        raise ValueError('MCP discovery exceeds 100 pages')

    async def _actor(self,name,entry,generation,ready,stop):
        secrets = ()
        try:
            from mcp import Client, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamable_http_client
            import httpx2
            resolved = resolve_server(entry)
            secrets = resolved['_secrets']
            if 'command' in resolved:
                parameters = StdioServerParameters(command=resolved['command'],args=resolved.get('args',[]),
                    env={**os.environ,**resolved.get('env',{})},cwd=resolved.get('cwd'))
                # Suppress untrusted stderr, which can contain expanded secrets.
                with open(os.devnull,'w') as errors:
                    transport = stdio_client(parameters,errlog=errors)
                    async with Client(transport,read_timeout_seconds=resolved['timeout']) as client:
                        await self._connected(name,client,generation,resolved,ready,stop)
            else:
                async with httpx2.AsyncClient(headers=resolved.get('headers',{}),timeout=resolved['timeout'],follow_redirects=False) as http:
                    transport = streamable_http_client(resolved['url'],http_client=http)
                    async with Client(transport,read_timeout_seconds=resolved['timeout']) as client:
                        await self._connected(name,client,generation,resolved,ready,stop)
        except asyncio.CancelledError:
            if not ready.done():
                ready.set_result(False)
            raise
        except Exception as exc:
            self._status(name,'failed', self._error(exc,secrets))
            if not ready.done():
                ready.set_result(False)
        finally:
            self._clients.pop(name,None)
            with self._lock:
                self._tools = [tool for tool in self._tools if tool['server'] != name]

    async def _connected(self,name,client,generation,resolved,ready,stop):
        tools = await self._list(client,name,generation,resolved['_secrets'])
        self._clients[name] = (client,generation,resolved)
        with self._lock:
            self._tools.extend(tools)
        self._status(name,'connected')
        ready.set_result(True)
        await stop.wait()

    def discover(self):
        return self._submit(self._discover())

    async def _discover(self):
        for name,(client,generation,resolved) in list(self._clients.items()):
            try:
                tools = await asyncio.wait_for(self._list(client,name,generation,resolved['_secrets']),resolved['timeout'])
                with self._lock:
                    self._tools = [t for t in self._tools if t['server'] != name] + tools
                self._status(name,'connected')
            except Exception as exc:
                with self._lock:
                    self._tools = [t for t in self._tools if t['server'] != name]
                self._status(name,'failed',self._error(exc,resolved['_secrets']))
        return self.statuses()

    async def _invoke(self,server,tool,arguments,generation):
        entry = self._clients.get(server)
        if not entry or (generation is not None and entry[1] != generation):
            return dict(ok=False,error='MCP provider is unavailable or configuration changed')
        client,_,resolved = entry
        task = asyncio.current_task()
        self._active.add(task)
        try:
            result = await asyncio.wait_for(client.call_tool(tool,arguments),resolved['timeout'])
            return normalized_result(result,resolved['_secrets'])
        except TimeoutError:
            return dict(ok=False,error='MCP tool timed out',timed_out=True)
        except Exception as exc:
            return dict(ok=False,error=self._error(exc,resolved['_secrets']))
        finally:
            self._active.discard(task)

    def call(self,server,tool,arguments,cancel_event, generation=None):
        if cancel_event.is_set():
            return dict(ok=False,error='Cancelled',cancelled=True)
        future = self._submit(self._invoke(server,tool,arguments,generation))
        while True:
            if cancel_event.is_set():
                future.cancel()
                return dict(ok=False,error='Cancelled',cancelled=True)
            try:
                return future.result(timeout=.05)
            except concurrent.futures.TimeoutError:
                continue
            except concurrent.futures.CancelledError:
                return dict(ok=False,error='Cancelled',cancelled=True)
            except Exception:
                return dict(ok=False,error='MCP connection is unavailable')

    def disconnect(self):
        return self._submit(self._disconnect())

    async def _disconnect(self):
        with self._lock:
            self.generation += 1
            self._tools = []
        for task in list(self._active):
            task.cancel()
        await asyncio.gather(*list(self._active),return_exceptions=True)
        actors = list(self._actors.items())
        self._actors.clear()
        for _,(_,stop) in actors:
            stop.set()
        if actors:
            tasks = [task for _,(task,_) in actors]
            try:
                await asyncio.wait_for(asyncio.gather(*tasks,return_exceptions=True),5)
            except TimeoutError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
        for name,_ in actors:
            if self.statuses().get(name,{}).get('status') != 'failed':
                self._status(name,'configured')
        return self.statuses()

    def close(self):
        if self._closed:
            return
        self.disconnect().result(timeout=15)
        self._closed = True
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
