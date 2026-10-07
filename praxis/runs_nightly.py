"""Ночная память о том, как работа ПРОШЛА, — не о том, что говорилось.

07.10 (ТЗ Егора → oro/runs-nightly-0710). Дыра: вход формирования
(formation.pending_compacts) берёт только компакты ЖУРНАЛА — диалоги. Как реально
прошли Forge-задачи (вердикт против контракта, перезапуски против
skipped_already_green, отложенные финиши) в материал, из которого она ночью
делает выводы о себе, не входит: характер ревизуется по словам, не по делу.

Этот модуль — сборщик дайджестов из durable-материала задач
(``memory/.forge/tasks/<id>/``: task.json, verifications/, agents/, events.jsonl).
НЕ сырые трейсы: только типизированные поля, собранные кодом, — рефлексия по
фактам, не по припоминанию (урок OWN: ALL-CALLS трейс, а не скан слов).

Границы (её AGENTS.md + ТЗ):
* рычаг PRAXIS_RUNS_NIGHTLY = off | dry | on, умолчание off — закон 2: поведенческий
  дефолт включает только она. dry — репетиция: дайджест считается и пишется рядом
  с задачей (RUN-DIGEST.md), в формирование НЕ попадает; on — ещё и публикует
  дайджесты formation-готовым фронтиром (``memory/.state/life/formation_runs.json`` —
  тот же каталог, который глобит formation._frontier_metas),
  откуда formation.pending_compacts берёт их как обычные источники.
* Вердикт контракта — из ЕДИНОГО источника: forge._contract_evidence +
  forge_learning.contract_status (те же, что finish и урок). Никаких вторых
  суждений о «выполнено ли».
* fact/interpretation: всё наблюдаемое лежит в ``observed``, всё выведенное
  кодом (класс эпизода) — в ``inferred``; сила — как в маркерах PR #2.
* Свёртка с дедупом: класс ошибки подтверждается счётчиком в реестре
  (``memory/.forge/patterns.jsonl``), а не пятым конкурентным уроком; эпизод с
  уже записанным уроком (forge_learning) помечается, урок не дублируется.
* Канон не пишем: люди/граф — только через formation claims. Этот модуль пишет
  только дайджесты, реестр классов и квитанции ночи.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger("praxis-runs-nightly")

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
FORGE_STATE = BASE / "memory" / ".forge"
TASKS_DIR = FORGE_STATE / "tasks"
PATTERNS_PATH = FORGE_STATE / "patterns.jsonl"
# Фронтир дайджестов: тот же файл, который читает formation.pending_compacts
# (life.STATE_DIR / "formation_runs.json", см. formation.py::_pending_run_digests).
# Импорт на уровне модуля создал бы цикл praxis.runs_nightly -> formation ->
# (heartbeat и др.), поэтому путь выводится из memory_life.STATE_DIR — SSOT каталога.
import memory_life as _life  # noqa: E402  (после BASE, до констант)

FORMATION_RUNS_PATH = _life.STATE_DIR / "formation_runs.json"

#: окно отбора задач: терминальные finished за последние N часов (чуть больше
#: суток, чтобы «догонная» ночь после пропущенной тоже видела вчерашнее).
WINDOW_H = float(os.getenv("PRAXIS_RUNS_NIGHTLY_WINDOW_H", "26") or 26)
#: сколько задач попадает в один ночной срез (остальные — следующей ночью).
MAX_TASKS = int(os.getenv("PRAXIS_RUNS_NIGHTLY_MAX_TASKS", "12") or 12)
#: потолок текста одного дайджеста в источнике формирования.
DIGEST_TEXT_CAP = 2500

_TERMINAL_TASK = {"done", "failed", "abandoned", "lost"}
_VERDICT_DONE, _VERDICT_FAIL, _VERDICT_UNKNOWN = "выполнен", "не выполнен", "неизвестно"


def mode() -> str:
    """off | dry | on — точные значения после strip/lower; умолчание off."""
    value = str(os.getenv("PRAXIS_RUNS_NIGHTLY", "off") or "").strip().lower()
    return value if value in {"off", "dry", "on"} else "off"


def _read_json(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return default


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(value) -> float:
    try:
        return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def recent_finished_tasks(*, tasks_dir: Path | None = None,
                          now: float | None = None) -> list[dict]:
    """Терминальные задачи с finished внутри окна. -> строки task.json (свежие позже)."""
    root = Path(tasks_dir) if tasks_dir else TASKS_DIR
    now = now if now is not None else time.time()
    out: list[tuple[float, dict]] = []
    if not root.is_dir():
        return []
    for task_file in root.glob("*/task.json"):
        task = _read_json(task_file, {})
        if not isinstance(task, dict) or not task.get("id"):
            continue
        if str(task.get("status") or "") not in _TERMINAL_TASK:
            continue
        finished = _epoch(task.get("finished"))
        if not finished or now - finished > WINDOW_H * 3600.0:
            continue
        out.append((finished, task))
    out.sort(key=lambda item: item[0], reverse=True)
    return [task for _ts, task in out[:MAX_TASKS]]


def _matrix_stats(task_id: str, *, tasks_dir: Path | None = None) -> dict:
    """Типизированная статистика матриц задачи — «куча тестов» в числах."""
    root = (Path(tasks_dir) if tasks_dir else TASKS_DIR) / str(task_id) / "verifications"
    units = 0
    checks_run = 0
    skipped_green = 0
    failed = 0
    timed_out = 0
    if root.is_dir():
        for result_file in sorted(root.glob("*/result.json")):
            result = _read_json(result_file, {})
            if not isinstance(result, dict):
                continue
            units += 1
            for row in result.get("checks") or []:
                status = str(row.get("status") or "")
                if status == "skipped_already_green":
                    skipped_green += 1
                elif status == "passed":
                    checks_run += 1
                elif status == "failed":
                    failed += 1
                elif status == "timed_out":
                    timed_out += 1
    return {"units": units, "checks_run": checks_run,
            "skipped_already_green": skipped_green,
            "failed": failed, "timed_out": timed_out}


def _worker_outcomes(task_id: str, *, tasks_dir: Path | None = None) -> list[dict]:
    """Исходы воркеров задачи — по одному полю на воркера, без текстов результата."""
    root = (Path(tasks_dir) if tasks_dir else TASKS_DIR) / str(task_id) / "agents"
    out = []
    if root.is_dir():
        for agent_id in sorted(p.name for p in root.iterdir() if p.is_dir()):
            result = _read_json(root / agent_id / "result.json", {})
            if isinstance(result, dict):
                out.append({"agent": agent_id,
                            "role": str(result.get("role") or "worker"),
                            "status": str(result.get("status") or "")})
    return out


def _deferred_finish(task: dict, workers: list[dict]) -> bool:
    """Воркер закончил, а задача осталась active — отложенный финиш (класс из ТЗ)."""
    if str(task.get("status") or "") != "active":
        return False
    return any(w.get("status") in {"done", "failed", "error"} for w in workers)


def digest_task(task: dict, *, tasks_dir: Path | None = None) -> dict:
    """Типизированный дайджест одной задачи: только факты + явный вывод класса.

    observed — прочитанное из durable-материала; inferred — выведенное кодом
    правило (класс эпизода). Вердикт контракта — из единого источника:
    forge._contract_evidence + forge_learning.contract_status.
    """
    import forge
    import forge_learning
    task_id = str(task.get("id") or "")
    try:
        evidence = forge._contract_evidence(task, forge._units(task_id, "verifications"))
        verdict = forge_learning.contract_status(evidence) if evidence else _VERDICT_UNKNOWN
        verify = (evidence or {}).get("verify") or {}
    except Exception:
        log.debug("вердикт контракта %s не посчитался", task_id, exc_info=True)
        verdict, verify = _VERDICT_UNKNOWN, {}
    workers = _worker_outcomes(task_id, tasks_dir=tasks_dir)
    matrix = _matrix_stats(task_id, tasks_dir=tasks_dir)
    criteria = [str(c).strip() for c in (task.get("success_criteria") or []) if str(c).strip()]
    commands = [str(c).strip() for c in (task.get("verify_commands") or []) if str(c).strip()]
    observed = {
        "task_id": task_id,
        "goal": str(task.get("goal") or "")[:200],
        "task_status": str(task.get("status") or ""),
        "contract": {"criteria": len(criteria), "commands": len(commands)},
        "verdict": verdict,
        "verify": {k: int(verify.get(k) or 0) for k in ("met", "unmet", "unknown")},
        "matrix": matrix,
        "workers": workers,
        "deferred_finish": _deferred_finish(task, workers),
    }
    return {"schema": "praxis.runs-nightly.digest.v1", "at": _utc_now_iso(),
            "observed": observed, "inferred": {"episode_class": classify(observed)}}


def classify(observed: dict) -> str:
    """Детерминированный класс эпизода по фактам. Пустой класс — «не ошибка»."""
    if observed.get("deferred_finish"):
        return "deferred_finish"
    if observed.get("task_status") == "lost":
        return "lost_task"
    if observed.get("verdict") == _VERDICT_FAIL:
        return "unmet_contract"
    if observed.get("task_status") in {"failed", "abandoned"}:
        return f"task_{observed['task_status']}"
    if (observed.get("matrix") or {}).get("checks_run", 0) >= 6 and \
            not (observed.get("matrix") or {}).get("skipped_already_green"):
        return "rerun_storm"
    return ""


# --------------------------------------------------------------------------- #
#  Свёртка: реестр классов с дедупом против уже записанных уроков
# --------------------------------------------------------------------------- #

def _read_lessons_task_ids(lessons_path: Path) -> set[str]:
    ids: set[str] = set()
    try:
        for line in lessons_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("task_id"):
                ids.add(str(row["task_id"]))
    except OSError:
        pass
    return ids


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _pattern_rows() -> list[dict]:
    rows: list[dict] = []
    try:
        for line in PATTERNS_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("class"):
                rows.append(row)
    except OSError:
        pass
    return rows


def collapse(digests: list[dict], *, lessons_path: Path | None = None) -> dict:
    """Реестр классов: подтверждённый класс поднимает счётчик, а не новый урок.

    Эпизод с уже записанным уроком (task_id в forge_learning lessons.jsonl)
    считается (класс жив), но помечается ``already_lessoned`` — конкурентный урок
    не плодится. Итог — append-only строка реестра на КАЖДЫЙ эпизод (журнал
    наблюдений) + сводка для квитанции ночи; «текущее состояние класса» читается
    из журнала свёрткой (count = строк класса), без второго стора.
    """
    lessons_path = lessons_path or (FORGE_STATE / "lessons.jsonl")
    lessoned = _read_lessons_task_ids(lessons_path)
    recorded = 0
    already = 0
    skipped_clean = 0
    for digest in digests:
        observed = digest.get("observed") or {}
        klass = str((digest.get("inferred") or {}).get("episode_class") or "")
        task_id = str(observed.get("task_id") or "")
        if not klass:
            skipped_clean += 1      # эпизод без класса — не ошибка, реестр не трогаем
            continue
        had_lesson = task_id in lessoned
        row = {"schema": "praxis.runs-nightly.pattern.v1", "at": _utc_now_iso(),
               "class": klass, "task_id": task_id,
               "verdict": observed.get("verdict"),
               "matrix": observed.get("matrix"),
               "already_lessoned": had_lesson,
               # структурная фиксация и статус — её слово; код только копит
               # эпизоды (счётчик = число строк класса в журнале).
               "note": ""}
        _append_jsonl(PATTERNS_PATH, row)
        recorded += 1
        already += 1 if had_lesson else 0
    return {"digests": len(digests), "episodes_recorded": recorded,
            "already_lessoned": already, "clean": skipped_clean}


def pattern_counts() -> dict[str, int]:
    """Текущий счётчик классов — свёртка журнала, не второй стор."""
    counts: dict[str, int] = {}
    for row in _pattern_rows():
        counts[str(row["class"])] = counts.get(str(row["class"]), 0) + 1
    return counts


# --------------------------------------------------------------------------- #
#  Дайджест-текст (formation-источник) и dry-запись рядом с задачей
# --------------------------------------------------------------------------- #

def digest_events(digest: dict) -> list[dict]:
    """Event-подобные строки дайджеста — первичные события с провенанс-IDs.

    Почему: formation требует event-IDs — harvest цитирует только видимые в
    источнике ID, а `_clean_claim` режет evidence вне ``allowed``. Дайджест без
    ID стал бы невидимым: потреблён — и молча. Каждая строка наблюдения получает
    свой ID (``run-digest-<task>#<поле>``), kind=run_digest; текст — русский,
    как у её событий. Это НЕ диалоговое событие: evidence-индекс не трогаем,
    строки живут в frontier-мете и умирают вместе с ней.
    """
    observed = digest.get("observed") or {}
    task_id = str(observed.get("task_id") or "")
    if not task_id:
        return []
    verdict = observed.get("verdict")
    verify = observed.get("verify") or {}
    matrix = observed.get("matrix") or {}
    workers = observed.get("workers") or []
    rows = []
    rows.append({
        "id": f"run-digest-{task_id}#verdict", "kind": "run_digest",
        "text": (f"задача «{observed.get('goal') or task_id}» ({task_id}) "
                 f"статус {observed.get('task_status')}; контракт {verdict} "
                 f"(met={verify.get('met', 0)}, unmet={verify.get('unmet', 0)}, "
                 f"unknown={verify.get('unknown', 0)})"),
    })
    rows.append({
        "id": f"run-digest-{task_id}#matrix", "kind": "run_digest",
        "text": (f"проверки задачи: матриц {matrix.get('units', 0)}, команд реально "
                 f"запущено {matrix.get('checks_run', 0)}, не перезапущено как уже "
                 f"зелёные {matrix.get('skipped_already_green', 0)}, падений "
                 f"{matrix.get('failed', 0)}, таймаутов {matrix.get('timed_out', 0)}"),
    })
    worker_line = ", ".join(f"{w.get('agent')}:{w.get('status')}" for w in workers) or "нет"
    rows.append({
        "id": f"run-digest-{task_id}#workers", "kind": "run_digest",
        "text": f"воркеры: {worker_line}; отложенный финиш: "
                f"{'да' if observed.get('deferred_finish') else 'нет'}",
    })
    klass = (digest.get("inferred") or {}).get("episode_class") or ""
    if klass:
        rows.append({
            "id": f"run-digest-{task_id}#class", "kind": "run_digest",
            "text": f"класс эпизода (вывод кода, не наблюдение): {klass}",
        })
    return rows


def digest_text(digest: dict) -> str:
    """Компактный JSON одной строкой — «событие» для источника формирования."""
    observed = digest.get("observed") or {}
    payload = {
        "task_id": observed.get("task_id"),
        "goal": observed.get("goal"),
        "task_status": observed.get("task_status"),
        "contract_verdict": observed.get("verdict"),
        "verify": observed.get("verify"),
        "matrix": observed.get("matrix"),
        "workers": observed.get("workers"),
        "deferred_finish": observed.get("deferred_finish"),
        "episode_class": (digest.get("inferred") or {}).get("episode_class"),
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text[:DIGEST_TEXT_CAP]


def write_dry_digest(digest: dict, *, tasks_dir: Path | None = None) -> Path | None:
    """dry: репетиция — дайджест рядом с задачей, формирование его НЕ видит."""
    root = Path(tasks_dir) if tasks_dir else TASKS_DIR
    task_id = str((digest.get("observed") or {}).get("task_id") or "")
    if not task_id:
        return None
    path = root / task_id / "RUN-DIGEST.md"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "<!-- praxis-runs-digest (dry rehearsal; formation does NOT read this) -->\n"
            "# RUN-DIGEST " + task_id + "\n\n```json\n"
            + json.dumps(digest, ensure_ascii=False, indent=1) + "\n```\n",
            encoding="utf-8")
        return path
    except OSError:
        log.debug("dry-дайджест %s не записался", task_id, exc_info=True)
        return None


def publish_for_formation(digests: list[dict], *, path: Path | None = None) -> int:
    """on: дайджесты становятся formation-готовым фронтиром (frontier-меты).

    Файл лежит в ``memory/.state/life/`` — тот же каталог ``life.STATE_DIR``, который
    глобит formation._frontier_metas (остальные frontier-файлы лежат там же);
    formation.pending_compacts берёт оттуда меты kind=run_digest (см. правку
    formation.py), а formation.run() пометит их обработанными тем же
    processed_compacts — дайджест потребляется один раз, как компакт.
    """
    target = path if path is not None else FORMATION_RUNS_PATH
    fresh = []
    for digest in digests:
        observed = digest.get("observed") or {}
        task_id = str(observed.get("task_id") or "")
        if not task_id:
            continue
        fresh.append({"id": f"run-digest-{task_id}", "kind": "run_digest",
                      "created_at": digest.get("at") or _utc_now_iso(),
                      "task_id": task_id,
                      "events": digest_events(digest),
                      "text": digest_text(digest)})
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"schema": "praxis.runs-nightly.frontier.v1",
                                   "written_at": _utc_now_iso(),
                                   "frontier": fresh}, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(target)
    except OSError:
        log.warning("formation-фронтир дайджестов не записался", exc_info=True)
        return 0
    return len(fresh)


def night_pass(day: str) -> dict:
    """Шаг ночи: off — честный skip; dry — репетиция; on — репетиция + публикация."""
    current = mode()
    if current == "off":
        return {"mode": "off", "reason": "PRAXIS_RUNS_NIGHTLY выключен (её рычаг)"}
    tasks = recent_finished_tasks()
    digests = [digest_task(task) for task in tasks]
    digests = [d for d in digests if d]
    for digest in digests:
        write_dry_digest(digest)
    summary = {"mode": current, "tasks": len(digests), "day": day}
    summary.update(collapse(digests))
    if current == "on":
        summary["published"] = publish_for_formation(digests)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(night_pass(_dt.date.today().isoformat()), ensure_ascii=False, indent=2))
