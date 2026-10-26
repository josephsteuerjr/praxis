"""Recovery double-fault and quiescent update-history consumers, no processes/models."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import python_source_updater as updater, update_overlay as overlay
from localharness import source_update as pipeline, update_io, update_progress
from localharness.package_notice import read, write


def put(root, name, data=b'fixture'):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.old = self.root / 'application'
        self.staging = self.root / '.helene-update-fault'
        self.new = self.staging / 'Helene'
        self.recovery = self.root / '.helene-recovery-fault'
        for name in ('a', 'b'):
            put(self.old, name, b'old-' + name.encode())
            put(self.new, name, b'new-' + name.encode())
        self.note = {'id': 'fault', 'data_paths': [], 'new_root': str(self.old)}
        self.progress = update_progress.Progress(self.root / '.helene-update-fault.progress.json', 'fault', self.old)
        overlay.prepare(self.new, self.old, self.recovery)
        self.prepared = {'format': 'helene-prepared-install.v1',
                         'old': str(self.old.resolve()), 'new': str(self.new.resolve()),
                         'note': self.note, 'recovery': str(self.recovery), 'progress': str(self.progress.path)}
        write(updater.prepared_install_path(self.new), self.prepared)
        self.args = SimpleNamespace(old=self.old, new=self.new, staging=self.staging,
                                    archive=[], pid=0, cleanup=False, browser=False)

    def fault(self, restore_fails=True):
        actual = overlay.copy_verified
        def copy(source, target, sha, progress=None, category='copy'):
            if category == 'publish_copy' and Path(source).name == 'b':
                raise OSError('injected publish disk full')
            if category == 'restore_copy' and restore_fails:
                raise OSError('injected restore disk full')
            return actual(source, target, sha, progress, category)
        return patch.object(overlay, 'copy_verified', side_effect=copy)

    def test_double_failure_keeps_complete_intent_does_not_reopen_and_startup_recovers(self):
        journal = (self.recovery / 'update-files.json').read_bytes()
        with patch.object(updater, 'validate_prepared', return_value=self.prepared), self.fault(), \
             patch.object(updater.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(overlay.RestorationIncomplete, 'restoration is incomplete'):
                updater.perform(self.args)
            launch.assert_not_called()
        self.assertEqual((self.old / 'a').read_bytes(), b'new-a')
        self.assertEqual((self.old / 'b').read_bytes(), b'old-b')
        self.assertTrue(updater.active_update(self.old).is_file())
        self.assertEqual((self.recovery / 'update-files.json').read_bytes(), journal)
        for name in ('a', 'b'):
            self.assertEqual((self.recovery / name).read_bytes(), b'old-' + name.encode())
        status = update_progress.status(self.old)
        self.assertIs(status['rollback_completed'], False)
        self.assertIs(status['previous_reopen_requested'], False)
        self.assertIs(status['previous_program_retained'], False)
        self.assertIs(status['recovery_required'], True)
        # A second failure does not discard the same recoverable transaction.
        with patch.object(update_io, 'copy_verified', side_effect=OSError('still no room')):
            with self.assertRaisesRegex(OSError, 'still no room'):
                updater.recover_startup(self.old)
        self.assertTrue(updater.active_update(self.old).is_file())
        # Remove the fault, enter the real startup recovery surface, not a direct
        # overlay rollback surrogate, and recover the exact original files.
        self.assertTrue(updater.recover_startup(self.old))
        self.assertFalse(updater.active_update(self.old).exists())
        self.assertFalse(updater.recover_startup(self.old))
        for name in ('a', 'b'):
            self.assertEqual((self.old / name).read_bytes(), b'old-' + name.encode())
        self.assertEqual((self.recovery / 'update-files.json').read_bytes(), journal)

    def test_confirmed_overlay_restoration_removes_marker_and_reopens_previous(self):
        with patch.object(updater, 'validate_prepared', return_value=self.prepared), self.fault(False), \
             patch.object(updater.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(OSError, 'injected publish'):
                updater.perform(self.args)
            launch.assert_called_once()
        self.assertFalse(updater.active_update(self.old).exists())
        self.assertEqual((self.old / 'a').read_bytes(), b'old-a')
        self.assertEqual((self.old / 'b').read_bytes(), b'old-b')
        self.assertTrue(update_progress.status(self.old)['previous_reopen_requested'])

    def test_failure_after_returned_overlay_retains_marker_if_rollback_fails(self):
        put(self.new, 'components/xtts/xtts-bundle.json', b'{}')
        # Rebuild only this tiny fixture's preimages after adding optional data.
        import shutil
        shutil.rmtree(self.recovery)
        overlay.prepare(self.new, self.old, self.recovery)
        from localharness import xtts_install
        with patch.object(updater, 'validate_prepared', return_value=self.prepared), \
             patch.object(xtts_install, 'install', side_effect=OSError('optional install failed')), \
             patch.object(updater, 'rollback', side_effect=OSError('rollback failed')):
            self.note['data_paths'] = ['synthetic-tree']
            self.prepared['note'] = self.note
            write(updater.prepared_install_path(self.new), self.prepared)
            with self.assertRaisesRegex(OSError, 'rollback failed'):
                updater.install(self.old, self.new)
        self.assertTrue(updater.active_update(self.old).exists())
        self.assertTrue(updater.recover_startup(self.old))
        self.assertEqual((self.old / 'a').read_bytes(), b'old-a')
        self.assertFalse((self.old / 'components/xtts/xtts-bundle.json').exists())

    def test_missing_recovery_journal_stays_unconfirmed(self):
        write(updater.active_update(self.old), {'target': str(self.old), 'recovery': str(self.recovery)})
        (self.recovery / 'update-files.json').unlink()
        with self.assertRaisesRegex(RuntimeError, 'journal is missing'):
            updater.recover_startup(self.old)
        self.assertTrue(updater.active_update(self.old).exists())


class HistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tree = self.root / 'tree'
        pipeline._OBSERVERS.pop(str(self.tree.resolve()), None)
        self.addCleanup(lambda: pipeline._OBSERVERS.pop(str(self.tree.resolve()), None))
        self.units = self.root / 'units'
        self.forge = SimpleNamespace(_unit_dir=lambda task, kind, unit: self.units / task / unit,
            _unit_state=Mock(return_value={'status': 'done'}), agent=Mock(return_value='unknown: injected Stop refusal'))
        self.service = SimpleNamespace(status=Mock(return_value={'execution': 'done'}),
                                       cancel=Mock(return_value={'execution': 'cancelled'}))

    def request(self, index, state='needs_attention', **extra):
        identity = f'{index:064x}'
        directory = self.root / 'program/.source-updates' / identity
        request = {'id': identity, 'directory': str(directory), 'state': state}
        operation = {**request, 'created': time.time(), 'tree': str(self.tree), **extra}
        write(directory / 'pipeline.json', operation)
        write(pipeline._requests(self.tree) / (identity + '.json'), request)
        write(pipeline.path(self.tree), request)
        return request, operation

    def tick(self, **kwargs):
        return pipeline.tick(self.tree, self.forge, self.service, **kwargs)

    def test_thousand_retained_states_warm_ticks_have_no_reads_writes_or_stop_calls(self):
        for index in range(1000):
            state = ('needs_attention', 'ready_to_apply', 'cancelled')[index % 3]
            self.request(index, state, **({'cancel_requested': True, 'stop_confirmed': True} if state == 'cancelled' else {}))
        bootstrap = []
        for _ in range(1002):
            start = time.perf_counter()
            self.tick()
            bootstrap.append(time.perf_counter() - start)
            cache = pipeline._OBSERVERS[str(self.tree.resolve())]
            if not cache['pending'] and not cache['watch']:
                break
        else:
            self.fail('historical discovery did not settle')
        self.assertGreater(len(bootstrap), 1)  # cold reconciliation is deliberately batched
        before = {p: p.stat().st_mtime_ns for p in self.root.rglob('*.json')}
        callback = Mock()
        latencies = []
        with patch.object(pipeline, 'read', wraps=pipeline.read) as reads, \
             patch.object(pipeline, 'write', wraps=pipeline.write) as writes, \
             patch.object(Path, 'glob', side_effect=AssertionError('settled history enumerated')):
            for _ in range(50):
                start = time.perf_counter()
                self.assertEqual(self.tick(), [])
                callback()
                latencies.append(time.perf_counter() - start)
            self.assertEqual(reads.call_count, 0)
            self.assertEqual(writes.call_count, 0)
        callback.assert_called()
        self.assertEqual(callback.call_count, 50)
        self.service.cancel.assert_not_called()
        self.forge.agent.assert_not_called()
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in self.root.rglob('*.json')})
        print('HISTORY_1000_WARM ' + json.dumps({'ticks': 50, 'json_reads': 0, 'writes': 0,
              'bootstrap_ticks': len(bootstrap), 'bootstrap_callback_max_seconds': max(bootstrap),
              'control_callback_max_seconds': max(latencies), 'control_callback_mean_seconds': sum(latencies) / len(latencies)}))

    def test_new_atomic_request_is_discovered_without_replaying_history(self):
        self.request(1)
        self.tick()
        request, _ = self.request(2, 'queued')
        def advance(operation, *args):
            operation['state'] = 'needs_attention'
        with patch.object(pipeline, '_advance', side_effect=advance) as advance:
            self.tick()
            self.assertEqual(advance.call_count, 1)
            self.assertEqual(advance.call_args.args[0]['id'], request['id'])
        self.tick()
        with patch.object(pipeline, 'read', wraps=pipeline.read) as reads:
            self.tick()
            self.assertEqual(reads.call_count, 0)

    def test_ready_current_receives_pause_resume_and_stop_but_history_does_not(self):
        past, _ = self.request(1, 'ready_to_apply')
        current, _ = self.request(2, 'ready_to_apply')
        self.tick()
        self.tick(paused=True)
        self.assertTrue(read(Path(current['directory']) / 'pipeline.json')['paused'])
        self.assertNotIn('paused', read(Path(past['directory']) / 'pipeline.json'))
        self.tick(paused=False)
        self.assertFalse(read(Path(current['directory']) / 'pipeline.json')['paused'])
        self.tick(stopped=True)
        self.assertTrue(read(Path(current['directory']) / 'pipeline.json')['stop_confirmed'])
        self.assertEqual(read(Path(past['directory']) / 'pipeline.json')['state'], 'ready_to_apply')
        self.tick(stopped=True)
        with patch.object(pipeline, 'write', wraps=pipeline.write) as writes:
            self.tick(stopped=True)
            self.assertEqual(writes.call_count, 0)

    def test_confirmed_cancel_retires_but_unknown_stop_retries_until_settled(self):
        request, operation = self.request(1, 'cancelled', cancel_requested=True,
                                        task_id='task', unit_id='unit', admission='spawned', review_id='review')
        unit = self.forge._unit_dir('task', 'agents', 'unit')
        unit.mkdir(parents=True)
        self.service.cancel.return_value = {'execution': 'unknown'}
        self.tick()
        self.tick()
        self.assertEqual(self.service.cancel.call_count, 2)
        self.assertEqual(self.forge.agent.call_count, 2)
        write(unit / 'stop.json', {'state': 'settled'})
        self.service.cancel.return_value = {'execution': 'cancelled'}
        self.tick()
        calls = self.service.cancel.call_count, self.forge.agent.call_count
        self.assertTrue(read(Path(request['directory']) / 'pipeline.json')['stop_confirmed'])
        self.tick()
        self.tick()
        self.assertEqual(calls, (self.service.cancel.call_count, self.forge.agent.call_count))

    def test_attention_retains_named_live_unknown_producer_without_rewriting_summary(self):
        request, _ = self.request(1, task_id='task', unit_id='unit', admission='spawned')
        unit = self.forge._unit_dir('task', 'agents', 'unit')
        unit.mkdir(parents=True)
        self.forge._unit_state.return_value = {'status': 'unknown'}
        self.tick()
        before = (Path(request['directory']) / 'pipeline.json').stat().st_mtime_ns
        self.forge._unit_state.reset_mock()
        with patch.object(pipeline, 'write', wraps=pipeline.write) as writes:
            self.tick()
            self.tick()
            self.assertEqual(writes.call_count, 0)
            self.assertGreaterEqual(self.forge._unit_state.call_count, 2)
        self.assertEqual((Path(request['directory']) / 'pipeline.json').stat().st_mtime_ns, before)
        self.forge._unit_state.return_value = {'status': 'done'}
        self.tick()
        self.forge._unit_state.reset_mock()
        self.tick()
        self.forge._unit_state.assert_not_called()

    def test_restart_rebuilds_active_observation_from_retained_records(self):
        self.request(1, 'merging')
        with patch.object(pipeline, '_advance') as advance:
            self.tick()
            pipeline._OBSERVERS.clear()
            self.tick()
            self.assertEqual(advance.call_count, 2)

    def test_cold_backlog_does_not_defer_current_stop(self):
        for index in range(50):
            self.request(index)
        current, _ = self.request(50, 'queued')
        with patch.object(pipeline, '_advance') as advance:
            self.tick(stopped=True)
            advance.assert_not_called()
        self.assertTrue(pipeline._OBSERVERS[str(self.tree.resolve())]['pending'])
        operation = read(Path(current['directory']) / 'pipeline.json')
        self.assertEqual(operation['state'], 'cancelled')
        self.assertTrue(operation['stop_confirmed'])

    def test_failed_named_producer_observation_remains_visible_and_retried(self):
        request, _ = self.request(1, review_id='review')
        self.service.status.side_effect = ValueError('producer record unreadable')
        self.tick()
        self.assertIn('unreadable', read(Path(request['directory']) / 'pipeline.json')['producer_error'])
        self.service.status.reset_mock()
        self.tick()
        self.service.status.assert_called()
        self.service.status.side_effect = None
        self.tick()
        self.service.status.reset_mock()
        self.tick()
        self.service.status.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
