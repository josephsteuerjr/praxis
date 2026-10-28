"""Ретенция прогонов `memory/runs` — решение Егора 12.09 («ретенцию проведи сам»).

Замер 12.09 на проде: 29,5 ГБ в 6 789 прогонах, и **28,7 ГБ из них — `results/`**: снимки
того, что уехало в модель и что она ответила (model-input/output), по одному на итерацию.
События (`events.jsonl`) — 0,5 ГБ на все три месяца, артефакты 0,4 ГБ, манифесты 28 МБ.
Незавершённых прогонов старше недели — ноль. Ретенции не было вовсе (её `frame_trace.py`
об этом говорил прямо), диск шёл к 93 % при +1 ГБ в сутки.

Правило (рычаги — переменные среды):

* `manifest.json` и всё, что не названо ниже, — навсегда: это история «что было».
* `results/*` старше `PRAXIS_RUNS_RAW_DAYS` (7) — удаляются: транспортные снимки, индекс
  их больше не читает (12.09), память их не хранит; расписки о ходе живут в манифесте
  и событиях.
* `events.jsonl` — навсегда (умолчание `PRAXIS_RUNS_EVENTS_DAYS=0`): их читают `list_active_runs`,
  сверка исходящих (`outstanding_tools`) и `read_run_result`; 13.09 первый проход с 60 днями
  снёс события 65 июльских прогонов, и оба читателя падали на них при каждом старте.
  Артефакты (`artifacts/`) старше `PRAXIS_RUNS_ARTIFACTS_DAYS` (60) — удаляются.
* Прогоны с нетерминальным статусом и прогоны, на которые ссылается открытая работа
  (`run_retention.discover_open_evidence_runs`), не трогаются. Если открытую работу
  прочитать не удалось — не трогается ничего: отсутствие ответа ≠ «можно».
* `PRAXIS_RUNS_RETENTION=off` выключает заботу целиком.

Зовётся ночью из `sleep.run()` и руками: `python runs_prune.py --apply` (без флага —
только считает). Возвращает отчёт числами; удалять начинает только с `apply=True`.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

log = logging.getLogger("praxis.runs_prune")

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
TERMINAL_STATUSES = frozenset({"done", "cancelled", "failed"})
# Имя прогона в тексте карточки или леджера: `run-<время>-<хвост>`.
_RUN_NAME = re.compile(r"run-\d{8}T\d{6}[0-9A-Za-z]*-[0-9a-f]+")


def enabled() -> bool:
    return str(os.getenv("PRAXIS_RUNS_RETENTION", "on") or "on").strip().lower() not in {
        "0", "off", "false", "no"}


def raw_days() -> float:
    try:
        return max(1.0, float(os.getenv("PRAXIS_RUNS_RAW_DAYS", "7") or 7))
    except ValueError:
        return 7.0


def events_days() -> float:
    """0 (умолчание) — события не удаляются никогда."""
    try:
        value = float(os.getenv("PRAXIS_RUNS_EVENTS_DAYS", "0") or 0)
    except ValueError:
        return 0.0
    return max(raw_days(), value) if value > 0 else 0.0


def artifacts_days() -> float:
    try:
        return max(raw_days(), float(os.getenv("PRAXIS_RUNS_ARTIFACTS_DAYS", "60") or 60))
    except ValueError:
        return 60.0


def run_stamp(name: str) -> _dt.datetime | None:
    """`run-20260912T174822518512Z-926e29fc` → момент создания (UTC) по имени каталога."""
    if not name.startswith("run-") or len(name) < 19:
        return None
    try:
        return _dt.datetime.strptime(name[4:19], "%Y%m%dT%H%M%S").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def _refs(value) -> set[str]:
    """Имена прогонов из значения любой формы: список, строка, вложенный текст."""
    found: set[str] = set()
    if isinstance(value, str):
        found.update(m.group(0) for m in _RUN_NAME.finditer(value))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            found |= _refs(item)
    elif isinstance(value, dict):
        for item in value.values():
            found |= _refs(item)
    return found


def _inside(base: Path, path: Path) -> bool:
    """Путь остаётся внутри дерева агента и не идёт через ссылку."""
    try:
        if path.is_symlink():
            return False
        real = path.resolve()
        return real == base.resolve() or base.resolve() in real.parents
    except OSError:
        return False


def _open_work_runs_here(base: Path) -> tuple[set[str], list[str]]:
    """Открытая работа — чтением ОБЫЧНЫМИ средствами, для издания на Windows.

    ⚠ ПОЧЕМУ НЕ ЯДЕРНЫЙ ПУТЬ. `run_retention.discover_open_evidence_runs` держит
    ворота на POSIX `openat` (`O_DIRECTORY|O_NOFOLLOW`, `dir_fd=`): он пришпиливает
    каждого предка, чтобы никто не подменил путь между проверкой и открытием. На
    Windows этих флагов нет вовсе — ядро в таком случае честно отвечает
    «compatibility: …», `protected_runs` возвращает `None`, и ретенция НЕ ДЕЛАЕТ
    НИЧЕГО. Ни исключения, ни красного: диск растёт молча. В издании это значит,
    что «ретенция есть» было бы неправдой.

    ⚠ ЧТО МЫ ЗДЕСЬ ДОПУСКАЕМ, ЧЕСТНО. Издание живёт на личном компьютере: одна
    машина, один пользователь, дерево внутри его профиля. Гонку «подменили каталог
    между проверкой и чтением» здесь может устроить только тот, кто и так пишет в
    это дерево, — а он может просто удалить прогоны. Поэтому проверяем то, что
    проверяемо без openat: не ссылка, путь после разрешения остаётся внутри дерева.
    Всё, что не прочиталось, по-прежнему делает проход НИЧЕГО НЕ УДАЛЯЮЩИМ: молчание
    источника — не разрешение.
    """
    found: set[str] = set()
    errors: list[str] = []
    tasks = base / "memory" / "work" / "tasks"
    if tasks.is_dir():
        try:
            cards = sorted(p / "TASK.md" for p in tasks.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError as exc:
            return found, [f"work/tasks: {type(exc).__name__}"]
        for card in cards:
            if not card.is_file():
                continue
            if not _inside(base, card):
                errors.append(f"work/tasks/{card.parent.name}: ссылка наружу")
                continue
            try:
                text = card.read_text(encoding="utf-8")
                lines = text.split("\n")
                fields: dict = {}
                if lines and lines[0] == "---" and "---" in lines[1:]:
                    for line in lines[1:lines.index("---", 1)]:
                        key, _, raw = line.partition(":")
                        if not _:
                            continue
                        try:
                            fields[key.strip()] = json.loads(raw.strip())
                        except ValueError:
                            fields[key.strip()] = raw.strip()
                status = str(fields.get("status") or "")
                # Незакрытая карточка — её прогоны не трогаем. Неизвестный статус
                # считаем открытым: ошибиться в эту сторону значит сохранить лишнее.
                if status in {"done", "dropped", "cancelled"}:
                    continue
                found |= _refs(fields.get("run_ids"))
                found |= _refs(fields.get("evidence"))
                found |= _refs(text)
            except (OSError, UnicodeError, ValueError) as exc:
                errors.append(f"work/tasks/{card.parent.name}: {type(exc).__name__}")
    ledger = base / "memory" / "desires" / "events.jsonl"
    if ledger.is_file() and _inside(base, ledger):
        latest: dict[str, dict] = {}
        try:
            for number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if isinstance(row, dict) and isinstance(row.get("desire_id"), str) and isinstance(row.get("state"), dict):
                    latest[row["desire_id"]] = row["state"]
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"desires/events.jsonl: {type(exc).__name__}")
        # Даже если хвост леджера повреждён, всё, что до него было прочитано
        # целиком, остаётся защищённым: испорченная строка не закрывает желание.
        for state in latest.values():
            if str(state.get("status") or "") not in {"satisfied", "released"}:
                found |= _refs(state.get("run_ids"))
                found |= _refs(state.get("evidence_refs"))
    elif ledger.exists() and not _inside(base, ledger):
        errors.append("desires/events.jsonl: ссылка наружу")
    return found, errors


def protected_runs(base: Path) -> set[str] | None:
    """Прогоны, нужные открытой работе. None — не удалось узнать (тогда не трогаем ничего)."""
    base = Path(base)
    try:
        import run_retention
        ids, errors = run_retention.discover_open_evidence_runs(base)
    except Exception as exc:  # noqa: BLE001 — любая неясность = fail closed
        log.warning("runs_prune: открытая работа не прочиталась: %s", exc)
        return None
    # ⚠ ИЗДАНИЕ. На Windows ядерный обход недоступен и отвечает «compatibility: …» —
    # это не поломка дерева, а отсутствие POSIX-примитива. Своим путём читаем те же
    # два источника; любая ДРУГАЯ ошибка по-прежнему значит «не трогаем ничего».
    if errors and all(str(e).startswith("compatibility:") for e in errors):
        ids, errors = _open_work_runs_here(base)
    if errors:
        log.warning("runs_prune: открытая работа не прочиталась: %s", "; ".join(errors)[:300])
        return None
    return set(ids)


def _status(run_dir: Path) -> str:
    try:
        with open(run_dir / "manifest.json", "r", encoding="utf-8") as fh:
            return str((json.load(fh) or {}).get("status") or "")
    except (OSError, ValueError):
        return ""


def _tree_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def prune(base: Path | None = None, *, now: _dt.datetime | None = None, apply: bool = True,
          budget_seconds: float | None = None) -> dict:
    """Один проход по всем месяцам. Отчёт — числа; при apply=False только считает."""
    base = Path(base or BASE)
    root = base / "memory" / "runs"
    now = now or _dt.datetime.now(_dt.timezone.utc)
    report = {"apply": bool(apply), "scanned": 0, "young": 0, "skipped_live": 0,
              "skipped_protected": 0, "results_runs": 0, "results_bytes": 0,
              "events_runs": 0, "events_bytes": 0, "artifacts_bytes": 0,
              "errors": [], "stopped_by_budget": False, "seconds": 0.0}
    started = time.monotonic()
    if not enabled():
        report["errors"].append("PRAXIS_RUNS_RETENTION=off")
        return report
    if not root.is_dir():
        return report
    protected = protected_runs(base)
    if protected is None:
        report["errors"].append("open-evidence discovery failed; nothing pruned")
        return report
    raw_cut = now - _dt.timedelta(days=raw_days())
    events_cut = (now - _dt.timedelta(days=events_days())) if events_days() > 0 else None
    artifacts_cut = now - _dt.timedelta(days=artifacts_days())
    for month_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for run_dir in sorted(p for p in month_dir.iterdir() if p.is_dir()):
            if budget_seconds is not None and time.monotonic() - started > budget_seconds:
                report["stopped_by_budget"] = True
                report["seconds"] = round(time.monotonic() - started, 1)
                return report
            stamp = run_stamp(run_dir.name)
            if stamp is None:
                continue
            report["scanned"] += 1
            if stamp > raw_cut:
                report["young"] += 1
                continue
            if _status(run_dir) not in TERMINAL_STATUSES:
                report["skipped_live"] += 1
                continue
            if run_dir.name in protected:
                report["skipped_protected"] += 1
                continue
            results = run_dir / "results"
            if results.is_dir():
                size = _tree_size(results)
                if size:
                    # ⚠ Байты засчитываются ПОСЛЕ удаления, а не до. На Windows файл,
                    # который держит открытым живой процесс, не удаляется — `rmtree`
                    # бросает, ошибка честно уходит в `errors`, а отчёт до этой правки
                    # всё равно рапортовал «снято N ГБ» при нуле снятого. Отчёт,
                    # который врёт в свою пользу, хуже отсутствующего.
                    gone = True
                    if apply:
                        try:
                            shutil.rmtree(results)
                        except OSError as exc:
                            gone = False
                            report["errors"].append(f"{run_dir.name}/results: {exc}")
                    if gone:
                        report["results_runs"] += 1
                        report["results_bytes"] += size
            if events_cut is not None and stamp <= events_cut:
                for name in ("events.jsonl", "events.jsonl.gz"):
                    path = run_dir / name
                    try:
                        size = path.stat().st_size
                    except OSError:
                        continue
                    gone = True
                    if apply:
                        try:
                            path.unlink()
                        except OSError as exc:
                            gone = False
                            report["errors"].append(f"{run_dir.name}/{name}: {exc}")
                    if gone:
                        report["events_runs"] += 1
                        report["events_bytes"] += size
            if stamp <= artifacts_cut:
                artifacts = run_dir / "artifacts"
                if artifacts.is_dir():
                    size = _tree_size(artifacts)
                    gone = True
                    if apply and size:
                        try:
                            shutil.rmtree(artifacts)
                        except OSError as exc:
                            gone = False
                            report["errors"].append(f"{run_dir.name}/artifacts: {exc}")
                    if gone:
                        report["artifacts_bytes"] += size
    report["seconds"] = round(time.monotonic() - started, 1)
    return report


def report_line(report: dict) -> str:
    gb = lambda n: f"{n / 1e9:.1f} ГБ" if n >= 1e8 else f"{n / 1e6:.0f} МБ"
    verb = "снято" if report.get("apply") else "к снятию"
    line = (f"прогоны: осмотрено {report.get('scanned', 0)}, {verb} снимков у "
            f"{report.get('results_runs', 0)} ({gb(report.get('results_bytes', 0))}), событий у "
            f"{report.get('events_runs', 0)} ({gb(report.get('events_bytes', 0))}), "
            f"живых пропущено {report.get('skipped_live', 0)}, под открытой работой "
            f"{report.get('skipped_protected', 0)}")
    if report.get("errors"):
        line += f"; ⚠ {len(report['errors'])} ошибок: {report['errors'][0][:120]}"
    if report.get("stopped_by_budget"):
        line += "; остановлено по бюджету времени, продолжу следующей ночью"
    return line


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ретенция memory/runs")
    ap.add_argument("--apply", action="store_true", help="удалять (без флага — только считать)")
    ap.add_argument("--base", default=str(BASE))
    ap.add_argument("--budget", type=float, default=None, help="секунд на проход")
    args = ap.parse_args(argv)
    out = prune(Path(args.base), apply=args.apply, budget_seconds=args.budget)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(report_line(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
