# -*- coding: utf-8 -*-
"""Карточка конфликта утверждений: механизм ПОКАЗЫВАЕТ, а не решает.

Её слова 08.08: «код РЕШАЕТ конфликт вместо того, чтобы показать его мне». Проверено:
`formation._contest_referenced_claims` при новом поддержанном утверждении молча
переписывает старое в `contested` с причиной «superseded by supported contradictory
claim …». Никакого следа ей: ни карточки, ни строки в кадре. Писателя `supersedes` в
коде нет ни одного — есть только `contradicts`, проставляемый тем же автоматом.

Её же спецификация 04.08, дословно: механизм **не решает** — отдаёт карточку и поднимает
её перед действием, зависящим от спорного поля; исход явный.

⚠⚠ КОНТРАКТ, КОТОРЫЙ ВАЖНЕЕ ФУНКЦИОНАЛЬНОСТИ: КАРТОЧКА — ЭТО ЗАПИСЬ, А НЕ ПРОГОН.

Слова Егора 10.08: «главное, чтобы эта херь не застревала у неё как бесконечный прогон».
Основание не гипотетическое: вечная петля возобновления возвращалась ЧЕТЫРЕ раза, а
`in_doubt` однажды стал вечным надгробием, у которого не было руки его закрыть. Поэтому
здесь по построению НЕТ:

* прогона, статуса выполнения и жизненного цикла — только строка в append-only файле;
* замка, аренды и владельца — писать может кто угодно и когда угодно;
* пробуждения, follow-up и напоминания — карточка НИКОГДА не будит её;
* блокировки — ни один ход, инструмент или отправка не ждут её решения;
* «терминального» состояния, из которого нет дороги назад.

Не ответила — не висит ничего. Карточка просто лежит; через `SHOW_DAYS` она перестаёт
показываться как свежая, но с диска не исчезает и в силу не вступает. Отсутствие решения
— полноценный исход, а не долг.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
PATH = BASE / "memory" / ".state" / "claim_conflicts.jsonl"

SHOW_DAYS = 30          # сколько дней карточка считается свежей для показа
SHOW_LIMIT = 12         # потолок показа: кадр не имеет права распухнуть от спора


def _read() -> list[dict]:
    try:
        rows = []
        for line in PATH.read_text("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue          # битую строку пропускаем молча: она не повод падать
        return rows
    except OSError:
        return []


def _append(row: dict) -> bool:
    """Строка на диск. Не легла — говорим False, а не делаем вид, что карточка есть."""
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False              # запись карточки не имеет права ломать её ход


def note(*, subject: str, old_id: str, new_id: str, field: str = "",
         reason: str = "", old_text: str = "", new_text: str = "") -> str:
    """Завести карточку. Идемпотентно по паре (old_id, new_id): повтор ничего не плодит."""
    old_id, new_id = str(old_id or ""), str(new_id or "")
    if not old_id or not new_id or old_id == new_id:
        return ""
    for row in _read():
        if row.get("kind") == "conflict" and row.get("old_id") == old_id \
                and row.get("new_id") == new_id:
            return str(row.get("id") or "")
    card = {
        "kind": "conflict",
        "id": "cfl-" + uuid.uuid4().hex[:12],
        "at": time.time(),
        "subject": str(subject or "")[:200],
        "field": str(field or "")[:80],
        "old_id": old_id, "new_id": new_id,
        "old_text": str(old_text or "")[:600],
        "new_text": str(new_text or "")[:600],
        "reason": str(reason or "")[:300],
    }
    return card["id"] if _append(card) else ""


def resolve(card_id: str, *, verdict: str, by: str = "praxis", note_text: str = "") -> bool:
    """Её решение. `verdict` — свободный текст: чей она признаёт, оба, ни один.

    Разрешение — ТОЖЕ строка, а не правка прошлой: история спора остаётся читаемой.
    """
    card_id = str(card_id or "")
    if not card_id or not any(r.get("id") == card_id for r in _read()):
        return False
    return _append({"kind": "resolution", "id": card_id, "at": time.time(),
                    "verdict": str(verdict or "")[:300], "by": str(by or "")[:60],
                    "note": str(note_text or "")[:600]})


def cards(*, only_open: bool = True, fresh_days: int | None = SHOW_DAYS,
          limit: int = SHOW_LIMIT) -> list[dict]:
    """Карточки, свежие сверху. Ничего не меняет и никого не будит."""
    rows = _read()
    resolved: dict[str, dict] = {}
    for r in rows:
        if r.get("kind") == "resolution" and r.get("id"):
            resolved[str(r["id"])] = r
    out = []
    now = time.time()
    for r in rows:
        if r.get("kind") != "conflict":
            continue
        cid = str(r.get("id") or "")
        res = resolved.get(cid)
        if only_open and res:
            continue
        if fresh_days is not None and (now - float(r.get("at") or 0)) > fresh_days * 86400:
            continue
        out.append({**r, "resolution": res})
    out.sort(key=lambda r: float(r.get("at") or 0), reverse=True)
    return out[:max(0, int(limit))] if limit else out


def line(card: dict) -> str:
    """Одна строка карточки для кадра. Без markdown: её регистр держит вёрстка."""
    subj = str(card.get("subject") or "?")
    fld = str(card.get("field") or "")
    return "%s: %s%s — старое %s против нового %s, не разрешено" % (
        card.get("id"), subj, (" · " + fld) if fld else "",
        card.get("old_id"), card.get("new_id"))
