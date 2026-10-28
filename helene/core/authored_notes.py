"""Explicit authored scratch and notes for Praxis.

This append-only ledger stores only entries Praxis deliberately writes through the
manage_notes tool. It is not indexed into ordinary memory and does not create a
task, loop, desire, journal entry, or self observation.
"""

from __future__ import annotations

import datetime as dt
import contextlib
import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Callable

from self_model import FileLock, utc_now


SCHEMA = "praxis.authored_note.event.v1"
KINDS = frozenset({"scratch", "note", "reflection", "question"})
SCOPES = frozenset({"global", "chat", "run"})
STATUSES = frozenset({"open", "closed"})
_LOCAL_LOCK = threading.RLock()
_NOTE_ID_RE = re.compile(r"^note-[A-Za-z0-9][A-Za-z0-9._:-]{0,91}$")
_WORK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


def work_reference(value: Any) -> str:
    """Validate an explicit stable identity, never a chat's 'latest work'."""
    value = str(value or "").strip()
    if not value:
        return ""
    parts = value.split(":")
    if (parts[0] not in {"forge", "run", "task"}
            or len(parts) not in ({2, 3} if parts[0] == "forge" else {2})
            or not all(_WORK_ID_RE.fullmatch(part) for part in parts[1:])):
        raise ValueError("work_ref requires forge:<task>[:<unit>], run:<run> or task:<task>")
    return value


def note_audience(value: Any = "", *, chat_id: str = "") -> str:
    """Legacy/missing means owner-private, not public/global permission."""
    value = str(value or "owner").strip()
    if value == "owner":
        return value
    if value.startswith("chat:") and chat_id and value == f"chat:{chat_id}":
        return value
    raise ValueError("audience must be owner or chat:<this exact chat_id>")


def visible(row: dict[str, Any], *, owner: bool = False, chat_id: str = "",
            audience: str = "") -> bool:
    """Admission at the consumer, including after restart or audience revocation.

    An explicit paired/public/unknown audience never inherits owner permission.
    The caller supplies current authority; ledger scope/global is not a grant.
    """
    target = str(row.get("audience") or "owner")
    if audience and audience not in {"owner", f"chat:{chat_id}"}:
        return False
    if target == "owner":
        return bool(owner and audience in {"", "owner"})
    if not target.startswith("chat:") or not str(row.get("chat_id") or ""):
        return False
    if target != f"chat:{row['chat_id']}":
        return False
    return bool((owner and audience in {"", "owner"}) or (
        chat_id and target == f"chat:{chat_id}" and audience in {"", target}))


def notice_work_reference(notice: dict[str, Any]) -> str:
    """Resolve producer identities and reject contradictory explicit links."""
    task = str(notice.get("task_id") or "")
    unit = str(notice.get("unit_id") or notice.get("agent_id") or "")
    run = str(notice.get("run_id") or "")
    explicit = work_reference(notice.get("work_ref"))
    if explicit:
        parts = explicit.split(":")
        if parts[0] in {"forge", "task"} and task and parts[1] != task:
            return ""
        if parts[0] == "forge" and len(parts) == 3 and unit and parts[2] != unit:
            return ""
        if parts[0] == "run" and run and parts[1] != run:
            return ""
        return explicit
    if task and unit:
        return work_reference(f"forge:{task}:{unit}")
    if task:
        return work_reference(f"forge:{task}")
    return work_reference(f"run:{run}") if run else ""


def for_notice(base: str | Path, notice: dict[str, Any], *, owner: bool = False,
               chat_id: str = "", audience: str = "") -> str:
    """Only an open authored note for this exact work AND original audience.

    Notice storage contains identities, not copied note prose. Closing a note or
    entering another audience therefore takes effect without rewriting history.
    Technical result/log summaries are never substituted for missing authorship.
    """
    if not visible(notice, owner=owner, chat_id=chat_id, audience=audience):
        return ""
    try:
        reference = notice_work_reference(notice)
    except ValueError:
        return ""
    if not reference:
        return ""
    target = str(notice.get("audience") or "owner")
    for row in reversed(list(AuthoredNoteLedger(base).states().values())):
        if row.get("work_ref") != reference or str(row.get("audience") or "owner") != target:
            continue
        # A closed newest note is an explicit withdrawal, not permission to
        # resurrect an earlier convenient note about the same work.
        if row.get("status") != "open" or not visible(
                row, owner=owner, chat_id=chat_id, audience=audience):
            return ""
        text = str(row.get("text") or "")
        return f"Authored work note `{row['id']}` (separate from the completion fact):\n{text}" if text else ""
    return ""


def _new_id() -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return f"note-{now:%Y%m%dT%H%M%S}-{secrets.token_hex(5)}"


def _text(value: Any, *, limit: int, required: str = "") -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"{required} is required")
    return result[:limit]


def _note_id(value: Any) -> str:
    result = _text(value, limit=100, required="note_id")
    if not _NOTE_ID_RE.fullmatch(result):
        raise ValueError("note_id must match note-[A-Za-z0-9._:-]+")
    return result


class AuthoredNoteLedger:
    def __init__(self, base: str | Path | None = None):
        self.base = Path(base or os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
        self.events_path = self.base / "memory" / "notes" / "events.jsonl"
        self.lock_path = self.base / "memory" / ".state" / "authored-notes.lock"

    def events(self) -> list[dict[str, Any]]:
        try:
            lines = self.events_path.read_bytes().splitlines()
        except OSError:
            return []
        events: list[dict[str, Any]] = []
        for raw_line in lines:
            if not raw_line.strip():
                continue
            try:
                line = raw_line.decode("utf-8", errors="strict")
                event = json.loads(line)
            except (TypeError, ValueError, UnicodeError):
                continue
            if isinstance(event, dict) and event.get("schema") == SCHEMA:
                events.append(event)
        return events

    def states(self) -> dict[str, dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        for event in self.events():
            note_id = str(event.get("note_id") or "")
            if not _NOTE_ID_RE.fullmatch(note_id):
                continue
            event_type = str(event.get("event_type") or "")
            if event_type == "written":
                states[note_id] = {
                    "id": note_id,
                    "created_at": str(event.get("ts") or ""),
                    "updated_at": str(event.get("ts") or ""),
                    "kind": str(event.get("kind") or "note"),
                    "scope": str(event.get("scope") or "global"),
                    "status": "open",
                    "text": str(event.get("text") or ""),
                    "run_id": str(event.get("run_id") or ""),
                    "chat_id": str(event.get("chat_id") or ""),
                    "message_id": event.get("message_id"),
                    "source_ref": str(event.get("source_ref") or ""),
                    "work_ref": str(event.get("work_ref") or ""),
                    "audience": str(event.get("audience") or "owner"),
                    "closed_reason": "",
                }
            elif event_type == "closed" and note_id in states:
                states[note_id]["status"] = "closed"
                states[note_id]["updated_at"] = str(event.get("ts") or "")
                states[note_id]["closed_reason"] = str(event.get("reason") or "")
        return states

    def get(self, note_id: str) -> dict[str, Any] | None:
        return self.states().get(_note_id(note_id))

    def list(self, *, status: str = "open", kind: str = "", scope: str = "",
             run_id: str = "", chat_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        if status and status not in STATUSES:
            raise ValueError("status must be open or closed")
        if kind and kind not in KINDS:
            raise ValueError("unknown note kind")
        if scope and scope not in SCOPES:
            raise ValueError("unknown note scope")
        values = list(self.states().values())
        values = [row for row in values if not status or row["status"] == status]
        values = [row for row in values if not kind or row["kind"] == kind]
        values = [row for row in values if not scope or row["scope"] == scope]
        values = [row for row in values if not run_id or row["run_id"] == run_id]
        values = [row for row in values if not chat_id or row["chat_id"] == chat_id]
        values.sort(key=lambda row: (row["updated_at"], row["id"]), reverse=True)
        return values[:max(1, min(100, int(limit)))]

    def write(self, text: str, *, kind: str = "note", scope: str = "global",
              run_id: str = "", chat_id: str = "", message_id: int | None = None,
              source_ref: str = "", work_ref: str = "", audience: str = "owner",
              validate_locked: Callable[[], None] | None = None) -> dict[str, Any]:
        kind = _text(kind, limit=20) or "note"
        scope = _text(scope, limit=20) or "global"
        if kind not in KINDS:
            raise ValueError("unknown note kind")
        if scope not in SCOPES:
            raise ValueError("unknown note scope")
        run_id = _text(run_id, limit=120)
        chat_id = _text(chat_id, limit=120)
        work_ref = work_reference(work_ref)
        audience = note_audience(audience, chat_id=chat_id)
        if scope == "run" and not run_id:
            raise ValueError("run scope requires an active run")
        if scope == "chat" and not chat_id:
            raise ValueError("chat scope requires an active chat")
        event = {
            "schema": SCHEMA,
            "event_id": f"note-event-{secrets.token_hex(12)}",
            "event_type": "written",
            "note_id": _new_id(),
            "ts": utc_now(),
            "kind": kind,
            "scope": scope,
            "text": _text(text, limit=12000, required="text"),
            "run_id": run_id,
            "chat_id": chat_id,
            "message_id": int(message_id) if message_id is not None else None,
            "source_ref": _text(source_ref, limit=300),
            "work_ref": work_ref,
            "audience": audience,
        }
        with self.locked():
            if validate_locked is not None:
                validate_locked()
            self._append_unlocked(event)
        return self.get(str(event["note_id"])) or {}

    def close(self, note_id: str, *, reason: str = "") -> dict[str, Any]:
        note_id = _note_id(note_id)
        current = self.get(note_id)
        if current is None:
            raise ValueError("note not found")
        if current["status"] == "closed":
            return current
        self._append({
            "schema": SCHEMA,
            "event_id": f"note-event-{secrets.token_hex(12)}",
            "event_type": "closed",
            "note_id": note_id,
            "ts": utc_now(),
            "reason": _text(reason, limit=1000),
        })
        return self.get(note_id) or {}

    def metadata_for_run(self, run_id: str, *, limit: int = 40) -> list[dict[str, Any]]:
        if not run_id:
            return []
        rows = self.list(status="", run_id=run_id, limit=limit)
        return [{
            "id": row["id"],
            "kind": row["kind"],
            "scope": row["scope"],
            "created_at": row["created_at"],
        } for row in rows]

    @contextlib.contextmanager
    def locked(self):
        with _LOCAL_LOCK, FileLock(self.lock_path):
            yield

    def append_locked(self, event: dict[str, Any]) -> None:
        self._append_unlocked(event)

    def _append(self, event: dict[str, Any]) -> None:
        with self.locked():
            self._append_unlocked(event)

    def _append_unlocked(self, event: dict[str, Any]) -> None:
        payload = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
