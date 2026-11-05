"""Application-qualified request client; imports neither authored audio nor TTS.

Every call reads its current voice selection, waits boundedly for the owned
service, binds a generation and renews its own lease. It never launches a model.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import time
import uuid
import json

from . import speech_protocol as ipc


def _live(home, ready, *, require_ready=True):
    from .voice import worker_live
    return worker_live(home, ready,require_ready=require_ready) is True


def _enabled(cfg):
    voice = cfg.get('voice') or {}
    return isinstance(voice, dict) and voice.get('speak') is True


def _check(tree, configuration, stopped):
    if stopped() or ipc.blocked(tree) or not _enabled(configuration()):
        raise InterruptedError('Speech disabled, paused or cancelled')


@dataclass(frozen=True)
class Audio:
    path: Path
    home: Path
    tree: Path
    generation: str
    configuration: object
    guard: object = None

    def check(self, stopped=lambda: False):
        _check(self.tree, self.configuration, stopped)
        if self.guard is not None:
            self.guard()
        if self.generation == 'fish':
            from . import fish_speech
            if fish_speech.status(self.tree, self.path.stem).get('cancelled'):
                raise InterruptedError('Speech cancelled')
            return
        ready = ipc.read(self.home / 'ready.json')
        if not ipc.generation(self.home, self.generation) or not _live(self.home, ready):
            raise InterruptedError('Speech service generation ended; no audio staged')


def synthesize(text, tree: Path, output_dir: Path, *, configuration,
               stopped=lambda: False, format='ogg_opus', timeout=300,
               selection=None, request_id=None) -> Audio:
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError('Speech expects 1..4000 characters')
    if format not in ('wav', 'ogg_opus'):
        raise ValueError('Unsupported speech output format')
    tree, output_dir = Path(tree), Path(output_dir)
    home = tree / 'memory/.state/xtts'
    deadline = time.monotonic() + timeout
    _check(tree, configuration, stopped)
    # Snapshot the selected settings at admission, not MediaAudio's boot env.
    admitted = configuration()
    chosen = dict(admitted.get('voice') or {})
    # Bind the full live configuration selection, even when preview overrides
    # unsaved fields. A config revision also fences an ABA switch back.
    def stamp(cfg):
        return json.dumps({'voice': cfg.get('voice'), 'epoch': cfg.get('_speech_epoch')}, sort_keys=True)
    initial = stamp(admitted)
    def guard():
        _check(tree, configuration, stopped)
        if stamp(configuration()) != initial:
            raise InterruptedError('Speech selection changed')
    if selection:
        chosen.update(selection)
    provider = str(chosen.get('voice') or 'xtts')
    if provider not in ('xtts', 'fish'):
        raise ValueError('Unsupported speech provider')
    if provider == 'fish':
        from . import fish_speech
        # A missing stable tool identity must never turn replay into a new bill.
        if request_id is None:
            raise ValueError('Fish requires a stable request identity')
        key = fish_speech.key_path(tree)
        try:key_stamp = key.stat()
        except OSError:raise RuntimeError('fish_key_unavailable') from None
        def fish_guard():
            guard()
            try:current = key.stat()
            except OSError:raise InterruptedError('Fish key removed') from None
            if (current.st_ino, current.st_mtime_ns, current.st_size) != (key_stamp.st_ino, key_stamp.st_mtime_ns, key_stamp.st_size):
                raise InterruptedError('Fish key changed')
        path = fish_speech.synthesize(text, tree, output_dir, chosen=chosen, check=fish_guard,
            format=format, timeout=timeout, request_id=request_id)
        audio = Audio(path, fish_speech.home(tree), tree, 'fish', configuration, fish_guard)
        try:
            audio.check(stopped)
            return audio
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    speaker = str(chosen.get('tts_speaker') or 'Ana Florence')
    language = str(chosen.get('tts_language') or 'ru')
    while True:
        guard()
        ready = ipc.read(home / 'ready.json')
        generation = ready.get('generation')
        if ready.get('ready') is True and ipc.identity(generation) and _live(home, ready):
            break
        if ready.get('phase')=='error' and _live(home,ready,require_ready=False):
            raise RuntimeError(ready.get('error') or 'XTTS voice preparation failed; correct the voice settings')
        if time.monotonic() >= deadline:
            raise TimeoutError('XTTS worker is not ready; install/enable XTTS and wait for model loading')
        time.sleep(.05)
    if format == 'ogg_opus' and ready.get('ogg_opus') is not True:
        raise RuntimeError('XTTS runtime has no Ogg/Opus encoder (libsndfile OPUS capability); voice note unavailable')
    reference = str(chosen.get('tts_reference') or '').strip()
    if not reference and speaker not in (ready.get('speakers') or []):
        raise ValueError('Выбери голос из каталога установленной модели')
    if language not in (ready.get('languages') or []):
        raise ValueError('Язык отсутствует в установленной модели')
    if reference:
        from .component_store import reference_path
        path = reference_path(tree,reference)
        if not path.is_file() or path.suffix.lower() != '.wav':
            raise ValueError('Свой образец должен быть существующим локальным WAV')
        reference = str(path.resolve())
    identity = uuid.uuid4().hex
    incoming, output = home / 'inbox', home / 'output'
    incoming.mkdir(parents=True, exist_ok=True)
    request = incoming / (identity + '.json')
    lease = incoming / (identity + '.lease')
    cancel = incoming / (identity + '.cancel')
    receipt = output / (identity + '.json')
    suffix = '.wav' if format == 'wav' else '.ogg'
    source = output / (identity + suffix)
    final = output_dir / (identity + suffix)
    partial = final.with_suffix('.part')
    audio = Audio(final, home, tree, generation, configuration, guard)
    succeeded = False
    try:
        audio.check(stopped)
        lease.touch()
        ipc.enqueue(request, {'request_id': identity, 'generation': generation,
                  'deadline': time.time() + max(0, deadline - time.monotonic()),
                  'text': text, 'speaker': speaker, 'language': language,
                  'reference': reference, 'format': format})
        while not receipt.exists():
            audio.check(stopped)
            if time.monotonic() >= deadline:
                raise TimeoutError('Speech timed out; request cancelled, no audio staged')
            # Worker may finish and remove its lease just before we observe the
            # receipt; touch rather than utime is safe for this unique identity.
            lease.touch()
            time.sleep(.05)
        result = ipc.read(receipt)
        if (result.get('request_id') != identity or result.get('generation') != generation
                or result.get('format', format) != format):
            raise RuntimeError('XTTS response identity mismatch')
        if not result.get('ok'):
            raise RuntimeError(result.get('error') or 'XTTS synthesis failed')
        if Path(str(result.get('path') or '')).resolve() != source.resolve() or not source.is_file():
            raise RuntimeError('XTTS output is unavailable')
        audio.check(stopped)
        output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, partial)
        audio.check(stopped)
        partial.replace(final)
        audio.check(stopped)
        succeeded = True
        return audio
    finally:
        if not succeeded:
            # Fences the worker even when it has already opened the request.
            cancel.touch()
            final.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
        ipc.unlink(request)
        source.unlink(missing_ok=True)
        ipc.unlink(receipt)
        if succeeded:
            ipc.unlink(lease)
