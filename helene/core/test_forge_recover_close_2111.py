"""Custody recovery must finish the queue: no ownerless active task survives it."""
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import forge
import forge_recovery
import forge_runtime
import forge_windows_custody as custody
import frame_language


def task_status(task_id):
    return (forge.get(task_id) or {}).get('status')


class BaseFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base/'workspace'; self.root.mkdir()
        for name, value in [('BASE', self.base), ('STATE_DIR', self.base/'memory/.forge'),
                            ('TASKS_DIR', self.base/'memory/.forge/tasks'), ('REPO', self.base)]:
            patch = mock.patch.object(forge, name, value); patch.start(); self.addCleanup(patch.stop)
        self.tid = 'code-recover-close'
        forge._save_task({'id': self.tid, 'root': str(self.root), 'scope': 'self',
                          'status': 'active', 'goal': 'recovery closes the queue'})

    def unit(self, name, kind='agents', parent='', *, running_dead=False, result=None, stop=None):
        d = forge._unit_dir(self.tid, kind, name)
        d.mkdir(parents=True, exist_ok=True)
        request = {'id': name, 'task_id': self.tid, 'root': str(self.root), 'scope': 'self',
                   'created': forge._now(), 'status': 'starting', 'role': 'worker',
                   'parent_unit_id': parent}
        if running_dead:
            request.update(supervisor_pid=2147483000, supervisor_started_at='fixture-birth',
                           status='running')
        forge._atomic_json(d/'request.json', request)
        if result is not None:
            forge._atomic_json(d/'result.json', result)
        if stop is not None:
            forge._atomic_json(d/'stop.json', stop)
        return d

    def last_event(self):
        rows = (forge._task_dir(self.tid)/'events.jsonl').read_text(encoding='utf-8').splitlines()
        return json.loads(rows[-1]) if rows else {}


class RecoverySettlement(BaseFixture):
    def test_recover_closes_task_once_nothing_outstanding_remains(self):
        self.unit('agent-done', result={'status': 'done', 'finished': forge._now()})
        self.unit('proc-done', 'processes', result={'status': 'done'})
        settlement = forge._recovery_settlement(self.tid, 'recover')
        self.assertTrue(settlement['task_closed'])
        self.assertEqual(settlement['task_status'], 'stopped')
        self.assertEqual(settlement['unresolved_units'], [])
        self.assertEqual(task_status(self.tid), 'stopped')
        self.assertEqual(self.last_event()['kind'], 'task_closed_after_recovery')
        # Idempotent: a second recover of the same settled queue changes nothing.
        again = forge._recovery_settlement(self.tid, 'recover')
        self.assertFalse(again['task_closed'])
        self.assertEqual(again['task_status'], 'stopped')

    def test_outstanding_unit_or_prepare_action_never_closes(self):
        pending = self.unit('agent-unknown', stop={'state': 'pending'})
        self.unit('agent-done', result={'status': 'done'})
        settlement = forge._recovery_settlement(self.tid, 'recover')
        self.assertFalse(settlement['task_closed'])
        self.assertEqual(settlement['unresolved_units'], [pending.name])
        self.assertEqual(task_status(self.tid), 'active')
        prepare = forge._recovery_settlement(self.tid, 'prepare_recovery')
        self.assertFalse(prepare['task_closed'])
        self.assertEqual(task_status(self.tid), 'active')

    def test_reason_appends_the_honest_next_step(self):
        closed = {'task_closed': True, 'unresolved_units': [], 'task_status': 'stopped'}
        self.assertEqual(forge._recovery_reason('BASE', closed, selected=True, show_outstanding=True),
                         'BASE ' + frame_language.text('forge.recovery.task_closed'))
        blocked = {'task_closed': False, 'unresolved_units': ['agent-x'], 'task_status': 'active'}
        self.assertEqual(forge._recovery_reason('BASE', blocked, selected=True, show_outstanding=True),
                         'BASE ' + frame_language.text('forge.recovery.remaining', v0='agent-x')
                         + ' ' + frame_language.text('forge.recovery.next_close'))
        self.assertEqual(forge._recovery_reason('BASE', blocked, selected=True, show_outstanding=False),
                         'BASE')
        hint = {'task_closed': False, 'unresolved_units': [], 'task_status': 'active'}
        self.assertEqual(forge._recovery_reason('BASE', hint, selected=False, show_outstanding=True),
                         'BASE ' + frame_language.text('forge.recovery.close_hint'))


@unittest.skipUnless(os.name == 'nt', 'Windows reboot witness')
class WindowsRecoveryClosesTask(BaseFixture):
    def legacy(self, name='agent-old', kind='agents', parent=''):
        d = self.unit(name, kind, parent, running_dead=True)
        forge._atomic_json(d/'stop.json', {'state': 'unresolved', 'note': 'tree exit unproven'})
        journal = forge_runtime.Journal(d)
        journal.start(forge._read_json(d/'request.json'), {'next_boundary': 'summary_in_flight'})
        journal.payload('preserve partial summary'); journal.close()
        return d

    def boundary(self, boot):
        return {'host_id': custody.host_identity(), 'boot_id': f'fixture-boot-{boot}', 'boot_time': 10000+boot}

    def prepare(self, agent_id=''):
        generation = ''
        if agent_id:
            generation = forge_runtime.read_progress(
                forge._unit_dir(self.tid, 'agents', agent_id))['generation']
        return forge.control(self.tid, 'prepare_recovery', agent_id, generation,
                             host_binding='verified-local-execution-host',
                             reason='original execution host independently confirmed for fixture')

    def test_whole_task_recover_closes_the_queue(self):
        d = self.legacy()
        original = {p.relative_to(d): p.read_bytes() for p in d.rglob('*') if p.is_file()}
        with mock.patch.object(custody, 'boot_boundary', return_value=self.boundary(1)):
            self.assertEqual(self.prepare()['status'], 'prepared')
        with mock.patch.object(custody, 'boot_boundary', return_value=self.boundary(2)):
            result = forge.control(self.tid, 'recover')
        self.assertEqual(result['status'], 'settled', result)
        self.assertTrue(result['task_closed'])
        self.assertEqual(result['task_status'], 'stopped')
        self.assertEqual(task_status(self.tid), 'stopped')
        self.assertEqual(self.last_event()['kind'], 'task_closed_after_recovery')
        self.assertEqual(result['reason'],
                         frame_language.text('forge.recovery.recovered') + ' '
                         + frame_language.text('forge.recovery.task_closed'))
        # Recovery still proves nothing about the lost outcome.
        for path, raw in original.items():
            self.assertEqual((d/path).read_bytes(), raw, str(path))
        self.assertFalse((d/'result.json').exists())
        self.assertFalse(forge_runtime.continuation_info(d)['safe'])

    def test_scoped_recover_names_the_remaining_unit_and_keeps_task_open(self):
        first = self.legacy('agent-first')
        second = self.legacy('agent-second')
        with mock.patch.object(custody, 'boot_boundary', return_value=self.boundary(1)):
            self.assertEqual(self.prepare(first.name)['status'], 'prepared')
            self.assertEqual(self.prepare(second.name)['status'], 'prepared')
        with mock.patch.object(custody, 'boot_boundary', return_value=self.boundary(2)):
            result = forge.control(self.tid, 'recover', first.name)
        self.assertEqual(result['status'], 'settled', result)
        self.assertFalse(result['task_closed'])
        self.assertEqual(result['task_status'], 'active')
        self.assertEqual(result['unresolved_units'], [second.name])
        self.assertEqual(result['reason'],
                         frame_language.text('forge.recovery.recovered') + ' '
                         + frame_language.text('forge.recovery.remaining', v0=second.name) + ' '
                         + frame_language.text('forge.recovery.next_close'))
        self.assertTrue(forge._unit_state(first)['custody_recovered'])
        self.assertIsNone(forge_recovery.read_recovery(second))
        with mock.patch.object(custody, 'boot_boundary', return_value=self.boundary(2)):
            closing = forge.control(self.tid, 'recover')
        self.assertEqual(closing['status'], 'settled', closing)
        self.assertTrue(closing['task_closed'])
        self.assertEqual(task_status(self.tid), 'stopped')

    def test_reverse_case_recovered_active_task_closes_without_selection(self):
        d = self.legacy()
        with mock.patch.object(custody, 'boot_boundary', return_value=self.boundary(1)):
            self.prepare()
        with mock.patch.object(custody, 'boot_boundary', return_value=self.boundary(2)):
            forge.control(self.tid, 'recover', d.name)  # scoped: the 2.8.12 leftover state
        task = forge.get(self.tid); task.update(status='active', finished='')
        forge._save_task(task)
        self.assertEqual(task_status(self.tid), 'active')
        with mock.patch.object(custody, 'boot_boundary', return_value=self.boundary(2)):
            result = forge.control(self.tid, 'recover')
        self.assertEqual(result['status'], 'settled', result)
        self.assertEqual(result['recovered_units'], [])
        self.assertTrue(result['task_closed'])
        self.assertEqual(task_status(self.tid), 'stopped')
        self.assertTrue(result['reason'].endswith(frame_language.text('forge.recovery.task_closed')))

    def test_group_stop_of_reversed_task_explains_the_next_stage(self):
        d = self.legacy()
        with mock.patch.object(custody, 'boot_boundary', return_value=self.boundary(1)):
            self.prepare()
        with mock.patch.object(custody, 'boot_boundary', return_value=self.boundary(2)):
            forge.control(self.tid, 'recover', d.name)
        task = forge.get(self.tid); task.update(status='active', finished='')
        forge._save_task(task)
        result = forge.control(self.tid, 'stop')
        self.assertEqual(result['status'], 'settled', result)
        self.assertEqual(result['note'], frame_language.text('forge.recovery.stopped_recovered'))
        self.assertEqual(task_status(self.tid), 'stopped')


class LinuxHistoryRecoveryClosesTask(BaseFixture):
    def setUp(self):
        super().setUp()
        self.boundary = {'boot_id': 'boot-before', 'host_id': 'fixture-host',
                         'boot_time': int(dt.datetime.now(dt.timezone.utc).timestamp()) - 900}
        patch = mock.patch.object(forge_recovery, 'kernel_boundary',
                                  side_effect=lambda: dict(self.boundary))
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(sys, 'platform', 'linux')
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(forge, '_pid_alive', return_value=False)
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(forge_recovery, 'windows_custody', return_value=custody)
        patch.start(); self.addCleanup(patch.stop)
        self.journal = forge._task_dir(self.tid)/'events.jsonl'
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        born = dt.datetime.fromtimestamp(self.boundary['boot_time']-3600, dt.timezone.utc).isoformat()
        self.born = born
        self.journal.write_bytes(b'')
        self.legacy('agent-old', born=born)
        self.legacy('agent-next', born=born)

    def legacy(self, name, *, born):
        d = self.unit(name, running_dead=True)
        forge._atomic_json(d/'request.json', {**forge._read_json(d/'request.json'), 'created': born})
        forge._atomic_json(d/'stop.json', {'state': 'unresolved', 'note': 'original unknown outcome'})
        with self.journal.open('ab') as out:
            out.write((json.dumps({'at': born, 'kind': 'agent_spawned', 'agent_id': name})+'\n').encode())
        return d

    def prepare(self, agent_id=''):
        return forge.control(self.tid, 'prepare_recovery', agent_id,
                             host_binding='verified-local-execution-host',
                             reason='owner verified the original execution host')

    def reboot(self):
        self.boundary = {**self.boundary, 'boot_id': 'boot-after',
                         'boot_time': self.boundary['boot_time']+600}

    def test_whole_task_recover_closes_the_queue(self):
        self.assertEqual(self.prepare()['status'], 'prepared')
        witness = (forge._unit_dir(self.tid, 'agents', 'agent-old')/forge_recovery.HISTORY_WITNESS).read_bytes()
        self.reboot()
        result = forge.control(self.tid, 'recover')
        self.assertEqual(result['status'], 'settled', result)
        self.assertTrue(result['task_closed'])
        self.assertEqual(result['task_status'], 'stopped')
        self.assertEqual(task_status(self.tid), 'stopped')
        self.assertEqual(self.last_event()['kind'], 'task_closed_after_recovery')
        # The retained witness and history stay byte-exact through closure.
        self.assertEqual((forge._unit_dir(self.tid, 'agents', 'agent-old')
                          /forge_recovery.HISTORY_WITNESS).read_bytes(), witness)
        self.assertTrue(forge_recovery.read_recovery(forge._unit_dir(self.tid, 'agents', 'agent-old')))

    def test_scoped_recover_reports_the_remaining_unit(self):
        self.prepare('agent-old'); self.prepare('agent-next')
        self.reboot()
        result = forge.control(self.tid, 'recover', 'agent-old')
        self.assertEqual(result['status'], 'settled', result)
        self.assertFalse(result['task_closed'])
        self.assertEqual(result['unresolved_units'], ['agent-next'])
        self.assertEqual(task_status(self.tid), 'active')

    def test_reverse_case_closes_and_group_stop_names_the_next_stage(self):
        self.prepare(); self.reboot()
        scoped = forge.control(self.tid, 'recover', 'agent-old')
        self.assertEqual(scoped['status'], 'settled', scoped)
        self.assertEqual(scoped['unresolved_units'], ['agent-next'])
        closing = forge.control(self.tid, 'recover')
        self.assertTrue(closing['task_closed'])
        task = forge.get(self.tid); task.update(status='active', finished='')
        forge._save_task(task)
        stopped = forge.control(self.tid, 'stop')
        self.assertEqual(stopped['status'], 'settled', stopped)
        self.assertEqual(stopped['note'], frame_language.text('forge.recovery.stopped_recovered'))
        self.assertEqual(task_status(self.tid), 'stopped')


if __name__ == '__main__':
    unittest.main()
