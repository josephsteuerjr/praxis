"""Owner-enabled scheduling receipts: replies cover only observed pings.

Telegram IDs order messages within a peer; conversation IDs retain the exact
forum topic. This acknowledges scheduling only, never that a model read or
answered every message. Raw history and delivery receipts remain authoritative.
"""
from __future__ import annotations

import hashlib
import json
import logging
import functools
import tempfile
import os
import threading
from pathlib import Path

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
_LOCK = threading.RLock()
_CACHE = {}
_LOG = logging.getLogger(__name__)


def _best_effort(default):
    """The disposable scheduling index must never veto delivery or wake."""
    def decorate(fn):
        @functools.wraps(fn)
        def call(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                # Do not include exception text: paths/receipt contents are private.
                _LOG.warning("addressed reply index %s failed (%s)", fn.__name__,
                             type(exc).__name__)
                return default
        return call
    return decorate


@_best_effort(False)
def enabled():
    path = BASE / "memory/policies/addressed-replies.json"
    if not path.exists():
        return False
    return json.loads(path.read_text(encoding="utf-8")).get("enabled") is True


def _path(chat_id):
    key = hashlib.sha256(str(chat_id).encode()).hexdigest()
    return BASE / "memory/.state/addressed-replies" / (key + ".jsonl")


def _positive(value):
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if result > 0 and str(result) == str(value) else None


def _exact_ids(value):
    if not isinstance(value, list) or any(
            isinstance(mid, bool) or not isinstance(mid, int) or mid <= 0
            for mid in value):
        raise ValueError("observed_message_ids must be a positive integer list")
    return frozenset(value)


def _scan(chat_id, data):
    rows, issues = [], []
    for number, line in enumerate(data.splitlines(), 1):
        try:
            row = json.loads(line)
            if (not isinstance(row, dict)
                    or row.get("schema") not in {"praxis.addressed-reply.v1", "praxis.addressed-reply.v2", "praxis.addressed-reply.v3"}
                    or row.get("chat_id") != str(chat_id)
                    or _positive(row.get("reply_message_id")) is None
                    or not isinstance(row.get("delivery_key"), str)
                    or not row["delivery_key"]):
                raise ValueError("invalid receipt")
            if row["schema"] == "praxis.addressed-reply.v2":
                observed = _positive(row.get("observed_message_id"))
                if observed is None or observed >= _positive(row["reply_message_id"]):
                    raise ValueError("invalid observed frontier")
            if row["schema"] == "praxis.addressed-reply.v3":
                observed = _exact_ids(row.get("observed_message_ids"))
                if not observed or max(observed) >= _positive(row["reply_message_id"]):
                    raise ValueError("invalid observed membership")
        except (ValueError, UnicodeError, TypeError):
            issues.append(dict(line=number, reason="invalid_receipt",
                               sha256=hashlib.sha256(line).hexdigest()))
        else:
            rows.append(row)
    return rows, issues


def diagnostics(chat_id):
    """Read-only integrity report, including unreadable index/policy failures.

    Never returns message text, keys or malformed raw bytes. Independent of the
    enable switch so an operator can inspect a disabled/corrupt index.
    """
    path = _path(chat_id)
    report = dict(ok=False, rows=0, issues=[], sha256=None)
    try:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            data = b""
        rows, issues = _scan(chat_id, data)
        report.update(ok=not issues, rows=len(rows), issues=issues,
                      sha256=hashlib.sha256(data).hexdigest(),
                      legacy_rows=sum(r["schema"] != "praxis.addressed-reply.v3" for r in rows),
                      needs_recovery=bool(issues or (data and not data.endswith(b"\n"))))
        policy = BASE / "memory/policies/addressed-replies.json"
        if policy.exists():
            value = json.loads(policy.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("invalid policy")
    except Exception as exc:
        report["ok"] = False
        report["issues"].append(dict(reason="index_or_policy_error",
                                      error=type(exc).__name__))
    return report


@_best_effort(frozenset())
def _covered_ids(chat_id):
    """Exact read evidence; corruption disables coverage, never delivery/wake."""
    if not enabled() or not str(chat_id).startswith("-"):
        return frozenset()
    path = _path(chat_id)
    with _LOCK:
        if not path.exists():
            return frozenset()
        st = path.stat()
        signature = (st.st_mtime_ns, st.st_size, st.st_ino, st.st_ctime_ns)
        cached = _CACHE.get(str(path))
        if cached and cached[0] == signature:
            return cached[1]
        rows, issues = _scan(chat_id, path.read_bytes())
        if issues:
            _LOG.warning("addressed reply index corrupt: %d invalid rows; coverage disabled",
                         len(issues))
            latest = frozenset()
        else:
            latest = frozenset(mid for row in rows
                               if row["schema"] == "praxis.addressed-reply.v3"
                               for mid in row["observed_message_ids"])
        _CACHE[str(path)] = (signature, latest)
        return latest


def watermark(chat_id):
    """Diagnostic maximum only: never evidence that lower IDs were read."""
    return max(_covered_ids(chat_id), default=0)


def _sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def recover(chat_id):
    """Explicit local repair, not an automatic live-data migration.

    Preserve exact damaged bytes in an immutable content-addressed backup before
    atomically retaining validated receipts. Invalid rows provide no coverage.
    Does not reconstruct missing receipts; replay of accepted outbox entries can
    do that afterwards. Failures propagate to the operator, never to delivery.
    """
    from self_model import FileLock
    path = _path(chat_id)
    with _LOCK:
        if not path.exists():
            return dict(changed=False, removed=0, backup=None)
        with FileLock(path.with_suffix(".lock"), timeout=10.0,
                      stale_after=300.0, heartbeat=5.0):
            data = path.read_bytes()
            rows, issues = _scan(chat_id, data)
            if not issues and (not data or data.endswith(b"\n")):
                return dict(changed=False, removed=0, backup=None)
            digest = hashlib.sha256(data).hexdigest()
            backup = path.with_name(path.name + ".corrupt-" + digest + ".bak")
            # Publish the backup only after a complete durable write. A crash
            # cannot leave a partial content-addressed backup blocking retries.
            backup_fd, backup_temp = tempfile.mkstemp(prefix=".receipt-backup-", dir=path.parent)
            try:
                with os.fdopen(backup_fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(backup_temp, backup)
                except FileExistsError:
                    if backup.read_bytes() != data:
                        raise ValueError("addressed reply backup mismatch")
                _sync_directory(path.parent)
            finally:
                os.unlink(backup_temp)
            clean = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True)
                              + "\n").encode() for row in rows)
            fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(clean)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                _sync_directory(path.parent)
                _CACHE.pop(str(path), None)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return dict(changed=True, removed=len(issues), backup=str(backup),
                        sha256=digest)


def covered(chat_id, message_id):
    mid = _positive(message_id)
    return mid is not None and mid in _covered_ids(chat_id)


@_best_effort(False)
def record(chat_id, reply_message_id, *, delivery_key, observed_message_id=None,
           observed_message_ids=None):
    """Call only with an accepted Telegram reply and its stable delivery key."""
    mid = _positive(reply_message_id)
    observed = (_exact_ids(observed_message_ids) if observed_message_ids is not None
                else frozenset([_positive(observed_message_id)]) - {None})
    if (mid is None or not observed or max(observed) >= mid or not delivery_key
            or not enabled() or not str(chat_id).startswith("-")):
        return False
    from self_model import FileLock
    path = _path(chat_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _LOCK, FileLock(path.with_suffix(".lock"), timeout=10.0,
                         stale_after=300.0, heartbeat=5.0):
        if path.exists():
            data = path.read_bytes()
            _, issues = _scan(chat_id, data)
            if issues or (data and not data.endswith(b"\n")):
                _LOG.warning("addressed reply index needs explicit recovery; append skipped")
                return False
        if observed <= _covered_ids(chat_id):
            return False
        row = dict(schema="praxis.addressed-reply.v3", chat_id=str(chat_id),
                   observed_message_ids=sorted(observed),
                   reply_message_id=mid, delivery_key=str(delivery_key))
        data = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            view = memoryview(data)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError("short addressed reply receipt write")
                view = view[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
        _CACHE.pop(str(path), None)
    return True


@_best_effort(False)
def record_outbox(entry):
    """An accepted reply, including replay, is not a narration or send intent."""
    if (entry.get("state") != "accepted" or entry.get("purpose") != "tool:reply"
            or entry.get("kind") != "text"):
        return False
    from telegram_topics import TopicRoute
    route = TopicRoute(str(entry["peer_id"]), entry.get("topic_id"))
    return record(route.conversation_id, (entry.get("receipt") or {}).get("message_id"),
                  delivery_key=entry.get("key"),
                  observed_message_id=entry.get("addressed_observed_message_id"),
                  observed_message_ids=entry.get("addressed_observed_message_ids"))
