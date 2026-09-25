"""Capture-time canonical ledger, separate from serving and run snapshots.

Trusted local writers supply explicit historical audience/transfer receipts at the
moment an occurrence is observed. Nothing here infers grants from text, run owner
or current rooms. JSONL is canonical; partial/corrupt records fail closed. A shared
reentrant file-lock guard (flock/msvcrt) lets a caller bind current evidence and an epoch checkpoint
without a capture/revocation race. This is not a hostile-writer security boundary.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import threading

from keat_candidate import (ContractError, RECEIPT_TRANSFORM, _digest, _tape,
                            is_render_receipt, schema_digest)
from keat_epoch import (_audience, _bytes, _capture, _hash, _keys, _require,
                        _sync_directory, _text, EvidencePins, exclusive, validate)
from keat_source import _json

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()

# 25.09: разобранное состояние реестра по (каталог, namespace). Реестр append-only и
# сцеплен хэшами, поэтому продолжается с хвоста; см. `state_cache_enabled`.
_STATE_CACHE = {}
_STATE_CACHE_GUARD = threading.Lock()


def state_cache_enabled():
    """Инкрементальный разбор реестра захвата (25.09).

    `_state()` перечитывал и перепроверял реестр ЦЕЛИКОМ на каждую операцию: выдачу
    расписки на входящее, отзыв по удалению, снимок, канарейку перед каждым вызовом
    модели. Реестр личного потока вырос до 19,6 МБ (12.09→25.09), и одна операция
    стала стоить 10–110 с под GIL: замер 25.09, ход в личке владельца — 20 вызовов
    модели, 718 с одних «канареек», а входящее сообщение в личке держало цикл
    Telegram на те же десятки секунд (выдача расписки идёт из обработчика апдейта).

    Реестр пишется только дописыванием. Разобранное состояние продолжается с хвоста,
    но префикс не берётся на веру: файл читается целиком, sha256 уже разобранных байт
    сверяется с запомненным (hashlib отпускает GIL, 19,6 МБ — десятки миллисекунд),
    и только совпавший префикс не разбирается заново. Любое расхождение (файл
    переписан, укорочен, подменён) — полный разбор с нуля, ровно как раньше, и порча
    всплывает той же ошибкой контракта. Проверки на строку те же; меняется только
    то, сколько строк проверяется повторно. `PRAXIS_KEAT_STATE_CACHE=off` возвращает
    полный разбор на каждый вызов.
    """
    raw = (os.getenv('PRAXIS_KEAT_STATE_CACHE') or 'on').strip().lower()
    return raw not in ('off', '0', 'false', 'no')


def _fresh_state():
    return dict(receipts=[], payloads={}, heads={}, current={}, events=set(),
                parent=None, invalidations={})


def _apply_capture_line(st, line, namespace):
    """Одна строка реестра поверх разобранного состояния — тело прежнего цикла `_state`."""
    receipts, payloads, heads, current, events, invalidations = (
        st['receipts'], st['payloads'], st['heads'], st['current'], st['events'],
        st['invalidations'])
    obj = _json(line)
    _keys(obj, 'schema namespace parent operation data')
    _require(obj['schema'] == 'keat.capture.v1' and obj['namespace'] == namespace
             and obj['parent'] == st['parent'], 'corrupt capture lineage')
    data = obj['data']
    if obj['operation'] == 'capture':
        _keys(data, 'receipt payload')
        r = data['receipt']
        _keys(r, 'event key kind revision parent deleted payload_digest capture')
        _text(r['event']); _text(r['key']); _capture(r['capture'])
        _require(r['event'] not in events and r['kind'] in ('message', 'synthetic', 'tool', 'runtime'),
                 'duplicate or invalid occurrence')
        _require(not any(r['key'].startswith(p) for p in invalidations),
                 'capture after invalidation')
        old = heads.get(r['key'])
        _require(type(r['revision']) is int and type(r['deleted']) is bool and
                 r['revision'] == (old['revision'] + 1 if old else 0) and
                 r['parent'] == (_digest(old) if old else None) and
                 (not old or (not old['deleted'] and r['kind'] == old['kind'])), 'invalid capture revision')
        _require(r['payload_digest'] == _digest(data['payload']), 'corrupt original payload')
        grant = r['capture']['grant']
        # A grant is occurrence-specific: edits require fresh explicit grants.
        _require(grant not in current, 'reused capture grant')
        current[grant] = r['capture']
        receipts.append(r); payloads[r['event']] = data['payload']
        heads[r['key']] = r; events.add(r['event'])
    elif obj['operation'] == 'narrow':
        _keys(data, 'grant capture')
        grant, new = data['grant'], data['capture']
        _text(grant)
        _require(grant in current and current[grant] is not None, 'unknown or revoked grant')
        if new is not None:
            _capture(new)
            old = current[grant]
            _require(all(new[k] == old[k] for k in ('issuer', 'policy_revision', 'grant', 'transfer', 'presence_hidden'))
                     and set(new['audience']).issubset(old['audience']), 'current authority may only narrow')
        current[grant] = new
    elif obj['operation'] == 'invalidate':
        _keys(data, 'prefix done basis' if 'basis' in data else 'prefix done')
        if 'basis' in data:
            _require(data['basis'] == 'peerless_candidate', 'invalid barrier basis')
        _require(type(data['prefix']) is str and type(data['done']) is bool,
                 'invalid invalidation barrier')
        prefix = data['prefix']
        if data['done']:
            _require(prefix in invalidations and all(
                current[r['capture']['grant']] is None for r in receipts
                if r['key'].startswith(prefix)), 'unproven revocation')
        invalidations[prefix] = data['done']
    else:
        raise ContractError('unknown capture operation')
    st['parent'] = hashlib.sha256(line).hexdigest()


def _continue_state(base, raw, namespace):
    """Продолжить разбор с хвоста. `base` не трогается: при ошибке контракта кэш цел."""
    _require(not raw or raw.endswith(b'\n'), 'partial capture ledger')
    st = dict(receipts=list(base['receipts']), payloads=dict(base['payloads']),
              heads=dict(base['heads']), current=dict(base['current']),
              events=set(base['events']), parent=base['parent'],
              invalidations=dict(base['invalidations']))
    for line in raw.splitlines():
        _apply_capture_line(st, line, namespace)
    return st


def _state_view(st):
    # Верхний уровень контейнеров — копии: читатели перебирают и фильтруют, внутрь
    # расписок не пишут (сверено 25.09 по keat_live/keat_epoch), а кэш остаётся своим.
    return (list(st['receipts']), dict(st['payloads']), dict(st['heads']),
            dict(st['current']), st['parent'], dict(st['invalidations']))


class CaptureLedger:
    def __init__(self, directory, namespace):
        _text(namespace)
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace
        with _LOCKS_GUARD:
            self._local = _LOCKS.setdefault(str(self.directory), (threading.RLock(), threading.local()))

    @contextmanager
    def guard(self):
        """All capture, narrowing, and binding operations share this guard.

        Издание: межпроцессная часть — `keat_epoch.exclusive` (fcntl или msvcrt).
        """
        lock, local = self._local
        with lock:
            if getattr(local, 'depth', 0):
                local.depth += 1
                try:
                    yield
                finally:
                    local.depth -= 1
                return
            with exclusive(self.directory / 'LOCK'):
                local.depth = 1
                try:
                    yield
                finally:
                    local.depth = 0

    def _state(self):
        path = self.directory / 'capture.jsonl'
        key = (str(self.directory), self.namespace)
        cached = None
        if state_cache_enabled():
            with _STATE_CACHE_GUARD:
                cached = _STATE_CACHE.get(key)
        raw = path.read_bytes() if path.exists() else b''
        consumed = cached['consumed'] if cached is not None else 0
        # mtime и размеру не верим (подмена той же длины в один тик часов невидима):
        # разобранным считается ровно тот префикс, чьи байты хэшируются в запомненное.
        if 0 < consumed <= len(raw) \
                and hashlib.sha256(raw[:consumed]).hexdigest() == cached['prefix_sha256']:
            if consumed == len(raw):
                return _state_view(cached)
            # Продолжаем с хвоста. Не сцепился хвост — файл не дописан, а переписан:
            # разбираем с нуля, и настоящая порча всплывёт той же ошибкой, что и раньше.
            try:
                st = _continue_state(cached, raw[consumed:], self.namespace)
            except ContractError:
                st = None
            if st is not None:
                self._remember(key, st, raw)
                return _state_view(st)
        st = _continue_state(_fresh_state(), raw, self.namespace)
        self._remember(key, st, raw)
        return _state_view(st)

    @staticmethod
    def _remember(key, st, raw):
        if not state_cache_enabled():
            return
        with _STATE_CACHE_GUARD:
            _STATE_CACHE[key] = dict(st, consumed=len(raw),
                                     prefix_sha256=hashlib.sha256(raw).hexdigest())

    def _append(self, operation, data, parent):
        import os
        obj = dict(schema='keat.capture.v1', namespace=self.namespace,
                   parent=parent, operation=operation, data=data)
        with (self.directory / 'capture.jsonl').open('ab') as stream:
            stream.write(_bytes(obj) + b'\n'); stream.flush(); os.fsync(stream.fileno())
        self._sync()

    def _sync(self):
        import os
        # Windows: fsync (FlushFileBuffers) требует дескриптор с правом записи.
        with (self.directory / 'capture.jsonl').open('rb' if os.name != 'nt' else 'r+b') as stream:
            os.fsync(stream.fileno())
        for directory in (self.directory, *self.directory.parents):
            _sync_directory(directory)

    def issue(self, *, occurrence_id, key, kind, payload, capture,
              expected_parent=None, deleted=False):
        """Issue original identity at capture, idempotently by explicit occurrence ID.

        key includes stable channel/chat/topic/message coordinates upstream. Parent
        is the previous *receipt digest*, not a run identifier or content match.
        """
        _text(occurrence_id); _text(key); _capture(capture)
        _require(type(deleted) is bool and kind in ('message', 'synthetic', 'tool', 'runtime'), 'invalid capture')
        if expected_parent is not None:
            _hash(expected_parent)
        payload, capture = _json(_bytes(payload)), _json(_bytes(capture))
        with self.guard():
            receipts, payloads, heads, current, parent, invalidations = self._state()
            _require(not any(key.startswith(p) for p in invalidations), 'invalidated native coordinate')
            old = heads.get(key)
            prior = next((r for r in receipts if r['event'] == occurrence_id), None)
            revision = prior['revision'] if prior else (old['revision'] + 1 if old else 0)
            receipt = dict(event=occurrence_id, key=key, kind=kind, revision=revision,
                           parent=expected_parent, deleted=deleted,
                           payload_digest=_digest(payload), capture=capture)
            if prior:
                _require(_digest(prior) == _digest(receipt), 'occurrence retry changed intent')
                self._sync()
                return prior
            _require(expected_parent == (_digest(old) if old else None) and
                     (not old or (not old['deleted'] and old['kind'] == kind)), 'stale capture parent')
            _require(capture['grant'] not in current, 'reused capture grant')
            self._append('capture', dict(receipt=receipt, payload=payload), parent)
            return receipt

    def invalidate_prefix(self, prefix, *, basis=None):
        """Durable write-ahead barrier; replay completes interrupted revocation.

        Completed prefixes remain tombstones against delayed native originals.
        Empty prefix conservatively retires this namespace (peerless deletion).
        """
        _require(type(prefix) is str, 'invalid prefix')
        _require(basis in (None, 'peerless_candidate'), 'invalid barrier basis')
        evidence = {} if basis is None else {'basis': basis}
        with self.guard():
            receipts, _, _, current, parent, invalidations = self._state()
            if prefix not in invalidations:
                self._append('invalidate', dict(prefix=prefix, done=False, **evidence), parent)
            else:
                self._sync()
            reader = NarrowingReader(self.directory, self.namespace)
            for receipt in receipts:
                if receipt['key'].startswith(prefix):
                    reader.narrow(receipt['capture']['grant'])
            receipts, _, _, current, parent, invalidations = self._state()
            _require(all(current[r['capture']['grant']] is None for r in receipts
                         if r['key'].startswith(prefix)), 'revocation incomplete')
            if not invalidations[prefix]:
                self._append('invalidate', dict(prefix=prefix, done=True, **evidence), parent)
            else:
                self._sync()

    def snapshot(self, keys=None):
        with self.guard():
            receipts, payloads, heads, current, parent, invalidations = self._state()
            if keys is not None:
                _require(type(keys) in (tuple, list) and len(keys) == len(set(keys)) and
                         all(k in heads for k in keys), 'missing selected source')
                receipts = [r for r in receipts if r['key'] in keys]
            return dict(namespace=self.namespace, receipts=receipts,
                        payloads={r['event']: payloads[r['event']] for r in receipts}, revision=parent)

    def project(self, *, messages, origins, transforms, issuer, tools,
                historical_count, keys=None):
        """Explicit ordered occurrence projection; never searches message content."""
        _text(issuer)
        _tape(messages)
        schema_digest(tools)
        _require(type(historical_count) is int and 0 <= historical_count <= len(messages),
                 'invalid authoritative cut')
        _require(type(origins) is list and type(transforms) is list and
                 len(messages) == len(origins) == len(transforms), 'missing ordered projection')
        with self.guard():
            selected = self.snapshot(keys)['receipts']
            heads = {r['key']: r for r in selected}
            expected = [r['event'] for r in selected if heads[r['key']] is r and not r['deleted']]
            for index, (origin, transform) in enumerate(zip(origins, transforms)):
                _text(transform)
                if transform == RECEIPT_TRANSFORM:
                    # Издание: расписка сборщика — без происхождения, но только точная и
                    # только сразу за своим вызовом (keat_candidate.render_receipt).
                    _require(origin == [] and is_render_receipt(messages, index),
                             'invalid render receipt')
                    continue
                _require(type(origin) is list and bool(origin) and
                         all(type(event) is str for event in origin), 'missing typed origins')
            _require([event for origin in origins for event in origin] == expected,
                     'incomplete/reordered/duplicate projection')
            source = dict(schema='keat.original.v1', namespace=self.namespace,
                          receipts=selected, messages=messages,
                          tools=tools, historical_count=historical_count,
                          projection=[dict(index=i, message_digest=_digest(m), origins=origins[i],
                                           transform=transforms[i], issuer=issuer)
                                      for i, m in enumerate(messages)])
            return _bytes(source)


class NarrowingReader:
    """Independent current reader: never accepts caller-provided authorization pins.

    Current grants come from the canonical capture + explicit narrowing ledger,
    not from candidate source assertions. v1 cannot transfer across audiences.
    """
    def __init__(self, directory, namespace):
        self.ledger = CaptureLedger(directory, namespace)

    def narrow(self, grant, capture=None):
        """None revokes permanently; an explicit capture may only reduce audience."""
        _text(grant)
        if capture is not None:
            _capture(capture)
        with self.ledger.guard():
            _, _, _, current, parent, _ = self.ledger._state()
            _require(grant in current, 'unknown grant')
            old = current[grant]
            if _digest(old) == _digest(capture):
                self.ledger._sync()
                return
            _require(old is not None, 'revoked grant cannot revive')
            if capture is not None:
                _require(all(capture[k] == old[k] for k in ('issuer', 'policy_revision', 'grant', 'transfer', 'presence_hidden'))
                         and set(capture['audience']).issubset(old['audience']), 'current authority may only narrow')
            self.ledger._append('narrow', dict(grant=grant, capture=capture), parent)

    def read(self, *, source_bytes, audience, now):
        _require(type(now) is int and now >= 0 and type(audience) is tuple, 'trusted audience/time required')
        _audience(list(audience))
        with self.ledger.guard():
            receipts, _, heads, current, revision, invalidations = self.ledger._state()
            _require(all(invalidations.values()), 'pending native invalidation')
            source = _json(source_bytes)
            _require(type(source) is dict and type(source.get('receipts')) is list, 'invalid source')
            canonical = {r['event']: r for r in receipts}
            for receipt in source['receipts']:
                _require(type(receipt) is dict and type(receipt.get('event')) is str and
                         receipt['event'] in canonical and
                         _digest(receipt) == _digest(canonical[receipt['event']]), 'source lacks capture receipt')
            auth = _bytes(dict(schema='keat.authorization.v1', namespace=self.ledger.namespace,
                               revision=revision or 'empty', audience=list(audience), valid_from=now,
                               valid_until=now + 1, heads={k: _digest(r) for k, r in heads.items()},
                               grants={k: c for k, c in current.items() if c is not None}))
            pins = EvidencePins(self.ledger.namespace, hashlib.sha256(source_bytes).hexdigest(),
                                hashlib.sha256(auth).hexdigest(), revision or 'empty')
            validate(source_bytes=source_bytes, authority_bytes=auth, pins=pins, audience=audience, now=now)
            return auth, pins
