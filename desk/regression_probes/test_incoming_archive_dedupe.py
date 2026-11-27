"""Offline archive regressions; real transport, temporary dummy archives only.

ARCHIVE_TRANSPORT_SOURCE supports red checks against the preserved baseline.
No runner/window is started and no live data is opened.
"""
import ast
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE = Path(os.environ.get('ARCHIVE_TRANSPORT_SOURCE', ROOT / 'localharness/transport.py'))
spec = importlib.util.spec_from_file_location('archive_transport_test', SOURCE)
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)


class IncomingArchive(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tree = Path(self.temp.name)
        self.desk = transport.Desk(self.tree, 'window', 'Dummy owner', 'Dummy room')
        self.path = self.tree / 'memory/groups/window.jsonl'

    def rows(self):
        return [json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines()]

    def write(self, identity='note:dummy-001', text='dummy message', **kw):
        self.desk.archive(text, outgoing=False, source_id=identity, **kw)

    def test_replay_after_new_desk_preserves_original_bytes_and_registry(self):
        self.write(now=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
        before = self.path.read_bytes()
        registry = self.tree / 'memory/.state/group_context/window.json'
        receipt = registry.read_bytes()
        self.desk = transport.Desk(self.tree, 'window', 'Other name', 'Dummy room')
        self.write(now=dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(registry.read_bytes(), receipt)

    def test_distinct_ids_same_text_remain_distinct(self):
        self.write('note:dummy-001')
        self.write('note:dummy-002')
        self.assertEqual(len(self.rows()), 2)

    def test_identity_not_text_controls_replay(self):
        self.write(text='first dummy text')
        self.write(text='changed dummy text')
        self.assertEqual([r['text'] for r in self.rows()], ['first dummy text'])

    def test_missing_identity_keeps_legacy_append_behavior(self):
        self.write('')
        self.write('')
        self.assertEqual(len(self.rows()), 2)

    def test_outgoing_same_id_is_not_suppressed_or_used_as_incoming(self):
        for _ in range(2):
            self.desk.archive('dummy reply', outgoing=True, source_id='note:dummy-001')
        self.write()
        self.write()
        self.assertEqual([r['outgoing'] for r in self.rows()], [True, True, False])

    def test_legacy_and_malformed_rows_are_not_rewritten(self):
        self.path.parent.mkdir(parents=True)
        old = '{"text":"legacy dummy"}\nnot-json\n[]\n'
        self.path.write_text(old, encoding='utf-8', newline='\n')
        self.write()
        self.write()
        result = self.path.read_text(encoding='utf-8')
        self.assertTrue(result.startswith(old))
        self.assertEqual(len(result.splitlines()), 4)

    def test_scope_is_per_archive_and_attachment_ids_are_distinct(self):
        self.write()
        self.write('note:dummy-001-file-0')
        self.write('note:dummy-001-file-0')
        other = transport.Desk(self.tree, 'window-dummy', 'Dummy', 'Other')
        other.archive('dummy message', outgoing=False, source_id='note:dummy-001')
        self.assertEqual(len(self.rows()), 2)
        self.assertTrue((self.tree / 'memory/groups/window-dummy.jsonl').exists())

    def test_concurrent_replays_across_desk_instances(self):
        barrier = threading.Barrier(12)
        def replay(_):
            desk = transport.Desk(self.tree, 'window', 'Dummy', 'Dummy')
            barrier.wait(timeout=10)
            desk.archive('dummy concurrent text', outgoing=False, source_id='note:concurrent')
        with ThreadPoolExecutor(max_workers=12) as workers:
            list(workers.map(replay, range(12)))
        self.assertEqual(len(self.rows()), 1)

    def test_mixed_concurrent_replays_distinct_ids_and_outgoing(self):
        barrier = threading.Barrier(12)
        def write(index):
            barrier.wait(timeout=10)
            if index < 4:
                self.write('note:shared')
            elif index < 8:
                self.write('note:distinct-' + str(index))
            else:
                self.desk.archive('dummy outgoing', outgoing=True, source_id='note:shared')
        with ThreadPoolExecutor(max_workers=12) as workers:
            list(workers.map(write, range(12)))
        rows = self.rows()
        self.assertEqual(sum(not r['outgoing'] for r in rows), 5)
        self.assertEqual(sum(r['outgoing'] for r in rows), 4)

    def test_runner_replay_still_attempts_unfinished_work(self):
        # Execute the real perception function without runner startup. Dedupe is
        # an archive concern, NOT permission to skip the unfinished agent turn.
        source = ROOT / 'localharness/runner.py'
        module = ast.parse(source.read_text(encoding='utf-8-sig'))
        node = next(n for n in module.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'handle_desk')
        from contextvars import ContextVar
        turns = []
        env = {'STREAM': 'window', 'Path': Path, '_IMAGE_EXT': {'.png'},
               '_now': lambda: dt.datetime.now(dt.timezone.utc),
               '_room': lambda room: self.desk,
               '_hear_attachments': lambda paths: ([], paths),
               '_ingest_attachments': lambda *a, **kw: ([], []),
               '_speaker': 'Dummy', '_INITIAL_FILES': ContextVar('dummy', default=None),
               '_turn_in_window': lambda source_id, **kw: turns.append(source_id)}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), env)
        for _ in range(2):
            env['handle_desk']('dummy replay', ingress_id='note:dummy-recovery')
        self.assertEqual(turns, ['note:dummy-recovery', 'note:dummy-recovery'])
        self.assertEqual(len(self.rows()), 1)

    def test_read_failure_is_not_silently_treated_as_missing(self):
        self.write()
        before = self.path.read_bytes()
        original = Path.open
        def fail(path, *args, **kw):
            if path == self.path and not args:
                raise PermissionError('dummy denied read')
            return original(path, *args, **kw)
        with patch.object(Path, 'open', fail):
            with self.assertRaises(PermissionError):
                self.write()
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
