"""Финиш по вердикту: рычаг PRAXIS_FORGE_CLOSE_ON_VERDICT (PR oro/forge-verdict-0710).

07.10 у Forge одна старая механика внимания: normal-завершение воркера всплывает
только в её ближайшем часовом окне (heartbeat.window_context), даже когда контракт
задачи уже решён дефинитивно. Три шва, каждый про одну честность расписки:

  • рычаг выключен (закон 2 AGENTS.md: поведенческий дефолт меняет только она) —
    скан вообще не смотрит на вердикты, путь ровно вчерашний;
  • рычаг включён — дефинитивный вердикт контракта (выполнен/не выполнен, из
    единого источника forge_learning.contract_status по тем же терминальным
    матрицам, что finish и урок) эскалирует normal-завершение до urgent: воркер
    будит её немедленным окном-приглашением, а не ждёт часового окна;
  • «неизвестно» (контракта нет / не решён / матрицы живые) НИКОГДА не
    эскалирует — grace-окно сохранено, потому что «неизвестно» не повод кончать
    думать. Приглашение остаётся приглашением (закон 3: advisory, не блок).

Эскалированная строка честно называет вердикт в приглашении ([контракт …]) —
молчаливый ⚡ читался бы как чужое «срочно», а это её же контракт.

Запуск: python praxis_test.py test_forge_verdict_0710 -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import forge


def _task_row(task_id: str, root: str, **extra) -> dict:
    row = {"id": task_id, "goal": "задача с контрактом", "root": root, "target": "self",
           "status": "active", "created": "2026-10-07T00:00:00+00:00",
           "updated": "2026-10-07T00:00:00+00:00"}
    row.update(extra)
    return row


def _worker_result(task_id: str, agent_id: str, *, status: str = "done") -> None:
    d = forge.TASKS_DIR / task_id / "agents" / agent_id
    d.mkdir(parents=True, exist_ok=True)
    import datetime as _dt
    finished = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    (d / "result.json").write_text(json.dumps({
        "role": "worker", "status": status, "result": "итог работы воркера",
        "finished": finished,
    }, ensure_ascii=False), encoding="utf-8")


def _green_unit(task_id: str, unit_id: str, command: str) -> None:
    d = forge.TASKS_DIR / task_id / "verifications" / unit_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "request.json").write_text(json.dumps({
        "id": unit_id, "task_id": task_id, "status": "starting",
        "created": "2026-10-07T00:30:00+00:00", "tree_sha": "t" * 40,
        "checks": [{"id": "c1", "command": command}],
    }, ensure_ascii=False), encoding="utf-8")
    (d / "result.json").write_text(json.dumps({
        "status": "passed", "finished": "2026-10-07T00:31:00+00:00",
        "passed": 1, "failed": 0, "skipped": 0,
        "checks": [{"id": "c1", "command": command, "status": "passed", "exit": 0}],
    }, ensure_ascii=False), encoding="utf-8")


def _red_unit(task_id: str, unit_id: str, command: str) -> None:
    d = forge.TASKS_DIR / task_id / "verifications" / unit_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "request.json").write_text(json.dumps({
        "id": unit_id, "task_id": task_id, "status": "starting",
        "created": "2026-10-07T00:30:00+00:00", "tree_sha": "t" * 40,
        "checks": [{"id": "c1", "command": command}],
    }, ensure_ascii=False), encoding="utf-8")
    (d / "result.json").write_text(json.dumps({
        "status": "failed", "finished": "2026-10-07T00:31:00+00:00",
        "passed": 0, "failed": 1, "skipped": 0,
        "checks": [{"id": "c1", "command": command, "status": "failed", "exit": 1}],
    }, ensure_ascii=False), encoding="utf-8")


class _Sandbox(unittest.TestCase):
    """tmp TASKS_DIR + выключенный рычаг по умолчанию (как в живой системе)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig_tasks = forge.TASKS_DIR
        forge.TASKS_DIR = self.tmp / "tasks"
        forge.TASKS_DIR.mkdir(parents=True, exist_ok=True)
        self._orig_state = forge.STATE_DIR
        forge.STATE_DIR = self.tmp / "memory" / ".forge"
        forge.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._env = mock.patch.dict("os.environ", {}, clear=False)
        import os
        os.environ.pop("PRAXIS_FORGE_CLOSE_ON_VERDICT", None)
        self._env.start()
        self.addCleanup(self._env.stop)

    def tearDown(self):
        forge.TASKS_DIR = self._orig_tasks
        forge.STATE_DIR = self._orig_state

    def _task(self, task_id: str, **extra) -> None:
        d = forge.TASKS_DIR / task_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "task.json").write_text(json.dumps(
            _task_row(task_id, str(self.tmp), **extra), ensure_ascii=False),
            encoding="utf-8")

    def _scan_keys(self) -> dict:
        return {row["key"]: row for row in forge._scan_worker_completions()}


class LeverOffKeepsYesterdayPath(_Sandbox):
    """Рычаг выключен — вердикты не читаются вовсе, путь ровно вчерашний."""

    def test_off_means_no_verdict_lookup_at_all(self):
        self._task("code-off001", priority="normal",
                   success_criteria=["тесты зелёные"],
                   verify_commands=["pytest -q"])
        _green_unit("code-off001", "verify-g1", "pytest -q")
        _worker_result("code-off001", "agtn-1")
        with mock.patch.object(forge, "_verdict_for",
                               side_effect=AssertionError("не должен зваться")):
            rows = self._scan_keys()
        self.assertEqual(rows["code-off001:agtn-1"]["priority"], "normal")
        self.assertNotIn("verdict_escalation", rows["code-off001:agtn-1"])


class DefinitiveVerdictEscalatesToUrgent(_Sandbox):
    """Выполненный контракт будит немедленно; приглашение называет вердикт."""

    def test_met_contract_escalates_when_lever_on(self):
        self._task("code-met001", priority="normal",
                   success_criteria=["тесты зелёные"],
                   verify_commands=["pytest -q"])
        _green_unit("code-met001", "verify-g1", "pytest -q")
        _worker_result("code-met001", "agtn-1")
        with mock.patch.dict("os.environ", {"PRAXIS_FORGE_CLOSE_ON_VERDICT": "on"}):
            rows = self._scan_keys()
        row = rows["code-met001:agtn-1"]
        self.assertEqual(row["priority"], "urgent",
                         "дефинитивный «выполнен» — окно сейчас, не через час")
        self.assertEqual(row["verdict_escalation"], "выполнен")

    def test_unmet_contract_also_escalates(self):
        """Провал — тоже дефинитивный вердикт: ей решать, но видеть его сразу."""
        self._task("code-bad001", priority="normal",
                   success_criteria=["без падений"],
                   verify_commands=["pytest -q"])
        _red_unit("code-bad001", "verify-r1", "pytest -q")
        _worker_result("code-bad001", "agtn-2")
        with mock.patch.dict("os.environ", {"PRAXIS_FORGE_CLOSE_ON_VERDICT": "on"}):
            rows = self._scan_keys()
        row = rows["code-bad001:agtn-2"]
        self.assertEqual(row["priority"], "urgent")
        self.assertEqual(row["verdict_escalation"], "не выполнен")

    def test_urgent_stays_urgent_without_marker(self):
        """Явно urgent-задача не получает двойную маркировку — она уже срочная."""
        self._task("code-urg001", priority="urgent",
                   success_criteria=["тесты зелёные"],
                   verify_commands=["pytest -q"])
        _green_unit("code-urg001", "verify-g1", "pytest -q")
        _worker_result("code-urg001", "agtn-3")
        with mock.patch.dict("os.environ", {"PRAXIS_FORGE_CLOSE_ON_VERDICT": "on"}):
            rows = self._scan_keys()
        row = rows["code-urg001:agtn-3"]
        self.assertEqual(row["priority"], "urgent")
        self.assertNotIn("verdict_escalation", row)

    def test_invitation_names_the_verdict(self):
        self._task("code-inv001", priority="normal",
                   success_criteria=["тесты зелёные"],
                   verify_commands=["pytest -q"])
        _green_unit("code-inv001", "verify-g1", "pytest -q")
        _worker_result("code-inv001", "agtn-4")
        with mock.patch.dict("os.environ", {"PRAXIS_FORGE_CLOSE_ON_VERDICT": "on"}):
            rows = list(forge._scan_worker_completions())
        text = forge.wake_invitation(rows)
        self.assertIn("[контракт выполнен]", text,
                      "эскалация честно называет причину срочности")


class UnknownVerdictKeepsGraceWindow(_Sandbox):
    """«Неизвестно» — не повод кончать думать: grace-окно живо при любом рычаге."""

    def test_no_contract_stays_normal(self):
        self._task("code-noctr1", priority="normal")
        _worker_result("code-noctr1", "agtn-5")
        with mock.patch.dict("os.environ", {"PRAXIS_FORGE_CLOSE_ON_VERDICT": "on"}):
            rows = self._scan_keys()
        self.assertEqual(rows["code-noctr1:agtn-5"]["priority"], "normal")

    def test_unresolved_contract_stays_normal(self):
        """Матрицы не гонялись — unknown, путь нормальный."""
        self._task("code-unkn01", priority="normal",
                   success_criteria=["что-то"],
                   verify_commands=["pytest -q"])
        _worker_result("code-unkn01", "agtn-6")
        with mock.patch.dict("os.environ", {"PRAXIS_FORGE_CLOSE_ON_VERDICT": "on"}):
            rows = self._scan_keys()
        self.assertEqual(rows["code-unkn01:agtn-6"]["priority"], "normal")
        self.assertNotIn("verdict_escalation", rows["code-unkn01:agtn-6"])

    def test_live_matrix_proves_nothing(self):
        """Живая матрица без result.json — не вердикт, эскалации нет."""
        self._task("code-live01", priority="normal",
                   success_criteria=["тесты зелёные"],
                   verify_commands=["pytest -q"])
        d = forge.TASKS_DIR / "code-live01" / "verifications" / "verify-live"
        d.mkdir(parents=True, exist_ok=True)
        (d / "request.json").write_text(json.dumps({
            "id": "verify-live", "task_id": "code-live01", "status": "running",
            "created": "2026-10-07T00:40:00+00:00", "tree_sha": "t" * 40,
            "checks": [{"id": "c1", "command": "pytest -q"}],
        }, ensure_ascii=False), encoding="utf-8")
        _worker_result("code-live01", "agtn-7")
        with mock.patch.dict("os.environ", {"PRAXIS_FORGE_CLOSE_ON_VERDICT": "on"}):
            rows = self._scan_keys()
        self.assertEqual(rows["code-live01:agtn-7"]["priority"], "normal")


class LeverForm(_Sandbox):
    """Форма — как у PRAXIS_WORK_ENGINE: {"1","true","yes","on"} после lower()."""

    def test_lever_form_matches_work_engine(self):
        for value in ("ON", "On", "true", "1", "yes", " on "):
            with mock.patch.dict("os.environ",
                                 {"PRAXIS_FORGE_CLOSE_ON_VERDICT": value}):
                self.assertTrue(forge.verdict_close_enabled(),
                                f"{value!r} должно включать рычаг (форма WORK_ENGINE)")
        for value in ("off", "", "no", "0", "verdict"):
            with mock.patch.dict("os.environ",
                                 {"PRAXIS_FORGE_CLOSE_ON_VERDICT": value}):
                self.assertFalse(forge.verdict_close_enabled(),
                                 f"{value!r} не должно включать рычаг")
        import os
        os.environ.pop("PRAXIS_FORGE_CLOSE_ON_VERDICT", None)
        self.assertFalse(forge.verdict_close_enabled(), "умолчание — выключено")


class UrgentPathStillWorks(_Sandbox):
    """has_urgent_pending видит эскалированную строку: старый urgent-путь жив."""

    def test_escalated_row_is_urgent_for_poller(self):
        self._task("code-poll01", priority="normal",
                   success_criteria=["тесты зелёные"],
                   verify_commands=["pytest -q"])
        _green_unit("code-poll01", "verify-g1", "pytest -q")
        _worker_result("code-poll01", "agtn-8")
        with mock.patch.dict("os.environ", {"PRAXIS_FORGE_CLOSE_ON_VERDICT": "on"}):
            self.assertTrue(forge.has_urgent_pending(),
                            "эскалация до urgent видна пуллеру без нового кода")


if __name__ == "__main__":
    unittest.main()
