"""Optional Fish REST producer. No Telegram, model loading, fallback or retries.

This module does not select itself as the active voice backend. Its
stream_intent interface fits the existing generation store; provider selection
and migration are separate from installing this additive capability.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import struct
import urllib.error
import urllib.parse
import urllib.request

from xtts_protocol import MAX_AUDIO, XttsError, digest, validate_intent

API = 'https://api.fish.audio'
# Reject typos locally: Fish silently substitutes a paid model for unknown names.
MODELS = ('s2.1-pro', 's2.1-pro-free', 's2-pro', 'drama-3', 's1')


class FishError(XttsError):
    """Stable text-free errors; remote bodies and credentials are never returned."""


@dataclass(frozen=True)
class FishProfile:
    reference_id: str
    model: str = 's2.1-pro'
    language: str | None = 'ru'
    speed: float = 1.0
    normalize: bool = True

    def __post_init__(self):
        if not isinstance(self.reference_id, str) or not self.reference_id.strip():
            raise FishError('fish_voice_not_selected')
        if self.model not in MODELS:
            raise FishError('fish_invalid_model')
        if (self.language is not None and
                (not isinstance(self.language, str) or len(self.language) > 35 or
                 re.fullmatch(r'[A-Za-z0-9_-]*', self.language) is None)):
            raise FishError('fish_invalid_language')
        if (type(self.speed) not in (int, float) or not math.isfinite(self.speed)
                or not 0.5 <= self.speed <= 2 or type(self.normalize) is not bool):
            raise FishError('fish_invalid_profile')
        # One canonical representation prevents 1 vs 1.0 from changing identity.
        object.__setattr__(self, 'speed', float(self.speed))

    @classmethod
    def load(cls, path):
        try:
            return cls(**json.loads(Path(path).read_text(encoding='utf-8')))
        except FishError:
            raise
        except (OSError, ValueError, TypeError):
            raise FishError('fish_invalid_profile') from None

    @property
    def model_id(self):
        return digest({'provider': 'fish', 'endpoint': API, 'model': self.model})

    @property
    def profile_id(self):
        return digest({'schema': 'praxis.fish.profile.v1', **asdict(self)})

    def selection(self):
        return {'provider': 'fish', 'model_id': self.model_id,
                'profile_id': self.profile_id}


def default_key_file():
    base = Path(os.environ.get('PRAXIS_BASE') or Path(__file__).resolve().parent)
    return Path(os.environ.get('PRAXIS_FISH_KEY_FILE') or
                base / 'memory/.secrets/fish/api-key')


def render_speech(text, delivery=''):
    """Spoken script and performance direction remain distinct in the request."""
    if not isinstance(delivery, str):
        raise FishError('fish_invalid_delivery')
    return ('[' + delivery + '] ' if delivery else '') + text


def read_key(path):
    """Read a regular private file, checking the opened descriptor, not a racey stat."""
    try:
        path = Path(path)
        if path.is_symlink():
            raise FishError('fish_invalid_key_file')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 4096:
                raise FishError('fish_invalid_key_file')
            if os.name == 'posix' and (info.st_mode & 0o077 or info.st_uid != os.geteuid()):
                raise FishError('fish_insecure_key_file')
            key = source.read(4097).decode('ascii').strip()
        if not key or len(key) > 4096 or any(not 33 <= ord(c) <= 126 for c in key):
            raise FishError('fish_invalid_key_file')
        return key
    except FishError:
        raise
    except (OSError, UnicodeError):
        raise FishError('fish_key_unavailable') from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the API key to a redirect target.
        return None


def opus_seconds(path):
    """Check complete single-stream Ogg framing and its Opus granule duration.

    This is a container check, not a substitute for real decoder acceptance.
    """
    serial = None
    sequence = 0
    preskip = None
    granule = None
    ended = False
    with Path(path).open('rb') as source:
        while True:
            header = source.read(27)
            if not header:
                break
            if (ended or len(header) != 27 or header[:5] != b'OggS\x00'
                    or header[5] & ~7):
                raise FishError('invalid_audio')
            flags = header[5]
            position, page_serial, page_sequence = struct.unpack_from('<QII', header, 6)
            if sequence == 0:
                if flags != 2:
                    raise FishError('invalid_audio')
                serial = page_serial
            elif page_serial != serial or flags & 2:
                raise FishError('invalid_audio')
            if page_sequence != sequence:
                raise FishError('invalid_audio')
            laces = source.read(header[26])
            if len(laces) != header[26]:
                raise FishError('invalid_audio')
            body = source.read(sum(laces))
            if len(body) != sum(laces):
                raise FishError('invalid_audio')
            if sequence == 0:
                if len(body) < 19 or not body.startswith(b'OpusHead') or body[9] != 1:
                    raise FishError('invalid_audio')
                preskip = struct.unpack_from('<H', body, 10)[0]
            if position != 0xffffffffffffffff:
                if granule is not None and position < granule:
                    raise FishError('invalid_audio')
                granule = position
            ended = bool(flags & 4)
            sequence += 1
    if (not ended or preskip is None or granule is None or granule <= preskip):
        raise FishError('invalid_audio')
    return (granule - preskip) / 48000


class FishTTSClient:
    skip_warmup = True

    def __init__(self, output_dir, profile=None, *, key_file=None, timeout=30, opener=None):
        self.output_dir = Path(output_dir)
        self.profile = profile
        self.key_file = Path(key_file) if key_file is not None else default_key_file()
        # Socket I/O timeout; no overall generation deadline is imposed.
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener(_NoRedirect())

    def _request(self, path, *, body=None, model=None):
        raw = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        headers = {'Authorization': 'Bearer ' + read_key(self.key_file),
                   'Accept': 'application/json' if body is None else 'audio/ogg',
                   'User-Agent': 'Praxis-Fish/1'}
        if raw is not None:
            headers['Content-Type'] = 'application/json'
        if model is not None:
            if model not in MODELS:
                raise FishError('fish_invalid_model')
            headers['model'] = model
        request = urllib.request.Request(API + path, data=raw, headers=headers)
        return self.opener.open(request, timeout=self.timeout)

    @staticmethod
    def _failure(exc):
        if isinstance(exc, urllib.error.HTTPError):
            code = {400: 'fish_invalid_request', 401: 'unauthorized', 402: 'fish_no_api_credit',
                    403: 'fish_forbidden', 404: 'fish_not_found',
                    422: 'fish_invalid_request', 429: 'fish_rate_limited'}.get(exc.code)
            exc.close()
            # The provider may have accepted a paid request before its failure.
            # Existing Generations maps worker_disconnected to unknown, not retry.
            return FishError(code or 'worker_disconnected')
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return FishError('timeout')
        return FishError('worker_disconnected')

    def _json(self, path):
        try:
            with self._request(path) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise FishError('fish_invalid_response')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise FishError('fish_invalid_response')
            return value
        except FishError:
            raise
        except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
            raise self._failure(exc) from None
        except (ValueError, UnicodeError):
            raise FishError('fish_invalid_response') from None

    def balance(self):
        value = self._json('/wallet/self/api-credit')
        credit = value.get('credit')
        if not isinstance(credit, str):
            raise FishError('fish_invalid_response')
        # Keep documented raw units, without inventing a USD conversion.
        return {'provider': 'fish', 'api_credit_raw': credit}

    def voices(self, *, own=True, language=None, page=1):
        if type(page) is not int or page < 1:
            raise FishError('fish_invalid_request')
        query = {'self': str(own).lower(), 'page_number': page, 'page_size': 20}
        if language:
            query['language'] = language
        value = self._json('/model?' + urllib.parse.urlencode(query))
        items = value.get('items')
        if not isinstance(items, list) or any(not isinstance(v, dict) for v in items):
            raise FishError('fish_invalid_response')
        return {'voices': [{k: v[k] for k in ('_id', 'title', 'languages', 'visibility') if k in v}
                           for v in items],
                'page': page, 'has_more': value.get('has_more')}

    def stream_intent(self, intent, destination, *, stopped=lambda: False):
        request = validate_intent(intent)
        profile = self.profile
        if profile is None:
            raise FishError('fish_voice_not_selected')
        if (request['model_id'] != profile.model_id or request['profile_id'] != profile.profile_id):
            raise FishError('profile_changed')
        destination = Path(destination)
        if destination.exists() or destination.is_symlink():
            raise FishError('generation_conflict')
        if stopped():
            raise FishError('cancelled')
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        provider_text = render_speech(request['text'], request.get('delivery', ''))
        body = {'text': provider_text, 'reference_id': profile.reference_id,
                'format': 'opus', 'sample_rate': 48000, 'opus_bitrate': 48000,
                'normalize': profile.normalize, 'latency': 'normal',
                'prosody': {'speed': profile.speed, 'volume': 0, 'normalize_loudness': True}}
        if profile.language is not None:
            body['language'] = profile.language
        owned = False
        total = 0
        checksum = hashlib.sha256()
        try:
            # Exclusive reservation precedes POST, avoiding a charge on collision.
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            owned = True
            with os.fdopen(fd, 'wb') as output:
                with self._request('/v1/tts', body=body, model=profile.model) as response:
                    while True:
                        if stopped():
                            raise FishError('cancelled')
                        chunk = response.read(16 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_AUDIO:
                            raise FishError('oversized_audio')
                        output.write(chunk)
                        checksum.update(chunk)
                        output.flush()
                output.flush()
                os.fsync(output.fileno())
            if stopped():
                raise FishError('cancelled')
            seconds = opus_seconds(destination)
            return {'request_id': request['request_id'], 'model_id': profile.model_id,
                    'profile_id': profile.profile_id, 'provider': 'fish',
                    'provider_model': profile.model, 'reference_id': profile.reference_id,
                    'mime': 'audio/ogg', 'bytes': total, 'sha256': checksum.hexdigest(),
                    'audio_seconds': seconds, 'input_utf8_bytes': len(provider_text.encode('utf-8')),
                    'provider_input_sha256': hashlib.sha256(provider_text.encode('utf-8')).hexdigest()}
        except BaseException as exc:
            if owned:
                destination.unlink(missing_ok=True)
            if isinstance(exc, (FishError, XttsError)):
                raise
            if isinstance(exc, FileExistsError):
                raise FishError('generation_conflict') from None
            if isinstance(exc, (OSError, urllib.error.URLError, http.client.HTTPException)):
                raise self._failure(exc) from None
            raise

    def cancel(self, identity):
        # Closing our response stops local reads; Fish documents no remote cancel.
        return {'active': None, 'cancel_requested': None, 'remote_cancel_supported': False}

    def synthesis_status(self, identity):
        # Never turn a lost HTTP response into proof that remote synthesis stopped.
        return {'active': None, 'cancel_requested': None, 'remote_status_supported': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-file', type=Path, default=default_key_file())
    parser.add_argument('--profile', type=Path)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('balance')
    voices = sub.add_parser('voices')
    voices.add_argument('--public', action='store_true')
    voices.add_argument('--language')
    voices.add_argument('--page', type=int, default=1)
    # Deliberately no synthesis CLI: paid generation must carry canonical origin.
    args = parser.parse_args(argv)
    try:
        profile = FishProfile.load(args.profile) if args.profile else None
        client = FishTTSClient(Path.cwd(), profile, key_file=args.key_file)
        result = (client.balance() if args.action == 'balance' else
                  client.voices(own=not args.public, language=args.language, page=args.page))
        print(json.dumps({'ok': True, **result}, ensure_ascii=False))
        return 0
    except FishError as exc:
        print(json.dumps({'ok': False, 'error': exc.code}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
