# -*- coding: utf-8 -*-
"""Разрезы кэша и времени по группам действий для экрана «Система».

Только чтение `memory/runs/*/*/events.jsonl` — тех же событий, что читает карточка хода.
Считается взвешенная доля кэша группы: Σ cache_read / Σ (in + cache_read) по всем вызовам
группы, а не среднее процентов отдельных вызовов (тяжёлый первый кадр и лёгкое продолжение
весят по-разному). Рядом всегда число вызовов, Σ ответ, медиана времени и обрывы потолком.

Это проекция того же прибора, что `tree/frame_stats.py` у агента (КАДР-УЧЁТ-08.09): Пульт
читает данные сам, чтобы не зависеть от пути к коду дерева и не импортировать чужой модуль.
"""
from __future__ import annotations

import collections
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

AXES = ("kind", "role", "model", "iteration", "hand", "frame_mode", "day")


def _iter_events(path: Path):
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def calls_from_run(run_dir: Path) -> list[dict]:
    """Прогон → вызовы модели с осями разреза. Отсутствующее поле остаётся пустым, не нулём."""
    manifest = _load_json(run_dir / "manifest.json")
    context = manifest.get("context") or {}
    kind = str(context.get("kind") or "")
    rows: list[dict] = []
    frame_by_call: dict[str, dict] = {}
    current: dict | None = None
    ordinal = 0
    for ev in _iter_events(run_dir / "events.jsonl"):
        ek = ev.get("kind")
        if ek == "model_input":
            meta = ev.get("metadata")
            if not isinstance(meta, dict):
                meta = ((ev.get("result") or {}).get("metadata") or {})
            frame_by_call[str(ev.get("call_id") or "")] = {
                "frame_mode": str(meta.get("mode") or ("reused" if meta.get("reused") else "")),
            }
        elif ek == "model_completed":
            ordinal += 1
            usage = ev.get("usage") or {}
            meta = frame_by_call.get(str(ev.get("call_id") or ""), {})
            current = {
                "run": str(ev.get("run_id") or run_dir.name), "kind": kind,
                "role": str(ev.get("role") or context.get("model_profile") or ""),
                "model": str(ev.get("model") or ""), "at": str(ev.get("at") or ""),
                "iteration": "первая" if ordinal == 1 else "продолжение",
                "in": int(usage.get("in") or 0), "cached": int(usage.get("cache_read") or 0),
                "out": int(usage.get("out") or 0), "ms": int(ev.get("duration_ms") or 0),
                "stop": str(ev.get("stop_reason") or ""), "text_chars": int(ev.get("text_chars") or 0),
                "hand": "", "frame_mode": meta.get("frame_mode", ""),
            }
            rows.append(current)
        elif ek == "tool_started" and current is not None:
            tool = str(ev.get("tool") or "")
            if tool and tool != "telegram.deliver" and not current["hand"]:
                current["hand"] = tool  # первая рука после ответа — то, что сделано этой итерацией
    for row in rows:
        if not row["hand"]:
            row["hand"] = "слово" if row["text_chars"] else ("обрыв" if row["stop"] == "max_tokens" else "молчание")
        row["day"] = row["at"][:10]
    return rows


def collect(tree: Path, *, days: int = 7) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    rows: list[dict] = []
    root = tree / "memory" / "runs"
    if not root.is_dir():
        return rows
    for run_dir in sorted(root.glob("*/run-*")):
        if not run_dir.is_dir():
            continue
        stamp = run_dir.name.split("-")[1][:15] if "-" in run_dir.name else ""
        try:
            created = datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            created = None
        if created is not None and created < since:
            continue
        rows.extend(calls_from_run(run_dir))
    return rows


def group(rows: list[dict], axis: str) -> list[dict]:
    buckets: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        buckets[str(row.get(axis) or "—")].append(row)
    out = []
    for key, items in buckets.items():
        s_in = sum(r["in"] for r in items)
        s_cached = sum(r["cached"] for r in items)
        total = s_in + s_cached
        ms = sorted(r["ms"] for r in items)
        out.append({
            "group": key, "calls": len(items), "runs": len({r["run"] for r in items}),
            "input_tokens": total, "cached_tokens": s_cached,
            "cache_ratio": round(s_cached / total, 3) if total else None,
            "output_tokens": sum(r["out"] for r in items),
            "median_ms": int(statistics.median(ms)) if ms else 0,
            "p90_ms": ms[max(0, int(len(ms) * 0.9) - 1)] if ms else 0,
            "cuts": sum(1 for r in items if r["stop"] == "max_tokens"),
        })
    out.sort(key=lambda g: (-g["calls"], g["group"]))
    return out


def cuts(tree: Path, *, days: int = 7) -> dict:
    """Готовый ответ для /api/frame-stats: сводка и разрезы по осям."""
    rows = collect(tree, days=days)
    s_in = sum(r["in"] for r in rows)
    s_cached = sum(r["cached"] for r in rows)
    total = s_in + s_cached
    return {
        "days": int(days),
        "summary": {
            "calls": len(rows), "runs": len({r["run"] for r in rows}),
            "input_tokens": total, "cached_tokens": s_cached,
            "cache_ratio": round(s_cached / total, 3) if total else None,
            "output_tokens": sum(r["out"] for r in rows),
            "cuts": sum(1 for r in rows if r["stop"] == "max_tokens"),
        },
        "by": {axis: group(rows, axis) for axis in AXES if axis != "day"},
    }
