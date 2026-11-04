"""Authenticated local speech capability. No Telegram, LLM or task store."""
from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import uuid

from xtts_protocol import MAX_AUDIO, MAX_REQUEST, REQUEST_ID, SCHEMA, XttsError, encoded, file_digest


def encode_opus(source, destination, stopped):
    import imageio_ffmpeg
    executable = os.environ.get("PRAXIS_XTTS_FFMPEG") or imageio_ffmpeg.get_ffmpeg_exe()
    process = subprocess.Popen([executable, "-nostdin", "-v", "error", "-y", "-i", str(source),
                                "-ac", "1", "-c:a", "libopus", "-b:a", "48k", str(destination)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 30
        while process.poll() is None:
            if stopped() or time.monotonic() >= deadline:
                raise XttsError("cancelled" if stopped() else "encoding_timeout")
            time.sleep(0.05)
        if process.returncode or not destination.is_file() or destination.stat().st_size > MAX_AUDIO:
            raise XttsError("encoding_failed")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


class SpeechServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, path: Path, token: str, engine, *, scratch: Path, encoder=encode_opus, voice_store=None):
        self.token, self.engine, self.scratch = token, engine, Path(scratch)
        self.encoder = encoder
        self.scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.generation = uuid.uuid4().hex
        self.inference_lock = threading.Lock()
        self.active = {}
        self.active_ops = {}
        self.config_lock = threading.RLock()
        self.voice_store = voice_store
        self.cancelled = {}
        self.active_lock = threading.Lock()
        self.stopping = threading.Event()
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(str(path), Handler)
        os.chmod(path, 0o600)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, address):
        # Never expose request text, tokens, paths or backend exceptions.
        pass

    def close_requests(self):
        self.stopping.set()
        with self.active_lock:
            for event, _deadline, _started in self.active.values():
                event.set()

    def needs_restart(self, now=None):
        """A non-cooperating model may be reaped only with its owning service."""
        now = time.monotonic() if now is None else now
        with self.active_lock:
            return any(now > deadline + 10 or (event.is_set() and
                       now > self.cancelled.get(identity, now) + 10)
                       for identity, (event, deadline, _started) in self.active.items())


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        output = encoded_output = None
        try:
            line = self.rfile.readline(MAX_REQUEST + 1)
            if not line.endswith(b"\n") or len(line) > MAX_REQUEST:
                raise XttsError("invalid_request")
            request = json.loads(line)
            if not isinstance(request, dict) or not hmac.compare_digest(
                    str(request.get("token") or ""), self.server.token):
                raise XttsError("unauthorized")
            if request.get("schema") != SCHEMA:
                raise XttsError("invalid_schema")
            op = request.get("operation")
            if op == "health":
                self.send({"ok": True, "generation": self.server.generation,
                           "busy": self.server.inference_lock.locked(),
                           **({'enabled':self.server.voice_store.selection()['enabled']} if self.server.voice_store else {}),
                           **self.server.engine.health()})
                return
            if op == 'voices':
                from xtts_voice_rpc import describe
                self.send(describe(self.server))
                return
            identity = str(request.get("request_id") or "")
            if not REQUEST_ID.fullmatch(identity):
                raise XttsError("invalid_request_id")
            if op == "cancel":
                with self.server.active_lock:
                    now = time.monotonic()
                    self.server.cancelled = {key: value for key, value in self.server.cancelled.items() if now - value < 600}
                    if len(self.server.cancelled) >= 128:
                        self.server.cancelled.pop(next(iter(self.server.cancelled)))
                    self.server.cancelled.setdefault(identity, now)
                    active = self.server.active.get(identity)
                    if active is not None:
                        active[0].set()
                self.send({"ok": True, "cancelled": active is not None})
                return
            if op == 'request_status':
                with self.server.active_lock:
                    self.send({'ok': True, 'active': identity in self.server.active,
                               'cancel_requested': identity in self.server.cancelled,
                               'generation': self.server.generation})
                return
            if op == 'voice_enabled' and type(request.get('enabled')) is not bool:
                raise XttsError('invalid_request')
            if op == 'voice_enabled' and request.get('enabled') is False:
                from xtts_voice_rpc import disable
                self.send(disable(self.server,request))
                return
            if op in {'voice_add','voice_select','voice_enabled','voice_delete'}:
                from xtts_voice_rpc import control
                self.send(control(self,request))
                return
            if op not in {"synthesize", 'preview', 'synthesize_stream', 'preview_stream'}:
                raise XttsError("invalid_operation")
            if not self.server.inference_lock.acquire(blocking=False):
                raise XttsError("busy")
            event = threading.Event()
            try:
                duration = request.get("timeout", 300)
                if duration is None and op not in {'synthesize_stream', 'preview_stream'}:
                    raise XttsError('invalid_deadline')
                if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 < duration <= 600):
                    raise XttsError("invalid_deadline")
                deadline = float('inf') if duration is None else time.monotonic() + duration
                with self.server.active_lock:
                    self.server.active[identity] = (event, deadline, time.monotonic())
                    self.server.active_ops[identity] = op
                    if identity in self.server.cancelled:
                        event.set()
                fd, name = tempfile.mkstemp(prefix="speech-", suffix=".wav", dir=self.server.scratch)
                os.close(fd)
                output = Path(name)
                stopped = lambda: event.is_set() or self.server.stopping.is_set() or time.monotonic() >= deadline
                if stopped():
                    raise XttsError("cancelled")
                profile = None
                if self.server.voice_store is not None:
                    if op in {'synthesize', 'synthesize_stream'} and not self.server.voice_store.selection()['enabled']:
                        raise XttsError('voice_disabled')
                    from xtts_voice_rpc import resolve_profile
                    if op in {'preview', 'preview_stream'} and not request.get('voice_id'):
                        raise XttsError('unknown_voice')
                    profile=resolve_profile(self.server,request)
                elif op in {'preview', 'preview_stream'}:
                    raise XttsError('unavailable')
                if op in {'synthesize_stream', 'preview_stream'}:
                    from xtts_stream import respond
                    respond(self, request, output, profile, stopped)
                    return
                meta = self.server.engine.synthesize(
                    request.get("text"), output,
                    model_id=request.get("model_id"), profile_id=request.get("profile_id"),
                    stopped=stopped, **({'profile':profile} if profile is not None else {}))
                if stopped():
                    raise XttsError("cancelled")
                encoded_output = output.with_suffix(".ogg")
                self.server.encoder(output, encoded_output, stopped)
                if stopped():
                    raise XttsError("cancelled")
                size = encoded_output.stat().st_size
                if not 32 < size <= MAX_AUDIO:
                    raise XttsError("invalid_audio")
                self.send({"ok": True, "request_id": identity,
                           "generation": self.server.generation, "bytes": size,
                           "sha256": file_digest(encoded_output), "mime": "audio/ogg", **meta})
                with encoded_output.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(64 * 1024), b""):
                        if event.is_set() or self.server.stopping.is_set():
                            return
                        self.wfile.write(chunk)
                self.wfile.flush()
            finally:
                with self.server.active_lock:
                    self.server.active.pop(identity, None)
                    self.server.active_ops.pop(identity, None)
                self.server.inference_lock.release()
        except XttsError as exc:
            self.send({"ok": False, "error": exc.code})
        except (OSError, ValueError, TypeError):
            self.send({"ok": False, "error": "invalid_request"})
        except Exception:
            self.send({"ok": False, "error": "inference_failed"})
        finally:
            if output is not None:
                output.unlink(missing_ok=True)
            if encoded_output is not None:
                encoded_output.unlink(missing_ok=True)

    def send(self, value):
        try:
            self.wfile.write(encoded({"schema": SCHEMA, **value}) + b"\n")
            self.wfile.flush()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    token = args.token_file.read_text().strip()
    if not 32 <= len(token) <= 256:
        parser.error("invalid token file")
    args.socket.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.socket.exists() or args.socket.is_symlink():
        if args.socket.is_symlink() or not args.socket.is_socket():
            raise XttsError("socket_collision")
        probe = socket.socket(socket.AF_UNIX)
        try:
            probe.settimeout(1)
            probe.connect(str(args.socket))
        except ConnectionRefusedError:
            args.socket.unlink()
        else:
            raise XttsError("socket_active")
        finally:
            probe.close()
    from xtts_engine import XttsEngine
    engine = XttsEngine(args.model, args.profile, device=args.device, threads=args.threads)
    from xtts_profiles import VoiceStore
    store=VoiceStore(args.profile.parent,args.socket.parent)
    server = SpeechServer(args.socket, token, engine, scratch=args.socket.parent / "scratch",voice_store=store)
    identity = args.socket.stat().st_ino

    def load():
        try:
            with server.inference_lock:
                engine.load()
                store.set_builtins(engine.health().get('speakers') or [])
        except Exception as exc:
            engine.phase = "failed"
            engine.error = exc.code if isinstance(exc, XttsError) else "model_load_failed"
            engine.error_type = type(exc).__name__

    threading.Thread(target=load, name="xtts-load", daemon=True).start()

    def watchdog():
        started = time.monotonic()
        while not server.stopping.wait(1):
            if server.needs_restart() or (engine.phase == "warming" and time.monotonic() - started > 600):
                # This process owns only the optional model capability. The
                # service manager reaps its whole cgroup and restores readiness.
                os._exit(1)

    threading.Thread(target=watchdog, name="xtts-watchdog", daemon=True).start()

    def stop(*_):
        server.close_requests()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.close_requests()
        server.server_close()
        if args.socket.exists() and args.socket.stat().st_ino == identity:
            args.socket.unlink()


if __name__ == "__main__":
    main()
