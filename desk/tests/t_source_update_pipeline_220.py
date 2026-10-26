"""Actual Git/Forge admission/adapters and frozen reviews; no provider or process."""
from __future__ import annotations
import ast
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'desk'))
sys.path.insert(0, str(ROOT / 'helene/core'))
sys.path.insert(0, str(Path(__file__).parent))
from localharness import source_update as pipeline, source_merge as merge, code_transition
from localharness import self_review, source_update_worker
from localharness.package_notice import read, write
from t_self_review_192 import load_forge
from t_source_merge_211 import BASE, LOCAL, BOTH, put


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.old, self.new, self.code, self.tree = [self.root / name for name in ('old', 'new', 'code', 'home')]
        put(self.old / 'tree', 'module.py', BASE)
        put(self.new / 'tree', 'module.py', BASE.replace('return 1', 'return 99'))
        put(self.code, 'module.py', LOCAL)
        self.tree.mkdir()
        write(self.old / 'helene.json', {'tree': str(self.tree), 'code': str(self.code), 'runner': ''})
        write(self.old / 'python-app.json', {'version': '2.1.1'})
        write(self.new / 'python-app.json', {'version': '2.2.0'})
        self.forge = load_forge()
        self.forge.TASKS_DIR = self.tree / 'memory/.forge/tasks'
        self.forge.BASE = self.tree
        self.forge._event = mock.Mock()
        self.forge.emit_unit_event = mock.Mock()
        self.forge._proc_started_at = lambda pid: 'test-birth'
        self.forge._pid_alive = lambda *args: True
        self.spawns = []
        def spawn(script, request, log):
            self.spawns.append((script, request, read(request)))
            return 99999999
        self.forge._spawn_runner = mock.Mock(side_effect=spawn)
        self.service = self_review.Service(self.tree, self.code, self.old, self.forge, {'after_update': False})
        write(self.tree / 'memory/.self-review/runtime.json', {'source_update_supported': True,
              'app_root': str(self.old.resolve()), 'at': __import__('time').time()})
        self.preflight()
        self.request = read(pipeline.path(self.tree))
        self.directory = Path(self.request['directory'])

    def preflight(self):
        return code_transition.preflight(self.old, self.new)

    def operation(self):
        return read(self.directory / 'pipeline.json')

    def tick(self, **kwargs):
        pipeline.tick(self.tree, self.forge, self.service, **kwargs)
        return self.operation()

    def merge_result(self, content=BOTH, *, complete=True, forged=None):
        operation = self.operation()
        unit = self.forge._unit_dir(operation['task_id'], 'agents', operation['unit_id'])
        request = read(unit / 'request.json')
        adapter = source_update_worker.SourceMergeAdapter(request['source_update'])
        adapter.prepare(types.SimpleNamespace(snapshot=lambda: {'voice': {'framework': 'fake', 'model': 'fake'}}), request)
        args = {'expected_input_sha256': operation['input_sha256']}
        for name in merge.validate_inputs(self.directory)['conflicts']:
            adapter.dispatch(request, 'merge_source', {**args, 'action': 'resolve', 'path': name, 'content': content})
        adapter.dispatch(request, 'merge_source', {**args, 'action': 'finish'})
        result = {'status': 'done', 'complete': complete, 'result': 'candidate finished', 'model': 'fake',
                  **adapter.result_stamp(), **(forged or {})}
        write(unit / 'result.json', result)
        return request

    def review_result(self, verdict='clear', *, complete=True, raw=None, wrong_sha=False):
        self.service.tick()
        record = self.service.status(self.operation()['review_id'])
        for critic in record['critics']:
            unit = self.forge._unit_dir(record['task_id'], 'agents', critic['unit_id'])
            finding = [{'path': 'resolved/module.py', 'scenario': 'overlapping edits', 'detail': 'preserve the second function'}]
            result = {'subject_sha256': record['subject_sha256'], 'verdict': verdict,
                      'summary': 'source-only fake reviewer', 'findings': finding if verdict == 'findings' else [], 'gaps': []}
            write(unit / 'result.json', {'status': 'done', 'complete': complete, 'snapshot_verified': True,
                  'subject_sha256': '0' * 64 if wrong_sha else record['subject_sha256'],
                  'result': json.dumps(result) if raw is None else raw, 'model': 'fake-review'})
        self.service.tick()
        return self.service.status(record['id'])

    def ready(self):
        self.tick()
        self.merge_result()
        self.tick()
        self.review_result()
        self.assertEqual(self.tick()['state'], 'ready_to_apply')
        return self.operation()

    def test_full_pipeline_findings_correction_new_candidate_and_apply(self):
        original_config = (self.old / 'helene.json').read_bytes()
        self.tick()
        self.merge_result(LOCAL)
        first = self.tick()
        first_subject = first['subject_sha256']
        first_task = first['task_id']
        self.review_result('findings')
        correction = self.tick()
        self.assertEqual(correction['state'], 'merging')
        self.assertNotEqual(first_task, correction['task_id'])
        self.tick()
        self.merge_result(BOTH)
        second = self.tick()
        self.assertNotEqual(first_subject, second['subject_sha256'])
        self.assertIn('source-update:' + self.request['id'] + ':' + second['candidate_sha256'],
                      self.service.status(second['review_id'])['operations'])
        self.review_result('clear')
        self.assertEqual(self.tick()['state'], 'ready_to_apply')
        self.assertEqual(self.preflight()['state'], 'ready')
        target = self.root / 'independent-copy'
        shutil.copytree(self.code, target)
        merge.apply(self.directory, target)
        self.assertEqual((target / 'module.py').read_text(), BOTH)
        self.assertEqual((self.code / 'module.py').read_text(), LOCAL)
        self.assertEqual((self.old / 'helene.json').read_bytes(), original_config)
        self.assertEqual(len(self.operation()['attempts']), 2)

    def test_no_ordinary_agent_turn_and_no_manual_bypass(self):
        callback = mock.Mock(side_effect=AssertionError('must not wake ordinary Jarvis'))
        pipeline.notify_due(self.tree, callback)
        callback.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'restricted'):
            pipeline.command(self.tree, 'resolve', path='module.py', content=BOTH)
        merge.resolve(self.directory, 'module.py', BOTH)
        merge.finish(self.directory)
        target = self.root / 'target'
        shutil.copytree(self.code, target)
        with self.assertRaises(ValueError):
            merge.apply(self.directory, target)
        self.assertEqual((target / 'module.py').read_text(), LOCAL)

    def test_old_forge_is_explicit_attention_no_generic_worker(self):
        self.forge.SOURCE_UPDATE_API = 0
        self.assertEqual(self.tick()['state'], 'needs_attention')
        self.assertIn('no generic', self.operation()['error'])
        self.assertEqual(self.spawns, [])

    def test_old_runner_without_coordinator_is_attention_not_legacy_pending(self):
        (self.tree / 'memory/.self-review/runtime.json').unlink()
        put(self.code, 'module.py', LOCAL + '\nnext = True\n')
        result = self.preflight()
        request = read(pipeline.path(self.tree))
        operation = read(Path(request['directory']) / 'pipeline.json')
        self.assertEqual(result['state'], 'needs_resolution')
        self.assertEqual(operation['state'], 'needs_attention')
        self.assertIn('Running application', operation['error'])
        self.assertIn('требует внимания', result['message'])
        self.assertIn('coordinator API v1', result['plans'][0]['error'])
        self.assertNotEqual(request['state'], 'pending')  # old runner's only activation state
        self.tick()
        self.assertEqual(self.spawns, [])

    def test_superseded_marker_survives_stale_summary_publication(self):
        self.tick()
        stale_operation = self.operation()
        old_request = dict(self.request)
        put(self.code, 'module.py', LOCAL + '\nnext = True\n')
        self.preflight()
        new_request = read(pipeline.path(self.tree))
        self.assertNotEqual(old_request['id'], new_request['id'])
        self.assertTrue((self.directory / 'cancel.json').exists())
        pipeline._persist(self.directory, stale_operation)
        pipeline._save_request(self.tree, old_request)
        self.assertEqual(self.operation()['state'], 'cancelled')
        self.assertEqual(read(pipeline.path(self.tree))['id'], new_request['id'])
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            pipeline._fresh(stale_operation, self.directory)

    def test_actual_durable_pause_and_stop_checked_without_a_maintenance_tick(self):
        from localharness import owner_stop
        self.ready()
        pause = self.tree / 'memory/.control/autonomy-paused.json'
        write(pause, {'reason': 'synthetic owner Pause'})
        with self.assertRaisesRegex(ValueError, 'Stop/Pause'):
            pipeline.validate_ready(self.directory)
        pause.unlink()
        with mock.patch.object(owner_stop, 'seed_file', return_value=self.root / 'stop.json'), \
             mock.patch.object(owner_stop, 'client_file', return_value=self.root / 'client-stop.json'):
            write(self.root / 'stop.json', {'reason': 'synthetic owner Stop'})
            with self.assertRaisesRegex(ValueError, 'Stop/Pause'):
                pipeline.validate_ready(self.directory)

    def test_paused_no_admission_stop_late_result_never_restarts(self):
        self.tick(paused=True)
        self.assertEqual(self.spawns, [])
        self.tick()
        self.merge_result()
        with mock.patch.object(self.forge, '_kill_tree', return_value='unknown: fixture denial'):
            stopped = self.tick(stopped=True)
        self.assertEqual(stopped['state'], 'cancelled')
        count = len(self.spawns)
        self.tick()
        self.assertEqual(len(self.spawns), count)
        with self.assertRaises(ValueError):
            pipeline.validate_ready(self.directory)
        self.assertEqual(self.preflight()['state'], 'needs_resolution')

    def test_reviewer_failed_incomplete_malformed_wrong_sha_never_ready(self):
        self.tick()
        self.merge_result()
        self.tick()
        self.review_result(raw='not json')
        self.assertEqual(self.tick()['state'], 'needs_attention')
        self.assertEqual(self.preflight()['state'], 'needs_resolution')

    def test_incomplete_merge_never_enters_review(self):
        self.tick()
        self.merge_result(complete=False)
        state = self.tick()
        self.assertEqual(state['state'], 'needs_attention')
        self.assertNotIn('review_id', state)

    def test_incomplete_review_is_not_clear(self):
        self.tick()
        self.merge_result()
        self.tick()
        self.review_result(complete=False)
        self.assertEqual(self.tick()['state'], 'needs_attention')

    def test_wrong_subject_review_is_not_clear(self):
        self.tick()
        self.merge_result()
        self.tick()
        self.review_result(wrong_sha=True)
        self.assertEqual(self.tick()['state'], 'needs_attention')

    def test_review_cancelled_then_late_clear_does_not_revive(self):
        self.tick()
        self.merge_result()
        self.tick()
        self.service.tick()
        review_id = self.operation()['review_id']
        with mock.patch.object(self.forge, '_kill_tree', return_value='unknown: fixture denial'):
            self.service.cancel(review_id)
        self.review_result()
        self.assertEqual(self.tick()['state'], 'needs_attention')

    def test_pausing_ready_candidate_prevents_apply(self):
        self.ready()
        self.tick(paused=True)
        with self.assertRaises(ValueError):
            pipeline.validate_ready(self.directory)
        self.tick(paused=False)
        self.assertEqual(pipeline.validate_ready(self.directory)['state'], 'ready_to_apply')

    def test_attempt_budget_exhaustion_is_not_review_override(self):
        self.tick()
        for index in range(pipeline.MAX_ATTEMPTS):
            self.merge_result(BOTH + '\ncorrection = ' + str(index) + '\n')
            self.tick()
            self.review_result('findings')
            self.tick()
            if index < pipeline.MAX_ATTEMPTS - 1:
                self.tick()
        self.assertEqual(self.operation()['state'], 'needs_attention')
        self.assertIn('budget exhausted', self.operation()['error'])
        self.assertEqual(len(self.operation()['attempts']), pipeline.MAX_ATTEMPTS)

    def test_merger_can_never_modify_a_nonconflict_path(self):
        self.tick()
        operation = self.operation()
        unit = self.forge._unit_dir(operation['task_id'], 'agents', operation['unit_id'])
        request = read(unit / 'request.json')
        adapter = source_update_worker.SourceMergeAdapter(request['source_update'])
        with self.assertRaises(ValueError):
            adapter.dispatch(request, 'merge_source', {'action': 'resolve', 'path': 'unrelated.py',
                'content': 'owned = False\n', 'expected_input_sha256': operation['input_sha256']})
        self.assertFalse((self.directory / 'result/unrelated.py').exists())

    def test_full_inventory_explicit_absence_and_json_hidden_metadata(self):
        # An add/delete requires no synthetic empty file. Non-private JSON is a
        # real code input; full manifests include executable mode and absence.
        for side in ('base', 'local', 'incoming', 'result'):
            put(self.directory / side, 'options.json', '{"value":1}')
        plan = read(self.directory / 'plan.json')
        for side in ('base', 'local', 'incoming'):
            plan['files'][side] = merge.inventory(self.directory / side)
        plan['result'] = merge.inventory(self.directory / 'result')
        plan['result'].pop('module.py')
        (self.directory / 'result/module.py').unlink()
        sources = pipeline._materials(self.directory, plan)
        row = self.service.request(trigger='contested_overlay', sources=sources,
                                   overlay={'conflicts': ['module.py']}, operation_id='absence-fixture')
        subject = self_review.validate_snapshot(row['snapshot'], row['subject_sha256'])
        self.assertFalse(subject['gaps'])
        entry = next(entry for entry in subject['manifest'] if entry['path'] == 'resolved/module.py')
        self.assertIs(entry['exists'], False)
        self.assertIn('mode', next(entry for entry in subject['manifest'] if entry['path'] == 'mine/options.json'))
        from localharness.self_review_worker import SnapshotReviewer
        critic = SnapshotReviewer({'root': row['snapshot'], 'subject_sha256': row['subject_sha256']})
        self.assertIs(json.loads(critic.dispatch({}, 'inspect', {'action': 'read', 'path': 'resolved/module.py'}))['exists'], False)

    def test_binary_and_private_material_gaps_cannot_be_clear(self):
        for side in ('base', 'local', 'incoming', 'result'):
            put(self.directory / side, 'AGENTS.md', 'private authored instructions')
            (self.directory / side / 'asset.bin').write_bytes(b'\x00binary')
        plan = read(self.directory / 'plan.json')
        for side in ('base', 'local', 'incoming'):
            plan['files'][side] = merge.inventory(self.directory / side)
        plan['result'] = merge.inventory(self.directory / 'result')
        row = self.service.request(trigger='contested_overlay', sources=pipeline._materials(self.directory, plan),
                                   overlay={'conflicts': ['module.py']})
        subject = self_review.validate_snapshot(row['snapshot'], row['subject_sha256'])
        self.assertEqual(len(subject['gaps']), 8)
        self.assertFalse((Path(row['snapshot']) / 'mine/AGENTS.md').exists())
        self.assertFalse((Path(row['snapshot']) / 'mine/asset.bin').exists())

    def test_all_frozen_sides_and_live_local_and_candidate_are_freshness_authority(self):
        ready = self.ready()
        file = self.directory / 'incoming/module.py'
        original = file.read_bytes()
        file.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'Frozen'):
            pipeline.validate_ready(self.directory)
        file.write_bytes(original)
        candidate = self.directory / 'result/module.py'
        original = candidate.read_bytes()
        candidate.write_bytes(original + b'\n')
        with self.assertRaises(ValueError):
            pipeline.validate_ready(self.directory)
        candidate.write_bytes(original)
        put(self.code, 'module.py', LOCAL + '\nlate = True\n')
        with self.assertRaisesRegex(ValueError, 'Local code changed'):
            pipeline.validate_ready(self.directory)
        self.assertEqual(ready['state'], 'ready_to_apply')

    def test_final_updater_validate_refuses_changed_B_or_selection(self):
        self.ready()
        self.preflight()
        put(self.new / 'tree', 'module.py', 'late_upstream = True\n')
        with self.assertRaisesRegex(ValueError, 'Incoming upstream'):
            code_transition.validate(self.new)
        put(self.new / 'tree', 'module.py', BASE.replace('return 1', 'return 99'))
        write(self.old / 'helene.json', {'tree': str(self.tree), 'code': str(self.old / 'tree')})
        with self.assertRaisesRegex(ValueError, 'Selected code'):
            code_transition.validate(self.new)

    def test_changed_canonical_critic_result_refuses_apply(self):
        operation = self.ready()
        record = self.service.status(operation['review_id'])
        Path(record['critics'][0]['result_path']).write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'critic result changed'):
            pipeline.validate_ready(self.directory)

    def test_budget_and_no_progress_are_concrete_attention(self):
        self.tick()
        self.merge_result(LOCAL)
        self.tick()
        self.review_result('findings')
        self.tick()
        self.tick()
        self.merge_result(LOCAL)
        operation = self.tick()
        self.assertEqual(operation['state'], 'needs_attention')
        self.assertIn('did not change', operation['error'])

    def test_actual_restricted_worker_loop_fake_provider(self):
        self.tick()
        script, request_path, request = self.spawns[0]
        self.assertEqual(Path(script), Path(source_update_worker.__file__).resolve())
        run_context = types.ModuleType('run_context')
        run_context.bind_run = lambda value: contextlib.nullcontext()
        llm = types.ModuleType('llm')
        llm.BrokenChannelError = type('BrokenChannelError', (RuntimeError,), {})
        llm.limits = lambda: types.SimpleNamespace(max_tool_iters=10)
        llm.snapshot = lambda: {'voice': {'framework': 'fake-route', 'model': 'fake-model'}}
        parsed = ast.parse((ROOT / 'helene/core/llm.py').read_text(encoding='utf-8'))
        namespace = {'LLMResponse': object, 'copy': copy, 'json': json}
        helpers = [node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name in {'assistant_message', '_wire_value'}]
        exec(compile(ast.Module(body=helpers, type_ignores=[]), 'llm.py', 'exec'), namespace)
        llm.assistant_message = namespace['assistant_message']
        calls = []
        def chat(role, **kwargs):
            calls.append(kwargs)
            self.assertEqual([t['name'] for t in kwargs['tools']], ['merge_source'])
            self.assertFalse(kwargs['allow_fallback'])
            sha = self.operation()['input_sha256']
            if len(calls) == 1:
                return types.SimpleNamespace(model='fake-actual', stop_reason='tool_use', text='', blocks=[
                    {'type': 'tool_use', 'id': 'bad', 'name': 'run', 'input': {'cmd': 'forbidden'}},
                    {'type': 'tool_use', 'id': 'resolve', 'name': 'merge_source', 'input':
                        {'action': 'resolve', 'path': 'module.py', 'content': BOTH, 'expected_input_sha256': sha}},
                    {'type': 'tool_use', 'id': 'finish', 'name': 'merge_source', 'input':
                        {'action': 'finish', 'expected_input_sha256': sha}}])
            return types.SimpleNamespace(model='fake-actual', stop_reason='end_turn', blocks=[], text='Finished isolated candidate.')
        llm.chat = chat
        spec = importlib.util.spec_from_file_location('update_test_worker', ROOT / 'helene/core/forge_worker.py')
        worker = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {'forge': self.forge, 'llm': llm, 'run_context': run_context}):
            spec.loader.exec_module(worker)
            prior_cwd, prior_path = Path.cwd(), sys.path[:]
            try:
                with mock.patch.dict(sys.modules, {'forge_worker': worker}):
                    self.assertEqual(source_update_worker.run(request_path), 0)
            finally:
                os.chdir(prior_cwd)
                sys.path[:] = prior_path
        result = read(request_path.parent / 'result.json')
        self.assertTrue(result['candidate_verified'])
        self.assertEqual(result['status'], 'done')
        self.assertIn('Refused', str(calls[1]['messages']))
        self.assertEqual(self.tick()['state'], 'reviewing')
        self.assertEqual((self.code / 'module.py').read_text(), LOCAL)


if __name__ == '__main__':
    unittest.main(verbosity=2)
