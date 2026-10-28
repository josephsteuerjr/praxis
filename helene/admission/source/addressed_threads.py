# -*- coding: utf-8 -*-
"""Адресный рефлекс памяти (desire-a03780224e).

Собирает открытые нити ЛЮДЕЙ, названных этим ходом транспортом, — типизированно
и с источником. Рендер не угадывает: frame_layout._working печатает ровно то,
что положено сюда как snap['addressed_threads'], а при пустоте — названную пустоту.

Контракт:
- присутствие определяет ТРАНСПОРТ (sender_id окна комнаты + principal хода),
  не появление имени в тексте (правило из _present_by_transport);
- источник нитей: раздел «Открытые нити» досье (people.LOOPS) и pending-следы
  telegram-followup ledger, ключуемые по peer/user id;
- каждая строка несёт происхождение: (досье) или (follow-up);
- пусто — значит пусто: никакой подстановки «открытых нитей вообще».
"""

from __future__ import annotations

import re
import time

import people

MAX_ITEMS = 6          # строк в зону «в работе»
MAX_CHARS = 200        # на строку
FOLLOWUP_MAX_AGE = 72 * 3600  # pending-след живёт 72ч, как и в ledger

_WAKE = re.compile(r"\[\s*x\s*\]", re.IGNORECASE)  # закрытые ([x]) нити не поднимаем


def _present_ids(ctx) -> set[str]:
    """tg-id присутствующих этого хода: транспорт + principal. Без ctx — пусто."""
    out: set[str] = set()
    try:
        room = ctx.room_id if getattr(ctx, "room_id", None) is not None else ctx.chat_id
        if room is not None:
            from agent import _room_window  # локальный импорт: цикл зависимостей
            for row in _room_window(room):
                sender = row.get("sender_id")
                if sender not in (None, "", 0):
                    out.add(str(sender))
    except Exception:
        pass
    principal = getattr(ctx, "principal_id", None)
    if principal:
        out.add(str(principal))
    return out


def _loops_for(slug: str) -> list[str]:
    """Открытые (не [x]) нити из досье, в порядке файла."""
    try:
        _, body = people.read(slug)
    except Exception:
        return []
    raw = str(body.get(people.LOOPS, "") or "").splitlines()
    out = []
    for line in raw:
        s = line.strip()
        if not s or _WAKE.search(s):
            continue
        out.append(re.sub(r"\s+", " ", s))
    return out


def _slug_map(ids: set[str]) -> dict[str, str]:
    """tg-id → слаг одним проходом по досье (не скан на каждый id)."""
    try:
        seen_once: dict[str, str] = {}
        dup: set[str] = set()
        for path in sorted(people.PEOPLE_DIR.glob("*.md")):
            if path.stem.startswith("_"):
                continue
            tg = people.telegram_id(path.stem)
            if not tg or tg not in ids:
                continue
            if tg in seen_once:
                dup.add(tg)  # расщепление — не угадываем, какого из двух (как slug_for_principal)
            else:
                seen_once[tg] = path.stem
        return {tg: slug for tg, slug in seen_once.items() if tg not in dup}
    except Exception:
        return {}


def _pending_followups(ids: set[str], peer: object) -> list[dict]:
    """Pending-следы ledger, адресованные этому ходу: по user id или peer комнаты."""
    if not ids and peer in (None, ""):
        return []
    try:
        from telegram_followups import _load
        data = _load()
    except Exception:
        return []
    now = time.time()
    out = []
    for item in data.get("items") or []:
        if item.get("status") != "pending":
            continue
        if now > float(item.get("expires_at") or 0):
            continue
        uid = item.get("target_user_id")
        p = str(item.get("target_peer_id") or "")
        if (uid and str(uid) in ids) or (peer is not None and p == str(peer)):
            out.append(item)
    return out


def collect(ctx=None) -> tuple[str, ...]:
    """Строки адресных нитей для snap['addressed_threads']. Пусто — значит пусто."""
    ids = _present_ids(ctx)
    if not ids:
        return ()
    peer = ctx.chat_id if ctx is not None else None
    lines: list[str] = []
    seen: set[str] = set()

    # 1) нити досье присутствующих (один проход по досье)
    for tg, slug in sorted(_slug_map(ids).items()):
        for loop in _loops_for(slug):
            line = f"{tg} (досье): {loop[:MAX_CHARS]}"
            if line not in seen:
                seen.add(line)
                lines.append(line)

    # 2) pending follow-up следы (мой след без ответа)
    for item in _pending_followups(ids, peer):
        label = str(item.get("target_label") or item.get("target_user_id") or "?")
        excerpt = str(item.get("sent_excerpt") or "")[:80]
        line = f"{label} (follow-up, без ответа): {excerpt}"
        if line not in seen:
            seen.add(line)
            lines.append(line)

    return tuple(lines[:MAX_ITEMS])
