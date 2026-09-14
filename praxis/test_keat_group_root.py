"""Offline canary for the single ordinary AbstractDL root-group adapter."""
import asyncio
import copy
import datetime
import json
import os
import tempfile
import unittest
from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from telethon.tl.types import Channel, User

import agent
import frame_serve
import keat_live
import keat_readiness
import keat_runtime
import mtproto_runner as runner

ROOT = '-1001240718803'


class OrdinaryRootGroupCanary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = self.tmp.name
        self.policy = Path(root) / 'policy.json'
        self.row = dict(mode='group', stream=ROOT, capture=dict(
            issuer='offline-canary', policy_revision='ordinary-root.v1',
            audience=['group:' + ROOT], transfer='none', presence_hidden=False))
        self.policy.write_text(json.dumps(dict(
            schema='keat.capture-policy.v1', namespace='abstractdl-offline',
            root=root, enrollments=[self.row])), encoding='utf-8')
        self.env = dict(PRAXIS_KEAT_CAPTURE='on',
                        PRAXIS_KEAT_CAPTURE_POLICY=str(self.policy),
                        PRAXIS_KEAT_ROOT=root, PRAXIS_KEAT='serve',
                        PRAXIS_KEAT_PAIRS='[["group","-1001240718803"]]')

    def capture(self, mid, role, text, principal):
        return keat_live.capture_ingress(
            ROOT, is_dm=False, source_id=str(mid),
            direction='out' if role == 'assistant' else 'in',
            payload={'role': role, 'content': text, 'actor': 'Praxis' if role == 'assistant' else 'Member'},
            root_chat_id=ROOT, principal_id=principal, ordinary_root=True)

    def context(self, principal='222', mid=3, **changes):
        values = dict(chat_id=ROOT, room_chat_id=ROOT, is_dm=False, owner=False,
                      owner_audience=False, principal_id=principal,
                      origin_message_id=mid, praxis_self=False,
                      telegram_root_group=True)
        values.update(changes)
        return SimpleNamespace(**values)

    def test_offline_canary_serves_exact_message_bound_projection(self):
        with patch.dict(os.environ, self.env, clear=False):
            a = self.capture(1, 'user', 'Alice: first', '111')
            b = self.capture(2, 'assistant', 'Praxis: answer', '999')
            c = self.capture(3, 'user', 'Bob: next', '222')
            self.assertTrue(all((a, b, c)))
            history = [{'role': 'user', 'content': 'Alice: first'},
                       {'role': 'assistant', 'content': 'Praxis: answer'}]
            state = keat_live.adopt_projection(
                self.context(), history, 'Bob: next',
                {'history': [[a], [b]], 'current': [c]})
            self.assertIsNotNone(state)
            tape = history + [{'role': 'user', 'content': 'Bob: next'}]
            system = [{'type': 'text', 'text': 'constitution'}]
            with keat_live.bind_turn(state, tape):
                provider = keat_live.prepare_provider_messages(system, tape, [])
                receipt = keat_live.select(system, provider, [], 'run-group', 'call-group')
            self.assertEqual(receipt['status'], 'served')
            self.assertEqual(provider, tape)

    def test_runner_voice_provider_preserves_legacy_until_exact_selection(self):
        """Real runner + voice renderer + model selector, with offline I/O stubs."""
        from contextlib import ExitStack

        class ProviderObserved(BaseException):
            pass

        async def no_compact(_):
            pass

        policy_bytes = self.policy.read_bytes()
        for rejection in ('baseline', 'valid', 'edit', 'missing_policy',
                          'invalid_policy', 'media', 'serve_off', 'selector_off',
                          'invalid_capture', 'malformed_sidecar', 'exception'):
            with self.subTest(rejection=rejection), ExitStack() as stack:
                self.policy.write_bytes(policy_bytes)
                stack.enter_context(patch.dict(os.environ, self.env, clear=True))
                a = self.capture(81, 'assistant', 'Praxis: answer', '999')
                b = self.capture(82, 'user', 'Bob: trigger', '222')
                frozen_history = [{'role': 'assistant', 'content': 'Praxis: answer'}]
                sidecar = dict(history=[[a]], current=[b],
                               projection_history=frozen_history,
                               projection_current='Bob: trigger')
                if rejection == 'baseline':
                    sidecar = None
                elif rejection == 'missing_policy':
                    self.policy.unlink()
                elif rejection == 'invalid_policy':
                    self.policy.write_text('{}')
                elif rejection == 'serve_off':
                    os.environ['PRAXIS_KEAT'] = 'off'
                elif rejection == 'selector_off':
                    os.environ['PRAXIS_KEAT_PAIRS'] = '[]'
                elif rejection == 'malformed_sidecar':
                    sidecar = 'invalid sidecar'
                elif rejection == 'invalid_capture':
                    sidecar['current'] = []
                elif rejection == 'exception':
                    stack.enter_context(patch.object(keat_live, 'adopt_projection',
                                                     side_effect=ValueError('offline')))
                turns = ((True, 'Praxis: answer', 'answer'),
                         (False, 'Bob: trigger', 'Bob: trigger'))
                late = (False, 'Alice: late room traffic', 'Alice: late room traffic')
                wake = runner.GroupWake(
                    message_id=82, message_ts=1, kind='reply', speaker='Bob',
                    sender_id=222, owner=False, known=True, family=False,
                    context_snapshot='Praxis: answer\nBob: trigger',
                    reply_targets_snapshot=(), media_snapshot=(object(),) if rejection == 'media' else (),
                    turns_snapshot=turns, occurrence_sidecar=sidecar)
                # Revoke the actual queued capture, not a mocked adoption result.
                if rejection == 'edit':
                    event = SimpleNamespace(chat_id=int(ROOT), is_private=False,
                        message=SimpleNamespace(id=82, edit_date=None), chat=None)
                    asyncio.run(runner.on_edited(event))
                values = dict(_meta={ROOT: dict(is_dm=False, peer_id=ROOT,
                    room_nature=False, room_policy={}, room_mode='normal')},
                    _group_wakes={ROOT: wake}, _last_pass=defaultdict(float),
                    _passing=set(), _ONE_MIND=asyncio.Lock(), _missed={},
                    _MODERATION_PRIORITY_PENDING=False)
                for name, value in values.items():
                    stack.enter_context(patch.object(runner, name, value))
                for name, value in (('_cooldown', 0),
                                    ('_gate_group_wake_room_mode', ('normal', 'open')),
                                    ('_group_context_frozen', ('new tail', turns + (late,))),
                                    ('_room_policy_for_state', {})):
                    stack.enter_context(patch.object(runner, name, return_value=value))
                stack.enter_context(patch.object(runner, '_maybe_compact', side_effect=no_compact))
                stack.enter_context(patch.object(runner, '_arm'))
                for name, value in (('_create_durable_run', None), ('_archive_run_media', {}),
                                    ('_presence_frame', ''), ('_presence_evidence', ''),
                                    ('_build_prompt_parts', ('persona', 'dynamic', ''))):
                    stack.enter_context(patch.object(agent, name, return_value=value))
                stack.enter_context(patch.object(agent, '_media_prompt',
                    side_effect=lambda convo, refs, ctx, model_text=None: (convo, model_text or convo)))
                stack.enter_context(patch.object(agent.turns, 'begin', return_value={}))
                stack.enter_context(patch.object(agent.llm, 'configured', return_value=True))
                stack.enter_context(patch.object(agent.frame_shadow, 'enabled', return_value=False))
                stack.enter_context(patch.object(agent, '_run_status_gate'))
                stack.enter_context(patch.object(agent, '_run_event_strict'))
                stack.enter_context(patch.object(agent.run_context, 'current_run',
                    return_value=SimpleNamespace(run_id='offline-run')))
                stack.enter_context(patch.object(agent, '_runs', return_value=SimpleNamespace(
                    store_result=lambda *a, **kw: None)))
                sent = []
                def chat(*args, **kwargs):
                    sent.append(copy.deepcopy({k: kwargs[k] for k in ('system', 'messages', 'tools')}))
                    raise ProviderObserved()
                stack.enter_context(patch.object(agent.llm, 'chat', side_effect=chat))
                def terminal(**kwargs):
                    agent._model_call(kwargs['system'], kwargs['messages'], [])
                stack.enter_context(patch.object(agent, '_terminal_tool_loop', side_effect=terminal))
                with self.assertRaises(ProviderObserved):
                    asyncio.run(runner._run_pass(ROOT))
                self.assertEqual(len(sent), 1)
                encoded = json.dumps(sent[0], ensure_ascii=False, separators=(',', ':')).encode()
                if rejection == 'baseline':
                    legacy = encoded
                    self.assertIn(b'Alice: late room traffic', legacy)
                    self.assertEqual(sent[0]['messages'][0]['content'], 'answer')
                elif rejection == 'valid':
                    self.assertNotIn(b'Alice: late room traffic', encoded)
                    self.assertEqual(sent[0]['messages'][0]['content'], 'Praxis: answer')
                else:
                    self.assertEqual(encoded, legacy)
                # Each subcase must receive fresh grants, including after revocation.
                if rejection == 'edit':
                    # Subsequent cases use a new isolated ledger namespace.
                    policy = json.loads(policy_bytes)
                    policy['namespace'] += '-after-edit'
                    policy_bytes = json.dumps(policy).encode()

    def test_wrong_root_ambiguous_sender_and_nonordinary_are_not_captured(self):
        with patch.dict(os.environ, self.env, clear=False):
            baseline = dict(role='user', content='Member: x', actor='Member')
            for kwargs in (
                dict(chat_id='1001240718803', root_chat_id='1001240718803', principal_id='2', ordinary_root=True),
                dict(chat_id=ROOT, root_chat_id=ROOT, principal_id=None, ordinary_root=True),
                dict(chat_id=ROOT, root_chat_id=ROOT, principal_id='-2', ordinary_root=True),
                dict(chat_id=ROOT, root_chat_id=ROOT, principal_id='2', ordinary_root=False),
            ):
                self.assertIsNone(keat_live.capture_ingress(
                    kwargs.pop('chat_id'), is_dm=False, payload=copy.deepcopy(baseline),
                    source_id='10', direction='in', **kwargs))

    def test_changed_trigger_principal_topic_or_root_falls_back_without_tape_mutation(self):
        with patch.dict(os.environ, self.env, clear=False):
            a = self.capture(1, 'assistant', 'Praxis: answer', '999')
            b = self.capture(2, 'user', 'Bob: next', '222')
            history = [{'role': 'assistant', 'content': 'Praxis: answer'}]
            sidecar = {'history': [[a]], 'current': [b]}
            for ctx in (self.context('333', 2), self.context('222', 99),
                        self.context('222', 2, chat_id=ROOT + '__topic__7'),
                        self.context('222', 2, telegram_root_group=False)):
                before = copy.deepcopy(history)
                self.assertIsNone(keat_live.adopt_projection(ctx, history, 'Bob: next', sidecar))
                self.assertEqual(history, before)

    def test_wake_projection_is_frozen_and_requires_exact_trigger_tail(self):
        with patch.dict(os.environ, self.env, clear=False), \
             patch.object(runner.memory_life, 'hot_records') as hot:
            a = self.capture(1, 'assistant', 'Praxis: answer', '999')
            b = self.capture(2, 'user', 'Bob: next', '222')
            hot.return_value = [
                {'direction': 'out', 'line': 'Praxis: answer', 'source_id': '1',
                 'meta': {'keat_occurrence': a}},
                {'direction': 'in', 'line': 'Bob: next', 'source_id': '2',
                 'meta': {'keat_occurrence': b}},
            ]
            history, current, sidecar = runner._group_native_projection(ROOT, 2)
            frozen = copy.deepcopy(sidecar)
            hot.return_value.append({'direction': 'in', 'line': 'late', 'source_id': '3',
                                     'meta': {}})
            self.assertEqual(sidecar, frozen)
            self.assertEqual((history, current),
                             ([{'role': 'assistant', 'content': 'Praxis: answer'}], 'Bob: next'))
            self.assertEqual(runner._group_native_projection(ROOT, 2), ([], '', {}))

    def test_edit_routes_exact_group_invalidation_and_old_occurrence_stays_revoked(self):
        with patch.dict(os.environ, self.env, clear=False):
            a = self.capture(1, 'assistant', 'Praxis: answer', '999')
            b = self.capture(2, 'user', 'Bob: old', '222')
            history = [{'role': 'assistant', 'content': 'Praxis: answer'}]
            sidecar = {'history': [[a]], 'current': [b]}
            self.assertIsNotNone(keat_live.adopt_projection(
                self.context(mid=2), history, 'Bob: old', sidecar))
            event = SimpleNamespace(
                chat_id=int(ROOT), is_private=False, chat=None,
                message=SimpleNamespace(
                    id=2, edit_date=datetime.datetime.now(), message='Bob: changed',
                    media=None, out=False),
                get_sender=AsyncMock(return_value=SimpleNamespace(is_self=True)))
            with patch.object(runner, '_known_forum', return_value=False):
                asyncio.run(runner.on_edited(event))
            self.assertIsNone(keat_live.adopt_projection(
                self.context(mid=2), history, 'Bob: old', sidecar))
            self.assertIsNone(self.capture(2, 'user', 'Bob: old', '222'))

    def test_edit_without_edit_date_revokes_before_legacy_noop_and_blocks_readoption(self):
        with patch.dict(os.environ, self.env, clear=False):
            a = self.capture(1, 'assistant', 'Praxis: answer', '999')
            b = self.capture(2, 'user', 'Bob: old', '222')
            history = [{'role': 'assistant', 'content': 'Praxis: answer'}]
            sidecar = {'history': [[a]], 'current': [b]}
            self.assertIsNotNone(keat_live.adopt_projection(
                self.context(mid=2), history, 'Bob: old', sidecar))
            event = SimpleNamespace(
                chat_id=int(ROOT), is_private=False, chat=None,
                message=SimpleNamespace(id=2, edit_date=None,
                                        message='Bob: ambiguous edit', media=None, out=False),
                get_sender=AsyncMock())
            with patch.object(runner, '_known_forum') as known_forum:
                asyncio.run(runner.on_edited(event))
            # Legacy handling of this malformed edit remains the same immediate no-op.
            event.get_sender.assert_not_awaited()
            known_forum.assert_not_called()
            # KEAT nevertheless fails closed before that no-op and cannot revive the
            # immutable old occurrence under the original native key.
            self.assertIsNone(keat_live.adopt_projection(
                self.context(mid=2), history, 'Bob: old', sidecar))
            self.assertIsNone(self.capture(2, 'user', 'Bob: old', '222'))

    def test_delete_routes_exact_group_invalidation_and_old_occurrence_stays_revoked(self):
        with patch.dict(os.environ, self.env, clear=False):
            a = self.capture(1, 'assistant', 'Praxis: answer', '999')
            b = self.capture(2, 'user', 'Bob: old', '222')
            history = [{'role': 'assistant', 'content': 'Praxis: answer'}]
            sidecar = {'history': [[a]], 'current': [b]}
            self.assertIsNotNone(keat_live.adopt_projection(
                self.context(mid=2), history, 'Bob: old', sidecar))
            with patch.object(runner, '_group_archive_enabled', return_value=False):
                asyncio.run(runner.on_deleted(
                    SimpleNamespace(chat_id=int(ROOT), deleted_ids=[2])))
            self.assertIsNone(keat_live.adopt_projection(
                self.context(mid=2), history, 'Bob: old', sidecar))
            self.assertIsNone(self.capture(2, 'user', 'Bob: old', '222'))

    def test_root_frame_enablement_requires_exact_capture_readiness(self):
        ctx = self.context()
        frame_env = dict(self.env, PRAXIS_FRAME_V6='serve')
        with patch.dict(os.environ, frame_env, clear=True):
            self.assertTrue(frame_serve.enabled(ctx))
        missing = dict(frame_env)
        missing.pop('PRAXIS_KEAT_CAPTURE_POLICY')
        with patch.dict(os.environ, missing, clear=True):
            self.assertFalse(frame_serve.enabled(ctx))
        bad = json.loads(self.policy.read_bytes())
        bad['enrollments'][0]['capture']['audience'] = ['group:wrong']
        self.policy.write_text(json.dumps(bad), encoding='utf-8')
        with patch.dict(os.environ, frame_env, clear=True):
            self.assertFalse(frame_serve.enabled(ctx))

    def test_group_principal_accepts_only_exact_user_entity(self):
        user = User(id=222, first_name='Bob')
        self.assertEqual(runner._keat_group_user_principal(user, 222), 222)
        self.assertIsNone(runner._keat_group_user_principal(user, 223))
        self.assertIsNone(runner._keat_group_user_principal(user, True))
        self.assertIsNone(runner._keat_group_user_principal(
            Channel(id=222, title='Anonymous', photo=None, date=None), 222))
        self.assertIsNone(runner._keat_group_user_principal(None, 222))

    def test_padded_exact_root_edit_retires_every_root_projection(self):
        with patch.dict(os.environ, self.env, clear=False):
            first = self.capture(1, 'user', 'Bob: first', '222')
            second = self.capture(2, 'user', 'Alice: second', '333')
            self.assertIsNotNone(keat_live.adopt_projection(
                self.context('222', 1), [], 'Bob: first',
                {'history': [], 'current': [first]}))
            self.assertIsNotNone(keat_live.adopt_projection(
                self.context('333', 2), [], 'Alice: second',
                {'history': [], 'current': [second]}))

            asyncio.run(runner.on_edited(SimpleNamespace(
                chat_id=int(ROOT), is_private=False,
                message=SimpleNamespace(id=' 2 '))))

            # A padded coordinate is not authority to select occurrence 2. The
            # exact-root adapter must retire every projection, including an
            # unrelated occurrence whose coordinate was otherwise canonical.
            self.assertIsNone(keat_live.adopt_projection(
                self.context('222', 1), [], 'Bob: first',
                {'history': [], 'current': [first]}))
            self.assertIsNone(keat_live.adopt_projection(
                self.context('333', 2), [], 'Alice: second',
                {'history': [], 'current': [second]}))

    def test_canonical_exact_root_edit_retires_only_named_occurrence(self):
        with patch.dict(os.environ, self.env, clear=False):
            first = self.capture(1, 'user', 'Bob: first', '222')
            second = self.capture(2, 'user', 'Alice: second', '333')

            asyncio.run(runner.on_edited(SimpleNamespace(
                chat_id=int(ROOT), is_private=False,
                message=SimpleNamespace(id=2, edit_date=None))))

            self.assertIsNotNone(keat_live.adopt_projection(
                self.context('222', 1), [], 'Bob: first',
                {'history': [], 'current': [first]}))
            self.assertIsNone(keat_live.adopt_projection(
                self.context('333', 2), [], 'Alice: second',
                {'history': [], 'current': [second]}))

    def test_ambiguous_exact_root_edit_retires_root_projection(self):
        with patch.dict(os.environ, self.env, clear=False):
            history = []
            sidecar = {'history': [], 'current': [self.capture(1, 'user', 'Bob: old', '222')]}
            self.assertIsNotNone(keat_live.adopt_projection(
                self.context(mid=1), history, 'Bob: old', sidecar))

            asyncio.run(runner.on_edited(SimpleNamespace(
                chat_id=int(ROOT), is_private=False,
                message=SimpleNamespace(id=True))))

            self.assertIsNone(keat_live.adopt_projection(
                self.context(mid=1), history, 'Bob: old', sidecar))
            self.assertIsNone(self.capture(2, 'user', 'Alice: delayed', '333'))

    def test_malformed_non_root_edit_remains_safe_noop(self):
        events = [SimpleNamespace(chat_id=-1001240718804, is_private=False),
                  SimpleNamespace(chat_id=-1001240718804, is_private=False,
                                  message=SimpleNamespace(id=True))]
        with patch.object(keat_live, 'invalidate_native') as invalidate:
            for event in events:
                asyncio.run(runner.on_edited(event))
        invalidate.assert_not_called()

    def test_root_frame_falls_back_to_complete_legacy_request_without_valid_capture(self):
        live_system = [{'type': 'text', 'text': 'LIVE BYTE EXACT',
                        'cache_control': {'type': 'ephemeral'}}]
        legacy_messages = [
            {'role': 'user', 'content': 'uncaptured legacy prefix'},
            {'role': 'assistant', 'content': 'legacy answer'},
            {'role': 'user', 'content': 'current root message'},
        ]
        legacy_tools = [{'name': 'shell', 'input_schema': {'type': 'object'}}]
        response = SimpleNamespace(blocks=[], text='ok', stop_reason='end_turn')
        ctx = self.context()
        frame_env = dict(self.env, PRAXIS_FRAME_V6='serve')
        original_policy = self.policy.read_bytes()

        def unavailable_state(kind):
            if kind == 'absent':
                env = dict(frame_env)
                env.pop('PRAXIS_KEAT_CAPTURE_POLICY')
            else:
                bad = copy.deepcopy(json.loads(original_policy))
                bad['enrollments'][0]['capture']['audience'] = ['group:wrong']
                self.policy.write_text(json.dumps(bad), encoding='utf-8')
                env = frame_env
            with patch.dict(os.environ, env, clear=True):
                return keat_live.adopt_projection(
                    ctx, [], 'current root message', {'history': [], 'current': []})

        for kind in ('absent', 'invalid'):
            with self.subTest(capture_policy=kind):
                self.policy.write_bytes(original_policy)
                state = unavailable_state(kind)
                self.assertIsNone(state)
                messages = copy.deepcopy(legacy_messages)
                tools = copy.deepcopy(legacy_tools)
                expected_bytes = json.dumps(
                    {'system': live_system, 'messages': messages, 'tools': tools},
                    ensure_ascii=False, separators=(',', ':')).encode()
                store = SimpleNamespace(store_result=lambda *args, **kwargs: None)
                candidate = 'K\n\n---\nK' + frame_serve.frame_shadow._SEP_E + ' economical'
                with patch.dict(os.environ, frame_env, clear=True), \
                     patch.object(frame_serve.frame_shadow, '_read', return_value='K'), \
                     patch.object(frame_serve.frame_measure, 'assemble',
                                  return_value={'system': candidate}), \
                     patch.object(agent.run_context, 'current_run',
                                  return_value=SimpleNamespace(run_id='r')), \
                     patch.object(agent, '_runs', return_value=store), \
                     patch.object(agent, '_run_status_gate'), \
                     patch.object(agent, '_run_event_strict'), \
                     patch.object(agent.llm, 'chat', return_value=response) as chat, \
                     frame_serve.bind(system=live_system, ctx=ctx, dynamic='dynamic'), \
                     keat_live.bind_turn(state, messages):
                    agent._model_call(live_system, messages, tools)
                sent = chat.call_args.kwargs
                self.assertIs(sent['system'], live_system)
                self.assertIs(sent['messages'], messages)
                self.assertIs(sent['tools'], tools)
                actual_bytes = json.dumps(
                    {'system': sent['system'], 'messages': sent['messages'],
                     'tools': sent['tools']}, ensure_ascii=False,
                    separators=(',', ':')).encode()
                self.assertEqual(actual_bytes, expected_bytes)

    def test_group_outgoing_fallback_is_same_aggregate_legacy_record_as_adjacent_group(self):
        off = dict(self.env, PRAXIS_KEAT='off')
        records = {}
        for chat_id in (ROOT, '-1001240718804'):
            calls = []
            with patch.dict(os.environ, off, clear=False), \
                    patch.object(runner, '_under_tests', return_value=False), \
                    patch.object(runner, '_persisted_life_sources', set()), \
                    patch.object(runner, '_buf', defaultdict(lambda: deque(maxlen=60))), \
                    patch.object(runner, '_buffer_message_ids', defaultdict(deque)), \
                    patch.object(runner.bufstore, 'meta_update'), \
                    patch.object(runner.memory_life, 'record_message',
                                 side_effect=lambda *args, **kwargs: calls.append((args, kwargs))), \
                    patch.object(keat_live, 'capture_ingress') as capture:
                runner._persist_sent_reply(chat_id, ['one ', 'two'], ['31', '32'], is_dm=False)
            capture.assert_not_called()
            self.assertEqual(len(calls), 1)
            records[chat_id] = calls[0]
        canonical, adjacent = records[ROOT], records['-1001240718804']
        self.assertEqual(canonical[0][1:], adjacent[0][1:])
        for _args, kwargs in (canonical, adjacent):
            self.assertEqual(kwargs['source_id'], '31,32')
            self.assertEqual(kwargs['actor'], 'Praxis')
            self.assertEqual(kwargs['direction'], 'out')
            self.assertIsNone(kwargs['keat_occurrence'])
            self.assertIsNone(kwargs['logical_send'])
        self.assertEqual(canonical[0][1], 'Praxis: one two')
        self.assertFalse((Path(self.tmp.name) / 'capture').exists())

    def test_group_native_outgoing_requires_full_readiness_not_chat_match(self):
        variants = [
            dict(self.env),
            dict(self.env, PRAXIS_KEAT='off'),
            dict(self.env, PRAXIS_KEAT_CAPTURE='off'),
            dict(self.env, PRAXIS_KEAT_PAIRS='[["group","-1001240718804"]]'),
        ]
        expected = [True, False, False, False]
        for env, wanted in zip(variants, expected):
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(runner._group_native_persistence_enabled(ROOT), wanted)

    def test_readiness_exact_pair_only_and_no_legacy_group_selector(self):
        with patch.dict(os.environ, self.env, clear=False):
            ready = keat_readiness.receipt(dict(os.environ))
            self.assertTrue(ready['serve_ready'])
        legacy = dict(self.env)
        legacy.pop('PRAXIS_KEAT_PAIRS')
        legacy.update(PRAXIS_KEAT_MODES='group', PRAXIS_KEAT_STREAMS=ROOT)
        self.assertFalse(keat_runtime.staged('group', ROOT, legacy))
        self.assertFalse(keat_readiness.receipt(legacy)['serve_ready'])
        wrong = dict(self.env, PRAXIS_KEAT_PAIRS='[["group","-1001240718804"]]')
        self.assertFalse(keat_readiness.receipt(wrong)['serve_ready'])


if __name__ == '__main__':
    unittest.main()
