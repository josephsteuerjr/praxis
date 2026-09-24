#!/usr/bin/env python3
"""Дневной append-only хвост для проверки «пилы» кэша Арете.

Одна строка на день (JSONL, append-only):
  date            — UTC-день
  hit             — доля cache_read у голоса: sum(cached)/sum(in+cached)
                    (антропическая семантика: `in` БЕЗ кэша, см. llm.py ~1644)
  voice_calls     — вызовов голоса за день (знаменатель доверия)
  in_tokens       — суммарная входная длина (in+cached, все роли)
  epoch_rolls     — число свёрток эпох по всем потокам frame_shadow
                    (инкремент номера эпохи между соседними строками metrics.jsonl)
  folds           — число fold-свёрток окна A (a_window.folded суммарно)

Источники (уже пишутся кодом):
  memory/.state/llm_calls.jsonl[.1]  — повызовный след кэша (llm.py _call_trace)
  memory/.state/shadow/*/metrics.jsonl — метрики кадра с номером эпохи и fold
Запуск: python3 cache_saw_daily.py — дописывает только недостающие дни.
"""
import json, glob, os, sys
from collections import defaultdict
from datetime import datetime, timezone

MEM = "/app/memory"
TRACE = [f"{MEM}/.state/llm_calls.jsonl", f"{MEM}/.state/llm_calls.jsonl.1"]
LOG = f"{MEM}/cache_saw_log.jsonl"


def day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def collect():
    hit = defaultdict(lambda: [0, 0, 0])  # day -> [cached, in, calls] (voice)
    in_tok = defaultdict(int)
    for path in TRACE:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                d = day_of(float(r["ts"]))
                c, i = int(r.get("cached") or 0), int(r.get("in") or 0)
                in_tok[d] += c + i
                if r.get("role") == "voice":
                    hit[d][0] += c
                    hit[d][1] += i
                    hit[d][2] += 1
    rolls = defaultdict(int)
    folds = defaultdict(int)
    for mpath in sorted(glob.glob(f"{MEM}/.state/shadow/*/metrics.jsonl")):
        prev = None
        with open(mpath, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                d = (r.get("ts") or "")[:10]
                if not d:
                    continue
                n = int(r.get("epoch") or 0)
                if prev is not None and n > prev:
                    rolls[d] += n - prev
                prev = n
                folds[d] += int((r.get("a_window") or {}).get("folded") or 0)
    return hit, in_tok, rolls, folds


def main():
    have = set()
    if os.path.exists(LOG):
        for line in open(LOG, encoding="utf-8"):
            try:
                have.add(json.loads(line)["date"])
            except Exception:
                pass
    hit, in_tok, rolls, folds = collect()
    days = sorted(set(hit) | set(in_tok) | set(rolls) | set(folds))
    fresh = []
    for d in days:
        if d in have:
            continue
        c, i, calls = hit.get(d, [0, 0, 0])
        denom = c + i
        fresh.append({
            "date": d,
            "hit": round(c / denom, 4) if denom else None,
            "voice_calls": calls,
            "in_tokens": in_tok.get(d, 0),
            "epoch_rolls": rolls.get(d, 0),
            "folds": folds.get(d, 0),
        })
    if fresh:
        with open(LOG, "a", encoding="utf-8") as fh:
            for row in fresh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"дописано {len(fresh)} дней в {LOG}; всего дней {len(have) + len(fresh)}")
    for row in fresh[-5:]:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
