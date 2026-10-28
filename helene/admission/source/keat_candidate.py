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


# Издание (26.09): расписка ленты-вызовов. Сборщик кадра (`frame_layout._hand_pair`)
# рисует её прошлую реплику вызовом руки `reply`, а следом — расписку о доставке. Расписку
# никто не говорил, захвата у неё нет: её байты целиком выводятся из предыдущего
# сообщения. Поэтому проекция признаёт её без происхождения — только в этой точной форме
# и только сразу за своим вызовом. Всё остальное по-прежнему требует захваченного оригинала.
RENDER_TRANSFORM = 'frame-render-v1'
RECEIPT_TRANSFORM = 'frame-render-receipt-v1'
_TAPE_HAND = 'reply'
_TAPE_RECEIPT = 'delivered'


def render_receipt(previous):
    """Точная расписка сборщика для вызова руки ленты `previous`, иначе None."""
    if type(previous) is not dict or previous.get('role') != 'assistant':
        return None
    content = previous.get('content')
    if type(content) is not list or len(content) != 1 or type(content[0]) is not dict:
        return None
    block = content[0]
    call_id = block.get('id')
    if (block.get('type') != 'tool_use' or block.get('name') != _TAPE_HAND or
            type(call_id) is not str or not call_id.startswith('tape_')):
        return None
    return {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': call_id, 'content': _TAPE_RECEIPT}]}


def is_render_receipt(messages, index):
    """Сообщение `index` — расписка сборщика за вызовом `index - 1`."""
    return (type(messages) is list and type(index) is int and 0 < index < len(messages)
            and render_receipt(messages[index - 1]) == messages[index])


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
