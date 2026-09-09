# -*- coding: utf-8 -*-
"""Статистика вызовов модели по группам действий: кэш, размеры, время, обрывы.

Зачем. Перед пилотом нового кадра (СТАТИСТИКА-КАДРА-08.09) нужен не «средний процент
кэша», а разрезы: по роду прогона (чат/группа/автономный/Forge/пробуждение), по роли и
модели, по первой итерации против продолжений, по тому, ЧТО модель сделала в этой
итерации (какой рукой ответила), по отпечатку кадра (frame_id) и его режиму. Всё это
уже лежит в `memory/runs/*/*/events.jsonl`: model_input (frame_id, mode, emit/reused),
model_completed (usage, duration_ms, stop_reason, role, model), tool_started (имя руки),
manifest.context (kind, model_profile). Этот файл только ЧИТАЕТ и считает.

Формула. Взвешенная доля кэша группы = Σ cache_read / Σ (in + cache_read) по всем вызовам
группы — не среднее процентов отдельных вызовов (тяжёлый первый кадр и лёгкое продолжение
весят по-разному). Рядом всегда число вызовов, Σ out, медиана времени и число обрывов
потолком: высокая доля кэша сама по себе не доказывает пользу.

Запуск:
  python frame_stats.py --tree <data или /app> [--days 7] [--since 2026-09-01] [--json out.json]
Осей можно выбрать: --by kind,role,model,iteration,hand,frame_mode,frame,epoch,day
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

AXES = ("kind", "role", "model", "iteration", "hand", "frame_mode", "frame", "day", "run")
DEFAULT_AXES = ("kind", "role", "model", "iteration", "hand", "frame_mode")


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
    """Один прогон → список вызовов модели с осями разреза. Ничего не додумывает:
    отсутствующее поле остаётся пустым, а не нулём."""
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
                "frame": str(meta.get("frame_id") or "")[:16],
                "frame_mode": str(meta.get("mode") or ("reused" if meta.get("reused") else "")),
                "emit": meta.get("emit"),
            }
        elif ek == "model_completed":
            ordinal += 1
            usage = ev.get("usage") or {}
            meta = frame_by_call.get(str(ev.get("call_id") or ""), {})
            current = {
                "run": str(ev.get("run_id") or run_dir.name),
                "kind": kind,
                "role": str(ev.get("role") or context.get("model_profile") or ""),
                "model": str(ev.get("model") or ""),
                "framework": str(ev.get("framework") or ""),
                "at": str(ev.get("at") or ""),
                "iteration": "first" if ordinal == 1 else "continuation",
                "ordinal": ordinal,
                "in": int(usage.get("in") or 0),
                "cached": int(usage.get("cache_read") or 0),
                "out": int(usage.get("out") or 0),
                "ms": int(ev.get("duration_ms") or 0),
                "stop": str(ev.get("stop_reason") or ""),
                "text_chars": int(ev.get("text_chars") or 0),
                "hand": "",
                "frame": meta.get("frame", ""),
                "frame_mode": meta.get("frame_mode", ""),
            }
            rows.append(current)
        elif ek == "tool_started" and current is not None:
            tool = str(ev.get("tool") or "")
            if tool and tool != "telegram.deliver" and not current["hand"]:
                # Первая рука после ответа модели — то, ЧТО она сделала этой итерацией.
                current["hand"] = tool
    for row in rows:
        if not row["hand"]:
            row["hand"] = "text" if row["text_chars"] else ("silence" if row["stop"] != "max_tokens" else "cut")
        row["day"] = row["at"][:10]
    return rows


def collect(tree: Path, *, since: datetime | None = None) -> list[dict]:
    runs_root = tree / "memory" / "runs"
    rows: list[dict] = []
    for run_dir in sorted(runs_root.glob("*/run-*")):
        if not run_dir.is_dir():
            continue
        if since is not None:
            # Имя прогона несёт UTC-время создания: дешёвый фильтр без чтения манифеста.
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
        out.append({
            "axis": axis, "group": key, "calls": len(items),
            "input_tokens": s_in, "cached_tokens": s_cached,
            "cache_ratio": round(s_cached / total, 3) if total else None,
            "output_tokens": sum(r["out"] for r in items),
            "median_ms": int(statistics.median(r["ms"] for r in items)) if items else 0,
            "p90_ms": int(sorted(r["ms"] for r in items)[max(0, int(len(items) * 0.9) - 1)]) if items else 0,
            "cuts": sum(1 for r in items if r["stop"] == "max_tokens"),
            "runs": len({r["run"] for r in items}),
        })
    out.sort(key=lambda g: (-g["calls"], g["group"]))
    return out


def render(groups_by_axis: dict[str, list[dict]], total: dict) -> str:
    lines = [f"# Вызовы модели: {total['calls']} вызовов в {total['runs']} прогонах, "
             f"кэш {total['cache_ratio'] if total['cache_ratio'] is not None else '—'} "
             f"(Σcached {total['cached_tokens']} / Σinput {total['input_tokens'] + total['cached_tokens']}), "
             f"out {total['output_tokens']}, обрывов {total['cuts']}", ""]
    for axis, groups in groups_by_axis.items():
        lines.append(f"## по {axis}")
        lines.append("| группа | вызовов | прогонов | кэш | вход всего | из кэша | ответ | медиана мс | p90 мс | обрывов |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for g in groups:
            ratio = "—" if g["cache_ratio"] is None else f"{g['cache_ratio']:.0%}"
            lines.append(f"| {g['group']} | {g['calls']} | {g['runs']} | {ratio} | "
                         f"{g['input_tokens'] + g['cached_tokens']} | {g['cached_tokens']} | {g['output_tokens']} | "
                         f"{g['median_ms']} | {g['p90_ms']} | {g['cuts']} |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", required=True, help="корень данных агента (там memory/runs)")
    parser.add_argument("--days", type=int, default=0, help="только прогоны за последние N дней")
    parser.add_argument("--since", default="", help="только прогоны с даты YYYY-MM-DD (UTC)")
    parser.add_argument("--by", default=",".join(DEFAULT_AXES), help="оси через запятую: " + ",".join(AXES))
    parser.add_argument("--json", default="", help="куда положить полный JSON (группы + строки)")
    args = parser.parse_args(argv)
    since = None
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    elif args.days:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)
    axes = [a.strip() for a in args.by.split(",") if a.strip()]
    unknown = [a for a in axes if a not in AXES]
    if unknown:
        parser.error(f"неизвестные оси: {unknown}; доступны {AXES}")
    rows = collect(Path(args.tree), since=since)
    groups_by_axis = {axis: group(rows, axis) for axis in axes}
    total = group(rows, "framework")  # любая ось: нужна только сумма
    summary = {
        "calls": len(rows), "runs": len({r["run"] for r in rows}),
        "input_tokens": sum(r["in"] for r in rows), "cached_tokens": sum(r["cached"] for r in rows),
        "output_tokens": sum(r["out"] for r in rows), "cuts": sum(1 for r in rows if r["stop"] == "max_tokens"),
    }
    denom = summary["input_tokens"] + summary["cached_tokens"]
    summary["cache_ratio"] = round(summary["cached_tokens"] / denom, 3) if denom else None
    del total
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    print(render(groups_by_axis, summary))
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": summary, "groups": groups_by_axis, "rows": rows},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"JSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
