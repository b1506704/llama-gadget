from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from llama_gadget.mcp_config import parse_config, load_config, save_config, resolve_server, redact


class ConfigTests(unittest.TestCase):
    def test_roundtrip_permissions_and_failed_save(self):
        config = parse_config('{"mcpServers":{"demo":{"command":"python","args":["server.py"],"disabled":true}}}')
        with TemporaryDirectory() as root:
            path = Path(root,'mcp.json')
            save_config(path, config)
            self.assertEqual(load_config(path), config)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                save_config(path, {'invalid': 1})
            self.assertEqual(load_config(path), config)
            self.assertEqual(load_config(Path(root,'absent')), {'mcpServers':{}})

    def test_validation(self):
        invalid = [[], {'other':{}}, {'mcpServers': []}, {'mcpServers': {'demo': {'command':'','args':[]}}},
                   {'mcpServers': {'demo': {'command':'python','url':'http://localhost/mcp'}}},
                   {'mcpServers': {'demo': {'url':'file:///tmp/a'}}},
                   {'mcpServers': {'demo': {'url':'http://name:password@localhost/mcp'}}},
                   {'mcpServers': {'demo': {'url':'http://localhost/mcp','timeout':True}}},
                   {'mcpServers': {'demo': {'url':'http://localhost/mcp','headers':{'bad':'a\nb'}}}},
                   {'mcpServers': {'demo': {'command':'python','unknown':True}}}]
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                parse_config(json.dumps(config))

    def test_interpolation_redaction(self):
        config = parse_config('{"mcpServers":{"demo":{"url":"https://localhost/mcp","headers":{"Authorization":"Bearer ${env:TOKEN}"}}}}')
        entry = config['mcpServers']['demo']
        with self.assertRaises(ValueError):
            resolve_server(entry, {})
        resolved = resolve_server(entry, {'TOKEN':'private-token'})
        self.assertEqual(resolved['headers']['Authorization'], 'Bearer private-token')
        self.assertEqual(entry['headers']['Authorization'], 'Bearer ${env:TOKEN}')
        self.assertNotIn('private-token', redact('failure Bearer private-token', resolved['_secrets']))
