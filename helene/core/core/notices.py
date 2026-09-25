"""Накопитель уведомлений: что случилось в другой комнате, пока агент занят другим (25.09, поток G).

Семантика — слово Егора (`desk-notes/УВЕДОМЛЕНИЯ-В-КАДРЕ-25.09.md`, одобрено 25.09):

  1. Пока идёт ход в одном чате, окно, задача или Пробуждение, в ДРУГОЙ комнате что-то
     происходит: упоминание, ответ на её сообщение, личка. Это ложится сюда, в накопитель.
  2. Первый показ — как непрочитанное («НОВОЕ»), блоком в её ближайшем вводе модели:
     следующая итерация тул-цикла или следующий ход любого окна/чата.
  3. Дальше — как прочитанное, компактно, но строка остаётся, пока не погаснет.
  4. Гаснет обычным ходом исходного чата: ход в этом чате НАЧАЛСЯ — его уведомления
     сняты (`clear_chat`). Нового тула нет, явного «подтвердить» нет.
  5. Уведомление — не будильник: отдельного хода не рождает, очередь и доставку не меняет.

Второй род записей — слова владельца в личке, пока живут её окна и будильники
(`owner_line`): каждое живое окно видит реплику один раз в своём следующем вводе и само
понимает, решение это или нет. Её собственная пометка `journal(decision=true)` даёт
`owner_decision` — такая строка держится в окне до его конца. Классификации по словам нет.

Приватность fail-closed: содержимое личек показывается только в кадре с владельческой
аудиторией (owner-DM, её окно к владельцу); в кадре публичной комнаты та же строка —
«кто и где», без «что». Без живого run блок не строится вовсе: аудитория неизвестна.

Файл — `memory/.state/notices.json`, запись атомарная (tmp + replace), потолок CAP
записей, старше TTL уходят. Блок в кадре живёт в изменчивом хвосте (последнее user-
сообщение), не в замороженном префиксе: кэш z.ai режется сообщениями, и хвост меняется
каждым ходом и так. Рычаг: PRAXIS_NOTICES=off.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent.parent)
STATE_FILE = BASE / "memory" / ".state" / "notices.json"

_LOCK = threading.Lock()

CAP = 200                 # потолок записей в накопителе
TTL_SEC = 48 * 3600       # старше двух суток — уходят
ROWS = 6                  # потолок строк блока; остальное — «…и ещё N»
GIST_CHARS = 120          # первые знаки чужой реплики в строке блока

ROOM_KINDS = ("mention", "reply", "name", "dm", "node")
OWNER_KINDS = ("owner_line", "owner_decision")
# Окна и будильники — кому адресованы слова владельца. Ход чата их не видит:
# владелец в своей личке и так в кадре, а в чужой комнате его слова — чужая приватность.
WINDOW_RUN_KINDS = ("task_window", "coding_window", "wake", "heartbeat")


def enabled() -> bool:
    return str(os.getenv("PRAXIS_NOTICES", "on") or "on").strip().lower() not in {
        "0", "off", "false", "no"}


# ─────────────────────────────────────────────── хранилище

def _load() -> list[dict]:
    """Список записей; любое повреждение — пустой накопитель (теряем показ, не ход)."""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return [row for row in (items or []) if isinstance(row, dict)]


def _save(items: list[dict]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"schema": "praxis.notices.v1", "items": items},
                              ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _prune(items: list[dict], now: float) -> list[dict]:
    fresh = [row for row in items if float(row.get("ts") or 0) >= now - TTL_SEC]
    return fresh[-CAP:]


def _clip(text, limit: int = GIST_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _hhmm(ts: float) -> str:
    try:
        import praxis_time
        return praxis_time.local_from(float(ts)).strftime("%H:%M")
    except Exception:
        return time.strftime("%H:%M", time.localtime(float(ts)))


def _put(record: dict, *, dedup: str) -> dict | None:
    """Положить запись; дубль по `dedup` не кладётся второй раз (возвращается None)."""
    now = float(record.get("ts") or time.time())
    with _LOCK:
        items = _prune(_load(), now)
        if dedup and any(row.get("dedup") == dedup for row in items):
            return None
        record = dict(record, id=record.get("id") or f"ntc-{uuid.uuid4().hex[:10]}",
                      dedup=dedup, shown_first_at=None, shown_runs={})
        items.append(record)
        _save(items[-CAP:])
    return record


# ─────────────────────────────────────────────── запись событий

def note_incoming(*, kind: str, chat_id, chat_title: str, who: str, message_id,
                  gist: str, private: bool, ts: float | None = None,
                  is_owner_dm: bool = False) -> dict | None:
    """Событие комнаты: упоминание / ответ / имя / личка. Дедуп по (chat_id, message_id).

    `is_owner_dm` — реплика владельца в личке: кроме события комнаты кладётся и
    `owner_line` для её живых окон и будильников (см. шапку модуля).
    """
    if not enabled():
        return None
    kind = str(kind or "").strip()
    if kind not in ROOM_KINDS:
        return None
    now = float(ts) if ts else time.time()
    chat = str(chat_id if chat_id is not None else "")
    mid = str(message_id if message_id is not None else "")
    record = {
        "kind": kind, "ts": now, "chat_id": chat, "chat_title": _clip(chat_title, 60),
        "who": _clip(who, 40), "message_id": mid, "gist": _clip(gist), "private": bool(private),
    }
    out = _put(record, dedup=f"room:{chat}:{mid or int(now * 1000)}")
    if is_owner_dm:
        note_owner_line(gist, chat_id=chat, message_id=mid, ts=now, who=who)
    return out


def note_owner_line(text: str, *, chat_id, message_id, ts: float | None = None,
                    who: str = "владелец") -> dict | None:
    """Реплика владельца в личке — каждому живому окну и будильнику, один раз."""
    if not enabled():
        return None
    now = float(ts) if ts else time.time()
    chat = str(chat_id if chat_id is not None else "")
    mid = str(message_id if message_id is not None else "")
    return _put({"kind": "owner_line", "ts": now, "chat_id": chat, "chat_title": "ЛС",
                 "who": _clip(who, 40), "message_id": mid, "gist": _clip(text),
                 "private": True},
                dedup=f"owner:{chat}:{mid or int(now * 1000)}")


def note_owner_decision(text: str, *, run_id: str = "", ts: float | None = None) -> dict | None:
    """Её пометка: запись в дневник с decision=true. Держится в окне до его конца."""
    if not enabled():
        return None
    now = float(ts) if ts else time.time()
    return _put({"kind": "owner_decision", "ts": now, "chat_id": "", "chat_title": "дневник",
                 "who": "владелец", "message_id": "", "gist": _clip(text, 200),
                 "private": True, "from_run": str(run_id or "")},
                dedup=f"decision:{int(now * 1000)}:{uuid.uuid4().hex[:6]}")


def note_node(*, task_id: str, unit_id: str, status: str, goal: str = "",
              origin_chat: str = "", ts: float | None = None) -> dict | None:
    """Узел Forge / субагент закончил. Дедуп по (task_id, unit_id)."""
    if not enabled():
        return None
    now = float(ts) if ts else time.time()
    gist = f"узел {unit_id} задачи {task_id} закончил: {status}"
    if goal:
        gist += f" — {_clip(goal, 60)}"
    return _put({"kind": "node", "ts": now, "chat_id": str(origin_chat or ""),
                 "chat_title": "Forge", "who": str(unit_id), "message_id": "",
                 "gist": _clip(gist, 160), "private": False},
                dedup=f"node:{task_id}:{unit_id}")


def clear_chat(chat_id) -> int:
    """Ход в этом чате начался — его комнатные уведомления сняты. Слова владельца
    (`owner_*`) не трогаются: они адресованы окнам, а не ходу чата."""
    chat = str(chat_id if chat_id is not None else "")
    if not chat:
        return 0
    with _LOCK:
        items = _load()
        keep = [row for row in items
                if not (row.get("kind") in ROOM_KINDS and str(row.get("chat_id")) == chat)]
        removed = len(items) - len(keep)
        if removed:
            _save(keep)
    return removed


def pending(now: float | None = None) -> list[dict]:
    """Живые записи (для стендов и читалок состояния)."""
    now = float(now or time.time())
    with _LOCK:
        return _prune(_load(), now)


# ─────────────────────────────────────────────── блок в кадре

def _line(row: dict, *, owner_context: bool, fresh: bool) -> str:
    mark = "НОВОЕ" if fresh else "прочитано"
    when = _hhmm(row.get("ts") or 0)
    kind = row.get("kind")
    who = row.get("who") or "?"
    where = row.get("chat_title") or row.get("chat_id") or "?"
    gist = row.get("gist") or ""
    private = bool(row.get("private"))
    if kind == "owner_decision":
        return f"• {mark}  решение владельца (твоя запись, {when}): «{gist}»"
    if kind == "owner_line":
        if owner_context:
            return f"• {mark}  владелец в ЛС, {when}: «{gist}»"
        return f"• {mark}  владелец написал в ЛС, {when} (содержимое — в ЛС)"
    if kind == "node":
        return f"• {mark}  {gist}, {when}"
    if private and not owner_context:
        return f"• {mark}  ЛС · {who} — новое сообщение, {when}"
    what = {"mention": "упоминание", "reply": "ответ на твоё сообщение",
            "name": "назвали по имени", "dm": "написал(а) в ЛС"}.get(kind, kind)
    place = "ЛС" if kind == "dm" else where
    return f"• {mark}  {place} · {who} — {what}, {when}: «{gist}»"


def block_for_input(*, run_id: str, run_kind: str, chat_id, owner_context: bool,
                    busy_label: str = "", mid_turn: bool = False,
                    now: float | None = None) -> str:
    """Текст блока для ЭТОГО ввода модели или '' — и пометка «показано» в накопителе.

    run_id/run_kind — живой run (без него не зовут: fail-closed на аудиторию);
    chat_id — чат текущего хода (его собственные события не показываются: ход их и снял);
    owner_context — владельческая аудитория (содержимое личек видно);
    mid_turn — итерация тул-цикла: только то, чего этот run ещё не видел.
    """
    if not enabled() or not run_id:
        return ""
    now = float(now or time.time())
    chat = str(chat_id if chat_id is not None else "")
    window = str(run_kind or "") in WINDOW_RUN_KINDS
    with _LOCK:
        items = _prune(_load(), now)
        rows: list[tuple[dict, bool]] = []
        for row in items:
            kind = row.get("kind")
            shown_runs = row.get("shown_runs") if isinstance(row.get("shown_runs"), dict) else {}
            seen_here = int(shown_runs.get(run_id) or 0)
            if kind in OWNER_KINDS:
                if not window:
                    continue
                if kind == "owner_line" and seen_here:
                    continue          # окно видело один раз — ушло из этого окна
            elif kind in ROOM_KINDS:
                if chat and str(row.get("chat_id")) == chat:
                    continue          # своя комната: ход её и снимает
            else:
                continue
            if mid_turn and seen_here:
                continue              # посреди хода — только новое для этого run
            rows.append((row, seen_here == 0))
        if not rows:
            return ""
        rows.sort(key=lambda pair: float(pair[0].get("ts") or 0))
        shown, rest = rows[:ROWS], rows[ROWS:]
        for row, _fresh in shown:
            row.setdefault("shown_runs", {})
            row["shown_runs"][run_id] = int(row["shown_runs"].get(run_id) or 0) + 1
            if row.get("shown_first_at") is None:
                row["shown_first_at"] = now
        _save(items)
    head = "Пока идёт другое" + (f" ({busy_label})" if busy_label else "") + ":"
    if mid_turn:
        head = "Пока шла работа, пришло:"
    lines = [head] + [_line(row, owner_context=owner_context, fresh=fresh)
                      for row, fresh in shown]
    if rest:
        by_place: dict[str, int] = {}
        for row, _fresh in rest:
            place = ("ЛС" if row.get("kind") in ("dm", "owner_line") else
                     str(row.get("chat_title") or row.get("chat_id") or "?"))
            by_place[place] = by_place.get(place, 0) + 1
        spread = ", ".join(f"{n} в {place}" for place, n in by_place.items())
        lines.append(f"…и ещё {len(rest)} ({spread})")
    lines.append("Это не будильник: закончишь текущее — зайдёшь в тот чат как обычно и "
                 "решишь по ситуации.")
    return "\n".join(lines)


def state_line() -> str:
    """Одна строка для читалок состояния: сколько ждёт и где."""
    rows = pending()
    if not rows:
        return ""
    by_kind: dict[str, int] = {}
    for row in rows:
        by_kind[str(row.get("kind"))] = by_kind.get(str(row.get("kind")), 0) + 1
    return "уведомлений в накопителе: " + ", ".join(f"{k} {n}" for k, n in sorted(by_kind.items()))
