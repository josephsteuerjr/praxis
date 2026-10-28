# -*- coding: utf-8 -*-
"""Комнаты окна: реестр `memory/.state/rooms.json` и архивы `memory/groups/<ключ>.jsonl`.

Задача A §3 (07.09): без Telegram окно — основной канал общения с агентом, и
одной комнаты мало. Комната = отдельная переписка и отдельный контекст хода
(горячий слой памяти дерева ведётся по chat_id); память жизни агента —
дневник, события, желания — одна на всех, она в комнаты не делится.

Ключ комнаты = имя потока = имя файла архива: `window` (комната по умолчанию,
зовётся именем агента, не удаляется) и `window-<8 hex>` для новых. Двоеточий
и пробелов в ключе нет. Значения — те же, что в `ui-kit/contract.json`
(сверка: tests/t_contract.py).

Модуль ЧИСТЫЙ (json, pathlib, secrets): его читают и канал (`deskapp`,
`deskd.readers`), и харнесс (`localharness/transport.py`) — в обе стороны
без импорта чужих пакетов. Записи атомарные (tmp + replace), под замком
процесса; двух писателей у реестра нет: комнаты заводит и удаляет канал по
слову владельца, раннер только читает названия.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import threading
from pathlib import Path

ROOM_DEFAULT = "window"
ROOM_PATTERN = re.compile(r"^window-[0-9a-f]{8}$")
ROOM_ARCHIVE_DIR = "memory/groups/archive"

#: Историческое значение ключа комнаты окна.
#:
#: До 10.09.2026 окно писало комнату ключом ``pult``. Сегодня его не пишет
#: НИКТО — но он лежит в данных: в манифестах прогонов и в реестрах комнат,
#: собранных до той даты. Читатели обязаны его понимать, иначе старые ходы
#: теряют комнату. Слово выкорчевано из кода 18.09.2026 (слово владельца);
#: здесь оно остаётся одной строкой — надгробием, а не именем. Новым кодом
#: не пользоваться: писать всегда ``ROOM_DEFAULT``.
ROOM_LEGACY = "pult"

_LOCK = threading.Lock()


class RoomError(Exception):
    """Отказ словами; `status` — HTTP-код для канала (404 нет, 409 нельзя)."""

    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status = int(status)


def is_room(key: str) -> bool:
    """Ключ комнаты окна (по умолчанию или новой)? Telegram-ключи — нет."""
    key = str(key or "")
    return key == ROOM_DEFAULT or bool(ROOM_PATTERN.match(key))


def registry_path(tree: Path) -> Path:
    return Path(tree) / "memory" / ".state" / "rooms.json"


def archive_path(tree: Path, key: str) -> Path:
    return Path(tree) / "memory" / "groups" / (str(key) + ".jsonl")


def _context_path(tree: Path, key: str) -> Path:
    return Path(tree) / "memory" / ".state" / "group_context" / (str(key) + ".json")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{os.getpid()}-{path.name}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8",
                   newline="\n")
    os.replace(tmp, path)


def load(tree: Path) -> dict[str, dict]:
    """ключ -> {"title", "created_at"}. Нет файла — пусто (комната `window`
    существует всегда, в реестре ей быть не обязательно)."""
    try:
        data = json.loads(registry_path(tree).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rooms = data.get("rooms") if isinstance(data, dict) else None
    out: dict[str, dict] = {}
    for key, row in (rooms or {}).items():
        if is_room(key) and isinstance(row, dict):
            out[str(key)] = {"title": str(row.get("title") or ""),
                             "created_at": str(row.get("created_at") or "")}
    return out


def _save(tree: Path, rooms: dict[str, dict]) -> None:
    _write_json(registry_path(tree), {"v": 1, "updated_at": _now(), "rooms": rooms})


def title(tree: Path, key: str, agent_name: str = "") -> str:
    """Имя комнаты для окна и для кадра агента. `window` без своего имени
    зовётся именем агента (слово владельца 06.09: не «Окно»)."""
    key = str(key or "")
    row = load(tree).get(key) or {}
    named = str(row.get("title") or "").strip()
    if named:
        return named
    if key == ROOM_DEFAULT:
        return str(agent_name or "").strip() or "Агент"
    return key


def _clean_title(raw) -> str:
    text = " ".join(str(raw or "").split())
    if not text:
        raise RoomError(400, "у комнаты должно быть имя")
    return text[:80]


def create(tree: Path, raw_title) -> dict:
    """Новая комната: ключ, реестр, пустой архив и запись в group_context —
    чтобы окно увидело комнату в списке сразу, до первого сообщения."""
    tree = Path(tree)
    name = _clean_title(raw_title)
    with _LOCK:
        rooms = load(tree)
        key = ""
        for _ in range(16):
            candidate = "window-" + secrets.token_hex(4)
            if candidate not in rooms and not archive_path(tree, candidate).exists():
                key = candidate
                break
        if not key:
            raise RoomError(500, "не нашлось свободного ключа комнаты")
        rooms[key] = {"title": name, "created_at": _now()}
        _save(tree, rooms)
        archive = archive_path(tree, key)
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists():
            archive.write_text("", encoding="utf-8")
        _write_json(_context_path(tree, key), {
            "peer_id": key, "archive": "memory/groups/" + key + ".jsonl",
            "message_count": 0, "participant_count": 2, "topic_count": 0,
            "archive_mtime_ns": archive.stat().st_mtime_ns, "archive_size": 0})
    return {"peer_id": key, "title": name, "kind": "window"}


def rename(tree: Path, key: str, raw_title) -> dict:
    tree = Path(tree)
    key = str(key or "")
    if not is_room(key):
        raise RoomError(404, "нет такой комнаты")
    name = _clean_title(raw_title)
    with _LOCK:
        rooms = load(tree)
        if key != ROOM_DEFAULT and key not in rooms and not archive_path(tree, key).exists():
            raise RoomError(404, "нет такой комнаты")
        row = rooms.get(key) or {"created_at": _now()}
        row["title"] = name
        rooms[key] = row
        _save(tree, rooms)
    return {"peer_id": key, "title": name, "kind": "window"}


def delete(tree: Path, key: str) -> dict:
    """Комната исчезает из списка, архив уезжает в `memory/groups/archive/` —
    без потери (решение владельца: прятать, не стирать). `window` — 409."""
    tree = Path(tree)
    key = str(key or "")
    if key == ROOM_DEFAULT:
        raise RoomError(409, "комнату по умолчанию удалить нельзя")
    if not is_room(key):
        raise RoomError(404, "нет такой комнаты")
    with _LOCK:
        rooms = load(tree)
        archive = archive_path(tree, key)
        if key not in rooms and not archive.exists():
            raise RoomError(404, "нет такой комнаты")
        moved = ""
        if archive.exists():
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            dest_dir = tree.joinpath(*ROOM_ARCHIVE_DIR.split("/"))
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{key}-{stamp}.jsonl"
            os.replace(archive, dest)
            moved = str(dest.relative_to(tree)).replace("\\", "/")
        try:
            _context_path(tree, key).unlink()
        except OSError:
            pass
        rooms.pop(key, None)
        _save(tree, rooms)
    return {"peer_id": key, "archived": moved}


def rows(tree: Path, agent_name: str = "") -> list[dict]:
    """Все комнаты окна, включая `window`, — для списка чатов."""
    tree = Path(tree)
    known = load(tree)
    keys = [ROOM_DEFAULT] + sorted(k for k in known if k != ROOM_DEFAULT)
    return [{"peer_id": k, "title": title(tree, k, agent_name), "kind": "window",
             "created_at": (known.get(k) or {}).get("created_at", "")} for k in keys]
