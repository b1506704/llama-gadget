import threading
import unittest
from llama_gadget.tool_catalog import ToolCatalog

class CatalogTests(unittest.TestCase):
    def test_typed_schemas_names_and_frozen_generation(self):
        from llama_gadget.mcp_client import MCPManager
        from tests.test_mcp_client import FIXTURE
        import sys
        manager = MCPManager()
        self.addCleanup(manager.close)
        manager.connect({'mcpServers':{'fixture':{'command':sys.executable,'args':[FIXTURE]}}}).result(5)
        catalog = ToolCatalog(None,manager,manager.snapshot())
        name = next(s['function']['name'] for s in catalog.schemas() if catalog.describe(s['function']['name'])['name']=='add')
        self.assertTrue(catalog.requires_approval(name))
        self.assertEqual(catalog.prepare(name,'{"a":2,"b":3}'),{'a':2,'b':3})
        with self.assertRaises(ValueError):
            catalog.prepare(name,'{"a":"bad","b":3}')
        result = catalog.execute(name,{'a':2,'b':3},threading.Event())
        self.assertEqual(result['structured_content']['result'],5)
        manager.disconnect().result(5)
        self.assertFalse(catalog.execute(name,{'a':2,'b':3},threading.Event())['ok'])

    def test_collision_and_schema_rejection(self):
        schema={'type':'object','properties':{'value':{'type':'integer'}}}
        entries=[{'server':server,'original_name':'long tool?'*20,'description':'test','input_schema':schema,'generation':1} for server in ('a','b')]
        catalog = ToolCatalog(None,None,entries)
        names=[s['function']['name'] for s in catalog.schemas()]
        self.assertEqual(len(set(names)),2)
        self.assertTrue(all(len(n)<=64 for n in names))
        bad=dict(entries[0],input_schema={'type':'object','$ref':'https://example.com/schema'})
        self.assertEqual(ToolCatalog(None,None,[bad]).schemas(),[])

    def test_chat_worker_mcp_integration(self):
        import json, os, sys
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from PyQt6.QtCore import Qt
        from llama_gadget.chat import ChatWorker
        from llama_gadget.mcp_client import MCPManager
        from tests.support import StreamingServer, call_delta, chunk
        from tests.test_mcp_client import FIXTURE

        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        log = str(Path(temp.name, 'calls'))
        manager = MCPManager()
        self.addCleanup(manager.close)
        config = {'mcpServers': {'fixture': {'command': sys.executable, 'args': [FIXTURE],
                                             'env': {'FIXTURE_LOG': log}, 'timeout': 3}}}
        manager.connect(config).result(5)
        catalog = ToolCatalog(None, manager, manager.snapshot())
        add_tool = next(s['function']['name'] for s in catalog.schemas() if catalog.describe(s['function']['name'])['name'] == 'add')

        # 1. Denial produces a result without invocation & no invocation before approval
        server = StreamingServer([
            [chunk(call_delta(name=add_tool, arguments='{"a": 2, "b": 3}')), chunk(finish='tool_calls'), '[DONE]'],
            [chunk({'content': 'Denied'}), chunk(finish='stop'), '[DONE]'],
        ])
        self.addCleanup(server.close)
        worker = ChatWorker([{'role': 'user', 'content': 'Calculate'}], base_url=server.url, registry=catalog)
        approvals = []
        def on_approval_denied(cid, name, args):
            self.assertFalse(Path(log).exists())
            approvals.append((cid, name, args))
            worker.decide(cid, False)
        worker.approval_requested.connect(on_approval_denied, Qt.ConnectionType.DirectConnection)
        worker.run()
        self.assertEqual(len(approvals), 1)
        self.assertFalse(Path(log).exists())
        last_req = server.requests[1]['messages'][-1]
        self.assertEqual(last_req['role'], 'tool')
        tool_res = json.loads(last_req['content'])
        self.assertFalse(tool_res['ok'])
        self.assertTrue(tool_res['denied'])

        # 2. Approved typed call feeds output into next request
        server2 = StreamingServer([
            [chunk(call_delta(name=add_tool, arguments='{"a": 2, "b": 3}')), chunk(finish='tool_calls'), '[DONE]'],
            [chunk({'content': 'Result is 5'}), chunk(finish='stop'), '[DONE]'],
        ])
        self.addCleanup(server2.close)
        worker2 = ChatWorker([{'role': 'user', 'content': 'Calculate'}], base_url=server2.url, registry=catalog)
        def on_approval_granted(cid, name, args):
            worker2.decide(cid, True)
        worker2.approval_requested.connect(on_approval_granted, Qt.ConnectionType.DirectConnection)
        worker2.run()
        self.assertTrue(Path(log).exists())
        self.assertEqual(Path(log).read_text(), 'add\n')
        last_req2 = server2.requests[1]['messages'][-1]
        tool_res2 = json.loads(last_req2['content'])
        self.assertTrue(tool_res2['ok'])
        self.assertEqual(tool_res2['structured_content']['result'], 5)

        # 3. Replacing config cannot reroute a pending call (generation mismatch)
        manager.disconnect().result(5)
        manager.connect(config).result(5)
        res = catalog.execute(add_tool, {'a': 1, 'b': 2}, threading.Event())
        self.assertFalse(res['ok'])
        self.assertIn('unavailable', res['error'])

