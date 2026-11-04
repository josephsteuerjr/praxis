"""Explicit, reversible runtime policy for context presentation (not authority)."""
from __future__ import annotations

import json
import os
from pathlib import Path

BASE = Path(os.environ.get('PRAXIS_BASE') or Path(__file__).resolve().parent)
PATH = BASE / 'memory' / 'policies' / 'context-continuity.json'
SCHEMA = 'praxis.context-continuity.v1'
HOST_TEXT_DEFAULT = 4000
LEGACY = dict(continuation_mode='legacy', tool_inline_chars=4000,
              host_text_chars=HOST_TEXT_DEFAULT, recent_work_chars=0, durable_host_full=False)


def read() -> dict:
    """Missing/invalid migration retains the previous explicit behavior."""
    try:
        value = json.loads(PATH.read_text(encoding='utf-8'))
        if value.get('schema') != SCHEMA:
            return dict(LEGACY)
        if value.get('continuation_mode') not in ('legacy', 'canonical'):
            return dict(LEGACY)
        if type(value.get('durable_host_full')) is not bool:
            return dict(LEGACY)
        for key in ('tool_inline_chars', 'host_text_chars', 'recent_work_chars'):
            if type(value.get(key)) is not int or value[key] < (0 if key == 'recent_work_chars' else 1):
                return dict(LEGACY)
        return {key: value[key] for key in LEGACY}
    except (OSError, ValueError, TypeError, AttributeError):
        return dict(LEGACY)


def inline_chars() -> int:
    return read()['tool_inline_chars']
