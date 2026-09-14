"""Default-off capture/project/checkpoint facade. No provider tape is rewritten.

The administrator-authored canonical JSON policy is independent of turn context.
Context selects an enrollment; it never supplies an audience or historical grant.
Malformed/missing evidence returns None, retaining the exact existing live path.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import copy
import os
import re
import time
import uuid

from keat_candidate import _digest
from keat_capture import CaptureLedger, NarrowingReader
from keat_epoch import _capture, _require
from keat_source import _json
from keat_runtime import RuntimeStore, ResumeBinding, staged

_METADATA = '_keat_occurrence'
_WAKE_SOURCE = '_keat_wake_source'
_BOUND = ContextVar('keat_live_bound', default=None)
_ACTIVATION_REASON = ContextVar('keat_live_activation_reason', default='capture_unavailable')
ABSTRACTDL_ROOT_CHAT_ID = '-1001240718803'


def activation_reason():
    """Last bounded reason in this context; contains no input or exception text."""
    value = _ACTIVATION_REASON.get()
    return value if value in {
        'capture_unavailable', 'capture_disabled', 'capture_policy_invalid',
        'projection_invalid', 'not_staged', 'model_tape_changed',
        'policy_changed', 'verification_failed',
    } else 'verification_failed'


def _capture_policy_failure():
    return ('capture_disabled' if os.getenv('PRAXIS_KEAT_CAPTURE') != 'on'
            else 'capture_policy_invalid')


def strip_occurrence(message):
    """Remove only our storage sidecar, without changing roles/content/media."""
    if isinstance(message, dict) and _METADATA in message:
        return {k: v for k, v in message.items() if k != _METADATA}
    return message


def _policy(ctx, mode=None):
    _require(os.getenv('PRAXIS_KEAT_CAPTURE') == 'on', 'capture disabled')
    path = Path(os.environ['PRAXIS_KEAT_CAPTURE_POLICY'])
    _require(path.is_absolute(), 'policy path must be absolute')
    policy = _json(path.read_bytes())
    _require(set(policy) == {'schema', 'namespace', 'root', 'enrollments'} and
             policy['schema'] == 'keat.capture-policy.v1', 'unsupported capture policy')
    _require(Path(policy['root']).is_absolute(), 'root must be absolute')
    stream = str(ctx.chat_id)
    mode = mode or ('owner' if ctx.is_dm and ctx.owner else 'dm' if ctx.is_dm else 'group')
    rows = [r for r in policy['enrollments'] if r['stream'] == stream and r['mode'] == mode]
    _require(len(rows) == 1, 'no unique explicit enrollment')
    row = rows[0]
    _require(set(row) == {'stream', 'mode', 'capture'}, 'invalid enrollment')
    _require(set(row['capture']) == {'issuer', 'policy_revision', 'audience', 'transfer', 'presence_hidden'},
             'capture policy fields required')
    _capture(dict(row['capture'], grant='validation-only'))
    if mode == 'dm':
        _require(stream.isascii() and stream.isdecimal() and
                 str(int(stream)) == stream and int(stream) > 0,
                 'ordinary DM requires an exact positive peer')
        _require(row['capture']['audience'] == ['dm:' + stream] and
                 row['capture']['transfer'] == 'none' and
                 row['capture']['presence_hidden'] is False,
                 'ordinary DM authority must be exact and nontransferable')
    elif mode == 'group':
        # One proved ordinary root group, deliberately not a generic group/topic mode.
        _require(stream == ABSTRACTDL_ROOT_CHAT_ID and
                 row['capture']['audience'] == ['group:' + ABSTRACTDL_ROOT_CHAT_ID] and
                 row['capture']['transfer'] == 'none' and
                 row['capture']['presence_hidden'] is False,
                 'ordinary root-group authority must be exact')
    return policy, row


def _dm_context(ctx):
    """Audience and actor are distinct; known/family never grants owner rights."""
    _require(getattr(ctx, 'is_dm', None) is True and
             getattr(ctx, 'owner', None) is False and
             getattr(ctx, 'owner_audience', None) is False and
             not getattr(ctx, 'hide_identity_load', False) and
             not getattr(ctx, 'praxis_self', False) and
             str(getattr(ctx, 'room_chat_id', None)) == str(ctx.chat_id),
             'unsupported ordinary DM context')


def _group_context(ctx):
    """Require message-bound proof of the one flat root group."""
    root = str(getattr(ctx, 'room_chat_id', None))
    principal = str(getattr(ctx, 'principal_id', ''))
    origin = str(getattr(ctx, 'origin_message_id', ''))
    _require(getattr(ctx, 'is_dm', None) is False and
             getattr(ctx, 'telegram_root_group', None) is True and
             str(getattr(ctx, 'chat_id', None)) == ABSTRACTDL_ROOT_CHAT_ID and
             root == ABSTRACTDL_ROOT_CHAT_ID and
             principal.isascii() and principal.isdecimal() and int(principal) > 0 and
             origin.isascii() and origin.isdecimal() and int(origin) > 0 and
             not getattr(ctx, 'praxis_self', False),
             'unsupported or ambiguous root-group context')


def _native_policy(chat_id):
    # Admission knows the peer, not trust. Require one independent DM enrollment.
    from types import SimpleNamespace
    raw = _json(Path(os.environ['PRAXIS_KEAT_CAPTURE_POLICY']).read_bytes())
    rows = [r for r in raw['enrollments'] if r['stream'] == str(chat_id)
            and r['mode'] in ('owner', 'dm', 'group')]
    _require(len(rows) == 1, 'ambiguous native DM enrollment')
    return _policy(SimpleNamespace(chat_id=chat_id), mode=rows[0]['mode'])


@dataclass
class TurnState:
    policy: dict
    enrollment: dict
    ledger: CaptureLedger
    history: list
    current_ref: dict
    epoch: str = ""
    head: str | None = None
    historical_count: int = 0
    projection_groups: list | None = None


def _issue(state, message):
    event = 'occurrence:' + uuid.uuid4().hex
    receipt = state.ledger.issue(occurrence_id=event, key=state.enrollment['mode'] + ':' + state.enrollment['stream'] + ':' + event, kind='runtime',
                                 payload=message,
                                 capture=dict(state.enrollment['capture'], grant='grant:' + uuid.uuid4().hex))
    return dict(namespace=state.ledger.namespace, event=receipt['event'], key=receipt['key'],
                payload_digest=receipt['payload_digest'])


def capture_turn(ctx, user_msg, history, mode=None):
    """Issue ONLY the newly observed current occurrence, before frame rendering.

    Existing history is retained with its original references, never reissued.
    Even legacy history can gradually age out as newly captured turns are stored.
    """
    try:
        policy, row = _policy(ctx, mode)
        if row['mode'] == 'dm':
            _dm_context(ctx)
        directory = Path(policy['root']) / 'capture' / _digest(policy['namespace'])
        ledger = CaptureLedger(directory, policy['namespace'])
        state = TurnState(copy.deepcopy(policy), copy.deepcopy(row), ledger,
                          copy.deepcopy(history), {})
        state.epoch = 'A:' + uuid.uuid4().hex
        state.current_ref = _issue(state, {'role': 'user', 'content': user_msg})
        _ACTIVATION_REASON.set('capture_unavailable')
        return state
    except Exception:
        _ACTIVATION_REASON.set(_capture_policy_failure())
        return None


def _scheduled_wake_payload(task, occurrence):
    """Exact model-current message authorized when the occurrence is created.

    Transport presence is deliberately not interpolated here: it is firing-time
    evidence and belongs to the wake system frame. Authorizing a task-shaped
    description and later accepting an arbitrary rendering would not authorize
    the bytes actually sent to the model.
    """
    if task.get('kind') == 'message':
        try:
            import tasks
            seed = tasks.message_reassessment_prompt(task)
        except Exception:
            seed = "A scheduled person-addressed intention is due for fresh reassessment."
    else:
        goal = str(task.get('goal') or '').strip()
        seed = (f"Ты просила разбудить себя вот с чем: {goal}" if goal else
                "Ты просила разбудить себя в этот момент.")
    return {'role': 'user', 'content': seed + "\n\n[твой будильник]"}


def issue_scheduled_wake(task, occurrence):
    """Issue a receipt at occurrence creation; the caller persists its reference.

    Failure is default-off and must never prevent task persistence.  The firing
    adapter deliberately does not call this function.
    """
    try:
        _require(type(task) is dict and task.get('kind') in ('wake', 'note', 'message'),
                 'scheduled cognitive task required')
        task_id = task.get('id')
        _require(re.fullmatch(r'[0-9a-f]{8}', task_id or '') is not None and
                 type(occurrence) is str and bool(occurrence.strip()) and
                 len(occurrence) <= 128, 'non-canonical scheduled wake coordinate')
        ctx = type('ScheduledWakeContext', (), {'chat_id': 'scheduler:wake'})()
        policy, row = _policy(ctx, mode='wake')
        ledger = CaptureLedger(Path(policy['root']) / 'capture' / _digest(policy['namespace']),
                               policy['namespace'])
        key = 'scheduler:wake:task:' + task_id + ':occurrence:' + occurrence
        event = 'native:' + _digest({'namespace': policy['namespace'], 'key': key})
        receipt = ledger.issue(
            occurrence_id=event, key=key, kind='synthetic',
            payload=_scheduled_wake_payload(task, occurrence),
            capture=dict(row['capture'], grant='grant:' + event),
        )
        return dict(namespace=ledger.namespace, event=event, key=key,
                    payload_digest=receipt['payload_digest'])
    except Exception:
        return None


def capture_scheduled_wake(task, user_msg=None):
    """Adopt a creation-time receipt for the exact scheduled model payload.

    Direct/ad-hoc wakes and legacy tasks without a persisted reference use the
    unchanged live fallback. This firing-time path never mints authority.
    """
    try:
        _require(type(task) is dict, 'scheduled wake task required')
        task_id = task.get('id')
        occurrence = str(task.get('when') or task.get('created') or 'one-shot')
        _require(re.fullmatch(r'[0-9a-f]{8}', task_id or '') is not None and
                 bool(occurrence.strip()) and len(occurrence) <= 128,
                 'non-canonical scheduled wake coordinate')
        ctx = type('ScheduledWakeContext', (), {'chat_id': 'scheduler:wake'})()
        policy, row = _policy(ctx, mode='wake')
        directory = Path(policy['root']) / 'capture' / _digest(policy['namespace'])
        ledger = CaptureLedger(directory, policy['namespace'])
        key = 'scheduler:wake:task:' + task_id + ':occurrence:' + occurrence
        event = 'native:' + _digest({'namespace': policy['namespace'], 'key': key})
        ref = task.get(_WAKE_SOURCE)
        _require(type(ref) is dict and set(ref) ==
                 {'namespace', 'event', 'key', 'payload_digest'} and
                 ref['namespace'] == ledger.namespace and ref['event'] == event and
                 ref['key'] == key, 'missing creation-time source receipt')
        snapshot = ledger.snapshot(keys=[key])
        receipt = snapshot['receipts'][0]
        payload = _scheduled_wake_payload(task, occurrence)
        _require(receipt['event'] == event and
                 receipt['payload_digest'] == ref['payload_digest'] and
                 snapshot['payloads'][event] == payload and
                 (user_msg is None or user_msg == payload['content']),
                 'scheduled wake model payload changed after creation')
        # Policy selects the enrollment but cannot reinterpret an old receipt.
        # Every original authority field must still match, and the canonical
        # narrowing ledger must say that exact grant is current.
        capture = receipt['capture']
        _require({k: capture[k] for k in row['capture']} == row['capture'] and
                 capture['grant'] == 'grant:' + event,
                 'creation authority is not currently enrolled')
        with ledger.guard():
            _, _, _, current, _, invalidations = ledger._state()
            _require(all(invalidations.values()) and current.get(capture['grant']) == capture,
                     'creation authority revoked or narrowed')
        state = TurnState(copy.deepcopy(policy), copy.deepcopy(row), ledger, [], ref,
                          epoch='A:' + _digest({
                              'namespace': policy['namespace'], 'mode': 'wake', 'key': key,
                          }))
        state.current_payload = copy.deepcopy(payload)
        return state
    except Exception:
        return None


def adopt_scheduled_wake(task):
    """Return creation-authorized state and exact seed, or fail closed."""
    state = capture_scheduled_wake(task)
    if state is None:
        return None, None
    return state, state.current_payload['content']


def capture_output(state, message):
    """Capture a newly produced response for caller-owned persistent history."""
    try:
        _require(isinstance(state, TurnState) and os.getenv('PRAXIS_KEAT_CAPTURE') == 'on', 'disabled')
        # A changed enrollment must be explicitly loaded on the next turn.
        _require(_json(Path(os.environ['PRAXIS_KEAT_CAPTURE_POLICY']).read_bytes()) == state.policy,
                 'policy changed')
        clean = strip_occurrence(message)
        return dict(clean, **{_METADATA: _issue(state, clean)})
    except Exception:
        return message


def history_entry(state, role, content):
    """Persist a newly created respond-history entry, without reissuing users."""
    message = dict(role=role, content=content)
    try:
        if role == 'assistant':
            return capture_output(state, message)
        _require(role == 'user' and isinstance(state, TurnState), 'unsupported history entry')
        _require(_digest(message) == state.current_ref['payload_digest'], 'changed current original')
        return dict(message, **{_METADATA: copy.deepcopy(state.current_ref)})
    except Exception:
        return message


def _references(state, count):
    _require(0 <= count <= len(state.history), 'unsupported projection length')
    history = state.history[-count:] if count else []
    snapshot = state.ledger.snapshot()
    receipts = {r['event']: r for r in snapshot['receipts']}
    refs = []
    for message in history:
        ref = message[_METADATA]
        _require(set(ref) == {'namespace', 'event', 'key', 'payload_digest'} and
                 ref['namespace'] == state.ledger.namespace, 'invalid occurrence reference')
        receipt = receipts[ref['event']]
        _require(ref['key'] == receipt['key'] and ref['payload_digest'] == receipt['payload_digest']
                 and _digest(strip_occurrence(message)) == receipt['payload_digest'],
                 'original occurrence payload changed')
        refs.append(ref)
    refs.append(state.current_ref)
    return refs


@dataclass
class Bound:
    state: TurnState
    messages: list
    digest: str
    refs: list
    fallback_current: object = None
    served_selection: object = None
    economy: object = None
    projection: object = None
    provider_messages: object = None
    provider_digest: object = None


@contextmanager
def bind_turn(state, messages, fallback_current=None, candidate_messages=None):
    """Bind the trusted renderer's original ordered projection.

    ``fallback_current`` is the already-rendered legacy current message for an
    occurrence whose stable source seed differs from the legacy live seed. It
    is installed only if the model-boundary selector does not serve this exact
    call; capture/adoption alone is never authority to change provider input.
    ``candidate_messages`` similarly permits a captured native suffix to age
    uncaptured legacy history out only after successful provider selection.
    """
    # Rollback is a renderer address, NOT authority. Establish it even when
    # reference lookup/snapshot fails, and keep legacy bytes as the default.
    rollback = None
    if candidate_messages is not None:
        _require(type(candidate_messages) is list and bool(candidate_messages),
                 'candidate tape required')
        candidate = copy.deepcopy(candidate_messages)
        legacy = copy.deepcopy(messages)
        rollback = (messages, candidate, legacy)
    elif fallback_current is not None:
        candidate = copy.deepcopy(messages)
        legacy = copy.deepcopy(messages)
        legacy[-1] = copy.deepcopy(fallback_current)
        rollback = (messages, candidate, legacy)
    bound = None
    try:
        if state is not None:
            projection = candidate if rollback is not None else messages
            refs = (state.projection_groups if state.projection_groups is not None
                    else [[ref] for ref in _references(state, len(projection) - 1)])
            state.historical_count = len(refs) - 1
            bound = Bound(state, messages, _digest(projection), refs,
                          copy.deepcopy(fallback_current))
    except Exception:
        if state is not None:
            _ACTIVATION_REASON.set('projection_invalid')
    if rollback is not None:
        messages[:] = copy.deepcopy(rollback[2])
    if bound is not None:
        bound.projection = copy.deepcopy(candidate if rollback is not None else messages)
        bound.digest = _digest(messages)
    token = _BOUND.set(bound)
    try:
        yield state
    finally:
        _BOUND.reset(token)


def discard_provider():
    """Drop ephemeral authority; never write to the canonical conversation."""
    bound = _BOUND.get()
    if bound is not None:
        bound.provider_messages = None
        bound.provider_digest = None
        bound.served_selection = None
        bound.economy = None


def fallback_bound(provider_messages=None):
    discard_provider()
    return False


def prepare_provider_messages(system, messages, tools):
    """Create a single-call isolated candidate from an unchanged issued tape.

    The caller owns canonical messages. Neither selection nor rollback ever
    writes to it. An unissued mutation disables selection rather than guessing
    which additions or renderer markers belong to us.
    """
    discard_provider()
    bound = _BOUND.get()
    if bound is None:
        return copy.deepcopy(messages)
    try:
        _require(messages is bound.messages and _digest(messages) == bound.digest,
                 'unissued canonical tape')
        candidate = copy.deepcopy(bound.projection)
        if bound.state.enrollment['mode'] == 'owner':
            import keat_economy
            economy = keat_economy.candidate(system, candidate)
            if economy is not None:
                candidate, removed = economy
                bound.economy = keat_economy.measurement(
                    system, messages, candidate, tools, removed)
        bound.provider_messages = candidate
        bound.provider_digest = _digest(candidate)
        return candidate
    except Exception:
        _ACTIVATION_REASON.set('model_tape_changed')
        discard_provider()
        return copy.deepcopy(messages)


def select_provider(system, messages, tools, run_id, call_id):
    """Select only the isolated candidate; never substitute request objects."""
    receipt = None
    try:
        receipt = select(system=system, messages=messages, tools=tools,
                         run_id=run_id, call_id=call_id)
        return receipt
    finally:
        if not valid_served_receipt(receipt, system=system, messages=messages,
                                    tools=tools, run_id=run_id, call_id=call_id):
            discard_provider()


def capture_appended(messages, additions):
    """Attest newly created tool-loop roles BEFORE the caller appends them.

    Never modifies provider objects and never repairs an unissued prefix.
    """
    try:
        bound = _BOUND.get()
        _require(bound is not None and messages is bound.messages and
                 _digest(messages) == bound.digest, 'unissued prefix')
        state = bound.state
        _require(os.getenv('PRAXIS_KEAT_CAPTURE') == 'on' and
                 _json(Path(os.environ['PRAXIS_KEAT_CAPTURE_POLICY']).read_bytes()) == state.policy,
                 'capture policy changed')
        refs = [_issue(state, message) for message in additions]
        bound.refs.extend([[ref] for ref in refs])
        bound.digest = _digest(messages + additions)
        bound.projection.extend(copy.deepcopy(additions))
        return True
    except Exception:
        return False


def _store(state):
    return RuntimeStore(Path(state.policy['root']) / 'epochs' / _digest(state.epoch))


def _reader(state):
    return NarrowingReader(state.ledger.directory, state.ledger.namespace)


def valid_served_receipt(receipt, *, system, messages, tools, run_id, call_id):
    """Only the exact current successful selection can authorize provider bytes.

    The public v1 receipt has no separate reference field: checkpoint identifies
    the durable projection. Bind it additionally to the in-context verified
    references and exact request, without reopening private evidence or I/O.
    """
    try:
        bound = _BOUND.get()
        _require(bound is not None and bound.served_selection is not None,
                 'no successful selection')
        issued, request_digest, refs_digest = bound.served_selection
        state = bound.state
        row = state.enrollment
        binding = ResumeBinding(run_id, call_id, row['mode'], row['stream'],
                                tuple(row['capture']['audience'])).value()
        _require(type(receipt) is dict and set(receipt) == set(issued) and
                 receipt['schema'] == 'keat.live.v1' and
                 receipt['status'] == 'served' and receipt['eligible'] is True and
                 receipt['served'] is True and receipt['fallback'] is False,
                 'unsupported served receipt')
        _require(type(receipt['checkpoint']) is str and bool(receipt['checkpoint']) and
                 type(receipt['epoch']) is str and bool(receipt['epoch']) and
                 receipt == issued and receipt['checkpoint'] == state.head and
                 receipt['epoch'] == state.epoch and receipt['binding'] == binding,
                 'changed selection identity')
        _require(messages is bound.provider_messages and
                 _digest(messages) == bound.provider_digest and
                 _digest(bound.messages) == bound.digest and
                 _digest([system, messages, tools]) == request_digest and
                 _digest(bound.refs) == refs_digest, 'changed selection projection')
        return True
    except Exception:
        return False


def accept_provider_receipt(receipt, **request):
    """Validate the isolated selection before intent persistence.

    This early check protects intervening persistence. Authority is linearized by
    ``finalize_provider_receipt`` at the last synchronous boundary before chat.
    """
    if not valid_served_receipt(receipt, **request):
        fallback_bound(request.get('messages'))
        return False
    return True


def finalize_provider_receipt(receipt, **request):
    """Revalidate current authority at the provider-boundary linearization point.

    Policy/enrollment, ledger projection (including revocations/pending
    invalidations), checkpoint and exact request are reopened under the ledger
    lock. On failure candidate authority is discarded; canonical bytes are untouched. The unavoidable interval after
    this function returns and before ``llm.chat`` begins contains no await or
    application I/O; a revocation linearized after this point governs the next
    call, just as one arriving after transport starts cannot recall that call.
    """
    try:
        _require(valid_served_receipt(receipt, **request), 'selection changed')
        bound = _BOUND.get()
        state, row = bound.state, bound.state.enrollment
        _require(os.getenv('PRAXIS_KEAT_CAPTURE') == 'on' and
                 staged(row['mode'], row['stream']), 'not staged')
        if row['mode'] == 'group':
            import keat_readiness
            _require(keat_readiness.receipt()['serve_ready'], 'group not ready')
        _require(_json(Path(os.environ['PRAXIS_KEAT_CAPTURE_POLICY']).read_bytes()) == state.policy,
                 'policy changed')
        binding = ResumeBinding(request['run_id'], request['call_id'], row['mode'],
                                row['stream'], tuple(row['capture']['audience']))
        with state.ledger.guard():
            state.ledger.project(
                messages=request['messages'],
                origins=[[r['event'] for r in group] for group in bound.refs],
                transforms=['frame-render-v1'] * len(bound.refs),
                issuer=row['capture']['issuer'], tools=request['tools'],
                historical_count=state.historical_count,
                keys=[r['key'] for group in bound.refs for r in group])
            _store(state).reopen_capture(
                ledger=state.ledger, reader=_reader(state), expected_head=state.head,
                binding=binding, now=int(time.time()), system=request['system'],
                messages=request['messages'], tools=request['tools'])
        return True
    except Exception:
        _ACTIVATION_REASON.set('verification_failed')
        discard_provider()
        return False


def _receipt(state, binding, system, messages, tools):
    # Only identifiers: never persist the source/auth envelopes or private A here.
    _ACTIVATION_REASON.set('capture_unavailable')
    receipt = dict(schema='keat.live.v1', status='served', eligible=True, served=True,
                   fallback=False, checkpoint=state.head, epoch=state.epoch,
                   binding=binding.value())
    bound = _BOUND.get()
    bound.served_selection = (copy.deepcopy(receipt),
                              _digest([system, messages, tools]), _digest(bound.refs))
    return receipt


def select(system, messages, tools, run_id, call_id):
    """Verify and checkpoint A, retaining the exact live structured role tape.

    None is the default-off/ineligible exact-live fallback. Successful receipts
    are sanitized identifiers, not RuntimeStore's private reopened source view.
    """
    reason = 'capture_unavailable'
    try:
        bound = _BOUND.get()
        _require(bound is not None, activation_reason())
        bound.served_selection = None
        state = bound.state
        row = state.enrollment
        reason = 'not_staged'
        _require(os.getenv('PRAXIS_KEAT_CAPTURE') == 'on' and
                 staged(row['mode'], row['stream']), 'not staged')
        if row['mode'] == 'group':
            import keat_readiness
            _require(keat_readiness.receipt()['serve_ready'], 'group not ready')
        reason = 'model_tape_changed'
        _require(messages is bound.provider_messages and
                 _digest(messages) == bound.provider_digest and
                 _digest(bound.messages) == bound.digest, 'unissued model tape')
        reason = 'policy_changed'
        _require(_json(Path(os.environ['PRAXIS_KEAT_CAPTURE_POLICY']).read_bytes()) == state.policy,
                 'policy changed')
        reason = 'verification_failed'
        binding = ResumeBinding(run_id, call_id, row['mode'], row['stream'], tuple(row['capture']['audience']))
        store = _store(state)
        now = int(time.time())
        with state.ledger.guard():
            source = state.ledger.project(messages=messages, origins=[[r['event'] for r in group] for group in bound.refs],
                                          transforms=['frame-render-v1'] * len(bound.refs),
                                          issuer=row['capture']['issuer'], tools=tools,
                                          historical_count=state.historical_count,
                                          keys=[r['key'] for group in bound.refs for r in group])
            # Exact retries reopen; subsequent calls atomically advance the same
            # turn epoch, with the historical cut and A unchanged.
            if state.head is not None:
                obj = store.epochs._load(state.head)
                envelope = _json(obj['reason'].encode())
                if envelope['binding'] == binding.value():
                    store.reopen_capture(ledger=state.ledger, reader=_reader(state), expected_head=state.head,
                                         binding=binding, now=now, system=system, messages=messages, tools=tools)
                    return _receipt(state, binding, system, messages, tools)
            state.head = store.checkpoint_capture(
                ledger=state.ledger, reader=_reader(state), source_bytes=source,
                binding=binding, expected_parent=state.head, epoch=state.epoch,
                policy=row['capture']['policy_revision'], reason='live ordered capture projection',
                now=now, system=system, messages=messages, tools=tools)
            receipt = _receipt(state, binding, system, messages, tools)
            _ACTIVATION_REASON.set('capture_unavailable')
            return receipt
    except Exception:
        _ACTIVATION_REASON.set(reason)
        return None


@contextmanager
def bind_resume(model_input, run_id, call_id):
    """Validate the saved original call before caller appends tool results.

    The context exposes state only after independent current narrowing and exact
    original system/messages/tools validation. Missing/legacy receipts fail closed.
    """
    bound = None
    try:
        from types import SimpleNamespace
        receipt = model_input['keat']
        _require(set(receipt) == {'schema', 'status', 'eligible', 'served', 'fallback',
                                 'checkpoint', 'epoch', 'binding'} and
                 receipt['schema'] == 'keat.live.v1' and receipt['status'] == 'served' and
                 receipt['eligible'] is True and receipt['served'] is True and
                 receipt['fallback'] is False, 'unsupported resume receipt')
        value = receipt['binding']
        binding = ResumeBinding(run_id, call_id, value['mode'], value['stream'], tuple(value['audience']))
        _require(binding.value() == value, 'changed resume identity')
        policy, row = _policy(SimpleNamespace(chat_id=value['stream']), mode=value['mode'])
        _require(staged(row['mode'], row['stream']) and
                 row['capture']['audience'] == value['audience'], 'resume not enrolled')
        ledger = CaptureLedger(Path(policy['root']) / 'capture' / _digest(policy['namespace']), policy['namespace'])
        state = TurnState(policy, row, ledger, [], {}, receipt['epoch'], receipt['checkpoint'])
        result = _store(state).reopen_capture(
            ledger=ledger, reader=_reader(state), expected_head=state.head, binding=binding,
            now=int(time.time()), system=model_input['system'], messages=model_input['messages'],
            tools=model_input['tools'])
        _require(result['epoch'] == state.epoch, 'changed epoch')
        source = result['source']
        state.historical_count = source['historical_count']
        receipts = {r['event']: r for r in source['receipts']}
        refs = [[dict(event=e, key=receipts[e]['key']) for e in entry['origins']]
                for entry in source['projection']]
        bound = Bound(state, model_input['messages'], _digest(model_input['messages']), refs,
                      projection=copy.deepcopy(model_input['messages']))
    except Exception:
        pass
    token = _BOUND.set(bound)
    try:
        yield bound.state if bound else None
    finally:
        _BOUND.reset(token)


def capture_ingress(chat_id, *, is_dm, payload, source_id, direction,
                    root_chat_id=None, principal_id=None, ordinary_root=False):
    """Capture one admitted native message; ambiguous group evidence is rejected."""
    try:
        _require(direction in ('in', 'out'), 'unsupported ingress')
        if is_dm is True:
            _require(root_chat_id is None and principal_id is None and ordinary_root is False,
                     'group evidence on DM ingress')
        else:
            root = str(root_chat_id)
            principal = str(principal_id)
            _require(is_dm is False and ordinary_root is True and
                     str(chat_id) == root == ABSTRACTDL_ROOT_CHAT_ID and
                     principal.isascii() and principal.isdecimal() and int(principal) > 0,
                     'unsupported root-group ingress')
            payload = dict(payload, telegram_root_chat_id=root,
                           telegram_principal_id=principal,
                           telegram_ordinary_root=True)
        policy, row = _native_policy(chat_id)
        ledger = CaptureLedger(Path(policy['root']) / 'capture' / _digest(policy['namespace']), policy['namespace'])
        # A native occurrence names exactly one Telegram message.  In particular,
        # a rendered reply split by Telegram must be enrolled once per accepted
        # chunk; a comma-joined transport receipt is not a message identity.
        source = str(source_id)
        _require(source.isdecimal() and str(int(source)) == source, 'non-canonical telegram message id')
        key = 'telegram:' + str(chat_id) + ':message:' + source + ':' + direction
        event = 'native:' + _digest({'namespace': policy['namespace'], 'key': key})
        receipt = ledger.issue(occurrence_id=event, key=key, kind='message', payload=payload,
                               capture=dict(row['capture'], grant='grant:' + event))
        return dict(namespace=ledger.namespace, event=event, key=key,
                    payload_digest=receipt['payload_digest'])
    except Exception:
        return None


def adopt_projection(ctx, history, user_msg, sidecar):
    """Validate native positional coalescing against captured raw payloads; issue nothing."""
    try:
        policy, row = _native_policy(ctx.chat_id)
        if row['mode'] == 'dm':
            _dm_context(ctx)
        elif row['mode'] == 'group':
            _group_context(ctx)
        elif hasattr(ctx, 'is_dm'):
            _require(ctx.is_dm is True and
                     (getattr(ctx, 'owner', False) or getattr(ctx, 'owner_audience', False)),
                     'owner enrollment cannot adopt ordinary DM context')
        ledger = CaptureLedger(Path(policy['root']) / 'capture' / _digest(policy['namespace']), policy['namespace'])
        _require(set(sidecar) in ({'history', 'current'},
                                  {'history', 'current', 'projection_history', 'projection_current'}),
                 'missing projection')
        projection_history = sidecar.get('projection_history', history)
        projection_current = sidecar.get('projection_current', user_msg)
        _require(len(sidecar['history']) == len(projection_history), 'missing projection')
        groups = sidecar['history'] + [sidecar['current']]
        snapshot = ledger.snapshot()
        receipts = {r['event']: r for r in snapshot['receipts']}
        group_current = None
        if row['mode'] == 'group':
            with ledger.guard():
                _, _, _, group_current, _, invalidations = ledger._state()
            _require(all(invalidations.values()), 'pending native invalidation')
        for index, group in enumerate(groups):
            _require(type(group) is list and bool(group), 'empty projection')
            lines, batches = [], []
            role = projection_history[index]['role'] if index < len(projection_history) else 'user'
            for ref in group:
                r = receipts[ref['event']]
                _require(ref['namespace'] == ledger.namespace and ref['key'] == r['key'] and
                         ref['payload_digest'] == r['payload_digest'], 'changed reference')
                if row['mode'] == 'group':
                    _require(group_current.get(r['capture']['grant']) is not None,
                             'revoked root-group occurrence')
                payload = snapshot['payloads'][ref['event']]
                _require(payload['role'] == role and type(payload['content']) is str,
                         'unsupported native role')
                if row['mode'] == 'group':
                    _require(payload.get('telegram_root_chat_id') == ABSTRACTDL_ROOT_CHAT_ID and
                             payload.get('telegram_ordinary_root') is True,
                             'missing immutable root-group evidence')
                    native_principal = str(payload.get('telegram_principal_id', ''))
                    _require(native_principal.isascii() and native_principal.isdecimal() and
                             int(native_principal) > 0, 'missing immutable sender principal')
                    if index == len(groups) - 1:
                        source = r['key'].split(':')
                        _require(len(source) == 5 and source[3] == str(ctx.origin_message_id) and
                                 native_principal == str(ctx.principal_id),
                                 'trigger principal or message changed')
                batches.append(payload.get('logical_send') if role == 'assistant' else None)
                text, actor = payload['content'], payload.get('actor', '')
                head = actor + ': '
                lines.append(text if row['mode'] == 'group' else
                             text[len(head):] if actor and text.startswith(head) else text)
            from logical_send import batch_groups
            grouped = batch_groups(batches)
            _require(all(batches[g[0]] is None or len(g) == batches[g[0]].get('count')
                         and batches[g[0]].get('index') == 0 for g in grouped),
                     'incomplete logical send')
            lines = [''.join(lines[i] for i in g) for g in grouped]
            rendered = ('\n\n'.join(lines).strip() if index < len(projection_history) else '\n'.join(lines))
            expected = (projection_history[index]['content']
                        if index < len(projection_history) else projection_current)
            _require(rendered == expected, 'native rendering changed')
        state = TurnState(policy, row, ledger, projection_history, {},
                          'A:' + uuid.uuid4().hex)
        state.projection_groups = copy.deepcopy(groups)
        if projection_history is not history:
            state.provider_history = copy.deepcopy(projection_history)
            state.provider_current = copy.deepcopy(projection_current)
        _ACTIVATION_REASON.set('capture_unavailable')
        return state
    except Exception:
        _ACTIVATION_REASON.set(_capture_policy_failure() if
                               os.getenv('PRAXIS_KEAT_CAPTURE') != 'on'
                               else 'projection_invalid')
        return None


def invalidate_native(chat_id, message_id):
    """An admitted native edit/delete permanently revokes the original grant.

    No new historical grant is minted. Missing capture history is harmless; I/O
    failure raises so the caller must not silently acknowledge the invalidation.
    For the exact root-group adapter, an observed edit with no trustworthy
    message id retires the whole root coordinate space: preserving one stale
    occurrence would be a fail-open claim about which message Telegram edited.
    """
    if os.getenv('PRAXIS_KEAT_CAPTURE') != 'on':
        return
    policy_raw = _json(Path(os.environ['PRAXIS_KEAT_CAPTURE_POLICY']).read_bytes())
    if not any(
            (row.get('mode') in ('owner', 'dm') or
             (row.get('mode') == 'group' and
              row.get('stream') == ABSTRACTDL_ROOT_CHAT_ID)) and
            row.get('stream') == str(chat_id)
            for row in policy_raw.get('enrollments', [])):
        return
    policy, row = _native_policy(chat_id)
    ledger = CaptureLedger(Path(policy['root']) / 'capture' / _digest(policy['namespace']), policy['namespace'])
    if message_id is None:
        _require(str(chat_id) == ABSTRACTDL_ROOT_CHAT_ID and row.get('mode') == 'group',
                 'ambiguous invalidation only supported for exact root group')
        prefix = 'telegram:' + str(chat_id) + ':message:'
    else:
        _require(type(message_id) is int and message_id > 0, 'invalid native message id')
        prefix = 'telegram:' + str(chat_id) + ':message:' + str(message_id) + ':'
    ledger.invalidate_prefix(prefix)


def invalidate_peerless_deletion(message_ids):
    """Deny candidate source coordinates, not an invented Telegram peer.

    Telethon's UpdateDeleteMessages covers DMs AND basic groups; only channel
    deletions carry a peer. Account-local IDs can narrow the uncertainty without
    claiming a chat identity. Every enrolled DM candidate gets a durable
    barrier, even when its original has not arrived yet. Runtime occurrences lack
    native IDs, so their enrollment prefix must still fail closed. Wake/group
    enrollments and unrelated native coordinates are not retired.
    """
    if os.getenv('PRAXIS_KEAT_CAPTURE') != 'on':
        return
    ids = list(message_ids)
    _require(all(type(mid) is int and mid > 0 for mid in ids),
             'invalid peerless deletion IDs')
    if not ids:
        return
    policy = _json(Path(os.environ['PRAXIS_KEAT_CAPTURE_POLICY']).read_bytes())
    _require(policy['schema'] == 'keat.capture-policy.v1' and
             Path(policy['root']).is_absolute(), 'invalid capture policy')
    ledger = CaptureLedger(Path(policy['root']) / 'capture' / _digest(policy['namespace']), policy['namespace'])
    with ledger.guard():
        receipts = ledger._state()[0]
        for row in policy['enrollments']:
            if row['mode'] not in ('owner', 'dm'):
                continue
            _, row = _native_policy(row['stream'])
            prefixes = [
                'telegram:' + row['stream'] + ':message:' + str(mid) + ':'
                for mid in sorted(set(ids))
            ]
            # Runtime occurrences have no native message coordinate. Retire them
            # only when durable native provenance positively ties this peerless
            # batch to the enrolled stream; an unmatched basic-group deletion must
            # not disable future DM turns. Candidate barriers still cover the
            # delete-before-capture race without inventing a chat identity.
            matched = any(r['key'].startswith(prefix)
                          for r in receipts for prefix in prefixes)
            for prefix in prefixes:
                ledger.invalidate_prefix(prefix, basis='peerless_candidate')
            if matched:
                ledger.invalidate_prefix(row['mode'] + ':' + row['stream'] + ':',
                                         basis='peerless_candidate')


def economy_measurement():
    bound = _BOUND.get()
    return copy.deepcopy(bound.economy) if bound is not None else None
