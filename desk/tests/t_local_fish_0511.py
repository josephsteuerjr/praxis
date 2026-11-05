"""Controlled local HTTP only. Private writer is an explicit fake, NOT ACL proof."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import uuid
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import fish_speech as fish, speech_client, speech_bridge, voice, speech_protocol as ipc


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
        audio.writeframes(b'\x01\x00' * 2400)
    return buffer.getvalue()


def private_fake(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data); path.chmod(0o600)


class LocalFish(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.tree = Path(self.temp.name); self.config = {'voice': {'speak': True, 'voice': 'fish',
            'fish_model': 'drama-3', 'fish_voice': 'fixture-voice', 'fish_delivery': 'calm, warm'}}
        self.stopped = False; self.calls = []; self.action = lambda: None; self.http_code = 200
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                outer.calls.append((self.path, self.headers.get('model'),
                    json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                outer.action()
                if outer.http_code == 0:
                    self.connection.close(); return
                data = outer.audio_bytes
                self.send_response(outer.http_code); self.send_header('Content-Length', str(len(data)))
                self.end_headers(); self.wfile.write(data)
        self.audio_bytes = wav_bytes()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever); worker.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), worker.join(3)))
        for mocked in (patch.object(fish, '_private', side_effect=private_fake),
                patch.object(ipc, 'blocked', side_effect=lambda tree: self.stopped),
                patch.object(fish, 'API', 'http://127.0.0.1:' + str(server.server_port) + '/v1/tts')):
            mocked.start(); self.addCleanup(mocked.stop)
        fish.configure_key(self.tree, 'fixture-key-never-real')

    def synth(self, identity=None, **kwargs):
        return speech_client.synthesize('fixture spoken text', self.tree, self.tree/'out',
            configuration=lambda: self.config, request_id=identity or uuid.uuid4().hex,
            format=kwargs.pop('format','wav'), timeout=3, **kwargs)

    def test_actual_http_wav_selection_and_private_journal(self):
        identity = uuid.uuid4().hex
        audio = self.synth(identity); audio.check()
        self.assertEqual(audio.path.read_bytes(), self.audio_bytes)
        self.assertEqual(self.calls[0][0:2], ('/v1/tts', 'drama-3'))
        self.assertEqual(self.calls[0][2]['text'], '[calm, warm] fixture spoken text')
        self.assertEqual(self.calls[0][2]['reference_id'], 'fixture-voice')
        receipt = (fish.home(self.tree)/(identity+'.json')).read_text()
        self.assertNotIn('fixture spoken text', receipt); self.assertNotIn('fixture-key', receipt)
        self.assertEqual(fish.status(self.tree,identity)['status'], 'completed')
        with self.assertRaisesRegex(RuntimeError, 'no automatic retry'): self.synth(identity)
        self.assertEqual(len(self.calls), 1)

    def test_disconnect_is_durable_unknown_and_cannot_repost(self):
        identity = uuid.uuid4().hex; self.http_code = 0
        with self.assertRaisesRegex(RuntimeError, 'fish_unknown'): self.synth(identity)
        self.assertEqual(fish.status(self.tree,identity)['status'], 'unknown')
        self.assertEqual(fish.latest(self.tree),fish.status(self.tree,identity))
        self.http_code = 200
        with self.assertRaisesRegex(RuntimeError, 'no automatic retry'): self.synth(identity)
        self.assertEqual(len(self.calls),1); self.assertEqual(list((self.tree/'out').iterdir()),[])

    def test_same_id_different_payload_cannot_charge(self):
        identity = uuid.uuid4().hex; self.synth(identity)
        self.config['voice']['fish_delivery'] = 'new delivery'
        with self.assertRaisesRegex(RuntimeError, 'conflict'): self.synth(identity)
        self.assertEqual(len(self.calls),1)

    def test_error_body_never_reaches_errors_or_receipt(self):
        self.http_code = 401; self.audio_bytes = b'private provider secret fixture-key-never-real'
        identity = uuid.uuid4().hex
        with self.assertRaisesRegex(RuntimeError, '^fish_http_401$'): self.synth(identity)
        value = fish.status(self.tree,identity)
        self.assertEqual(value['status'],'rejected'); self.assertNotIn('private',json.dumps(value))

    def test_redirect_never_forwards_authorization_or_reposts(self):
        self.http_code = 302
        with self.assertRaisesRegex(RuntimeError,'unknown'): self.synth()
        self.assertEqual(len(self.calls),1)

    def test_stop_during_http_discards_late_audio(self):
        self.action = lambda: setattr(self,'stopped',True)
        with self.assertRaises(InterruptedError): self.synth()
        self.assertEqual(len(self.calls),1); self.assertEqual(list((self.tree/'out').iterdir()),[])

    def test_selection_change_during_http_discards_late_audio(self):
        self.action = lambda: self.config['voice'].update(voice='xtts')
        with self.assertRaises(InterruptedError): self.synth()
        self.assertEqual(list((self.tree/'out').iterdir()),[])

    def test_config_revision_fences_change_back(self):
        self.config['_speech_epoch'] = 1
        self.action = lambda: self.config.update(_speech_epoch=3)
        with self.assertRaises(InterruptedError): self.synth()

    def test_cancel_before_admission_prevents_paid_post(self):
        identity = uuid.uuid4().hex; fish.cancel(self.tree,identity)
        with self.assertRaisesRegex(RuntimeError, 'cancelled'): self.synth(identity)
        self.assertEqual(self.calls,[])

    def test_cancel_during_http_and_after_completed_fences_audio(self):
        identity = uuid.uuid4().hex
        self.action = lambda: fish.cancel(self.tree,identity)
        with self.assertRaises(InterruptedError): self.synth(identity)
        self.action = lambda: None
        identity = uuid.uuid4().hex; audio = self.synth(identity)
        fish.cancel(self.tree,identity)
        with self.assertRaises(InterruptedError): audio.check()

    def test_invalid_audio_and_server_500_remain_unknown(self):
        self.audio_bytes = b'not audio'
        with self.assertRaisesRegex(RuntimeError,'unknown'): self.synth()
        self.http_code = 500
        with self.assertRaisesRegex(RuntimeError,'unknown'): self.synth()
        self.assertEqual(list((self.tree/'out').iterdir()),[])

    def test_preview_uses_fish_without_model_runtime_or_worker(self):
        with patch.object(voice,'speech_library',side_effect=AssertionError('XTTS probed')):
            state = voice.speech_state(self.tree,self.config)
            self.assertTrue(state['ready']); self.assertEqual(state['voice'],'fish')
        value = voice.preview(self.tree,self.config, {'request_id':uuid.uuid4().hex,'text':'sample'}, timeout=3)
        self.assertEqual(value['mime'],'audio/wav'); self.assertFalse((self.tree/'models').exists())
        self.assertFalse((self.tree/'memory/.state/xtts').exists())

    def test_fish_supervisor_never_requests_xtts_runtime_or_worker(self):
        from localharness import desktop_supervisor
        config=self.tree/'helene.json'
        (self.tree/'data').mkdir();(self.tree/'deskapp.py').write_text('# fixture')
        ipc.write(config,dict(self.config,mode='local',tree='data',code='missing-authored'))
        with patch.object(desktop_supervisor,'runtime_for',side_effect=AssertionError('Fish must not inspect XTTS runtime')):
            _,specs=desktop_supervisor._specs(config)
        self.assertFalse(any(spec.role=='xtts' for spec in specs.values()))

    def test_private_boundary_failure_writes_no_key_and_makes_no_post(self):
        fish.configure_key(self.tree,None)
        with patch.object(fish,'_private',side_effect=RuntimeError('private boundary unavailable')):
            with self.assertRaises(RuntimeError): fish.configure_key(self.tree,'new-fixture-key')
        self.assertFalse(fish.key_path(self.tree).exists()); self.assertEqual(self.calls,[])

    def test_migration_preserves_key_through_shared_private_boundary(self):
        from localharness import app_migration
        with tempfile.TemporaryDirectory() as temporary:
            destination=Path(temporary)/'new-tree'
            app_migration.preserve_source(self.tree,destination)
            self.assertEqual(fish._key(destination),'fixture-key-never-real')

    def test_selected_data_transfer_preserves_key_through_shared_private_boundary(self):
        from localharness import data_relocation
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);program=base/'program';program.mkdir()
            rows=[{'relative':'helene.json','tree':str(self.tree),'value':{'mode':'local'}}]
            receipt=program/'transfer.json';destination=base/'new-data'
            plan=data_relocation.plan(rows,self.tree,destination,program,base/'old-program',receipt)
            data_relocation.prepare(plan,receipt,lambda value:None)
            self.assertEqual(fish._key(destination),'fixture-key-never-real')

    @unittest.skipUnless(shutil.which('ffmpeg'), 'External codec fixture unavailable; not an embedded-runtime qualification')
    def test_real_opus_over_http_and_actual_decoder(self):
        source=self.tree/'codec.wav';encoded=self.tree/'codec.ogg'
        source.write_bytes(wav_bytes())
        subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-i',str(source),
            '-c:a','libopus',str(encoded)],check=True,capture_output=True,timeout=15)
        self.audio_bytes=encoded.read_bytes()
        audio=self.synth(format='ogg_opus');audio.check()
        decoded=self.tree/'decoded.wav'
        subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-i',str(audio.path),
            str(decoded)],check=True,capture_output=True,timeout=15)
        with wave.open(str(decoded),'rb') as wav:self.assertGreater(wav.getnframes(),0)
        self.assertEqual(self.calls[0][2]['format'],'opus')

    def test_agent_bridge_uses_stable_identity_and_automatic_delivery(self):
        # Real HTTP + real speech_client; use a valid WAV fixture here. Actual
        # Opus decoder/platform qualification is a separate integration gate.
        config = self.tree/'helene.json'; ipc.write(config,self.config)
        outbound=[]; staged=[]
        agent=types.SimpleNamespace(_TURN_CHANNEL=ContextVar('channel',default=types.SimpleNamespace(chat_id='123')),
            _TURN_OUTBOUND=ContextVar('outbound',default=outbound),tool_speak=lambda:None,
            run_context=types.SimpleNamespace(current_run=lambda:types.SimpleNamespace(run_id='run-fixture')),
            _runs=lambda:types.SimpleNamespace(manifest=lambda run:{'status':'running'}),
            current_tool_execution=lambda:{'call_id':'call-fixture'}, TOOL_IMPL={})
        def send(job,audio,check):
            check(); staged.append((audio.path.read_bytes(),job)); return 'delivered'
        agent._stage_turn_media=lambda *a,**k: (_ for _ in ()).throw(AssertionError('No per-turn staging'))
        @contextmanager
        def delivery(*args): yield True
        boundary=types.SimpleNamespace(cancelled=lambda state:False,delivery=delivery)
        owner=types.SimpleNamespace(LOCK=threading.RLock())
        service=speech_bridge.install(agent,self.tree,config,boundary=boundary,owner=owner,deliver=send)
        self.addCleanup(service.close)
        original=speech_client.synthesize
        def wav_transport(*args,**kwargs):return original(*args,**dict(kwargs,format='wav'))
        with patch.object(speech_client,'synthesize',side_effect=wav_transport):
            self.assertIn('принят',agent.tool_speak('agent speech'));service.pending.join()
            self.assertIn('delivered',agent.tool_speak('agent speech'))
        self.assertEqual(len(self.calls),1);self.assertEqual(len(staged),1)
        self.assertEqual(staged[0][1]['chat_id'],'123')
        identity=hashlib.sha256(b'run-fixture:call-fixture').hexdigest()[:32]
        self.assertEqual(fish.status(self.tree,identity)['status'],'completed')



if __name__=='__main__':unittest.main()
