"""A missing foreground shell cannot release finish without a group receipt."""
import contextlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import forge


class LostCustody(unittest.TestCase):
    def test_lost_shell_blocks_finish_until_explicit_terminal_receipt(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root = Path(tmp); tid = 'code-12345678'
            for name, value in [('TASKS_DIR', root / 'tasks'), ('BASE', root),
                                ('_RUNNERS', {}), ('_event', mock.Mock()),
                                ('_remote_client', lambda _: None), ('_pid_alive', lambda *a: False)]:
                stack.enter_context(mock.patch.object(forge, name, value))
            forge._save_task({'id': tid, 'root': str(root), 'status': 'active', 'goal': 'test'})
            directory = forge._unit_dir(tid, 'processes', 'proc-12345678')
            forge._atomic_json(directory / 'request.json', {
                'id': 'proc-12345678', 'task_id': tid, 'status': 'running',
                'command_custody': 'foreground-group-v1',
                'supervisor_pid': 123, 'supervisor_started_at': 'original-shell'})
            self.assertEqual(forge._unit_state(directory)['status'], 'unknown')
            self.assertIn('blocked', forge._finish_survey(tid))
            # Only the existing explicit stop/reconciliation protocol writes this receipt.
            forge._atomic_json(directory / 'result.json', {'status': 'stopped', 'group_exited': True})
            self.assertEqual(forge._unit_state(directory)['status'], 'stopped')
            self.assertNotIn('blocked', forge._finish_survey(tid))


if __name__ == '__main__': unittest.main()
