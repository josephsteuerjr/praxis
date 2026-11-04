"""Private voice profiles and reference audio. No brain, delivery or model imports."""
from __future__ import annotations

from contextlib import contextmanager
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
import wave

from xtts_protocol import SCHEMA, XttsError, digest, encoded, file_digest

MAX_REFERENCE = 8 * 1024 * 1024
VOICE_ID = re.compile(r"(?:builtin-[0-9a-f]{20}|clone-[0-9a-f]{32})\Z")


def write_config(path: Path, value: dict, *, actor: str, operation_id=None):
    value=dict(value)
    if value.get('schema')=='praxis.xtts.selection.v1':value.setdefault('revision',uuid.uuid4().hex)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    event = {"schema": "praxis.xtts.config-event.v1", "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
             "actor": actor, "file": path.name, "sha256": digest(value)}
    if operation_id:event['operation_id']=operation_id
    history = path.parent / "config-events.jsonl"
    with history.open("ab") as stream:
        stream.write(encoded({**event, "kind": "config_intent"}) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
        descriptor = os.open(str(path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        with history.open("ab") as stream:
            stream.write(encoded({**event, "kind": "config_applied"}) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        temporary.unlink(missing_ok=True)


def audio_magic(path: Path):
    with path.open("rb") as stream:
        head = stream.read(64)
    return (head.startswith((b"OggS", b"ID3", b"fLaC", b"\x1aE\xdf\xa3"))
            or (head.startswith(b"RIFF") and head[8:12] == b"WAVE")
            or head[4:8] == b"ftyp" or (len(head) > 2 and head[0] == 255 and head[1] & 224 == 224))


def decode_reference(source: Path, destination: Path, stopped):
    if not source.is_file() or not 0 < source.stat().st_size <= MAX_REFERENCE or not audio_magic(source):
        raise XttsError("invalid_reference")
    import imageio_ffmpeg
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-v", "error", "-y",
               "-protocol_whitelist", "file,pipe", "-i", str(source), "-t", "61",
               "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(destination)]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 40
        while process.poll() is None:
            if stopped() or time.monotonic() > deadline:
                raise XttsError("cancelled" if stopped() else "reference_decode_timeout")
            time.sleep(.05)
        if process.returncode:
            raise XttsError("invalid_reference")
        try:
            with wave.open(str(destination), "rb") as audio:
                seconds = audio.getnframes() / audio.getframerate()
                if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 24000:
                    raise XttsError("invalid_reference")
        except (OSError, wave.Error) as exc:
            raise XttsError("invalid_reference") from exc
        if not 3 <= seconds <= 60:
            raise XttsError("reference_duration")
        return seconds
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


class VoiceStore:
    def __init__(self, prefix: Path, state: Path, *, decoder=decode_reference):
        self.prefix, self.state = Path(prefix), Path(state)
        self.root = self.prefix / "voices"
        self.decoder = decoder
        if self.root.is_symlink():
            raise XttsError("invalid_voice_store")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.builtins = {}
        for path in self.root.glob('clone-*.json'):
            if not path.is_symlink() and path.stat().st_size <= 8192:
                value=json.loads(path.read_bytes())
                if value.get('deleted') is True and path==self._path(value.get('id')):
                    (self.root/(value['id']+'.wav')).unlink(missing_ok=True)

    @contextmanager
    def locked(self):
        import fcntl
        with (self.root / "voices.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def set_builtins(self, names):
        self.builtins = {"builtin-" + digest({"speaker": name})[:20]: name for name in sorted(names)}

    def _path(self, identity):
        if not isinstance(identity, str) or not VOICE_ID.fullmatch(identity):
            raise XttsError("unknown_voice")
        return self.root / (identity + ".json")

    def records(self):
        result = [{"id": key, "name": name, "kind": "builtin", "language": "ru"}
                  for key, name in self.builtins.items()]
        for path in sorted(self.root.glob("clone-*.json")):
            if path.is_symlink() or path.stat().st_size > 8192:
                raise XttsError("invalid_voice_store")
            value = json.loads(path.read_bytes())
            if value.get("schema") != "praxis.xtts.voice.v1" or path != self._path(value.get("id")):
                raise XttsError("invalid_voice_store")
            if value.get('deleted') is True:
                continue
            result.append({key: value[key] for key in ("id", "name", "kind", "language", "seconds")})
        return result

    def spec(self, identity, *, verify=True):
        path = self._path(identity)
        if identity in self.builtins:
            return {"voice_id": identity, "speaker": self.builtins[identity], "reference": "", "language": "ru"}
        if not identity.startswith("clone-") or not path.is_file() or path.is_symlink() or path.stat().st_size > 8192:
            raise XttsError("unknown_voice")
        value = json.loads(path.read_bytes())
        if value.get("schema") != "praxis.xtts.voice.v1" or value.get("id") != identity:
            raise XttsError("invalid_voice_store")
        if value.get('deleted') is True:
            raise XttsError('voice_deleted')
        reference = self.root / (identity + ".wav")
        if reference.is_symlink() or not reference.is_file():
            raise XttsError("invalid_reference")
        if verify and file_digest(reference) != value.get("reference_sha256"):
            raise XttsError("reference_changed")
        return {"voice_id": identity, "speaker": "", "reference": str(reference),
                "reference_sha256": value["reference_sha256"], "language": value["language"]}

    def add(self, source, *, identity, name, upload_sha256, stopped=lambda: False, actor='operator'):
        identity = "clone-" + identity
        path = self._path(identity)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
            raise XttsError("invalid_voice_name")
        name = name.strip()
        with self.locked():
            if path.exists():
                prior = json.loads(path.read_bytes())
                if prior.get('deleted') is True:
                    raise XttsError('voice_deleted')
                if prior.get("upload_sha256") != upload_sha256 or prior.get("name") != name:
                    raise XttsError("voice_conflict")
                self.spec(identity)
                return identity
            reference = self.root / (identity + ".wav")
            partial = self.root / ("." + identity + ".part.wav")
            if reference.exists() or partial.exists():
                raise XttsError("voice_conflict")
            try:
                seconds = self.decoder(Path(source), partial, stopped)
                if stopped():
                    raise XttsError("cancelled")
                partial.chmod(0o600)
                with partial.open("rb") as stream:
                    os.fsync(stream.fileno())
                checksum = file_digest(partial)
                partial.replace(reference)
                value = {"schema": "praxis.xtts.voice.v1", "id": identity, "name": name,
                         "kind": "clone", "language": "ru", "seconds": seconds,
                         "reference_sha256": checksum, "upload_sha256": upload_sha256}
                write_config(path, value, actor=actor)
            except BaseException:
                reference.unlink(missing_ok=True)
                raise
            finally:
                partial.unlink(missing_ok=True)
        return identity

    def selection(self):
        path = self.state / "selection.json"
        if not path.exists():
            return {"schema": "praxis.xtts.selection.v1", "enabled": False}
        if path.is_symlink() or path.stat().st_size > 4096:
            raise XttsError("invalid_selection")
        value = json.loads(path.read_bytes())
        if value.get("schema") != "praxis.xtts.selection.v1" or type(value.get("enabled")) is not bool:
            raise XttsError("invalid_selection")
        return value

    def disable(self, *, actor='operator',operation_id=None):
        current=self.selection()
        if operation_id and current.get('operation_id')==operation_id and not current['enabled']:return
        write_config(self.state / "selection.json", {"schema": "praxis.xtts.selection.v1", "enabled": False,
                     **({'operation_id':operation_id} if operation_id else {})}, actor=actor,operation_id=operation_id)

    def activate(self, spec, health, *, actor='operator',operation_id=None,expected_revision=None):
        current=self.selection()
        desired={'voice_id':spec['voice_id'],'model_id':health['model_id'],'profile_id':health['profile_id']}
        if operation_id and current.get('operation_id')==operation_id:
            if current['enabled'] and all(current.get(k)==v for k,v in desired.items()):return
            raise XttsError('voice_conflict')
        if expected_revision is not None and expected_revision!=current.get('revision','legacy'):
            raise XttsError('config_changed')
        write_config(self.prefix / "profile.json", spec, actor=actor,operation_id=operation_id)
        write_config(self.state / "selection.json", {"schema": "praxis.xtts.selection.v1", "enabled": True,
                     **desired,**({'operation_id':operation_id} if operation_id else {})}, actor=actor,operation_id=operation_id)

    def delete(self, identity, active_voice, *, actor='operator'):
        self._path(identity)
        if not identity.startswith("clone-"):
            raise XttsError("builtin_voice")
        if identity == active_voice:
            raise XttsError("active_voice")
        with self.locked():
            path=self._path(identity)
            if path.is_file() and not path.is_symlink() and json.loads(path.read_bytes()).get('deleted') is True:
                (self.root/(identity+'.wav')).unlink(missing_ok=True)
                return
            self.spec(identity)
            path = self._path(identity)
            value = json.loads(path.read_bytes())
            write_config(path, {'schema':'praxis.xtts.voice.v1','id':identity,'deleted':True,
                                'upload_sha256':value['upload_sha256']}, actor=actor)
            (self.root / (identity + ".wav")).unlink(missing_ok=True)
