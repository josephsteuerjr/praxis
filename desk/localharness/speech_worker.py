"""Application-owned XTTS service, always launched inside the desktop owner's Job.

The model algorithm is the byte-identical packaged copy of the canonical core
xtts_tts_worker.py beside this module. Never import it from the authored tree.
The legacy core IPC is not used: this lane binds every request to one process
incarnation, a caller lease, and the tree's durable Stop/Pause controls.
"""
from __future__ import annotations

import argparse
import importlib.util
import math
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
        deadline=float(request['deadline'])
        return not math.isfinite(deadline) or time.time()>deadline or time.time()-lease>LEASE_SECONDS
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
          stopped=lambda: False, synthesis=None, catalogue=None, codec=None,
          warmer=None,terminate=None,warmup_timeout=180,initial_selection=None,cancellation_grace=10):
    generation = generation or uuid.uuid4().hex
    if not ipc.identity(generation):
        raise ValueError('Invalid speech generation')
    home.mkdir(parents=True, exist_ok=True)
    incoming, output = home / 'inbox', home / 'output'
    incoming.mkdir(exist_ok=True); output.mkdir(exist_ok=True)
    if initial_selection is not None:ipc.write(home/'selection.json',initial_selection)
    # This transient marker belongs to the retired worker incarnation. Durable
    # owner Stop/Pause remains in the independent tree control checked below.
    ipc.unlink(home/'stop')
    # This service is admitted under the existing exclusive desktop tree owner.
    # No old request/output is a request to its replacement incarnation.
    for folder in (incoming, output):
        for path in folder.iterdir():
            if ipc.identity(path.name.lstrip('.').split('.')[0]) and path.is_file():
                ipc.unlink(path)
    import psutil
    state = {'schema': 2, 'pid': os.getpid(), 'generation': generation, 'ready': False,
        'process_identity':{'adapter':'psutil-v1','pid':os.getpid(),'birth':psutil.Process().create_time()},
        'phase':'loading','device_requested':device}
    state_lock=threading.RLock();guard_stop=threading.Event();abandoned=threading.Event();active=[None]
    def publish(**values):
        with state_lock:
            state.update(values,observed_at=time.time())
            ipc.write(home/'state.json',state);ipc.write(home/'ready.json',state)
    def guard():
        previous=None;since=None
        while not guard_stop.wait(.05):
            item=active[0]
            if item is not previous:previous=item;since=None
            if item and (time.time()>item['deadline'] or item['cancel']()):
                if since is None:since=time.monotonic()
                # Real CPU first chunks can take 4-6s. Cancellation fences the
                # caller immediately; let ordinary inference settle before
                # discarding a healthy hot model. A stuck call stays bounded.
                if time.monotonic()-since>=cancellation_grace:
                    abandoned.set()
                    if ipc.read(home/'ready.json').get('generation')==generation:
                        publish(ready=False,phase='error',error='XTTS did not settle cancellation/deadline; worker exit requested for recovery')
                    terminate(75)  # ONLY this process; the existing owner performs restart/backoff
                    return
            else:since=None
    guard_thread=threading.Thread(target=guard,name='xtts-deadline',daemon=True) if terminate else None
    publish()
    attempted=None;attempted_stamp=None
    def warm(cancel):
        nonlocal attempted,attempted_stamp
        choice=ipc.selection(ipc.read(home/'selection.json'),tree);stamp=ipc.reference_stamp(choice)
        if choice!=attempted or stamp!=attempted_stamp:
            publish(ready=False,phase='warming_voice',error='')
            active[0]={'deadline':time.time()+warmup_timeout,'cancel':cancel}
            try:
                info=warmer(choice,cancel) if warmer else {}
                if cancel() or abandoned.is_set():raise InterruptedError('Voice warmup cancelled')
            except InterruptedError:
                if not abandoned.is_set():publish(ready=False,phase='paused' if ipc.blocked(tree) else 'stopped')
                return False
            except Exception as exc:
                # Bad voice input does not invalidate the loaded model. Retry
                # when the owner changes the selection/reference, not each tick.
                attempted,attempted_stamp=choice,stamp
                publish(ready=False,phase='error',error=f'{type(exc).__name__}: {exc}')
                return False
            finally:
                active[0]=None
            attempted,attempted_stamp=choice,stamp
            publish(**(info or {}),ready=True,phase='ready',error='',selection=choice,
                selection_key=ipc.selection_key(choice),reference_stamp=stamp)
            return True
        return False
    try:
        if stopped() or ipc.blocked(tree) or (home / 'stop').exists():
            # Pause may keep the supervised model unloaded until explicit resume.
            while ipc.blocked(tree) and not stopped() and not (home / 'stop').exists():
                time.sleep(.05)
            if stopped() or (home / 'stop').exists():
                return
        active[0]={'deadline':time.time()+warmup_timeout,'cancel':lambda:stopped() or ipc.blocked(tree)}
        if guard_thread:guard_thread.start()
        if synthesis is None:
            canonical = engine_module()
            engine = canonical.load_engine(model, device)
            synthesis = lambda request, path, cancel: canonical.synthesize_wav(engine, request, path, cancel)
            warmer=lambda choice,cancel:canonical.warm_voice(engine,choice,cancel)
            catalogue = {'speakers': list(engine.speaker_manager.speakers),
                         'languages': list(engine.config.languages)}
        state.update(catalogue or {})
        state['ogg_opus']=speech_encode.available() if codec is None else codec
        warm(lambda:stopped() or ipc.blocked(tree))
        active[0]=None
        next_choice=0.0
        while not stopped() and not abandoned.is_set() and not (home / 'stop').exists():
            if ipc.read(home/'ready.json').get('generation')!=generation:break
            if ipc.blocked(tree) and state.get('phase')!='paused':
                publish(ready=False,phase='paused')
                attempted=None
            if not ipc.blocked(tree) and time.monotonic()>=next_choice:
                warm(lambda:stopped() or ipc.blocked(tree))
                active[0]=None
                next_choice=time.monotonic()+1
            # New clients publish strictly ordered tickets, even when the
            # filesystem gives successive requests identical mtimes.
            for path in ipc.queued(incoming):
                request=ipc.read(path)
                cancel=lambda request=request,identity=path.stem:request_cancelled(home,tree,request,identity,generation,stopped)
                try:
                    deadline=float(request.get('deadline') or 0)
                    deadline=min(deadline,time.time()+300) if math.isfinite(deadline) else 0
                except (ValueError,TypeError):deadline=0
                active[0]={'deadline':deadline,'cancel':cancel}
                available=state.get('ready') is True
                if available:publish(phase='busy',active_request=path.stem)
                process(home, tree, path, generation, synthesis, lambda:stopped() or abandoned.is_set())
                active[0]=None
                if available and not abandoned.is_set() and ipc.read(home/'ready.json').get('generation')==generation:
                    publish(phase='ready',active_request='')
            # Retire abandoned markers even if the client removed its pending JSON.
            for path in incoming.glob('*.cancel'):
                identity = path.stem
                if ipc.identity(identity) and not path.with_suffix('.json').exists():
                    _remove(_artifacts(home, identity))
                    _remove((path, path.with_suffix('.lease')))
            time.sleep(.05)
    except InterruptedError:
        publish(ready=False,phase='stopped',error='')
    except Exception as exc:
        publish(ready=False,phase='error',error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        active[0]=None;guard_stop.set()
        if guard_thread and guard_thread.ident:guard_thread.join(timeout=1)
        owned=ipc.read(home / 'ready.json').get('generation') == generation
        if owned:
            if state.get('phase')!='error':publish(ready=False,phase='stopped')
            ipc.unlink(home / 'ready.json')
        # Teardown never leaves late audio or pending requests to replay.
        if owned:
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
    parser.add_argument('--speaker')
    parser.add_argument('--language')
    parser.add_argument('--reference')
    args = parser.parse_args()
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    serve(args.home, args.model, args.tree, device=args.device,
          generation=args.generation, stopped=stop.is_set,terminate=os._exit,
          initial_selection={'speaker':args.speaker,'language':args.language,'reference':args.reference}
              if any(value is not None for value in (args.speaker,args.language,args.reference)) else None)


if __name__ == '__main__':
    main()
