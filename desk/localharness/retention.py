# -*- coding: utf-8 -*-
"""Леджер ретенции рабочих материалов: что лежит, сколько весит, что убрано.

Решение владельца 01.10: данные агента и рабочие ресурсы разводятся; ретенция
папок проектов (в том числе вложений) и прочих вторичных материалов — отдельный
леджер. Права леджера намеренно узкие:

* проектные папки и runs-артефакты — ТОЛЬКО отчёт: что, сколько, когда трогали;
  удаление — слово владельца, не политика;
* однозначный мусор (staging старых обновлений, tmp) — убирается сам, каждая
  уборка пишется в леджер и журнал;
* модели и медиа-спул — отчёт (у спула свой TTL, у моделей — версионный вес).

Раскладка корней (цель, 1.2.9): программа — Program Files; данные —
%USERPROFILE%\\Helene; работа — %LOCALAPPDATA%\\Helene\\work. Сейчас всё лежит
в одном data/ у программы — леджер работает с обоими мирами: единый data/
классифицируется по тем же правилам, переезд меняет корни, не классы.

CLI:  python localharness/retention.py [--data ПУТЬ] [--work ПУТЬ] report|sweep
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

#: staging обновлений старше этого числа дней — мусор (обновление либо
#: завершилось, либо не завершится никогда; свежие 2 суток не трогаем на любом
#: возрасте, чтобы не спорить с идущим обновлением).
STAGING_TTL_DAYS = 14
STAGING_MIN_AGE_DAYS = 2

#: Имена workspace-папок, не являющихся проектами.
_NON_PROJECT = {"media", "tmp", "cache", ".cache"}

SCHEMA = "praxis.retention.v1"

LEDGER_REL = Path("memory/.state/retention.json")


def _tree_size(root: Path) -> tuple[int, int]:
    files = 0
    total = 0
    try:
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                try:
                    stat = os.stat(Path(dirpath) / name)
                except OSError:
                    continue
                files += 1
                total += stat.st_size
    except OSError:
        pass
    return total, files


def _age_days(stamp: float) -> float:
    return max(0.0, (time.time() - stamp) / 86400.0)


def classify(data: Path, work: Path | None = None) -> list[dict]:
    """Снимок вторичных материалов. Чтение, без изменений на диске."""
    roots = [("data", data)] + ([("work", work)] if work and work.is_dir() else [])
    entries: list[dict] = []
    for origin, root in roots:
        workspace = root / "workspace"
        if workspace.is_dir():
            for child in sorted(workspace.iterdir()):
                if not child.is_dir():
                    continue
                name = child.name
                if name.startswith("update-"):
                    entries.append(_entry(origin, child, "update_staging"))
                elif name.lower() in _NON_PROJECT:
                    entries.append(_entry(origin, child, "spool_cache"))
                else:
                    entries.append(_entry(origin, child, "project"))
        for sub, kind in (("models", "models"), ("memory/runs", "run_artifacts")):
            folder = root / sub
            if folder.is_dir():
                entries.append(_entry(origin, folder, kind))
        # Снимки перед обновлениями лежат РЯДОМ с data/ (и лягут рядом же после
        # переезда в профиль): главная часть «занятого пространства» не должна
        # пропадать из леджера — 02.10, слово владельца.
        backups = root.parent / "backups"
        if origin == "data" and backups.is_dir():
            entries.append(_entry(origin, backups, "backups"))
    return entries


def _entry(origin: Path, folder: Path, kind: str) -> dict:
    total, files = _tree_size(folder)
    try:
        mtime = folder.stat().st_mtime
    except OSError:
        mtime = 0.0
    return {"class": kind, "origin": str(origin), "path": str(folder),
            "bytes": total, "files": files, "age_days": round(_age_days(mtime), 1),
            "sweepable": kind == "update_staging"
            and _age_days(mtime) >= STAGING_TTL_DAYS
            and _age_days(mtime) >= STAGING_MIN_AGE_DAYS}


def sweep(data: Path, work: Path | None = None) -> dict:
    """Убрать однозначный мусор, записать леджер. Возврат — сам леджер."""
    entries = classify(data, work)
    removed = []
    for entry in entries:
        if not entry["sweepable"]:
            continue
        folder = Path(entry["path"])
        try:
            import shutil
            shutil.rmtree(folder)
            removed.append({"path": entry["path"], "bytes": entry["bytes"],
                            "files": entry["files"]})
        except OSError:
            continue  # занято/нет прав — останется в леджере на следующий раз
    ledger = {"schema": SCHEMA, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "data_root": str(data), "work_root": str(work) if work else "",
              "entries": [e for e in entries
                          if not any(r["path"] == e["path"] for r in removed)],
              "removed": removed,
              "policies": {
                  "update_staging": f"auto >= {STAGING_TTL_DAYS} дней (и не моложе {STAGING_MIN_AGE_DAYS})",
                  "project": "только отчёт; удаление — слово владельца",
                  "run_artifacts": "только отчёт; отдельная политика по медиа — после переезда раскладки",
                  "models": "только отчёт",
                  "backups": "только отчёт; снимки чистит владелец руками",
                  "spool_cache": "только отчёт (у спула свой TTL)"}}
    _write_ledger(data, ledger)
    return ledger


def _write_ledger(data: Path, ledger: dict) -> None:
    target = data / LEDGER_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf8")
    os.replace(tmp, target)


def load_ledger(data: Path) -> dict | None:
    try:
        return json.loads((data / LEDGER_REL).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="леджер ретенции рабочих материалов")
    ap.add_argument("--data", default=str(Path(__file__).resolve().parents[1] / "data"),
                    help="корень данных агента (по умолчанию — data/ у программы)")
    ap.add_argument("--work", default="", help="корень рабочих материалов (если уже разнесён)")
    ap.add_argument("action", choices=["report", "sweep"])
    args = ap.parse_args(argv)
    data = Path(args.data)
    work = Path(args.work) if args.work else None
    if args.action == "report":
        entries = classify(data, work)
        total = sum(e["bytes"] for e in entries)
        for e in sorted(entries, key=lambda e: -e["bytes"]):
            mark = "УБРАТЬ" if e["sweepable"] else " "
            print(f"{mark} {e['bytes']/1048576:8.1f} МБ  {e['files']:6d} файл.  "
                  f"{e['class']:14} {e['path']}")
        print(f"итого {total/1048576:.1f} МБ в {len(entries)} папках "
              f"(проекты и runs — только отчёт, не удаляются)")
        return 0
    ledger = sweep(data, work)
    print(f"убрано {len(ledger['removed'])} staging-папок; леджер: "
          f"{data / LEDGER_REL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
