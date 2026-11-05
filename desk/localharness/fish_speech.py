"""Application-owned Fish transport and durable, single-attempt paid admission.

No worker, model weights, environment key, retry, fallback or message delivery.
Unknown is deliberately terminal: Fish provides no remote reconciliation API.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import stat
import struct
import time
import urllib.error
import urllib.request
import wave

from . import speech_protocol as ipc

API = 'https://api.fish.audio/v1/tts'
MODELS = ('s2.1-pro', 's2.1-pro-free', 's2-pro', 'drama-3', 's1')
MAX_AUDIO = 24 * 1024 * 1024


def _private(path, data):
    # Supplied by the updater. Absence must fail closed, never write a secret
    # via a weaker local replacement. Tests explicitly inject this boundary.
    try:
        from .private_files import write_private
        write_private(Path(path), data)
        from .server_components import share_fish
        share_fish(path)
    except (ImportError, OSError, RuntimeError, ValueError):
        raise RuntimeError('fish_private_storage_unavailable') from None


def key_path(tree):
    return Path(tree) / 'memory/.secrets/fish/api-key'


def _plain(path):
    for entry in (path, *path.parents):
        if entry.is_symlink() or getattr(entry, 'is_junction', lambda: False)():
            raise ValueError('fish_linked_private_path')
    return path


def configure_key(tree, value):
    path = _plain(key_path(tree))
    if value is None:
        path.unlink(missing_ok=True)
    else:
        if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 4096
                or any(not 33 <= ord(c) <= 126 for c in value.strip())):
            raise ValueError('fish_invalid_key')
        _private(path, value.strip().encode('ascii'))
    return {'configured': path.is_file()}


def _key(tree):
    try:
        path = _plain(key_path(tree))
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            from .server_components import shared_key
            if (not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 4096
                    or (os.name == 'posix' and not shared_key(info)
                        and (info.st_mode & 0o077 or info.st_uid != os.geteuid()))):
                raise ValueError()
            value = source.read(4097).decode('ascii').strip()
        if not value or any(not 33 <= ord(c) <= 126 for c in value):
            raise ValueError()
        return value
    except (OSError, ValueError, UnicodeError):
        raise RuntimeError('fish_key_unavailable') from None


def profile(voice):
    model = str(voice.get('fish_model') or '')
    reference = str(voice.get('fish_voice') or '').strip()
    delivery = str(voice.get('fish_delivery') or '').strip()
    if model not in MODELS:
        raise ValueError('fish_select_explicit_model')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', reference):
        raise ValueError('fish_select_voice_id')
    if len(delivery) > 1000:
        raise ValueError('fish_delivery_too_long')
    return {'model': model, 'reference_id': reference, 'delivery': delivery}


def home(tree):
    return _plain(Path(tree) / 'memory/.state/fish-speech')


def _identity(value):
    if not ipc.identity(value):
        raise ValueError('fish_invalid_request_id')
    return value


def status(tree, identity):
    identity = _identity(identity)
    value = ipc.read(home(tree) / (identity + '.json'))
    return {k: value[k] for k in ('request_id', 'status', 'cancelled', 'error') if k in value}


def latest(tree):
    value=ipc.read(home(tree)/'last-intent.json')
    identity=value.get('request_id')
    return status(tree,identity) if ipc.identity(identity) else None


def cancel(tree, identity):
    identity = _identity(identity)
    folder = home(tree); folder.mkdir(parents=True, exist_ok=True)
    with ipc._queue_lock(folder):
        path = folder / (identity + '.json')
        value = ipc.read(path)
        # A cancel arriving before admission must also fence its future POST.
        value.update(request_id=identity, cancelled=True)
        _private(path, json.dumps(value).encode())
    return status(tree, identity)


def _finish(tree, identity, **fields):
    folder = home(tree)
    with ipc._queue_lock(folder):
        path = folder / (identity + '.json')
        value = ipc.read(path)
        value.update(fields)
        _private(path, json.dumps(value).encode())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def transport(body, key, model, timeout, output, check):
    """Exactly one HTTP POST. Untrusted response bodies never enter errors/logs."""
    request = urllib.request.Request(API, data=json.dumps(body, ensure_ascii=False).encode(),
        headers={'Authorization': 'Bearer ' + key, 'model': model,
                 'Content-Type': 'application/json', 'Accept': 'audio/wav' if body['format'] == 'wav' else 'audio/ogg'})
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        check()
        with opener.open(request, timeout=min(30, max(1, timeout))) as response:
            size = 0
            with output.open('xb') as stream:
                while True:
                    check()
                    chunk = response.read(16 * 1024)
                    if not chunk: break
                    size += len(chunk)
                    if size > MAX_AUDIO: raise RuntimeError('fish_audio_too_large')
                    stream.write(chunk)
                stream.flush(); os.fsync(stream.fileno())
        check()
    except InterruptedError:
        raise
    except urllib.error.HTTPError as exc:
        code = exc.code; exc.close()
        # Only definitive validation/auth/credit refusals are known rejections.
        if code in (400, 401, 402, 403, 404, 422, 429):
            raise Rejected('fish_http_' + str(code)) from None
        raise RuntimeError('fish_unknown') from None
    except (OSError, urllib.error.URLError, http.client.HTTPException):
        raise RuntimeError('fish_unknown') from None


class Rejected(RuntimeError):
    pass


def validate_audio(path, format):
    if not 0 < path.stat().st_size <= MAX_AUDIO:
        raise ValueError('fish_invalid_audio')
    if format == 'wav':
        try:
            with wave.open(str(path), 'rb') as audio:
                count = audio.getnframes()
                if (audio.getnchannels() != 1 or audio.getsampwidth() != 2 or count <= 0
                        or len(audio.readframes(count)) != count * 2):
                    raise ValueError('fish_invalid_audio')
        except (wave.Error, EOFError):
            raise ValueError('fish_invalid_audio') from None
    else:
        # Complete single-stream Ogg/Opus framing, including end-of-stream.
        sequence = 0; serial = None; granule = 0; preskip = None; ended = False
        with path.open('rb') as source:
            while header := source.read(27):
                if ended or len(header) != 27 or header[:5] != b'OggS\x00' or header[5] & ~7:
                    raise ValueError('fish_invalid_audio')
                position, page_serial, page_sequence = struct.unpack_from('<QII', header, 6)
                laces = source.read(header[26]); body = source.read(sum(laces))
                if len(laces) != header[26] or len(body) != sum(laces) or page_sequence != sequence:
                    raise ValueError('fish_invalid_audio')
                if sequence == 0:
                    if header[5] != 2 or len(body) < 19 or not body.startswith(b'OpusHead') or body[9] != 1:
                        raise ValueError('fish_invalid_audio')
                    serial = page_serial; preskip = struct.unpack_from('<H', body, 10)[0]
                elif page_serial != serial or header[5] & 2:
                    raise ValueError('fish_invalid_audio')
                if position != 0xffffffffffffffff:
                    if position < granule: raise ValueError('fish_invalid_audio')
                    granule = position
                ended = bool(header[5] & 4); sequence += 1
        if not ended or preskip is None or granule <= preskip:
            raise ValueError('fish_invalid_audio')


def synthesize(text, tree, output_dir, *, chosen, check, format, timeout, request_id):
    identity = _identity(request_id)
    selected = profile(chosen)
    body = {'text': ('[' + selected['delivery'] + '] ' if selected['delivery'] else '') + text,
            'reference_id': selected['reference_id'], 'format': 'wav' if format == 'wav' else 'opus',
            'normalize': True, 'latency': 'normal'}
    fingerprint = hashlib.sha256(json.dumps({'body': body, 'model': selected['model']},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    key = _key(tree)  # Read BEFORE reserving, but never persist credentials in a receipt.
    folder = home(tree); folder.mkdir(parents=True, exist_ok=True)
    receipt = folder / (identity + '.json')
    with ipc._queue_lock(folder):
        if receipt.exists():
            old = ipc.read(receipt)
            if old.get('fingerprint') not in (None, fingerprint):
                raise RuntimeError('fish_intent_conflict')
            raise RuntimeError('fish_intent_' + str(old.get('status') or 'cancelled') + '; no automatic retry')
        check()
        _private(receipt, json.dumps({'request_id': identity, 'fingerprint': fingerprint,
            'status': 'unknown', 'cancelled': False}).encode())
        _private(folder/'last-intent.json',json.dumps({'request_id':identity}).encode())
    suffix = '.wav' if format == 'wav' else '.ogg'
    output_dir.mkdir(parents=True, exist_ok=True)
    final = output_dir / (identity + suffix); partial = output_dir / (identity + '.part')
    deadline = time.monotonic() + timeout
    def guarded():
        check()
        if status(tree, identity).get('cancelled'):
            raise InterruptedError('Speech cancelled')
        if time.monotonic() >= deadline: raise TimeoutError('fish_unknown')
    success = False
    try:
        guarded()
        transport(body, key, selected['model'], timeout, partial, guarded)
        validate_audio(partial, format)
        guarded()
        partial.replace(final)
        _finish(tree, identity, status='completed')
        guarded(); success = True
        return final
    except Rejected as exc:
        _finish(tree, identity, status='rejected', error=str(exc))
        raise
    except InterruptedError:
        _finish(tree, identity, cancelled=True)
        raise
    except (OSError, ValueError, RuntimeError, TimeoutError):
        _finish(tree, identity, error='fish_unknown')
        raise RuntimeError('fish_unknown; request ' + identity + '; no automatic retry') from None
    finally:
        partial.unlink(missing_ok=True)
        if not success: final.unlink(missing_ok=True)
