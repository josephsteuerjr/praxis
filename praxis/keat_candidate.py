"""Offline KEAT partition contract; no rendering, serving, IO or epoch policy.

Anchors identify occurrences in ONE exact role-tape snapshot, not matching text
across sliding windows. Borrowed message objects must not be mutated while used.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


class ContractError(ValueError):
    """The supplied evidence does not establish an exact partition."""


def _digest(value) -> str:
    # No default=str: lossy serialization cannot establish identity.
    def check(item):
        if item is None or type(item) in (str, bool, int, float):
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(k) is str for k in item):
            for child in item.values():
                check(child)
            return
        raise ContractError('unsupported JSON value')
    try:
        check(value)
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ContractError('unsupported JSON value') from exc
    return hashlib.sha256(raw).hexdigest()


def schema_digest(tools: list | None) -> str:
    """Full ordered offered schemas, including descriptions; None != [].

    This is NOT an epoch invalidation policy or a provider cache key.
    """
    if tools is not None and (type(tools) is not list or
                              any(type(t) is not dict for t in tools)):
        raise ContractError('invalid offered schemas')
    return _digest({'contract': 'keat-schemas-v1', 'tools': tools})


def _scope(audience, source_scope):
    if (type(audience) is not tuple or not audience or
            any(type(a) is not str or not a.strip() for a in audience) or
            len(set(audience)) != len(audience) or
            type(source_scope) is not str or not source_scope.strip()):
        raise ContractError('explicit audience and source scope required')


def _tape(messages):
    if (type(messages) is not list or any(
            type(m) is not dict or type(m.get('role')) is not str or
            not m['role'] or 'content' not in m for m in messages)):
        raise ContractError('invalid authoritative role tape')
    return _digest(messages)


@dataclass(frozen=True)
class Anchor:
    index: int
    message_digest: str


@dataclass(frozen=True)
class Boundary:
    version: int
    tape_digest: str
    count: int
    historical_end: Anchor | None
    active_start: Anchor | None
    audience: tuple[str, ...]
    source_scope: str


def bind_boundary(messages: list, *, historical_count: int,
                  audience: tuple[str, ...], source_scope: str) -> Boundary:
    """Bind an explicitly caller-chosen cut. Never infer overlap by content.

    None is only the beginning/end sentinel, never a missing interior anchor.
    The caller must establish provenance and choose the cut authoritatively.
    """
    _scope(audience, source_scope)
    digest = _tape(messages)
    if type(historical_count) is not int or not 0 <= historical_count <= len(messages):
        raise ContractError('invalid historical count')
    cut = historical_count
    return Boundary(1, digest, len(messages),
                    Anchor(cut - 1, _digest(messages[cut - 1])) if cut else None,
                    Anchor(cut, _digest(messages[cut])) if cut < len(messages) else None,
                    audience, source_scope)


@dataclass(frozen=True)
class Candidate:
    """Borrowed, lossless views, NOT a provider request or a durable snapshot."""
    historical_a: tuple[dict, ...]
    active_roles: tuple[dict, ...]
    boundary: Boundary
    offered_schema_digest: str


def candidate(messages: list, *, boundary: Boundary, audience: tuple[str, ...],
              source_scope: str, tools: list | None) -> Candidate:
    """Validate exact snapshot/scope and partition without copies or rewriting.

    Appended/edited/reordered/slid tapes need a newly authoritative boundary;
    no fuzzy overlap, legacy count fallback or automatic audience narrowing.
    """
    _scope(audience, source_scope)
    digest = _tape(messages)
    if type(boundary) is not Boundary or type(boundary.version) is not int or boundary.version != 1:
        raise ContractError('unsupported boundary')
    if boundary.audience != audience or boundary.source_scope != source_scope:
        raise ContractError('audience or source scope mismatch')
    if (type(boundary.count) is not int or boundary.count != len(messages) or
            boundary.tape_digest != digest):
        raise ContractError('changed role tape')
    left, right = boundary.historical_end, boundary.active_start
    for anchor in (left, right):
        if anchor is not None and (type(anchor) is not Anchor or
                type(anchor.index) is not int or not 0 <= anchor.index < len(messages) or
                anchor.message_digest != _digest(messages[anchor.index])):
            raise ContractError('invalid occurrence anchor')
    cut = right.index if right is not None else len(messages)
    expected = bind_boundary(messages, historical_count=cut,
                             audience=audience, source_scope=source_scope)
    if boundary != expected:
        raise ContractError('missing, overlapping or nonadjacent anchors')
    return Candidate(tuple(messages[:cut]), tuple(messages[cut:]), boundary,
                     schema_digest(tools))
