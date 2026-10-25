import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import keat_live as live
from keat_capture import NarrowingReader


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'policy.json'
        self.policy = dict(schema='keat.capture-policy.v1', namespace='test', root=self.tmp.name,
                           enrollments=[dict(stream='1', mode='owner', capture=dict(
                               issuer='explicit-admin', policy_revision='p1', audience=['owner'],
                               transfer='none', presence_hidden=False))])
        self.path.write_text(json.dumps(self.policy))
        self.env = patch.dict(os.environ, {'PRAXIS_KEAT_CAPTURE': 'on',
            'PRAXIS_KEAT_CAPTURE_POLICY': str(self.path), 'PRAXIS_KEAT': 'serve',
            'PRAXIS_KEAT_MODES': 'owner', 'PRAXIS_KEAT_STREAMS': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.ctx = SimpleNamespace(chat_id=1, owner=True, is_dm=True)

    def select(self, state, messages):
        with live.bind_turn(state, messages):
            provider = live.prepare_provider_messages('system', messages, [])
            return live.select('system', provider, [], 'run1', 'call1')

    def test_capture_checkpoint_reopen_and_revoke(self):
        state = live.capture_turn(self.ctx, 'new', [])
        self.assertIsNotNone(state)
        tape = [dict(role='user', content='rendered current')]
        before = copy.deepcopy(tape)
        self.assertIsNotNone(self.select(state, tape))
        self.assertIsNotNone(self.select(state, tape))
        self.assertEqual(tape, before)
        grant = state.ledger.snapshot()['receipts'][0]['capture']['grant']
        NarrowingReader(state.ledger.directory, 'test').narrow(grant)
        self.assertIsNone(self.select(state, tape))

    def test_legacy_never_recaptured(self):
        state = live.capture_turn(self.ctx, 'new', [dict(role='user', content='legacy')])
        self.assertEqual(len(state.ledger.snapshot()['receipts']), 1)
        self.assertIsNone(self.select(state, [dict(role='user', content='legacy'), dict(role='user', content='new')]))

    def test_original_refs_checked_and_ordered(self):
        first = live.capture_turn(self.ctx, 'first', [])
        old = dict(role='user', content='first', _keat_occurrence=first.current_ref)
        output = live.capture_output(first, dict(role='assistant', content='answer'))
        state = live.capture_turn(self.ctx, 'second', [old, output])
        tape = [dict(role='user', content='> first'), dict(role='assistant', content='answer'),
                dict(role='user', content='> second')]
        self.assertIsNotNone(self.select(state, tape))
        state.history[0]['content'] = 'edited'
        self.assertIsNone(self.select(state, tape))
        state.history = [output, old]
        self.assertIsNone(self.select(state, tape))

    def test_tool_loop_mutation_and_copy_fallback(self):
        state = live.capture_turn(self.ctx, 'new', [])
        tape = [dict(role='user', content='new')]
        with live.bind_turn(state, tape):
            provider = live.prepare_provider_messages('s', tape, [])
            self.assertIsNone(live.select('s', copy.deepcopy(provider), [], 'r', 'c'))
            tape.append(dict(role='assistant', content='tool call'))
            provider = live.prepare_provider_messages('s', tape, [])
            self.assertIsNone(live.select('s', provider, [], 'r', 'c'))
        self.assertIsNone(live.select('s', tape, [], 'r', 'c'))

    def test_failed_select_retains_bounded_specific_reason(self):
        state = live.capture_turn(self.ctx, 'new', [])
        tape = [dict(role='user', content='new')]
        with live.bind_turn(state, tape):
            provider = live.prepare_provider_messages('s', tape, [])
            self.assertIsNone(live.select('s', copy.deepcopy(provider), [], 'r', 'c'))
            self.assertEqual(live.activation_reason(), 'model_tape_changed')
        with patch.dict(os.environ, {'PRAXIS_KEAT': 'off'}), live.bind_turn(state, tape):
            provider = live.prepare_provider_messages('s', tape, [])
            self.assertIsNone(live.select('s', provider, [], 'r', 'c'))
            self.assertEqual(live.activation_reason(), 'not_staged')

    def test_failed_capture_retains_content_free_reason(self):
        with patch.dict(os.environ, {'PRAXIS_KEAT_CAPTURE': 'off'}):
            self.assertIsNone(live.capture_turn(self.ctx, 'private input', []))
            self.assertEqual(live.activation_reason(), 'capture_disabled')
        self.ctx.chat_id = 2
        self.assertIsNone(live.capture_turn(self.ctx, 'private input', []))
        self.assertEqual(live.activation_reason(), 'capture_policy_invalid')
        self.assertNotIn('private input', live.activation_reason())

    def test_default_off_and_explicit_policy(self):
        with patch.dict(os.environ, {'PRAXIS_KEAT_CAPTURE': ''}):
            self.assertIsNone(live.capture_turn(self.ctx, 'new', []))
        self.ctx.chat_id = 2
        self.assertIsNone(live.capture_turn(self.ctx, 'new', []))
        self.assertFalse((Path(self.tmp.name) / 'capture').exists())

    def test_history_entry_reuses_user_and_issues_output(self):
        state = live.capture_turn(self.ctx, 'new', [])
        user = live.history_entry(state, 'user', 'new')
        self.assertEqual(user['_keat_occurrence'], state.current_ref)
        self.assertEqual(len(state.ledger.snapshot()['receipts']), 1)
        self.assertNotIn('_keat_occurrence', live.history_entry(state, 'user', 'edited'))
        answer = live.history_entry(state, 'assistant', 'reply')
        self.assertIn('_keat_occurrence', answer)
        self.assertEqual(len(state.ledger.snapshot()['receipts']), 2)
        self.assertEqual(live.history_entry(None, 'user', 'fallback'),
                         dict(role='user', content='fallback'))

    def test_sidecar_strip_preserves_media_and_policy_change_fails(self):
        message = dict(role='user', content=[dict(type='image', source={'data': 'AA=='})],
                       _keat_occurrence={'event': 'x'})
        clean = live.strip_occurrence(message)
        self.assertIs(clean['content'], message['content'])
        self.assertIn('_keat_occurrence', message)
        state = live.capture_turn(self.ctx, 'new', [])
        self.path.write_text('{}')
        self.assertIsNone(self.select(state, [dict(role='user', content='new')]))

    def test_receipt_tool_loop_and_resume_preserve_epoch(self):
        first = live.capture_turn(self.ctx, 'old', [])
        old = live.history_entry(first, 'user', 'old')
        state = live.capture_turn(self.ctx, 'new', [old])
        tape = [dict(role='user', content='old rendered'), dict(role='user', content='new')]
        with live.bind_turn(state, tape):
            provider = live.prepare_provider_messages('system', tape, [])
            first_receipt = live.select('system', provider, [], 'run', 'c1')
            self.assertEqual(first_receipt['status'], 'served')
            self.assertNotIn('source', first_receipt)
            self.assertNotIn('historical_a', first_receipt)
            additions = [dict(role='assistant', content=[dict(type='tool_use', id='t', name='x', input={})])]
            self.assertTrue(live.capture_appended(tape, additions))
            tape.extend(additions)
            provider = live.prepare_provider_messages('system', tape, [])
            second = live.select('system', provider, [], 'run', 'c2')
            self.assertEqual(first_receipt['epoch'], second['epoch'])
            self.assertNotEqual(first_receipt['checkpoint'], second['checkpoint'])
        saved = copy.deepcopy(dict(system='system', messages=tape, tools=[], keat=second))
        with live.bind_resume(saved, 'run', 'c2') as resumed:
            self.assertIsNotNone(resumed)
            self.assertEqual(resumed.historical_count, 1)
            canonical = saved['messages']
            additions = [dict(role='user', content=[dict(type='tool_result', tool_use_id='t', content='ok')])]
            self.assertTrue(live.capture_appended(canonical, additions))
            canonical.extend(additions)
            provider = live.prepare_provider_messages('system', canonical, [])
            third = live.select('system', provider, [], 'run', 'c3')
            self.assertEqual(third['epoch'], second['epoch'])
            obj = live._store(resumed).epochs._load(third['checkpoint'])
            source = json.loads(obj['source'])
            self.assertEqual(source['historical_count'], 1)
            self.assertEqual(source['messages'][0], tape[0])
        # The older checkpoint cannot fork a resumed lineage.
        with live.bind_resume(dict(system='system', messages=tape, tools=[], keat=second), 'run', 'c2') as stale:
            self.assertIsNone(stale)

    def test_resume_tamper_revoke_and_identity_fail_closed(self):
        state = live.capture_turn(self.ctx, 'new', [])
        tape = [dict(role='user', content='new')]
        receipt = self.select(state, tape)
        saved = dict(system='system', messages=tape, tools=[], keat=receipt)
        with live.bind_resume(saved, 'other', 'call1') as result:
            self.assertIsNone(result)
        tampered = copy.deepcopy(saved)
        tampered['messages'].append(dict(role='user', content='unissued'))
        with live.bind_resume(tampered, 'run1', 'call1') as result:
            self.assertIsNone(result)
        with live.bind_resume(saved, 'run1', 'call1') as result:
            self.assertIsNotNone(result)
        grant = state.ledger.snapshot()['receipts'][0]['capture']['grant']
        NarrowingReader(state.ledger.directory, 'test').narrow(grant)
        with live.bind_resume(saved, 'run1', 'call1') as result:
            self.assertIsNone(result)

    def test_ordinary_dm_native_delete_and_peerless_barriers(self):
        policy = copy.deepcopy(self.policy)
        policy['enrollments'][0]['mode'] = 'dm'
        policy['enrollments'][0]['capture']['audience'] = ['dm:1']
        self.path.write_text(json.dumps(policy))
        ctx = SimpleNamespace(chat_id=1, room_chat_id=1, is_dm=True,
                              owner=False, owner_audience=False)
        with patch.dict(os.environ, {'PRAXIS_KEAT_PAIRS': '[["dm","1"]]'}):
            for mid, peerless in ((11, False), (12, True)):
                ref = live.capture_ingress(1, is_dm=True,
                    payload=dict(role='user', content='new'), source_id=mid, direction='in')
                state = live.adopt_projection(ctx, [], 'new', dict(history=[], current=[ref]))
                self.assertIsNotNone(self.select(state, [dict(role='user', content='new')]))
                if peerless:
                    live.invalidate_peerless_deletion([mid])
                else:
                    live.invalidate_native(1, mid)
                self.assertIsNone(self.select(state, [dict(role='user', content='new')]))
                self.assertIsNone(live.capture_ingress(1, is_dm=True,
                    payload=dict(role='user', content='new'), source_id=mid, direction='in'))

    def test_native_owner_enrollment_cannot_adopt_ordinary_context(self):
        ref = live.capture_ingress(1, is_dm=True, payload=dict(role='user', content='new'),
                                   source_id=12, direction='in')
        self.assertIsNotNone(ref)
        ctx = SimpleNamespace(chat_id=1, room_chat_id=1, is_dm=True,
                              owner=False, owner_audience=False)
        self.assertIsNone(live.adopt_projection(ctx, [], 'new',
                            dict(history=[], current=[ref])))

    def test_ordinary_dm_policy_and_context_fail_closed(self):
        policy = copy.deepcopy(self.policy)
        policy['enrollments'][0].update(mode='dm')
        policy['enrollments'][0]['capture']['audience'] = ['dm:1']
        self.path.write_text(json.dumps(policy))
        ctx = SimpleNamespace(chat_id=1, room_chat_id=1, is_dm=True,
                              owner=False, owner_audience=False)
        self.assertIsNotNone(live.capture_turn(ctx, 'new', []))
        for change in (dict(owner=True), dict(owner_audience=True),
                       dict(room_chat_id=2), dict(is_dm=False),
                       dict(hide_identity_load=True), dict(praxis_self=True)):
            with self.subTest(context=change):
                invalid = SimpleNamespace(**dict(vars(ctx), **change))
                self.assertIsNone(live.capture_turn(invalid, 'new', []))
        for change in (dict(audience=['owner']), dict(audience=['dm:2']),
                       dict(audience=['dm:1', 'dm:2']), dict(transfer='allowed'),
                       dict(presence_hidden=True)):
            with self.subTest(policy=change):
                bad = copy.deepcopy(policy)
                bad['enrollments'][0]['capture'].update(change)
                self.path.write_text(json.dumps(bad))
                self.assertIsNone(live.capture_turn(ctx, 'new', []))
                self.assertIsNone(live.capture_ingress(1, is_dm=True,
                    payload=dict(role='user', content='new'), source_id=12, direction='in'))

    def test_group_adapter_remains_legacy_even_when_enrolled(self):
        policy = copy.deepcopy(self.policy)
        policy['enrollments'][0]['mode'] = 'group'
        self.path.write_text(json.dumps(policy))
        self.assertIsNone(live.capture_turn(self.ctx, 'new', [], mode='group'))

    def test_explicit_modes_require_enrollment_and_staging(self):
        for mode in ('owner', 'wake', 'window'):
            policy = copy.deepcopy(self.policy)
            policy['enrollments'][0]['mode'] = mode
            self.path.write_text(json.dumps(policy))
            with patch.dict(os.environ, {'PRAXIS_KEAT_MODES': mode}):
                state = live.capture_turn(self.ctx, 'new', [], mode=mode)
                self.assertIsNotNone(state)
                self.assertIsNotNone(self.select(state, [dict(role='user', content='new')]))
            with patch.dict(os.environ, {'PRAXIS_KEAT': ''}):
                self.assertIsNone(self.select(state, [dict(role='user', content='new')]))


if __name__ == '__main__':
    unittest.main()

class NativeInvalidationTests(LiveTests):
    def native_checkpoint(self):
        incoming = live.capture_ingress('1', is_dm=True, source_id=17, direction='in',
                                       payload=dict(role='user', content='old'))
        outgoing = live.capture_ingress('1', is_dm=True, source_id=17, direction='out',
                                       payload=dict(role='assistant', content='answer'))
        state = live.capture_turn(self.ctx, 'new', [
            dict(role='user', content='old', _keat_occurrence=incoming),
            dict(role='assistant', content='answer', _keat_occurrence=outgoing)])
        tape = [dict(role='user', content='old'), dict(role='assistant', content='answer'),
                dict(role='user', content='new')]
        receipt = self.select(state, tape)
        self.assertIsNotNone(receipt)
        return state, tape, dict(system='system', messages=tape, tools=[], keat=receipt)

    def assert_blocked(self, state, tape, saved):
        before = copy.deepcopy(tape)
        self.assertIsNone(self.select(state, tape))
        with live.bind_resume(saved, 'run1', 'call1') as reopened:
            self.assertIsNone(reopened)
        self.assertEqual(before, tape)

    def test_both_directions_revoked_reopen_and_delayed_original(self):
        state, tape, saved = self.native_checkpoint()
        live.invalidate_native('1', 17)
        self.assert_blocked(state, tape, saved)
        current = state.ledger._state()[3]
        for r in state.ledger.snapshot()['receipts'][:2]:
            self.assertIsNone(current[r['capture']['grant']])
        live.invalidate_native('1', 17)  # replay idempotent
        self.assertIsNone(live.capture_ingress('1', is_dm=True, source_id=17,
                         direction='out', payload=dict(role='assistant', content='answer')))

    def test_pending_survives_new_reader_and_duplicate_retry(self):
        state, tape, saved = self.native_checkpoint()
        with patch.object(NarrowingReader, 'narrow', side_effect=OSError('transient')):
            with self.assertRaises(OSError):
                live.invalidate_native('1', 17)
        # New instances and resume perform canonical barrier reads, no process cache.
        from keat_capture import CaptureLedger
        state.ledger = CaptureLedger(state.ledger.directory, state.ledger.namespace)
        self.assertFalse(state.ledger._state()[5]['telegram:1:message:17:'])
        self.assert_blocked(state, tape, saved)
        live.invalidate_native('1', 17)
        self.assertTrue(state.ledger._state()[5]['telegram:1:message:17:'])
        self.assert_blocked(state, tape, saved)

    def test_real_peerless_deletion_denies_candidates_without_chat_guess(self):
        import asyncio
        import mtproto_runner as runner
        from telethon.events import MessageDeleted
        from telethon.tl.types import UpdateDeleteMessages
        event = MessageDeleted.build(UpdateDeleteMessages(messages=[17], pts=1, pts_count=1))
        state, tape, saved = self.native_checkpoint()
        with patch.object(runner.group_context, 'append_deletion_retry') as archive:
            with patch.object(NarrowingReader, 'narrow', side_effect=OSError('transient')):
                with self.assertRaises(OSError):
                    asyncio.run(runner.on_deleted(event))
            self.assert_blocked(state, tape, saved)
            asyncio.run(runner.on_deleted(event))
            archive.assert_not_called()
        self.assertNotIn('', state.ledger._state()[5])
        self.assertTrue(state.ledger._state()[5]['telegram:1:message:17:'])
        self.assert_blocked(state, tape, saved)
        self.assertIsNone(live.capture_turn(self.ctx, 'future', []))

    def test_real_basic_group_peerless_preserves_unrelated_native_capture(self):
        import asyncio
        import mtproto_runner as runner
        from telethon.events import MessageDeleted
        from telethon.tl.types import UpdateDeleteMessages
        from keat_capture import CaptureLedger
        from keat_candidate import _digest
        ref = live.capture_ingress('1', is_dm=True, source_id=17,
            direction='in', payload=dict(role='user', content='owner'))
        unrelated = live.capture_turn(self.ctx, 'current', [
            dict(role='user', content='owner', _keat_occurrence=ref)])
        unrelated_tape = [dict(role='user', content='owner'),
                          dict(role='user', content='current')]
        unrelated_saved = dict(system='system', messages=unrelated_tape, tools=[],
                               keat=self.select(unrelated, unrelated_tape))
        event = MessageDeleted.build(UpdateDeleteMessages(messages=[99], pts=1, pts_count=1))
        self.assertIsNone(event.chat_id)  # identical wire form for basic group / DM
        with patch.object(runner.group_context, 'append_deletion_retry') as archive:
            asyncio.run(runner.on_deleted(event))
            archive.assert_not_called()
        ledger = CaptureLedger(Path(self.tmp.name) / 'capture' / _digest('test'), 'test')
        self.assertIsNotNone(ledger._state()[3]['grant:' + ref['event']])
        self.assertNotIn('', ledger._state()[5])
        self.assertIsNotNone(self.select(unrelated, unrelated_tape))
        with live.bind_resume(unrelated_saved, 'run1', 'call1') as reopened:
            self.assertIsNotNone(reopened)
        self.assertIsNotNone(live.capture_ingress('1', is_dm=True, source_id=100,
            direction='in', payload=dict(role='user', content='future')))
        # Missing original remains denied across a fresh ledger reader, without
        # asserting that message 99 belonged to this chat.
        self.assertIsNone(live.capture_ingress('1', is_dm=True, source_id=99,
            direction='in', payload=dict(role='user', content='delayed')))
        self.assertIn('peerless_candidate', (ledger.directory / 'capture.jsonl').read_text())

    def test_real_known_peer_dm_revokes_with_archive_disabled(self):
        import asyncio
        import mtproto_runner as runner
        state, tape, saved = self.native_checkpoint()
        with patch.object(runner, '_group_archive_enabled', return_value=False):
            asyncio.run(runner.on_deleted(SimpleNamespace(chat_id=1, deleted_ids=[17])))
        self.assert_blocked(state, tape, saved)
        self.assertNotIn('', state.ledger._state()[5])

    def test_real_channel_deletion_does_not_revoke_owner(self):
        import asyncio
        import mtproto_runner as runner
        from telethon.events import MessageDeleted
        from telethon.tl.types import UpdateDeleteChannelMessages
        state, tape, saved = self.native_checkpoint()
        event = MessageDeleted.build(UpdateDeleteChannelMessages(
            channel_id=42, messages=[17], pts=1, pts_count=1))
        with patch.object(runner, '_group_archive_enabled', return_value=False):
            asyncio.run(runner.on_deleted(event))
        self.assertIsNotNone(self.select(state, tape))
        self.assertEqual(state.ledger._state()[5], {})

    def test_real_outgoing_edit_revokes_before_self_short_circuit(self):
        import asyncio
        import datetime
        from unittest.mock import AsyncMock
        import mtproto_runner as runner
        state, tape, saved = self.native_checkpoint()
        event = SimpleNamespace(chat_id=1, is_private=True,
            message=SimpleNamespace(id=17, edit_date=datetime.datetime.now(),
                                    message='edited', media=None),
            get_sender=AsyncMock(return_value=SimpleNamespace(is_self=True)))
        asyncio.run(runner.on_edited(event))
        self.assert_blocked(state, tape, saved)
        asyncio.run(runner.on_edited(event))
        self.assert_blocked(state, tape, saved)

    def test_barrier_write_failure_precedes_revision_and_replay(self):
        import asyncio
        import datetime
        from unittest.mock import AsyncMock
        import mtproto_runner as runner
        from keat_capture import CaptureLedger
        state, tape, saved = self.native_checkpoint()
        event = SimpleNamespace(chat_id=1, is_private=True,
            message=SimpleNamespace(id=17, edit_date=datetime.datetime.now(),
                                    message='edited', media=None),
            get_sender=AsyncMock(return_value=SimpleNamespace(is_self=True)))
        with patch.object(CaptureLedger, '_append', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                asyncio.run(runner.on_edited(event))
        event.get_sender.assert_not_called()  # no revision effects / acknowledgement
        asyncio.run(runner.on_edited(event))
        self.assert_blocked(state, tape, saved)

    def test_partial_revoke_failure_retry(self):
        state, tape, saved = self.native_checkpoint()
        original = NarrowingReader.narrow
        calls = []
        def fail_second(reader, grant, **kw):
            calls.append(grant)
            if len(calls) == 2:
                raise OSError('interrupted')
            return original(reader, grant, **kw)
        with patch.object(NarrowingReader, 'narrow', fail_second):
            with self.assertRaises(OSError):
                live.invalidate_native('1', 17)
        self.assert_blocked(state, tape, saved)
        live.invalidate_native('1', 17)
        self.assert_blocked(state, tape, saved)

    def test_dirty_namespace_blocks_unrelated_new_selection(self):
        state, tape, saved = self.native_checkpoint()
        with patch.object(NarrowingReader, 'narrow', side_effect=OSError('transient')):
            with self.assertRaises(OSError):
                live.invalidate_native('1', 17)
        fresh = live.capture_turn(self.ctx, 'unrelated', [])
        self.assertIsNotNone(fresh)
        self.assertIsNone(self.select(fresh, [dict(role='user', content='unrelated')]))
        live.invalidate_native('1', 17)
        self.assertIsNotNone(self.select(fresh, [dict(role='user', content='unrelated')]))
