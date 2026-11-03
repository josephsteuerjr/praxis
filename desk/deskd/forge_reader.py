# -*- coding: utf-8 -*-
"""Owner-only Forge observations. No core import, locks, process probes or writes.

Files remain owned by Forge. Missing optional progress is not a dead worker;
malformed/unreadable records are errors, not an empty team. Content is addressed
by existing task/unit identity and a SHA, never a caller-supplied path.
"""
from __future__ import annotations

import datetime as dt
import codecs
import base64
import hashlib
import json
import re
from pathlib import Path

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_RECORDS = {"result": "result.json", "request": "request.json"}
_MAX_RECORD = 16 * 1024 * 1024
_RESULT_CACHE: dict[str, tuple[tuple, dict, dict]] = {}
_TERMINAL = frozenset({'done', 'completed', 'cancelled', 'canceled', 'stopped', 'failed', 'error', 'lost', 'abandoned', 'closed'})


class ReadError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def identity(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value) or value in {".", ".."}:
        raise ReadError("Invalid Forge identity")
    return value


def _inside(base: Path, path: Path) -> Path:
    base = base.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(base) or path.is_symlink():
        raise ReadError("Forge record escapes its identity directory", 403)
    return resolved


def task_dir(tree: Path, task_id: str) -> Path:
    root = Path(tree) / "memory" / ".forge" / "tasks"
    # Disallow a redirected tasks root too (tree itself may intentionally be relocated).
    _inside(Path(tree).resolve(), root)
    folder = _inside(root, root / identity(task_id))
    if not folder.is_dir():
        raise ReadError("Forge task not found", 404)
    return folder


def unit_dir(tree: Path, task_id: str, unit_id: str) -> Path:
    task = task_dir(tree, task_id)
    agents = _inside(task, task / "agents")
    folder = _inside(agents, agents / identity(unit_id))
    if not folder.is_dir():
        raise ReadError("Forge participant not found", 404)
    request = _read(folder, "request.json", required=True)
    if request.get("id") not in (None, unit_id) or request.get("task_id") not in (None, task_id):
        raise ReadError("Forge participant identity does not match its request", 409)
    return folder


def _bytes(folder: Path, name: str, required: bool = False) -> bytes | None:
    path = _inside(folder, folder / name)
    try:
        if path.stat().st_size > _MAX_RECORD:
            raise ReadError("Forge record exceeds the supported read size", 413)
        return path.read_bytes()
    except FileNotFoundError:
        if required:
            raise ReadError("Forge record not found", 404)
        return None
    except OSError as exc:
        raise ReadError(f"Forge record unreadable: {exc}", 503) from exc


def _read(folder: Path, name: str, required: bool = False) -> dict:
    data = _bytes(folder, name, required)
    if data is None:
        return {}
    try:
        row = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise ReadError(f"Invalid Forge {name}", 503) from exc
    if not isinstance(row, dict):
        raise ReadError(f"Invalid Forge {name} object", 503)
    return row


def _ref(folder: Path, kind: str) -> dict | None:
    data = _bytes(folder, _RECORDS[kind])
    if data is None:
        return None
    return {"kind": kind, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def _result(folder: Path) -> tuple[dict, dict | None]:
    """Terminal outputs can be large; poll only their small cached projection."""
    path = _inside(folder, folder / "result.json")
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        prior = _RESULT_CACHE.get(str(path))
        if prior and prior[0] == stamp:
            return prior[1], prior[2]
        data = path.read_bytes()
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("expected an object")
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError, UnicodeError) as exc:
        raise ReadError(f"Forge result unreadable: {exc}", 503) from exc
    small = {k: value[k] for k in ("status", "role", "model", "finished", "diff_ref", "files_ref", "checks_ref", "review_ref") if k in value}
    ref = {"kind": "result", "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
    if len(_RESULT_CACHE) >= 1024:
        _RESULT_CACHE.pop(next(iter(_RESULT_CACHE)), None)
    _RESULT_CACHE[str(path)] = (stamp, small, ref)
    return small, ref


def _payload_refs(result: dict) -> dict:
    out = {}
    for name in ("diff_ref", "files_ref", "checks_ref", "review_ref"):
        ref = result.get(name)
        if isinstance(ref, dict) and isinstance(ref.get("sha256"), str) and _SHA.fullmatch(ref["sha256"]):
            out[name] = {"kind": "payload", "sha256": ref["sha256"], "size_bytes": ref.get("size_bytes")}
    return out


def _unit(folder: Path, task_id: str) -> dict:
    request = _read(folder, "request.json", required=True)
    if request.get("id") not in (None, folder.name) or request.get("task_id") not in (None, task_id):
        raise ReadError("Forge participant request identity mismatch", 409)
    result, ref = _result(folder)
    progress = _read(folder, "progress.json")
    stop = _read(folder, "stop.json")
    unresolved = stop.get("state") in {"pending", "unresolved"}
    receipts, stale = [], 0
    for path in sorted(folder.glob("disposition-*.json")):
        row = _read(folder, path.name)
        if (ref and row.get("result_sha256") == ref["sha256"]
                and row.get("child_unit_id") == folder.name
                and str(row.get("parent_unit_id") or "") == str(request.get("parent_unit_id") or "")
                and row.get("disposition") in {"integrated", "irrelevant", "deferred"}):
            receipts.append(row)
        else:
            stale += 1
    # New revisions retain previous receipts; chronological revision wins.
    receipts.sort(key=lambda r: (int(r.get("revision") or 0), str(r.get("at") or r.get("created") or "")))
    current = receipts[-1].get("disposition") if receipts else None
    observation = {k: progress[k] for k in ("generation", "phase", "iteration", "model", "tool", "call_id", "observed_at", "seq", "status") if k in progress}
    # This record is published by Forge's process owner. A phase/timestamp or
    # terminal result alone is not a fresh probe of the worker's OS identity.
    live = _read(folder, 'liveness.json')
    live = live if (live.get('schema') == 1 and live.get('unit_id') == folder.name
                    and live.get('task_id') == task_id
                    and live.get('supervisor_pid') == request.get('supervisor_pid')
                    and live.get('process_identity') == request.get('process_identity')) else {}
    liveness = {k: live[k] for k in ('state', 'observed_at', 'basis') if k in live}
    if liveness.get('state') not in {'alive', 'exited', 'unknown', 'not_started'}:
        liveness = {'state': 'unknown', 'basis': 'no_owner_observation'}
    try:
        observed=dt.datetime.fromisoformat(str(liveness.get('observed_at')).replace('Z','+00:00'))
        fresh=-5<=(dt.datetime.now(dt.timezone.utc)-observed).total_seconds()<15
    except (ValueError,TypeError):fresh=False
    effective = ('stop_pending' if unresolved else str(result.get('status') or 'unknown') if result else
                 'paused' if request.get('status') == 'paused' else
                 'waiting' if progress.get('phase') == 'waiting' else
                 'queued' if request.get('status') == 'queued' else
                 'running' if fresh and liveness.get('state')=='alive' and request.get('status') in ('starting','running','finishing') else 'unknown')
    process=request.get('process_identity') or {}
    return {
        "id": folder.name, "parent_unit_id": str(request.get("parent_unit_id") or ""),
        "role": request.get("role") or result.get("role"),
        "model": progress.get("model") or request.get("model") or result.get("model"),
        "subtask": request.get("brief") or request.get("goal") or "",
        "status": effective,
        "request_status": str(request.get("status") or ""),
        "custody": "unresolved_stop" if unresolved else "result" if result else "unknown",
        "created": request.get("created"), "finished": result.get("finished"),
        "started": process.get('birth') if isinstance(process,dict) and process.get('adapter')=='psutil-v1' else None,
        "observation": observation, "process_liveness": liveness['state'], "liveness": liveness,
        "result_ref": ref, "request_ref": _ref(folder, "request"),
        "dispositions": [{k: r.get(k) for k in ("parent_unit_id", "disposition", "result_sha256", "revision")} for r in receipts],
        "current_disposition": current, "stale_dispositions": stale,
        "payload_refs": _payload_refs(result),
        "stop": {k: stop[k] for k in ("state", "at", "finished") if k in stop},
    }


def _ancestry(agents: list[dict]) -> list[dict]:
    by_id = {r["id"]: r for r in agents}
    def order(row):
        seen, parent, depth = {row["id"]}, row["parent_unit_id"], 0
        while parent:
            if parent in seen or parent not in by_id:
                row["ancestry_unknown"] = True
                break
            seen.add(parent)
            depth += 1
            parent = by_id[parent]["parent_unit_id"]
        row["depth"] = depth
        return depth, str(row.get("created") or ""), row["id"]
    return sorted(agents, key=order)


def task_detail(tree: Path, task_id: str) -> dict:
    folder = task_dir(tree, task_id)
    task = _read(folder, "task.json")
    agents_path = _inside(folder, folder / "agents")
    try:
        folders = sorted(p for p in agents_path.iterdir() if p.is_dir()) if agents_path.exists() else []
        agents = [_unit(unit_dir(tree, task_id, p.name), task_id) for p in folders]
    except OSError as exc:
        raise ReadError(f"Forge participants unreadable: {exc}", 503) from exc
    return {"id": task_id, "goal": task.get("goal") or "", "status": task.get("status") or "unknown",
            'kind':'review' if task.get('self_review') else 'source_update' if task.get('source_update') else 'work',
            "created": task.get('created') or '',
            "finished": max([str(task.get('finished') or '')] + [str(unit.get('finished') or '') for unit in agents]),
            "priority": task.get("priority"), "control": _read(folder, "control.json"),
            "max_active_workers": task.get("max_active_workers"),
            "agents": _ancestry(agents), "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "process_liveness": "not_probed"}


def tasks(tree: Path, limit: int = 30) -> list[dict]:
    root = Path(tree) / "memory" / ".forge" / "tasks"
    _inside(Path(tree).resolve(), root)
    try:
        folders = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ReadError(f"Forge tasks unreadable: {exc}", 503) from exc
    return [task_detail(tree, p.name) for p in folders[:min(max(int(limit), 1), 100)]]


def activity(tree: Path, limit: int = 20, cursor: str = '') -> dict:
    """All unfinished participants, plus keyset-paged terminal teams.

    Discovery is not cropped by the recent-task page. In particular a terminal
    parent does not hide an unfinished child. Unknown/missing outcomes stay in
    the active set; neither old mtime nor a missing heartbeat buries a worker.
    """
    root = Path(tree) / 'memory' / '.forge' / 'tasks'
    _inside(Path(tree).resolve(), root)
    after = None
    if cursor:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor.encode('ascii')).decode('utf-8'))
            if not (isinstance(value, list) and len(value) == 2 and all(isinstance(x, str) for x in value)):
                raise ValueError('invalid key')
            after = tuple(value)
        except (ValueError, UnicodeError, TypeError) as exc:
            raise ReadError('Invalid Forge activity cursor') from exc
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir())
    except FileNotFoundError:
        return {'active': [], 'recent': [], 'next_cursor': None}
    except OSError as exc:
        raise ReadError(f'Forge tasks unreadable: {exc}', 503) from exc
    active, recent = [], []
    for folder in folders:
        row = task_detail(tree, folder.name)
        if not row['agents']:
            continue
        if any(unit['status'] not in _TERMINAL for unit in row['agents']):
            active.append(row)
        else:
            recent.append(row)
    key = lambda row: (str(row.get('finished') or row.get('created') or ''), row['id'])
    active.sort(key=lambda row: (str(row.get('created') or ''), row['id']), reverse=True)
    recent.sort(key=key, reverse=True)
    if after is not None:
        recent = [row for row in recent if key(row) < after]
    count = min(max(int(limit), 1), 100)
    page = recent[:count]
    next_cursor = (base64.urlsafe_b64encode(json.dumps(key(page[-1]), ensure_ascii=False).encode('utf-8')).decode('ascii')
                   if len(recent) > count else None)
    return {'active': active, 'recent': page, 'next_cursor': next_cursor}


def payload(tree: Path, task_id: str, unit_id: str, sha256: str, kind: str = "payload", offset: int = 0, limit: int = 8000) -> dict:
    folder = unit_dir(tree, task_id, unit_id)
    if not isinstance(sha256, str) or not _SHA.fullmatch(sha256):
        raise ReadError("Expected an exact payload SHA-256")
    if kind == "payload":
        path = f"payloads/{sha256}.txt"
    elif kind in _RECORDS:
        path = _RECORDS[kind]
    else:
        raise ReadError("Unsupported Forge content kind")
    offset, limit = int(offset), min(max(int(limit), 1), 8000)
    if offset < 0:
        raise ReadError("Forge page offset outside content")
    digest, size, total, pieces = hashlib.sha256(), 0, 0, []
    decoder = codecs.getincrementaldecoder("utf-8")()
    # Verify the whole source but retain only this Unicode page in memory.
    try:
        with _inside(folder, folder / path).open("rb") as stream:
            while True:
                block = stream.read(128 * 1024)
                digest.update(block)
                size += len(block)
                text = decoder.decode(block, final=not block)
                if total < offset + limit and total + len(text) > offset:
                    pieces.append(text[max(0, offset-total):max(0, min(len(text), offset+limit-total))])
                total += len(text)
                if not block:
                    break
    except FileNotFoundError as exc:
        raise ReadError("Forge content not found", 404) from exc
    except OSError as exc:
        raise ReadError(f"Forge content unreadable: {exc}", 503) from exc
    except UnicodeError as exc:
        raise ReadError("Forge content is not UTF-8", 503) from exc
    if digest.hexdigest() != sha256:
        raise ReadError("Forge content changed; refresh its reference before reading", 409)
    if offset > total:
        raise ReadError("Forge page offset outside content")
    end = min(offset + limit, total)
    return {"sha256": sha256, "size_bytes": size, "total_chars": total,
            "offset": offset, "text": "".join(pieces), "next_offset": end if end < total else None}


def events(tree: Path, task_id: str, unit_id: str, offset: int = 0, limit: int = 50, generation: str = "", after_seq: int = 0) -> dict:
    folder = unit_dir(tree, task_id, unit_id)
    progress = _read(folder, "progress.json")
    current = str(progress.get("generation") or "")
    if generation and generation != current:
        raise ReadError("Forge generation changed; reload participant history", 409)
    offset, limit, after_seq = int(offset), min(max(int(limit), 1), 100), int(after_seq)
    if offset < 0 or after_seq < 0 or (offset == 0 and after_seq != 0):
        raise ReadError("Invalid Forge event offset")
    path = _inside(folder, folder / "events.jsonl")
    rows, next_offset, has_more, pending_tail = [], offset, False, False
    try:
        with path.open("rb") as fh:
            if offset > path.stat().st_size:
                raise ReadError("Forge event cursor is no longer available", 409)
            if offset:
                fh.seek(offset - 1)
                if fh.read(1) != b"\n":
                    raise ReadError("Forge event offset is not a record boundary")
            fh.seek(offset)
            while len(rows) < limit:
                start = fh.tell()
                line = fh.readline(256 * 1024 + 1)
                if not line:
                    break
                if len(line) > 256 * 1024:
                    raise ReadError("Forge event record exceeds page limit", 413)
                if not line.endswith(b"\n"):
                    # A currently appended record is not malformed historical data.
                    pending_tail = True
                    fh.seek(start)
                    break
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError) as exc:
                    raise ReadError("Forge event journal is invalid", 503) from exc
                if not isinstance(row, dict) or not isinstance(row.get("seq"), int):
                    raise ReadError("Forge event journal identity is invalid", 503)
                if row["seq"] != after_seq + len(rows) + 1:
                    raise ReadError("Forge event journal sequence changed", 409)
                rows.append(row)
                next_offset = fh.tell()
            has_more = not pending_tail and bool(fh.read(1))
    except FileNotFoundError:
        if offset:
            raise ReadError("Forge event journal no longer available", 409)
    except OSError as exc:
        raise ReadError(f"Forge event journal unreadable: {exc}", 503) from exc
    return {"events": rows, "next_offset": next_offset, "next_after_seq": after_seq + len(rows),
            "has_more": has_more, "pending_tail": pending_tail, "generation": current}
