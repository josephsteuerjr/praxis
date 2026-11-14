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
from functools import lru_cache
from pathlib import Path
from string import Formatter

LANGUAGES = ("ru", "en", "fr", "zh")
_ROOT = Path(__file__).with_name("frame_text")
_override = contextvars.ContextVar("helene_frame_language", default=None)
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
    agent = settings().get("agent")
    selected = agent.get("frame_language", "ru") if isinstance(agent, dict) else "ru"
    return selected if selected in LANGUAGES else "ru"


def living_prose_enabled() -> bool:
    agent = settings().get("agent")
    return not isinstance(agent, dict) or agent.get("living_prose", True) is not False


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
    path = _ROOT / f"{language}.json"
    stat = path.stat()
    return _catalog(str(path), stat.st_mtime_ns, stat.st_size)


def catalog(language: str) -> dict[str, str]:
    return _loaded(language)[0]


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


def fingerprint() -> str:
    selected = language()
    return f"{selected}:{int(living_prose_enabled())}:{_loaded(selected)[1]}"
