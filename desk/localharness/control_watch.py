"""Edition interrupt consumer, independent of the synchronous conversation loop.

One reader owns interrupt.processing.json. Claim by rename before reading; a new
request may be published meanwhile without being removed by this consumer.
Run birth times fence old all-scope requests from cancelling later work.
"""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)
_BOUNDARY_LOCK = threading.RLock()


@contextmanager
def delivery(runs, run_id: str):
    """Order this consumer's cancel against the edition's boundary effects.

    Already-started boundary delivery finishes before cancellation is acknowledged;
    cancellation winning first suppresses the boundary using durable run state.
    This is not a rollback of messages/files sent earlier by tools.
    """
    with _BOUNDARY_LOCK:
        if not run_id:
            yield True  # legacy envelope has no durable identity to cancel
            return
        state = runs().status(run_id)
        cancelled = state.get("status") in {"cancelled", "paused", "blocked", "in_doubt"}
        cancelled = cancelled or (state.get("control") or {}).get("action") == "cancel"
        yield not cancelled


def _instant(value: object) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("interrupt/run timestamp has no timezone")
    return parsed


def consume(tree: Path, manager, statuses) -> dict | None:
    folder = Path(tree) / "memory" / ".control"
    pending = folder / "interrupt.json"
    claimed = folder / "interrupt.processing.json"
    if not claimed.exists():
        try:
            os.replace(pending, claimed)
        except FileNotFoundError:
            return None
    raw = claimed.read_bytes()
    result = {"id": hashlib.sha256(raw).hexdigest(), "requested": [],
              "cancelled": [], "pending_tool_outcomes": [], "failed": []}
    try:
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError("interrupt request must be an object")
        result["id"] = str(request.get("id") or result["id"])
        cutoff = _instant(request.get("at"))
        scope = str(request.get("scope") or "all").strip()
        if not scope:
            raise ValueError("interrupt scope is empty")
        result["scope"] = scope
        by = str(request.get("by") or "owner")[:40]
        reason = str(request.get("reason") or "interrupted from the window")[:200]
        for row in manager.list_runs(statuses=tuple(statuses)):
            rid = str(row.get("run_id") or row.get("id") or "")
            if not rid or (scope != "all" and rid != scope):
                continue
            try:
                # Missing birth evidence is a named refusal, never authority to
                # cancel an unrelated run born after a stale request.
                # RunContext birth is millisecond precision. A birth in the same
                # millisecond is ambiguous, not evidence that this request owns it.
                born = _instant(row.get("created_at"))
                if born + dt.timedelta(milliseconds=1) > cutoff:
                    continue
                result["requested"].append(rid)
                with _BOUNDARY_LOCK:
                    receipt = manager.request_cancel(rid, actor=f"desk:{by}", reason=reason)
                key = ("cancelled" if receipt.get("status") == "cancelled"
                       else "pending_tool_outcomes")
                result[key].append(rid)
            except Exception as exc:
                result["failed"].append({"run_id": rid, "error": type(exc).__name__})
    except Exception as exc:
        result["failed"].append({"error": type(exc).__name__, "detail": str(exc)})
    result["at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    receipt_path = folder / "interrupt-receipt.json"
    temp = folder / ".tmp-interrupt-receipt.json"
    temp.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, receipt_path)
    claimed.unlink()
    log.warning("interrupt receipt: %s", result)
    return result


def start(tree: Path, manager, statuses, *, interval: float = 0.5):
    """Start once after tree ownership and agent initialization, before first turn."""
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            try:
                consume(tree, manager, statuses)
            except Exception:
                # Keep the claimed request for recovery after an I/O failure.
                log.exception("interrupt consumer failed; request retained")
            stop.wait(interval)

    thread = threading.Thread(target=watch, name="edition-control", daemon=True)
    thread.start()
    return stop, thread
