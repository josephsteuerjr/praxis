# ⚠ ИЗДАНИЕ: затравка будильника без рода («Твоя просьба разбудить себя…») — как все
# тексты издания (keat_live, agent.legacy_seed). Её стенд ждал «Ты просила…».
"""Scheduled-wake KEAT adapter regressions; no provider or live filesystem."""
import asyncio
import copy
import json
import os
from contextlib import contextmanager
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import agent
import keat_live as live
import mtproto_runner as runner
import tasks


class ScheduledWakeCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.policy_path = Path(self.tmp.name) / 'policy.json'
        self.policy_path.write_text(json.dumps({
            'schema': 'keat.capture-policy.v1',
            'namespace': 'wake-adapter-test',
            'root': self.tmp.name,
            'enrollments': [{
                'stream': 'scheduler:wake', 'mode': 'wake',
                'capture': {
                    'issuer': 'explicit-test-admin', 'policy_revision': 'p1',
                    'audience': ['owner'], 'transfer': 'none',
                    'presence_hidden': False,
                },
            }],
        }))
        self.env = patch.dict(os.environ, {
            'PRAXIS_KEAT_CAPTURE': 'on',
            'PRAXIS_KEAT_CAPTURE_POLICY': str(self.policy_path),
            'PRAXIS_KEAT': 'serve',
            'PRAXIS_KEAT_MODES': 'wake',
            'PRAXIS_KEAT_STREAMS': 'scheduler:wake',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        # Exact mode/stream selectors above are the contract under test. An
        # ambient pair selector takes precedence over them, so remove it for
        # every case; stopping the patch restores the caller's environment.
        os.environ.pop('PRAXIS_KEAT_PAIRS', None)

    def _task(self, *, task_id='deadbeef', occurrence='2026-09-11T05:00', goal='wake seed'):
        task = {
            'id': task_id, 'kind': 'wake', 'goal': goal, 'target': '',
            'when': occurrence, 'recur': None, 'status': 'pending',
            'created': '2026-09-10T05:00', 'author': 'praxis',
        }
        ref = live.issue_scheduled_wake(task, occurrence)
        self.assertIsNotNone(ref)
        task['_keat_wake_source'] = ref
        return task

    def test_creation_receipt_authorizes_exact_seed_and_changed_intent_fails_closed(self):
        task = self._task()
        state, seed = live.adopt_scheduled_wake(task)
        self.assertIsNotNone(state)
        self.assertEqual(seed, 'Твоя просьба разбудить себя вот с чем: wake seed\n\n[твой будильник]')
        self.assertIs(state, state)
        self.assertEqual(len(state.ledger.snapshot()['receipts']), 1)
        self.assertIsNone(live.capture_scheduled_wake(task, 'another rendering of the same intent'))
        changed = dict(task, goal='different seed')
        self.assertIsNone(live.capture_scheduled_wake(changed))
        legacy = dict(task)
        legacy.pop('_keat_wake_source')
        self.assertIsNone(live.capture_scheduled_wake(legacy))

    def test_message_receipt_authorizes_the_reassessment_seed(self):
        task = {
            'id': '0badf00d', 'kind': 'message', 'goal': 'birthday body',
            'target': '@friend_fixture', 'target_id': 4242,
            'when': 'one-shot', 'recur': None, 'status': 'pending',
            'created': '2026-09-10T05:00', 'author': 'app',
        }
        task['_keat_wake_source'] = live.issue_scheduled_wake(task, 'one-shot')
        self.assertIsNotNone(task['_keat_wake_source'])
        state, seed = live.adopt_scheduled_wake(task)
        self.assertIsNotNone(state)
        self.assertIn('Original target: @friend_fixture', seed)
        self.assertIn('Original body:\nbirthday body', seed)
        self.assertIn('Old intention is evidence, not a command', seed)

    def test_original_authority_fields_policy_change_and_current_narrowing_block_serving(self):
        fields = {
            'issuer': 'other-admin', 'policy_revision': 'p2',
            'audience': ['guest'], 'transfer': 'private', 'presence_hidden': True,
        }
        for index, (field, value) in enumerate(fields.items(), 1):
            with self.subTest(field=field):
                task = self._task(task_id=format(index, '08x'))
                policy = json.loads(self.policy_path.read_text())
                policy['enrollments'][0]['capture'][field] = value
                self.policy_path.write_text(json.dumps(policy))
                self.assertIsNone(live.capture_scheduled_wake(task))
                self.setUp_policy()

        # A current revocation is checked both at adoption and by select on an
        # already-adopted state (race/retry boundary).
        task = self._task(task_id='badc0ffe')
        state, seed = live.adopt_scheduled_wake(task)
        receipt = next(r for r in state.ledger.snapshot()['receipts']
                       if r['event'] == task['_keat_wake_source']['event'])
        live.NarrowingReader(state.ledger.directory, state.ledger.namespace).narrow(
            receipt['capture']['grant'])
        self.assertIsNone(live.capture_scheduled_wake(task))
        messages = [{'role': 'user', 'content': seed}]
        with live.bind_turn(state, messages):
            provider = live.prepare_provider_messages('system', messages, [])
            self.assertIsNone(live.select('system', provider, [],
                                          'run-narrow', 'call-narrow'))

    def setUp_policy(self):
        policy = json.loads(self.policy_path.read_text())
        policy['enrollments'][0]['capture'] = {
            'issuer': 'explicit-test-admin', 'policy_revision': 'p1',
            'audience': ['owner'], 'transfer': 'none', 'presence_hidden': False,
        }
        self.policy_path.write_text(json.dumps(policy))

    def test_current_audience_narrowing_blocks_adoption_and_select(self):
        policy = json.loads(self.policy_path.read_text())
        policy['enrollments'][0]['capture']['audience'] = ['owner', 'guest']
        self.policy_path.write_text(json.dumps(policy))
        task = self._task(task_id='decafbad')
        state, seed = live.adopt_scheduled_wake(task)
        receipt = next(r for r in state.ledger.snapshot()['receipts']
                       if r['event'] == task['_keat_wake_source']['event'])
        narrowed = dict(receipt['capture'], audience=['owner'])
        live.NarrowingReader(state.ledger.directory, state.ledger.namespace).narrow(
            receipt['capture']['grant'], narrowed)
        self.assertIsNone(live.capture_scheduled_wake(task))
        messages = [{'role': 'user', 'content': seed}]
        with live.bind_turn(state, messages):
            provider = live.prepare_provider_messages('system', messages, [])
            self.assertIsNone(live.select('system', provider, [],
                                          'run-narrow-audience', 'call-narrow-audience'))

    def test_wake_turn_uses_exact_creation_seed_and_actual_select(self):
        task = self._task(task_id='facefeed')
        selected = []

        def voice(seed, history, **kwargs):
            self.assertEqual(seed, 'Твоя просьба разбудить себя вот с чем: wake seed\n\n[твой будильник]')
            state = agent._KEAT_CAPTURE_STATE.get()
            messages = [{'role': 'user', 'content': seed}]
            with live.bind_turn(state, messages):
                provider = live.prepare_provider_messages('system', messages, [])
                selected.append(live.select('system', provider, [], 'run-wake-real', 'call-wake-real'))
            return 'answer'

        fake_turn = {'tools': [], 'out': ''}
        with patch.object(agent.llm, 'configured', return_value=True), \
             patch.object(agent, 'telegram_transport_status', return_value='connected'), \
             patch.object(agent, '_create_durable_run', return_value=None), \
             patch.object(agent, '_voice', side_effect=voice), \
             patch.object(agent.turns, 'begin', return_value=fake_turn), \
             patch.object(agent.turns, 'record'), \
             patch.object(agent, '_self_intent_eligible', return_value=False):
            self.assertEqual(agent.wake_turn('wake seed', source_id=task), 'answer')
        self.assertTrue(selected[0]['eligible'])
        self.assertEqual(selected[0]['binding']['mode'], 'wake')

    def test_exact_tool_tape_checkpoints_and_resumes_without_rewrite(self):
        state, seed = live.adopt_scheduled_wake(
            self._task(task_id='cafef00d', occurrence='one-shot'))
        tape = [{'role': 'user', 'content': seed}]
        before = copy.deepcopy(tape)
        with live.bind_turn(state, tape):
            provider = live.prepare_provider_messages('system', tape, [])
            first = live.select('system', provider, [], 'run-wake', 'call-1')
            addition = {'role': 'assistant', 'content': [{
                'type': 'tool_use', 'id': 'tool-1', 'name': 'shell', 'input': {'x': 1},
            }]}
            self.assertTrue(live.capture_appended(tape, [addition]))
            tape.append(addition)
            provider = live.prepare_provider_messages('system', tape, [])
            second = live.select('system', provider, [], 'run-wake', 'call-2')
        self.assertEqual(before, tape[:1])
        self.assertEqual(first['binding']['mode'], 'wake')
        self.assertEqual(first['binding']['stream'], 'scheduler:wake')
        saved = copy.deepcopy({'system': 'system', 'messages': tape, 'tools': [], 'keat': second})
        with live.bind_resume(saved, 'run-wake', 'call-2') as resumed:
            self.assertIsNotNone(resumed)
            canonical = saved['messages']
            result = {'role': 'user', 'content': [{
                'type': 'tool_result', 'tool_use_id': 'tool-1', 'content': 'exact result',
            }]}
            self.assertTrue(live.capture_appended(canonical, [result]))
            canonical.append(result)
            provider = live.prepare_provider_messages('system', canonical, [])
            third = live.select('system', provider, [], 'run-wake', 'call-3')
        self.assertEqual(third['epoch'], first['epoch'])
        self.assertEqual(saved['messages'][-1], result)

    def test_default_off_or_missing_source_keeps_unchanged_fallback(self):
        with patch.dict(os.environ, {'PRAXIS_KEAT_CAPTURE': ''}):
            self.assertIsNone(live.issue_scheduled_wake({
                'id': '1234abcd', 'kind': 'wake', 'goal': 'seed', 'created': 'now',
            }, 'one-shot'))
        self.assertIsNone(live.capture_scheduled_wake(None, 'seed'))

    def test_task_store_persists_receipt_at_creation_and_next_recurrence(self):
        path = Path(self.tmp.name) / 'tasks.json'
        prior = tasks.TASKS
        tasks.TASKS = path
        self.addCleanup(setattr, tasks, 'TASKS', prior)
        task = tasks.add('wake', 'seed', 'daily 09:00')
        saved = tasks._load()[0]
        self.assertNotIn('_keat_wake_source', saved)
        # daily has no concrete occurrence until due() schedules it.
        self.assertIsNone(saved['when'])
        self.assertEqual(tasks.due(), [])
        scheduled = tasks._load()[0]
        self.assertTrue(scheduled['when'])
        self.assertIn('_keat_wake_source', scheduled)
        # Reloaded row is the restart/handoff boundary; firing only adopts it.
        self.assertIsNotNone(live.capture_scheduled_wake(scheduled))
        first_event = scheduled['_keat_wake_source']['event']
        scheduled['when'] = '2020-01-01T00:00'
        # Simulate the same durable occurrence with an earlier clock for firing.
        source = live.issue_scheduled_wake(scheduled, scheduled['when'])
        scheduled['_keat_wake_source'] = source
        first_event = source['event']
        tasks._save([scheduled])
        tasks.mark_fired(task['id'])
        next_occurrence = tasks._load()[0]
        self.assertNotEqual(next_occurrence['_keat_wake_source']['event'], first_event)

    def _provider_current(self, state, candidate, legacy, mutate=None, **env):
        messages = [{'role': 'user', 'content': candidate}]
        response = SimpleNamespace(blocks=[], text='ok', stop_reason='end_turn')
        store = SimpleNamespace(store_result=lambda *args, **kwargs: None)
        with patch.dict(os.environ, env), \
             live.bind_turn(state, messages, fallback_current={
                 'role': 'user', 'content': legacy,
             }), \
             patch.object(agent.run_context, 'current_run',
                          return_value=SimpleNamespace(run_id='wake-run')), \
             patch.object(agent, '_runs', return_value=store), \
             patch.object(agent, '_run_status_gate'), \
             patch.object(agent, '_run_event_strict'), \
             patch.object(agent.frame_serve, 'select',
                          side_effect=lambda system, messages, tools: (system, None)), \
             patch.object(agent.llm, 'chat', return_value=response) as chat:
            self.assertEqual(messages, [{'role': 'user', 'content': legacy}])
            if mutate is not None:
                replacement = mutate(messages)
                if replacement is not None:
                    messages = replacement
            agent._model_call('system', messages, [])
        return copy.deepcopy(chat.call_args.kwargs['messages'])

    def test_provider_seed_changes_only_for_exact_served_wake_selector(self):
        task = self._task(task_id='feedface')
        state, candidate = live.adopt_scheduled_wake(task)
        legacy = candidate[:-1] + '; Telegram открыт — связь живая]'
        cases = (
            ('capture-only', {'PRAXIS_KEAT': 'off'}, legacy),
            ('wake-mode-absent', {'PRAXIS_KEAT_MODES': 'owner'}, legacy),
            ('wake-stream-unstaged', {'PRAXIS_KEAT_STREAMS': 'another'}, legacy),
            ('exact-served', {}, candidate),
        )
        for name, env, expected in cases:
            with self.subTest(name=name):
                # select mutates the epoch state; adoption gives each call its own
                # clean firing-time view of the same durable occurrence.
                current, seed = live.adopt_scheduled_wake(task)
                self.assertEqual(self._provider_current(current, seed, legacy, **env),
                                 [{'role': 'user', 'content': expected}])

    def test_revocation_between_adoption_and_model_selection_keeps_legacy_seed(self):
        task = self._task(task_id='0ddba11a')
        state, candidate = live.adopt_scheduled_wake(task)
        receipt = next(r for r in state.ledger.snapshot()['receipts']
                       if r['event'] == task['_keat_wake_source']['event'])
        live.NarrowingReader(state.ledger.directory, state.ledger.namespace).narrow(
            receipt['capture']['grant'])
        legacy = candidate[:-1] + '; связи сейчас нет (disconnected)]'
        self.assertEqual(self._provider_current(state, candidate, legacy),
                         [{'role': 'user', 'content': legacy}])

    def test_provider_faults_restore_legacy_without_authority_binding(self):
        task = self._task(task_id='fa01700d')
        for fault in ('snapshot', 'references', 'select', 'no-state', 'policy'):
            with self.subTest(fault=fault):
                state, candidate = live.adopt_scheduled_wake(task)
                legacy = candidate + ' LIVE transport'
                from contextlib import nullcontext
                failure = nullcontext()
                if fault == 'snapshot':
                    failure = patch.object(state.ledger, 'snapshot', side_effect=OSError('disk'))
                elif fault == 'references':
                    failure = patch.object(live, '_references', side_effect=ValueError('bad reference'))
                elif fault == 'select':
                    failure = patch.object(live, 'select', side_effect=RuntimeError('selector'))
                elif fault == 'no-state':
                    state = None
                elif fault == 'policy':
                    failure = patch.object(live, '_json', side_effect=ValueError('changed policy'))
                with failure:
                    self.assertEqual(self._provider_current(state, candidate, legacy),
                         [{'role': 'user', 'content': legacy}])

    def test_capture_only_snapshot_failure_and_changed_tape_keep_legacy(self):
        # Fault the durable read only AFTER successful source adoption. This
        # exercises bind_turn's authority failure, not the earlier adapter's
        # fail-closed return, through the real _model_call/provider boundary.
        task = self._task(task_id='ca970001')
        for mode in ('', 'off', 'serve'):
            for changed in (False, True):
                with self.subTest(mode=mode, changed=changed):
                    state, candidate = live.adopt_scheduled_wake(task)
                    self.assertIsNotNone(state)
                    legacy = candidate[:-1] + '; Telegram открыт — связь живая]'
                    mutation = (lambda tape: tape.append({
                        'role': 'assistant', 'content': 'unissued',
                    })) if changed else None
                    with patch.object(state.ledger, 'snapshot',
                                      side_effect=OSError('synthetic disk failure')) as snapshot:
                        actual = self._provider_current(
                            state, candidate, legacy, mutate=mutation,
                            PRAXIS_KEAT=mode,
                        )
                    snapshot.assert_called()
                    expected = [{'role': 'user', 'content': legacy}]
                    if changed:
                        expected.append({'role': 'assistant', 'content': 'unissued'})
                    self.assertEqual(actual, expected)
                    self.assertIsNone(state.head)

    def test_changed_canonical_tape_disables_candidate_without_deleting_changes(self):
        task = self._task(task_id='a17e2ed0')
        cases = (
            (lambda tape: tape[0].update(content='changed'),
             [{'role': 'user', 'content': 'changed'}]),
            (lambda tape: tape.append({'role': 'assistant', 'content': 'unissued'}), None),
            (lambda tape: tape.clear(), []),
            (lambda tape: [{'role': 'user', 'content': 'replacement unissued'}],
             [{'role': 'user', 'content': 'replacement unissued'}]),
        )
        for mutation, expected in cases:
            state, candidate = live.adopt_scheduled_wake(task)
            legacy = candidate + ' LIVE transport'
            if expected is None:
                expected = [{'role': 'user', 'content': legacy},
                            {'role': 'assistant', 'content': 'unissued'}]
            self.assertEqual(
                self._provider_current(state, candidate, legacy, mutate=mutation), expected)

    def test_capture_disabled_wake_turn_preserves_legacy_seed(self):
        task = self._task(task_id='cab005e0')
        with patch.dict(os.environ, {'PRAXIS_KEAT_CAPTURE': 'off'}), \
             patch.object(agent.llm, 'configured', return_value=True), \
             patch.object(agent, 'telegram_transport_status', return_value='connected'), \
             patch.object(agent, '_create_durable_run', return_value=None), \
             patch.object(agent, '_voice', return_value='answer') as voice, \
             patch.object(agent.turns, 'begin', return_value={'tools': [], 'out': ''}), \
             patch.object(agent.turns, 'record'), \
             patch.object(agent, '_self_intent_eligible', return_value=False):
            self.assertEqual(agent.wake_turn('wake seed', source_id=task), 'answer')
        self.assertEqual(voice.call_args.args[0],
                         'Твоя просьба разбудить себя вот с чем: wake seed\n\n'
                         '[твой будильник; Telegram открыт — связь живая]')

    def test_agent_uses_preissued_state_without_runtime_recapture(self):
        state = live.capture_scheduled_wake({'task_id': '1234abcd', 'occurrence': 'one-shot'}, 'seed')
        seen = []

        @contextmanager
        def bind(actual, messages, **kwargs):
            seen.append(actual)
            yield actual

        token = agent._KEAT_CAPTURE_STATE.set(state)
        try:
            with patch.object(agent.keat_live, 'capture_turn') as recapture, \
                 patch.object(agent.keat_live, 'bind_turn', side_effect=bind), \
                 patch.object(agent, '_build_prompt_parts', return_value=('P', 'D', '')), \
                 patch.object(agent, '_terminal_tool_loop', return_value='answer'), \
                 patch.object(agent.frame_shadow, 'enabled', return_value=False):
                result = agent._voice_impl('seed', [], None, ctx=agent.ChannelContext(
                    chat_id=None, principal_id=agent.PRAXIS_SELF_PRINCIPAL,
                    is_dm=True, owner=False, known=True, _scope_override='owner'))
        finally:
            agent._KEAT_CAPTURE_STATE.reset(token)
        self.assertEqual(result, 'answer')
        self.assertEqual(seen, [state])
        recapture.assert_not_called()

    def test_failed_adapter_capture_does_not_fall_through_to_inherited_ingress(self):
        seen = []

        @contextmanager
        def bind(actual, messages, **kwargs):
            seen.append(actual)
            yield actual

        capture_token = agent._KEAT_CAPTURE_STATE.set(None)
        ingress_token = agent._KEAT_ORIGINAL_INGRESS.set(True)
        try:
            with patch.object(agent.keat_live, 'capture_turn') as recapture, \
                 patch.object(agent.keat_live, 'bind_turn', side_effect=bind), \
                 patch.object(agent, '_build_prompt_parts', return_value=('P', 'D', '')), \
                 patch.object(agent, '_terminal_tool_loop', return_value='answer'), \
                 patch.object(agent.frame_shadow, 'enabled', return_value=False):
                result = agent._voice_impl('seed', [], None, ctx=agent.ChannelContext(
                    chat_id=None, principal_id=agent.PRAXIS_SELF_PRINCIPAL,
                    is_dm=True, owner=False, known=True, _scope_override='owner'))
        finally:
            agent._KEAT_ORIGINAL_INGRESS.reset(ingress_token)
            agent._KEAT_CAPTURE_STATE.reset(capture_token)
        self.assertEqual(result, 'answer')
        self.assertEqual(seen, [None])
        recapture.assert_not_called()

    def test_scheduler_forwards_persisted_task_row_not_a_firing_mint(self):
        received = []
        task = self._task(task_id='89abcdef', occurrence='one-shot')

        def wake(goal='', **kwargs):
            received.append((goal, kwargs.get('source_id')))
            return ''

        with patch.object(runner.agent, 'wake_turn', side_effect=wake), \
             patch.object(runner, '_tasks', create=True):
            self.assertTrue(asyncio.run(runner._wake_pass('goal', source_id=task)))
        self.assertEqual(received, [('goal', task)])

    def test_fire_task_hands_off_the_persisted_source_before_mark_fired(self):
        task = self._task(task_id='87654321', occurrence='one-shot')
        seen = []

        async def wake(goal, **kwargs):
            seen.append(kwargs['source_id'])
            await kwargs['on_open']()
            kwargs['on_run']('run-1')
            return True

        with patch.object(runner, '_wake_pass', side_effect=wake), \
             patch.object(tasks, 'claim_open', return_value=True), \
             patch.object(tasks, 'mark_fired') as marked:
            asyncio.run(runner._fire_task(task))
        self.assertEqual(seen, [task])
        marked.assert_called_once_with(task['id'])


if __name__ == '__main__':
    unittest.main()
