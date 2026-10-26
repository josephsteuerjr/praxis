# -*- coding: utf-8 -*-
"""Owner intent queue; only the runner calls the selected core's control API.

The channel returns queued, never stopped. A runner crash after claiming an
intent leaves an unknown receipt and does not replay a possible external effect.
No ordinary agent turn, tool admission or Forge import occurs in this module.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
from pathlib import Path
from typing import Callable

from . import forge_reader as reader

_COMMAND = re.compile(r"[0-9a-f]{32}\Z")
_ACTIONS = {"stop", "pause", "resume"}


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _root(tree: Path) -> Path:
    root = Path(tree) / "memory" / ".control" / "forge"
    return reader._inside(Path(tree).resolve(), root)


def _write(path: Path, data: dict, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(".tmp-" + path.name + "-" + secrets.token_hex(6))
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        if exclusive:
            os.link(temp, path)  # Atomic publish-if-absent, never overwrite an intent.
        else:
            os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _command_id(command_id: str) -> str:
    if not isinstance(command_id, str) or not _COMMAND.fullmatch(command_id):
        raise reader.ReadError("Expected a 32-character command identity")
    return command_id


def _record(root: Path, subdir: str, command_id: str) -> dict:
    return reader._read(reader._inside(root, root / subdir), _command_id(command_id) + ".json")


def receipt(tree: Path, command_id: str) -> dict:
    root = _root(tree)
    result = _record(root, "receipts", command_id)
    if result:
        if result.get("command_id") != command_id:
            raise reader.ReadError("Forge command receipt identity mismatch", 409)
        return result
    request = _record(root, "commands", command_id)
    processing = _record(root, "processing", command_id)
    if not request and not processing:
        raise reader.ReadError("Forge command not found", 404)
    return {"ok": True, "command_id": command_id, "state": "processing" if processing else "queued",
            "task_id": (request or processing).get("task_id"),
            "agent_id": (request or processing).get("agent_id", ""), "action": (request or processing).get("action")}


def enqueue(tree: Path, task_id: str, action: str, agent_id: str = "", expected_generation: str = "", command_id: str = "") -> dict:
    command_id = _command_id(command_id)
    if not isinstance(action, str) or action not in _ACTIONS:
        raise reader.ReadError("Unsupported Forge control action")
    if not isinstance(expected_generation, str) or len(expected_generation) > 160:
        raise reader.ReadError("Invalid Forge generation")
    root = _root(tree)
    identity = {"schema": 1, "command_id": command_id, "task_id": task_id, "agent_id": agent_id,
                "action": action, "expected_generation": expected_generation}
    for subdir in ("commands", "processing", "receipts"):
        prior = _record(root, subdir, command_id)
        if prior:
            original = prior.get("command") if subdir == "receipts" else prior
            if not isinstance(original, dict) or any(original.get(k) != v for k, v in identity.items()):
                raise reader.ReadError("Command identity is already bound to different control", 409)
            return receipt(tree, command_id)
    reader.task_dir(tree, task_id)
    if agent_id:
        folder = reader.unit_dir(tree, task_id, agent_id)
        current = str(reader._read(folder, "progress.json").get("generation") or "")
        if current != expected_generation:
            raise reader.ReadError("Participant generation changed; refresh before control", 409)
    elif expected_generation:
        raise reader.ReadError("Group control does not take a participant generation")
    value = {**identity, "created_at": _utc(), "by": "owner"}
    commands = reader._inside(root, root / "commands")
    try:
        _write(commands / (command_id + ".json"), value, exclusive=True)
    except FileExistsError:
        return enqueue(tree, task_id, action, agent_id, expected_generation, command_id)
    except OSError as exc:
        raise reader.ReadError(f"Forge command was not queued: {exc}", 503) from exc
    return {"ok": True, "command_id": command_id, "state": "queued", "task_id": task_id, "agent_id": agent_id, "action": action}


def recent(tree: Path, task_id: str, limit: int = 8) -> dict:
    """Reconstruct owner control history from actual intents/receipts on reload."""
    reader.task_dir(tree, task_id)
    root = _root(tree)
    found = {}
    for subdir in ("commands", "processing", "receipts"):
        folder = reader._inside(root, root / subdir)
        for path in folder.glob("*.json"):
            row = reader._read(folder, path.name, required=True)
            if row.get("task_id") == task_id:
                found[path.stem] = row
    ordered = sorted(found, key=lambda key: str(found[key].get("observed_at") or found[key].get("created_at") or ""), reverse=True)
    return {"commands": [receipt(tree, key) for key in ordered[:limit]]}


def consume_pending(tree: Path, callback: Callable[..., dict], limit: int = 30) -> list[dict]:
    """Runner tick hook. Never call concurrently from multiple runner owners.

    The runner's existing lifetime/lease owns that invariant. Generation is
    rechecked immediately before callback; core control rechecks under its mutex.
    Callback effects are not retried after exceptions or a claimed crash.
    """
    root = _root(tree)
    commands = reader._inside(root, root / "commands")
    processing = reader._inside(root, root / "processing")
    results = reader._inside(root, root / "receipts")
    if not commands.exists() and not processing.exists():
        return []
    out = []
    # Recovered claims may already have performed effects. Keep them unknown.
    for path in sorted(processing.glob("*.json")):
        request = reader._read(processing, path.name, required=True)
        command_id = _command_id(path.stem)
        if (results / path.name).exists():
            path.unlink()
            continue
        value = {"ok": False, "command_id": command_id, "state": "unknown", "command": request,
                 "task_id": request.get("task_id"), "agent_id": request.get("agent_id", ""),
                 "action": request.get("action"), "observed_at": _utc(),
                 "reason": "Runner restarted after claiming control; effects were not replayed"}
        _write(results / path.name, value)
        path.unlink()
        out.append(value)
    for path in sorted(commands.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)[:limit]:
        command_id = _command_id(path.stem)
        if (results / path.name).exists():
            continue
        request = reader._read(commands, path.name, required=True)
        processing.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(path, processing / path.name)
        except FileNotFoundError:
            continue
        try:
            if request.get("command_id") != command_id or request.get("action") not in _ACTIONS:
                raise reader.ReadError("Invalid Forge command identity")
            task_id, agent_id = request["task_id"], request.get("agent_id", "")
            reader.task_dir(tree, task_id)
            if agent_id:
                folder = reader.unit_dir(tree, task_id, agent_id)
                if str(reader._read(folder, "progress.json").get("generation") or "") != request.get("expected_generation", ""):
                    raise reader.ReadError("Participant generation changed before control", 409)
            observed = callback(task_id, request["action"], agent_id=agent_id,
                                expected_generation=request.get("expected_generation", ""))
            if not isinstance(observed, dict):
                raise TypeError("Forge control returned no receipt")
            value = {"ok": observed.get("ok", True), "state": "observed", "result": observed}
        except reader.ReadError as exc:
            value = {"ok": False, "state": "rejected", "reason": str(exc)}
        except Exception as exc:
            value = {"ok": False, "state": "unknown", "reason": f"Control outcome unconfirmed: {type(exc).__name__}: {exc}"}
        value.update(command_id=command_id, command=request, task_id=request.get("task_id"),
                     agent_id=request.get("agent_id", ""), action=request.get("action"), observed_at=_utc())
        _write(results / path.name, value)
        (processing / path.name).unlink()
        out.append(value)
    return out
