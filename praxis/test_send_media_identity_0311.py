"""Addressed media keeps its real tool identity through delivery and recovery."""
from __future__ import annotations

import asyncio
import copy
from contextlib import ExitStack
from pathlib import Path
import types
import unittest
from unittest import mock

import agent
import mtproto_runner as runner
import run_context
import run_resume
import telegram_outbox
import test_agent_resume_runtime as fixtures
import workshop


class AddressedMediaIdentity(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.AgentResumeRuntimeTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.manager = self.fx.manager
        self.context = self.fx._create(self._testMethodName)
        self.source = self.fx.base / 'speech.ogg'
        self.source.write_bytes(b'OggS' + b'fixture-audio' * 32)
        self.outbox_root = self.fx.base / 'direct-outbox'
        self.outbox = telegram_outbox.TelegramOutbox(self.outbox_root)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(runner, '_DIRECT_OUTBOX', self.outbox))
        self.stack.enter_context(mock.patch.object(runner, '_DIRECT_OUTBOX_RECONCILED', set()))
        self.stack.enter_context(mock.patch.object(runner, 'OWNER_ID', 42))
        self.stack.enter_context(mock.patch.object(runner, '_resolve_entity', mock.AsyncMock(return_value=types.SimpleNamespace(id=42))))
        self.stack.enter_context(mock.patch.object(runner, '_marked_peer_id', return_value=42))
        self.stack.enter_context(mock.patch.object(runner, '_entity_kind', return_value='user'))
        self.stack.enter_context(mock.patch.object(runner, '_ent_label', return_value='fixture peer'))
        self.stack.enter_context(mock.patch.object(workshop, '_resolve_read', return_value=self.source))
        self.stack.enter_context(mock.patch.dict(agent._TELETHON, {
            'send_file': runner._sync_send_file,
            'project_direct_outbox_acceptance': lambda proof, entry: 'projected',
        }))
        self.key = ''
        def threadsafe(factory, *_args):
            entry = runner._direct_outbox().get(self.key) if self.key else None
            if entry:
                runner._direct_outbox().clock = lambda: max(entry['not_before'], entry.get('retry_at', 0))
            return asyncio.run(factory())
        self.stack.enter_context(mock.patch.object(runner, '_threadsafe_result', side_effect=threadsafe))
        async def send(_entity, _item, **kwargs):
            return types.SimpleNamespace(id=501), kwargs['random_id']
        self.send = self.stack.enter_context(mock.patch.object(runner, '_send_file_idempotent', mock.AsyncMock(side_effect=send)))

    def start(self, tool='send_media', *, kind='audio', voice=True):
        self.args = {'path': str(self.source), 'caption': '', 'to': '42'}
        if tool == 'send_media':
            self.args.update(kind=kind, voice_note=voice)
        self.key = agent._tool_idempotency_key(self.context, 'media-call', tool, self.args)
        self.assertEqual(self.key, f'telegram-outbox:{self.context.run_id}:tool:media-call')
        self.manager.start_tool(self.context.run_id, 'media-call', tool, self.args,
                                side_effect=True, idempotency_key=self.key)
        self.execution = {'run_id': self.context.run_id, 'call_id': 'media-call',
                          'tool': tool, 'args': self.args, 'idempotency_key': self.key,
                          'side_effect': True}
        token = agent._TOOL_EXECUTION.set(self.execution)
        self.addCleanup(agent._TOOL_EXECUTION.reset, token)
        return self.execution

    def invoke(self):
        return agent.tool_send_media(**self.args)

    def test_voice_bridge_receipt_and_restart_replay_preserve_identity(self):
        self.start()
        result = self.invoke()
        entry = self.outbox.get(self.key)
        self.assertEqual(entry['state'], 'accepted', result)
        self.assertEqual(entry['purpose'], 'tool:send_media')
        self.assertTrue(agent.direct_outbox_prepared(entry))
        self.assertEqual(self.send.await_args.args[1].kind, 'audio')
        self.assertTrue(self.send.await_args.args[1].voice_note)
        self.assertEqual(entry['receipt']['media_kind'], 'audio')
        self.assertTrue(entry['receipt']['voice_note'])
        self.assertEqual(entry['receipt']['random_id'], telegram_outbox.stable_random_id(self.key))
        runner._DIRECT_OUTBOX = telegram_outbox.TelegramOutbox(self.outbox_root)
        self.assertEqual(self.invoke(), result)
        self.assertEqual(self.send.await_count, 1)
        self.assertEqual(agent.current_tool_execution()['tool'], 'send_media')

    def test_legacy_send_file_keeps_its_existing_purpose(self):
        self.start('send_file')
        result = runner._sync_send_file(str(self.source), to='42')
        entry = self.outbox.get(self.key)
        self.assertEqual(entry['state'], 'accepted', result)
        self.assertEqual(entry['purpose'], 'tool:send_file')
        self.assertNotIn('media_kind', agent._direct_outbox_identity(entry, verify_file=True)['payload'])

    def test_addressed_photo_keeps_photo_type(self):
        self.start(kind='photo', voice=True)
        result = self.invoke()
        entry = self.outbox.get(self.key)
        self.assertEqual(entry['state'], 'accepted', result)
        self.assertEqual(entry['receipt']['media_kind'], 'photo')
        self.assertFalse(entry['receipt']['voice_note'])

    def test_pending_voice_acceptance_closes_real_paused_call_once(self):
        self.start()
        self.send.side_effect = ConnectionError('fixture offline')
        with self.assertRaises(agent.DurableSideEffectPending):
            self.invoke()
        entry = self.outbox.get(self.key)
        self.assertEqual(entry['purpose'], 'tool:send_media')
        self.assertTrue(agent.direct_outbox_prepared(entry))
        self.fx._pause(self.context, reason='durable send_media intent awaits Telegram acceptance')
        accepted = self.outbox.mark_accepted(self.key, message_id=502)
        self.assertTrue(agent.run_direct_outbox_accepted(accepted))
        self.assertTrue(agent.run_direct_outbox_accepted(accepted))
        results = [r for r in self.manager.iter_events(self.context.run_id) if r.get('kind') == 'tool_result' and r.get('call_id') == 'media-call']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], 'send_media')
        self.assertNotIn('media-call', self.manager.outstanding_tools(self.context.run_id))
        self.assertEqual(self.send.await_count, 1)
        self.assertTrue(run_resume._DIRECT_OUTBOX_PAUSE.fullmatch('durable send_media intent awaits Telegram acceptance'))

    def test_receipt_reconciliation_recognizes_media_without_resending(self):
        self.start()
        self.invoke()
        started = self.manager.outstanding_tools(self.context.run_id)['media-call']
        with mock.patch.object(agent, '_direct_outbox_state', return_value=self.outbox.get(self.key)):
            outcome, evidence = agent._receipt_for_outstanding(started)
        self.assertEqual(outcome, 'completed')
        self.assertEqual(evidence['message_id'], 501)
        self.assertEqual(self.send.await_count, 1)

    def test_changed_voice_flag_cannot_reuse_a_delivery_or_its_proof(self):
        self.start()
        self.invoke()
        entry = self.outbox.get(self.key)
        changed = copy.deepcopy(entry)
        changed['payload']['voice_note'] = False
        self.assertFalse(agent.direct_outbox_prepared(changed))
        with self.assertRaises(agent.DurableExecutionError):
            agent.run_direct_outbox_prepared(changed)
        self.args['voice_note'] = False
        result = self.invoke()
        self.assertIn('TelegramOutboxConflict', result)
        self.assertEqual(self.send.await_count, 1)

    def test_media_purpose_cannot_be_relabelled_as_file(self):
        self.start()
        self.invoke()
        changed = copy.deepcopy(self.outbox.get(self.key))
        changed['purpose'] = 'tool:send_file'
        with self.assertRaises(agent.DurableExecutionError):
            agent.run_direct_outbox_prepared(changed)

    def test_unrelated_or_missing_call_identity_fails_before_resolution(self):
        for execution in (None, {}, {'run_id': 'run', 'call_id': 'call', 'tool': 'shell'},
                          {'run_id': '', 'call_id': 'call', 'tool': 'send_media'},
                          {'run_id': 'run', 'call_id': '', 'tool': 'send_media'}):
            with self.subTest(execution=execution), mock.patch.object(agent, 'current_tool_execution', return_value=execution):
                with self.assertRaises(agent.DurableExecutionError):
                    runner._sync_send_file(str(self.source), to='42', media_kind='audio', voice_note=True)
        runner._resolve_entity.assert_not_awaited()
        self.send.assert_not_awaited()

    def test_unaddressed_media_does_not_acquire_a_direct_delivery_key(self):
        for to in ('', '  ', None):
            self.assertEqual(agent._tool_idempotency_key(self.context, 'call', 'send_media', {'to': to}), '')

    def test_nonowner_secret_floor_still_blocks_before_delivery(self):
        from core import secrets
        import stewardship
        self.start()
        with mock.patch.object(runner, 'OWNER_ID', 99), mock.patch.object(secrets, 'document_floor', return_value='fixture credential'), mock.patch.object(stewardship, 'export_denial', return_value=''):
            self.invoke()
        self.assertIsNone(self.outbox.get(self.key))
        self.send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
