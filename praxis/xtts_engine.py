"""Resident XTTS weights and bounded conditioning cache, owned by xtts_service."""
from __future__ import annotations

from collections import OrderedDict
import json
from pathlib import Path
import re
import time
import wave

from xtts_protocol import HEX, MAX_AUDIO, MAX_TEXT, XttsError, digest, file_digest


def split_text(text: str, tokenizer, language: str, token_limit: int):
    """Keep every word while respecting the actual model tokenizer budget."""
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_TEXT or len(text) > MAX_TEXT:
        raise XttsError("invalid_text")

    def bounded(part):
        part = part.strip()
        if not part:
            return
        if len(tokenizer.encode(part, lang=language)) < token_limit:
            yield part
            return
        middle = len(part) // 2
        cut = part.rfind(" ", 0, middle + 1)
        if cut <= 0:
            cut = middle
        if cut <= 0:
            raise XttsError("token_budget")
        yield from bounded(part[:cut])
        yield from bounded(part[cut:])

    for sentence in re.split(r"(?<=[.!?…])\s+|\n+", text):
        yield from bounded(sentence)


class XttsEngine:
    def __init__(self, model: Path, profile: Path, *, device="cpu", threads=2):
        self.model_path, self.profile_path = Path(model), Path(profile)
        self.device, self.threads = device, threads
        self.engine = None
        self.weights_ready = False
        self.phase = "warming"
        self.model_id = ""
        self.runtime_id = ""
        self.profile_id = ""
        self.profile = {}
        self.cache = OrderedDict()
        self.conditioning_loads = 0
        self.requests = 0
        self.load_seconds = None
        self.last_seconds = None
        self.error = ""
        self.error_type = ""
        self.startup_stage = "integrity"
        self.warmed_profiles = set()

    def load(self):
        started = time.monotonic()
        manifest = json.loads((self.model_path / "manifest.json").read_text())
        files = manifest.get("files", {})
        required = {"config.json", "model.pth", "vocab.json", "speakers_xtts.pth"}
        if not required.issubset(files):
            raise XttsError("invalid_model_manifest")
        for name, expected in files.items():
            if Path(name).name != name or not HEX.fullmatch(str(expected)):
                raise XttsError("invalid_model_manifest")
            path = self.model_path / name
            if path.is_symlink() or file_digest(path) != expected:
                raise XttsError("model_integrity")
        self.model_id = digest(files)
        from importlib.metadata import version
        self.runtime_id = digest({name: version(name) for name in ("coqui-tts", "torch", "transformers")})
        import torch
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts, _legacy_safe_globals
        torch.set_num_threads(self.threads)
        config = XttsConfig()
        config.load_json(str(self.model_path / "config.json"))
        class ReadOnlyXtts(Xtts):
            def _load_checkpoint(self, model_path):
                # Coqui 0.28's legacy adapter torch.save()s a converted config
                # over the input checkpoint. We need only its state dict and
                # must retain the verified upstream file across every restart.
                with _legacy_safe_globals():
                    checkpoint = torch.load(model_path, map_location=torch.device("cpu"), weights_only=True)
                if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("model"), dict):
                    raise XttsError("invalid_checkpoint")
                return checkpoint["model"]
        self.startup_stage = "weights"
        self.engine = ReadOnlyXtts(config)
        self.engine.load_checkpoint(config, checkpoint_dir=str(self.model_path), eval=True)
        self.engine.to(self.device)
        self.weights_ready = True
        self.profile = json.loads(self.profile_path.read_text())
        self.startup_stage = "conditioning"
        if not self.profile.get("speaker") and not self.profile.get("reference"):
            self.phase = "awaiting_voice"
        else:
            self.profile_id = self._profile_identity()
            self._conditioning(lambda: False)
            self.startup_stage = "warmup"
            # A real internal inference is required before advertising readiness.
            for _ in self._chunks("Проверка голоса.", lambda: False):
                pass
            self.warmed_profiles.add(self.profile_id)
            self.phase = "ready"
        self.load_seconds = time.monotonic() - started
        self.startup_stage = "complete"

    def _profile_identity(self, profile=None, *, verify_reference=True):
        profile = dict(self.profile if profile is None else profile)
        reference = str(profile.pop("reference", "") or "")
        profile["reference_sha256"] = ((file_digest(Path(reference)) if verify_reference
                                        else profile.get('reference_sha256') or file_digest(Path(reference)))
                                       if reference else "")
        return digest({"model_id": self.model_id, "runtime_id": self.runtime_id,
                       "conditioning_contract": 1, "profile": profile})

    def _conditioning(self, stopped, profile=None, expected_id=None):
        if stopped():
            raise XttsError("cancelled")
        key = self._profile_identity() if profile is None else self._profile_identity(profile)
        # A reference changed in place needs explicit reconfiguration/warmup;
        # queued speech must never silently acquire a different voice.
        expected = self.profile_id if profile is None else expected_id
        if expected and key != expected:
            raise XttsError("profile_changed")
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        chosen = self.profile if profile is None else profile
        reference = chosen.get("reference")
        if reference:
            value = self.engine.get_conditioning_latents(audio_path=[str(reference)])
        else:
            speaker = self.engine.speaker_manager.speakers.get(str(chosen.get("speaker") or ""))
            if not isinstance(speaker, dict):
                raise XttsError("unknown_speaker")
            try:
                value = (speaker["gpt_cond_latent"], speaker["speaker_embedding"])
            except KeyError as exc:
                raise XttsError("speaker_format") from exc
        if stopped():
            raise XttsError("cancelled")
        if (self._profile_identity() if profile is None else self._profile_identity(profile)) != key:
            raise XttsError("profile_changed")
        self.conditioning_loads += 1
        self.cache[key] = value
        while len(self.cache) > 4:
            self.cache.popitem(last=False)
        return value

    def _chunks(self, text, stopped, profile=None, expected_id=None):
        conditioning, speaker = (self._conditioning(stopped) if profile is None
                                 else self._conditioning(stopped, profile, expected_id))
        language = str((self.profile if profile is None else profile).get("language") or "ru")
        limit = int(self.engine.args.gpt_max_text_tokens)
        for sentence in split_text(text, self.engine.tokenizer, language, limit):
            if stopped():
                raise XttsError("cancelled")
            for chunk in self.engine.inference_stream(
                    sentence, language, conditioning, speaker, enable_text_splitting=False):
                if stopped():
                    raise XttsError("cancelled")
                yield chunk

    def prepare_voice(self, profile, stopped):
        if not self.weights_ready:
            raise XttsError(self.error or self.phase)
        identity = self._profile_identity(profile)
        if identity not in self.warmed_profiles:
            for _ in self._chunks('Проверка голоса.', stopped, profile, identity):
                pass
            self.warmed_profiles.add(identity)
        return identity

    def set_active_voice(self, profile, identity):
        self.profile = dict(profile)
        self.profile_id = identity
        self.phase = 'ready' if identity else 'awaiting_voice'

    def synthesize(self, text, path: Path, *, model_id, profile_id, stopped, profile=None, on_pcm=None):
        if (profile is None and self.phase != "ready") or (profile is not None and not self.weights_ready):
            raise XttsError(self.error or self.phase)
        expected = self.profile_id if profile is None else self._profile_identity(profile)
        if (model_id, profile_id) != (self.model_id, expected):
            raise XttsError("profile_changed")
        started = time.monotonic()
        import numpy as np
        total = 0
        try:
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(24000)
                chunks = (self._chunks(text, stopped) if profile is None
                          else self._chunks(text, stopped, profile, expected))
                for chunk in chunks:
                    pcm = np.clip(chunk.detach().cpu().numpy(), -1, 1)
                    raw = (pcm * 32767).astype("<i2").tobytes()
                    total += len(raw)
                    if total > MAX_AUDIO - 44:
                        raise XttsError("oversized_audio")
                    audio.writeframes(raw)
                    if on_pcm is not None:
                        on_pcm(raw)
            if stopped():
                raise XttsError("cancelled")
            if not total:
                raise XttsError("empty_audio")
            self.requests += 1
            self.warmed_profiles.add(profile_id)
            self.last_seconds = time.monotonic() - started
            return {"seconds": self.last_seconds, "audio_seconds": total / 48000,
                    "model_id": self.model_id, "profile_id": profile_id,
                    "conditioning_loads": self.conditioning_loads}
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def health(self):
        manager = getattr(self.engine, "speaker_manager", None)
        speakers = getattr(manager, "speakers", {}) or {}
        return {"phase": self.phase, "ready": self.phase == "ready",
                "model_ready": self.weights_ready, "model_id": self.model_id,
                "profile_id": self.profile_id, "device": self.device,
                "voice_id": self.profile.get('voice_id', ''),
                "speakers": sorted(speakers) if self.weights_ready else [],
                "requests": self.requests, "conditioning_loads": self.conditioning_loads,
                "load_seconds": self.load_seconds, "last_seconds": self.last_seconds,
                "error": self.error, "error_type": self.error_type,
                "startup_stage": self.startup_stage}
