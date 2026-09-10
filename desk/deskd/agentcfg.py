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
from pathlib import Path
from typing import Any

from . import readers

SCHEMA = "helene.agentcfg.v1"

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
                           "code", "tree", "port", "mode", "setup_complete")


def _read(path: Path) -> dict:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        got = json.loads(text)
        if not isinstance(got, dict):
            raise ValueError("верхний уровень должен быть объектом {…}")
        return got
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
        cfg = _read(path)
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
        "mtime_ns": mtime_ns(path),
        # Что применится только перезапуском — а это ВСЁ здесь: дерево читает
        # конфиг на старте. Окно говорит это словами и даёт кнопку рядом.
        "restart_needed": True,
    }


def save(patch: Any, seen: str | None = None) -> dict:
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
        current = _read(path)
    except (OSError, ValueError) as exc:
        return {"ok": False, "code": "unreadable", "error": f"{path} не разобрался: {exc}"}

    now = mtime_ns(path)
    if seen and now and str(seen) != now:
        return {"ok": False, "code": "conflict", "mtime_ns": now,
                "error": "Настройки на сервере изменились с тех пор, как окно их "
                         "открыло — перечитай и перенеси правку заново."}

    took: list[str] = []
    dropped: list[str] = []
    for key, value in patch.items():
        if key in EDITABLE:
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
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": False, "code": "write_failed", "error": f"не записалось: {exc}"}
    return {
        "ok": True,
        "path": str(path),
        "saved": took,
        "dropped": dropped,
        "mtime_ns": mtime_ns(path),
        "note": "записано в файл агента; дерево читает его на старте — "
                "перезапусти агента, иначе правка полежит без дела",
    }
