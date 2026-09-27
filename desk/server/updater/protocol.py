# -*- coding: utf-8 -*-
"""Протокол обновления — копия исполнителя. Оригинал: `app/deskd/control.py`.

Почему копия, а не импорт. С 27.09 код агента (`tree/`, `app/`) лежит на диске сервера и
смонтирован в его контейнер на запись: агент правит себя, и правки переживают пересборку.
Значит, `app/deskd/control.py` — файл, который агент может переписать, а исполнитель
работает с правами root и сокетом докера. Импортируй он протокол оттуда — агент получил
бы свой код в процессе исполнителя, то есть root на хосте. Эта копия лежит в `server/`,
куда контейнеру агента хода нет.

Что копия не разъехалась с оригиналом (константы, закрытые списки, разбор плана на
наборе входов), держит стенд `tests/t_updater_2709.py`, класс DriftFromControl: правишь
одно — правь и другое, иначе стенд краснеет по имени поля.
"""
from __future__ import annotations

import re

UPDATE_SCHEMA = "helene.update.v1"
UPDATE_PLAN = "update-plan.json"
UPDATE_RECEIPT = "update-plan.receipt.json"
UPDATE_CONFIRM = "update-plan.confirm.json"
UPDATE_VERDICT = "update-plan.verdict.json"
UPDATE_REPORTED = "update-plan.reported.json"
UPDATER_BEAT = "updater.json"
UPDATE_HISTORY = "update-history.jsonl"

#: Надзор serverboot бьётся каждые три секунды; записка старше — надзор молчит.
BEAT_STALE = 15.0

UPDATE_BACKUPS = {
    "full": "код, helene.json и вся папка data/ (память, переписка, ключи)",
    "code": "код и helene.json — БЕЗ копии data/",
}

UPDATE_CHECKS = {
    "running": "контейнер поднят и не перезапускается",
    "channel": "канал отвечает (/api/health)",
    "version": "канал поднят новой версией (/api/state → desk.version)",
    "supervisor": "надзор serverboot бьётся в своей записке",
    "runner": "агент (раннер) жив",
    "config": "адрес, порт, имена хостов и модели перенесены с прежнего контейнера",
    "code": "код агента — с диска сервера (tree/, app/): его правки переживают пересборку",
    "brain": "мозг настроен: модель и ключ на месте",
}
UPDATE_MANDATORY = ("running", "channel", "version", "supervisor", "runner", "config", "code")
UPDATE_VERDICTS = ("accept", "reject")

UPDATE_AWAIT_HOURS = 24
UPDATE_WAIT_MIN = (2, 30)
UPDATE_WAIT_DEFAULT = 10
UPDATE_TRIAL_MIN = (10, 120)
UPDATE_TRIAL_DEFAULT = 30

_VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,5}$")


def version_tuple(text) -> tuple[int, ...] | None:
    """«1.1.0» / «v1.1.0» -> (1, 1, 0). Непонятное — None, а не «0.0.0»."""
    raw = str(text or "").strip().lstrip("vV")
    if not _VERSION_RE.match(raw):
        return None
    return tuple(int(part) for part in raw.split("."))


def validate_plan(raw) -> tuple[dict | None, str]:
    """План -> (чистый план, "") или (None, причина). Копия `control.validate_plan`."""
    if not isinstance(raw, dict):
        return None, "план — не объект"
    plan_id = str(raw.get("id") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{8,32}", plan_id):
        return None, "у плана нет годного id"
    version = str(raw.get("version") or "latest").strip().lstrip("vV") or "latest"
    if version != "latest" and version_tuple(version) is None:
        return None, f"версия «{version}» не похожа на номер выпуска (1.2.3) и не «latest»"
    backup = str(raw.get("backup") or "full").strip().lower()
    if backup not in UPDATE_BACKUPS:
        return None, f"копия бывает {' или '.join(UPDATE_BACKUPS)}, а не «{backup}»"
    asked = raw.get("checks") or []
    if not isinstance(asked, list):
        return None, "checks — список имён проверок"
    unknown = [str(name) for name in asked if str(name) not in UPDATE_CHECKS]
    if unknown:
        return None, (f"таких проверок нет: {', '.join(unknown)} "
                      f"(есть: {', '.join(UPDATE_CHECKS)})")
    checks = list(UPDATE_MANDATORY) + [str(n) for n in asked
                                       if str(n) in UPDATE_CHECKS and str(n) not in UPDATE_MANDATORY]
    try:
        wait_min = int(raw.get("wait_min") or UPDATE_WAIT_DEFAULT)
    except (TypeError, ValueError):
        return None, "wait_min — число минут"
    wait_min = max(UPDATE_WAIT_MIN[0], min(UPDATE_WAIT_MIN[1], wait_min))
    try:
        trial_min = int(raw.get("trial_min") or UPDATE_TRIAL_DEFAULT)
    except (TypeError, ValueError):
        return None, "trial_min — число минут"
    trial_min = max(UPDATE_TRIAL_MIN[0], min(UPDATE_TRIAL_MIN[1], trial_min))
    return {
        "schema": UPDATE_SCHEMA,
        "id": plan_id,
        "version": version,
        "backup": backup,
        "checks": list(dict.fromkeys(checks)),
        "wait_min": wait_min,
        "trial_min": trial_min,
        "force_extensions": bool(raw.get("force_extensions")),
        "reason": " ".join(str(raw.get("reason") or "").split())[:500],
        "asked_by": str(raw.get("asked_by") or "agent")[:40],
        "asked_utc": str(raw.get("asked_utc") or "")[:40],
        "chat": " ".join(str(raw.get("chat") or "").split())[:160],
    }, ""
