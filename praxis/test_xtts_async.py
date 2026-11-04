"""Detached voice generation, explicit publication, stream integrity and recovery.

No test sends a Telegram message or loads neural weights. The actual encoder
and resident model have a separate server acceptance lane.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault('PRAXIS_TEST', '1')

import test_xtts as legacy
import xtts_intent
from xtts_generations import Generations, process_owner
from xtts_protocol import SCHEMA, XttsError, encoded, read_intent
from xtts_service import SpeechServer
from xtts_stream_upload import upload
from xtts_tts_client import XttsTTSClient


def payload(size=5000):
    head = b'OggS' + bytes(24) + b'OpusHead'
    return head + b'x' * (size - len(head))


def metadata(identity, raw):
    return {'request_id': identity, 'model_id': legacy.HEALTH['model_id'],
            'profile_id': legacy.HEALTH['profile_id'], 'mime': 'audio/ogg',
            'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'audio_seconds': 1.25}


class GenerationFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='voice-generations-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Generations(self.root/'generations')

    def submit(self, call='call-1', channel=None):
        return self.store.submit('Точные слова.', run_id='source-run', call_id=call,
            channel=channel or {'chat_id': '777', 'scope': 'owner', 'is_dm': True, 'known': True},
            selection=legacy.HEALTH)


class GenerationCase(GenerationFixture):
    def test_replay_is_one_generation_and_different_intent_conflicts(self):
        first = self.submit()
        self.assertEqual(self.submit()['generation_id'], first['generation_id'])
        self.assertEqual(len(self.store.list()), 1)
        with self.assertRaisesRegex(XttsError, 'generation_conflict'):
            self.store.submit('Другие слова.', run_id='source-run', call_id='call-1',
                channel=first['channel'], selection=legacy.HEALTH)

    def test_cancel_twice_is_still_only_requested_while_producer_is_active(self):
        identity = self.submit()['generation_id']
        self.store.update(identity, 'running', owner=process_owner(), attempt_id='c'*32)
        self.assertEqual(self.store.cancel(identity)['state'], 'cancel_requested')
        self.assertEqual(self.store.cancel(identity)['state'], 'cancel_requested')

    def test_recovery_keeps_a_cancel_request_after_producer_loss(self):
        identity = self.submit()['generation_id']
        self.store.update(identity, 'running', owner={**process_owner(), 'boot_id': 'different-boot'},
                          attempt_id='c'*32)
        self.store.cancel(identity)
        self.store.recover_lost()
        self.assertEqual(self.store.read(identity)['state'], 'cancel_requested')
        self.assertEqual(self.store.cancel(identity)['state'], 'cancel_requested')

    def test_missing_producer_is_unknown_and_can_still_be_cancelled(self):
        identity = self.submit()['generation_id']
        owner = {**process_owner(), 'boot_id': 'different-boot'}
        self.store.update(identity, 'running', owner=owner, attempt_id='c'*32)
        self.store.recover_lost()
        self.assertEqual(self.store.read(identity)['state'], 'unknown')
        self.assertEqual(self.store.cancel(identity)['state'], 'cancel_requested')

    def test_ready_result_integrity_is_required_and_no_deadline_is_created(self):
        value = self.submit()
        identity = value['generation_id']
        self.assertNotIn('deadline', value)
        self.assertNotIn('expires_at', value)
        self.store.update(identity, 'running', owner=process_owner(), attempt_id='c'*32)
        raw = payload()
        self.store.audio_path(identity).write_bytes(raw)
        with self.assertRaisesRegex(XttsError, 'generation_integrity'):
            self.store.complete(identity, {**metadata(identity, raw), 'sha256': '0'*64})
        self.store.complete(identity, metadata(identity, raw))
        result = self.store.observation(self.store.read(identity))
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['audio']['bytes'], len(raw))
        self.assertEqual(result['delivery'], 'not_requested_by_generation')


class ToolCase(GenerationFixture):
    def setUp(self):
        super().setUp()
        import agent
        import media
        import run_context
        import run_manager
        self.agent = agent
        self.spool = media.MediaSpool(self.root/'media')
        self.manager = run_manager.RunManager(self.root/'runs')
        self.run = self.manager.create(run_context.RunContext.create(
            kind='chat', goal='detached speech', principal_id='owner', scope='owner',
            origin_chat_id='777', delivery_chat_id='777'), 'fixture')
        self.manager.transition(self.run.run_id, 'running')
        self.ctx = agent.ChannelContext(chat_id='777', is_dm=True, owner=True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(agent, '_RUN_MANAGER', self.manager))
        self.stack.enter_context(mock.patch.object(agent, '_voice_generations', return_value=self.store))
        self.stack.enter_context(mock.patch.object(agent, '_media_spool', return_value=self.spool))
        self.stack.enter_context(mock.patch('xtts_tts_client.selected', return_value=legacy.HEALTH))
        self.stack.enter_context(run_context.bind_run(self.run))
        self.outbound = []
        self.notes = {}
        for variable, value in [(agent._TURN_CHANNEL, self.ctx), (agent._TURN_OUTBOUND, self.outbound),
                (agent._TURN_MEDIA_GUARD, self.notes),
                (agent._TOOL_EXECUTION, {'run_id': self.run.run_id, 'call_id': 'voice-call', 'tool': 'generate_voice'})]:
            token = variable.set(value)
            self.addCleanup(variable.reset, token)

    def test_parent_turn_can_finish_without_sending_or_cancelling_generation(self):
        result = json.loads(self.agent.tool_generate_voice('Точные слова.'))
        self.assertTrue(result['ok'])
        identity = result['generation_id']
        self.manager.transition(self.run.run_id, 'done')
        raw = payload()
        def synthesize(request, path, **kwargs):
            self.assertEqual(self.manager.manifest(self.run.run_id)['status'], 'done')
            self.assertFalse(kwargs['stopped']())
            path.write_bytes(raw)
            return metadata(identity, raw)
        client = SimpleNamespace(stream_intent=synthesize)
        self.store.execute(identity, client, owner=process_owner())
        self.assertEqual(self.store.read(identity)['state'], 'ready')
        self.assertFalse(self.outbound)
        self.assertFalse(self.spool.pending())

    def test_only_explicit_send_stages_and_repeated_event_does_not_duplicate_it(self):
        result = json.loads(self.agent.tool_generate_voice('Точные слова.'))
        identity = result['generation_id']
        self.assertFalse(self.outbound)
        sent = json.loads(self.agent.tool_send_voice(identity))
        self.assertEqual(sent['delivery'], 'staged')
        self.assertEqual(len(self.outbound), 1)
        item = self.outbound[0]
        self.assertEqual(read_intent(item.path)['generation_id'], identity)
        self.assertIn('Точные слова.', self.notes[item.queue_id])
        with mock.patch.object(self.agent.media_audio, 'transcribe') as transcribe:
            kept, context, _, _ = self.agent._prepare_outbound_guard(self.outbound, self.notes, self.ctx)
        self.assertEqual(kept, self.outbound)
        self.assertIn('Точные слова.', context)
        transcribe.assert_not_called()
        self.spool.enqueue(item)
        self.outbound.clear()
        import run_context
        other = self.manager.create(run_context.RunContext.create(
            kind='chat', goal='redelivered result', principal_id='praxis:self', scope='owner',
            origin_chat_id='777', delivery_chat_id='777'), 'later event')
        self.manager.transition(other.run_id, 'running')
        with run_context.bind_run(other):
            repeated = json.loads(self.agent.tool_send_voice(identity))
        self.assertEqual(repeated['delivery']['queue_id'], item.queue_id)
        self.assertFalse(self.outbound)
        self.assertEqual(len(self.spool.pending()), 1)

    def test_another_chat_does_not_gain_access_from_generation_id(self):
        identity = json.loads(self.agent.tool_generate_voice('Точные слова.'))['generation_id']
        other = self.agent.ChannelContext(chat_id='888', is_dm=False, owner=False, _scope_override='group')
        token = self.agent._TURN_CHANNEL.set(other)
        try:
            result = json.loads(self.agent.tool_voice_generation('result', identity))
        finally:
            self.agent._TURN_CHANNEL.reset(token)
        self.assertEqual(result['error'], 'generation_audience_mismatch')

    def test_frames_expose_split_tools_and_keep_old_dispatch_only_for_replay(self):
        names = {tool['name'] for tool in self.agent.TOOLS}
        self.assertTrue({'generate_voice', 'voice_generation', 'send_voice'} <= names)
        self.assertNotIn('voice_message', names)
        self.assertNotIn('speak', names)
        self.assertIs(self.agent.TOOL_IMPL['speak'], self.agent.TOOL_IMPL['voice_message'])

    def test_late_event_is_a_tool_result_in_its_origin_audience(self):
        value = json.loads(self.agent.tool_generate_voice('Точные слова.'))
        event = {'id': 'fixture-event', 'payload': {'generation_id': value['generation_id']}}
        envelope = SimpleNamespace(run_id='', failed=False, deferred=False)
        with mock.patch.object(self.agent, 'voice_turn_envelope', return_value=envelope) as turn:
            self.agent.voice_generation_event_turn(event)
        chat, seed = turn.call_args.args
        ctx = turn.call_args.kwargs['ctx']
        self.assertEqual(chat, '777')
        self.assertEqual(ctx.scope, 'owner')
        self.assertEqual(ctx.principal_id, self.agent.PRAXIS_SELF_PRINCIPAL)
        self.assertIsNone(ctx.origin_message_id)
        self.assertIn('not a new message', seed)
        self.assertIn('chat reply is optional', seed)
        self.assertIn(self.run.run_id, seed)
        self.assertIn('voice-call', seed)

    def test_runner_delivers_late_result_in_dm_and_selected_group_tape(self):
        import mtproto_runner as runner
        import frame_epoch
        from core import events
        for is_dm in (True, False):
            with self.subTest(is_dm=is_dm):
                origin = {'chat_id': '777' if is_dm else '-100777',
                          'scope': 'owner' if is_dm else 'group',
                          'is_dm': is_dm, 'known': True}
                value = self.submit(call='late-' + str(is_dm), channel=origin)
                event = {'id': 'late-result', 'dedup_key': 'late-result',
                         'payload': {'generation_id': value['generation_id']}}
                selected = {'anchor': 42, 'n': 2, 'rows': [], 'fixture': 'detached-tape'}
                envelope = SimpleNamespace(run_id='', failed=False, deferred=False,
                                           retry_media=False, outbound=[])
                def freeze(chat_id, policy, *, epoch_sink):
                    self.assertEqual(chat_id, origin['chat_id'])
                    epoch_sink.update(selected)
                    return '', ()
                def model(chat_id, seed, *, ctx):
                    self.assertEqual(chat_id, origin['chat_id'])
                    self.assertEqual(ctx.scope, origin['scope'])
                    self.assertEqual(ctx.principal_id, self.agent.PRAXIS_SELF_PRINCIPAL)
                    if not is_dm:
                        self.assertEqual(frame_epoch.bound_tape(chat_id), selected)
                        self.assertEqual(frame_epoch.bound_anchor(chat_id), selected['anchor'])
                    return envelope
                with mock.patch.object(runner, '_ONE_MIND', asyncio.Lock()), \
                        mock.patch('xtts_generations.Generations', return_value=self.store), \
                        mock.patch.object(self.agent.llm, 'configured', return_value=True), \
                        mock.patch.object(events, 'undelivered', return_value=[event]), \
                        mock.patch.object(events, 'bump_attempts', return_value={'late-result': 1}), \
                        mock.patch.object(events, 'mark_delivered') as delivered, \
                        mock.patch.object(frame_epoch, 'room_enabled', return_value=not is_dm), \
                        mock.patch.object(runner, '_group_context_frozen', side_effect=freeze) as frozen, \
                        mock.patch.object(self.agent, 'voice_turn_envelope', side_effect=model) as turn, \
                        mock.patch.object(self.agent, 'run_delivery_completed'):
                    asyncio.run(runner._run_voice_result_pass())
                turn.assert_called_once()
                delivered.assert_called_once_with(['late-result'])
                self.assertEqual(frozen.call_count, 0 if is_dm else 1)

    def test_unavailable_group_tape_does_not_consume_a_late_result(self):
        import mtproto_runner as runner
        import frame_epoch
        from core import events
        value = self.submit(channel={'chat_id': '-100777', 'scope': 'group',
                                     'is_dm': False, 'known': True})
        event = {'id': 'late-result', 'payload': {'generation_id': value['generation_id']}}
        with mock.patch.object(runner, '_ONE_MIND', asyncio.Lock()), \
                mock.patch('xtts_generations.Generations', return_value=self.store), \
                mock.patch.object(self.agent.llm, 'configured', return_value=True), \
                mock.patch.object(events, 'undelivered', return_value=[event]), \
                mock.patch.object(events, 'bump_attempts') as attempts, \
                mock.patch.object(events, 'mark_delivered') as delivered, \
                mock.patch.object(frame_epoch, 'room_enabled', return_value=True), \
                mock.patch.object(runner, '_group_context_frozen', side_effect=frame_epoch.EpochUnavailable('fixture')), \
                mock.patch.object(self.agent, 'voice_generation_event_turn') as turn:
            asyncio.run(runner._run_voice_result_pass())
        attempts.assert_not_called()
        delivered.assert_not_called()
        turn.assert_not_called()

    def test_admitted_send_streams_then_uses_native_voice_media_without_reupload(self):
        import mtproto_runner as runner
        from telethon.tl import functions, types
        from xtts_stream_upload import PART_SIZE
        identity = json.loads(self.agent.tool_generate_voice('Точные слова.'))['generation_id']
        self.agent.tool_send_voice(identity)
        item = self.outbound[0]
        self.spool.enqueue(item)
        self.agent.run_delivery_started(self.run.run_id, chat_id='777', text_chars=0,
                                       media_count=1, media_queue_ids=[item.queue_id])
        self.store.update(identity, 'running', attempt_id='c'*32, owner=process_owner())
        raw = payload(PART_SIZE + 1000)
        path = self.store.audio_path(identity)
        path.write_bytes(raw[:PART_SIZE])
        parts, messages = [], []
        case = self

        class Telegram:
            async def get_input_entity(self, entity):
                return types.InputPeerSelf()

            async def _file_to_media(self, *args, **kwargs):
                raise AssertionError('already streamed; no second upload')

            async def _parse_message_text(self, text, mode):
                return text, []

            def _get_response_message(self, request, response, entity):
                return SimpleNamespace(id=41)

            async def __call__(self, request):
                if isinstance(request, functions.upload.SaveBigFilePartRequest):
                    parts.append(request)
                    if len(parts) == 1:
                        case.assertEqual(case.store.read(identity)['state'], 'running')
                        path.write_bytes(raw)
                        case.store.complete(identity, metadata(identity, raw))
                    return True
                case.assertIsInstance(request, functions.messages.SendMediaRequest)
                messages.append(request)
                return SimpleNamespace()

        async def attempt(entity, original, **kwargs):
            actual = xtts_intent.prepared(self.spool, original)
            self.assertEqual(actual.queue_id, original.queue_id)
            sent, _ = await runner._send_file_idempotent(entity, actual)
            return sent.id == 41

        with mock.patch.object(runner, 'client', Telegram()), \
                mock.patch.object(runner, '_media_spool', return_value=self.spool), \
                mock.patch.object(runner, '_attempt_queued_media', side_effect=attempt), \
                mock.patch.object(self.spool, 'ready', return_value=True), \
                mock.patch('xtts_generations.Generations', return_value=self.store):
            asyncio.run(runner._prepare_speech_in_background(777, item, ctx=self.ctx,
                                                           reply_to=None, state_chat_id='777'))
        self.assertEqual(b''.join(part.bytes for part in parts), raw)
        self.assertEqual(len(messages), 1)
        media = messages[0].media
        self.assertTrue(next(attr for attr in media.attributes if isinstance(attr, types.DocumentAttributeAudio)).voice)
        self.assertNotIn(item.queue_id, runner._PREUPLOADED_SPEECH)


class CancellationCase(GenerationFixture):
    def test_producer_clock_is_independent_of_a_blocked_main_care(self):
        import mtproto_runner as runner
        async def exercise():
            blocked = asyncio.Event()
            kicked = asyncio.Event()
            async def slow():
                await blocked.wait()
            async def voice():
                kicked.set()
            main, side = runner._split_clock_jobs({'slow': (1, slow), 'voice_jobs': (.01, voice)})
            self.assertIn('voice_jobs', side)
            main_task = asyncio.create_task(main['slow'][1]())
            side_task = asyncio.create_task(runner._clock_side('voice_jobs', *side['voice_jobs']))
            try:
                await asyncio.wait_for(kicked.wait(), 1)
                self.assertFalse(main_task.done())
            finally:
                blocked.set()
                side_task.cancel()
                await asyncio.gather(main_task, side_task, return_exceptions=True)
        asyncio.run(exercise())

    def test_clock_waits_for_capability_quiescence_before_confirming_cancel(self):
        import mtproto_runner as runner
        from core import events
        identity = self.submit()['generation_id']
        self.store.update(identity, 'running', owner={**process_owner(), 'boot_id': 'different-boot'},
                          attempt_id='c'*32)
        self.store.recover_lost()
        self.assertEqual(self.store.read(identity)['state'], 'unknown')
        self.store.cancel(identity)
        client = mock.Mock()
        client.synthesis_status.side_effect = [
            {'active': True, 'cancel_requested': True},
            {'active': False, 'cancel_requested': True}]
        with mock.patch('xtts_generations.Generations', return_value=self.store), \
                mock.patch('xtts_tts_client.XttsTTSClient', return_value=client), \
                mock.patch('xtts_tts_client.selected', return_value=None), \
                mock.patch.object(events, 'undelivered', return_value=[]), \
                mock.patch.dict(runner._VOICE_GENERATING, {}, clear=True):
            asyncio.run(runner._voice_jobs_once())
            self.assertEqual(self.store.read(identity)['state'], 'cancel_requested')
            asyncio.run(runner._voice_jobs_once())
            self.assertEqual(self.store.read(identity)['state'], 'cancelled')


@unittest.skipUnless(os.name == 'posix' and hasattr(socket, 'AF_UNIX'), 'Linux UDS capability')
class WireCase(GenerationFixture):
    def setUp(self):
        super().setUp()
        self.release = threading.Event()
        self.started = threading.Event()
        self.raw = payload(12000)
        self.bad_checksum = False
        self.disconnect = False
        self.token = self.root/'token'
        self.token.write_text('q'*64)
        self.server = SpeechServer(self.root/'speech.sock', 'q'*64, SimpleNamespace(), scratch=self.root/'scratch')
        self.patch = mock.patch('xtts_stream.respond', side_effect=self.respond)
        self.patch.start()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.client = XttsTTSClient(self.root, socket_path=self.root/'speech.sock', token_file=self.token, timeout=None)

    def shutdown(self):
        self.release.set()
        self.server.close_requests()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.patch.stop()

    def respond(self, handler, request, wav_path, profile, stopped):
        meta = metadata(request['request_id'], self.raw)
        handler.send({'ok': True, 'stream': True, 'generation': handler.server.generation, **meta})
        handler.send({'ok': True, 'chunk_bytes': len(self.raw)})
        handler.wfile.write(self.raw)
        handler.wfile.flush()
        self.started.set()
        self.release.wait(3)
        if self.disconnect:
            return
        if stopped():
            raise XttsError('cancelled')
        if self.bad_checksum:
            meta['sha256'] = '0'*64
        handler.send({'ok': True, 'done': True, **meta})

    def request(self):
        return {'request_id': 'd'*32, 'text': 'fixture', **{k: legacy.HEALTH[k] for k in ('model_id', 'profile_id')}}

    def test_audio_prefix_is_visible_before_generation_is_complete(self):
        destination = self.root/'growing.ogg'
        results = []
        errors = []
        def request():
            try:
                results.append(self.client.stream_intent(self.request(), destination))
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=request)
        thread.start()
        try:
            self.assertTrue(self.started.wait(2))
            until = time.monotonic() + 2
            while (not destination.exists() or destination.stat().st_size != len(self.raw)) and time.monotonic() < until:
                time.sleep(.01)
            self.assertEqual(destination.read_bytes(), self.raw)
            self.assertFalse(results)
        finally:
            self.release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(results[0]['sha256'], hashlib.sha256(self.raw).hexdigest())

    def test_bad_final_hash_removes_only_this_incomplete_output(self):
        self.bad_checksum = True
        self.release.set()
        destination = self.root/'bad.ogg'
        with self.assertRaisesRegex(XttsError, 'audio_integrity'):
            self.client.stream_intent(self.request(), destination)
        self.assertFalse(destination.exists())

    def test_disconnect_does_not_turn_a_prefix_into_a_result(self):
        self.disconnect = True
        self.release.set()
        destination = self.root/'lost.ogg'
        with self.assertRaisesRegex(XttsError, 'worker_disconnected'):
            self.client.stream_intent(self.request(), destination)
        self.assertFalse(destination.exists())

    def test_preexisting_output_is_preserved(self):
        destination = self.root/'existing.ogg'
        destination.write_bytes(b'previous-result')
        with self.assertRaisesRegex(XttsError, 'generation_conflict'):
            self.client.stream_intent(self.request(), destination)
        self.assertEqual(destination.read_bytes(), b'previous-result')
        self.assertFalse(self.started.is_set())

    def test_repeated_cancel_does_not_postpone_stuck_worker_recovery(self):
        identity = 'f'*32
        self.server.active[identity] = (threading.Event(), float('inf'), time.monotonic())
        self.client.cancel(identity)
        started = self.server.cancelled[identity]
        time.sleep(.01)
        self.client.cancel(identity)
        self.assertEqual(self.server.cancelled[identity], started)
        self.assertTrue(self.server.needs_restart(now=started + 11))
        self.server.active.pop(identity)


class UploadCase(GenerationFixture):
    def test_unknown_length_upload_starts_before_completion_and_finalizes_voice(self):
        from telethon.tl import functions, types
        async def exercise(raw):
            value = self.submit(call=str(len(raw)))
            identity = value['generation_id']
            self.store.update(identity, 'running', attempt_id='c'*32, owner=process_owner())
            path = self.store.audio_path(identity)
            path.write_bytes(raw[:1024])
            parts = []
            async def rpc(request):
                self.assertIsInstance(request, functions.upload.SaveBigFilePartRequest)
                parts.append(request)
                if len(parts) == 1:
                    self.assertEqual(self.store.read(identity)['state'], 'running')
                    path.write_bytes(raw)
                    self.store.complete(identity, metadata(identity, raw))
                return True
            request = {**value['request'], 'generation_id': identity}
            media = await upload(rpc, self.store, request, part_size=1024)
            self.assertEqual(b''.join(part.bytes for part in parts), raw)
            self.assertEqual([part.file_part for part in parts], list(range(len(parts))))
            self.assertTrue(all(part.file_total_parts == -1 for part in parts[:-1]))
            self.assertEqual(parts[-1].file_total_parts, (len(raw)+1023)//1024)
            self.assertIsInstance(media, types.InputMediaUploadedDocument)
            voice = next(attr for attr in media.attributes if isinstance(attr, types.DocumentAttributeAudio))
            self.assertTrue(voice.voice)
            self.assertEqual(media.mime_type, 'audio/ogg')
            if len(raw) % 1024 == 0:
                self.assertEqual(parts[-1].bytes, b'')
        for size in (2048, 2500):
            with self.subTest(size=size):
                asyncio.run(exercise(payload(size)))

    def test_attempt_change_aborts_instead_of_splicing_two_streams(self):
        async def exercise():
            value = self.submit()
            identity = value['generation_id']
            self.store.update(identity, 'running', attempt_id='c'*32, owner=process_owner())
            self.store.audio_path(identity).write_bytes(payload(2048))
            async def rpc(request):
                self.store.update(identity, 'running', attempt_id='d'*32)
                return True
            with self.assertRaisesRegex(XttsError, 'stream_restart'):
                await upload(rpc, self.store, {**value['request'], 'generation_id': identity}, part_size=1024)
        asyncio.run(exercise())
