"""Fresh-E owner/ordinary-DM canaries, never a frozen shadow epoch or default migration.

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
import keat_readiness
import keat_runtime

_BOUND = ContextVar('frame_serve_bound', default=None)
_COVERAGE = ContextVar('frame_serve_coverage', default=None)

def economy_coverage(system):
    """Same-call renderer-owned source maps; never inferred from system text."""
    value = _COVERAGE.get()
    return value[1] if value is not None and value[0] is system else None



def _ordinary_dm(ctx) -> bool:
    """Match the same actor/audience boundary as keat_live._dm_context."""
    chat_id = getattr(ctx, 'chat_id', None)
    return bool(
        getattr(ctx, 'is_dm', False) is True
        and getattr(ctx, 'owner', False) is False
        and getattr(ctx, 'owner_audience', False) is False
        and chat_id is not None
        and str(getattr(ctx, 'room_chat_id', None)) == str(chat_id)
        and getattr(ctx, 'hide_identity_load', False) is False
        and not getattr(ctx, 'praxis_self', False)
    )


def _root_group(ctx) -> bool:
    """Match only the proved flat AbstractDL root-group ingress boundary."""
    return bool(
        getattr(ctx, 'is_dm', True) is False
        and getattr(ctx, 'telegram_root_group', False) is True
        and str(getattr(ctx, 'chat_id', None)) == '-1001240718803'
        and str(getattr(ctx, 'room_chat_id', None)) == '-1001240718803'
    )


def enabled(ctx) -> bool:
    owner = os.getenv('PRAXIS_OWNER_ID', '').strip()
    owner_dm = bool(
        os.getenv('PRAXIS_FRAME_V6', '').strip().lower() == 'serve'
        and owner.isascii() and owner.isdecimal() and bool(owner.lstrip('0'))
        and getattr(ctx, 'is_dm', False) is True
        and getattr(ctx, 'owner_audience', False) is True
        and not getattr(ctx, 'hide_identity_load', False)
        and str(getattr(ctx, 'chat_id', '')) == owner
        and str(getattr(ctx, 'room_chat_id', owner)) == owner
        and os.getenv('PRAXIS_FRAME_V6_STREAMS', '').strip() == f'dm-{owner}'
    )
    if owner_dm:
        return True
    try:
        root_group = bool(
            os.getenv('PRAXIS_FRAME_V6', '').strip().lower() == 'serve'
            and _root_group(ctx)
            and keat_runtime.staged('group', '-1001240718803')
            and keat_readiness.receipt(dict(os.environ))['serve_ready'])
        if root_group:
            return True
    except Exception:
        return False
    # Ordinary DMs are a separate, sparse, default-off rollout. Do not let a
    # frame-only canary bypass authority: the exact DM stream must also be staged.
    try:
        chat_id = getattr(ctx, 'chat_id', None)
        return bool(
            os.getenv('PRAXIS_FRAME_V6', '').strip().lower() == 'serve'
            and _ordinary_dm(ctx)
            and keat_runtime.staged('dm', str(chat_id))
        )
    except Exception:
        return False


@contextmanager
def bind(*, system, ctx, dynamic):
    # Retain current context for revalidation at EACH call; nested/off turns must
    # mask the outer binding. No source reads or copying in the default path.
    value = (system, ctx, dynamic) if enabled(ctx) else None
    token = _BOUND.set(value)
    coverage_token = _COVERAGE.set(None)
    try:
        yield
    finally:
        _BOUND.reset(token)
        _COVERAGE.reset(coverage_token)


def select(*, system, messages, tools):
    """Return (served system, numeric/enum receipt), failing back to live.

    Binding identity prevents a reconstructed or unrelated request inheriting
    another turn's audience. Mode is rechecked so rollback also affects the next
    iteration of an already running loop. No exception strings are recorded.
    """
    _COVERAGE.set(None)
    bound = _BOUND.get()
    if bound is None or bound[0] is not system or not enabled(bound[1]):
        return system, None
    receipt = {'schema': 1, 'variant': 'serve', 'served_variant': 'live',
               'assembly': 'fresh_e_live_transport_original_roles_v1',
               'status': 'fallback', 'resume_variant': 'live'}
    # Ordinary DM and root-group frames are reciprocal with KEAT selection:
    # neither may produce a mixed request (new system plus legacy tape).
    _, bound_ctx, _ = bound
    if _ordinary_dm(bound_ctx) or _root_group(bound_ctx):
        receipt['keat_required'] = True
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
        coverage = {}
        candidate = frame_measure.assemble(
            ctx=copy.deepcopy(ctx), live_sections=[], messages=[], coverage=coverage,
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
        _COVERAGE.set((selected, coverage))
        receipt.update(status='served', served_variant='v6')
        # Ordinary-DM candidates require independently verified KEAT at the
        # provider boundary. Owner serving predates this and stays identical.
        return selected, receipt
    except Exception:
        return system, receipt


def resume_system(model_input):
    """Rollback artifact is scrubbed by the same durable-input secret policy."""
    return model_input.get('frame_v6_live_system', model_input['system'])
