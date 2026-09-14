"""Default-off durable KEAT binding, preserving the exact live provider tape.

A runtime commit is ONE EpochStore object/HEAD: its versioned reason envelope
binds the request identity and exact system/tape/tools to source, authorization
bytes and pins, and A. There is no separately published pin/checkpoint pointer.
POSIX trusted local storage/lock assumptions are those of keat_epoch. Staging
is a caller-side canary selector, not an authorization issuer.
"""
from dataclasses import asdict, dataclass
import json
import os

from keat_candidate import ContractError, _digest
from keat_epoch import EpochStore, EvidencePins, _bytes, _require, _text, validate
from keat_source import _json

MODES = frozenset(('owner', 'dm', 'group', 'wake', 'window'))


def _canonical_pairs(raw):
    """Parse the exact sparse serving selector, or return ``None``.

    Canonical bytes make receipts reproducible. Pair order is canonical too:
    enrollment is a set, not an operator-controlled precedence list.
    """
    if type(raw) is not str:
        return None
    try:
        value = json.loads(raw)
        if (type(value) is not list or not value or
                any(type(row) is not list or len(row) != 2 for row in value)):
            return None
        pairs = []
        for mode, stream in value:
            if (type(mode) is not str or mode not in MODES or
                    type(stream) is not str or not stream or stream != stream.strip()):
                return None
            pairs.append((mode, stream))
        if len(pairs) != len(set(pairs)) or pairs != sorted(pairs):
            return None
        canonical = json.dumps(value, ensure_ascii=True, allow_nan=False,
                               separators=(',', ':'))
        return frozenset(pairs) if raw == canonical else None
    except (TypeError, ValueError, RecursionError):
        return None


def _legacy_owner_pairs(env):
    """Compatibility for the deployed owner-only selector variables.

    Legacy variables can never enroll a new mode. All widening uses sparse pairs,
    avoiding the former modes x streams Cartesian product.
    """
    modes = env.get('PRAXIS_KEAT_MODES')
    streams = env.get('PRAXIS_KEAT_STREAMS')
    # A single historical mode is unambiguous and therefore not Cartesian.  Keep
    # deployed owner (and pre-existing adapter test/canary) configurations working,
    # but require PAIRS for the newly introduced dm mode and every multi-mode stage.
    if (type(modes) is not str or modes not in MODES - {'dm', 'group'} or
            type(streams) is not str):
        return None
    values = streams.split(',')
    if (not values or not all(values) or any(value != value.strip() for value in values)
            or len(values) != len(set(values))):
        return None
    return frozenset((modes, value) for value in values)


def enrollment_pairs(env):
    """Return exact pairs, preferring PRAXIS_KEAT_PAIRS when it is present."""
    if 'PRAXIS_KEAT_PAIRS' in env:
        return _canonical_pairs(env.get('PRAXIS_KEAT_PAIRS'))
    return _legacy_owner_pairs(env)


def staged(mode, stream, env=None):
    """Every exact pair requires explicit opt-in; rollback takes effect now."""
    env = os.environ if env is None else env
    pairs = enrollment_pairs(env)
    return bool(env.get('PRAXIS_KEAT', '') == 'serve' and pairs is not None and
                type(mode) is str and type(stream) is str and (mode, stream) in pairs)


@dataclass(frozen=True)
class ResumeBinding:
    run_id: str
    call_id: str
    mode: str
    stream: str
    audience: tuple[str, ...]

    def value(self):
        for value in (self.run_id, self.call_id, self.stream):
            _text(value)
        _require(self.mode in MODES, 'unsupported runtime mode')
        _require(type(self.audience) is tuple and bool(self.audience), 'missing runtime audience')
        for value in self.audience:
            _text(value)
        _require(len(set(self.audience)) == len(self.audience), 'duplicate runtime audience')
        result = asdict(self)
        result['audience'] = list(self.audience)
        return result


def _request(system, messages, tools):
    _require(type(system) is str or (type(system) is list and
             all(type(block) is dict for block in system)), 'exact system required')
    # Canonical JSON identity retains roles, media, ordering and numeric types.
    return _digest(dict(system=system, messages=messages, tools=tools))


class RuntimeStore:
    def __init__(self, directory):
        self.epochs = EpochStore(directory)

    def checkpoint(self, *, binding, expected_parent, epoch, policy, reason,
                   source_bytes, authority_bytes, pins, now, system, messages, tools):
        """Low-level trusted-reader API. Caller holds capture/current-reader guard.

        No provider objects are modified. Contract or I/O failures MUST select the
        original live request, never an unverified partial A or reconstructed tape.
        Same exact intent retries are inherited from EpochStore.
        """
        _require(type(binding) is ResumeBinding, 'runtime binding required')
        _text(reason)
        source = validate(source_bytes=source_bytes, authority_bytes=authority_bytes,
                          pins=pins, audience=binding.audience, now=now)
        _require(_digest(source['messages']) == _digest(messages), 'provider tape changed')
        _require(_digest(source['tools']) == _digest(tools), 'provider tools changed')
        envelope = dict(schema='keat.runtime.v1', binding=binding.value(), reason=reason,
                        request_digest=_request(system, messages, tools),
                        authority=authority_bytes.decode('utf-8'), pins=asdict(pins))
        return self.epochs.checkpoint(
            expected_parent=expected_parent, epoch=epoch, policy=policy,
            reason=_bytes(envelope).decode('utf-8'), source_bytes=source_bytes,
            authority_bytes=authority_bytes, pins=pins, audience=binding.audience, now=now)

    def _bound_object(self, expected_head, binding, system, messages, tools):
        _require(type(binding) is ResumeBinding, 'runtime binding required')
        obj = self.epochs._load(expected_head)
        envelope = _json(obj['reason'].encode('utf-8'))
        _require(type(envelope) is dict and set(envelope) == {
            'schema', 'binding', 'reason', 'request_digest', 'authority', 'pins'},
            'missing runtime envelope')
        _require(envelope['schema'] == 'keat.runtime.v1', 'unsupported runtime envelope')
        _require(envelope['binding'] == binding.value(), 'resume identity changed')
        _require(envelope['request_digest'] == _request(system, messages, tools),
                 'resume request changed')
        # Recheck the issuance artifact against the issuance pins, not current pins.
        issuance = EvidencePins(**envelope['pins'])
        _require(issuance.source_sha256 == obj['source_sha256'] and
                 issuance.namespace == obj['issuance']['namespace'] and
                 issuance.authority_sha256 == obj['issuance']['authority_sha256'] and
                 issuance.authority_revision == obj['issuance']['authority_revision'],
                 'runtime issuance binding changed')
        from keat_source import _pinned
        _pinned(envelope['authority'].encode('utf-8'), issuance.authority_sha256)
        return obj

    def reopen(self, *, expected_head, binding, authority_bytes, pins, now,
               system, messages, tools):
        """Fresh independent current narrowing evidence required on EVERY reopen."""
        self._bound_object(expected_head, binding, system, messages, tools)
        result = self.epochs.reopen(expected_head=expected_head,
                                    authority_bytes=authority_bytes, pins=pins,
                                    audience=binding.audience, now=now)
        _require(_digest(result['source']['messages']) == _digest(messages) and
                 _digest(result['source']['tools']) == _digest(tools),
                 'resume source request changed')
        return result

    @staticmethod
    def _capture_pair(ledger, reader):
        _require(reader.ledger.directory == ledger.directory and
                 reader.ledger.namespace == ledger.namespace,
                 'capture and narrowing reader must share transaction lock')

    def checkpoint_capture(self, *, ledger, reader, source_bytes, **kwargs):
        """Serialize capture/head/auth validation through atomic publication."""
        self._capture_pair(ledger, reader)
        with ledger.guard():
            authority_bytes, pins = reader.read(source_bytes=source_bytes,
                                                audience=kwargs['binding'].audience, now=kwargs['now'])
            return self.checkpoint(**kwargs, source_bytes=source_bytes,
                                   authority_bytes=authority_bytes, pins=pins)

    def reopen_capture(self, *, ledger, reader, **kwargs):
        self._capture_pair(ledger, reader)
        with ledger.guard():
            obj = self._bound_object(kwargs['expected_head'], kwargs['binding'],
                                     kwargs['system'], kwargs['messages'], kwargs['tools'])
            authority_bytes, pins = reader.read(source_bytes=obj['source'].encode('utf-8'),
                                                audience=kwargs['binding'].audience, now=kwargs['now'])
            return self.reopen(**kwargs, authority_bytes=authority_bytes, pins=pins)
