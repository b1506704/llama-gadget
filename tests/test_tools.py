import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from llama_gadget import tools


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = tools.ToolRegistry(self.root)
        self.cancel = threading.Event()

    def test_read_and_listing(self):
        (self.root / 'note.txt').write_text('hello')
        self.assertEqual(self.registry.execute('read_file', {'path': 'note.txt'}, self.cancel)['output'], 'hello')
        self.assertIn('note.txt', self.registry.execute('list_directory', {'path': '.'}, self.cancel)['output'])
        self.assertEqual(len(self.registry.schemas()), 4)
        self.assertFalse(self.registry.requires_approval('read_file'))
        self.assertTrue(self.registry.requires_approval('write_file'))
        self.assertTrue(self.registry.requires_approval('bash_command'))

    def test_validate_arguments(self):
        for name, args in [('missing', '{}'), ('read_file', '{'), ('read_file', '[]'),
                           ('read_file', '{}'), ('read_file', '{"path": 2}'),
                           ('read_file', '{"path":"a", "extra": 1}')]:
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                self.registry.prepare(name, args)
        self.assertEqual(self.registry.prepare('read_file', '{"path":"note.txt"}')['path'], str(self.root / 'note.txt'))

    def test_paths_cannot_escape(self):
        with TemporaryDirectory() as outside:
            (Path(outside) / 'secret').write_text('secret')
            (self.root / 'escape').symlink_to(outside, target_is_directory=True)
            for path in ['../secret', str(Path(outside) / 'secret'), 'escape/secret']:
                for name in ['read_file', 'write_file', 'list_directory']:
                    args = {'path': path}
                    if name == 'write_file':
                        args['content'] = 'bad'
                    self.assertFalse(self.registry.execute(name, args, self.cancel)['ok'])
            self.assertEqual((Path(outside) / 'secret').read_text(), 'secret')

    def test_write_and_limits(self):
        (self.root / 'note').write_text('old')
        self.assertTrue(self.registry.execute('write_file', {'path': 'note', 'content': 'new'}, self.cancel)['ok'])
        self.assertEqual((self.root / 'note').read_text(), 'new')
        self.assertFalse(self.registry.execute('write_file', {'path': 'missing/note', 'content': 'x'}, self.cancel)['ok'])
        self.assertFalse(self.registry.execute('write_file', {'path': 'note', 'content': 'x' * (1024*1024+1)}, self.cancel)['ok'])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['note'])
        (self.root / 'big').write_text('x'*100000)
        result = self.registry.execute('read_file', {'path': 'big'}, self.cancel)
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(result['output'].encode()), 65536)
        (self.root / 'binary').write_bytes(b'\0bad')
        self.assertFalse(self.registry.execute('read_file', {'path': 'binary'}, self.cancel)['ok'])

    def test_commands_bounded_output_and_exit(self):
        result = self.registry.execute('bash_command', {'command': "pwd; printf err >&2; exit 3"}, self.cancel)
        self.assertEqual(result['exit_code'], 3)
        self.assertIn(str(self.root), result['stdout'])
        self.assertEqual(result['stderr'], 'err')
        result = self.registry.execute('bash_command', {'command': 'head -c 200000 /dev/zero'}, self.cancel)
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(result['stdout'].encode()), 65536)

    def test_timeout_and_cancellation(self):
        self.registry.command_timeout = 0.15
        started = time.monotonic()
        result = self.registry.execute('bash_command', {'command': 'sleep 30'}, self.cancel)
        self.assertTrue(result['timed_out'])
        self.assertLess(time.monotonic() - started, 3)
        self.registry.command_timeout = 60
        timer = threading.Timer(.15, self.cancel.set)
        timer.start()
        self.addCleanup(timer.join)
        result = self.registry.execute('bash_command', {'command': 'sleep 30'}, self.cancel)
        self.assertTrue(result['cancelled'])
        self.assertFalse(self.registry.execute('write_file', {'path': 'no', 'content': 'x'}, self.cancel)['ok'])
        self.assertFalse((self.root / 'no').exists())

    def test_cancellation_stops_background_child(self):
        timer = threading.Timer(.2, self.cancel.set)
        timer.start()
        self.addCleanup(timer.join)
        result = self.registry.execute('bash_command', {'command': 'sleep 30 & echo $! > child.pid; wait'}, self.cancel)
        self.assertTrue(result['cancelled'])
        pid = (self.root / 'child.pid').read_text().strip()
        try:
            state = Path('/proc', pid, 'stat').read_text().split()[2]
        except (FileNotFoundError, ProcessLookupError):
            state = None
        self.assertIn(state, (None, 'Z'))  # A terminated child may await OS reaping.

    def test_directory_entry_limit(self):
        for i in range(1001):
            (self.root / str(i)).touch()
        result = self.registry.execute('list_directory', {'path': '.'}, self.cancel)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['output'].splitlines()), 1000)


if __name__ == '__main__':
    unittest.main()


class OutputBoundaryTests(unittest.TestCase):
    def test_workspace_ancestor_substitution_is_rejected(self):
        with TemporaryDirectory() as root:
            root = Path(root)
            workspace = root / 'allowed' / 'workspace'
            workspace.mkdir(parents=True)
            outside = root / 'outside' / 'workspace'
            outside.mkdir(parents=True)
            (outside / 'note').write_text('keep')
            registry = tools.ToolRegistry(workspace)
            args = registry.prepare('write_file', {'path': 'note', 'content': 'bad'})
            real_open = tools.os.open
            swapped = False
            def swap_before_open(path, flags, *args, **kwargs):
                nonlocal swapped
                if not swapped and flags & tools.os.O_DIRECTORY and kwargs.get('dir_fd') is None:
                    swapped = True
                    (root / 'allowed').rename(root / 'parked')
                    (root / 'allowed').symlink_to(root / 'outside', target_is_directory=True)
                return real_open(path, flags, *args, **kwargs)
            with patch.object(tools.os, 'open', side_effect=swap_before_open):
                result = registry.execute('write_file', args, threading.Event(), prepared=True)
            self.assertTrue(swapped)
            self.assertFalse(result['ok'])
            self.assertEqual((outside / 'note').read_text(), 'keep')

    def test_error_output_is_bounded(self):
        with TemporaryDirectory() as root:
            result = tools.ToolRegistry(root).execute('read_file', {'path': 'a'*100000}, threading.Event())
            self.assertFalse(result['ok'])
            self.assertLessEqual(len(result['error'].encode()), 65536)
            self.assertTrue(result['truncated'])

    def test_directory_output_is_bounded(self):
        with TemporaryDirectory() as root:
            registry = tools.ToolRegistry(root)
            for i in range(400):
                Path(root, str(i) + 'a'*200).touch()
            result = registry.execute('list_directory', {'path': '.'}, threading.Event())
            self.assertTrue(result['truncated'])
            self.assertLessEqual(len(result['output'].encode('utf-8')), 65536)

    def test_binary_command_output_is_bounded_after_decoding(self):
        with TemporaryDirectory() as root:
            registry = tools.ToolRegistry(root)
            result = registry.execute('bash_command', {'command': r"head -c 100000 /dev/zero | tr '\000' '\377'"}, threading.Event())
            self.assertTrue(result['truncated'])
            self.assertLessEqual(len((result['stdout'] + result['stderr']).encode('utf-8')), 65536)

    def test_write_target_cannot_change_after_preparation(self):
        with TemporaryDirectory() as root:
            registry = tools.ToolRegistry(root)
            args = registry.prepare('write_file', {'path': 'approved', 'content': 'x'})
            Path(root, 'other').write_text('keep')
            Path(root, 'approved').symlink_to(Path(root, 'other'))
            result = registry.execute('write_file', args, threading.Event(), prepared=True)
            self.assertFalse(result['ok'])
            self.assertEqual(Path(root, 'other').read_text(), 'keep')
