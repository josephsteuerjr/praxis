"""Light authenticated client for the optional resident XTTS capability."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import time
import uuid

from xtts_protocol import ERROR_CODES, HEX, MAX_AUDIO, MAX_REQUEST, SCHEMA, XttsError, encoded, file_digest


def selection_path():
    base = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
    return Path(os.environ.get("PRAXIS_XTTS_SELECTION_FILE") or base / "memory/.state/xtts/selection.json")


def selected():
    """An explicit durable option; missing retains the existing audio backend."""
    path = selection_path()
    if not path.exists() and not path.is_symlink():
        return None
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            raise XttsError("invalid_selection")
        value = json.loads(path.read_bytes())
        if (not isinstance(value, dict) or value.get("schema") != "praxis.xtts.selection.v1"
                or type(value.get("enabled")) is not bool):
            raise XttsError("invalid_selection")
        if not value["enabled"]:
            return None
        if any(not HEX.fullmatch(str(value.get(k) or "")) for k in ("model_id", "profile_id")):
            raise XttsError("invalid_selection")
        return value
    except (OSError, ValueError) as exc:
        raise XttsError("invalid_selection") from exc


class XttsTTSClient:
    skip_warmup = True

    def __init__(self, output_dir: Path, *, socket_path=None, token_file=None, timeout=300):
        self.output_dir = Path(output_dir)
        self.socket_path = str(socket_path or os.environ.get("PRAXIS_XTTS_SOCKET") or "/app/memory/.state/xtts/xtts.sock")
        self.token_file = Path(token_file or os.environ.get("PRAXIS_XTTS_TOKEN_FILE") or "/app/memory/.secrets/xtts-client.token")
        self.timeout = timeout

    def _request(self, value, *, stopped=lambda: False, audio_path=None, upload_path=None, response_limit=MAX_REQUEST, on_audio_created=lambda: None):
        started = time.monotonic()
        deadline = None if self.timeout is None else started + self.timeout
        try:
            token = self.token_file.read_text().strip()
            if not 32 <= len(token) <= 256:
                raise XttsError("invalid_token_file")
            request = encoded({"schema": SCHEMA, "token": token, **value}) + b"\n"
            if len(request) > MAX_REQUEST:
                raise XttsError("oversized_request")
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            with connection:
                connection.settimeout(min(2, self.timeout or 2))
                connection.connect(self.socket_path)
                connection.sendall(request)
                connection.settimeout(0.2)
                pending = bytearray()

                def check():
                    if stopped():
                        raise XttsError("cancelled")
                    if deadline is not None and time.monotonic() >= deadline:
                        raise XttsError("timeout")

                def receive():
                    while True:
                        check()
                        try:
                            raw = connection.recv(64 * 1024)
                        except socket.timeout:
                            continue
                        if not raw:
                            raise XttsError("worker_disconnected")
                        return raw

                def header():
                    nonlocal pending
                    while b'\n' not in pending:
                        pending.extend(receive())
                        if len(pending) > response_limit + 64 * 1024:
                            raise XttsError('invalid_response')
                    head, body = pending.split(b'\n', 1)
                    if len(head) > response_limit:
                        raise XttsError('invalid_response')
                    result = json.loads(head)
                    if not isinstance(result, dict) or result.get('schema') != SCHEMA:
                        raise XttsError('invalid_response')
                    if result.get('ok') is not True:
                        code = result.get('error')
                        raise XttsError(code if code in ERROR_CODES else 'synthesis_failed')
                    pending = bytearray(body)
                    return result, body

                result, body = header()
                if upload_path is not None:
                    if result.get('upload') is not True or body:
                        raise XttsError('invalid_response')
                    connection.settimeout(2)
                    with Path(upload_path).open('rb') as source:
                        for chunk in iter(lambda: source.read(64 * 1024), b''):
                            check()
                            connection.sendall(chunk)
                    connection.settimeout(.2)
                    result, body = header()
                if audio_path is None:
                    return result
                if result.get('stream') is True:
                    for key in ('request_id', 'model_id', 'profile_id'):
                        if result.get(key) != value.get(key):
                            raise XttsError('invalid_response')
                    if result.get('mime') != 'audio/ogg':
                        raise XttsError('invalid_response')
                    total = 0
                    checksum = hashlib.sha256()
                    with Path(audio_path).open('xb') as output:
                        on_audio_created()
                        while True:
                            part, _body = header()
                            if part.get('done') is True:
                                result = part
                                break
                            count = part.get('chunk_bytes')
                            if type(count) is not int or not 0 < count <= 64 * 1024:
                                raise XttsError('invalid_response')
                            while len(pending) < count:
                                pending.extend(receive())
                            chunk = bytes(pending[:count])
                            del pending[:count]
                            total += count
                            if total > MAX_AUDIO:
                                raise XttsError('oversized_audio')
                            checksum.update(chunk)
                            output.write(chunk)
                            output.flush()  # An explicitly admitted sender may read this prefix.
                        output.flush()
                        os.fsync(output.fileno())
                    if (not 32 < total <= MAX_AUDIO or result.get('bytes') != total
                            or result.get('sha256') != checksum.hexdigest() or pending
                            or result.get('mime') != 'audio/ogg'
                            or any(result.get(key) != value.get(key)
                                   for key in ('request_id', 'model_id', 'profile_id'))):
                        raise XttsError('audio_integrity')
                    with Path(audio_path).open('rb') as audio:
                        head = audio.read(4096)
                    if not head.startswith(b'OggS') or b'OpusHead' not in head:
                        raise XttsError('invalid_audio')
                    check()
                    return result
                size = result.get("bytes")
                if (type(size) is not int or not 32 < size <= MAX_AUDIO
                        or not HEX.fullmatch(str(result.get("sha256") or ""))
                        or result.get("request_id") != value.get("request_id")
                        or result.get("mime") != "audio/ogg"
                        or result.get("model_id") != value.get("model_id")
                        or result.get("profile_id") != value.get("profile_id")):
                    raise XttsError("invalid_response")
                total = 0
                checksum = hashlib.sha256()
                with Path(audio_path).open("xb") as output:
                    while total < size:
                        check()
                        chunk = bytes(body) if body else receive()
                        body = b""
                        if total + len(chunk) > size:
                            raise XttsError("oversized_audio")
                        output.write(chunk)
                        checksum.update(chunk)
                        total += len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if checksum.hexdigest() != result["sha256"]:
                    raise XttsError("audio_integrity")
                with Path(audio_path).open("rb") as audio:
                    head = audio.read(4096)
                if not head.startswith(b"OggS") or b"OpusHead" not in head:
                    raise XttsError("invalid_audio")
                check()
                return result
        except XttsError:
            raise
        except (OSError, ValueError, EOFError) as exc:
            raise XttsError("unavailable") from exc

    def health(self):
        control = XttsTTSClient(self.output_dir, socket_path=self.socket_path,
                                token_file=self.token_file, timeout=min(2, self.timeout or 2))
        return control._request({"operation": "health"})

    def cancel(self, identity):
        # Independent connection: inference never owns its own control channel.
        control = XttsTTSClient(self.output_dir, socket_path=self.socket_path,
                                token_file=self.token_file, timeout=2)
        try:
            control._request({"operation": "cancel", "request_id": identity})
        except XttsError:
            pass

    def synthesis_status(self, identity):
        control = XttsTTSClient(self.output_dir, socket_path=self.socket_path,
                                token_file=self.token_file, timeout=2)
        return control._request({'operation': 'request_status', 'request_id': identity})

    def synthesize_intent(self, intent, *, stopped=lambda: False, operation='synthesize'):
        self.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity = intent.get('attempt_id') or intent["request_id"]
        final = self.output_dir / ("xtts-" + uuid.uuid4().hex + ".ogg")
        partial = final.with_suffix(".part.ogg")
        try:
            result = self._request({"operation": operation, "request_id": identity,
                                    "text": intent["text"], "model_id": intent["model_id"],
                                    "profile_id": intent["profile_id"], "timeout": self.timeout,
                                    **({'voice_id':intent['voice_id']} if intent.get('voice_id') else {})},
                                   stopped=stopped, audio_path=partial)
            if stopped():
                raise XttsError("cancelled")
            partial.replace(final)
            result = {**result, 'request_id':intent['request_id'], 'transport_request_id':identity}
            return final, result
        except BaseException as exc:
            # A rejected busy request does not own the already active inference
            # with the same durable ID (e.g. concurrent restart/recovery).
            if not isinstance(exc, XttsError) or exc.code not in {"busy", "warming", "awaiting_voice", "profile_changed", "unauthorized"}:
                self.cancel(identity)
            final.unlink(missing_ok=True)
            raise
        finally:
            partial.unlink(missing_ok=True)

    def voices(self):
        control = XttsTTSClient(self.output_dir, socket_path=self.socket_path, token_file=self.token_file, timeout=min(3,self.timeout or 3))
        return control._request({'operation':'voices'}, response_limit=1024*1024)

    def stream_intent(self, intent, destination, *, stopped=lambda: False, operation='synthesize_stream'):
        """Write a private growing OGG; the caller publishes it only after final integrity."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists() or destination.is_symlink():
            raise XttsError('generation_conflict')
        identity = intent.get('attempt_id') or intent['request_id']
        owned = False

        def created():
            nonlocal owned
            owned = True

        try:
            result = self._request({
                'operation': operation, 'request_id': identity, 'text': intent['text'],
                'model_id': intent['model_id'], 'profile_id': intent['profile_id'],
                'timeout': self.timeout,
                **({'voice_id': intent['voice_id']} if intent.get('voice_id') else {}),
            }, stopped=stopped, audio_path=destination, on_audio_created=created)
            return {**result, 'request_id': intent['request_id'], 'transport_request_id': identity}
        except BaseException as exc:
            if not isinstance(exc, XttsError) or exc.code not in {'busy', 'warming', 'awaiting_voice', 'profile_changed', 'unauthorized'}:
                self.cancel(identity)
            if owned:
                destination.unlink(missing_ok=True)
            raise

    def command(self, operation, values=None, *, stopped=lambda:False, request_id=None, actor='operator'):
        return self._request({'operation':operation,'request_id':request_id or uuid.uuid4().hex,
                              'timeout':self.timeout, 'actor':actor, **(values or {})}, stopped=stopped)

    def add_voice(self, path, name, *, stopped=lambda:False, request_id=None, actor='operator',attempt_id=None):
        from xtts_profiles import MAX_REFERENCE
        path=Path(path)
        if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=MAX_REFERENCE:
            raise XttsError('reference_too_large')
        canonical=request_id or uuid.uuid4().hex
        identity=attempt_id or canonical
        try:
            return self._request({'operation':'voice_add','request_id':identity,'voice_key_id':canonical,'name':name,
                                  'bytes':path.stat().st_size,'sha256':file_digest(path),
                                  'timeout':self.timeout,'actor':actor},
                                 stopped=stopped,upload_path=path)
        except BaseException as exc:
            if not isinstance(exc,XttsError) or exc.code!='busy':self.cancel(identity)
            raise

    def preview(self, voice, text, *, stopped=lambda:False, request_id=None):
        intent={'request_id':request_id or uuid.uuid4().hex, 'text':text,
                'voice_id':voice['id'],'profile_id':voice['profile_id'],'model_id':voice['model_id']}
        return self.synthesize_intent(intent,stopped=stopped,operation='preview')

    def synthesize(self, text):
        raise XttsError("use_async_speech_intent")

    def clear_cache(self):
        # Resident weights belong to the independent service, not this client.
        pass
