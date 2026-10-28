# -*- coding: utf-8 -*-
"""Ревизия расхода модели по чатам, людям и задачам. ТОЛЬКО чтение.

Слово владельца 08.09: «хочу иметь возможность проводить ревизию расхода по
задачам, чатам и людям, которые общаются с Праксис». Журнал вызовов
`memory/.state/llm_calls.jsonl` (пишет live/llm.py) знает прогон (`run`), с 09.09
— ещё чат (`chat`), человека (`who`) и задачу Forge (`task`). Для строк старого
ядра эти три поля восстанавливаются через манифест прогона:
`context.origin_chat_id` / `delivery_chat_id` / `principal_id` / `forge_task_id`.

Что здесь считается:
- «задача» = прогон (один ход в чате, одно окно задачи, одно событие Forge);
  задачи Forge отдельной осью появятся, когда ядро начнёт писать `task`
  (сегодня forge_task_id в манифестах всегда пуст — см. отчёт 09.09);
- «чат» = Telegram-место или комната окна (имя — readers._title_for);
- «человек» = principal_id прогона: Telegram-id того, кому она отвечала,
  `praxis:self` — её собственные ходы (будильник, задачи, Forge). Имена — из
  known_ids.json и хвоста turns.jsonl (who ↔ run_id); незнакомый id так и
  показывается числом;
- вызовы без прогона (судья/evaluator вне хода, forge_worker, реле-пробы) —
  отдельная корзина «вне прогона» по ролям: их не приписать ни чату, ни
  человеку, и делать вид иначе нельзя.

Денег нет: прайс-листа по живым моделям в дереве нет (memory/llm.json знает
одну gpt-5.5), а выдуманные цены хуже токенов.
"""
from __future__ import annotations

import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from deskd import readers

SELF_PRINCIPAL = "praxis:self"
MAX_ROWS_PER_FILE = 200_000
TOP_RUNS = 40


def _int(v, default: int = 0) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return default


def _run_manifest(tree: Path, run_id: str, cache: dict[str, dict]) -> dict:
    if run_id in cache:
        return cache[run_id]
    m = readers._RUN_ID_RE.match(run_id or "")
    ctx: dict = {}
    if m:
        path = tree / "memory" / "runs" / f"{m.group(1)}-{m.group(2)}" / run_id / "manifest.json"
        data = readers._load_json(path)
        ctx = {"context": data.get("context") or {}, "status": str(data.get("status") or ""),
               "terminal": (data.get("terminal") or {}).get("status") or ""}
    cache[run_id] = ctx
    return ctx


def _owner_id(tree: Path) -> str:
    env = str(os.environ.get("HELENE_TG_OWNER_ID") or os.environ.get("PRAXIS_OWNER_ID") or "").strip()
    if env:
        return env
    cfg = readers._load_json(readers.config_path()) if readers.config_path() else {}
    return str(((cfg.get("telegram") or {}).get("owner_id")) or "").strip()


def _who_by_run(tree: Path) -> dict[str, str]:
    """run_id -> имя собеседника из хвоста turns.jsonl (только для подписи)."""
    out: dict[str, str] = {}
    for row in readers.tail_jsonl(tree / "memory" / ".state" / "turns.jsonl", 4000):
        run = str(row.get("run_id") or "")
        who = str(row.get("who") or "").strip()
        if run and who and run not in out:
            out[run] = who
    return out


class _Bucket:
    __slots__ = ("calls", "in_", "cached", "out", "runs", "kinds", "models", "ms", "errors", "extra")

    def __init__(self) -> None:
        self.calls = 0
        self.in_ = 0
        self.cached = 0
        self.out = 0
        self.runs: set[str] = set()
        self.kinds: Counter = Counter()
        self.models: Counter = Counter()
        self.ms = 0
        self.errors = 0
        self.extra: dict[str, Any] = {}

    def add(self, row: dict, run: str) -> None:
        self.calls += 1
        self.in_ += _int(row.get("in"))
        self.cached += _int(row.get("cached"))
        self.out += _int(row.get("out"))
        self.ms += _int(row.get("ms"))
        if run:
            self.runs.add(run)
        if row.get("kind"):
            self.kinds[str(row["kind"])] += 1
        if row.get("model"):
            self.models[str(row["model"])] += 1
        if row.get("ok") is False or (row.get("err") and row.get("err") != "fallback"):
            self.errors += 1

    def row(self) -> dict:
        total_in = self.in_ + self.cached
        return {
            "calls": self.calls, "runs": len(self.runs),
            "input_tokens": total_in, "cached_tokens": self.cached, "fresh_tokens": self.in_,
            "output_tokens": self.out, "total_tokens": total_in + self.out,
            "cache_ratio": (self.cached / total_in) if total_in else None,
            "seconds": round(self.ms / 1000, 1), "errors": self.errors,
            "kinds": dict(self.kinds.most_common(4)), "models": dict(self.models.most_common(3)),
            **self.extra,
        }


def collect(tree: Path, days: int = 7) -> dict:
    days = max(1, min(int(days or 7), 90))
    since = time.time() - days * 86400
    state = tree / "memory" / ".state"
    rows: list[dict] = []
    for name in ("llm_calls.jsonl.1", "llm_calls.jsonl"):
        path = state / name
        if path.is_file():
            rows.extend(readers.tail_jsonl(path, MAX_ROWS_PER_FILE))
    rows = [r for r in rows if readers._num(r.get("ts")) >= since]

    manifests: dict[str, dict] = {}
    titles = readers.chat_titles()
    stable = readers._room_titles()
    owner = _owner_id(tree)
    who_by_run: dict[str, str] | None = None

    by_chat: dict[str, _Bucket] = {}
    by_person: dict[str, _Bucket] = {}
    by_run: dict[str, _Bucket] = {}
    by_kind: dict[str, _Bucket] = {}
    unbound: dict[str, _Bucket] = {}
    total = _Bucket()
    bound_calls = 0

    for row in rows:
        run = str(row.get("run") or "")
        total.add(row, run)
        if not run:
            role = str(row.get("role") or "?")
            unbound.setdefault(role, _Bucket()).add(row, "")
            continue
        bound_calls += 1
        ctx = _run_manifest(tree, run, manifests).get("context") or {}
        chat = str(row.get("chat") or ctx.get("origin_chat_id") or ctx.get("delivery_chat_id") or "")
        who = str(row.get("who") or ctx.get("principal_id") or "")
        kind = str(row.get("kind") or ctx.get("kind") or "")
        if kind and not row.get("kind"):
            row = {**row, "kind": kind}
        by_kind.setdefault(kind or "?", _Bucket()).add(row, run)
        b = by_run.setdefault(run, _Bucket())
        b.add(row, run)
        if not b.extra:
            goal = str(ctx.get("goal") or "").strip()
            b.extra = {"run_id": run, "kind": kind, "chat_id": chat, "who": who,
                       "goal_head": goal.splitlines()[0][:140] if goal else "",
                       "first_ts": readers._num(row.get("ts")), "status": _run_manifest(tree, run, manifests).get("status", "")}
        b.extra["last_ts"] = readers._num(row.get("ts"))
        if chat:
            by_chat.setdefault(chat, _Bucket()).add(row, run)
        else:
            by_chat.setdefault("", _Bucket()).add(row, run)
        by_person.setdefault(who or "?", _Bucket()).add(row, run)

    def chat_row(chat: str, b: _Bucket) -> dict:
        title = readers._title_for(chat, titles) if chat else ""
        if not title and chat:
            title = stable.get(chat, "")
        return {"chat_id": chat, "title": title or ("без чата (окно задачи, будильник, Forge)" if not chat else ""), **b.row()}

    def person_name(who: str, b: _Bucket) -> str:
        if who == SELF_PRINCIPAL:
            return "сама, без человека"
        if who in ("", "?"):
            return "не записан"
        if owner and who == owner:
            return "владелец"
        if who in stable:
            return stable[who]
        nonlocal who_by_run
        if who_by_run is None:
            who_by_run = _who_by_run(tree)
        for run in sorted(b.runs, reverse=True):
            if run in who_by_run:
                return who_by_run[run]
        return ""

    def person_row(who: str, b: _Bucket) -> dict:
        r = b.row()
        # Чаты, где этот человек её звал: по прогонам корзины.
        chats: Counter = Counter()
        for run in b.runs:
            ctx = manifests.get(run, {}).get("context") or {}
            c = str(ctx.get("origin_chat_id") or ctx.get("delivery_chat_id") or "")
            if c:
                chats[readers._title_for(c, titles) or stable.get(c, "") or c] += 1
        return {"who": who, "name": person_name(who, b), "chats": [k for k, _ in chats.most_common(3)], **r}

    def run_row(run: str, b: _Bucket) -> dict:
        r = b.row()
        chat = r.get("chat_id") or ""
        r["chat_title"] = readers._title_for(chat, titles) or stable.get(chat, "") if chat else ""
        r["who_name"] = person_name(str(r.get("who") or ""), b)
        return r

    by_tokens = lambda item: -item[1]["total_tokens"]  # noqa: E731
    chats_out = sorted((chat_row(k, b) for k, b in by_chat.items()), key=lambda r: -r["total_tokens"])
    people_out = sorted((person_row(k, b) for k, b in by_person.items()), key=lambda r: -r["total_tokens"])
    runs_out = sorted((run_row(k, b) for k, b in by_run.items()), key=lambda r: -r["total_tokens"])[:TOP_RUNS]
    kinds_out = sorted(({"kind": k, **b.row()} for k, b in by_kind.items()), key=lambda r: -r["total_tokens"])
    unbound_out = sorted(({"role": k, **b.row()} for k, b in unbound.items()), key=lambda r: -r["total_tokens"])
    del by_tokens
    summary = total.row()
    summary.update({"bound_calls": bound_calls, "unbound_calls": total.calls - bound_calls,
                    "fields_in_log": {"chat": any(r.get("chat") for r in rows),
                                      "who": any(r.get("who") for r in rows),
                                      "task": any(r.get("task") for r in rows)}})
    return {"days": days, "summary": summary, "by_chat": chats_out, "by_person": people_out,
            "by_run": runs_out, "by_kind": kinds_out, "unbound": unbound_out}
