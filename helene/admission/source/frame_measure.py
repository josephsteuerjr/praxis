"""Opt-in same-call measurement; never supplies model input.

The caller must capture the actual system/messages/tools at _model_call, not a
reconstructed frame_trace or a scrubbed durable receipt. Candidate assembly is a
separate contract: frame_shadow.text alone is NOT a complete model request.
No disk/network I/O, identifiers, content hashes, or exception text are emitted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    # In-memory identity only; must be allocated anew at the model-call boundary.
    call: object
    request: dict


def _size(sample):
    if sample is None:
        return {"status": "missing", "json_utf8_bytes": None,
                "estimated_tokens": None}
    request = sample.request
    # None is not a measured empty list. Missing stays missing, on either side.
    if (not isinstance(request, dict)
            or set(request) != {"system", "messages", "tools"}
            or not isinstance(request["system"], (str, list))
            or not isinstance(request["messages"], list)
            or not isinstance(request["tools"], list)):
        return {"status": "unknown", "json_utf8_bytes": None,
                "estimated_tokens": None}
    try:
        size = len(json.dumps(request, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return {"status": "unknown", "json_utf8_bytes": None,
                "estimated_tokens": None}
    return {"status": "known", "json_utf8_bytes": size,
            "estimated_tokens": (size + 3) // 4}


def compare(*, actual: Sample | None, candidate: Sample | None) -> dict:
    """Compare complete request envelopes from one call, without modifying them.

    This local JSON-byte heuristic includes tool schemas and multimodal payload
    bytes, NOT provider serialization or image token accounting. It cannot prove
    semantic equivalence, cache savings, or justify serving the candidate.
    Unknown provider token counts are intentionally never filled from estimates.
    Identity correlation is checked BEFORE inspecting either request's contents.
    """
    matched = (actual is not None and candidate is not None
               and actual.call is not None and actual.call is candidate.call)
    old = _size(actual) if matched or candidate is None else _size(None)
    new = _size(candidate) if matched or actual is None else _size(None)
    comparable = matched and old["status"] == new["status"] == "known"
    return {
        "schema": 1, "variant": "measure", "basis": "model_call_arguments",
        "pair_status": "same_call" if matched else (
            "missing" if actual is None or candidate is None else "mismatch"),
        "method": "local_json_utf8_bytes_div4_v1",
        "provider_tokens": {"actual": None, "candidate": None},
        "actual": old, "candidate": new,
        "estimated_token_delta": (new["estimated_tokens"] - old["estimated_tokens"]
                                  if comparable else None),
        "semantic_equivalence": "unknown", "cache_savings": None,
    }

# The experimental envelope deliberately keeps the original role tape (including
# current situation/evidence, images and unfinished tool transactions). It does
# not flatten A/T dialogue into a system prompt. Fresh E is a measurement policy,
# not an epoch migration and not a claim about eventual cache behaviour.
import copy
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

import frame_shadow

_BOUND = ContextVar('frame_measure_bound', default=None)


def enabled(ctx) -> bool:
    """Only explicit measure + exact stream selection; serve is NOT implemented."""
    if os.getenv('PRAXIS_FRAME_V6', '').strip().lower() != 'measure':
        return False
    streams = {s.strip() for s in os.getenv('PRAXIS_FRAME_V6_STREAMS', '').split(',')
               if s.strip()}
    return frame_shadow._stream_key(ctx) in streams


@contextmanager
def bind(*, system, ctx, live_sections):
    """Scope one voice turn, resetting even on nested turns or model failures.

    Setup is observational too: a copying/selection failure must not stop a turn.
    We never retain the message tape, tool results or candidate beyond a call.
    """
    value = None
    try:
        if enabled(ctx):
            sections = live_sections() if callable(live_sections) else live_sections
            value = (system, copy.deepcopy(ctx), copy.deepcopy(sections))
    except Exception:
        pass
    token = _BOUND.set(value)
    try:
        yield
    finally:
        _BOUND.reset(token)


def assemble(*, ctx, live_sections, messages, tools, coverage=None) -> dict:
    """Build a complete, private in-memory measurement envelope from fresh sources.

    No prepare/capture: those write frozen epochs and shadow bodies to disk. This
    fresh-E candidate does neither. Private source selection uses the same narrow
    audience rules as shadow; original role messages are already channel-scoped.
    Callers pass detached copies, so even faulty candidate code cannot edit input.
    """
    now = datetime.now(timezone.utc)
    payload = frame_shadow._live_payload(live_sections)
    audience = frame_shadow._audience(ctx)
    e = frame_shadow._zone_e_current(tools, ctx=ctx, now=now,
                                     audience=audience, payload=payload, coverage=coverage)
    machine = '\n\n'.join(f'--- {name} ---\n{text}'
                            for name, text in payload['machine'])
    # Compacted history may exist only in the live system, not the role tape.
    # Carry that channel-scoped recap as historical evidence, not fresh E.
    recap = (frame_shadow._SEP_A
             + '[сводка прошлых ходов · снимок живого пути · это НЕ мой отбор]\n'
             + payload['recap']) if payload['recap'] else ''
    system = (frame_shadow._zone_k() + frame_shadow._SEP_E + e
              + recap + frame_shadow._SEP_T + machine)
    return {'system': system, 'messages': messages, 'tools': tools}


def measure(*, system, messages, tools) -> dict | None:
    """At the actual _model_call boundary; output contains counts/enums only.

    A resumed/reconstructed system cannot reuse another turn's source context.
    The token is freshly allocated per iteration, never a durable identifier.
    Failures emit no exception text, source text, hashes, paths or stream IDs.
    """
    bound = _BOUND.get()
    if bound is None or bound[0] is not system:
        return None
    call = object()
    actual = Sample(call, {'system': system, 'messages': messages,
                           'tools': tools if tools is not None else []})
    try:
        _, ctx, sections = bound
        candidate = assemble(ctx=copy.deepcopy(ctx),
                             live_sections=copy.deepcopy(sections),
                             messages=copy.deepcopy(messages),
                             tools=copy.deepcopy(tools if tools is not None else []))
        out = compare(actual=actual, candidate=Sample(call, candidate))
        out['status'] = 'measured'
    except Exception:
        out = compare(actual=actual, candidate=None)
        out['status'] = 'failed'
    out['assembly'] = 'fresh_e_original_role_tape_v1'
    out['served_variant'] = 'live'
    return out
