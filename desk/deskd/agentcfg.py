# -*- coding: utf-8 -*-
"""Настройки агента, который живёт НЕ ЗДЕСЬ: чтение и правка через канал.

Зачем это есть. Окно к серверу с 0.5.2 умеет перезапускать агента и читать его
журналы, но настройки его правились только там, где он живёт: ssh, редактор,
`helene.json` руками. То есть за сменой модели или бот-токена владелец шёл в
терминал — при живом окне, которое к этому агенту уже подключено.

⚠ ЧТО ЗДЕСЬ ПРАВИТСЯ, А ЧТО НЕТ, И ПОЧЕМУ ЭТО ГЛАВНОЕ В ФАЙЛЕ.

Правится только то, что про САМОГО АГЕНТА: мозг, Telegram, имена, голос,
уведомления. Раскладка установки — где python, где код, где дерево, на каком
порту канал, что со службой и с реле — не правится отсюда НИКОГДА, и это не
осторожность, а граница ответственности: раскладку кладёт тот, кто разворачивал
агента, и правка её из окна означала бы «окно может увести раннер на чужой
исполняемый файл». Ключ окна и так открывает многое (записка агенту, правка его
конституции), но подменить программу, которой он запускается, он не должен.

Поэтому список разрешённых ключей — ЗАКРЫТЫЙ (`EDITABLE`), а всё остальное из
присланного отбрасывается и называется в расписке: молча проглоченный ключ
выглядел бы как «сохранилось», а на диск бы не попало.

Запись атомарная и со сверкой свежести (`mtime_ns`), как у маркдаунов: агент
правит свой конфиг сам (`switch_brain` пишет туда же), и выигрывать не должен
тот, кто записал последним.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from . import readers

SCHEMA = "helene.agentcfg.v1"
_WRITE_LOCK = threading.RLock()

#: Что окно вправе править. Ключ верхнего уровня → человеческое имя для расписки.
#: Всё, чего здесь нет, — про раскладку установки и правится там, где её клали.
EDITABLE: dict[str, str] = {
    "model": "мозг",
    "telegram": "Telegram",
    "agent": "имя агента",
    "owner": "имя владельца и комната",
    "voice": "голос",
    "notifications": "уведомления",
    "env": "ручки среды дерева",
}

#: Что НЕ отдаётся окну на чтение вовсе. Не из-за секретности — ключ владельца и
#: так открывает анатомию с ключом модели, — а чтобы экран не показывал ручек,
#: которых он всё равно не сохранит.
HIDDEN: tuple[str, ...] = ("service", "relay", "update", "python", "app", "runner",
                           "code", "tree", "port", "mode", "setup_complete", "agent_mode", "sandbox")


def server_access(cfg: dict) -> dict | None:
    """Stored server controls; presence of a socket is not a successful root call."""
    if os.environ.get("HELENE_SUPERVISOR") != "serverboot":
        return None
    from localharness import modes
    picture = modes.resolve(cfg, installed=False)
    socket_path = (os.environ.get("HELENE_HOST_BROKER_SOCKET") or "").strip()
    return {"agent_mode": picture["name"], "sandbox_enabled": picture["sandbox"],
            "session0": modes.session0(cfg), "scope": "host",
            "socket_present": bool(socket_path and Path(socket_path).exists())}


def _read_snapshot(path: Path) -> tuple[dict, str]:
    with path.open('rb') as source:
        before = os.fstat(source.fileno())
        raw = source.read()
        after = os.fstat(source.fileno())
    live = path.stat()
    if (before.st_mtime_ns != after.st_mtime_ns or before.st_size != after.st_size
            or (live.st_ino, live.st_mtime_ns) != (after.st_ino, after.st_mtime_ns)):
        raise ValueError('файл изменился во время чтения — перечитай настройки')
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        got = json.loads(text)
        if not isinstance(got, dict):
            raise ValueError("верхний уровень должен быть объектом {…}")
        return got, str(after.st_mtime_ns)
    raise ValueError("файл не читается ни как UTF-8, ни как UTF-16, ни как CP1251")


def mtime_ns(path: Path) -> str:
    """Отпечаток свежести строкой — как у маркдаунов: в JS число теряет точность."""
    try:
        return str(path.stat().st_mtime_ns)
    except OSError:
        return ""


def state() -> dict:
    """Что можно править и что там сейчас. Без ключей раскладки (см. HIDDEN)."""
    path = readers.config_path()
    if path is None:
        return {"schema": SCHEMA, "ok": False,
                "why": "рядом с каналом нет helene.json — этот агент развёрнут иначе, "
                       "и править отсюда нечего",
                "path": "", "config": {}, "editable": list(EDITABLE), "mtime_ns": ""}
    try:
        cfg, seen = _read_snapshot(path)
    except (OSError, ValueError) as exc:
        return {"schema": SCHEMA, "ok": False,
                "why": f"{path} не разобрался: {exc}",
                "path": str(path), "config": {}, "editable": list(EDITABLE),
                "mtime_ns": mtime_ns(path)}
    shown = {key: value for key, value in cfg.items() if key not in HIDDEN}
    return {
        "schema": SCHEMA,
        "ok": True,
        "why": "",
        "path": str(path),
        "config": shown,
        "editable": list(EDITABLE),
        "access": server_access(cfg),
        "mtime_ns": seen,
        # Что применится только перезапуском — а это ВСЁ здесь: дерево читает
        # конфиг на старте. Окно говорит это словами и даёт кнопку рядом.
        "restart_needed": True,
    }


def save(patch: Any, seen: str | None = None) -> dict:
    # HTTP handlers run in different threads. Only one writer may check freshness
    # and commit; a second request with the old receipt must get a conflict.
    with _WRITE_LOCK:
        return _save(patch, seen)


def _save(patch: Any, seen: str | None = None) -> dict:
    """Записать разрешённые блоки. Возвращает расписку, а не «ок».

    `patch` — объект с ключами верхнего уровня. Каждый разрешённый блок
    ЗАМЕЩАЕТСЯ целиком (окно присылает его целиком, как и у себя), остальные
    ключи файла остаются как были. Неразрешённые ключи в ответе названы
    поимённо: «отброшено» обязано быть видимым.
    """
    path = readers.config_path()
    if path is None:
        return {"ok": False, "code": "no_config",
                "error": "рядом с каналом нет helene.json — править нечего"}
    if not isinstance(patch, dict):
        return {"ok": False, "code": "bad_body", "error": "ожидался объект настроек"}
    try:
        current, now = _read_snapshot(path)
    except (OSError, ValueError) as exc:
        return {"ok": False, "code": "unreadable", "error": f"{path} не разобрался: {exc}"}

    if seen and now and str(seen) != now:
        return {"ok": False, "code": "conflict", "mtime_ns": now,
                "error": "Настройки на сервере изменились с тех пор, как окно их "
                         "открыло — перечитай и перенеси правку заново."}

    access = patch.get("access")
    if "access" in patch:
        if server_access(current) is None:
            return {"ok": False, "code": "unsupported_access", "error": "Эта установка не серверный Docker агент"}
        if (not isinstance(access, dict) or not access or set(access) - {"agent_mode", "session0"}
                or ("agent_mode" in access and (not isinstance(access["agent_mode"], str)
                                                or access["agent_mode"] not in {"sandbox", "interactive"}))
                or ("session0" in access and not isinstance(access["session0"], bool))):
            return {"ok": False, "code": "bad_access", "error": "Режим: sandbox или interactive; запрос root: true или false"}
        from localharness import modes
        if "agent_mode" in access:
            current["agent_mode"] = access["agent_mode"]
        if "session0" in access:
            service = current.get("service")
            current["service"] = {**(service if isinstance(service, dict) else {}),
                                  "session0": access["session0"]}
        modes.apply(current, installed=False)
    took: list[str] = []
    dropped: list[str] = []
    for key, value in patch.items():
        if key == "access":
            took.append(key)
        elif key in EDITABLE:
            current[key] = value
            took.append(key)
        else:
            dropped.append(key)
    if not took:
        return {"ok": False, "code": "nothing_editable", "dropped": dropped,
                "error": "в присланном нет ни одного ключа, который окно вправе "
                         f"править: это {', '.join(EDITABLE)}"}

    text = json.dumps(current, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(f".tmp-{path.name}")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        # Сервер: helene.json — одиночный файл, примонтированный в контейнер, и подмена
        # файла на его месте — EBUSY («Device or resource busy»: Настройки на сервере не
        # сохранялись вовсе, найдено 29.09), а папка установки каналу (с 1.2.5 — не root)
        # не пишется. Тогда — на месте, тем же текстом.
        try:
            with open(path, "r+", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
                fh.truncate()
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            return {"ok": False, "code": "write_failed", "error": f"не записалось: {exc}"}
    return {
        "ok": True,
        "path": str(path),
        "saved": took,
        "dropped": dropped,
        "mtime_ns": mtime_ns(path),
        "access": server_access(current),
        "note": "записано в файл агента; дерево читает его на старте — "
                "перезапусти агента, иначе правка полежит без дела",
    }
