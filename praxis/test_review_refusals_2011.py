"""20.11: two coding_review refusals killed every turn that touched the review.

``coding_review(action='status')`` without an id raised ``invalid review id`` and,
being classified as a side effect, turned the whole run ``in_doubt`` — in the
owner DM she went silent on every question about the update. A complete-source
``prepare`` refused the tree because credential-floor fixtures contain a bare
private-key header (no key body), so no review of her own source could start.
"""
import json
from types import SimpleNamespace
import unittest
from unittest import mock

import agent
import forge_review
import run_context
import test_forge_wave2_server as fixtures
from test_truth_agent import _RunHarness


class ReviewDispatcherLedgerTests(_RunHarness):
    def setUp(self):
        super().setUp()
        self.fixture = fixtures.ServerWave2()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def _dispatch(self, suffix, args):
        context = self._create(suffix)
        block = {'type': 'tool_use', 'id': 'review-call', 'name': 'coding_review', 'input': args}
        responses = [SimpleNamespace(stop_reason='tool_use', blocks=[block], text=''),
                     SimpleNamespace(stop_reason='end_turn', blocks=[], text='Continued.')]
        with run_context.bind_run(context), \
                mock.patch.object(agent, '_model_call', side_effect=responses) as model, \
                mock.patch.object(agent, '_notices_block', return_value=''), \
                mock.patch.object(agent, '_work_loop_continue', return_value=False):
            try:
                reply = agent._terminal_tool_loop(
                    system='test', messages=[{'role': 'user', 'content': 'Review.'}],
                    tools=[{'name': 'coding_review', 'input_schema': {'type': 'object'}}],
                )
            except agent.RunStopped as exc:
                self.assertEqual(exc.status, 'in_doubt')
                self.assertEqual(model.call_count, 1)
            else:
                self.assertEqual(reply, 'Continued.')
                self.assertEqual(model.call_count, 2)
                self.manager.transition(context.run_id, 'done', expected='running')
        rows = [r for r in self.manager.events(context.run_id) if r.get('call_id') == 'review-call']
        return context.run_id, {r['kind']: r for r in rows}

    def _assert_settled(self, run_id, rows, *, side_effect, refused=False):
        self.assertEqual(self.manager.manifest(run_id)['status'], 'done')
        self.assertEqual(self.manager.outstanding_tools(run_id), {})
        self.assertIs(rows['tool_started']['side_effect'], side_effect)
        self.assertNotIn('tool_uncertain_error', rows)
        result = rows['tool_result']
        if refused:
            self.assertEqual(result['metadata']['outcome'], 'refused')
        return agent.run_resume.read_full_result_bytes(self.manager, run_id, result['result']).decode()

    def test_default_status_and_bad_exact_status_close_as_reads(self):
        run_id, rows = self._dispatch('review-list', {'task_id': self.fixture.tid})
        self.assertEqual(json.loads(self._assert_settled(run_id, rows, side_effect=False))['reviews'], [])
        run_id, rows = self._dispatch('review-bad-status', {
            'task_id': self.fixture.tid, 'action': 'status', 'review_id': 'review-latest'})
        self.assertIn('invalid review id', self._assert_settled(run_id, rows, side_effect=False, refused=True))

    def test_live_review_timeout_keeps_mutation_outstanding(self):
        with mock.patch.dict(agent.TOOL_IMPL, {'coding_review': mock.Mock(
                return_value=agent.ToolCeilingExpired('fixture ceiling expired'))}):
            run_id, rows = self._dispatch('review-ceiling', {
                'task_id': self.fixture.tid, 'action': 'prepare'})
        self.assertEqual(self.manager.manifest(run_id)['status'], 'in_doubt')
        self.assertIn('review-call', self.manager.outstanding_tools(run_id))
        self.assertIn('tool_uncertain_error', rows)
        self.assertNotIn('tool_result', rows)

    def test_empty_battery_refuses_before_export_or_task_registration(self):
        import forge_battery
        rid = forge_review.prepare(self.fixture.tid, paths=['code.py'])['id']
        directory, _ = forge_review._record(self.fixture.tid, rid)
        with mock.patch.object(forge_battery.forge_intelligence, 'verification_plan',
                               return_value={'checks': []}), \
                mock.patch.object(forge_battery.forge, 'start') as start:
            run_id, rows = self._dispatch('empty-battery', {
                'task_id': self.fixture.tid, 'action': 'verification_plan', 'review_id': rid})
        start.assert_not_called()
        self.assertFalse((directory / 'verification').exists())
        self.assertIn('no authoritative verification checks found', self._assert_settled(
            run_id, rows, side_effect=True, refused=True))

    def test_mutation_preflight_refusals_close_without_reserving_a_review(self):
        for suffix, task_id, action, extra, message in (
            ('missing', 'code-00000000', 'prepare', {}, 'нет coding-задачи'),
            ('bad-id', self.fixture.tid, 'start', {'review_id': 'review-latest'}, 'invalid review id'),
            ('bad-battery-id', self.fixture.tid, 'verification_plan', {'review_id': 'review-latest'}, 'invalid review id'),
            ('unsupported', self.fixture.tid, 'publish', {}, 'unsupported review action'),
            ('private-path', self.fixture.tid, 'prepare', {'paths': ['memory/private']}, 'private review input excluded'),
        ):
            with self.subTest(suffix=suffix):
                run_id, rows = self._dispatch(suffix, {'task_id': task_id, 'action': action, **extra})
                self.assertIn(message, self._assert_settled(run_id, rows, side_effect=True, refused=True))
        self.assertFalse((forge_review.forge._task_dir(self.fixture.tid) / 'reviews').exists())
        self.assertFalse(forge_review.forge._task_dir('code-00000000').exists())

    def test_missing_task_never_creates_phantom_lock_parent(self):
        forge = forge_review.forge
        missing = 'code-00000000'
        for action in ('start_merge', 'verification_plan', 'bind_verification'):
            with self.subTest(action=action):
                run_id, rows = self._dispatch('missing-' + action, {
                    'task_id': missing, 'action': action,
                    'review_id': 'merge-' + '0' * 16, 'verification_id': 'verify-00000000'})
                self.assertIn('нет coding-задачи', self._assert_settled(
                    run_id, rows, side_effect=True, refused=True))
                self.assertFalse(forge._task_dir(missing).exists())

    def test_malformed_input_never_dispatches(self):
        for index, extra in enumerate(({'action': ['status']}, {'action': 'status', 'paths': [1]},
                                       {'action': 'prepare', 'unknown_field': None})):
            with self.subTest(extra=extra), mock.patch.object(forge_review, 'command') as invoke:
                run_id, rows = self._dispatch(f'malformed-{index}', {'task_id': self.fixture.tid, **extra})
                self.assertIn('НЕ выполнен', self._assert_settled(run_id, rows, side_effect=True, refused=True))
                invoke.assert_not_called()

    def test_live_review_rejects_reserved_execution_before_dispatch(self):
        for index, value in enumerate((None, 'not provenance')):
            invoke = mock.Mock()
            with self.subTest(value=value), mock.patch.dict(agent.TOOL_IMPL, {
                    'coding_review': invoke}):
                run_id, rows = self._dispatch(f'reserved-live-{index}', {
                    'task_id': self.fixture.tid, 'action': 'status', '_execution': value})
                self.assertIn('_execution', self._assert_settled(
                    run_id, rows, side_effect=True, refused=True))
                invoke.assert_not_called()

    def test_resumed_review_rejects_reserved_execution_before_dispatch(self):
        context = self._create('reserved-resume')
        self._model_output(context, text='', stop_reason='tool_use', blocks=[{
            'type': 'tool_use', 'id': 'pending', 'name': 'coding_review',
            'input': {'task_id': self.fixture.tid, 'action': 'status', '_execution': None},
        }])
        self._pause(context)
        invoke = mock.Mock()
        with mock.patch.dict(agent.TOOL_IMPL, {'coding_review': invoke}), \
                mock.patch.object(agent.social, 'owner_id', return_value='100'), \
                mock.patch.object(agent, 'guard_outbound_reply', side_effect=self._passthrough), \
                mock.patch.object(agent, '_terminal_tool_loop', return_value='Continued.') as continued:
            agent.resume_durable_run(context.run_id)
        continued.assert_called_once()
        invoke.assert_not_called()
        self.assertNotEqual(self.manager.manifest(context.run_id)['status'], 'in_doubt')
        self.assertNotIn('pending', self.manager.outstanding_tools(context.run_id))
        results = [r for r in self.manager.events(context.run_id)
                   if r.get('call_id') == 'pending' and r['kind'] == 'tool_result']
        self.assertEqual([r['metadata']['outcome'] for r in results], ['refused'])

    def test_legacy_outstanding_review_with_reserved_null_does_not_replay(self):
        context = self._create('legacy-reserved-review')
        args = {'task_id': self.fixture.tid, 'action': 'status'}
        self._model_output(context, text='', stop_reason='tool_use', blocks=[{
            'type': 'tool_use', 'id': 'pending', 'name': 'coding_review',
            'input': {**args, '_execution': None},
        }])
        self.manager.start_tool(context.run_id, 'pending', 'coding_review', args, side_effect=False)
        self._pause(context)
        plan = agent.run_resume.plan_resume(self.manager, context.run_id)
        self.assertFalse(plan.auto_resume)
        self.assertIn('pending', self.manager.outstanding_tools(context.run_id))

    def test_legacy_outstanding_review_with_unknown_null_does_not_replay(self):
        context = self._create('legacy-unknown-review')
        args = {'task_id': self.fixture.tid, 'action': 'status'}
        self._model_output(context, text='', stop_reason='tool_use', blocks=[{
            'type': 'tool_use', 'id': 'pending', 'name': 'coding_review',
            'input': {**args, 'unknown_keyword': None},
        }])
        self.manager.start_tool(context.run_id, 'pending', 'coding_review', args, side_effect=False)
        self._pause(context)
        plan = agent.run_resume.plan_resume(self.manager, context.run_id)
        self.assertFalse(plan.auto_resume)
        self.assertIn('pending', self.manager.outstanding_tools(context.run_id))

    def _wrapped_review(self, suffix, *, action='status'):
        context = self._create(suffix)
        args = {'task_id': self.fixture.tid, 'action': action}
        self._model_output(context, text='', stop_reason='tool_use', blocks=[{
            'type': 'tool_use', 'id': 'wrapped', 'name': 'call',
            'input': {'name': 'coding_review', 'args_json': json.dumps({**args, 'review_id': None})},
        }])
        return context, args

    def test_completed_wrapped_review_reuses_record_without_dispatch(self):
        for recorded_null in (False, True):
            with self.subTest(recorded_null=recorded_null):
                context, args = self._wrapped_review(f'wrapped-completed-{recorded_null}')
                if recorded_null:
                    args['review_id'] = None
                rid = context.run_id
                self.manager.start_tool(rid, 'wrapped', 'coding_review', args, side_effect=False)
                content = '{"reviews": []}'
                ref = self.manager.store_result(rid, content, call_id='wrapped', name='coding_review')
                self._pause(context)
                plan = agent.run_resume.plan_resume(self.manager, rid)
                self.assertTrue(plan.auto_resume, plan)
                self.assertIsNone(plan.checkpoint)
                [call] = plan.tool_calls
                self.assertEqual((call.call_id, call.name, call.state), ('wrapped', 'coding_review', 'completed'))
                self.assertFalse(call.replayable)
                self.assertEqual(call.result_ref, ref)
                dispatch = mock.Mock(side_effect=AssertionError('unexpected dispatch'))
                with mock.patch.dict(agent.TOOL_IMPL, {'call': dispatch, 'coding_review': dispatch}), \
                        mock.patch.object(agent._AgentResumeRuntime, 'execute_pending_tool', dispatch), \
                        mock.patch.object(agent._AgentResumeRuntime, 'replay_outstanding_tool', dispatch), \
                        mock.patch.object(agent.social, 'owner_id', return_value='100'), \
                        mock.patch.object(agent, 'guard_outbound_reply', side_effect=self._passthrough), \
                        mock.patch.object(agent, '_terminal_tool_loop', return_value='Continued.') as continued:
                    report = agent.resume_durable_run(rid)
                self.assertEqual(report['status'], 'completed', report)
                dispatch.assert_not_called()
                continued.assert_called_once()
                self.assertEqual(continued.call_args.kwargs['messages'][-1]['content'], [{
                    'type': 'tool_result', 'tool_use_id': 'wrapped', 'content': agent._result_for_model(ref),
                }])
                self.assertEqual([r['result'] for r in self.manager.events(rid)
                                  if r['kind'] == 'tool_result' and r.get('call_id') == 'wrapped'], [ref])

    def test_unsettled_or_mismatched_wrapped_review_never_dispatches(self):
        for case in ('pending', 'outstanding', 'wrong_args', 'wrong_result_name', 'wrong_ref_call'):
            with self.subTest(case=case):
                context, args = self._wrapped_review(f'wrapped-{case}', action='decide')
                rid = context.run_id
                if case != 'pending':
                    if case == 'wrong_args':
                        args['action'] = 'status'
                    self.manager.start_tool(rid, 'wrapped', 'coding_review', args, side_effect=False)
                if case in ('wrong_args', 'wrong_result_name', 'wrong_ref_call'):
                    name = 'call' if case == 'wrong_result_name' else 'coding_review'
                    if case == 'wrong_ref_call':
                        ref = self.manager.store_result(rid, 'other call', call_id='other', name=name)
                        self.manager.append_event(rid, 'tool_result', call_id='wrapped', name=name, result=ref)
                    else:
                        self.manager.store_result(rid, 'record', call_id='wrapped', name=name)
                self._pause(context)
                plan = agent.run_resume.plan_resume(self.manager, rid)
                self.assertFalse(plan.auto_resume, plan)
                dispatch = mock.Mock(side_effect=AssertionError('unexpected dispatch'))
                with mock.patch.dict(agent.TOOL_IMPL, {'call': dispatch, 'coding_review': dispatch}), \
                        mock.patch.object(agent._AgentResumeRuntime, 'execute_pending_tool', dispatch), \
                        mock.patch.object(agent._AgentResumeRuntime, 'replay_outstanding_tool', dispatch), \
                        mock.patch.object(agent.social, 'owner_id', return_value='100'), \
                        mock.patch.object(agent, '_terminal_tool_loop', side_effect=AssertionError('unexpected continuation')) as continued:
                    report = agent.resume_durable_run(rid)
                self.assertFalse(report['lease_acquired'], report)
                self.assertFalse(report['effects_started'], report)
                continued.assert_not_called()
                dispatch.assert_not_called()

    def test_wrong_kind_and_blank_roots_refuse_without_writes(self):
        made = forge_review.prepare(self.fixture.tid, paths=['code.py'])
        directory, _ = forge_review._record(self.fixture.tid, made['id'])
        record = (directory / 'record.json').read_bytes()
        before = set(directory.parent.iterdir())
        for action, extra, message in (
            ('start_merge', {'review_id': made['id']}, 'expected a merge id'),
            ('prepare_merge', {'base': ' ', 'local': str(self.fixture.root),
                               'incoming': str(self.fixture.root)}, 'nonblank'),
        ):
            with self.subTest(action=action):
                run_id, rows = self._dispatch(action, {'task_id': self.fixture.tid, 'action': action, **extra})
                self.assertIn(message, self._assert_settled(run_id, rows, side_effect=True, refused=True))
        self.assertEqual(set(directory.parent.iterdir()), before)
        self.assertEqual((directory / 'record.json').read_bytes(), record)

    def test_bind_refuses_bad_result_before_evidence_and_keeps_partial_write_uncertain(self):
        import forge_battery
        from test_forge_battery_server import BoundBattery

        (self.fixture.root / 'test_candidate.py').write_text(
            'import unittest\nclass Actual(unittest.TestCase):\n def test_answer(self):\n  self.assertEqual(1, 1)\n')
        tid = self.fixture.tid
        rid = forge_review.prepare(tid)['id']
        self.plan = forge_battery.prepare(tid, rid)
        vid, unit = BoundBattery.execute(self)
        directory, _ = forge_review._record(tid, rid)
        evidence = directory / 'verification' / 'evidence'
        args = {'task_id': tid, 'action': 'bind_verification', 'review_id': rid,
                'verification_id': vid, 'expected_candidate_sha256': self.plan['candidate_sha256']}
        result = unit / 'result.json'
        raw = result.read_bytes()
        wrong = json.loads(raw)
        wrong['checks'][0]['id'] = 'wrong-check'
        result.write_text(json.dumps(wrong))
        run_id, rows = self._dispatch('bad-bind', args)
        self.assertIn('check result identity mismatch', self._assert_settled(
            run_id, rows, side_effect=True, refused=True))
        self.assertFalse(evidence.exists())
        result.write_bytes(raw)

        with mock.patch.object(forge_battery.runtime, 'atomic_json',
                               side_effect=forge_review._InvalidReviewInput('injected after evidence write')):
            run_id, rows = self._dispatch('partial-bind', args)
        self.assertTrue(list(evidence.glob('check-*.txt')))
        self.assertFalse((evidence.parent / 'report.json').exists())
        self.assertEqual(self.manager.manifest(run_id)['status'], 'in_doubt')
        self.assertIn('review-call', self.manager.outstanding_tools(run_id))
        self.assertIn('tool_uncertain_error', rows)
        self.assertNotIn('tool_result', rows)

    def test_prepare_refuses_detected_credentials_before_creating_a_snapshot(self):
        (self.fixture.root / 'blocked.py').write_bytes(b'ghp_' + b'x' * 36)
        run_id, rows = self._dispatch('credential-fixture', {
            'task_id': self.fixture.tid, 'action': 'prepare'})
        result = self._assert_settled(run_id, rows, side_effect=True, refused=True)
        self.assertIn('possible credential in review source: blocked.py', result)
        self.assertFalse((forge_review.forge._task_dir(self.fixture.tid) / 'reviews').exists())

    def test_resumed_review_refusal_uses_the_same_dispatch_outcome(self):
        context = self._create('resume-review')
        self._model_output(context, text='', stop_reason='tool_use', blocks=[{
            'type': 'tool_use', 'id': 'pending', 'name': 'coding_review',
            'input': {'task_id': self.fixture.tid, 'action': 'start', 'review_id': 'review-latest'},
        }])
        self._pause(context)
        with mock.patch.object(agent.social, 'owner_id', return_value='100'), \
                mock.patch.object(agent, 'guard_outbound_reply', side_effect=self._passthrough), \
                mock.patch.object(agent, '_terminal_tool_loop', return_value='Continued.') as continued:
            agent.resume_durable_run(context.run_id)
        continued.assert_called_once()
        self.assertNotEqual(self.manager.manifest(context.run_id)['status'], 'in_doubt')
        self.assertNotIn('pending', self.manager.outstanding_tools(context.run_id))
        results = [r for r in self.manager.events(context.run_id)
                   if r.get('call_id') == 'pending' and r['kind'] == 'tool_result']
        self.assertEqual([r['metadata']['outcome'] for r in results], ['refused'])

    def test_early_legacy_admission_refusals_settle_without_policy_writes(self):
        forge = forge_review.forge
        tid = self.fixture.tid
        parent_id = 'code-11223344'
        parent = dict(self.fixture.task, id=parent_id, status='active')
        for case, task_status, parent_status, parent_fence, message in (
            ('terminal', 'done', None, None, 'task is terminal or missing'),
            ('missing-parent', 'active', None, None, 'coordinator task is terminal or missing'),
            ('terminal-parent', 'active', 'done', None, 'coordinator task is terminal or missing'),
            ('paused-parent', 'active', 'active', 'pause', 'coordinator task is pause-fenced'),
        ):
            with self.subTest(case=case):
                task = dict(self.fixture.task, status=task_status)
                del task['max_active_workers']
                if case != 'terminal':
                    task['coordinator_task_id'] = parent_id
                if parent_status:
                    forge._save_task(dict(parent, status=parent_status))
                if parent_fence:
                    forge._atomic_json(forge._task_dir(parent_id) / 'control.json', {'action': parent_fence})
                forge._save_task(task)
                directory = forge._task_dir(tid)
                before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
                run_id, rows = self._dispatch(case, {'task_id': tid, 'action': 'prepare'})
                self.assertIn(message, self._assert_settled(run_id, rows, side_effect=True, refused=True))
                self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}, before)
                self.assertFalse((directory / 'migrations').exists())
                self.assertFalse((directory / 'reviews').exists())

    def test_own_fence_after_legacy_policy_migration_is_uncertain(self):
        forge = forge_review.forge
        tid = self.fixture.tid
        task = forge.get(tid)
        del task['max_active_workers']
        forge._save_task(task)
        original = forge._task_file(tid).read_bytes()
        directory = forge._task_dir(tid)
        forge._atomic_json(directory / 'control.json', {'action': 'pause'})
        run_id, rows = self._dispatch('own-legacy-fence', {'task_id': tid, 'action': 'prepare'})
        self.assertIsNone(forge.get(tid)['max_active_workers'])
        self.assertEqual([p.read_bytes() for p in (directory / 'migrations').glob('*.preimage')], [original])
        self.assertFalse((directory / 'reviews').exists())
        self.assertEqual(self.manager.manifest(run_id)['status'], 'in_doubt')
        self.assertIn('review-call', self.manager.outstanding_tools(run_id))
        self.assertIn('tool_uncertain_error', rows)
        self.assertNotIn('tool_result', rows)

    def test_refusal_during_legacy_policy_write_keeps_partial_migration_uncertain(self):
        forge = forge_review.forge
        tid = self.fixture.tid
        task = forge.get(tid)
        del task['max_active_workers']
        forge._save_task(task)
        task_file = forge._task_file(tid)
        original = task_file.read_bytes()
        directory = forge._task_dir(tid)

        # Admission has already written the preimage when the task write fails.
        with mock.patch.object(forge, '_save_task', side_effect=
                forge_review._InvalidReviewInput('fixture policy write refused')) as save:
            run_id, rows = self._dispatch('legacy-policy-write', {
                'task_id': tid, 'action': 'prepare'})
        save.assert_called_once()
        self.assertIsNone(save.call_args.args[0]['max_active_workers'])
        self.assertEqual(task_file.read_bytes(), original)
        migrations = directory / 'migrations'
        self.assertEqual([p.read_bytes() for p in migrations.glob('*.preimage')], [original])
        self.assertEqual(list(migrations.glob('*.json')), [])
        self.assertFalse((directory / 'reviews').exists())
        self.assertEqual(self.manager.manifest(run_id)['status'], 'in_doubt')
        self.assertIn('review-call', self.manager.outstanding_tools(run_id))
        self.assertIn('tool_uncertain_error', rows)
        self.assertNotIn('tool_result', rows)

    def test_refusal_after_legacy_policy_migration_does_not_claim_no_effect(self):
        forge = forge_review.forge
        task = forge.get(self.fixture.tid)
        del task['max_active_workers']
        forge._save_task(task)
        run_id, rows = self._dispatch('legacy', {
            'task_id': self.fixture.tid, 'action': 'prepare', 'paths': ['memory/private']})
        self.assertIsNone(forge.get(self.fixture.tid)['max_active_workers'])
        self.assertEqual(self.manager.manifest(run_id)['status'], 'in_doubt')
        self.assertIn('review-call', self.manager.outstanding_tools(run_id))
        self.assertIn('tool_uncertain_error', rows)
        self.assertNotIn('tool_result', rows)

    def test_exception_after_critic_reservation_leaves_mutation_unknown(self):
        made = forge_review.prepare(self.fixture.tid, paths=['code.py'])
        with mock.patch.object(forge_review.forge, 'agent', side_effect=forge_review._InvalidReviewInput('invalid review id')) as spawn:
            run_id, rows = self._dispatch('lost-spawn', {
                'task_id': self.fixture.tid, 'action': 'start', 'review_id': made['id']})
        spawn.assert_called_once()
        _, record = forge_review._record(self.fixture.tid, made['id'])
        self.assertTrue(record['review_task_id'])
        self.assertEqual(record['units'][0]['status'], 'reserved')
        self.assertEqual(self.manager.manifest(run_id)['status'], 'in_doubt')
        self.assertIn('review-call', self.manager.outstanding_tools(run_id))
        self.assertIn('tool_uncertain_error', rows)
        self.assertNotIn('tool_result', rows)


class ReviewStatusIsARead(unittest.TestCase):
    def test_status_is_a_read_and_mutating_actions_stay_conservative(self):
        for action in ('status', None, ''):
            self.assertFalse(agent._tool_has_side_effect('coding_review', {'action': action, 'task_id': 'code-00000000'}), action)
        self.assertFalse(agent._tool_has_side_effect('coding_review', {'task_id': 'code-00000000'}))
        self.assertTrue(agent._tool_has_side_effect('coding_review', {}))
        for action in ('prepare', 'start', 'decide', 'prepare_merge', 'start_merge',
                       'rework_merge', 'verification_plan', 'bind_verification'):
            self.assertTrue(agent._tool_has_side_effect('coding_review', {'action': action, 'task_id': 'code-00000000'}), action)


class StatusWithoutIdListsReviews(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ServerWave2()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.tid = self.fixture.tid

    def test_listing_names_each_review_and_the_exact_id_still_reads_it(self):
        empty = json.loads(forge_review.command(self.tid, 'status'))
        self.assertEqual(empty['reviews'], [])
        made = forge_review.prepare(self.tid, paths=['code.py'])
        listed = json.loads(forge_review.command(self.tid, 'status', review_id=''))
        self.assertEqual([row['id'] for row in listed['reviews']], [made['id']])
        self.assertEqual(listed['reviews'][0]['kind'], 'review')
        exact = json.loads(forge_review.command(self.tid, 'status', review_id=made['id']))
        self.assertEqual(exact['id'], made['id'])

    def test_a_folder_left_by_a_refused_prepare_is_named_not_fatal(self):
        stray = forge_review.forge._task_dir(self.tid) / 'reviews' / ('review-' + '0' * 16)
        stray.mkdir(parents=True)
        listed = json.loads(forge_review.command(self.tid, 'status'))
        self.assertEqual(listed['reviews'], [])
        self.assertEqual(listed['incomplete_directories'], [stray.name])

    def test_a_malformed_id_is_still_refused(self):
        with self.assertRaises(ValueError):
            forge_review.command(self.tid, 'status', review_id='review-latest')


class KeyHeadersWithoutKeys(unittest.TestCase):
    # Built by concatenation so this file itself never carries a header literal.
    HEADER = b'-----BEGIN ' + b'PRIVATE KEY-----'

    def test_bare_fixture_header_is_not_a_credential(self):
        for tail in (b'', b'")', b'\\nMIIE...\\n")', b'\n'):
            self.assertIsNone(forge_review.SECRET.search(self.HEADER + tail), tail)

    def test_a_header_with_key_material_is_still_refused(self):
        line = b'MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7'
        self.assertIsNotNone(forge_review.SECRET.search(self.HEADER + b'\n' + line + b'\n' + line))
        self.assertIsNotNone(forge_review.SECRET.search(self.HEADER + b'\\n' + line + line))
        self.assertIsNotNone(forge_review.SECRET.search(b'sk-' + b'a' * 40))
        self.assertIsNotNone(forge_review.SECRET.search(b'ghp_' + b'b' * 36))


if __name__ == '__main__':
    unittest.main()
