"""Offline durable-snapshot inspection, NOT a provider or KEAT assembly path.

Trust starts at independently retained pins and run provenance, not input JSON.
No IO, live imports, reconstruction, scope inference or candidate promotion.
"""
from dataclasses import dataclass
import hashlib
import json
import re

from keat_candidate import ContractError, _digest, _tape, schema_digest


class UnsupportedSource(ContractError):
    """Native evidence cannot establish the requested authority."""


@dataclass(frozen=True)
class SourcePins:
    """External offline evidence; never derive these from untrusted inputs on retry.

    namespace identifies the authoritative durable store (not a local file path).
    Digests pin exact bytes, including event order, and are NOT signatures.
    """
    namespace: str
    manifest_sha256: str
    events_sha256: str
    model_input_sha256: str


@dataclass(frozen=True)
class RunProvenance:
    run_id: str
    principal_id: str
    scope: str
    origin_chat_id: str | None
    delivery_chat_id: str | None


@dataclass(frozen=True)
class Occurrence:
    namespace: str
    run_id: str
    event_id: str
    call_id: str
    result_id: str
    index: int


@dataclass(frozen=True)
class DurableSnapshot:
    """Owned parsed JSON, exact to persisted input, not necessarily provider input.

    Nested objects remain mutable: re-adapt pinned bytes before further use.
    Run provenance is NOT audience authorization or message-level provenance.
    """
    model_input: dict
    occurrences: tuple[Occurrence, ...]
    provenance: RunProvenance
    offered_schema_digest: str
    audience_support: str = 'unsupported: no durable per-occurrence audience provenance'

    def require_candidate_authority(self):
        raise UnsupportedSource(self.audience_support)


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ContractError('duplicate JSON key')
            value[key] = item
        return value
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs)
        _digest(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ContractError('invalid durable JSON') from exc


def _pinned(raw, digest):
    if (type(raw) is not bytes or type(digest) is not str or
            re.fullmatch('[0-9a-f]{64}', digest) is None or
            hashlib.sha256(raw).hexdigest() != digest):
        raise ContractError('missing or changed independently pinned bytes')


def _text(value):
    return type(value) is str and bool(value.strip())


def adapt_model_input(*, manifest_bytes: bytes, events_bytes: bytes,
                      model_input_bytes: bytes, pins: SourcePins,
                      expected_provenance: RunProvenance,
                      call_id: str) -> DurableSnapshot:
    """Inspect one entire durable model_input occurrence tape.

    Native IDs identify occurrences within a stored call, NEVER originating chat
    events across calls. No cut is guessed. All three byte pins and current run
    provenance must come independently from the trusted offline evidence reader.
    No caller-provided audience can turn this into a supported candidate.
    """
    if type(pins) is not SourcePins or not _text(pins.namespace):
        raise UnsupportedSource('authoritative durable namespace and pins required')
    if type(expected_provenance) is not RunProvenance:
        raise UnsupportedSource('independently revalidated run provenance required')
    for raw, digest in ((manifest_bytes, pins.manifest_sha256),
                        (events_bytes, pins.events_sha256),
                        (model_input_bytes, pins.model_input_sha256)):
        _pinned(raw, digest)
    manifest = _json(manifest_bytes)
    if type(manifest) is not dict or manifest.get('schema') != 'praxis.run.v1':
        raise UnsupportedSource('unsupported manifest')
    context = manifest.get('context')
    fields = tuple(RunProvenance.__dataclass_fields__)
    if type(context) is not dict or any(k not in context for k in fields):
        raise UnsupportedSource('missing run provenance')
    provenance = RunProvenance(**{k: context[k] for k in fields})
    if (any(not _text(getattr(provenance, k)) for k in fields[:3]) or
            any(v is not None and not _text(v) for v in
                (provenance.origin_chat_id, provenance.delivery_chat_id)) or
            provenance != expected_provenance):
        raise ContractError('run provenance mismatch')
    if not _text(call_id):
        raise UnsupportedSource('source call identity required')
    rows = [_json(line) for line in events_bytes.splitlines()]
    if not rows or type(manifest.get('event_seq')) is not int or manifest['event_seq'] != len(rows):
        raise ContractError('incomplete event snapshot')
    selected = None
    calls, results = set(), set()
    for seq, row in enumerate(rows, 1):
        if (type(row) is not dict or row.get('schema') != 'praxis.run.event.v1' or
                row.get('run_id') != provenance.run_id or type(row.get('seq')) is not int or
                row['seq'] != seq or row.get('id') != f'{provenance.run_id}:evt:{seq:08d}' or
                not _text(row.get('at')) or not _text(row.get('kind')) or
                (seq == 1 and row['kind'] != 'run_created')):
            raise ContractError('invalid, mixed-run or reordered events')
        if row['kind'] != 'model_input':
            continue
        ref = row.get('result')
        cid = row.get('call_id')
        if (not _text(cid) or cid in calls or type(ref) is not dict or
                ref.get('schema') != 'praxis.result-ref.v1' or
                ref.get('run_id') != provenance.run_id or
                not _text(ref.get('result_id')) or
                re.fullmatch(r'result-\d{4,12}', ref['result_id']) is None or
                ref['result_id'] in results):
            raise ContractError('missing or duplicate source occurrence identity')
        calls.add(cid)
        results.add(ref['result_id'])
        if cid == call_id:
            selected = row
    if selected is None:
        raise UnsupportedSource('no durable model_input for call')
    ref = selected['result']
    number = ref['result_id'][7:]
    if (ref.get('path') != f'results/{number}-model-input.log' or
            ref.get('encoding') != 'utf-8' or
            ref.get('media_type') != 'application/json; charset=utf-8' or
            type(ref.get('size')) is not int or ref['size'] != len(model_input_bytes)):
        raise ContractError('unsupported or inconsistent model-input ResultRef')
    _pinned(model_input_bytes, ref.get('sha256'))
    model_input = _json(model_input_bytes)
    if (type(model_input) is not dict or 'system' not in model_input or
            type(model_input.get('tools')) is not list):
        raise UnsupportedSource('model_input lacks structured system/messages/tools')
    _tape(model_input.get('messages'))
    occurrences = tuple(Occurrence(pins.namespace, provenance.run_id, selected['id'],
                                   call_id, ref['result_id'], index)
                        for index in range(len(model_input['messages'])))
    return DurableSnapshot(model_input, occurrences, provenance,
                           schema_digest(model_input['tools']))
