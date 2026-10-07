"""Прогоны → ночная память: рычаг PRAXIS_RUNS_NIGHTLY (PR oro/runs-nightly-0710).

07.10. Дыра: вход формирования (formation.pending_compacts) брал только компакты
ЖУРНАЛА — как работа реально прошла (вердикт против контракта, перезапуски против
skipped_already_green, отложенные финиши) в ночной материал не входила: характер
ревизовался по словам, не по делу. Этот PR — кодовая часть переноса OWN-механик:

  • сборщик дайджестов из durable-материала задач — типизированные поля, собранные
    кодом, НЕ сырые трейсы (рефлексия по фактам, не по припоминанию);
  • fact/interpretation: observed против inferred, вердикт — из единого
    источника (forge_learning.contract_status), как у finish и урока;
  • свёртка с дедупом: класс ошибки — счётчик в append-only журнале классов,
    эпизод с уже записанным уроком помечается already_lessoned, конкурентный
    урок НЕ плодится (Pattern Register-форма в её конвенциях);
  • dry — репетиция: дайджест пишется рядом с задачей, формирование его НЕ
    видит; on — публикация formation-фронтиром с собственными evidence-ID;
  • рычаг off по умолчанию (закон 2 AGENTS.md) — ночной шаг честно скипается.

Запуск: python praxis_test.py test_runs_nightly_0710 -v
"""
from __future__ import annotations

import datetime as _dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import forge
import runs_nightly


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _task(task_id: str, *, status: str = "done", priority: str = "normal",
          finished: str | None = None, criteria: list | None = None,
          commands: list | None = None) -> None:
    row = {"id": task_id, "goal": f"цель {task_id}", "root": "C:/nonexistent",
           "target": "self", "status": status, "priority": priority,
           "created": _now_iso(), "updated": _now_iso(),
           "finished": finished or _now_iso()}
    if criteria is not None:
        row["success_criteria"] = criteria
    if commands is not None:
        row["verify_commands"] = commands
    d = forge.TASKS_DIR / task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.json").write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")


def _matrix(task_id: str, unit_id: str, *, status: str, checks: list) -> None:
    d = forge.TASKS_DIR / task_id / "verifications" / unit_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "request.json").write_text(json.dumps({
        "id": unit_id, "task_id": task_id, "status": "starting",
        "created": _now_iso(), "tree_sha": "t" * 40,
        "checks": [{"id": f"c{i}", "command": c} for i, c in enumerate(checks)],
    }, ensure_ascii=False), encoding="utf-8")
    (d / "result.json").write_text(json.dumps({
        "status": status, "finished": _now_iso(),
        "checks": checks and [
            {"id": f"c{i}", "command": c, "status": s} for i, (c, s) in enumerate(checks)],
    }, ensure_ascii=False), encoding="utf-8")


def _agent(task_id: str, agent_id: str, *, status: str = "done",
           role: str = "worker") -> None:
    d = forge.TASKS_DIR / task_id / "agents" / agent_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "result.json").write_text(json.dumps({
        "role": role, "status": status, "result": "...", "finished": _now_iso(),
    }, ensure_ascii=False), encoding="utf-8")


class _Sandbox(unittest.TestCase):
    """tmp TASKS_DIR/STATE_DIR + рычаг выключен по умолчанию."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig_tasks = forge.TASKS_DIR
        forge.TASKS_DIR = self.tmp / "tasks"
        forge.TASKS_DIR.mkdir(parents=True, exist_ok=True)
        self._orig_state = forge.STATE_DIR
        forge.STATE_DIR = self.tmp / "memory" / ".forge"
        forge.STATE_DIR.mkdir(parents=True, exist_ok=True)
        # runs_nightly глобали указывают на те же корни
        self._rn_tasks = runs_nightly.TASKS_DIR
        self._rn_state = runs_nightly.FORGE_STATE
        self._rn_form = runs_nightly.FORMATION_RUNS_PATH
        runs_nightly.TASKS_DIR = forge.TASKS_DIR
        runs_nightly.FORGE_STATE = forge.STATE_DIR
        runs_nightly.PATTERNS_PATH = forge.STATE_DIR / "patterns.jsonl"
        runs_nightly.FORMATION_RUNS_PATH = self.tmp / "memory" / ".state" / "formation_runs.json"
        import os
        os.environ.pop("PRAXIS_RUNS_NIGHTLY", None)
        self.addCleanup(self._restore)

    def _restore(self):
        forge.TASKS_DIR = self._orig_tasks
        forge.STATE_DIR = self._orig_state
        runs_nightly.TASKS_DIR = self._rn_tasks
        runs_nightly.FORGE_STATE = self._rn_state
        runs_nightly.PATTERNS_PATH = self._rn_state / "patterns.jsonl"
        runs_nightly.FORMATION_RUNS_PATH = self._rn_form


class LeverForm(_Sandbox):
    """off|dry|on после strip/lower; всё другое — off (закон 2: умолчание off)."""

    def test_form(self):
        for value, want in (("off", "off"), ("dry", "dry"), ("on", "on"),
                            ("ON", "on"), (" dry ", "dry"), ("", "off"),
                            ("yes", "off"), ("1", "off"), ("verbose", "off")):
            with mock.patch.dict("os.environ", {"PRAXIS_RUNS_NIGHTLY": value}):
                self.assertEqual(runs_nightly.mode(), want, f"{value!r} → {want}")
        import os
        os.environ.pop("PRAXIS_RUNS_NIGHTLY", None)
        self.assertEqual(runs_nightly.mode(), "off", "умолчание — выключено")


class NightStepRespectsLever(_Sandbox):
    """off — честный skip с причиной; dry — репетиция без публикации; on — с ней."""

    def test_off_is_honest_skip(self):
        out = runs_nightly.night_pass("2026-10-07")
        self.assertEqual(out["mode"], "off")
        self.assertIn("PRAXIS_RUNS_NIGHTLY", out["reason"],
                      "причина скипа называет рычаг, а не молчит")

    def test_dry_writes_digest_beside_task_but_not_formation(self):
        _task("code-dry01", status="done", criteria=["зелёные тесты"],
              commands=["pytest -q"])
        _matrix("code-dry01", "verify-1", status="passed",
                checks=[("pytest -q", "passed")])
        _agent("code-dry01", "wk-1")
        with mock.patch.dict("os.environ", {"PRAXIS_RUNS_NIGHTLY": "dry"}):
            out = runs_nightly.night_pass("2026-10-07")
        self.assertEqual(out["mode"], "dry")
        self.assertEqual(out["tasks"], 1)
        self.assertTrue((forge.TASKS_DIR / "code-dry01" / "RUN-DIGEST.md").exists(),
                        "dry-репетиция пишет дайджест рядом с задачей")
        self.assertFalse(runs_nightly.FORMATION_RUNS_PATH.exists(),
                         "dry НЕ публикует формирование — она смотрит, потом решает")

    def test_on_publishes_formation_frontier(self):
        _task("code-on001", status="done", criteria=["зелёные тесты"],
              commands=["pytest -q"])
        _matrix("code-on001", "verify-1", status="passed",
                checks=[("pytest -q", "passed")])
        _agent("code-on001", "wk-1")
        with mock.patch.dict("os.environ", {"PRAXIS_RUNS_NIGHTLY": "on"}):
            out = runs_nightly.night_pass("2026-10-07")
        self.assertEqual(out.get("published"), 1)
        data = json.loads(runs_nightly.FORMATION_RUNS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(data["frontier"]), 1)
        meta = data["frontier"][0]
        self.assertEqual(meta["kind"], "run_digest")
        self.assertEqual(meta["id"], "run-digest-code-on001")
        self.assertTrue(meta["events"], "дайджест несёт собственные evidence-строки")


class DigestIsTypedFacts(_Sandbox):
    """Дайджест — типизированные поля из durable-материала, не сырой трейс."""

    def test_verdict_comes_from_single_source(self):
        _task("code-ver01", status="done", criteria=["зелёные"],
              commands=["pytest -q"])
        _matrix("code-ver01", "verify-1", status="passed",
                checks=[("pytest -q", "passed")])
        digest = runs_nightly.digest_task(forge.get("code-ver01"))
        self.assertEqual(digest["observed"]["verdict"], "выполнен")
        self.assertEqual(digest["observed"]["verify"], {"met": 1, "unmet": 0, "unknown": 0})
        self.assertEqual(digest["observed"]["matrix"]["checks_run"], 1)

    def test_unmet_and_unknown_verdicts(self):
        _task("code-ver02", status="done", criteria=["без падений"],
              commands=["pytest -q"])
        _matrix("code-ver02", "verify-1", status="failed",
                checks=[("pytest -q", "failed")])
        _task("code-ver03", status="done", criteria=["что-то"],
              commands=["pytest -q"])
        self.assertEqual(runs_nightly.digest_task(forge.get("code-ver02"))
                         ["observed"]["verdict"], "не выполнен")
        self.assertEqual(runs_nightly.digest_task(forge.get("code-ver03"))
                         ["observed"]["verdict"], "неизвестно")

    def test_skipped_green_counted_separately(self):
        """«Куча тестов» в числах: перезапуски против skipped_already_green."""
        _task("code-mat01", status="done", criteria=["зелёные"],
              commands=["pytest -q", "flake8 ."])
        _matrix("code-mat01", "verify-1", status="passed", checks=[
            ("pytest -q", "passed"),
            ("flake8 .", "skipped_already_green"),
        ])
        digest = runs_nightly.digest_task(forge.get("code-mat01"))
        self.assertEqual(digest["observed"]["matrix"]["checks_run"], 1)
        self.assertEqual(digest["observed"]["matrix"]["skipped_already_green"], 1)

    def test_fact_interpretation_split(self):
        """observed — факты; inferred — класс эпизода (вывод кода, помечен)."""
        _task("code-def01", status="active", criteria=["зелёные"],
              commands=["pytest -q"])
        _agent("code-def01", "wk-1", status="done")
        digest = runs_nightly.digest_task(forge.get("code-def01"))
        self.assertTrue(digest["observed"]["deferred_finish"],
                        "воркер закончил, задача active — отложенный финиш")
        self.assertEqual(digest["inferred"]["episode_class"], "deferred_finish")


class Classification(_Sandbox):
    """Классы — детерминированные, из фактов; без класса — не ошибка."""

    def test_classes(self):
        cases = {
            "deferred_finish": {"task_status": "active", "verdict": "неизвестно",
                                "matrix": {"checks_run": 0, "skipped_already_green": 0},
                                "deferred_finish": True},
            "lost_task": {"task_status": "lost", "verdict": "неизвестно",
                          "matrix": {"checks_run": 0, "skipped_already_green": 0},
                          "deferred_finish": False},
            "unmet_contract": {"task_status": "done", "verdict": "не выполнен",
                               "matrix": {"checks_run": 1, "skipped_already_green": 0},
                               "deferred_finish": False},
            "task_failed": {"task_status": "failed", "verdict": "неизвестно",
                            "matrix": {"checks_run": 0, "skipped_already_green": 0},
                            "deferred_finish": False},
            "": {"task_status": "done", "verdict": "выполнен",
                 "matrix": {"checks_run": 2, "skipped_already_green": 1},
                 "deferred_finish": False},
        }
        for expected, observed in cases.items():
            self.assertEqual(runs_nightly.classify(observed), expected,
                             f"{observed['task_status']}/{observed['verdict']} → {expected!r}")

    def test_rerun_storm_needs_many_reruns(self):
        """Граница: 6+ перезапусков без единого дедупа — класс rerun_storm."""
        storm = {"task_status": "done", "verdict": "выполнен",
                 "matrix": {"checks_run": 7, "skipped_already_green": 0},
                 "deferred_finish": False}
        calm = {"task_status": "done", "verdict": "выполнен",
                "matrix": {"checks_run": 7, "skipped_already_green": 5},
                "deferred_finish": False}
        self.assertEqual(runs_nightly.classify(storm), "rerun_storm")
        self.assertEqual(runs_nightly.classify(calm), "",
                         "дедуп работал — шторма нет, чистый эпизод")


class SelectionWindow(_Sandbox):
    """Окно отбора: свежие терминальные входят, старые и живые — нет."""

    def test_window_and_freshness(self):
        old = (_dt.datetime.now(_dt.timezone.utc)
               - _dt.timedelta(hours=runs_nightly.WINDOW_H + 5)).isoformat()
        _task("code-old01", finished=old)
        _task("code-new01")
        _task("code-live1", status="active")
        rows = runs_nightly.recent_finished_tasks()
        ids = [r["id"] for r in rows]
        self.assertIn("code-new01", ids)
        self.assertNotIn("code-old01", ids, "за окном — следующая ночь, не эта")
        self.assertNotIn("code-live1", ids, "живая задача — не итог")


class CollapseBumpsCounterNotLessons(_Sandbox):
    """Свёртка: подтверждённый класс — счётчик; урок с этим task_id не дублируется."""

    def test_counter_bumps_and_already_lessoned_flag(self):
        digests = []
        for task_id in ("code-cls01", "code-cls02"):
            _task(task_id, status="failed", priority="normal")
            _agent(task_id, "wk-1", status="failed")
            digests.append(runs_nightly.digest_task(forge.get(task_id)))
        # у первой задачи урок уже записан (forge_learning) — конкурент не нужен
        lessons = forge.STATE_DIR / "lessons.jsonl"
        lessons.parent.mkdir(parents=True, exist_ok=True)
        lessons.write_text(json.dumps({"id": "lesson-x", "task_id": "code-cls01"}) + "\n",
                           encoding="utf-8")
        out = runs_nightly.collapse(digests, lessons_path=lessons)
        self.assertEqual(out["episodes_recorded"], 2)
        self.assertEqual(out["already_lessoned"], 1)
        rows = [json.loads(line) for line in
                runs_nightly.PATTERNS_PATH.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 2, "журнал наблюдений append-only: строка на эпизод")
        self.assertTrue(rows[0]["already_lessoned"])
        self.assertFalse(rows[1]["already_lessoned"])
        counts = runs_nightly.pattern_counts()
        self.assertEqual(counts.get("task_failed"), 2,
                         "счётчик класса = строк класса; пятый конкурентный урок не заводится")

    def test_clean_episodes_do_not_touch_journal(self):
        _task("code-cln01", status="done", criteria=["зелёные"],
              commands=["pytest -q"])
        _matrix("code-cln01", "verify-1", status="passed",
                checks=[("pytest -q", "passed")])
        digest = runs_nightly.digest_task(forge.get("code-cln01"))
        self.assertEqual(digest["inferred"]["episode_class"], "")
        out = runs_nightly.collapse([digest])
        self.assertEqual(out["episodes_recorded"], 0)
        self.assertEqual(out["clean"], 1)
        self.assertFalse(runs_nightly.PATTERNS_PATH.exists())


class FormationIntegration(_Sandbox):
    """run_digest входит в pending_compacts; потребление — тем же processed."""

    def test_pending_run_digests_flow(self):
        import formation
        frontier = [{"id": "run-digest-code-f01", "kind": "run_digest",
                     "created_at": _now_iso(), "task_id": "code-f01",
                     "events": [{"id": "run-digest-code-f01#verdict",
                                 "kind": "run_digest", "text": "контракт выполнен"}],
                     "text": "{}"}]
        state_dir = formation.life.STATE_DIR
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "formation_runs.json").write_text(json.dumps(
            {"frontier": frontier}, ensure_ascii=False), encoding="utf-8")
        with mock.patch.object(formation, "_frontier_metas", return_value=[]):
            pending = formation.pending_compacts()
        ids = [str(m.get("id")) for m in pending]
        self.assertIn("run-digest-code-f01", ids,
                      "on-фронтир дайджестов становится источником формирования")

    def test_processed_digest_not_returned(self):
        import formation
        frontier = [{"id": "run-digest-code-f02", "kind": "run_digest",
                     "created_at": _now_iso(), "task_id": "code-f02",
                     "events": [], "text": "{}"}]
        state_dir = formation.life.STATE_DIR
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "formation_runs.json").write_text(json.dumps(
            {"frontier": frontier}, ensure_ascii=False), encoding="utf-8")
        st = formation.state()
        st["processed_compacts"] = ["run-digest-code-f02"]
        formation._atomic(formation.STATE_PATH, st)
        with mock.patch.object(formation, "_frontier_metas", return_value=[]):
            pending = formation.pending_compacts()
        ids = [str(m.get("id")) for m in pending]
        self.assertNotIn("run-digest-code-f02", ids,
                         "дайджест потребляется один раз, как компакт")

    def test_source_bundle_includes_digest_events(self):
        import formation
        meta = {"id": "run-digest-code-f03", "kind": "run_digest",
                "events": [{"id": "run-digest-code-f03#verdict",
                            "kind": "run_digest", "text": "контракт выполнен"}]}
        text, allowed = formation._source_bundle([meta])
        self.assertIn("run-digest-code-f03#verdict", text)
        self.assertIn("run-digest-code-f03#verdict", allowed,
                      "evidence-ID дайджеста входит в allowed — harvest может цитировать")


class DigestEvents(_Sandbox):
    """Event-строки дайджеста: русские, с ID, наблюдение/вывод разведены."""

    def test_events_have_ids_and_russian_text(self):
        _task("code-evt01", status="failed", priority="normal")
        _agent("code-evt01", "wk-1", status="failed")
        digest = runs_nightly.digest_task(forge.get("code-evt01"))
        events = runs_nightly.digest_events(digest)
        self.assertTrue(events)
        for event in events:
            self.assertTrue(event["id"].startswith("run-digest-code-evt01#"))
            self.assertTrue(event["text"].strip())
        classes = [e for e in events if e["id"].endswith("#class")]
        self.assertTrue(classes, "вывод кода помечен отдельной строкой-классом")


if __name__ == "__main__":
    unittest.main()
