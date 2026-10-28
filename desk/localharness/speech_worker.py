"""Application-owned XTTS service, always launched inside the desktop owner's Job.

The model algorithm is the byte-identical packaged copy of the canonical core
xtts_tts_worker.py beside this module. Never import it from the authored tree.
The legacy core IPC is not used: this lane binds every request to one process
incarnation, a caller lease, and the tree's durable Stop/Pause controls.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import signal
import threading
import time
import uuid

if __package__:
    from . import speech_protocol as ipc, speech_encode
else:
    # The optional Windows runtime is embeddable: python311._pth deliberately
    # excludes argv[0]'s directory (also true for -I). Bootstrap only this
    # application-owned sibling directory, never the authored agent tree.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import speech_protocol as ipc
    import speech_encode

LEASE_SECONDS = 10


def engine_module():
    path = Path(__file__).with_name('xtts_tts_worker.py')
    if not path.is_file():
        raise RuntimeError('Application XTTS engine missing; update the application payload')
    spec = importlib.util.spec_from_file_location('_helene_application_xtts_engine', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _remove(paths):
    for path in paths:
        ipc.unlink(path)


def _artifacts(home, identity):
    return [home / 'output' / (identity + suffix) for suffix in
            ('.wav', '.part.wav', '.ogg', '.part.ogg', '.json')]


def request_cancelled(home, tree, request, identity, generation, stopped):
    if stopped() or ipc.blocked(tree) or (home / 'stop').exists():
        return True
    if request.get('generation') != generation or not ipc.generation(home, generation):
        return True
    if (home / 'inbox' / (identity + '.cancel')).exists():
        return True
    try:
        # A dead caller cannot keep a pending/streaming request alive indefinitely.
        lease = (home / 'inbox' / (identity + '.lease')).stat().st_mtime
        return time.time() > float(request['deadline']) or time.time() - lease > LEASE_SECONDS
    except (OSError, ValueError, TypeError, KeyError):
        return True


def process(home, tree, path, generation, synthesize, stopped, *, encoder=speech_encode.opus):
    identity = path.stem
    if not ipc.identity(identity):
        return
    incoming, output = home / 'inbox', home / 'output'
    cancel, lease = incoming / (identity + '.cancel'), incoming / (identity + '.lease')
    artifacts = _artifacts(home, identity)
    partial, wav = output / (identity + '.part.wav'), output / (identity + '.wav')
    request = ipc.read(path)
    cancelled = lambda: request_cancelled(home, tree, request, identity, generation, stopped)
    try:
        if cancelled():
            raise InterruptedError('Speech request stale, paused or cancelled')
        if request.get('format') not in ('wav', 'ogg_opus'):
            raise ValueError('Unsupported speech output format')
        synthesize(request, partial, cancelled)
        if cancelled():
            raise InterruptedError('Speech cancelled after synthesis')
        partial.replace(wav)
        final = wav
        if request['format'] == 'ogg_opus':
            final = encoder(wav, output / (identity + '.ogg'), stopped=cancelled)
            wav.unlink(missing_ok=True)
        if cancelled():
            raise InterruptedError('Speech cancelled before output publication')
        result = {'ok': True, 'request_id': identity, 'generation': generation,
                  'format': request['format'], 'path': str(final)}
    except InterruptedError:
        _remove(artifacts)
        result = None
    except Exception as exc:
        _remove(artifacts)
        result = {'ok': False, 'request_id': identity, 'generation': generation,
                  'error': str(exc)}
    finally:
        ipc.unlink(path)
        # Keep lease/cancel until the publication check below: they also fence a
        # caller abandoning work between the final chunk and receipt write.
    if result is None or cancelled():
        _remove(artifacts)
    else:
        ipc.write(output / (identity + '.json'), result)
        if cancelled():
            _remove(artifacts)
    _remove((cancel, lease))


def serve(home: Path, model: Path, tree: Path, *, device='cpu', generation=None,
          stopped=lambda: False, synthesis=None, catalogue=None, codec=None):
    generation = generation or uuid.uuid4().hex
    if not ipc.identity(generation):
        raise ValueError('Invalid speech generation')
    home.mkdir(parents=True, exist_ok=True)
    incoming, output = home / 'inbox', home / 'output'
    incoming.mkdir(exist_ok=True); output.mkdir(exist_ok=True)
    # This service is admitted under the existing exclusive desktop tree owner.
    # No old request/output is a request to its replacement incarnation.
    for folder in (incoming, output):
        for path in folder.iterdir():
            if ipc.identity(path.name.lstrip('.').split('.')[0]) and path.is_file():
                ipc.unlink(path)
    state = {'schema': 2, 'pid': os.getpid(), 'generation': generation, 'ready': False}
    ipc.write(home / 'ready.json', state)
    try:
        if stopped() or ipc.blocked(tree) or (home / 'stop').exists():
            # Pause may keep the supervised model unloaded until explicit resume.
            while ipc.blocked(tree) and not stopped() and not (home / 'stop').exists():
                time.sleep(.05)
            if stopped() or (home / 'stop').exists():
                return
        if synthesis is None:
            canonical = engine_module()
            engine = canonical.load_engine(model, device)
            synthesis = lambda request, path, cancel: canonical.synthesize_wav(engine, request, path, cancel)
            catalogue = {'speakers': list(engine.speaker_manager.speakers),
                         'languages': list(engine.config.languages)}
        state.update(catalogue or {})
        state.update(ready=True, ogg_opus=speech_encode.available() if codec is None else codec)
        ipc.write(home / 'ready.json', state)
        while not stopped() and not (home / 'stop').exists():
            for path in sorted(incoming.glob('*.json')):
                process(home, tree, path, generation, synthesis, stopped)
            # Retire abandoned markers even if the client removed its pending JSON.
            for path in incoming.glob('*.cancel'):
                identity = path.stem
                if ipc.identity(identity) and not path.with_suffix('.json').exists():
                    _remove(_artifacts(home, identity))
                    _remove((path, path.with_suffix('.lease')))
            time.sleep(.05)
    finally:
        if ipc.read(home / 'ready.json').get('generation') == generation:
            ipc.unlink(home / 'ready.json')
        # Teardown never leaves late audio or pending requests to replay.
        for folder in (incoming, output):
            for path in folder.iterdir():
                if ipc.identity(path.name.lstrip('.').split('.')[0]) and path.is_file():
                    ipc.unlink(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--tree', type=Path, required=True)
    parser.add_argument('--generation', required=True)
    parser.add_argument('--device', default='cpu')
    args = parser.parse_args()
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    serve(args.home, args.model, args.tree, device=args.device,
          generation=args.generation, stopped=stop.is_set)


if __name__ == '__main__':
    main()
