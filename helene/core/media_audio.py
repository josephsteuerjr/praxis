"""Speech-to-text and text-to-speech backends for Praxis.

The module deliberately keeps heavyweight dependencies optional.  Importing
the module does not import or load faster-whisper/XTTS, and model objects are
created only on the first request.  Inference is serialized because the target
server is a small two-vCPU host where parallel model execution would mainly add
memory pressure.
"""

from __future__ import annotations

import gc
import os
import tempfile
import threading
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


# This is the canonical mapping used by faster-whisper 1.2.1 for `large-v3-turbo`.
DEFAULT_STT_MODEL = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
DEFAULT_STT_REVISION = None


class AudioBackendError(RuntimeError):
    """Base class for an expected audio-backend failure."""


class AudioConfigurationError(AudioBackendError):
    """The backend is not provisioned or its configuration is invalid."""


class AudioDependencyError(AudioBackendError):
    """An optional runtime dependency is missing."""


class AudioProcessingError(AudioBackendError):
    """A configured backend failed while processing media."""


class AudioBusyError(AudioBackendError):
    """The single inference worker is already serving another request."""


class AudioNotReadyError(AudioBackendError):
    """A caller required the resident model, but warmup has not completed."""


def _env_bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise AudioConfigurationError(f"invalid boolean value: {value!r}")


def _env_int(
    value: str | None,
    default: int,
    *,
    name: str,
    minimum: int = 1,
) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise AudioConfigurationError(f"{name} must be an integer") from exc
    if parsed < minimum:
        raise AudioConfigurationError(f"{name} must be >= {minimum}")
    return parsed


@dataclass(frozen=True)
class AudioConfig:
    """Configuration for the audio stack."""

    model_dir: Path
    output_dir: Path
    stt_model: str = DEFAULT_STT_MODEL
    stt_revision: str | None = DEFAULT_STT_REVISION
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    stt_cpu_threads: int = 2
    stt_num_workers: int = 1
    stt_language: str | None = "ru"
    stt_beam_size: int = 5
    stt_vad_filter: bool = True
    stt_local_files_only: bool = True
    stt_keep_loaded: bool = True
    stt_max_bytes: int = 200 * 1024 * 1024
    tts_backend: str = "xtts"
    tts_max_chars: int = 4000

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AudioConfig":
        env = os.environ if environ is None else environ
        model_dir = Path(
            env.get("PRAXIS_AUDIO_MODEL_DIR", "/app/models/audio")
        ).expanduser()
        output_dir = Path(
            env.get(
                "PRAXIS_TTS_OUTPUT_DIR",
                str(Path(tempfile.gettempdir()) / "praxis-audio"),
            )
        ).expanduser()
        language = env.get("PRAXIS_STT_LANGUAGE", "ru").strip() or None
        revision_raw = env.get("PRAXIS_STT_REVISION", DEFAULT_STT_REVISION or "")
        revision = str(revision_raw or "").strip() or None

        return cls(
            model_dir=model_dir,
            output_dir=output_dir,
            stt_model=env.get("PRAXIS_STT_MODEL", DEFAULT_STT_MODEL).strip()
            or DEFAULT_STT_MODEL,
            stt_revision=revision,
            stt_device=env.get("PRAXIS_STT_DEVICE", "cpu").strip() or "cpu",
            stt_compute_type=env.get(
                "PRAXIS_STT_COMPUTE_TYPE", "int8"
            ).strip()
            or "int8",
            stt_cpu_threads=_env_int(
                env.get("PRAXIS_STT_CPU_THREADS"),
                2,
                name="PRAXIS_STT_CPU_THREADS",
            ),
            stt_num_workers=_env_int(
                env.get("PRAXIS_STT_NUM_WORKERS"),
                1,
                name="PRAXIS_STT_NUM_WORKERS",
            ),
            stt_language=language,
            stt_beam_size=_env_int(
                env.get("PRAXIS_STT_BEAM_SIZE"),
                5,
                name="PRAXIS_STT_BEAM_SIZE",
            ),
            stt_vad_filter=_env_bool(env.get("PRAXIS_STT_VAD_FILTER"), True),
            stt_local_files_only=_env_bool(
                env.get("PRAXIS_STT_LOCAL_FILES_ONLY"), True
            ),
            stt_keep_loaded=_env_bool(env.get("PRAXIS_STT_KEEP_LOADED"), True),
            stt_max_bytes=_env_int(
                env.get("PRAXIS_STT_MAX_BYTES"),
                200 * 1024 * 1024,
                name="PRAXIS_STT_MAX_BYTES",
            ),
            tts_backend=env.get("PRAXIS_TTS_BACKEND", "disabled").strip().lower() or "disabled",
            tts_max_chars=_env_int(
                env.get("PRAXIS_TTS_MAX_CHARS"),
                4000,
                name="PRAXIS_TTS_MAX_CHARS",
            ),
        )


class SpeechToText(Protocol):
    def transcribe(self, path: str | os.PathLike[str]) -> str: ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> Path: ...


class FasterWhisperSTT:
    """Lazy, single-worker faster-whisper adapter."""

    def __init__(
        self,
        config: AudioConfig,
        *,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self._model_factory = model_factory
        self._model: Any | None = None
        self._load_lock = threading.Lock()
        self._run_lock = threading.Lock()

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            factory = self._model_factory
            if factory is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise AudioDependencyError(
                        "faster-whisper is not installed"
                    ) from exc
                factory = WhisperModel

            try:
                self._model = factory(
                    self.config.stt_model,
                    device=self.config.stt_device,
                    compute_type=self.config.stt_compute_type,
                    cpu_threads=self.config.stt_cpu_threads,
                    num_workers=self.config.stt_num_workers,
                    download_root=str(self.config.model_dir / "whisper"),
                    local_files_only=self.config.stt_local_files_only,
                    revision=self.config.stt_revision,
                )
            except AudioBackendError:
                raise
            except Exception as exc:
                raise AudioConfigurationError(
                    "failed to load the configured faster-whisper model"
                ) from exc
            return self._model

    @property
    def is_loaded(self) -> bool:
        """Whether the process-local Whisper model is already resident."""

        return self._model is not None

    @property
    def is_busy(self) -> bool:
        """Best-effort health signal; admission still uses an atomic lock acquire."""

        return self._run_lock.locked()

    def transcribe(self, path: str | os.PathLike[str]) -> str:
        """Use the normal Praxis profile, waiting for the one inference worker."""

        return self._transcribe(
            path,
            language=self.config.stt_language,
            beam_size=self.config.stt_beam_size,
            wait_for_worker=True,
            require_loaded=False,
        )

    def transcribe_external(
        self,
        path: str | os.PathLike[str],
        *,
        language: str | None,
    ) -> str:
        """Use the normal decode profile without loading or queueing."""

        return self._transcribe(
            path,
            language=language,
            beam_size=self.config.stt_beam_size,
            wait_for_worker=False,
            require_loaded=True,
        )

    def _transcribe(
        self,
        path: str | os.PathLike[str],
        *,
        language: str | None,
        beam_size: int,
        wait_for_worker: bool,
        require_loaded: bool,
    ) -> str:
        source = Path(path).expanduser()
        if not source.is_file():
            raise AudioConfigurationError(f"audio input does not exist: {source}")
        size = source.stat().st_size
        if size == 0:
            raise AudioConfigurationError(f"audio input is empty: {source}")
        if size > self.config.stt_max_bytes:
            raise AudioConfigurationError(
                f"audio input is too large ({size} > {self.config.stt_max_bytes} bytes)"
            )
        if beam_size < 1:
            raise AudioConfigurationError("beam_size must be >= 1")

        acquired = False
        model: Any | None = None
        try:
            if require_loaded:
                if self._model is None:
                    raise AudioNotReadyError("Whisper warmup has not completed")
            else:
                # Keep the existing load-before-run lock ordering.  ``clear_cache``
                # takes the load lock first, so reversing it here could deadlock.
                model = self._get_model()

            acquired = self._run_lock.acquire(blocking=wait_for_worker)
            if not acquired:
                raise AudioBusyError("Whisper inference worker is busy")
            if require_loaded:
                model = self._model
                if model is None:
                    raise AudioNotReadyError("Whisper warmup has not completed")

            segments, _info = model.transcribe(
                str(source),
                language=language,
                beam_size=beam_size,
                vad_filter=self.config.stt_vad_filter,
                condition_on_previous_text=False,
            )
            parts = [
                str(segment.text).strip()
                for segment in segments
                if getattr(segment, "text", "").strip()
            ]
        except AudioBackendError:
            raise
        except Exception as exc:
            raise AudioProcessingError("failed to transcribe audio input") from exc
        finally:
            if acquired:
                self._run_lock.release()
            if model is not None and not self.config.stt_keep_loaded:
                self.clear_cache()
        return " ".join(parts)

    def clear_cache(self) -> None:
        with self._load_lock:
            with self._run_lock:
                self._model = None
        gc.collect()


class MediaAudio:
    """Hélène local Whisper + explicitly configured XTTS, without fallback."""
    def __init__(self, config=None, *, stt=None, tts=None):
        self.config = config or AudioConfig.from_env()
        self.stt = stt or FasterWhisperSTT(self.config)
        self.tts = tts

    def transcribe(self, path):
        return self.stt.transcribe(path)

    def synthesize(self, text, *, stopped=None):
        if self.tts is not None:
            return self.tts.synthesize(text)
        if self.config.tts_backend != "xtts":
            raise AudioConfigurationError(os.environ.get("HELENE_TTS_REASON") or "XTTS voice is disabled or not ready")
        from xtts_tts_client import synthesize
        return synthesize(text, self.config.output_dir, stopped=stopped)

    def clear_caches(self):
        self.stt.clear_cache()
        if self.tts is not None and hasattr(self.tts, "clear_cache"):
            self.tts.clear_cache()

_default_backend = None
_default_backend_lock = threading.Lock()

def set_default_backend(backend):
    global _default_backend
    with _default_backend_lock:
        _default_backend = backend

def get_default_backend():
    global _default_backend
    with _default_backend_lock:
        if _default_backend is None:
            _default_backend = MediaAudio()
        return _default_backend

def transcribe(path):
    return get_default_backend().transcribe(path)

def synthesize(text, *, stopped=None):
    return get_default_backend().synthesize(text, stopped=stopped)

def warm(*, stt=True, tts=True):
    result = {}
    backend = get_default_backend()
    if stt:
        try:
            backend.stt._get_model()
            result["stt"] = "ready"
        except AudioBackendError as exc:
            result["stt"] = str(exc)
    # XTTS lives in the separately owned worker, never imported into this runner.
    if tts:
        result["tts"] = os.environ.get("HELENE_TTS_REASON") or backend.config.tts_backend
    return result
