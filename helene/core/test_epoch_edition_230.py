"""Edition epoch consumer checks, in addition to all inherited source scenarios.

Real imported runner -> durable envelope -> voice -> provider adapter; only SDK,
transport and volatile/private external observations are synthetic. No Telegram
login, real model or public network. Run through praxis_test.py.
"""
from __future__ import annotations
import asyncio
import copy
import datetime
import json
from collections import defaultdict
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import agent
import frame_epoch as fe
import frame_layout
import group_context as gc
import llm
import memory_life as ml
import mtproto_runner as runner
from run_manager import RunManager
import test_epoch_revision_receipts_2010 as receipt_fixtures
ROOM = receipt_fixtures.ROOM
from test_group_wake_snapshot import _Client


class EditionEpoch(unittest.TestCase):
    def setUp(self):
        self.fixture = receipt_fixtures.IncomingEpochReceipts()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.base = fe.STORE.parent
        self.calls = []
        self.enterContext(mock.patch.object(llm, 'CONFIG_PATH', self.base/'llm.json'))
        self.enterContext(mock.patch.object(llm, 'JOURNAL_DIR', self.base/'llm-journal'))
        self.enterContext(mock.patch.dict(llm._CACHE, {'mtime': None, 'cfg': None}))
        config = llm._from_env()
        config['frameworks']['anthropic']['api_key'] = 'offline-synthetic-key'
        config['roles']['voice'].update(framework='anthropic', model='offline-synthetic-model')
        llm.save_config(config)
        def create(**kwargs):
            self.calls.append(copy.deepcopy(kwargs))
            return SimpleNamespace(stop_reason='end_turn', model='offline-synthetic-model',
                content=[SimpleNamespace(type='text', text='[молчу]')],
                usage=SimpleNamespace(input_tokens=7, output_tokens=2))
        llm.use_test_client(SimpleNamespace(messages=SimpleNamespace(create=create)), 'anthropic')
        self.addCleanup(llm.clear_test_clients)
        self.enterContext(mock.patch.object(agent, '_persona_text', return_value='synthetic persona'))
        self.enterContext(mock.patch.object(agent, '_active_desires_block', return_value=''))
        self.enterContext(mock.patch.object(agent, '_work_loop_continue', return_value=False))
        self.enterContext(mock.patch.object(agent, '_arm_private_floor', return_value=0))
        self.enterContext(mock.patch.object(agent, '_pre_shell_dirt', return_value=set()))
        self.enterContext(mock.patch.object(agent, '_autocommit_self_edit'))
        self.enterContext(mock.patch.object(agent, '_runs', return_value=RunManager(self.base)))
        self.enterContext(mock.patch.object(agent.work_loop, 'reply_hand_enabled', return_value=True))
        original = agent._voice_impl
        def restricted(*args, **kwargs):
            kwargs.update(tools_override=[], no_tools=True, max_iters=1)
            return original(*args, **kwargs)
        self.enterContext(mock.patch.object(agent, '_voice_impl', side_effect=restricted))

    def wake(self, mid, text, *, owner=True):
        return runner._group_trigger_snapshot(ROOM, mid=mid, message_ts=1,
            kind='mention', addressed=True, query=text, name='Alice', sender_id=10,
            is_owner=owner, known=True, family=False)

    def dispatch(self, wake):
        moving_guard = (nullcontext() if getattr(self, 'allow_moving_snapshot', False) else
                        mock.patch.object(runner, '_group_context_frozen', side_effect=AssertionError('moving tape')))
        runner._group_wakes[ROOM] = wake
        async def run():
            with mock.patch.object(runner, '_ONE_MIND', asyncio.Lock()):
                await runner._run_pass(ROOM)
                await asyncio.sleep(0)
        with mock.patch.object(runner, 'client', _Client()), \
             mock.patch.object(runner, '_last_pass', defaultdict(float)), \
             mock.patch.object(runner, '_passing', set()), \
             mock.patch.object(runner, '_missed', {}), \
             mock.patch.object(runner, '_cooldown', return_value=0), \
             mock.patch.object(runner, '_gate_group_wake_room_mode', return_value=('normal', 'open')), \
             moving_guard:
            asyncio.run(run())
        self.assertTrue(self.calls, 'actual SDK was not called')
        return self.calls[-1]

    def test_queued_wake_actual_sdk_survives_fold_restart_and_newer_room_without_rewind(self):
        self.fixture.new(1, 'first before fold')
        ml.record_message(ROOM, 'Praxis: accepted prior answer', actor='Praxis', direction='out', source_id='2',
                          ts=datetime.datetime(2026, 10, 20, tzinfo=datetime.timezone.utc).timestamp()+1.5)
        text = 'ORIGINAL TRIGGER ' + 'x'*4300
        self.fixture.new(3, text)
        wake = self.wake(3, text)
        self.assertEqual(wake.inbound_text, text)
        self.assertEqual(len(wake.query), 4000)
        self.assertIn('envelope', wake.epoch_snapshot)
        detached = copy.deepcopy(wake.epoch_snapshot)
        self.fixture.fold('NEWER RECAP NOT FOR OLD WAKE')
        self.fixture.new(4, 'LATER TRAFFIC NOT FOR OLD WAKE')
        # Reload the durable selection as a restart would; the old wake stays detached.
        fe.reset()
        newer = self.fixture.prepare()
        self.assertNotEqual(newer['boundary'], detached['boundary'])
        saved = copy.deepcopy(fe.load(ROOM))
        wire = self.dispatch(wake)
        text_wire = json.dumps(wire, ensure_ascii=False)
        self.assertIn('first before fold', text_wire)
        self.assertIn(text, text_wire)
        self.assertNotIn('NEWER RECAP NOT FOR OLD WAKE', text_wire)
        self.assertNotIn('LATER TRAFFIC NOT FOR OLD WAKE', text_wire)
        self.assertEqual(wake.epoch_snapshot, detached)
        self.assertEqual(fe.load(ROOM), saved, 'old dispatched E rewound newer durable room')

    def test_actual_sdk_uses_detached_summary_not_later_disk_policy(self):
        self.fixture.new(1, 'source to summarize')
        self.fixture.new(2, 'uncovered original trigger')
        self.fixture.fold('FROZEN RECAP')
        wake = self.wake(2, 'uncovered original trigger')
        with mock.patch.object(agent, 'read_summary', side_effect=AssertionError('later recap read')), \
             mock.patch.object(agent, 'SUMMARY_FRAME_CHARS', 1):
            wire = self.dispatch(wake)
        text_wire = json.dumps(wire, ensure_ascii=False)
        self.assertIn('FROZEN RECAP', text_wire)
        self.assertIn('uncovered original trigger', text_wire)

    def test_admission_change_cannot_reuse_owner_epoch_bytes_or_locator(self):
        self.fixture.new(1, 'public room text')
        selected = self.fixture.prepare()
        owner = {'audience':'owner', 'private_in_rooms':True, 'scope':'full'}
        public = {'audience':'chat:'+ROOM, 'private_in_rooms':False, 'scope':'chat'}
        with fe.bind(ROOM, selected['anchor'], tape=selected):
            fe.serve(ROOM, selected['anchor'], [('Эта комната','json','PRIVATE /private/locator')], admission_key=owner)
        selected2 = self.fixture.prepare()
        with fe.bind(ROOM, selected2['anchor'], tape=selected2):
            blocks, _ = fe.serve(ROOM, selected2['anchor'], [('Эта комната','json','PUBLIC')], admission_key=public)
        self.assertEqual(blocks[0][2], 'PUBLIC')
        self.assertNotIn('PRIVATE', str(blocks))
        self.assertNotIn('/private/locator', str(blocks))

    def test_atomic_refresh_keeps_old_wake_if_replaced_during_freeze(self):
        self.fixture.new(1, 'original')
        old = self.wake(1, 'original')
        runner._group_wakes[ROOM] = old
        newer = self.wake(1, 'newer same message')
        real = runner._freeze_group_wake_epoch
        def supersede(chat, value):
            result = real(chat, value)
            runner._group_wakes[chat] = newer
            return result
        with mock.patch.object(runner, '_freeze_group_wake_epoch', side_effect=supersede):
            self.assertFalse(runner._refresh_group_wake_context(ROOM, message_id=1, query='edited'))
        self.assertIs(runner._group_wakes[ROOM], newer)
        self.assertEqual(old.query, 'original')

    def test_corrupt_archive_and_cap_refuse_before_actual_sdk(self):
        self.fixture.new(1, 'retained source')
        self.fixture.prepare()
        with mock.patch.object(fe, 'max_chars', return_value=1):
            with self.assertRaises(fe.EpochUnavailable):
                self.wake(1, 'retained source')
        self.assertEqual(self.calls, [])
        with gc.archive_path(ROOM).open('ab') as stream:
            stream.write(b'{corrupt complete row}\n')
        with self.assertRaises(fe.EpochUnavailable):
            self.wake(1, 'retained source')
        self.assertEqual(self.calls, [])
        self.assertTrue(fe.load(ROOM)['tape']['gaps'])

    def test_reversed_snapshot_completion_cannot_replace_newer_addressed_wake(self):
        import threading
        self.fixture.new(1, 'older address')
        self.fixture.new(2, 'newer address')
        old_entered, release_old = threading.Event(), threading.Event()
        real = runner._freeze_group_wake_epoch
        def freeze(chat, wake):
            if wake.message_id == 1:
                old_entered.set()
                if not release_old.wait(10):
                    raise AssertionError('old snapshot barrier timeout')
            return real(chat, wake)
        def args(mid):
            return dict(mid=mid, message_ts=1, kind='mention', addressed=True,
                        query='address '+str(mid), name='Alice', sender_id=10,
                        is_owner=True, known=True, family=False)
        async def run():
            old_task = asyncio.create_task(runner._prepare_group_wake(ROOM, **args(1)))
            try:
                self.assertTrue(await asyncio.to_thread(old_entered.wait, 10))
                self.assertIsNotNone(await runner._prepare_group_wake(ROOM, **args(2)))
            finally:
                release_old.set()
            self.assertIsNone(await old_task)
        with mock.patch.object(runner, '_freeze_group_wake_epoch', side_effect=freeze):
            asyncio.run(run())
        self.assertEqual(runner._group_wakes[ROOM].message_id, 2)
        # An explicitly later admitted edit/address of a smaller native id is
        # allowed. Remote numeric/date ordering is not the admission policy.
        asyncio.run(runner._prepare_group_wake(ROOM, **dict(args(1), query='later edit')))
        self.assertEqual(runner._group_wakes[ROOM].message_id, 1)
        self.assertEqual(runner._group_wakes[ROOM].query, 'later edit')
        installed = runner._group_wakes[ROOM]
        with mock.patch.object(runner, '_freeze_group_wake_epoch', side_effect=RuntimeError('freeze failed')):
            with self.assertRaisesRegex(RuntimeError, 'freeze failed'):
                asyncio.run(runner._prepare_group_wake(ROOM, **args(2)))
        self.assertIs(runner._group_wakes[ROOM], installed)
        self.assertNotIn(ROOM, runner._group_wake_admissions)

    def test_incoming_raw_bytes_and_derived_canonical_receipt_are_distinct(self):
        raw = '\u00a0 raw trailing \u00a0 '
        self.fixture.new(1, raw)
        archived = gc.archive_occurrences(ROOM)['occurrences'][0]
        event = ml.iter_events(chat_id=ROOM, kinds={'conversation_message'})[0]
        self.assertEqual(archived['row']['text'], raw)
        self.assertEqual(gc.epoch_life_signature(archived['row']), (event['source_id'], 'in', event['text']))
        self.assertNotEqual(event['text'], 'Alice: '+raw)
        self.assertEqual(event['text'], ml.canonical_event_text('Alice: '+raw, 'in'))
        self.assertEqual(gc.archive_path(ROOM).read_bytes()[archived['offset']:archived['end']].decode('utf-8').strip(),
                         json.dumps(archived['row'], ensure_ascii=False, sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    unittest.main()
