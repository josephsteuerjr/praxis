"""Supervised, process-isolated client for the optional Silero TTS worker.

This module is deliberately parent-side only: it imports neither PyTorch nor
Silero.  A worker process is started on the first synthesis request, receives
one JSON-lines request at a time, and is killed and reaped before a recoverable
error is returned to the caller.  That last property is important when the
caller falls back to an in-process TTS backend: a timed-out model must not stay
resident while the fallback is loading.
"""

from __future__ import annotations

import json
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

# Reusing the audio facade's error hierarchy makes the existing FallbackTTS
# catch this backend exactly as it catches Piper/Edge failures.  media_audio has
# no heavyweight imports at module import time.
from media_audio import AudioConfigurationError, AudioProcessingError


REQUEST_SCHEMA = "praxis.silero.tts.request.v1"
RESPONSE_SCHEMA = "praxis.silero.tts.response.v1"
DEFAULT_MODEL = "v5_ru"
DEFAULT_MODEL_SHA256 = "7ba04d42340fe0398042eed2e0d12d62e23096d626b1b9feff4dcb1309197ab4"
DEFAULT_MODEL_PATH = Path("/models/audio/silero/v5_ru.pt")
DEFAULT_WORKER_PATH = Path(__file__).with_name("silero_tts_worker.py")
DEFAULT_EXECUTABLE = "/models/audio/silero/.venv/bin/python"
DEFAULT_SPEAKER = "xenia"
DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_MAX_TEXT_CHARS = 4_000
DEFAULT_MAX_REQUEST_BYTES = 64 * 1024
_MAX_RESPONSE_BYTES = 64 * 1024
_SPEAKER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_OWNED_PARTIAL_RE = re.compile(
    r"^\.tts-silero-(?P<pid>[1-9][0-9]*)-(?P<start>[0-9]+)-"
    r"[0-9a-f]{32}-[0-9a-f]{32}\.part\.wav$"
)
_OWNED_FINAL_RE = re.compile(
    r"^tts-silero-(?P<pid>[1-9][0-9]*)-(?P<start>[0-9]+)-"
    r"[0-9a-f]{32}-[0-9a-f]{32}\.wav$"
)
_OWNED_TEMP_RE = re.compile(
    r"^\.\.tts-silero-(?P<pid>[1-9][0-9]*)-(?P<start>[0-9]+)-"
    r"[0-9a-f]{32}-[0-9a-f]{32}\.part\.wav\.[0-9a-f]{32}\.tmp$"
)


class SileroTTSConfigurationError(AudioConfigurationError):
    """The isolated worker cannot be started with the supplied configuration."""


class SileroTTSProcessError(AudioProcessingError):
    """The isolated worker failed, timed out, or violated its protocol."""


class SileroTTSTimeoutError(SileroTTSProcessError):
    """The end-to-end worker request deadline expired."""


class SileroTTSMemoryError(SileroTTSProcessError):
    """The worker crossed the client-observed soft resident-memory guard."""


@dataclass(frozen=True)
class SileroTTSConfig:
    """Explicit configuration for :class:`SileroTTSClient`.

    ``model_sha256`` is passed to the child, which verifies the local model
    before deserializing it.  ``executable`` should be the Python interpreter
    in the environment where the worker-only PyTorch dependency is installed.
    No process is created while this object or the client is constructed.
    """

    worker_path: Path
    model_path: Path
    model_sha256: str
    output_dir: Path
    executable: str = sys.executable
    request_timeout_seconds: float = 45.0
    idle_timeout_seconds: float = 300.0
    terminate_grace_seconds: float = 1.0
    rss_poll_interval_seconds: float = 0.05
    max_rss_bytes: int = 1_500 * 1024 * 1024
    recycle_after_requests: int = 100
    max_chars: int = DEFAULT_MAX_TEXT_CHARS
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    max_output_bytes: int = 64 * 1024 * 1024
    model: str = DEFAULT_MODEL
    speaker: str = DEFAULT_SPEAKER
    sample_rate: int = DEFAULT_SAMPLE_RATE

    def __post_init__(self) -> None:
        for name in ("worker_path", "model_path", "output_dir"):
            value = getattr(self, name)
            if not isinstance(value, Path):
                raise SileroTTSConfigurationError(f"{name} must be a pathlib.Path")
            try:
                normalized_path = value.expanduser()
            except (OSError, RuntimeError) as exc:
                raise SileroTTSConfigurationError(f"{name} cannot be expanded") from exc
            if not normalized_path.is_absolute() or "\x00" in str(normalized_path):
                raise SileroTTSConfigurationError(f"{name} must be a safe absolute path")
            object.__setattr__(self, name, normalized_path)

        if not isinstance(self.model_sha256, str):
            raise SileroTTSConfigurationError("Silero model SHA-256 must be text")
        digest = self.model_sha256.strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise SileroTTSConfigurationError(
                "Silero model SHA-256 must contain exactly 64 hexadecimal characters"
            )
        object.__setattr__(self, "model_sha256", digest)
        if (
            not isinstance(self.executable, str)
            or not self.executable
            or self.executable != self.executable.strip()
            or "\x00" in self.executable
        ):
            raise SileroTTSConfigurationError("Silero worker executable path is invalid")
        try:
            executable_path = Path(self.executable).expanduser()
        except (OSError, RuntimeError) as exc:
            raise SileroTTSConfigurationError(
                "Silero worker executable path cannot be expanded"
            ) from exc
        if not executable_path.is_absolute():
            raise SileroTTSConfigurationError(
                "Silero worker executable must be an absolute path"
            )
        object.__setattr__(self, "executable", str(executable_path))
        float_fields = (
            "request_timeout_seconds",
            "idle_timeout_seconds",
            "terminate_grace_seconds",
            "rss_poll_interval_seconds",
        )
        for name in float_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SileroTTSConfigurationError(f"{name} must be a finite number")
            normalized = float(value)
            if not math.isfinite(normalized) or normalized <= 0:
                raise SileroTTSConfigurationError(f"{name} must be a finite positive number")
            object.__setattr__(self, name, normalized)

        integer_fields = (
            "max_rss_bytes",
            "recycle_after_requests",
            "max_chars",
            "max_request_bytes",
            "max_output_bytes",
            "sample_rate",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise SileroTTSConfigurationError(f"{name} must be a positive integer")
        if self.max_chars > DEFAULT_MAX_TEXT_CHARS:
            raise SileroTTSConfigurationError(
                f"max_chars cannot exceed {DEFAULT_MAX_TEXT_CHARS}"
            )
        if self.max_request_bytes > DEFAULT_MAX_REQUEST_BYTES:
            raise SileroTTSConfigurationError(
                f"max_request_bytes cannot exceed {DEFAULT_MAX_REQUEST_BYTES}"
            )
        if not isinstance(self.model, str) or self.model != DEFAULT_MODEL:
            raise SileroTTSConfigurationError(
                f"Silero worker only supports model {DEFAULT_MODEL!r}"
            )
        if self.sample_rate not in {8_000, 24_000, 48_000}:
            raise SileroTTSConfigurationError(
                "Silero sample rate must be one of 8000, 24000, or 48000"
            )
        if not isinstance(self.speaker, str) or _SPEAKER_RE.fullmatch(self.speaker) is None:
            raise SileroTTSConfigurationError(
                "Silero speaker must be a 1-64 character ASCII identifier"
            )

    @classmethod
    def from_env(
        cls,
        *,
        output_dir: Path,
        environ: Mapping[str, str] | None = None,
    ) -> "SileroTTSConfig":
        """Build an opt-in config without changing the live audio default.

        Required model identity defaults are immutable deployment paths and the
        digest measured in the accepted cold A/B.  Environment variables may
        point at a separately provisioned worker venv/model, but no download is
        ever attempted by this client or its worker.
        """

        env = os.environ if environ is None else environ

        def value(name: str, default: str) -> str:
            configured = env.get(name, "").strip()
            return configured or default

        def integer(name: str, default: int) -> int:
            raw = env.get(name, "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError as exc:
                raise SileroTTSConfigurationError(f"{name} must be an integer") from exc

        def number(name: str, default: float) -> float:
            raw = env.get(name, "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError as exc:
                raise SileroTTSConfigurationError(f"{name} must be a number") from exc

        return cls(
            executable=value("PRAXIS_SILERO_PYTHON", DEFAULT_EXECUTABLE),
            worker_path=Path(
                value("PRAXIS_SILERO_WORKER", str(DEFAULT_WORKER_PATH))
            ).expanduser(),
            model_path=Path(
                value("PRAXIS_SILERO_MODEL", str(DEFAULT_MODEL_PATH))
            ).expanduser(),
            model_sha256=value(
                "PRAXIS_SILERO_MODEL_SHA256", DEFAULT_MODEL_SHA256
            ),
            output_dir=Path(output_dir).expanduser(),
            request_timeout_seconds=number("PRAXIS_SILERO_TIMEOUT", 45.0),
            idle_timeout_seconds=number("PRAXIS_SILERO_IDLE_TIMEOUT", 300.0),
            terminate_grace_seconds=number("PRAXIS_SILERO_TERM_GRACE", 1.0),
            rss_poll_interval_seconds=number("PRAXIS_SILERO_RSS_POLL", 0.05),
            max_rss_bytes=integer(
                "PRAXIS_SILERO_MAX_RSS_BYTES", 1_500 * 1024 * 1024
            ),
            recycle_after_requests=integer("PRAXIS_SILERO_RECYCLE_REQUESTS", 100),
            max_chars=integer("PRAXIS_TTS_MAX_CHARS", DEFAULT_MAX_TEXT_CHARS),
            max_request_bytes=integer(
                "PRAXIS_SILERO_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES
            ),
            max_output_bytes=integer(
                "PRAXIS_SILERO_MAX_OUTPUT_BYTES", 64 * 1024 * 1024
            ),
            model=env.get("PRAXIS_SILERO_MODEL_NAME", DEFAULT_MODEL).strip()
            or DEFAULT_MODEL,
            speaker=env.get("PRAXIS_SILERO_SPEAKER", DEFAULT_SPEAKER).strip()
            or DEFAULT_SPEAKER,
            sample_rate=integer("PRAXIS_SILERO_SAMPLE_RATE", DEFAULT_SAMPLE_RATE),
        )


def read_process_rss_bytes(pid: int) -> int:
    """Read current RSS for ``pid`` from Linux procfs.

    The worker is the process that imports and owns PyTorch, so its ``VmRSS``
    is the relevant guard signal.  Failure to observe a live worker is treated
    fail-closed by the client rather than silently disabling the memory rail.
    """

    status_path = Path("/proc") / str(pid) / "status"
    with status_path.open("r", encoding="ascii", errors="strict") as source:
        for line in source:
            if not line.startswith("VmRSS:"):
                continue
            fields = line.split()
            if len(fields) < 2:
                break
            kibibytes = int(fields[1])
            return kibibytes * 1024
    raise OSError(f"RSS is unavailable for worker pid {pid}")


def _linux_process_start_ticks(pid: int) -> int | None:
    """Return Linux process start ticks, or ``None`` when identity is unprovable."""

    try:
        raw = (Path("/proc") / str(pid) / "stat").read_text(
            encoding="ascii", errors="strict"
        )
        # comm may contain spaces and parentheses; fields after its final ')'
        # start with field 3, making starttime (field 22) offset 19 here.
        fields = raw.rsplit(")", 1)[1].split()
        value = int(fields[19])
        return value if value > 0 else None
    except (IndexError, OSError, ValueError):
        return None


class _ProtocolFailure(RuntimeError):
    pass


class _WorkerExited(RuntimeError):
    pass


class SileroTTSClient:
    """Serialized supervisor implementing MediaAudio's ``TextToSpeech`` shape.

    Public integration surface:

    * ``synthesize(text) -> Path``
    * ``clear_cache()`` (also available as ``close()``)
    * ``status() -> dict`` / ``metrics``

    The instance may be used as a context manager.  Call ``clear_cache`` during
    application shutdown so the process group is synchronously killed/reaped.
    """

    def __init__(
        self,
        config: SileroTTSConfig,
        *,
        rss_reader: Callable[[int], int] = read_process_rss_bytes,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._rss_reader = rss_reader
        self._popen_factory = popen_factory
        self._clock = clock
        self._lock = threading.RLock()
        self._launch_condition = threading.Condition()
        self._launches_inflight = 0
        self._proc: subprocess.Popen[bytes] | None = None
        self._reaper: threading.Thread | None = None
        self._stdout_buffer = bytearray()
        self._process_requests = 0
        self._starts = 0
        self._requests = 0
        self._peak_rss_bytes = 0
        self._last_rss_bytes = 0
        self._recycles = 0
        self._failures = 0
        self._last_error: str | None = None
        process_start = _linux_process_start_ticks(os.getpid())
        # A zero start token is deliberately never swept: on a platform where
        # process identity cannot be proved, retaining stale files is safer
        # than deleting an artifact belonging to another live supervisor.
        self._owner_token = f"{os.getpid()}-{process_start or 0}-{uuid.uuid4().hex}"
        self._artifacts_swept = False
        self.skip_warmup = True

    def __enter__(self) -> "SileroTTSClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.clear_cache()

    @property
    def pid(self) -> int | None:
        with self._lock:
            self._notice_exited_worker()
            return self._proc.pid if self._proc is not None else None

    @property
    def metrics(self) -> dict[str, Any]:
        return self.status()

    def status(self) -> dict[str, Any]:
        """Return a lock-consistent, text-free observability snapshot."""

        with self._lock:
            self._notice_exited_worker()
            proc = self._proc
            return {
                "pid": proc.pid if proc is not None else None,
                "alive": proc is not None,
                # Keep the final observation after stop/reap.  ``alive`` tells
                # callers whether this is current or retained evidence.
                "rss_bytes": self._last_rss_bytes,
                "peak_rss_bytes": self._peak_rss_bytes,
                "starts": self._starts,
                "requests": self._requests,
                "process_requests": self._process_requests if proc is not None else 0,
                "recycles": self._recycles,
                "failures": self._failures,
                "last_error": self._last_error,
            }

    def synthesize(self, text: str) -> Path:
        deadline = self._clock() + self.config.request_timeout_seconds
        if not isinstance(text, str):
            raise SileroTTSConfigurationError("TTS input must be text")
        clean_text = text.strip()
        if not clean_text:
            raise SileroTTSConfigurationError("TTS input is empty")
        if len(clean_text) > self.config.max_chars:
            raise SileroTTSConfigurationError(
                f"TTS input is too long ({len(clean_text)} > "
                f"{self.config.max_chars} characters)"
            )

        remaining = deadline - self._clock()
        if remaining <= 0 or not self._lock.acquire(timeout=remaining):
            raise SileroTTSTimeoutError(
                "Silero synthesis timed out while waiting for the worker"
            )
        try:
            partial_path: Path | None = None
            final_path: Path | None = None
            try:
                partial_path, final_path = self._reserve_output()
                if self._clock() >= deadline:
                    raise SileroTTSTimeoutError(
                        "Silero synthesis timed out before worker startup"
                    )
                request_id = uuid.uuid4().hex
                request = {
                    "schema": REQUEST_SCHEMA,
                    "id": request_id,
                    "op": "synthesize",
                    "text": clean_text,
                    "output_path": str(partial_path),
                    "model": self.config.model,
                    "speaker": self.config.speaker,
                    "sample_rate": self.config.sample_rate,
                }
                request_bytes = json.dumps(
                    request, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8") + b"\n"
                if len(request_bytes) > self.config.max_request_bytes:
                    raise SileroTTSConfigurationError(
                        "Silero request exceeds the configured byte limit"
                    )
                if self._clock() >= deadline:
                    raise SileroTTSTimeoutError(
                        "Silero synthesis timed out before worker startup"
                    )
                proc = self._ensure_process(deadline)
                self._write_request(proc, request_bytes, deadline)
                response = self._read_response(proc, deadline)
                byte_count = self._validate_response(response, request_id, partial_path)
                # The worker may exit immediately after a successful response
                # when max_requests is reached.  Its response carries the exact
                # post-synthesis RSS evidence, so no racy /proc read is needed.
                self._validate_wav(partial_path, expected_bytes=byte_count)
                os.replace(partial_path, final_path)
                try:
                    final_path.chmod(0o600)
                except OSError:
                    pass
                self._requests += 1
                self._process_requests += 1
                self._last_error = None

                if self._process_requests >= self.config.recycle_after_requests:
                    self._stop_process(count_recycle=True)
                return final_path
            except SileroTTSConfigurationError:
                self._unlink_artifact(partial_path)
                self._unlink_artifact(final_path)
                raise
            except SileroTTSTimeoutError:
                self._record_failure("timeout")
                self._stop_process(count_recycle=True)
                self._unlink_artifact(partial_path)
                self._unlink_artifact(final_path)
                raise
            except SileroTTSMemoryError:
                self._record_failure("rss_limit")
                self._stop_process(count_recycle=True)
                self._unlink_artifact(partial_path)
                self._unlink_artifact(final_path)
                raise
            except (_ProtocolFailure, _WorkerExited) as exc:
                code = "protocol" if isinstance(exc, _ProtocolFailure) else "worker_exit"
                self._record_failure(code)
                self._stop_process(count_recycle=True)
                self._unlink_artifact(partial_path)
                self._unlink_artifact(final_path)
                raise SileroTTSProcessError(
                    "Silero worker failed before producing a valid artifact"
                ) from exc
            except SileroTTSProcessError:
                self._record_failure("processing")
                self._stop_process(count_recycle=True)
                self._unlink_artifact(partial_path)
                self._unlink_artifact(final_path)
                raise
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._record_failure("io")
                self._stop_process(count_recycle=True)
                self._unlink_artifact(partial_path)
                self._unlink_artifact(final_path)
                raise SileroTTSProcessError("Silero worker I/O failed") from exc
        finally:
            self._lock.release()

    def clear_cache(self) -> None:
        """Stop the published worker; late unpublished launches clean themselves."""

        with self._lock:
            self._stop_process(count_recycle=self._proc is not None)

    def prepare_fallback(self) -> bool:
        """Clear synchronously, then report whether every possible child is gone."""

        self.clear_cache()
        return self.fallback_ready()

    def fallback_ready(self) -> bool:
        """True only when no published or not-yet-published worker can be alive."""

        with self._lock:
            self._notice_exited_worker()
            if self._proc is not None:
                return False
        with self._launch_condition:
            return self._launches_inflight == 0

    close = clear_cache

    def _record_failure(self, code: str) -> None:
        self._failures += 1
        self._last_error = code

    def _reserve_output(self) -> tuple[Path, Path]:
        output_dir = self.config.output_dir.expanduser().resolve()
        try:
            output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            output_dir.chmod(0o700)
        except OSError as exc:
            raise SileroTTSConfigurationError(
                "Silero output directory cannot be prepared"
            ) from exc
        if not output_dir.is_dir():
            raise SileroTTSConfigurationError("Silero output path is not a directory")

        if not self._artifacts_swept:
            self._sweep_stale_owned_artifacts(output_dir)
            self._artifacts_swept = True

        for _attempt in range(8):
            artifact_id = uuid.uuid4().hex
            identity = f"{self._owner_token}-{artifact_id}"
            partial_path = output_dir / f".tts-silero-{identity}.part.wav"
            final_path = output_dir / f"tts-silero-{identity}.wav"
            partial_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            final_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                partial_flags |= os.O_NOFOLLOW
                final_flags |= os.O_NOFOLLOW
            try:
                partial_descriptor = os.open(partial_path, partial_flags, 0o600)
            except FileExistsError:
                continue
            try:
                final_descriptor = os.open(final_path, final_flags, 0o600)
            except FileExistsError:
                os.close(partial_descriptor)
                self._unlink_artifact(partial_path)
                continue
            try:
                os.close(partial_descriptor)
                os.close(final_descriptor)
            except BaseException:
                self._unlink_artifact(partial_path)
                self._unlink_artifact(final_path)
                raise
            return partial_path, final_path
        raise SileroTTSProcessError("could not reserve a unique Silero artifact")

    def _sweep_stale_owned_artifacts(self, output_dir: Path) -> None:
        """Delete only exact Silero crash artifacts whose recorded owner is gone.

        Filenames include both PID and Linux process start ticks, so PID reuse
        cannot make a live supervisor look stale.  If procfs identity cannot be
        read, the artifact is retained.  Completed non-empty WAV files are never
        swept; only hidden partial/temp files and empty final reservations are
        eligible.
        """

        try:
            entries = tuple(output_dir.iterdir())
        except OSError:
            return
        for path in entries:
            match = (
                _OWNED_PARTIAL_RE.fullmatch(path.name)
                or _OWNED_TEMP_RE.fullmatch(path.name)
                or _OWNED_FINAL_RE.fullmatch(path.name)
            )
            if match is None:
                continue
            owner_pid = int(match.group("pid"))
            owner_start = int(match.group("start"))
            if owner_start <= 0:
                continue
            live_start = _linux_process_start_ticks(owner_pid)
            if live_start == owner_start:
                continue
            if live_start is None and (Path("/proc") / str(owner_pid)).exists():
                continue
            try:
                info = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                continue
            if _OWNED_FINAL_RE.fullmatch(path.name) is not None and info.st_size != 0:
                continue
            self._unlink_artifact(path)

    def _ensure_process(self, deadline: float) -> subprocess.Popen[bytes]:
        self._notice_exited_worker()
        if self._proc is not None:
            return self._proc
        if self._clock() >= deadline:
            raise SileroTTSTimeoutError(
                "Silero synthesis timed out before worker startup"
            )

        worker_path = self.config.worker_path.expanduser().resolve()
        model_path = self.config.model_path.expanduser().resolve()
        if not worker_path.is_file():
            raise SileroTTSConfigurationError("Silero worker script does not exist")
        if not model_path.is_file():
            raise SileroTTSConfigurationError("Silero model file does not exist")

        output_root = self.config.output_dir.expanduser().resolve()
        command = [
            str(self.config.executable),
            "-I",
            str(worker_path),
            "--parent-pid",
            str(os.getpid()),
            "--model-path",
            str(model_path),
            "--model-sha256",
            self.config.model_sha256,
            "--output-root",
            str(output_root),
            "--idle-timeout",
            str(self.config.idle_timeout_seconds),
            "--max-requests",
            str(self.config.recycle_after_requests),
            "--max-text-chars",
            str(self.config.max_chars),
            "--max-request-bytes",
            str(self.config.max_request_bytes),
            "--max-output-bytes",
            str(self.config.max_output_bytes),
        ]
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "bufsize": 0,
            "close_fds": True,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        proc = self._popen_before_deadline(command, kwargs, deadline)
        if proc.stdin is None or proc.stdout is None:
            self._dispose_untracked_process(proc)
            raise SileroTTSProcessError("Silero worker pipes were not created")
        if os.name == "posix":
            os.set_blocking(proc.stdin.fileno(), False)
            os.set_blocking(proc.stdout.fileno(), False)
        self._proc = proc
        self._start_reaper(proc)
        self._stdout_buffer.clear()
        self._process_requests = 0
        self._starts += 1
        if self._clock() >= deadline:
            self._stop_process(count_recycle=True)
            raise SileroTTSTimeoutError(
                "Silero synthesis timed out during worker startup"
            )
        self._sample_rss(proc)
        if self._clock() >= deadline:
            self._stop_process(count_recycle=True)
            raise SileroTTSTimeoutError(
                "Silero synthesis timed out during worker startup"
            )
        return proc

    def _popen_before_deadline(
        self,
        command: list[str],
        kwargs: dict[str, Any],
        deadline: float,
    ) -> subprocess.Popen[bytes]:
        """Contain a child even when an injected ``Popen`` blocks past deadline.

        Python cannot cancel an in-progress ``Popen`` call.  A daemon launcher
        therefore retains ownership until the caller atomically accepts the
        handle.  If the deadline wins, a child returned later is killed as a
        process group and its leader is reaped by that launcher.
        """

        completed = threading.Event()
        state_lock = threading.Lock()
        state: dict[str, Any] = {"abandoned": False}
        with self._launch_condition:
            self._launches_inflight += 1

        def launch() -> None:
            try:
                try:
                    proc = self._popen_factory(command, **kwargs)
                except BaseException as exc:
                    with state_lock:
                        state["error"] = exc
                    completed.set()
                    return

                with state_lock:
                    if state["abandoned"]:
                        dispose = True
                    else:
                        state["proc"] = proc
                        dispose = False
                completed.set()
                if dispose:
                    self._dispose_untracked_process(proc)
            finally:
                with self._launch_condition:
                    self._launches_inflight -= 1
                    self._launch_condition.notify_all()

        launcher = threading.Thread(
            target=launch,
            name="silero-process-launcher",
            daemon=True,
        )
        try:
            launcher.start()
        except RuntimeError as exc:
            with self._launch_condition:
                self._launches_inflight -= 1
                self._launch_condition.notify_all()
            raise SileroTTSProcessError(
                "failed to start Silero process-launcher thread"
            ) from exc
        remaining = deadline - self._clock()
        if remaining > 0:
            completed.wait(remaining)

        dispose: subprocess.Popen[bytes] | None = None
        with state_lock:
            proc = state.pop("proc", None)
            error = state.pop("error", None)
            if proc is None and error is None:
                state["abandoned"] = True
            elif self._clock() >= deadline and proc is not None:
                # Boundary race: Popen returned as wait expired, before this
                # thread could mark the launch abandoned.
                dispose = proc
                proc = None
                state["abandoned"] = True

        if dispose is not None:
            self._dispose_untracked_process(dispose)
        if proc is not None:
            return proc
        if error is not None:
            if isinstance(error, (OSError, ValueError)):
                raise SileroTTSProcessError(
                    "failed to start isolated Silero worker"
                ) from error
            if isinstance(error, Exception):
                raise SileroTTSProcessError(
                    "Silero worker launcher failed"
                ) from error
            raise error
        raise SileroTTSTimeoutError(
            "Silero synthesis timed out during worker startup"
        )

    def _dispose_untracked_process(self, proc: subprocess.Popen[bytes]) -> None:
        """Best-effort containment for a child not published as ``self._proc``."""

        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                try:
                    proc.kill()
                except OSError:
                    pass
        else:
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait()
        except (OSError, ValueError):
            pass
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _start_reaper(self, proc: subprocess.Popen[bytes]) -> None:
        """Reap a worker that exits on its own while the client is otherwise idle."""

        def reap() -> None:
            try:
                proc.wait()
            except BaseException:
                return
            with self._lock:
                if self._proc is proc:
                    self._proc = None
                    self._stdout_buffer.clear()
                    self._process_requests = 0

        thread = threading.Thread(
            target=reap,
            name=f"silero-reaper-{proc.pid}",
            daemon=True,
        )
        self._reaper = thread
        thread.start()

    def _notice_exited_worker(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is None:
            return
        exit_code = proc.returncode
        if exit_code not in (0, None):
            self._record_failure("worker_exit")
        self._stop_process(count_recycle=True)

    def _write_request(
        self,
        proc: subprocess.Popen[bytes],
        payload: bytes,
        deadline: float,
    ) -> None:
        if proc.poll() is not None or proc.stdin is None:
            raise _WorkerExited("Silero worker exited before request write")
        descriptor = proc.stdin.fileno()
        selector = selectors.DefaultSelector()
        selector.register(descriptor, selectors.EVENT_WRITE)
        view = memoryview(payload)
        written = 0
        try:
            while written < len(payload):
                self._sample_rss(proc)
                if proc.poll() is not None:
                    raise _WorkerExited("Silero worker exited during request write")
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise SileroTTSTimeoutError("Silero synthesis timed out during request write")
                events = selector.select(
                    min(remaining, self.config.rss_poll_interval_seconds)
                )
                if not events:
                    continue
                try:
                    count = os.write(descriptor, view[written:])
                except BlockingIOError:
                    continue
                except (BrokenPipeError, OSError) as exc:
                    raise _WorkerExited("Silero worker pipe closed") from exc
                if count <= 0:
                    raise _WorkerExited("Silero worker accepted no request bytes")
                written += count
        finally:
            view.release()
            selector.close()

    def _read_response(
        self, proc: subprocess.Popen[bytes], deadline: float
    ) -> dict[str, Any]:
        if proc.stdout is None:
            raise _WorkerExited("Silero worker stdout is unavailable")
        descriptor = proc.stdout.fileno()
        selector = selectors.DefaultSelector()
        selector.register(descriptor, selectors.EVENT_READ)
        try:
            while True:
                newline = self._stdout_buffer.find(b"\n")
                if newline >= 0:
                    line = bytes(self._stdout_buffer[:newline])
                    del self._stdout_buffer[: newline + 1]
                    if self._stdout_buffer:
                        raise _ProtocolFailure("unsolicited Silero worker output")
                    if not line or len(line) > _MAX_RESPONSE_BYTES:
                        raise _ProtocolFailure("invalid Silero response length")
                    try:
                        decoded = line.decode("utf-8", errors="strict")
                        response = json.loads(decoded)
                    except (UnicodeError, json.JSONDecodeError) as exc:
                        raise _ProtocolFailure("malformed Silero JSON response") from exc
                    if not isinstance(response, dict):
                        raise _ProtocolFailure("Silero response is not an object")
                    return response

                if len(self._stdout_buffer) > _MAX_RESPONSE_BYTES:
                    raise _ProtocolFailure("Silero response exceeded size limit")
                if proc.poll() is not None:
                    # Drain bytes already committed to the pipe before declaring a crash.
                    try:
                        chunk = os.read(descriptor, 8192)
                    except BlockingIOError:
                        chunk = b""
                    if chunk:
                        self._stdout_buffer.extend(chunk)
                        continue
                    raise _WorkerExited("Silero worker exited during request")
                self._sample_rss(proc)

                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise SileroTTSTimeoutError("Silero synthesis timed out")
                wait_for = min(remaining, self.config.rss_poll_interval_seconds)
                events = selector.select(wait_for)
                if not events:
                    continue
                try:
                    chunk = os.read(descriptor, 8192)
                except BlockingIOError:
                    continue
                if not chunk:
                    # EOF may race the worker's final max_requests response.
                    # Drain-before-fail even if poll/reaper has not observed exit.
                    newline = self._stdout_buffer.find(b"\n")
                    if newline >= 0:
                        continue
                    raise _WorkerExited("Silero worker closed its response pipe")
                self._stdout_buffer.extend(chunk)
        finally:
            selector.close()

    def _sample_rss(self, proc: subprocess.Popen[bytes]) -> int:
        try:
            rss_bytes = int(self._rss_reader(proc.pid))
        except (OSError, ValueError, TypeError) as exc:
            if proc.poll() is not None:
                raise _WorkerExited("Silero worker exited before RSS observation") from exc
            raise SileroTTSProcessError("Silero worker RSS cannot be observed") from exc
        if rss_bytes < 0:
            raise SileroTTSProcessError("Silero worker returned invalid RSS")
        self._last_rss_bytes = rss_bytes
        self._peak_rss_bytes = max(self._peak_rss_bytes, rss_bytes)
        if rss_bytes > self.config.max_rss_bytes:
            raise SileroTTSMemoryError(
                "Silero worker exceeded its resident-memory limit"
            )
        return rss_bytes

    def _validate_response(
        self, response: dict[str, Any], request_id: str, partial_path: Path
    ) -> int:
        if response.get("schema") != RESPONSE_SCHEMA:
            raise _ProtocolFailure("Silero response schema mismatch")
        if response.get("id") != request_id:
            raise _ProtocolFailure("Silero response id mismatch")
        ok = response.get("ok")
        if ok is not True:
            if ok is not False:
                raise _ProtocolFailure("Silero response has no boolean status")
            error = response.get("error")
            if not isinstance(error, dict) or not isinstance(error.get("code"), str):
                raise _ProtocolFailure("Silero error response is malformed")
            raise SileroTTSProcessError(
                f"Silero worker rejected synthesis ({error['code']})"
            )
        rss_bytes = response.get("rss_bytes")
        peak_rss_bytes = response.get("peak_rss_bytes")
        for name, value in (
            ("rss_bytes", rss_bytes),
            ("peak_rss_bytes", peak_rss_bytes),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise _ProtocolFailure(f"Silero response has invalid {name}")
        assert isinstance(rss_bytes, int) and isinstance(peak_rss_bytes, int)
        if peak_rss_bytes < rss_bytes:
            raise _ProtocolFailure("Silero response peak RSS is below current RSS")
        observed_peak = max(rss_bytes, peak_rss_bytes)
        self._last_rss_bytes = rss_bytes
        self._peak_rss_bytes = max(self._peak_rss_bytes, observed_peak)
        if observed_peak > self.config.max_rss_bytes:
            raise SileroTTSMemoryError(
                "Silero worker exceeded its resident-memory limit"
            )
        if response.get("output_path") != str(partial_path):
            raise _ProtocolFailure("Silero worker returned an unexpected output path")
        if response.get("model") != self.config.model:
            raise _ProtocolFailure("Silero worker returned an unexpected model")
        if response.get("speaker") != self.config.speaker:
            raise _ProtocolFailure("Silero worker returned an unexpected speaker")
        sample_rate = response.get("sample_rate")
        if (
            not isinstance(sample_rate, int)
            or isinstance(sample_rate, bool)
            or sample_rate != self.config.sample_rate
        ):
            raise _ProtocolFailure("Silero worker returned an unexpected sample rate")
        byte_count = response.get("bytes")
        if (
            not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or byte_count <= 0
            or byte_count > self.config.max_output_bytes
        ):
            raise _ProtocolFailure("Silero worker returned an invalid byte count")
        return byte_count

    def _validate_wav(self, path: Path, *, expected_bytes: int) -> None:
        try:
            info = path.lstat()
        except OSError as exc:
            raise SileroTTSProcessError("Silero worker did not create an artifact") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SileroTTSProcessError("Silero artifact is not a private regular file")
        if info.st_size <= 44 or info.st_size > self.config.max_output_bytes:
            raise SileroTTSProcessError("Silero worker produced an invalid WAV size")
        if info.st_size != expected_bytes:
            raise SileroTTSProcessError(
                "Silero artifact size does not match the worker response"
            )
        try:
            with wave.open(str(path), "rb") as audio:
                if (
                    audio.getnchannels() <= 0
                    or audio.getsampwidth() <= 0
                    or audio.getframerate() != self.config.sample_rate
                    or audio.getnframes() <= 0
                ):
                    raise SileroTTSProcessError(
                        "Silero worker produced an empty WAV artifact"
                    )
        except (EOFError, wave.Error) as exc:
            raise SileroTTSProcessError(
                "Silero worker produced a malformed WAV artifact"
            ) from exc

    def _stop_process(self, *, count_recycle: bool) -> None:
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        self._stdout_buffer.clear()
        self._process_requests = 0
        if count_recycle:
            self._recycles += 1

        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass

        if os.name == "posix":
            self._kill_posix_group(proc)
        else:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=self.config.terminate_grace_seconds)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
                proc.wait()

        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _kill_posix_group(self, proc: subprocess.Popen[bytes]) -> None:
        pgid = proc.pid  # start_new_session makes the child its own group leader.
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            try:
                proc.terminate()
            except OSError:
                pass

        deadline = self._clock() + self.config.terminate_grace_seconds
        while self._process_group_exists(pgid) and self._clock() < deadline:
            time.sleep(min(0.01, max(0.0, deadline - self._clock())))
        if self._process_group_exists(pgid):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                try:
                    proc.kill()
                except OSError:
                    pass
        try:
            proc.wait(timeout=self.config.terminate_grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            proc.wait()

    @staticmethod
    def _process_group_exists(pgid: int) -> bool:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _unlink_artifact(path: Path | None) -> None:
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


# Concise name for MediaAudio wiring while retaining the explicit client name
# in tests/observability documentation.
SileroTTS = SileroTTSClient
SileroProcessTTS = SileroTTSClient


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_SAMPLE_RATE",
    "DEFAULT_SPEAKER",
    "REQUEST_SCHEMA",
    "RESPONSE_SCHEMA",
    "SileroProcessTTS",
    "SileroTTS",
    "SileroTTSClient",
    "SileroTTSConfig",
    "SileroTTSConfigurationError",
    "SileroTTSMemoryError",
    "SileroTTSProcessError",
    "SileroTTSTimeoutError",
    "read_process_rss_bytes",
]
