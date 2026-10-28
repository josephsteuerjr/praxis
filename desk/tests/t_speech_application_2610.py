"""Application speech consumers; synthetic audio only, no model/network/subprocess.

The codec test is real when run in the qualified XTTS runtime. A missing codec
is an explicit skip here, not a pretend Ogg payload or a runtime qualification.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch
import wave

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
sys.path.insert(0, str(DESK / 'localharness'))
from localharness import (speech_bridge, speech_client, speech_encode,
                         speech_protocol as ipc, speech_worker, voice, owner_stop)
GEN = 'a' * 32
CFG = {'voice': {'speak': True, 'tts_speaker': 'A', 'tts_language': 'en'}}


def wav(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(24000)
        out.writeframes(b'\x01\x00' * 2400)


class SpeechApplication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'memory/.state/xtts'
        self.home.mkdir(parents=True)
        self.stop = threading.Event()
        self.patches = [patch.object(ipc, 'blocked', return_value=False),
                        patch.object(speech_client, '_live', return_value=True)]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)

    def ready(self):
        ipc.write(self.home / 'ready.json', {'schema': 2, 'pid': os.getpid(),
            'generation': GEN, 'ready': True, 'speakers': ['A', 'B'],
            'languages': ['en', 'ru'], 'ogg_opus': False})

    def serve(self, synthesis=None):
        errors = []
        def run():
            try:
                speech_worker.serve(self.home, self.root / 'models', self.root,
                    generation=GEN, stopped=self.stop.is_set,
                    synthesis=synthesis or (lambda req, path, cancelled: wav(path)),
                    catalogue={'speakers': ['A', 'B'], 'languages': ['en', 'ru']}, codec=False)
            except BaseException as exc:
                import traceback
                traceback.print_exc()
                errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        def finish():
            self.stop.set(); thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
        self.addCleanup(finish)
        end = time.monotonic() + 2
        while not ipc.read(self.home / 'ready.json').get('ready'):
            if time.monotonic() > end:
                self.fail('synthetic worker did not become ready: ' + repr(errors))
            time.sleep(.01)

    def test_client_waits_for_generation_and_returns_real_wav(self):
        self.serve()
        result = speech_client.synthesize('test', self.root, self.root / 'client',
            configuration=lambda: CFG, format='wav', timeout=2)
        result.check()
        with wave.open(str(result.path), 'rb') as audio:
            self.assertEqual(audio.getframerate(), 24000)
            self.assertGreater(audio.getnframes(), 0)
        self.assertEqual(result.generation, GEN)
        self.assertFalse(list((self.home / 'output').iterdir()))

    def test_preview_is_wav_and_never_requires_opus(self):
        self.serve()
        result = voice.preview(self.root, CFG, {'speaker': 'B', 'language': 'ru', 'text': 'test'}, timeout=2)
        self.assertEqual(result['mime'], 'audio/wav')
        self.assertGreater(len(result['base64']), 100)
        self.assertFalse(list((self.root / 'media/tts-preview').iterdir()))

    def test_per_request_selection_is_not_boot_environment(self):
        observed = []
        self.serve(lambda req, path, cancelled: (observed.append(req), wav(path)))
        config = json.loads(json.dumps(CFG))
        for speaker in ('A', 'B'):
            config['voice']['tts_speaker'] = speaker
            audio = speech_client.synthesize('test', self.root, self.root / 'client',
                configuration=lambda: config, format='wav', timeout=2)
            audio.path.unlink()
        self.assertEqual([row['speaker'] for row in observed], ['A', 'B'])

    def request(self, *, generation=GEN, deadline=None):
        self.ready()
        identity = 'b' * 32
        incoming = self.home / 'inbox'; incoming.mkdir(exist_ok=True)
        (self.home / 'output').mkdir(exist_ok=True)
        path = incoming / (identity + '.json')
        path.with_suffix('.lease').touch()
        ipc.write(path, {'generation': generation, 'deadline': deadline or time.time() + 10,
            'text': 'test', 'speaker': 'A', 'language': 'en', 'format': 'wav'})
        return path

    def test_stale_generation_does_not_synthesize_or_leave_audio(self):
        path = self.request(generation='c' * 32)
        synth = Mock()
        speech_worker.process(self.home, self.root, path, GEN, synth, lambda: False)
        synth.assert_not_called()
        self.assertFalse(list((self.home / 'output').iterdir()))
        self.assertFalse(list((self.home / 'inbox').iterdir()))

    def test_dead_caller_lease_is_not_a_request_to_run(self):
        path = self.request()
        old = time.time() - 100
        os.utime(path.with_suffix('.lease'), (old, old))
        synth = Mock()
        speech_worker.process(self.home, self.root, path, GEN, synth, lambda: False)
        synth.assert_not_called()
        self.assertFalse(path.exists())

    def test_cancel_after_synthesis_leaves_no_receipt_or_partial(self):
        path = self.request()
        def synth(req, partial, cancelled):
            wav(partial); path.with_suffix('.cancel').touch()
            self.assertTrue(cancelled())
        speech_worker.process(self.home, self.root, path, GEN, synth, lambda: False)
        self.assertFalse(list((self.home / 'output').iterdir()))
        self.assertFalse(list((self.home / 'inbox').iterdir()))

    def test_pause_before_request_does_not_publish_it(self):
        self.ready()
        with patch.object(ipc, 'blocked', return_value=True):
            with self.assertRaises(InterruptedError):
                speech_client.synthesize('test', self.root, self.root / 'client',
                    configuration=lambda: CFG, format='wav', timeout=.01)
        self.assertFalse((self.home / 'inbox').exists())

    def test_pause_during_streaming_cancels_client_and_worker(self):
        paused = threading.Event()
        def synth(req, partial, cancelled):
            wav(partial); paused.set()
            end = time.monotonic() + 2
            while not cancelled() and time.monotonic() < end:
                time.sleep(.01)
            self.assertTrue(cancelled())
        self.serve(synth)
        with patch.object(ipc, 'blocked', side_effect=lambda tree: paused.is_set()):
            with self.assertRaises(InterruptedError):
                speech_client.synthesize('test', self.root, self.root / 'client',
                    configuration=lambda: CFG, format='wav', timeout=2)
            time.sleep(.1)
            self.assertFalse(list((self.home / 'output').iterdir()))

    def test_replacement_generation_never_adopts_old_result(self):
        def synth(req, partial, cancelled):
            wav(partial)
            ready = ipc.read(self.home / 'ready.json'); ready['generation'] = 'd' * 32
            ipc.write(self.home / 'ready.json', ready)
        self.serve(synth)
        with self.assertRaises(InterruptedError):
            speech_client.synthesize('test', self.root, self.root / 'client',
                configuration=lambda: CFG, format='wav', timeout=2)
        self.assertFalse(list((self.home / 'output').iterdir()))

    def test_voice_note_refuses_missing_codec_before_inbox(self):
        self.ready()
        with self.assertRaisesRegex(RuntimeError, 'Ogg/Opus'):
            speech_client.synthesize('test', self.root, self.root / 'client', configuration=lambda: CFG, timeout=.01)
        self.assertFalse((self.home / 'inbox').exists())

    def test_cancel_during_encoder_never_publishes(self):
        path = self.request()
        body = ipc.read(path); body['format'] = 'ogg_opus'; ipc.write(path, body)
        def encode(source, destination, *, stopped):
            destination.write_bytes(b'partial fixture, NOT an Ogg assertion')
            path.with_suffix('.cancel').touch()
            self.assertTrue(stopped())
            raise InterruptedError('cancelled during codec')
        speech_worker.process(self.home, self.root, path, GEN,
            lambda req, partial, cancelled: wav(partial), lambda: False, encoder=encode)
        self.assertFalse(list((self.home / 'output').iterdir()))

    def test_timeout_leaves_cancellation_marker_not_an_unbound_replay(self):
        self.ready()
        with self.assertRaises(TimeoutError):
            speech_client.synthesize('test', self.root, self.root / 'client',
                configuration=lambda: CFG, format='wav', timeout=.01)
        incoming = self.home / 'inbox'
        self.assertEqual(len(list(incoming.glob('*.cancel'))), 1)
        self.assertFalse(list(incoming.glob('*.json')))

    def old_agent(self):
        code = self.root / 'authored'; code.mkdir()
        source = code / 'agent.py'
        (code / 'media_audio.py').write_text(
            "SUPPORTED_TTS = ('edge', 'piper', 'silero')\n"
            "def transcribe(path): return 'STT untouched'\n", encoding='utf-8')
        source.write_text(
            "# sentinel self-edit: deliberately old code, no XTTS client/backend\n"
            "import importlib.util\nfrom pathlib import Path\nfrom contextvars import ContextVar\n"
            "spec = importlib.util.spec_from_file_location('_old_audio_fixture', Path(__file__).with_name('media_audio.py'))\n"
            "media_audio = importlib.util.module_from_spec(spec); spec.loader.exec_module(media_audio)\n"
            "def tool_speak(*args, **kwargs): return 'old-edge-only'\n"
            "_TURN_CHANNEL = ContextVar('old_channel', default=object())\n"
            "_TURN_OUTBOUND = ContextVar('old_outbound', default=None)\n"
            "_TURN_MEDIA_GUARD = ContextVar('old_guard', default=None)\n"
            "TOOL_IMPL = {'speak': tool_speak}\n", encoding='utf-8')
        spec = importlib.util.spec_from_file_location('_old_agent_fixture', source)
        old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
        state = {'status': 'running'}
        runs = types.SimpleNamespace(manifest=lambda rid: state, status=lambda rid: state)
        old.run_context = types.SimpleNamespace(current_run=lambda: types.SimpleNamespace(run_id='run'))
        old._runs = lambda: runs
        return old, source, state

    @contextmanager
    def boundary(self, runs, run_id):
        yield True

    def test_old_authored_adapter_preserves_bytes_and_stt_and_stages_voice_note(self):
        old, source, state = self.old_agent()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        config = self.root / 'helene.json'; ipc.write(config, CFG)
        staged = []; old._TURN_OUTBOUND.set(staged)
        audio_path = self.root / 'generated.ogg'
        audio_path.write_bytes(b'transport fixture; codec qualification is separate')
        audio = types.SimpleNamespace(path=audio_path, check=lambda stopped: None)
        old._stage_turn_media = Mock(return_value='staged')
        boundary = types.SimpleNamespace(delivery=self.boundary,
            cancelled=lambda state: state.get('control', {}).get('action') == 'cancel')
        speech_bridge.install(old, self.root, config, boundary=boundary, owner=owner_stop)
        with patch.object(speech_client, 'synthesize', return_value=audio) as synth:
            self.assertEqual(old.TOOL_IMPL['speak']('whole text', 'caption'), 'staged')
            self.assertIs(old.TOOL_IMPL['speak'], old.tool_speak)
            self.assertTrue(synth.call_args.kwargs['configuration']()['voice']['speak'])
        self.assertEqual(old._stage_turn_media.call_args.kwargs['voice_note'], True)
        self.assertEqual(old._stage_turn_media.call_args.kwargs['guard_text'], 'Точный текст синтезированной речи: whole text')
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        self.assertEqual(old.media_audio.transcribe(None), 'STT untouched')
        self.assertFalse((source.parent / 'xtts_tts_worker.py').exists())
        self.assertFalse(audio_path.exists())

    def test_cancel_or_generation_change_during_staging_removes_only_new_item(self):
        for action in ('cancel', 'pause', 'generation'):
            with self.subTest(action=action):
                old, source, state = self.old_agent() if not (self.root / 'authored').exists() else self._reuse_old()
                config = self.root / 'helene.json'; ipc.write(config, CFG)
                existing = types.SimpleNamespace(path=self.root / 'earlier.ogg', queue_id='earlier')
                existing.path.write_bytes(b'earlier')
                outbound = [existing]; old._TURN_OUTBOUND.set(outbound)
                path = self.root / 'new.ogg'; path.write_bytes(b'new fixture')
                self.ready()
                paused = threading.Event()
                audio = speech_client.Audio(path, self.home, self.root, GEN, lambda: CFG)
                def stage(source, **kwargs):
                    moved = self.root / 'spool.ogg'; shutil.move(source, moved)
                    outbound.append(types.SimpleNamespace(path=moved, queue_id='new'))
                    if action == 'cancel': state.update(status='cancelled')
                    elif action == 'pause': paused.set()
                    else:
                        ready = ipc.read(self.home/'ready.json'); ready['generation'] = 'd'*32
                        ipc.write(self.home/'ready.json', ready)
                    return 'staged'
                old._stage_turn_media = stage
                boundary = types.SimpleNamespace(delivery=self.boundary, cancelled=lambda state: False)
                speech_bridge.install(old, self.root, config, boundary=boundary, owner=owner_stop)
                with patch.object(speech_client, 'synthesize', return_value=audio), patch.object(ipc, 'blocked', side_effect=lambda tree: paused.is_set()):
                    self.assertIn('отменена', old.tool_speak('test'))
                self.assertEqual(outbound, [existing])
                self.assertTrue(existing.path.exists())
                self.assertFalse((self.root / 'spool.ogg').exists())

    def _reuse_old(self):
        for path in (self.root / 'authored').iterdir(): path.unlink()
        (self.root / 'authored').rmdir()
        return self.old_agent()

    def test_supervisor_selects_app_worker_without_authored_worker(self):
        from localharness import desktop_supervisor as owner
        tree = self.root / 'data'; tree.mkdir()
        python = self.root / 'components/xtts/runtime' / ('python.exe' if os.name == 'nt' else 'bin/python3')
        python.parent.mkdir(parents=True); python.write_bytes(b'fixture')
        model = tree / 'models/xtts/model.pth'; model.parent.mkdir(parents=True); model.write_bytes(b'fixture')
        app = self.root / 'deskapp.py'; app.write_text('# fixture')
        config = self.root / 'helene.json'
        ipc.write(config, {'mode': 'local', 'tree': 'data', 'code': 'missing-authored', 'voice': {'speak': True}})
        _, specs = owner._specs(config)
        spec = specs['main:xtts']
        self.assertEqual(Path(spec.argv[2]), DESK / 'localharness/speech_worker.py')
        self.assertIn('--tree', spec.argv)
        self.assertFalse((self.root / 'missing-authored').exists())
        coordinator = owner.Coordinator({key: item.role for key, item in specs.items()})
        actions = coordinator.tick({'seq': 1, 'now_ms': 0, 'children': {key: {'state': 'absent'} for key in specs}})
        self.assertIn({'id': 'main:xtts', 'action': 'spawn'}, actions)

    def test_botapi_real_method_selection_preserves_topic_and_voice_payload(self):
        import botapi
        transport = object.__new__(botapi.BotTransport)
        transport.tree = self.root
        transport._before_send = Mock()
        transport.client = types.SimpleNamespace(upload=Mock(return_value={'message_id': 7}))
        transport._retry_send = lambda peer, method, send: (send(), 0)
        transport.rooms = types.SimpleNamespace(record=Mock())
        transport.contacts = types.SimpleNamespace(label=lambda ref: 'fixture')
        path = self.root / 'note.ogg'
        path.write_bytes(b'transport-only fixture: actual codec tested separately')
        with patch.object(botapi.telegram_media, 'outbound', return_value={}):
            receipt = transport.deliver_file(path, chat_id='-123__topic__45',
                caption='caption', media_kind='audio', voice_note=True)
        call = transport.client.upload.call_args
        self.assertEqual(call.args, ('sendVoice', 'voice', path))
        self.assertEqual(call.kwargs['chat_id'], '-123')
        self.assertEqual(call.kwargs['message_thread_id'], 45)
        self.assertEqual(call.kwargs['caption'], 'caption')
        self.assertIn('7', receipt)

    def test_mtproto_real_upload_sets_voice_note_and_reply(self):
        import mtproto
        calls = []
        async def send_file(chat, path, **kwargs):
            calls.append((chat, path, kwargs))
            return types.SimpleNamespace(id=8)
        client = object.__new__(mtproto.MtprotoClient)
        client.client = types.SimpleNamespace(send_file=send_file)
        client._run = lambda coro, timeout: asyncio.run(coro)
        path = self.root / 'note.ogg'
        result = client.upload('sendVoice', 'voice', path, chat_id='-123',
            caption='caption', message_thread_id=45, reply_to_message_id=46)
        self.assertEqual(result, {'message_id': 8})
        self.assertEqual(calls, [(-123, str(path), {'caption': 'caption', 'reply_to': 46,
            'voice_note': True, 'force_document': False})])

    def test_canonical_synthesis_is_shared_not_copied_into_app_wrapper(self):
        source = DESK.parent / 'helene/core/xtts_tts_worker.py'
        spec = importlib.util.spec_from_file_location('_speech_canonical_fixture', source)
        canonical = importlib.util.module_from_spec(spec); spec.loader.exec_module(canonical)
        import numpy as np
        class Chunk:
            def detach(self): return self
            def cpu(self): return self
            def numpy(self): return np.array([.1, -.1, .0])
        sentences = []
        def stream(text, language, conditioning, speaker, **kwargs):
            sentences.append(text)
            yield Chunk()
        engine = types.SimpleNamespace(speaker_manager=types.SimpleNamespace(
            speakers={'A': {'conditioning': 'c', 'speaker': 's'}}), inference_stream=stream)
        path = self.root / 'canonical.wav'
        text = 'one two three ' * 40
        canonical.synthesize_wav(engine, {'text': text, 'speaker': 'A', 'language': 'en'}, path)
        self.assertEqual(' '.join(sentences).split(), text.split())
        with wave.open(str(path)) as audio:
            self.assertEqual(audio.getframerate(), 24000)
            self.assertEqual(audio.getnframes(), 3 * len(sentences))
        self.assertFalse(hasattr(speech_worker, 'Xtts'))

    @unittest.skipUnless(speech_encode.available(), 'actual libsndfile/OPUS runtime qualification owed')
    def test_real_ogg_opus_codec_and_cancel_cleanup(self):
        import soundfile
        source = self.root / 'sample.wav'; wav(source)
        destination = self.root / 'sample.ogg'
        speech_encode.opus(source, destination)
        with soundfile.SoundFile(str(destination)) as container:
            self.assertEqual(container.format, 'OGG')
            self.assertEqual(container.subtype, 'OPUS')
            self.assertGreater(len(container.read()), 0)
        with self.assertRaises(InterruptedError):
            speech_encode.opus(source, self.root / 'cancelled.ogg', stopped=lambda: True)
        self.assertFalse((self.root / 'cancelled.ogg').exists())


if __name__ == '__main__':
    unittest.main()
