"""Server-owned speech generation records; no Telegram delivery or model imports.

The original cognitive turn owns provenance, not the lifetime of the producer.
An audio artifact is not an outbound intent. Only send_voice creates one in the
existing media spool. Records have no implicit overall deadline or result TTL.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

from xtts_protocol import MAX_AUDIO, MAX_REQUEST, REQUEST_ID, XttsError, encoded, file_digest, validate_intent

SCHEMA = 'praxis.voice-generation.v1'
TERMINAL = frozenset({'ready', 'failed', 'cancelled', 'unknown'})


def default_root():
    base = Path(os.environ.get('PRAXIS_BASE') or Path(__file__).resolve().parent)
    return base / 'memory/.state/xtts/generations'


def process_owner():
    value = {'pid': os.getpid()}
    if os.name == 'posix':
        value['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        value['start_ticks'] = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
    return value


def owner_alive(owner):
    from process_liveness import is_process_alive
    pid = owner.get('pid')
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == 'posix':
        try:
            if owner.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
                return False
            start = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[19]
            if owner.get('start_ticks') != start:
                return False
        except (OSError, IndexError):
            return False
    return is_process_alive(pid)


class Generations:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else default_root()
        self.lock = threading.RLock()

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise XttsError('invalid_generation_store')
        with self.lock, (self.root / '.lock').open('a+b') as lock:
            if os.name == 'posix':
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == 'posix':
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def directory(self, identity):
        if not REQUEST_ID.fullmatch(str(identity or '')):
            raise XttsError('invalid_generation_id')
        path = self.root / identity
        if path.is_symlink():
            raise XttsError('invalid_generation_store')
        return path

    def _read(self, identity):
        path = self.directory(identity) / 'generation.json'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REQUEST:
            raise XttsError('unknown_generation')
        try:
            value = json.loads(path.read_bytes())
            if value.get('schema') != SCHEMA or value.get('generation_id') != identity:
                raise XttsError('invalid_generation_store')
            validate_intent(value['request'])
            return value
        except (ValueError, KeyError, TypeError) as exc:
            raise XttsError('invalid_generation_store') from exc

    def read(self, identity):
        with self.locked():
            return self._read(identity)

    def _write(self, value):
        directory = self.directory(value['generation_id'])
        directory.mkdir(mode=0o700, exist_ok=True)
        value = {**value, 'updated_at': time.time()}
        temporary = directory / ('.generation-' + uuid.uuid4().hex)
        try:
            with temporary.open('xb') as output:
                output.write(encoded(value))
                output.flush()
                os.fsync(output.fileno())
            temporary.chmod(0o600)
            temporary.replace(directory / 'generation.json')
            event = {key: value.get(key) for key in
                     ('schema', 'generation_id', 'state', 'updated_at', 'error')}
            with (directory / 'events.jsonl').open('ab') as output:
                output.write(encoded(event) + b'\n')
                output.flush()
                os.fsync(output.fileno())
            if os.name == 'posix':
                fd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return value
        finally:
            temporary.unlink(missing_ok=True)

    def submit(self, text, *, run_id, call_id, channel, selection, delivery=""):
        if not run_id or not call_id:
            raise XttsError('missing_generation_provenance')
        if not isinstance(delivery, str):
            raise XttsError('fish_invalid_delivery')
        if delivery and selection.get('provider') != 'fish':
            raise XttsError('expressive_speech_requires_fish')
        identity = hashlib.sha256(encoded({'run_id': run_id, 'call_id': call_id})).hexdigest()[:32]
        request = validate_intent({
            'schema': 'praxis.xtts.v1', 'request_id': identity, 'run_id': run_id,
            'text': text, 'model_id': selection.get('model_id'),
            'profile_id': selection.get('profile_id'),
            'target_chat_id': str(channel['chat_id']), 'scope': channel['scope'],
            **({'provider': 'fish', 'delivery': delivery} if selection.get('provider') == 'fish' else {}),
        })
        immutable = {'request': request, 'call_id': call_id, 'channel': dict(channel)}
        with self.locked():
            path = self.directory(identity) / 'generation.json'
            if path.exists():
                previous = self._read(identity)
                if any(previous.get(key) != value for key, value in immutable.items()):
                    raise XttsError('generation_conflict')
                return previous
            return self._write({
                'schema': SCHEMA, 'generation_id': identity, **immutable,
                'state': 'queued', 'created_at': time.time(), 'error': '',
                'attempt_id': '', 'owner': {},
            })

    def update(self, identity, state, *, expected=(), **fields):
        if state not in TERMINAL | {'queued', 'running', 'paused', 'cancel_requested'}:
            raise XttsError('invalid_generation_state')
        with self.locked():
            value = self._read(identity)
            if expected and value['state'] not in expected:
                return None
            if value['state'] != state:
                fields.setdefault('result_event_created', False)
            return self._write({**value, **fields, 'state': state})

    def cancel(self, identity):
        with self.locked():
            value = self._read(identity)
            if value['state'] in TERMINAL and not (value['state'] == 'unknown' and value.get('attempt_id')):
                return value
            state = ('cancel_requested' if value['state'] in {'running', 'cancel_requested', 'unknown'}
                     else 'cancelled')
            return self._write({**value, 'state': state, 'result_event_created': False})

    def list(self, states=None):
        with self.locked():
            rows = []
            for directory in sorted(self.root.iterdir()):
                if not REQUEST_ID.fullmatch(directory.name):
                    continue
                value = self._read(directory.name)
                if states is None or value['state'] in states:
                    rows.append(value)
            return sorted(rows, key=lambda item: item['created_at'])

    def audio_path(self, identity):
        return self.directory(identity) / 'audio.ogg'

    def complete(self, identity, metadata):
        with self.locked():
            value = self._read(identity)
            if value['state'] == 'cancel_requested':
                return self._write({**value, 'state': 'cancelled'})
            if value['state'] != 'running':
                raise XttsError('generation_not_running')
            request = value['request']
            if request.get('provider') == 'fish' and metadata.get('provider') != 'fish':
                raise XttsError('generation_integrity')
            path = self.audio_path(identity)
            if (path.is_symlink() or not path.is_file() or not 32 < path.stat().st_size <= MAX_AUDIO
                    or metadata.get('bytes') != path.stat().st_size
                    or metadata.get('sha256') != file_digest(path)
                    or metadata.get('model_id') != request['model_id']
                    or metadata.get('profile_id') != request['profile_id']
                    or metadata.get('request_id') != identity):
                raise XttsError('generation_integrity')
            with path.open('rb') as audio:
                head = audio.read(4096)
            if not head.startswith(b'OggS') or b'OpusHead' not in head:
                raise XttsError('invalid_audio')
            path.chmod(0o600)
            return self._write({**value, 'state': 'ready', 'result': {
                key: metadata[key] for key in ('bytes', 'sha256', 'mime', 'model_id',
                                              'profile_id', 'audio_seconds', 'provider', 'provider_model',
                                              'reference_id', 'input_utf8_bytes', 'provider_input_sha256') if key in metadata}})

    def observation(self, value):
        result = {key: value[key] for key in ('generation_id', 'state', 'created_at', 'updated_at')}
        result.update({'run_id': value['request']['run_id'], 'call_id': value['call_id'],
                       'delivery': 'not_requested_by_generation'})
        if value.get('error'):
            result['error'] = value['error']
        if value['request'].get('provider') == 'fish':
            result['provider'] = 'fish'
            result['performance_direction'] = value['request'].get('delivery', '')
        if value['state'] == 'ready':
            result['audio'] = {**value['result'], 'path': str(self.audio_path(value['generation_id']))}
        return result

    def execute(self, identity, client, *, owner):
        """One producer attempt. Normal completion of the originating run is irrelevant."""
        attempt = uuid.uuid4().hex
        with self.locked():
            previous = self._read(identity)
            if previous['state'] not in {'queued', 'paused'} or time.time() < previous.get('retry_at', 0):
                return previous
            value = self._write({**previous, 'state': 'running', 'owner': dict(owner),
                                 'attempt_id': attempt, 'error': ''})

        def stopped():
            current = self.read(identity)
            return current['state'] != 'running' or current['attempt_id'] != attempt

        try:
            result = client.stream_intent({**value['request'], 'attempt_id': attempt},
                                        self.audio_path(identity), stopped=stopped)
            return self.complete(identity, result)
        except Exception as exc:
            code = exc.code if isinstance(exc, XttsError) else type(exc).__name__
            current = self.read(identity)
            if current['attempt_id'] != attempt:
                return current
            if current['state'] == 'cancel_requested' or code in {'cancelled', 'voice_disabled'}:
                # A closed local socket is not proof that the resident model stopped.
                state = 'cancel_requested'
            elif code in {'busy', 'warming', 'awaiting_voice'}:
                state = 'queued'
            elif code in {'unavailable', 'worker_disconnected', 'timeout', 'stream_disconnected'}:
                state = 'unknown'
            else:
                state = 'failed'
            return self.update(identity, state, expected={'running', 'cancel_requested'},
                               error=code, retry_at=time.time() + 5)

    def emit_result(self, identity):
        """Best-effort typed tool event; never a human chat message or send command."""
        from core import events
        with self.locked():
            value = self._read(identity)
            if value['state'] not in TERMINAL or value.get('result_event_created'):
                return
            event = events.emit('voice_generation_result', value['request'].get('provider', 'xtts'), self.observation(value),
                                dedup_key='voice-generation:' + identity + ':' + value['state'])
            if event is not None:
                self._write({**value, 'result_event_created': True})

    def recover_lost(self):
        """A dead producer is an unknown result, never a fabricated successful replay."""
        with self.locked():
            recovered = []
            for directory in sorted(self.root.iterdir()):
                if not REQUEST_ID.fullmatch(directory.name):
                    continue
                value = self._read(directory.name)
                # Cancellation remains an outstanding capability command even
                # after its producer dies. The clock must still send it and
                # confirm quiescence; producer loss cannot erase that request.
                if value['state'] == 'running' and not owner_alive(value.get('owner') or {}):
                    recovered.append(self._write({**value, 'state': 'unknown', 'error': 'executor_lost'}))
            return recovered
