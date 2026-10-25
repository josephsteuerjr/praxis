"""Fail-closed, single-peer Telegram moderation ledger."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
import time

TARGET_PEER_ID = -1001240718803
ACTIONS = {"delete", "delete_and_ban"}
BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
LEDGER = BASE / "memory" / ".state" / "telegram_moderation.jsonl"
_LOCK = threading.Lock()


def operation_key(peer_id: int, message_id: int, sender_id: int, action: str) -> str:
    raw = f"{peer_id}:{message_id}:{sender_id}:{action}".encode()
    return hashlib.sha256(raw).hexdigest()


def validate(peer_id: int, message_id: int, sender_id: int, action: str) -> str:
    if any(isinstance(value, bool) or not isinstance(value, int)
           for value in (peer_id, message_id, sender_id)):
        raise TypeError("peer_id, message_id and sender_id must be exact JSON integers")
    if not isinstance(action, str):
        raise TypeError("action must be an exact string")
    action = action.strip()
    if peer_id != TARGET_PEER_ID:
        raise PermissionError("moderation actuator is scoped only to AbstractDL")
    if message_id <= 0 or sender_id <= 0:
        raise ValueError("positive message_id and sender_id are required")
    if action not in ACTIONS:
        raise ValueError("action must be delete or delete_and_ban")
    return operation_key(peer_id, message_id, sender_id, action)


def _canonical(row: dict) -> bytes:
    return json.dumps(row, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _read_verified() -> list[dict]:
    rows: list[dict] = []
    previous = ""
    try:
        lines = LEDGER.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise RuntimeError(f"moderation ledger unreadable: {exc}") from exc
    for index, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise RuntimeError(f"moderation ledger corrupt at line {index}") from exc
        if not isinstance(row, dict) or row.get("schema") != "praxis.telegram.moderation.v1":
            raise RuntimeError(f"moderation ledger invalid schema at line {index}")
        digest = str(row.get("receipt_sha256") or "")
        unsigned = dict(row)
        unsigned.pop("receipt_sha256", None)
        expected = hashlib.sha256(_canonical(unsigned)).hexdigest()
        if digest != expected or str(row.get("previous_sha256") or "") != previous:
            raise RuntimeError(f"moderation ledger hash-chain mismatch at line {index}")
        required = {"idempotency_key", "actor", "peer_id", "message_id", "sender_id",
                    "action", "status", "deleted", "banned", "sender_verified", "error"}
        if not required.issubset(row):
            raise RuntimeError(f"moderation ledger missing fields at line {index}")
        if operation_key(int(row["peer_id"]), int(row["message_id"]),
                         int(row["sender_id"]), str(row["action"])) != row["idempotency_key"]:
            raise RuntimeError(f"moderation ledger key mismatch at line {index}")
        rows.append(row)
        previous = digest
    return rows


def latest(key: str) -> dict | None:
    with _LOCK:
        for row in reversed(_read_verified()):
            if row["idempotency_key"] == key:
                return row
    return None


def prior(key: str) -> dict | None:
    row = latest(key)
    return row if row and row["status"] == "completed" else None


def history_for_sender(peer_id: int, sender_id: int) -> list[dict]:
    """Завершённые меры к этому отправителю в этом месте — по ПРОВЕРЕННОЙ цепи.

    27.08. Заведено ради пробуждения на спам: раньше туда ехали одни идентификаторы,
    и повторность приходилось выяснять руками уже внутри хода. Пока история не
    лежала в руках, первое срабатывание выглядело как одиночное — и бан наступал
    только с третьего захода того же человека.

    Читается тем же `_read_verified`, что и всё остальное: если цепь расписок
    порвана, эта функция обязана падать вместе с остальными, а не отдавать
    правдоподобный огрызок.
    """
    with _LOCK:
        rows = _read_verified()
    out: list[dict] = []
    for row in rows:
        if str(row.get("status")) != "completed":
            continue
        try:
            same = (int(row.get("peer_id")) == int(peer_id)
                    and int(row.get("sender_id")) == int(sender_id))
        except (TypeError, ValueError):
            continue
        if same:
            out.append({"ts": row.get("ts"), "message_id": row.get("message_id"),
                        "action": row.get("action"),
                        "deleted": bool(row.get("deleted")),
                        "banned": bool(row.get("banned"))})
    return out


def append_receipt(payload: dict) -> dict:
    with _LOCK:
        rows = _read_verified()
        previous = str(rows[-1]["receipt_sha256"]) if rows else ""
        row = {"schema": "praxis.telegram.moderation.v1", "ts": time.time(),
               "previous_sha256": previous, **payload}
        row["receipt_sha256"] = hashlib.sha256(_canonical(row)).hexdigest()
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(LEDGER), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (json.dumps(row, ensure_ascii=False,
                                     separators=(",", ":")) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return row
