"""Real Forge journal discovery, owner process observations and cursor boundaries.

No models, credentials, live profiles or application service. Run from root:
python desk/tests/t_forge_activity_230.py
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'desk'), str(ROOT / 'helene/core')]
from deskd import forge_reader as reader
import forge_liveness as live
import forge_runtime


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class Activity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.forge-230-', dir=ROOT)
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def unit(self, task, unit, *, result=None, request=None):
        folder = self.base / 'memory/.forge/tasks' / task
        put(folder / 'task.json', {'id': task, 'goal': 'Long task 🐍', 'status': 'done', 'created': task})
        directory = folder / 'agents' / unit
        put(directory / 'request.json', {'task_id': task, 'id': unit, 'status': 'running', **(request or {})})
        if result is not None:
            put(directory / 'result.json', {'status': result})
        return directory

    def test_old_active_child_outlives_terminal_parent_and_more_than_thirty_recent_tasks(self):
        parent = self.unit('000-old', 'agent-parent', result='done')
        child = self.unit('000-old', 'agent-child', request={'parent_unit_id': parent.name})
        for n in range(45):
            self.unit(f'{n+1:03}-recent', 'agent-terminal', result='done')
        page = reader.activity(self.base, limit=7)
        self.assertEqual([r['id'] for r in page['active']], ['000-old'])
        units = {r['id']: r for r in page['active'][0]['agents']}
        self.assertEqual(units[child.name]['depth'], 1)
        self.assertEqual(units[child.name]['process_liveness'], 'unknown')
        seen = []
        while True:
            seen.extend(r['id'] for r in page['recent'])
            if not page['next_cursor']:
                break
            page = reader.activity(self.base, limit=7, cursor=page['next_cursor'])
            self.assertEqual([r['id'] for r in page['active']], ['000-old'])
        self.assertEqual(len(seen), 45)
        self.assertEqual(len(set(seen)), 45)
        self.assertNotIn('000-old', seen)
        for cursor in ('not-a-cursor', 'WzFd', '////'):
            with self.assertRaises(reader.ReadError):
                reader.activity(self.base, cursor=cursor)

    def test_paused_waiting_stop_pending_are_not_terminal_or_process_proof(self):
        cases = [('paused', {}, 'paused'), ('running', {'phase': 'waiting'}, 'waiting'), ('running', {}, 'stop_pending')]
        for i, (status, progress, expected) in enumerate(cases):
            directory = self.unit(f'task-{i}', 'agent-one', request={'status': status})
            put(directory / 'progress.json', progress)
            if expected == 'stop_pending':
                put(directory / 'stop.json', {'state': 'pending'})
                put(directory / 'result.json', {'status': 'done'})
            row = reader.task_detail(self.base, f'task-{i}')['agents'][0]
            self.assertEqual(row['status'], expected)
            self.assertEqual(row['process_liveness'], 'unknown')
        self.assertEqual(len(reader.activity(self.base)['active']), 3)

    def test_actual_windows_or_native_process_birth_and_exit_owner_receipts(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
            creationflags=0x08000000 if os.name == 'nt' else 0)
        try:
            identity = live.capture(child.pid)
            self.assertEqual(identity['adapter'], 'psutil-v1')
            request = {'supervisor_pid': child.pid, 'process_identity': identity, 'status': 'running'}
            directory = self.unit('actual-process', 'agent-one', request=request)
            request.update(id='agent-one', task_id='actual-process')
            observed = live.observe(directory, request, put)
            self.assertEqual(observed['state'], 'alive')
            row = reader.task_detail(self.base, 'actual-process')['agents'][0]
            self.assertEqual(row['liveness']['state'], 'alive')
            self.assertNotIn('supervisor_pid', row['liveness'])
            self.assertEqual(row['status'], 'running', 'fresh observation confirms the assigned running process')
            self.assertEqual(row['observation'], {}, 'liveness must not invent tool or model progress')
            child.terminate(); child.wait(10)
            observed = live.observe(directory, request, put)
            self.assertEqual(observed['state'], 'exited')
            self.assertEqual(reader.task_detail(self.base, 'actual-process')['agents'][0]['process_liveness'], 'exited')
            request['process_identity'] = {**identity, 'birth': identity['birth'] + 1}
            put(directory / 'request.json', request)
            self.assertEqual(reader.task_detail(self.base, 'actual-process')['agents'][0]['process_liveness'], 'unknown', 'stale different identity cannot be reused')
        finally:
            if child.poll() is None:
                child.terminate(); child.wait(10)

    def test_failed_identity_is_unknown_and_metadata_does_not_change_worker_assignment(self):
        directory = self.unit('no-birth', 'agent-one', request={'supervisor_pid': os.getpid()})
        request = json.loads((directory / 'request.json').read_bytes())
        self.assertEqual(live.observe(directory, request, put)['state'], 'unknown')
        stable = forge_runtime.request_identity(request)
        request['process_identity'] = live.capture(os.getpid())
        self.assertEqual(stable, forge_runtime.request_identity(request))
        fake = mock.Mock()
        fake.create_time.return_value = request['process_identity']['birth'] + 1
        with mock.patch('psutil.Process', return_value=fake):
            self.assertEqual(live.observe(directory, request, put)['basis'], 'pid_reused')

    def test_real_journal_unicode_pages_retained_after_parent_ends(self):
        directory = self.unit('journal', 'agent-one')
        request = json.loads((directory / 'request.json').read_bytes())
        journal = forge_runtime.Journal(directory)
        try:
            journal.start(request, {'next_boundary': 'model_ready', 'iteration': 1, 'model': 'fixture', 'trace': []})
            text = 'Кириллица🐍漢字\n' * 2000
            reference = journal.payload(text)
            journal.event('tool_result', tool='test', output_ref=reference)
            journal.save('model_ready', 'ready')
        finally:
            journal.close()
        offset, parts = 0, []
        while True:
            page = reader.payload(self.base, 'journal', 'agent-one', reference['sha256'], offset=offset)
            parts.append(page['text'])
            if page['next_offset'] is None:
                break
            offset = page['next_offset']
        self.assertEqual(''.join(parts), text)
        self.assertEqual(hashlib.sha256(''.join(parts).encode()).hexdigest(), reference['sha256'])


if __name__ == '__main__':
    unittest.main()
