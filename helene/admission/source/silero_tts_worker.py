"""Process-isolated, offline Silero TTS JSON-lines worker.

The parent process is expected to launch this file as a separate interpreter.  This
module intentionally has no PyTorch or ``silero`` import at module scope.  The
first valid synthesis request verifies an already-present local model artifact
against its configured SHA-256 and only then imports ``torch`` in this process.
There is deliberately no ``torch.hub`` or download fallback.

Protocol (one JSON object per input/output line)::

    {"schema":"praxis.silero.tts.request.v1","id":"...",
     "op":"synthesize","text":"...","output_path":"/private/.part.wav",
     "model":"v5_ru","speaker":"xenia","sample_rate":48000}

A successful response has ``ok: true``, the exact requested output path, byte
count, and process RSS observations.  Failures have ``ok: false`` and a stable
``error`` object.  Request text and exception details are never echoed.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import math
import os
import re
import resource
import selectors
import signal
import stat
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, Protocol

REQUEST_SCHEMA = "praxis.silero.tts.request.v1"
RESPONSE_SCHEMA = "praxis.silero.tts.response.v1"
DEFAULT_MODEL = "v5_ru"
DEFAULT_SPEAKER = "xenia"
DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_MAX_TEXT_CHARS = 4_000
DEFAULT_MAX_REQUEST_BYTES = 64 * 1024
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024 * 1024

_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SUPPORTED_SAMPLE_RATES = frozenset({8_000, 24_000, 48_000})
_PR_SET_PDEATHSIG = 1


def _arm_parent_death(parent_pid: int | None) -> None:
    """Kill the worker if its exact launching parent disappears unexpectedly."""

    if parent_pid is None:
        return
    if parent_pid <= 0:
        raise ValueError("parent pid must be greater than zero")
    if sys.platform != "linux":
        raise RuntimeError("parent-death supervision requires Linux")
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    result = prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if os.getppid() != parent_pid:
        raise RuntimeError("parent exited before worker initialization")


class SileroModel(Protocol):
    def save_wav(
        self,
        *,
        text: str,
        speaker: str,
        sample_rate: int,
        audio_path: str,
    ) -> Any: ...


ModelLoader = Callable[[Path], SileroModel]


class WorkerFailure(Exception):
    """A request failure safe to expose through the structured protocol."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable


@dataclass(frozen=True)
class WorkerConfig:
    model_path: Path
    model_sha256: str
    output_root: Path
    idle_timeout: float = 300.0
    max_requests: int = 100
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES

    def __post_init__(self) -> None:
        digest = self.model_sha256.strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("model SHA-256 must be exactly 64 hexadecimal characters")
        object.__setattr__(self, "model_sha256", digest)
        if not self.model_path.is_absolute():
            raise ValueError("model path must be absolute")
        if not self.output_root.is_absolute():
            raise ValueError("output root must be absolute")
        if (
            isinstance(self.idle_timeout, bool)
            or not isinstance(self.idle_timeout, (int, float))
            or not math.isfinite(float(self.idle_timeout))
            or float(self.idle_timeout) <= 0
        ):
            raise ValueError("idle timeout must be a finite positive number")
        object.__setattr__(self, "idle_timeout", float(self.idle_timeout))
        for name in (
            "max_requests",
            "max_text_chars",
            "max_request_bytes",
            "max_output_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


def load_local_silero_model(model_path: Path) -> SileroModel:
    """Load a verified Silero torch package without consulting a registry/network.

    Real API evidence used here is the Silero v5 package path used by
    ``silero.silero_tts``: ``torch.package.PackageImporter(path)`` followed by
    ``load_pickle("tts_models", "model")``.  Calling ``silero_tts`` itself is
    intentionally avoided because that helper downloads a missing package via
    ``torch.hub.download_url_to_file``.
    """

    # Deliberately worker-only and lazy.  Do not move this import to module scope.
    from torch import package  # type: ignore[import-not-found]

    importer = package.PackageImporter(str(model_path))
    return importer.load_pickle("tts_models", "model")


def _current_rss_bytes() -> int:
    """Return current resident bytes when procfs is available."""

    try:
        with open("/proc/self/status", "r", encoding="ascii", errors="strict") as source:
            for line in source:
                if line.startswith("VmRSS:"):
                    fields = line.split()
                    if len(fields) >= 2:
                        return max(0, int(fields[1])) * 1024
    except (OSError, ValueError):
        pass
    return _peak_rss_bytes()


def _peak_rss_bytes() -> int:
    peak = max(0, int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
    # Linux and the server deployment report KiB; macOS reports bytes.
    return peak if sys.platform == "darwin" else peak * 1024


def _regular_file_from_fd(descriptor: int) -> os.stat_result:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise WorkerFailure("model_invalid", "local model is not a regular file")
    return info


class SileroTTSWorker:
    """Lazy single-model worker which can serve multiple serial requests."""

    def __init__(
        self,
        config: WorkerConfig,
        *,
        model_loader: ModelLoader = load_local_silero_model,
    ) -> None:
        self.config = config
        self._model_loader = model_loader
        self._model: SileroModel | None = None
        # Held for the model lifetime.  On Linux the importer is given the procfs
        # path for this exact verified open file, closing the hash/load TOCTOU gap.
        self._model_fd: int | None = None
        self._requests_served = 0
        self._validate_static_paths()

    @property
    def requests_served(self) -> int:
        return self._requests_served

    def _validate_static_paths(self) -> None:
        try:
            root_info = self.config.output_root.lstat()
        except OSError as exc:
            raise ValueError("output root does not exist") from exc
        if not stat.S_ISDIR(root_info.st_mode) or self.config.output_root.is_symlink():
            raise ValueError("output root must be a real directory")
        try:
            model_info = self.config.model_path.lstat()
        except OSError as exc:
            raise ValueError("local model path does not exist") from exc
        if not stat.S_ISREG(model_info.st_mode):
            raise ValueError("local model path must be a regular file")

    def close(self) -> None:
        self._model = None
        if self._model_fd is not None:
            try:
                os.close(self._model_fd)
            except OSError:
                pass
            self._model_fd = None

    def _load_model(self) -> SileroModel:
        if self._model is not None:
            return self._model

        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.config.model_path, flags)
        except OSError as exc:
            raise WorkerFailure("model_unavailable", "local model cannot be opened") from exc

        try:
            before = _regular_file_from_fd(descriptor)
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise WorkerFailure("model_changed", "local model changed during verification")
            if digest.hexdigest() != self.config.model_sha256:
                raise WorkerFailure(
                    "model_hash_mismatch",
                    "local model SHA-256 does not match the configured digest",
                )

            os.lseek(descriptor, 0, os.SEEK_SET)
            verified_path = self.config.model_path
            proc_fd_path = Path("/proc/self/fd") / str(descriptor)
            if proc_fd_path.exists():
                verified_path = proc_fd_path
            try:
                model = self._model_loader(verified_path)
            except WorkerFailure:
                raise
            except BaseException as exc:
                # Model/package exceptions may contain paths or input fragments.
                raise WorkerFailure("model_load_failed", "verified local model could not be loaded") from exc
            if not callable(getattr(model, "save_wav", None)):
                raise WorkerFailure("model_invalid", "loaded model has no save_wav API")
            self._model_fd = descriptor
            self._model = model
            descriptor = -1
            return model
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _validate_request(self, request: Mapping[str, Any]) -> tuple[str, str, Path, str, int]:
        if request.get("schema") != REQUEST_SCHEMA:
            raise WorkerFailure("invalid_schema", "unsupported request schema")
        if request.get("op") != "synthesize":
            raise WorkerFailure("invalid_operation", "unsupported worker operation")

        request_id = request.get("id")
        if not isinstance(request_id, str) or _ID_RE.fullmatch(request_id) is None:
            raise WorkerFailure("invalid_id", "request id is invalid")
        text = request.get("text")
        if not isinstance(text, str) or not text.strip():
            raise WorkerFailure("invalid_text", "synthesis text is empty")
        text = text.strip()
        if len(text) > self.config.max_text_chars:
            raise WorkerFailure("text_too_long", "synthesis text exceeds the configured limit")

        model_name = request.get("model", DEFAULT_MODEL)
        speaker = request.get("speaker", DEFAULT_SPEAKER)
        sample_rate = request.get("sample_rate", DEFAULT_SAMPLE_RATE)
        if model_name != DEFAULT_MODEL:
            raise WorkerFailure("unsupported_model", "this worker only serves the v5_ru model")
        if not isinstance(speaker, str) or _NAME_RE.fullmatch(speaker) is None:
            raise WorkerFailure("invalid_speaker", "speaker name is invalid")
        if (
            not isinstance(sample_rate, int)
            or isinstance(sample_rate, bool)
            or sample_rate not in _SUPPORTED_SAMPLE_RATES
        ):
            raise WorkerFailure("invalid_sample_rate", "sample rate is not supported")

        request_hash = request.get("model_hash")
        if request_hash is not None and request_hash != self.config.model_sha256:
            raise WorkerFailure("model_hash_mismatch", "request model digest does not match worker")

        raw_output = request.get("output_path")
        if not isinstance(raw_output, str) or not raw_output or "\x00" in raw_output:
            raise WorkerFailure("invalid_output", "output path is invalid")
        output_path = Path(raw_output)
        if not output_path.is_absolute():
            raise WorkerFailure("invalid_output", "output path must be absolute")
        self._validate_output_path(output_path)
        return request_id, text, output_path, speaker, sample_rate

    def _validate_output_path(self, output_path: Path) -> None:
        root = self.config.output_root.resolve(strict=True)
        try:
            parent = output_path.parent.resolve(strict=True)
            parent.relative_to(root)
        except (OSError, ValueError) as exc:
            raise WorkerFailure("unsafe_output", "output path escapes the private root") from exc
        if parent == root and output_path.name in {"", ".", ".."}:
            raise WorkerFailure("unsafe_output", "output filename is invalid")
        try:
            target_info = output_path.lstat()
        except OSError as exc:
            raise WorkerFailure("unsafe_output", "output must be reserved by the parent") from exc
        if (
            not stat.S_ISREG(target_info.st_mode)
            or target_info.st_nlink != 1
            or target_info.st_size != 0
        ):
            raise WorkerFailure(
                "unsafe_output", "reserved output must be an empty private regular file"
            )

    def _temporary_path(self, output_path: Path) -> tuple[Path, int]:
        # The parent directory is private, but still use kernel-exclusive creation,
        # no-follow, and an unpredictable name rather than trusting a path check.
        for _attempt in range(32):
            suffix = os.urandom(16).hex()
            path = output_path.parent / f".{output_path.name}.{suffix}.tmp"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(path, flags, 0o600)
            except FileExistsError:
                continue
            return path, descriptor
        raise WorkerFailure("output_create_failed", "could not reserve atomic output")

    def _validate_wav(self, path: Path, sample_rate: int) -> int:
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise WorkerFailure("invalid_audio", "model output is not a regular WAV")
            if info.st_size <= 44 or info.st_size > self.config.max_output_bytes:
                raise WorkerFailure("invalid_audio", "model produced an invalid WAV size")
            with wave.open(str(path), "rb") as audio:
                if (
                    audio.getnchannels() <= 0
                    or audio.getsampwidth() <= 0
                    or audio.getnframes() <= 0
                    or audio.getframerate() != sample_rate
                ):
                    raise WorkerFailure("invalid_audio", "model produced an invalid WAV")
            return info.st_size
        except WorkerFailure:
            raise
        except (EOFError, OSError, wave.Error) as exc:
            raise WorkerFailure("invalid_audio", "model produced a malformed WAV") from exc

    def synthesize(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request_id, text, output_path, speaker, sample_rate = self._validate_request(request)
        model = self._load_model()
        temporary_path, descriptor = self._temporary_path(output_path)
        try:
            os.close(descriptor)
            descriptor = -1
            try:
                model.save_wav(
                    text=text,
                    speaker=speaker,
                    sample_rate=sample_rate,
                    audio_path=str(temporary_path),
                )
            except BaseException as exc:
                raise WorkerFailure(
                    "synthesis_failed", "Silero synthesis failed", retryable=True
                ) from exc
            byte_count = self._validate_wav(temporary_path, sample_rate)
            # Check that the parent's reservation was not swapped while synthesis ran.
            self._validate_output_path(output_path)
            with open(temporary_path, "rb") as artifact:
                os.fsync(artifact.fileno())
            os.replace(temporary_path, output_path)
            try:
                directory_fd = os.open(output_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                # The atomic rename is the safety invariant; some filesystems do
                # not permit directory fsync, so lack of durability is non-fatal.
                pass
            self._requests_served += 1
            return {
                "schema": RESPONSE_SCHEMA,
                "id": request_id,
                "ok": True,
                "output_path": str(output_path),
                "bytes": byte_count,
                "model": DEFAULT_MODEL,
                "speaker": speaker,
                "sample_rate": sample_rate,
                "requests_served": self._requests_served,
                "rss_bytes": (rss_bytes := _current_rss_bytes()),
                "peak_rss_bytes": max(_peak_rss_bytes(), rss_bytes),
            }
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


class _Idle:
    pass


class _EndOfInput:
    pass


class _OversizedLine:
    pass


IDLE = _Idle()
END_OF_INPUT = _EndOfInput()
OVERSIZED_LINE = _OversizedLine()


class _IdleLineReader:
    def __init__(self, descriptor: int, *, max_bytes: int) -> None:
        self.descriptor = descriptor
        self.max_bytes = max_bytes
        self.buffer = bytearray()
        self.selector = selectors.DefaultSelector()
        self.selector.register(descriptor, selectors.EVENT_READ)

    def close(self) -> None:
        self.selector.close()

    def readline(self, timeout: float) -> bytes | _Idle | _EndOfInput | _OversizedLine:
        deadline = time.monotonic() + timeout
        while True:
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self.buffer[:newline])
                del self.buffer[: newline + 1]
                if len(line) > self.max_bytes:
                    return OVERSIZED_LINE
                return line
            if len(self.buffer) > self.max_bytes:
                # Drain this single oversized JSONL record without letting its
                # already-buffered tail be mistaken for a new request.
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.buffer.clear()
                    return OVERSIZED_LINE
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return IDLE
            try:
                events = self.selector.select(remaining)
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                raise
            if not events:
                if len(self.buffer) > self.max_bytes:
                    self.buffer.clear()
                    return OVERSIZED_LINE
                return IDLE
            try:
                chunk = os.read(self.descriptor, 8192)
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                raise
            if not chunk:
                if not self.buffer:
                    return END_OF_INPUT
                line = bytes(self.buffer)
                self.buffer.clear()
                if len(line) > self.max_bytes:
                    return OVERSIZED_LINE
                return line
            self.buffer.extend(chunk)


def _safe_request_id(request: object) -> str | None:
    if isinstance(request, Mapping):
        request_id = request.get("id")
        if isinstance(request_id, str) and _ID_RE.fullmatch(request_id):
            return request_id
    return None


def _error_response(request_id: str | None, failure: WorkerFailure) -> dict[str, Any]:
    return {
        "schema": RESPONSE_SCHEMA,
        "id": request_id,
        "ok": False,
        "error": {
            "code": failure.code,
            "message": failure.safe_message,
            "retryable": failure.retryable,
        },
        "rss_bytes": _current_rss_bytes(),
        "peak_rss_bytes": _peak_rss_bytes(),
    }


def _write_response(output: BinaryIO, response: Mapping[str, Any]) -> None:
    payload = json.dumps(
        response, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    output.write(payload + b"\n")
    output.flush()


def serve(
    worker: SileroTTSWorker,
    *,
    input_fd: int,
    output: BinaryIO,
) -> int:
    """Serve JSONL until EOF, idle expiry, or the configured recycle count."""

    reader = _IdleLineReader(input_fd, max_bytes=worker.config.max_request_bytes)
    requests_seen = 0
    try:
        while requests_seen < worker.config.max_requests:
            record = reader.readline(worker.config.idle_timeout)
            if record is IDLE or record is END_OF_INPUT:
                return 0
            requests_seen += 1
            if record is OVERSIZED_LINE:
                response = _error_response(
                    None,
                    WorkerFailure("request_too_large", "JSON request exceeds the byte limit"),
                )
            else:
                assert isinstance(record, bytes)
                request: object = None
                try:
                    request = json.loads(record.decode("utf-8", errors="strict"))
                    if not isinstance(request, dict):
                        raise WorkerFailure("invalid_request", "JSON request must be an object")
                    response = worker.synthesize(request)
                except (UnicodeError, json.JSONDecodeError):
                    response = _error_response(
                        None, WorkerFailure("invalid_json", "request is not valid JSON")
                    )
                except WorkerFailure as failure:
                    response = _error_response(_safe_request_id(request), failure)
                except BaseException:
                    # Fail closed but keep a structured protocol response.  The
                    # parent treats it as a poisoned worker and synchronously reaps.
                    response = _error_response(
                        _safe_request_id(request),
                        WorkerFailure("internal_error", "worker request failed"),
                    )
            response["recycle"] = requests_seen >= worker.config.max_requests
            _write_response(output, response)
        return 0
    finally:
        reader.close()
        worker.close()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="offline Silero TTS JSONL worker")
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--model-path", required=True, type=Path)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--idle-timeout", type=float, default=300.0)
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--max-text-chars", type=int, default=DEFAULT_MAX_TEXT_CHARS)
    parser.add_argument("--max-request-bytes", type=int, default=DEFAULT_MAX_REQUEST_BYTES)
    parser.add_argument("--max-output-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES)
    return parser.parse_args(argv)


def _protocol_output() -> BinaryIO:
    """Keep fd 1 protocol-only, including across noisy native/model code."""

    sys.stdout.flush()
    protocol_fd = os.dup(sys.stdout.fileno())
    protocol = os.fdopen(protocol_fd, "wb", buffering=0)
    try:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        # Python-level print should follow stderr too; native writes to fd 1 were
        # redirected above.  Protocol writes use the saved duplicate only.
        sys.stdout = sys.stderr
    except BaseException:
        protocol.close()
        raise
    return protocol


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        _arm_parent_death(args.parent_pid)
        config = WorkerConfig(
            model_path=args.model_path,
            model_sha256=args.model_sha256,
            output_root=args.output_root,
            idle_timeout=args.idle_timeout,
            max_requests=args.max_requests,
            max_text_chars=args.max_text_chars,
            max_request_bytes=args.max_request_bytes,
            max_output_bytes=args.max_output_bytes,
        )
        worker = SileroTTSWorker(config)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"silero worker configuration error: {exc}", file=sys.stderr)
        return 2

    protocol = _protocol_output()
    try:
        return serve(worker, input_fd=sys.stdin.buffer.fileno(), output=protocol)
    except (BrokenPipeError, OSError):
        return 1
    finally:
        protocol.close()


if __name__ == "__main__":
    raise SystemExit(main())
