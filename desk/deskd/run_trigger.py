"""Trigger provenance shared by the runner and standalone channel."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

_ID = re.compile(r"^run-(\d{4})(\d{2})\d{2}T\d{6,}Z?-[0-9a-f]{8}$")
_NAME = "helene-trigger.json"
_SCHEMA = "helene.run-trigger.v1"


def wake_source(path: Path, manifest: dict) -> dict:
    try:
        row = json.loads((path / _NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    ctx = manifest.get("context") or {}
    if not isinstance(row, dict) or row.get("schema") != _SCHEMA \
            or row.get("run_id") != path.name or row.get("kind") != "wake" \
            or ctx.get("kind") != "chat_turn" \
            or str(row.get("chat_id") or "") != str(ctx.get("origin_chat_id") or "") \
            or not str(row.get("source_id") or "").startswith("alarm-"):
        return {}
    return row


def record_wake(tree: Path, run_id: str, chat_id: str, source_id: str) -> None:
    match = _ID.fullmatch(run_id)
    if not match or not source_id.startswith("alarm-"):
        raise ValueError("invalid scheduled trigger identity")
    path = Path(tree) / "memory/runs" / (match[1] + "-" + match[2]) / run_id
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    ctx = manifest.get("context") or {}
    if ctx.get("kind") != "chat_turn" or str(ctx.get("origin_chat_id") or "") != str(chat_id):
        raise ValueError("scheduled trigger does not belong to this run")
    row = {"schema": _SCHEMA, "run_id": run_id, "chat_id": str(chat_id),
           "kind": "wake", "source_id": source_id}
    target = path / _NAME
    if target.exists():
        if wake_source(path, manifest) != row:
            raise ValueError("scheduled trigger is already bound to another source")
        return
    tmp = path / (_NAME + ".tmp")
    tmp.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, target)
