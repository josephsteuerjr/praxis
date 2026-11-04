"""Explicit PR10 deployment migration; never invoked by parsing a profile.

Only Markdown profiles and append-only JSONL are authoritative. No soul access.
The CLI must run against the deployment backup/release, before starting the runner.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import hashlib
import os
from pathlib import Path
import re
import threading
from typing import Callable

from self_model import FileLock, _atomic_write

MIGRATION = "pr10-authored-only-v1"
POLICY = "authored-only"
_LOCK = threading.RLock()


def _ledger(base: Path) -> Path:
    return base / "memory" / "deployments" / "compaction.jsonl"


def _sync_directory(path: Path) -> None:
    # POSIX power-loss durability for newly created ledger/quarantine names.
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        try:
            written = os.write(fd, remaining)
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError("deployment ledger write made no progress")
        remaining = remaining[written:]


def _record(line: bytes) -> dict:
    row = json.loads(line.decode("utf-8"))
    if not isinstance(row, dict):
        raise ValueError("deployment ledger record must be a JSON object")
    return row


def _rows(base: Path) -> list[dict]:
    """Read/recover under _LOCK and _lock; never discard complete history.

    A malformed newline-terminated record is corruption, even at EOF. Only an
    undecodable non-newline suffix can be an interrupted append. Quarantine it
    durably before removing it, retaining every committed byte (all migrations).
    A complete object missing only its newline is preserved and terminated.
    """
    path = _ledger(base)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    end = data.rfind(b"\n") + 1
    # Validate ALL complete records before any recovery mutation.
    rows = [_record(line) for line in data[:end].split(b"\n")[:-1]]
    tail = data[end:]
    if tail:
        try:
            row = _record(tail)
        except (json.JSONDecodeError, UnicodeDecodeError):
            digest = hashlib.sha256(tail).hexdigest()
            quarantine = path.with_name(f"{path.name}.torn-{end}-{digest}")
            if quarantine.exists():
                if quarantine.read_bytes() != tail:
                    raise ValueError("deployment ledger quarantine mismatch")
            else:
                _atomic_write(quarantine, tail)
            _sync_directory(path.parent)
            with path.open("r+b") as stream:
                stream.truncate(end)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            rows.append(row)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND)
            try:
                _write_all(fd, b"\n")
                os.fsync(fd)
            finally:
                os.close(fd)
    return [row for row in rows if row.get("migration") == MIGRATION]


def _append(base: Path, row: dict) -> None:
    """Append while holding both migration locks, after _rows has recovered EOF."""
    path = _ledger(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(row, ensure_ascii=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    _sync_directory(path.parent)


def _lock(base: Path) -> FileLock:
    return FileLock(base / "memory" / "deployments" / "compaction.lock")


def _stamp(raw: str, actor: str) -> str:
    # Parse with the canonical room grammar, preserving its prose and all headers.
    import rooms
    profile = rooms.parse_profile(raw)
    header = dict(profile["header"])
    header.update(compaction_policy=POLICY, compaction_set_by=actor)
    lines = [profile["title"] or "# Compaction policy", ""]
    lines.extend(f"{key}: {value}" for key, value in header.items())
    return "\n".join(lines) + "\n\n" + profile["body"] + "\n"


def _is_room_profile(path: Path) -> bool:
    # Use the persisted topic-route grammar, not an independent suffix parser.
    from telegram_topics import route_from_conversation_id
    route = route_from_conversation_id(path.stem)
    return (re.fullmatch(r"-?\d+", route.peer_id) is not None
            and route.conversation_id == path.stem)


def apply(base: Path, *, sha: str, reason: str) -> dict:
    """Idempotent across restarts; an interrupted plan is resumed, not re-attributed.

    Full release SHA required. A single global transition, not a new receipt per restart.
    Caller must quiesce profile writers while deploying (normal release procedure).
    """
    base = Path(base)
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not reason.strip() or "\n" in reason:
        raise ValueError("full release SHA and single-line reason required")
    with _LOCK, _lock(base):
        rows = _rows(base)
        completed = next((r for r in rows if r["kind"] == "applied"), None)
        if completed:
            return completed
        plan = next((r for r in rows if r["kind"] == "planned"), None)
        if plan is None:
            profiles = ["memory/policies/compaction.md"]
            profiles += [p.relative_to(base).as_posix() for p in
                         sorted((base / "memory" / "rooms").glob("*.md"))
                         if _is_room_profile(p)]
            plan = dict(kind="planned", migration=MIGRATION, actor=f"deploy:{sha}",
                        reason=reason.strip(), profiles=profiles,
                        at=dt.datetime.now(dt.timezone.utc).isoformat())
            _append(base, plan)
        for relative in plan["profiles"]:
            path = base / relative
            raw = path.read_text(encoding="utf-8") if path.exists() else (
                "# Global compaction policy\n\n"
                "Applies to DMs, groups (including future rooms), and background work.\n"
                "Helper compaction is removed. Pressure offers work; only authored\n"
                "memory_compact prepare → fold(selection, text) writes compacts.\n"
                if relative == "memory/policies/compaction.md" else "")
            text = _stamp(raw, plan["actor"])
            if raw != text:
                _atomic_write(path, text.encode())
        receipt = dict(plan, kind="applied", explanation=(
            "PR10: helper compaction removed globally (DMs, groups, background). "
            "Only your authored memory_compact prepare → fold(selection, text) changes "
            "compacts; pressure offers work, never runs a helper. Existing compacts and "
            "raw archives remain intact. Reason: " + plan["reason"] + ". "
            "Rollback: stop the runner; restore the pre-deploy release and the affected "
            "profiles from its deployment backup; retain this append-only deployment "
            "ledger as history. Changing a policy label alone does NOT restore helpers. "
            "No soul files changed; schema budget remains 32000."))
        _append(base, receipt)
        return receipt


def context_once(base: Path, *, render: Callable[[str], str] | None = None) -> str:
    """Construct a context, then durably acknowledge its one-time insertion.

    ``render`` runs under the shared lock, before acknowledgement. If construction
    fails, no receipt is consumed. Call only for intended model input, not probes.
    This is NOT model/Telegram delivery: a later failure/crash can still lose the
    constructed input. The explanation remains canonical in the applied ledger.
    """
    base = Path(base)
    render = render or (lambda text: text)
    with _LOCK, _lock(base):
        rows = _rows(base)
        if any(r["kind"] == "context_shown" for r in rows):
            return render("")
        receipt = next((r for r in rows if r["kind"] == "applied"), None)
        if receipt is None:
            return render("")
        text = f"[{receipt['actor']}; {MIGRATION}] {receipt['explanation']}"
        context = render(text)
        _append(base, dict(kind="context_shown", migration=MIGRATION,
                           acknowledgement="context_constructed_not_delivered",
                           actor=receipt["actor"],
                           at=dt.datetime.now(dt.timezone.utc).isoformat()))
        return context


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    receipt = apply(args.base, sha=args.sha, reason=args.reason)
    print(f"{receipt['migration']}: {receipt['actor']} applied; runtime context pending/claimed in ledger")


if __name__ == "__main__":
    main()
