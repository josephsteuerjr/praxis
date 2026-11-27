"""Owner window: what the harness recorded about a run, and the harness's own journals.

Read-only. Everything here comes from records the harness already wrote — the
run's event journal, its context and recap, and a fixed list of state journals
under memory/.state. Nothing is used for a decision and nothing is written.
A paired phone never reaches these routes: they are not in the device list of
the channel, so only the owner key opens them. Journals that hold contact
lists, credentials or device state are deliberately not in the list.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import stat
from functools import lru_cache
from pathlib import Path

from . import readers

_EVENTS = 8000                      # newest events of a run that are examined
_EVENT_BYTES = 64 * 1024 * 1024
_FRAME_MAX = 12 * 1024 * 1024       # a recorded model input larger than this is reported, not read
_DOC_MAX = 400_000
_TEXT = 600                         # longest string carried out of a journal row or an event
_ITEMS = 40                         # longest list carried out whole

# id, file under memory/.state, title, what the journal records, columns shown first.
JOURNALS = (
    ("denials", "denials.jsonl", "Отказы рук",
     "Где ограда остановила её руку и на каком рельсе", ("ts", "rail", "op", "detail")),
    ("perception", "perception_skips.jsonl", "Пропуски восприятия",
     "Что она не стала читать в этом проходе и почему", ("ts", "stage", "class", "detail", "prev_n")),
    ("verdicts", "gnome_verdicts.jsonl", "Вердикты гномов",
     "Фоновые проверки: свежесть лент, противоречия, свёртки", ("ts", "gnome", "subject", "verdict", "detail")),
    ("moderation", "telegram_moderation.jsonl", "Модерация Telegram",
     "Удаления и баны: решение, подтверждение, ошибка", ("ts", "action", "status", "peer_id", "sender_verified", "banned", "deleted", "error")),
    ("membership", "telegram_membership.jsonl", "Членство в группах",
     "Выходы и вступления: подготовлено, под вопросом, не удалось", ("at", "action", "kind", "target")),
    ("admin", "telegram_admin.jsonl", "Админ-действия Telegram",
     "Баны, ограничения и их снятие", ("ts", "action", "status", "peer_id", "error")),
    ("core", "core_events.jsonl", "События ядра",
     "Результаты субагентов, ревью Forge, модерация, генерация голоса", ("ts", "kind", "source")),
    ("hands", "hands.jsonl", "Руки",
     "Каждое обращение руки и его исход", ("ts", "op", "ok", "target", "note")),
    ("llm", "llm_calls.jsonl", "Вызовы модели",
     "Вход, кэш, выход, время и повторы каждого вызова", ("ts", "role", "model", "ok", "in", "cached", "out", "ms", "retries")),
    ("envelope", "envelope_shadow.jsonl", "Тень конверта",
     "С каким конвертом ушёл каждый ход: аудитория, повод, уровень открытия", ("ts", "run_id", "note")),
    ("turns", "turns.jsonl", "Ходы",
     "Её решение по ходу: удержано, сокращено, переписано, доставлено", ("ts", "kind", "run_id", "verdict", "held", "cut", "rewrote")),
    ("narration", "narration.jsonl", "Наррация",
     "Что она рассказала владельцу и как это принято", ("ts", "dest", "delivery", "gist")),
    ("probes", "probes.jsonl", "Пробы",
     "Разовые замеры маршрутов и ключей", ("ts", "kind")),
    ("seed", "seed_trace.jsonl", "След семян",
     "Замеры выборки воспоминаний: задержка и что вошло только по семени", ("ts", "scope", "latency_ms")),
    ("sleep", "sleep_receipts.jsonl", "Квитанции сна",
     "Ночные шаги: сделано, пропущено, не удалось", ("ts", "day", "step", "status", "reason")),
    ("services", "services.jsonl", "Службы",
     "Перезапуски служб с итогом", ("ts", "op", "unit", "ok", "note")),
)
_JOURNALS = {row[0]: row for row in JOURNALS}
_DOCS = {"context": "context.md", "recap": "RECAP.md"}


def _text(value, limit=_TEXT):
    return value if len(value) <= limit else value[:limit] + "…"


def compact(value, depth=0):
    """A journal value small enough to show: long strings and long lists are cut, never rewritten."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, dict):
        if depth >= 3:
            return "{…%d}" % len(value)
        return {_text(str(key), 80): compact(item, depth + 1) for key, item in list(value.items())[:_ITEMS]}
    if isinstance(value, (list, tuple)):
        if depth >= 3:
            return "[…%d]" % len(value)
        out = [compact(item, depth + 1) for item in value[:_ITEMS]]
        if len(value) > _ITEMS:
            out.append("…ещё %d" % (len(value) - _ITEMS))
        return out
    return _text(str(value))


def _ref(row):
    """A recorded result as the window names it; the bytes stay where the harness put them."""
    ref = row.get("result") if isinstance(row.get("result"), dict) else {}
    return {"result_id": str(ref.get("result_id") or ""), "size": ref.get("size"),
            "truncated": bool((ref.get("inline") or {}).get("truncated"))}


def _stamp(path):
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size, info.st_ino)


@lru_cache(maxsize=64)
def _count_lines(path, stamp):
    try:
        with open(path, "rb") as handle:
            return sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 20), b""))
    except OSError:
        return 0


def _state_dir():
    return Path(readers.tree()) / "memory" / ".state"


# ------------------------------------------------------------------ journals

def journals():
    """The fixed list of the harness's state journals with their size and freshness."""
    out = []
    for key, name, title, hint, columns in JOURNALS:
        path = _state_dir() / name
        stamp = _stamp(path)
        if stamp is None:
            out.append({"id": key, "title": title, "hint": hint, "columns": list(columns), "exists": False})
            continue
        modified = dt.datetime.fromtimestamp(stamp[0] / 1e9, dt.UTC).isoformat(timespec="seconds")
        out.append({"id": key, "title": title, "hint": hint, "columns": list(columns), "exists": True,
                    "bytes": stamp[1], "lines": _count_lines(str(path), stamp), "modified": modified})
    return out


def journal(key, limit):
    """The newest rows of one journal, oldest first."""
    spec = _JOURNALS.get(key)
    if spec is None:
        return None
    _, name, title, hint, columns = spec
    path = _state_dir() / name
    rows = []
    for line in readers.tail_lines(path, limit, max_bytes=24_000_000):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(compact(row))
    stamp = _stamp(path)
    return {"id": key, "title": title, "hint": hint, "columns": list(columns), "rows": rows[-limit:],
            "lines": _count_lines(str(path), stamp) if stamp else 0}


def _journal_rows(name, limit):
    return readers.tail_jsonl(_state_dir() / name, limit)


def _last_for_run(name, run_id, limit):
    for row in reversed(_journal_rows(name, limit)):
        if isinstance(row, dict) and row.get("run_id") == run_id:
            return compact(row)
    return None


# ------------------------------------------------------------------ one run

def _rows(folder):
    rows = []
    for line in readers.tail_lines(folder / "events.jsonl", _EVENTS, max_bytes=_EVENT_BYTES):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _frame_meta(metadata):
    """What the harness wrote about the frame it sent: budget, tiers, sections, honesty."""
    meta = metadata if isinstance(metadata, dict) else {}
    out = {}
    for key in ("basis", "mode", "frame_id", "reused", "emit", "roster", "system_form", "schema"):
        if key in meta:
            out[key] = compact(meta[key], 2)
    budget = meta.get("budget")
    if isinstance(budget, dict):
        out["budget"] = {key: budget[key] for key in ("limit", "unit", "used_start", "used_final") if key in budget}
        out["budget"]["excludes"] = [str(item)[:80] for item in (budget.get("excludes") or [])[:_ITEMS]]
    for key in ("counts", "caps", "honesty", "receipt"):
        if isinstance(meta.get(key), dict):
            out[key] = compact(meta[key], 2)
    covers = meta.get("covers")
    if isinstance(covers, list):
        out["covers"] = compact(covers, 2)
    sections = meta.get("sections")
    if isinstance(sections, list):
        out["sections"] = compact(sections, 1)
    return out


def _generation(metrics):
    """Relay generation telemetry, as numbers only; a detector reads them, this does not judge."""
    data = metrics if isinstance(metrics, dict) else {}
    if data.get("schema") != "relay.generation.v1":
        return None
    safe = {key: data[key] for key in ("argument_bytes", "argument_deltas", "attempt_ms",
                                      "longest_whitespace_run", "outside_string_whitespace_chars")
            if type(data.get(key)) is int and 0 <= data[key] <= 2 ** 53 - 1}
    if data.get("outcome") in ("streaming", "completed", "interrupted", "upstream_failed", "runaway_generation"):
        safe["outcome"] = data["outcome"]
    return safe or None


def timeline(run_id):
    """Everything the harness journaled about one run, grouped the way the window shows it."""
    folder = readers.run_dir(run_id)
    if folder is None:
        return None
    manifest = readers._load_json(folder / "manifest.json")
    context = manifest.get("context") or {}
    calls: dict[str, dict] = {}
    order: list[str] = []
    statuses, outgoing, artifacts, checkpoints, other = [], [], [], [], []
    tools: dict[str, int] = {}
    info: dict = {}
    for row in _rows(folder):
        kind = row.get("kind")
        at, seq = row.get("at"), row.get("seq")
        if kind == "run_created":
            snapshot = row.get("context_snapshot") if isinstance(row.get("context_snapshot"), dict) else {}
            info["context_snapshot"] = {"path": str(snapshot.get("path") or ""), "size": snapshot.get("size"),
                                        "sha256": snapshot.get("sha256")}
            continue
        if kind in ("status_changed", "delivery_skipped", "artifact_created", "recap_written", "run_checkpoint"):
            if kind == "status_changed":
                statuses.append({"at": at, "seq": seq, "from": row.get("from_status"), "to": row.get("to_status"),
                                 "reason": _text(str(row.get("reason") or ""), 300), "requested_by": row.get("requested_by"),
                                 "control_action": row.get("control_action")})
            elif kind == "delivery_skipped":
                outgoing.append({"at": at, "seq": seq, "kind": kind, "reason": _text(str(row.get("reason") or ""), 300),
                                 "receipt_key": str(row.get("receipt_key") or "")})
            elif kind == "artifact_created":
                item = row.get("artifact") if isinstance(row.get("artifact"), dict) else {}
                artifacts.append({"at": at, "seq": seq, "artifact_id": str(item.get("artifact_id") or ""),
                                  "name": _text(str(item.get("name") or ""), 200), "size": item.get("size"),
                                  "media_type": item.get("media_type")})
            elif kind == "recap_written":
                info["recap"] = {"size": row.get("size"), "sha256": row.get("sha256")}
            else:
                checkpoints.append({"at": at, "seq": seq, **_ref(row)})
            continue
        if kind in ("direct_outbox_intent", "direct_outbox_projection", "outbound_guard_input"):
            outgoing.append({"at": at, "seq": seq, "kind": kind, "tool": row.get("tool"),
                             "call_id": str(row.get("call_id") or ""), **_ref(row)})
            continue
        call_id = row.get("call_id")
        if kind in ("tool_started", "tool_result", "tool_failed", "tool_completed", "tool_reconciled", "tool_dispatch",
                    "tool_execution", "tool_model_response"):
            name = str(row.get("tool") or row.get("name") or "?")
            if kind == "tool_started":
                tools[name] = tools.get(name, 0) + 1
            continue
        if not call_id:
            other.append({"at": at, "seq": seq, "kind": str(kind)})
            continue
        call = calls.get(call_id)
        if call is None:
            call = calls[call_id] = {"call_id": call_id, "at": at, "seq": seq}
            order.append(call_id)
        if kind == "model_started":
            call.update(at=at, seq=seq, role=row.get("role"), messages=row.get("message_count"), tools=row.get("tool_count"))
        elif kind == "model_input":
            call["input"] = _ref(row)
            call["frame"] = _frame_meta(row.get("metadata"))
        elif kind == "model_preparation_timing":
            call["preparation"] = {"observed": bool(row.get("preparation_observed")), "total_ms": row.get("measured_total_ms"),
                                   "stages_ms": compact(row.get("stages_ms"), 1), "substages_ms": compact(row.get("substages_ms"), 1)}
        elif kind == "model_progress":
            generation = _generation(row.get("metrics"))
            if generation:
                call["generation"] = generation
                call["generation_at"] = row.get("at")
        elif kind == "model_output":
            call["output"] = _ref(row)
        elif kind == "model_completed":
            call["completed"] = {"at": at, "duration_ms": row.get("duration_ms"), "stop_reason": row.get("stop_reason"),
                                "model": row.get("model"), "role": row.get("role"), "framework": row.get("framework"),
                                "vision": row.get("vision"), "tool_calls": row.get("tool_calls"),
                                "text_chars": row.get("text_chars"), "usage": compact(row.get("usage"), 1)}
        elif kind == "model_failed":
            call["failed"] = {"at": at, "duration_ms": row.get("duration_ms"), "error": _text(str(row.get("error") or ""), 400)}
    for call in calls.values():
        call["state"] = "failed" if "failed" in call else "completed" if "completed" in call else "started"
    return {
        "id": run_id,
        "created": manifest.get("created_at"), "status": manifest.get("status"),
        "kind": context.get("kind"), "goal": _text(str(context.get("goal") or ""), 400),
        "model_profile": context.get("model_profile"), "event_seq": manifest.get("event_seq"),
        "examined_events": len(other) + len(statuses) + len(outgoing) + len(checkpoints) + len(order),
        "calls": [calls[call_id] for call_id in order], "tools": tools,
        "statuses": statuses, "outgoing": outgoing, "artifacts": artifacts, "checkpoints": checkpoints,
        "other": other, **info,
        # what the run has left so far: the window asks only for documents that exist
        "documents": {name: (folder / _DOCS[name]).is_file() for name in _DOCS},
        "envelope": _last_for_run("envelope_shadow.jsonl", run_id, 400),
        "turn": _last_for_run("turns.jsonl", run_id, 600),
    }


def frame(run_id, call_id):
    """The exact input a model call received, verified against the digest the harness recorded."""
    folder = readers.run_dir(run_id)
    if folder is None or not call_id:
        return None
    for row in _rows(folder):
        if row.get("kind") == "model_input" and row.get("call_id") == call_id:
            break
    else:
        return None
    ref = row.get("result") if isinstance(row.get("result"), dict) else {}
    path = readers._result_file(folder, ref)
    out = {"call_id": call_id, "at": row.get("at"), "result_id": str(ref.get("result_id") or ""),
           "recorded_bytes": ref.get("size"), "sha256": ref.get("sha256"), "frame": _frame_meta(row.get("metadata"))}
    if path is None:
        return {**out, "available": False, "reason": "missing"}
    try:
        if path.stat().st_size > _FRAME_MAX:
            return {**out, "available": False, "reason": "large"}
        raw = path.read_bytes()
    except OSError:
        return {**out, "available": False, "reason": "unreadable"}
    out["bytes"] = len(raw)
    out["verified"] = hashlib.sha256(raw).hexdigest() == ref.get("sha256")
    if not out["verified"]:
        return {**out, "available": False, "reason": "integrity"}
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return {**out, "available": False, "reason": "format"}
    if not (isinstance(data, dict) and isinstance(data.get("messages"), list)):
        return {**out, "available": False, "reason": "format"}
    return {**out, "available": True, "system": data.get("system"), "messages": data["messages"],
            "tools": data.get("tools") if isinstance(data.get("tools"), list) else []}


def document(run_id, name):
    folder = readers.run_dir(run_id)
    if folder is None or name not in _DOCS:
        return None
    try:
        info = os.stat(folder / _DOCS[name])
        if not stat.S_ISREG(info.st_mode) or info.st_size > _DOC_MAX * 4:
            return None
        with open(folder / _DOCS[name], encoding="utf-8", errors="replace") as handle:
            return {"name": name, "text": handle.read(_DOC_MAX), "truncated": info.st_size > _DOC_MAX}
    except OSError:
        return None


# ------------------------------------------------------------------ routes

def routes(Route):
    """Five owner-only reads, added to the channel's one route table."""
    import asyncio
    from aiohttp import web

    def limit_of(call, default=200, high=2000):
        try:
            return max(1, min(high, int(call.query.get("limit") or default)))
        except (TypeError, ValueError):
            return default

    async def one(call, work, missing):
        data = await asyncio.to_thread(work)
        if data is None:
            raise web.HTTPNotFound(text=missing)
        return data

    async def journals_route(call):
        return await asyncio.to_thread(journals)

    async def journal_route(call):
        return await one(call, lambda: journal(call.match["name"], limit_of(call)), "нет такого журнала")

    async def timeline_route(call):
        return await one(call, lambda: timeline(call.match["run_id"]), "нет такого хода")

    async def frame_route(call):
        return await one(call, lambda: frame(call.match["run_id"], call.query.get("call") or ""),
                         "нет записи входа для этого вызова")

    async def document_route(call):
        return await one(call, lambda: document(call.match["run_id"], call.match["name"]), "нет такого документа")

    return (
        Route("GET", "/api/window/journals", journals_route),
        Route("GET", "/api/window/journal/{name}", journal_route),
        Route("GET", "/api/window/run/{run_id}", timeline_route),
        Route("GET", "/api/window/run/{run_id}/frame", frame_route),
        Route("GET", "/api/window/run/{run_id}/doc/{name}", document_route),
    )
