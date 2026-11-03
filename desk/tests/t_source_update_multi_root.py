"""Real host orchestration/composition with controlled producers, no model or install."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import t_update_contract_230 as composition_fixtures
from localharness import source_update_host as host, source_update as pipeline
from localharness import source_merge as merge, update_composition as composition
from localharness.package_notice import read, write
from localharness.agent_source_update import AgentResolutionPending


class MultiRootTests(unittest.TestCase):
    def setUp(self):
        self.f = composition_fixtures.CompositionTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.peer_tree, self.peer, self.rows = self.f.peer()
        for row in self.rows: row['state'] = 'needs_resolution'
        self.started = []; self.processes = []; self.subjects = {}
        self.cancelled = threading.Event(); self.mode = 'success'; self.polls = 0
        for target, name, kwargs in (
            (host, 'verified_runtime', {'return_value': self.f.operation['coordinator']}),
            (__import__('localharness.update_process', fromlist=['interpreter_for']), 'interpreter_for', {'return_value': Path(sys.executable)}),
            (pipeline, '_owner_blocked', {'return_value': False}),
            (pipeline.self_review, 'Service', {'return_value': Mock(status=lambda identity: {'id': identity})}),
            (pipeline, '_clear', {'side_effect': lambda service, record, subject: self.subjects[record['id']]}),
            (__import__('localharness.source_update_battery', fromlist=['validate']), 'validate', {'return_value': None}),
            (host, '_producer', {'side_effect': self.producer}),
            (host.time, 'sleep', {'return_value': None}),
        ):
            context = patch.object(target, name, **kwargs); context.start(); self.addCleanup(context.stop)

    def ready(self, directory):
        operation = read(directory/'pipeline.json')
        operation.update(state='ready_to_apply', candidate_sha256=merge.candidate_signature(directory),
            composition_sha256=composition.signature(directory), review_id=directory.name,
            subject_sha256='controlled-subject')
        self.subjects[directory.name] = {'merge_identity': {
            'plan_id': operation['id'], 'input_sha256': operation['input_sha256'],
            'candidate_sha256': operation['candidate_sha256'], 'composition_sha256': operation['composition_sha256']}}
        write(directory/'pipeline.json', operation)

    def producer(self, command, new, directory, cancel, log):
        if self.mode == 'spawn_failure' and self.started:
            raise OSError('controlled second admission failure')
        self.started.append(directory)
        # Each selected agent explicitly repairs its own editable M, never a
        # host-side union/copy of the first candidate over its peer.
        composition.repair(directory, 'app/localharness/feature.py', 'answer=42\n',
            expected=composition.signature(directory), reason='Controlled agent resolution')
        test = self; mode = self.mode
        class Producer:
            returncode = None
            def poll(self):
                test.polls += 1
                if mode == 'cancel' and len(test.started) == 2: test.cancelled.set()
                if cancel.exists():
                    self.returncode = 1; return 1
                if mode in ('cancel', 'spawn_failure'): return None
                if mode == 'failure' and directory == test.started[0]:
                    self.returncode = 1; return 1
                if mode == 'failure': return None
                if mode == 'manual':
                    operation = read(directory/'pipeline.json'); operation['state'] = 'waiting_agent'
                    write(directory/'pipeline.json', operation); self.returncode = 75; return 75
                if len(test.started) < len(test.rows):
                    if test.polls > 3: raise AssertionError('serial launch starves peer resolution')
                    return None
                composition.require_shared(new, test.rows)
                test.ready(directory)
                if mode == 'late_edit' and directory == test.started[-1]:
                    # The earlier root has already supplied its terminal result.
                    composition.repair(test.started[0], 'app/resources/example.txt', 'later edit\n',
                        expected=composition.signature(test.started[0]), reason='Late peer change')
                self.returncode = 0; return 0
        process = Producer(); self.processes.append((process, cancel))
        return process, cancel, log

    def run_host(self):
        return host.review(self.f.old, self.f.program, self.rows, cancelled=self.cancelled)

    def test_all_resolution_channels_start_before_first_shared_barrier(self):
        self.run_host()
        self.assertEqual(self.started, [self.f.directory, self.peer])
        self.assertTrue(composition.require_shared(self.f.program, self.rows)['consistent'])
        for row in self.rows: pipeline.validate_ready(row['directory'])

    def test_stop_signals_and_observes_every_admitted_peer(self):
        self.mode = 'cancel'
        with self.assertRaisesRegex(RuntimeError, 'cancel|stopped|not completed|не завершена'):
            self.run_host()
        self.assertEqual(len(self.started), 2)
        self.assertTrue(all(cancel.exists() and process.returncode is not None for process, cancel in self.processes))
        self.assertFalse((self.f.program.parent/'compound-overlay.json').exists())

    def test_failed_peer_settles_remaining_coordinators_before_return(self):
        self.mode = 'failure'
        with self.assertRaises(RuntimeError): self.run_host()
        self.assertEqual(len(self.started), 2)
        self.assertTrue(all(process.returncode is not None for process, _ in self.processes))

    def test_failed_later_admission_keeps_and_settles_earlier_producer(self):
        self.mode = 'spawn_failure'
        with self.assertRaisesRegex(OSError, 'second admission'): self.run_host()
        self.assertTrue(all(cancel.exists() and process.returncode is not None for process, cancel in self.processes))

    def test_manual_pending_does_not_skip_peer_admission(self):
        self.mode = 'manual'
        with self.assertRaises(AgentResolutionPending): self.run_host()
        self.assertEqual(len(self.started), 2)
        self.assertEqual(self.processes[0][0].returncode, 75)
        self.assertTrue(all(process.returncode is not None for process, _ in self.processes))

    def test_script_entry_ready_barrier_does_not_use_package_relative_imports(self):
        import runpy
        entry = runpy.run_path(str(Path(host.__file__)), run_name='coordinator_script_fixture')
        self.ready(self.f.directory); self.ready(self.peer)
        with patch.object(composition, '_runtime_inventory', side_effect=AssertionError('Waiting must not hash runtime')):
            self.assertTrue(entry['_shared_batch_ready'](self.f.directory, read(self.f.directory/'pipeline.json')))

    def test_mixed_manual_pending_and_waiting_ready_peer_settle(self):
        original = self.producer
        def mixed(*args):
            self.mode = 'manual' if not self.started else 'failure'
            return original(*args)
        # First producer returns75; second never reaches its shared barrier and
        # must be signalled rather than waited on forever.
        with patch.object(host, '_producer', side_effect=mixed):
            self.mode = 'manual'
            with self.assertRaises(AgentResolutionPending): self.run_host()
        self.assertEqual(len(self.started), 2)
        self.assertTrue(all(process.returncode is not None for process, _ in self.processes))

    def test_ready_hand_survives_peer_work_and_reopens_for_late_resolution(self):
        self.coordinate_fixture()

    def test_phase_published_during_transport_decision_cannot_admit_critic(self):
        self.coordinate_fixture(race=True)

    def test_preflight_ready_gated_peer_is_rejoined_before_wait(self):
        self.rows[0]['state'] = 'ready'
        self.ready(self.f.directory)
        self.run_host()
        self.assertEqual(self.started, [self.f.directory, self.peer])

    def coordinate_fixture(self, race=False):
        from contextlib import contextmanager, nullcontext
        from types import SimpleNamespace
        from localharness import app_migration, boot, owner_stop, session_exit
        from localharness import source_update_hand, source_update_transport, agent_source_update
        import python_migrate
        active = []; steps = []; transport_active = []
        binding = self.f.operation['coordinator']
        agent = {'tree': str(self.f.tree), 'code': str(self.f.local), 'value': {'mode': 'local'}}
        self.ready(self.f.directory)
        @contextmanager
        def serving(*args):
            active.append(True)
            try: yield
            finally: active.pop()
        def ensure(*args, **kwargs):
            operation = read(self.f.directory/'pipeline.json')
            operation['continuation'] = pipeline.AUTOMATIC
            operation['coordinator'] = kwargs['coordinator']
            write(self.f.directory/'pipeline.json', operation)
            return operation
        @contextmanager
        def transport_context(*args):
            transport_active.append(True)
            try: yield None
            finally: transport_active.pop()
        mutex = pipeline._LOCK
        class CrossingLock:
            crossed = False
            def __enter__(inner):
                mutex.acquire()
                if not inner.crossed:
                    inner.crossed = True
                    current = read(self.f.directory/'pipeline.json'); current['state'] = 'review_requested'
                    write(self.f.directory/'pipeline.json', current)
                return inner
            def __exit__(inner, *args): mutex.release()
        def tick(*args, **kwargs):
            self.assertTrue(active, 'Ready fast path must not bypass the hand')
            steps.append(read(self.f.directory/'pipeline.json')['state'])
            if race:
                self.assertTrue(transport_active, 'Critic cannot be admitted before relay turn')
                self.ready(self.f.directory); self.ready(self.peer)
                return
            if len(steps) == 1:
                self.assertEqual(steps[-1], 'ready_to_apply')
                return  # peer remains waiting; own current review stays intact
            if len(steps) == 2:
                agent_source_update.command(self.peer_tree, 'patch', path='app/localharness/feature.py',
                    before='answer=1', after='answer=42', expected_composition_sha256=composition.signature(self.peer),
                    reason='Peer explicitly changes common application')
                return
            self.assertEqual(steps[-1], 'waiting_agent')
            current = read(self.f.directory/'pipeline.json')
            self.assertNotIn('review_id', current)
            self.assertEqual(current['evidence_history'][-1]['review_id'], self.f.directory.name)
            agent_source_update.command(self.f.tree, 'patch', path='app/localharness/feature.py',
                before='answer=1', after='answer=42', expected_composition_sha256=composition.signature(self.f.directory),
                reason='Own selected agent reconciles peer, not copied by host')
            self.ready(self.f.directory); self.ready(self.peer)
        fake_forge = SimpleNamespace(__file__=str(self.f.program/'tree/forge.py'), SOURCE_UPDATE_API=1, SELF_REVIEW_API=1)
        fake_llm = SimpleNamespace(_config=lambda: {})
        saved_path = list(sys.path); self.addCleanup(lambda: sys.path.__setitem__(slice(None), saved_path))
        with patch.dict(sys.modules, forge=fake_forge, llm=fake_llm), patch.dict(host.os.environ), \
             patch.object(app_migration, 'discover', return_value={'agents': [agent]}), \
             patch.object(boot, 'apply_env'), patch.object(boot, 'env_knobs', return_value={}), \
             patch.object(owner_stop, 'configure'), patch.object(owner_stop, 'stopped', return_value=False), \
             patch.object(owner_stop, 'paused', return_value=False), \
             patch.object(python_migrate, 'installation_lock', return_value=nullcontext()), \
             patch.object(pipeline, 'ensure', side_effect=ensure), patch.object(pipeline, 'tick', side_effect=tick), \
             patch.object(source_update_hand, 'serve', side_effect=serving), \
             patch.object(source_update_transport, 'review_transport', side_effect=transport_context), \
             patch.object(pipeline, '_LOCK', CrossingLock() if race else mutex):
            host.coordinate(self.f.old, self.f.program, self.f.directory, self.f.root/'unused-cancel')
        self.assertEqual(steps, ['review_requested'] if race else ['ready_to_apply', 'ready_to_apply', 'waiting_agent'])
        self.assertEqual(active, [])
        pipeline.validate_ready(self.f.directory)

    def test_late_app_repair_promotes_real_preflight_pristine_peer(self):
        import shutil
        from localharness import code_transition, app_migration, update_source, app_source_update, agent_source_update
        f = self.f
        # Fresh real preflight: only the authored root receives a pipeline.
        shutil.copytree(f.root/'A', f.old/'tree')
        agents = [{'tree': str(f.tree), 'code': str(f.local), 'value': {'mode': 'local'}},
                  {'tree': str(self.peer_tree), 'code': str(f.root/'pristine-code'), 'value': {'mode': 'local'}}]
        shutil.copytree(f.root/'A', f.root/'pristine-code')
        (f.program/'code-transition-plan.json').unlink()
        choice = {'mode': 'preserve', 'new': 'controlled-update', 'resolution_runtime': str(f.old)}
        with patch.object(app_migration, 'discover', return_value={'agents': agents}), \
             patch.object(update_source, 'selected', return_value=choice), \
             patch.object(app_source_update, 'changed', return_value=None), \
             patch.object(agent_source_update, 'require_patch_tool'):
            self.rows = code_transition.preflight(f.old, f.program)['plans']
            f.directory, self.peer = (Path(row['directory']) for row in self.rows)
            self.assertEqual(self.rows[1]['state'], 'ready')
            self.assertFalse((self.peer/'pipeline.json').exists())
            self.run_host()
        self.assertEqual(self.started, [f.directory, self.peer])
        self.assertEqual(read(self.peer/'pipeline.json')['resolution_owner'], 'agent')
        composition.require_shared(f.program, self.rows)
        for row in self.rows: pipeline.validate_ready(row['directory'])

    def test_waiting_reuses_only_unchanged_composition_revisions(self):
        observation = {}; self.ready(self.f.directory)
        with patch.object(composition, 'app_inventory', wraps=composition.app_inventory) as scanned:
            self.assertFalse(host._shared_batch_ready(self.f.directory, read(self.f.directory/'pipeline.json'), observation))
            calls = scanned.call_count; self.assertGreater(calls, 0)
            self.assertFalse(host._shared_batch_ready(self.f.directory, read(self.f.directory/'pipeline.json'), observation))
            self.assertEqual(scanned.call_count, calls)
        composition.repair(self.peer, 'app/localharness/feature.py', 'answer=42\n',
            expected=composition.signature(self.peer), reason='Explicit later peer revision')
        self.assertFalse(host._shared_batch_ready(self.f.directory, read(self.f.directory/'pipeline.json'), observation))
        self.assertEqual(read(self.f.directory/'pipeline.json')['state'], 'waiting_agent')
        self.assertNotIn('review_id', read(self.f.directory/'pipeline.json'))

    def test_already_cancelled_does_not_start_any_coordinator(self):
        self.cancelled.set()
        with self.assertRaisesRegex(RuntimeError, 'cancel'): self.run_host()
        self.assertEqual(self.started, [])

    def test_late_peer_edit_cannot_reuse_first_review(self):
        self.mode = 'late_edit'
        with self.assertRaisesRegex(ValueError, 'shared application|composition changed'):
            self.run_host()
        self.assertEqual(len(self.started), 2)
        self.assertFalse((self.f.program.parent/'compound-overlay.json').exists())


if __name__ == '__main__': unittest.main()
