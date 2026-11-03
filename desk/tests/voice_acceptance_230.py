"""Opt-in Windows real speech consumer, never an installed-profile test.

Explicit read-only XTTS Python/model arguments; only the tiny worker closure is
copied. Inference and codec run in the actual retained Windows ChildJob. No TG
send, audio device, credentials or remote model call. Decoder/local sendVoice
consumer prove bytes and routing, not an audible/Telegram playback claim.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import types
import uuid
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
CORE = DESK.parent / 'helene/core'
sys.path[:0] = [str(DESK), str(DESK / 'localharness'), str(CORE)]


def wait_for(predicate, description, timeout=120):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise TimeoutError(description)
        time.sleep(.025)


def run(args):
    if os.name != 'nt':
        raise RuntimeError('This ChildJob qualification is Windows-only')
    from localharness import speech_bridge, speech_client, speech_protocol as ipc
    from localharness import owner_stop, control_watch, desktop_process, voice
    args.python, args.model = args.python.resolve(), args.model.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    tree = output / 'private-data'
    tree.mkdir()
    # Fence live source/default reads before the actual registered agent import.
    os.environ['PRAXIS_TEST_BASE'] = str(tree)
    import praxis_test  # noqa: F401
    import agent
    import media
    import media_audio
    import workshop
    import botapi
    home = tree / 'memory/.state/xtts'
    app = output / 'application/localharness'
    app.mkdir(parents=True)
    copied = {}
    for name in ('speech_worker.py', 'speech_protocol.py', 'speech_encode.py', 'owner_stop.py'):
        source = DESK / 'localharness' / name
        shutil.copyfile(source, app / name)
        copied[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    engine = CORE / 'xtts_tts_worker.py'
    shutil.copyfile(engine, app / engine.name)
    copied[engine.name] = hashlib.sha256(engine.read_bytes()).hexdigest()
    cfg = {'voice': {'speak': True, 'tts_language': 'en'}}
    config = output / 'helene.json'
    ipc.write(config, cfg)
    owner_stop.configure(tree)
    if ipc.blocked(tree):
        raise RuntimeError('Actual owner Stop set; this test cannot clear it')
    workshop.BASE = workshop.REPO = tree
    agent._MEDIA_SPOOL = media.MediaSpool(tree / 'workspace/media',
        audio_max_bytes=16 * 1024 * 1024, max_total_bytes=32 * 1024 * 1024)
    manifest = {'status': 'running'}
    manager = types.SimpleNamespace(manifest=lambda rid: manifest, status=lambda rid: manifest)
    child = None
    report = {'format': 'helene.speech-acceptance.v1', 'worker_source_sha256': copied,
              'xtts_python': str(args.python.resolve()), 'model': str(args.model.resolve()),
              'effects': 'local inference/staging/decoding; no network/audio-device/TG send',
              'generations': [], 'audio': [], 'negative_cases': []}
    errors = []
    env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')
           if key in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONUNBUFFERED='1',
               HOME=str(output), USERPROFILE=str(output), HF_HOME=str(output / 'hf-cache'),
               TORCH_HOME=str(output / 'torch-cache'), TRANSFORMERS_OFFLINE='1', HF_HUB_OFFLINE='1')

    def start():
        nonlocal child
        home.mkdir(parents=True, exist_ok=True)
        (home / 'stop').unlink(missing_ok=True)
        generation = uuid.uuid4().hex
        argv = [str(args.python.resolve()), '-B', '-u', str(app / 'speech_worker.py'),
                '--home', str(home), '--model', str(args.model.resolve()),
                '--tree', str(tree), '--generation', generation]
        if args.diagnostics:
            # Test-only traceback sampling, no alternate synthesis implementation.
            argv = [argv[0], '-B', '-u', '-c',
                'import sys,faulthandler,runpy; faulthandler.dump_traceback_later(45,repeat=True); '
                'sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name="__main__")', *argv[3:]]
        print('STARTING_SPEECH_WORKER ' + generation, flush=True)
        child = desktop_process.JobChild.spawn(argv, cwd=tree, env=env,
                   log=output / ('worker-' + generation + '.log'), stopped=lambda: False)
        def ready_or_exited():
            if child.poll() is not None:
                raise RuntimeError('Speech worker exited before readiness; inspect worker-' + generation + '.log')
            return ipc.read(home / 'ready.json').get('ready') is True
        wait_for(ready_or_exited, 'real model worker readiness', args.timeout)
        ready = ipc.read(home / 'ready.json')
        print('SPEECH_WORKER_READY ' + generation, flush=True)
        assert ready['generation'] == generation and voice.worker_live(home, ready) is True
        assert ready['ogg_opus'] is True, ready
        report['generations'].append({'generation': generation, 'pid': child.pid,
                                     'ready': ready, 'job_active': child.job.active()})
        cfg['voice']['tts_speaker'] = ready['speakers'][0]
        ipc.write(config, cfg)
        return generation

    def stop():
        nonlocal child
        if child is None:
            return
        (home / 'stop').touch()
        wait_for(lambda: child.poll() is not None, 'speech worker exit', 20)
        report.setdefault('worker_exits', []).append({'handle_closed': child.handle is None,
                                                     'job_closed': child.job.handle is None})
        child = None

    def registered_speak(label, text='Hello.'):
        outbound, guards = [], {}
        channel = agent._TURN_CHANNEL.set(agent.ChannelContext(chat_id='777', is_dm=True, owner=True))
        out_token = agent._TURN_OUTBOUND.set(outbound)
        guard_token = agent._TURN_MEDIA_GUARD.set(guards)
        try:
            speech_bridge.install(agent, tree, config, boundary=control_watch, owner=owner_stop)
            assert agent.TOOL_IMPL['speak'] is agent.tool_speak
            with (patch.object(agent.run_context, 'current_run', return_value=types.SimpleNamespace(run_id='fixture')),
                  patch.object(agent, '_runs', return_value=manager)):
                note = agent.TOOL_IMPL['speak'](text, caption=label)
            return note, outbound, guards
        finally:
            agent._TURN_CHANNEL.reset(channel)
            agent._TURN_OUTBOUND.reset(out_token)
            agent._TURN_MEDIA_GUARD.reset(guard_token)

    def consume(label, outbound, guards):
        assert len(outbound) == 1, outbound
        item = outbound[0]
        assert item.voice_note is True and item.kind == 'audio'
        source = Path(item.path)
        data = source.read_bytes()
        assert data.startswith(b'OggS') and b'OpusHead' in data[:512]
        assert guards[item.queue_id].endswith('Hello.')
        saved = output / (label + '.ogg')
        shutil.copyfile(source, saved)
        code = ('import json,soundfile,sys; s,sr=soundfile.read(sys.argv[1]); '
                'i=soundfile.info(sys.argv[1]); print(json.dumps({"frames":len(s),'
                '"rate":sr,"format":i.format,"subtype":i.subtype}))')
        decoded = json.loads(subprocess.check_output([str(args.python), '-B', '-c', code, str(saved)],
                              cwd=tree, env=env, creationflags=0x08000000, timeout=60))
        assert decoded['frames'] > 0 and decoded['format'] == 'OGG' and decoded['subtype'] == 'OPUS'
        calls = []
        transport = object.__new__(botapi.BotTransport)
        transport.tree = tree
        transport._before_send = lambda *a, **k: None
        def upload(*a, **kw):
            calls.append((a, kw)); return {'message_id': 17}
        transport.client = types.SimpleNamespace(upload=upload)
        transport._retry_send = lambda peer, method, send: (send(), 0)
        transport.rooms = types.SimpleNamespace(record=lambda *a, **k: None)
        transport.contacts = types.SimpleNamespace(label=lambda ref: 'isolated')
        with patch.object(botapi.telegram_media, 'outbound', return_value={}):
            transport.deliver_file(source, chat_id='-123__topic__45', caption=label,
                                   media_kind='audio', voice_note=True)
        assert calls[0][0][0:2] == ('sendVoice', 'voice')
        assert calls[0][1]['message_thread_id'] == 45
        report['audio'].append({'label': label, 'path': str(saved), 'sha256': hashlib.sha256(data).hexdigest(),
                                'bytes': len(data), 'decoded': decoded, 'transport': 'sendVoice local fake'})
        source.unlink()

    try:
        first = start()
        note, out, guards = registered_speak('before-restart')
        assert out, note
        consume('before-restart', out, guards)
        audio = speech_client.synthesize('Hello.', tree, tree / 'wav',
                    configuration=lambda: cfg, format='wav', timeout=args.timeout)
        stop()
        second = start()
        assert first != second
        try:
            audio.check()
        except InterruptedError:
            report['negative_cases'].append('previous-generation Audio.check refused')
        else:
            raise AssertionError('old audio admitted after restart')
        audio.path.unlink(missing_ok=True)
        note, out, guards = registered_speak('after-restart')
        assert out, note
        consume('after-restart', out, guards)
        def interrupt(kind):
            wait_for(lambda: any((home / 'output').glob('*.part.wav')), 'real PCM production started', 30)
            if kind == 'cancel':
                manifest['requested_control'] = {'action': 'cancel'}
            else:
                latch = tree / 'memory/.control/autonomy-paused.json'
                latch.parent.mkdir(parents=True, exist_ok=True); latch.write_text('{}')
        for kind in ('cancel', 'pause'):
            manifest.clear(); manifest.update(status='running')
            failures = []
            def trigger():
                try: interrupt(kind)
                except BaseException as exc: failures.append(repr(exc))
            thread = threading.Thread(target=trigger)
            thread.start()
            note, out, guards = registered_speak(kind, 'Hello. ' * 20)
            thread.join(15)
            assert not thread.is_alive() and not failures, failures
            assert not out and not guards, (kind, note)
            wait_for(lambda: not list((home / 'output').glob('*')) and
                            not list((home / 'inbox').glob('*')), 'cancelled artifacts reclaimed', 15)
            report['negative_cases'].append(kind + ' after real PCM producer started: no staged/late audio')
            (tree / 'memory/.control/autonomy-paused.json').unlink(missing_ok=True)
        ident = uuid.uuid4().hex
        req = home / 'inbox' / (ident + '.json')
        lease = req.with_suffix('.lease'); lease.touch()
        os.utime(lease, (time.time() - 100, time.time() - 100))
        ipc.write(req, {'request_id': ident, 'generation': second, 'deadline': time.time() + 30,
                       'text': 'never synthesize', 'speaker': cfg['voice']['tts_speaker'],
                       'language': 'en', 'format': 'ogg_opus'})
        wait_for(lambda: not req.exists(), 'expired lease retirement', 5)
        assert not list((home / 'output').glob(ident + '*'))
        report['negative_cases'].append('expired real worker request lease: no audio/receipt')
        backend = media_audio.MediaAudio(stt=types.SimpleNamespace(transcribe=lambda p: 'independent STT'))
        with patch.dict(os.environ, {'PRAXIS_TTS_BACKEND': 'disabled'}):
            assert backend.transcribe('fixture') == 'independent STT'
        report['stt_independence'] = 'actual facade, deterministic recognizer; no model qualification'
        stop()
        report['passed'] = True
    except BaseException as exc:
        errors.append(type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        if child is not None:
            try:
                child.stop()
                report['forced_cleanup'] = 'retained Job terminated and observed empty'
            except BaseException as exc:
                errors.append('cleanup: ' + repr(exc))
        report['errors'] = errors
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print('SPEECH_NATIVE_ACCEPTANCE_PASS ' + str(output / 'report.json'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, required=True, help='existing XTTS Python (read-only)')
    parser.add_argument('--model', type=Path, required=True, help='existing model directory (read-only)')
    parser.add_argument('--output', type=Path, required=True, help='NEW isolated output directory')
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--diagnostics', action='store_true', help='test-only sampled Python stacks')
    run(parser.parse_args())
