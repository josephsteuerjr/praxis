"""Fresh-E owner-DM canary, never a frozen shadow epoch or a default migration.

Only system is replaced. The authoritative role tape and offered tool schemas
remain the original objects at every model boundary. The complete live dynamic
transport is carried verbatim in T (not reconstructed from trace coverage).
Unbound/durable continuations use the stored live system, not a stale canary E.
"""
from __future__ import annotations

import copy
import os
from contextlib import contextmanager
from contextvars import ContextVar

import frame_measure
import frame_shadow

_BOUND = ContextVar('frame_serve_bound', default=None)


def enabled(ctx) -> bool:
    owner = os.getenv('PRAXIS_OWNER_ID', '').strip()
    return bool(
        os.getenv('PRAXIS_FRAME_V6', '').strip().lower() == 'serve'
        and owner.isascii() and owner.isdecimal() and bool(owner.lstrip('0'))
        and getattr(ctx, 'is_dm', False) is True
        and getattr(ctx, 'owner_audience', False) is True
        and not getattr(ctx, 'hide_identity_load', False)
        and str(getattr(ctx, 'chat_id', '')) == owner
        and str(getattr(ctx, 'room_chat_id', owner)) == owner
        and os.getenv('PRAXIS_FRAME_V6_STREAMS', '').strip() == f'dm-{owner}'
    )


@contextmanager
def bind(*, system, ctx, dynamic):
    # Retain current context for revalidation at EACH call; nested/off turns must
    # mask the outer binding. No source reads or copying in the default path.
    value = (system, ctx, dynamic) if enabled(ctx) else None
    token = _BOUND.set(value)
    try:
        yield
    finally:
        _BOUND.reset(token)


def select(*, system, messages, tools):
    """Return (served system, numeric/enum receipt), failing back to live.

    Binding identity prevents a reconstructed or unrelated request inheriting
    another turn's audience. Mode is rechecked so rollback also affects the next
    iteration of an already running loop. No exception strings are recorded.
    """
    bound = _BOUND.get()
    if bound is None or bound[0] is not system or not enabled(bound[1]):
        return system, None
    receipt = {'schema': 1, 'variant': 'serve', 'served_variant': 'live',
               'assembly': 'fresh_e_live_transport_original_roles_v1',
               'status': 'fallback', 'resume_variant': 'live'}
    try:
        _, ctx, dynamic = bound
        if not isinstance(dynamic, str):
            raise ValueError('unsupported transport')
        # Reuse the measure envelope's fresh source selector, never prepare(),
        # capture() or epoch.text. No trace dependency: carry ALL dynamic bytes.
        # Shadow permits absent K sources for measurement, serving must not
        # silently replace a valid live constitution with an empty/degraded one.
        soul = frame_shadow._read(frame_shadow.BASE / 'soul' / 'SOUL.md')
        voice = frame_shadow._read(frame_shadow.BASE / 'soul' / 'VOICE.md')
        if not soul.strip() or not voice.strip():
            raise ValueError('missing constitution')
        expected_k = soul + '\n\n---\n' + voice
        candidate = frame_measure.assemble(
            ctx=copy.deepcopy(ctx), live_sections=[], messages=[],
            tools=copy.deepcopy(tools if tools is not None else []))
        if not candidate['system'].startswith(expected_k + frame_shadow._SEP_E):
            raise ValueError('constitution changed during assembly')
        selected = candidate['system'] + dynamic + (
            '\n[frame v6 canary: E перечитан перед этим вызовом модели, не '
            'замороженная эпоха; указания «момент заморозки» в блоках E здесь '
            'означают момент этой сборки. Живой транспорт T и ролевая лента '
            'сохранены; это не миграция накопителя.]'
        )
        if not isinstance(selected, str) or not selected.strip():
            raise ValueError('empty candidate')
        receipt.update(status='served', served_variant='v6')
        return selected, receipt
    except Exception:
        return system, receipt


def resume_system(model_input):
    """Rollback artifact is scrubbed by the same durable-input secret policy."""
    return model_input.get('frame_v6_live_system', model_input['system'])
