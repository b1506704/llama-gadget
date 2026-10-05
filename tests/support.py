import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading


def chunk(delta=None, finish=None, **extra):
    return {'choices': [{'index': 0, 'delta': delta or {}, 'finish_reason': finish}], **extra}


def call_delta(index=0, id='call_1', name='read_file', arguments='{"path":"note.txt"}'):
    call = {'index': index, 'function': {}}
    if id is not None:
        call['id'] = id
        call['type'] = 'function'
    if name is not None:
        call['function']['name'] = name
    if arguments is not None:
        call['function']['arguments'] = arguments
    return {'tool_calls': [call]}


class StreamingServer:
    def __init__(self, responses):
        self.requests = []
        self.response_started = threading.Event()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(body)
                events = responses[len(owner.requests)-1]
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                owner.response_started.set()
                try:
                    for event in events:
                        if isinstance(event, threading.Event):
                            event.wait(5)
                            continue
                        data = event if isinstance(event, str) else json.dumps(event)
                        self.wfile.write(('data: ' + data + '\n\n').encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
