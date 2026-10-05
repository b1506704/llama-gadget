import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from llama_gadget.mcp_client import MCPManager

FIXTURE = str(Path(__file__).parent / 'fixtures' / 'mcp_server.py')


class MCPClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = str(Path(self.temp.name,'calls'))
        self.manager = MCPManager()
        self.addCleanup(self.manager.close)
        self.config = {'mcpServers': {'fixture': {'command':sys.executable,'args':[FIXTURE],
                                                'env':{'FIXTURE_LOG':self.log},'timeout':3}}}

    def test_stdio_discovery_call_and_disconnect(self):
        self.assertFalse(Path(self.log+'.pid').exists())
        self.manager.connect(self.config).result(5)
        tools = self.manager.snapshot()
        self.assertIn('add',[tool['original_name'] for tool in tools])
        result = self.manager.call('fixture','add',{'a':2,'b':3}, threading.Event())
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['structured_content']['result'],5)
        self.assertEqual(Path(self.log).read_text(), 'add\n')
        pid = int(Path(self.log+'.pid').read_text())
        self.manager.disconnect().result(5)
        self.assertEqual(self.manager.snapshot(), [])
        with self.assertRaises(ProcessLookupError):
            os.kill(pid,0)

    def test_failure_isolated_and_output_bound(self):
        self.config['mcpServers']['bad'] = {'command':'/no/such/program','timeout':1}
        self.manager.connect(self.config).result(5)
        self.assertEqual(self.manager.statuses()['bad']['status'], 'failed')
        self.assertIn('add',[t['original_name'] for t in self.manager.snapshot()])
        result = self.manager.call('fixture','large',{},threading.Event())
        self.assertTrue(result['truncated'])
        import json
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False).encode()),65536)

    def test_cancellation_and_timeout(self):
        self.manager.connect(self.config).result(5)
        event = threading.Event()
        timer = threading.Timer(.15,event.set)
        timer.start()
        self.addCleanup(timer.join)
        start = time.monotonic()
        result = self.manager.call('fixture','slow',{'seconds':20},event)
        self.assertTrue(result['cancelled'],result)
        self.assertLess(time.monotonic()-start,2)
        result = self.manager.call('fixture','add',{'a':1,'b':1},threading.Event())
        self.assertTrue(result['ok'],result)

    def test_http(self):
        sock = socket.socket()
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
        sock.close()
        process = subprocess.Popen([sys.executable,FIXTURE,str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (process.terminate(),process.wait(timeout=5)))
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.1):
                    break
            except OSError:
                time.sleep(.03)
        self.manager.connect({'mcpServers':{'http':{'url':f'http://127.0.0.1:{port}/mcp','timeout':3}}}).result(5)
        result = self.manager.call('http','add',{'a':4,'b':5},threading.Event())
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['structured_content']['result'],9)
