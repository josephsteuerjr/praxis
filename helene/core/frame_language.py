"""Language of product-authored model context, independent of the UI locale.

Only callers that own a prompt/template use this module. It never translates
user messages, tool data, personal Markdown or a completed model response.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import re
from functools import lru_cache, wraps
from pathlib import Path
from string import Formatter

LANGUAGES = ("ru", "en", "fr", "zh")
_ROOT = Path(__file__).with_name("frame_text")
_override = contextvars.ContextVar("helene_frame_language", default=None)
_pinned = contextvars.ContextVar("helene_frame_settings", default=None)
_last_settings: dict[str, dict] = {}


def settings() -> dict:
    """Read the current runner's config, also inside inherited worker processes.

    Atomic config replacement is normal. A temporarily unreadable document
    retains the last valid settings for that exact path, never another agent's.
    """
    raw = os.environ.get("HELENE_CONFIG", "")
    if not raw:
        return {}
    key = os.path.normcase(os.path.abspath(raw))
    try:
        value = json.loads(Path(raw).read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("configuration is not an object")
        selected = {"agent": value.get("agent", {})}
        _last_settings[key] = selected
        return selected
    except (OSError, ValueError):
        return _last_settings.get(key, {})


def language() -> str:
    selected = _override.get()
    if selected is not None:
        return selected
    pinned = _pinned.get()
    if pinned is not None:
        return pinned['language']
    agent = settings().get("agent")
    selected = agent.get("frame_language", "ru") if isinstance(agent, dict) else "ru"
    return selected if selected in LANGUAGES else "ru"


def living_prose_enabled() -> bool:
    pinned = _pinned.get()
    if pinned is not None:
        return pinned['living_prose']
    agent = settings().get("agent")
    return not isinstance(agent, dict) or agent.get("living_prose", True) is not False


@contextlib.contextmanager
def pin():
    """One coherent language choice for a turn or standalone frame read.

    A saved change belongs to the next turn; it cannot produce a half-translated
    frame or re-enable a resident halfway through an existing tool loop.
    Nested builders share the snapshot and avoid repeated config I/O per label.
    """
    if _pinned.get() is not None:
        yield
        return
    agent = settings().get('agent')
    agent = agent if isinstance(agent, dict) else {}
    selected = _override.get() or agent.get('frame_language', 'ru')
    token = _pinned.set({'language': selected if selected in LANGUAGES else 'ru',
                         'living_prose': agent.get('living_prose', True) is not False})
    try:
        yield
    finally:
        _pinned.reset(token)


def pinned(function):
    @wraps(function)
    def invoke(*args, **kwargs):
        with pin():
            return function(*args, **kwargs)
    return invoke


@contextlib.contextmanager
def use(language: str):
    if language not in LANGUAGES:
        raise ValueError("unsupported frame language")
    token = _override.set(language)
    try:
        yield
    finally:
        _override.reset(token)


@lru_cache(maxsize=16)
def _catalog(path: str, mtime_ns: int, size: int) -> tuple[dict[str, str], str]:
    raw = Path(path).read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("frame catalog must be an object")
    return value, hashlib.sha256(raw).hexdigest()


def _loaded(language: str) -> tuple[dict[str, str], str]:
    pinned = _pinned.get()
    snapshots = pinned.setdefault('catalogs', {}) if pinned is not None else None
    if snapshots is not None and language in snapshots:
        return snapshots[language]
    path = _ROOT / f"{language}.json"
    stat = path.stat()
    loaded = _catalog(str(path), stat.st_mtime_ns, stat.st_size)
    if snapshots is not None:
        snapshots[language] = loaded
    return loaded


def catalog(language: str) -> dict[str, str]:
    return _loaded(language)[0]


def owned_label(value: str) -> str:
    """Display an explicitly catalogued product label, preserving unknown extensions.

    Call only for owned headings, enum captions or table metadata. Never apply to
    user messages, authored document bodies, guest slots or source identifiers.
    """
    from frame_labels import KEYS
    key = KEYS.get(value)
    return text(key) if key is not None else value


def text(key: str, **values) -> str:
    """Format an owned template; substitutions are data and are never translated."""
    selected = catalog(language())
    template = selected.get(key)
    if not isinstance(template, str) or not template:
        raise KeyError(f"missing frame translation: {language()}:{key}")
    expected = {name for _, name, _, _ in Formatter().parse(template) if name is not None}
    if expected != set(values):
        raise ValueError(f"frame template parameters differ: {key}")
    return template.format_map(values)


def match(key: str, value: str, *, source_language: str | None = None):
    """Read an owned rendered row using the same localized template.

    Labels are escaped literally, never interpreted as regular expressions.
    This is for internal presentation rows, not authorization or user input.
    """
    template = catalog(source_language or language())[key]
    parts, seen = [], set()
    for literal, field, _format, _conversion in Formatter().parse(template):
        parts.append(re.escape(literal))
        if field is not None:
            if not field.isidentifier():
                raise ValueError('template field must be a simple identifier')
            parts.append(f'(?P={field})' if field in seen else f'(?P<{field}>.*?)')
            seen.add(field)
    return re.fullmatch(''.join(parts), value, flags=re.DOTALL)


def fingerprint() -> str:
    selected = language()
    return f"{selected}:{int(living_prose_enabled())}:{_loaded(selected)[1]}"
