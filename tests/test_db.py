import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from llama_gadget.db import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'chat.db'
        self.db = Database()
        self.db.open(self.path)
        self.addCleanup(lambda: self.db.conn.close())
        self.sid = self.db.create_session('test', 'tools')

    def test_exchange_roundtrip_reopen(self):
        calls = [{'id': 'call_1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":"note"}'}}]
        saved = self.db.add_exchange(self.sid, [
            {'role': 'assistant', 'content': 'Checking', 'tool_calls': calls},
            {'role': 'tool', 'content': '{"ok":true}', 'tool_call_id': 'call_1', 'name': 'read_file'},
        ])
        self.assertEqual(saved[0]['metadata']['tool_calls'], calls)
        self.db.open(self.path)
        loaded = self.db.load_messages(self.sid)
        self.assertEqual(saved, loaded)
        self.assertEqual(loaded[1]['metadata']['tool_call_id'], 'call_1')

    def test_exchange_rolls_back(self):
        with self.assertRaises(KeyError):
            self.db.add_exchange(self.sid, [{'role': 'assistant', 'content': 'Checking'}, {'role': 'tool'}])
        self.assertEqual(self.db.load_messages(self.sid), [])

    def test_old_database_migration_is_idempotent(self):
        old_path = Path(self.temp.name) / 'old.db'
        conn = sqlite3.connect(old_path)
        conn.executescript('''
        CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL,
        role TEXT NOT NULL, content TEXT NOT NULL, attachments TEXT NOT NULL DEFAULT '[]', ts TEXT NOT NULL);
        INSERT INTO messages(session_id,role,content,ts) VALUES(1,'user','Old message','2026-10-05 10:00:00');
        ''')
        conn.close()
        self.db.open(old_path)
        self.db.open(old_path)
        loaded = self.db.load_messages(1)
        self.assertEqual(loaded[0]['content'], 'Old message')
        self.assertEqual(loaded[0]['metadata'], {})

    def test_old_message_api_and_metadata(self):
        self.db.add_message(self.sid, 'user', 'Hi', [{'name': 'note', 'text': 'hello'}])
        self.assertEqual(self.db.load_messages(self.sid)[0]['metadata'], {})
        self.db.add_message(self.sid, 'assistant', '', metadata={'tool_calls': []})
        self.assertEqual(self.db.load_messages(self.sid)[1]['metadata'], {'tool_calls': []})
