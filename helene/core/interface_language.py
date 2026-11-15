"""Explicit interface-caption scope for shared core APIs; never selects agent context."""
from __future__ import annotations
from contextlib import contextmanager
from functools import wraps
import json
import os
from pathlib import Path
import frame_language


def language() -> str:
    config_path = os.environ.get('HELENE_CONFIG', '')
    if not config_path:
        return 'ru'  # Standalone legacy core has no edition interface preference.
    try:
        path = Path(config_path)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('oversized interface settings')
        config = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(config, dict):
            config = {}
    except (OSError, ValueError):
        config = {}
    # The edition runner already owns the app package. Lazy import preserves core
    # startup layering and reuses its actual automatic/native preference resolver.
    try:
        from localharness.ui_language import system_tags
        from localharness.language_settings import interface_language as select
    except ModuleNotFoundError as exc:
        if exc.name not in {'localharness', 'localharness.ui_language', 'localharness.language_settings'}:
            raise
        selected = config.get('lang')
        return selected if selected in frame_language.LANGUAGES else 'en'
    return select(config, system_tags())


@contextmanager
def use():
    with frame_language.use(language()):
        yield


def text(key: str, **values) -> str:
    with use():
        return frame_language.text(key, **values)


def localized(function):
    """Readonly API boundary only; authored journal/effect prose uses frame scope."""
    @wraps(function)
    def invoke(*args, **kwargs):
        with use():
            return function(*args, **kwargs)
    return invoke
