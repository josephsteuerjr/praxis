"""Isolated offline contracts. Pins are caller trust, NOT signatures or live policy.

POSIX local-filesystem checkpoint store; no live imports or provider execution.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import tempfile

from keat_candidate import ContractError, _digest, _tape, schema_digest
from keat_source import _json, _pinned


def _require(ok, message):
    if not ok:
        raise ContractError(message)


def _keys(value, keys):
    _require(type(value) is dict and set(value) == set(keys.split()), 'invalid receipt shape')


def _text(value):
    _require(type(value) is str and bool(value.strip()), 'missing identity')


def _hash(value):
    _require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None,
             'invalid digest')


def _audience(value):
    _require(type(value) is list and bool(value), 'missing audience')
    for item in value:
        _text(item)
    _require(len(value) == len(set(value)), 'duplicate audience')


def _capture(value):
    _keys(value, 'issuer policy_revision grant audience transfer presence_hidden')
    for field in ('issuer', 'policy_revision', 'grant', 'transfer'):
        _text(value[field])
    _audience(value['audience'])
    _require(type(value['presence_hidden']) is bool, 'missing capture presence')


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sync_file(path):
    with path.open('rb') as stream:
        os.fsync(stream.fileno())


def _bytes(value):
    import json
    _digest(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


@dataclass(frozen=True)
class EvidencePins:
    namespace: str
    source_sha256: str
    authority_sha256: str
    authority_revision: str


def validate(*, source_bytes: bytes, authority_bytes: bytes, pins: EvidencePins,
             audience: tuple[str, ...], now: int) -> dict:
    """Return owned exact JSON after independent source/current-authority pin checks.

    Issuer attests ordered projection (including transformations); this validates
    that attestation, not semantic correctness of a transformation. Every live
    original occurs once in the projection, possibly coalesced with others.
    """
    _require(type(pins) is EvidencePins, 'independent evidence pins required')
    _text(pins.namespace)
    _text(pins.authority_revision)
    _require(type(now) is int and now >= 0, 'trusted current time required')
    _require(type(audience) is tuple, 'explicit audience required')
    _audience(list(audience))
    _pinned(source_bytes, pins.source_sha256)
    _pinned(authority_bytes, pins.authority_sha256)
    source, auth = _json(source_bytes), _json(authority_bytes)
    _keys(source, 'schema namespace receipts messages projection tools historical_count')
    _keys(auth, 'schema namespace revision audience valid_from valid_until grants heads')
    _require(source['schema'] == 'keat.original.v1' and auth['schema'] == 'keat.authorization.v1',
             'legacy or unknown evidence unsupported')
    _require(source['namespace'] == auth['namespace'] == pins.namespace, 'namespace changed')
    _require(auth['revision'] == pins.authority_revision, 'stale authority revision')
    _require(auth['audience'] == list(audience), 'changed audience')
    _require(type(auth['valid_from']) is int and type(auth['valid_until']) is int and
             auth['valid_from'] <= now < auth['valid_until'], 'expired or future authority')
    _require(type(auth['grants']) is dict and type(auth['heads']) is dict, 'missing current authority')
    _require(type(source['receipts']) is list, 'missing original receipts')
    heads, live, events = {}, [], set()
    for receipt in source['receipts']:
        _keys(receipt, 'event key kind revision parent deleted payload_digest capture')
        for field in ('event', 'key'):
            _text(receipt[field])
        _require(receipt['kind'] in ('message', 'synthetic', 'tool', 'runtime'), 'untyped origin')
        _hash(receipt['payload_digest'])
        _require(type(receipt['deleted']) is bool and type(receipt['revision']) is int,
                 'invalid revision')
        event, key = receipt['event'], receipt['key']
        _require(event not in events, 'duplicate original event')
        events.add(event)
        previous = heads.get(key)
        _require(receipt['revision'] == (previous['revision'] + 1 if previous else 0) and
                 receipt['parent'] == (_digest(previous) if previous else None),
                 'missing or reordered revision lineage')
        if previous:
            _require(not previous['deleted'] and previous['kind'] == receipt['kind'],
                     'deleted or changed origin lineage')
        capture = receipt['capture']
        _capture(capture)
        heads[key] = receipt
    # Source ledger order defines live original order, not text or model-call index.
    live = [r for r in source['receipts'] if heads[r['key']] is r and not r['deleted']]
    for key, receipt in heads.items():
        _require(auth['heads'].get(key) == _digest(receipt), 'edited/deleted/stale original')
    for receipt in live:
        capture = receipt['capture']
        _require(capture['audience'] == list(audience), 'historical audience mismatch')
        grant = auth['grants'].get(capture['grant'])
        _capture(grant)
        _require(grant == capture,
                 'revoked or changed transfer/presence grant')
    messages = source['messages']
    _tape(messages)
    schema_digest(source['tools'])
    cut = source['historical_count']
    _require(type(cut) is int and 0 <= cut <= len(messages), 'invalid authoritative cut')
    projection = source['projection']
    _require(type(projection) is list and len(projection) == len(messages), 'missing projection')
    references = []
    for index, entry in enumerate(projection):
        _keys(entry, 'index message_digest origins transform issuer')
        _require(type(entry['index']) is int and entry['index'] == index and
                 entry['message_digest'] == _digest(messages[index]), 'changed projection tape')
        _text(entry['transform'])
        _text(entry['issuer'])
        _require(type(entry['origins']) is list and bool(entry['origins']) and
                 all(type(x) is str for x in entry['origins']), 'missing typed origins')
        references.extend(entry['origins'])
    _require(references == [r['event'] for r in live], 'incomplete/reordered/duplicate projection')
    return source


class EpochStore:
    """Immutable objects + atomic HEAD under a cooperative POSIX flock.

    Caller supplies expected HEAD independently on *every* operation. Filesystem
    and lock participants are trusted; hashes detect corruption, not hostile writers.
    """
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        self.directory.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _locked(self):
        import fcntl  # Explicit POSIX-only backend; never silently weaken locking.
        with (self.directory / 'LOCK').open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _head(self):
        try:
            raw = (self.directory / 'HEAD').read_bytes()
        except FileNotFoundError:
            return None
        try:
            head = raw.decode('ascii')
        except UnicodeError as exc:
            raise ContractError('corrupt HEAD') from exc
        _hash(head)
        return head

    def _load(self, digest):
        _hash(digest)
        try:
            raw = (self.directory / (digest + '.json')).read_bytes()
        except FileNotFoundError as exc:
            raise ContractError('missing checkpoint') from exc
        _pinned(raw, digest)
        obj = _json(raw)
        _keys(obj, 'schema parent epoch policy reason source source_sha256 schema_digest issuance')
        _require(obj['schema'] == 'keat.epoch.v1', 'unsupported checkpoint')
        # Traverse all immutable parents: missing/corrupt ancestors fail closed.
        return obj

    def _lineage(self, head):
        seen = set()
        while head is not None:
            _require(head not in seen, 'cyclic lineage')
            seen.add(head)
            obj = self._load(head)
            head = obj['parent']

    def _durable_head(self, digest):
        # Visibility after replace is not a durability receipt. Re-sync even on
        # exact retries, including files left by interrupted publication.
        _sync_file(self.directory / (digest + '.json'))
        _sync_file(self.directory / 'HEAD')
        # Every containing entry matters for nested mkdir, including directories
        # already visible after an earlier failed attempt. No in-memory "created"
        # list can establish their durability after a process restart.
        for directory in (self.directory, *self.directory.parents):
            _sync_directory(directory)

    def _atomic(self, name, raw):
        fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=self.directory)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, self.directory / name)
            _sync_directory(self.directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def checkpoint(self, *, expected_parent, epoch: str, policy: str, reason: str,
                   source_bytes: bytes, authority_bytes: bytes, pins: EvidencePins,
                   audience: tuple[str, ...], now: int) -> str:
        source = validate(source_bytes=source_bytes, authority_bytes=authority_bytes,
                          pins=pins, audience=audience, now=now)
        for value in (epoch, policy, reason):
            _text(value)
        if expected_parent is not None:
            _hash(expected_parent)
        obj = dict(schema='keat.epoch.v1', parent=expected_parent, epoch=epoch,
                   policy=policy, reason=reason, source=source_bytes.decode('utf-8'),
                   source_sha256=pins.source_sha256,
                   schema_digest=schema_digest(source['tools']),
                   issuance=dict(namespace=pins.namespace, audience=list(audience),
                                 authority_sha256=pins.authority_sha256,
                                 authority_revision=pins.authority_revision))
        raw = _bytes(obj)
        digest = hashlib.sha256(raw).hexdigest()
        with self._locked():
            head = self._head()
            self._lineage(head)
            if head == digest:  # Same exact intent after a lost acknowledgement.
                self._durable_head(digest)
                return digest
            _require(head == expected_parent, 'stale checkpoint parent')
            if head is not None:
                parent = self._load(head)
                if parent['epoch'] == epoch:
                    old = _json(parent['source'].encode('utf-8'))
                    n, m = old['historical_count'], source['historical_count']
                    _require(parent['policy'] == policy and
                             parent['issuance']['namespace'] == pins.namespace and
                             parent['issuance']['audience'] == list(audience) and m >= n and
                             old['messages'][:n] == source['messages'][:n] and
                             old['projection'][:n] == source['projection'][:n],
                             'A changed: explicit new epoch required')
                    old_receipts = {r['event']: r for r in old['receipts']}
                    new_receipts = {r['event']: r for r in source['receipts']}
                    for entry in old['projection'][:n]:
                        for event in entry['origins']:
                            _require(new_receipts.get(event) == old_receipts[event],
                                     'historical receipt changed')
            path = self.directory / (digest + '.json')
            if path.exists():
                _pinned(path.read_bytes(), digest)
                _sync_file(path)
                _sync_directory(self.directory)
            else:
                self._atomic(path.name, raw)
            self._atomic('HEAD', digest.encode('ascii'))
            self._durable_head(digest)
        return digest

    def reopen(self, *, expected_head: str, authority_bytes: bytes,
               pins: EvidencePins, audience: tuple[str, ...], now: int) -> dict:
        _hash(expected_head)
        with self._locked():
            _require(self._head() == expected_head, 'stale or missing checkpoint head')
            self._lineage(expected_head)
            obj = self._load(expected_head)
            _require(type(pins) is EvidencePins, 'independent evidence pins required')
            _require(obj['source_sha256'] == pins.source_sha256, 'changed source pin')
            _require(obj['issuance']['namespace'] == pins.namespace and
                     obj['issuance']['audience'] == list(audience), 'changed checkpoint audience')
            source = validate(source_bytes=obj['source'].encode('utf-8'),
                              authority_bytes=authority_bytes, pins=pins,
                              audience=audience, now=now)
            _require(obj['schema_digest'] == schema_digest(source['tools']), 'changed schema')
            cut = source['historical_count']
            return dict(checkpoint=expected_head, epoch=obj['epoch'], policy=obj['policy'],
                        historical_a=source['messages'][:cut], active_roles=source['messages'][cut:],
                        source=source)
