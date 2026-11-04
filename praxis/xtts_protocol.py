"""Private XTTS wire/intent contracts. This module never imports a model."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA = "praxis.xtts.v1"
INTENT_MIME = "application/vnd.praxis.xtts-intent+json"
MAX_REQUEST = 64 * 1024
MAX_AUDIO = 64 * 1024 * 1024
MAX_TEXT = 4000
HEX = re.compile(r"[0-9a-f]{64}\Z")
REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z")
ERROR_CODES = frozenset({
    'busy', 'warming', 'awaiting_voice', 'profile_changed', 'cancelled', 'timeout',
    'model_load_failed', 'unauthorized', 'invalid_text', 'voice_disabled',
    'unknown_voice', 'voice_deleted', 'invalid_voice_name', 'voice_conflict',
    'invalid_reference', 'reference_changed', 'reference_duration',
    'reference_decode_timeout', 'reference_too_large', 'active_voice',
    'builtin_voice', 'voice_not_configured', 'invalid_selection', 'invalid_voice_store',
    'config_changed',
})


class XttsError(RuntimeError):
    """A text-free, stable error code suitable for returning to the caller."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def encoded(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(value: dict) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def file_digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def validate_intent(value: object) -> dict:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise XttsError("invalid_intent")
    text = value.get("text")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_TEXT or len(text) > MAX_TEXT:
        raise XttsError("invalid_text")
    if not REQUEST_ID.fullmatch(str(value.get("request_id") or "")):
        raise XttsError("invalid_request_id")
    if 'generation_id' in value and not REQUEST_ID.fullmatch(str(value['generation_id'])):
        raise XttsError('invalid_generation_id')
    for field in ("model_id", "profile_id"):
        if not HEX.fullmatch(str(value.get(field) or "")):
            raise XttsError("invalid_profile")
    if not isinstance(value.get("target_chat_id"), str) or not value["target_chat_id"]:
        raise XttsError("invalid_target")
    if value.get("scope") not in {"owner", "family", "known", "unknown", "group"}:
        raise XttsError("invalid_scope")
    if not isinstance(value.get("run_id"), str) or not value["run_id"]:
        raise XttsError("missing_run")
    if len(encoded(value)) > MAX_REQUEST:
        raise XttsError("oversized_intent")
    return value


def read_intent(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REQUEST:
        raise XttsError("invalid_intent_file")
    try:
        value = json.loads(path.read_bytes())
    except (ValueError, UnicodeError) as exc:
        raise XttsError("invalid_intent") from exc
    return validate_intent(value)
