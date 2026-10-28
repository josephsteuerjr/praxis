"""Explicit private binding for the one AbstractDL root group.

Frame/KEAT selectors and policy enrollments choose a rollout, not the identity of
its authorized group. Never infer a root from a list of staged rooms or a message.
The same variable works for historical env-based deployments; absent/malformed
configuration disables only root-group-specific powers.
"""
from __future__ import annotations

import os
import re

ROOT_ROOM_ENV = 'PRAXIS_ABSTRACTDL_ROOT_CHAT_ID'
_ROOT_ROOM = re.compile(r'-100[1-9][0-9]{8,15}\Z', re.ASCII)


def root_chat_id(env=None) -> str | None:
    """Canonical negative Telegram root ID, or None (never a guessed room)."""
    env = os.environ if env is None else env
    value = env.get(ROOT_ROOM_ENV)
    return value if type(value) is str and _ROOT_ROOM.fullmatch(value) else None


def root_peer_id(env=None) -> int | None:
    """Exact integer form for Telethon/admin hands; None disables their scope."""
    value = root_chat_id(env)
    return int(value) if value is not None else None
