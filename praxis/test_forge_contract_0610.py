"""Контракт задачи, дедуп верификации и честный finish (PR oro/forge-contract-0610).

06.10 у Forge три шва, каждый про одну и ту же честность расписок:

  • start знал только goal: «сделано» означало «мне так кажется». Теперь контракт
    (success_criteria + verify_commands) сохраняется в task.json и показывается
    ПЕРВОЙ секцией ориентации — до обзоров и кандидатов проверек.
  • Каждый заход в задачу гонял матрицу заново. Зелёный прогон на НЕИЗМЕННОМ
    дереве доказывает то же, что доказал в прошлый раз: теперь команда с passed
    на том же tree-sha не запускается повторно — supervisor пишет расписку
    skipped_already_green со ссылкой на прежний юнит. На другом дереве зелёное
    ничего не доказывает и гоняется заново.
  • finish собирал дифф и уроки, но не отвечал на вопрос «выполнен ли контракт».
    Теперь survey и урок получают сводку met/unmet/unknown. Это advisory-расписка,
    не блок: критики советуют, не блокируют (закон 3 AGENTS.md).

Отдельно закреплена регрессия классификатора submit: «НЕ смёржено» обязано
оставлять задачу active — раньше подстрока «смёрж» матчилась в отрицании и
штамповала done поверх заблокированного гейтом мёржа.

Запуск: python praxis_test.py test_forge_contract_0610 -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import forge
import forge_learning


def _task_row(task_id: str, root: str, **extra) -> dict:
    row = {"id": task_id, "goal": "контракт задачи", "root": root, "target": "self",
           "status": "active", "created": "2026-10-06T00:00:00+00:00",
           "updated": "2026-10-06T00:00:00+00:00"}
    row.update(extra)
    return row


def _green_unit(task_id: str, unit_id: str, tree_sha: str, command: str) -> None:
    """Записать прошедшую матрицу так, как её видит _unit_state: request + result."""
    d = forge.TASKS_DIR / task_id / "verifications" / unit_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "request.json").write_text(json.dumps({
        "id": unit_id, "task_id": task_id, "status": "starting", "created":
        "2026-10-06T01:00:00+00:00", "tree_sha": tree_sha,
        "checks": [{"id": "c1", "command": command}],
    }, ensure_ascii=False), encoding="utf-8")
    (d / "result.json").write_text(json.dumps({
        "status": "passed", "finished": "2026-10-06T01:01:00+00:00", "passed": 1,
        "failed": 0, "skipped": 0,
        "checks": [{"id": "c1", "command": command, "status": "passed", "exit": 0}],
    }, ensure_ascii=False), encoding="utf-8")


class ContractLivesInTaskJson(unittest.TestCase):
    """start сохраняет контракт; ориентация печатает его первой секцией."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig_tasks = forge.TASKS_DIR
        forge.TASKS_DIR = self.tmp / "tasks"
        forge.TASKS_DIR.mkdir(parents=True, exist_ok=True)
        self._orig_state = forge.STATE_DIR
        forge.STATE_DIR = self.tmp / "memory" / ".forge"
        forge.STATE_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        forge.TASKS_DIR = self._orig_tasks
        forge.STATE_DIR = self._orig_state

    def test_start_saves_criteria_and_verify_commands(self):
        with mock.patch.object(forge.selfdev, "begin", return_value={"ok": False}):
            out = forge.start("контрактная задача", target=str(self.tmp),
                              isolation="direct",
                              success_criteria=["тесты зелёные", "", "  ревью пройдено  "],
                              verify_commands=["python praxis_test.py test_forge_lean.py"])
        self.assertIn("coding-задача ", out)
        task_id = next(line.split()[1].rstrip(":") for line in out.splitlines()
                       if line.startswith("coding-задача "))
        task = forge.get(task_id)
        self.assertIsNotNone(task, f"задача {task_id} не нашлась в TASKS_DIR после start")
        self.assertEqual(task["success_criteria"], ["тесты зелёные", "ревью пройдено"],
                         "пустые строки и обрезанные пробелы — не критерии")
        self.assertEqual(task["verify_commands"],
                         ["python praxis_test.py test_forge_lean.py"])

    def test_orientation_prints_contract_first(self):
        with mock.patch.object(forge.selfdev, "begin", return_value={"ok": False}):
            out = forge.start("контрактная задача", target=str(self.tmp),
                              isolation="direct",
                              success_criteria=["тесты зелёные"],
                              verify_commands=["python praxis_test.py test_forge_lean.py"])
        first_line = out.splitlines()[0]
        self.assertEqual(first_line, "Контракт задачи:",
                         "контракт читается до обзоров — он и есть главное «что значит сделано»")
        self.assertIn("1. тесты зелёные", out)
        self.assertIn("$ python praxis_test.py test_forge_lean.py", out)
        self.assertLess(out.index("Контракт задачи:"), out.index("coding-задача"))

    def test_old_task_without_contract_stays_readable(self):
        """Старая задача без полей — контракта нет, ориентация как раньше."""
        d = forge.TASKS_DIR / "code-old0001"
        d.mkdir(parents=True)
        (d / "task.json").write_text(json.dumps(
            _task_row("code-old0001", str(self.tmp)), ensure_ascii=False), encoding="utf-8")
        task = forge.get("code-old0001")
        self.assertEqual(forge._contract_text(task), "")
        self.assertEqual(forge._contract_evidence(task, []), {})


class DedupSkipsGreenOnSameTree(unittest.TestCase):
    """Дедуп — по байт-идентичной команде И тому же tree-sha; иначе гоняется заново."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._tasks_backup = forge.TASKS_DIR
        forge.TASKS_DIR = self.tmp / "tasks"
        forge.TASKS_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        forge.TASKS_DIR = self._tasks_backup

    def test_green_command_on_same_tree_is_skipped_with_reference(self):
        _green_unit("code-d001", "verify-aaa1", "t" * 40, "pytest -q test_forge_lean.py")
        checks = [{"id": "c1", "command": "pytest -q test_forge_lean.py", "cwd": "."},
                  {"id": "c2", "command": "flake8 forge.py", "cwd": "."}]
        out, notes = forge._dedup_against_green("code-d001", "t" * 40, checks)
        self.assertTrue(out[0].get("skip"))
        self.assertEqual(out[0]["skip_status"], "skipped_already_green")
        self.assertIn("verify-aaa1", out[0]["skip_reference"])
        self.assertFalse(out[1].get("skip"), "новой команде нечего наследовать")
        self.assertTrue(notes)

    def test_green_command_on_another_tree_runs_again(self):
        _green_unit("code-d002", "verify-bbb2", "t" * 40, "pytest -q test_forge_lean.py")
        out, notes = forge._dedup_against_green("code-d002", "f" * 40, [
            {"id": "c1", "command": "pytest -q test_forge_lean.py", "cwd": "."}])
        self.assertFalse(out[0].get("skip"),
                         "зелёное на другом дереве ничего не доказывает этому")
        self.assertEqual(notes, [])

    def test_no_tree_sha_no_dedup(self):
        """Non-git корень — дедупить нечем: каждый прогон честно гоняется."""
        out, notes = forge._dedup_against_green("code-d003", "", [
            {"id": "c1", "command": "pytest -q", "cwd": "."}])
        self.assertFalse(out[0].get("skip"))
        self.assertEqual(notes, [])

    def test_live_matrix_proves_nothing_yet(self):
        """Живая (starting/running) матрица без result.json — не прошлое, а настоящее."""
        d = forge.TASKS_DIR / "code-d004" / "verifications" / "verify-live1"
        d.mkdir(parents=True)
        (d / "request.json").write_text(json.dumps({
            "id": "verify-live1", "task_id": "code-d004", "status": "running",
            "created": "2026-10-06T02:00:00+00:00", "tree_sha": "t" * 40,
            "checks": [{"id": "c1", "command": "pytest -q"}],
        }, ensure_ascii=False), encoding="utf-8")
        out, notes = forge._dedup_against_green("code-d004", "t" * 40, [
            {"id": "c1", "command": "pytest -q", "cwd": "."}])
        self.assertFalse(out[0].get("skip"))
        self.assertEqual(notes, [])

    def test_failed_check_is_not_dedup_material(self):
        d = forge.TASKS_DIR / "code-d005" / "verifications" / "verify-red1"
        d.mkdir(parents=True)
        (d / "request.json").write_text(json.dumps({
            "id": "verify-red1", "task_id": "code-d005", "status": "starting",
            "created": "2026-10-06T03:00:00+00:00", "tree_sha": "t" * 40,
            "checks": [{"id": "c1", "command": "pytest -q"}],
        }, ensure_ascii=False), encoding="utf-8")
        (d / "result.json").write_text(json.dumps({
            "status": "failed", "finished": "2026-10-06T03:01:00+00:00",
            "checks": [{"id": "c1", "command": "pytest -q", "status": "failed"}],
        }, ensure_ascii=False), encoding="utf-8")
        out, notes = forge._dedup_against_green("code-d005", "t" * 40, [
            {"id": "c1", "command": "pytest -q", "cwd": "."}])
        self.assertFalse(out[0].get("skip"), "красное — не повод не проверять снова")
        self.assertEqual(notes, [])


class SkipRowBecomesReceiptInMatrix(unittest.TestCase):
    """Supervisor пишет расписку, не спавня процесс; матрица остаётся зелёной."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_run_one_skip_row_is_a_receipt(self):
        import forge_verify
        state = {"checks": {}}
        row = forge_verify._run_one(
            0, {"id": "c1", "command": "pytest -q", "skip": True,
                "skip_status": "skipped_already_green",
                "skip_reference": "verify-aaa1/c1"},
            self.tmp, self.tmp, 60, state, threading_lock(),
            observation={"tree_sha": "t" * 40})
        self.assertEqual(row["status"], "skipped_already_green")
        self.assertEqual(row["exit"], 0)
        self.assertTrue(row["dedup"])
        self.assertEqual(row["skip_reference"], "verify-aaa1/c1")
        self.assertEqual(row["tree_sha"], "t" * 40)
        self.assertEqual(row["duration_s"], 0)
        self.assertEqual(state["checks"]["c1"]["status"], "skipped_already_green")
        self.assertFalse(list((self.tmp / "logs").glob("*.log")) if (self.tmp / "logs").is_dir()
                         else False, "расписке не нужен лог — процесса не было")

    def test_deduped_counts_as_passed_in_matrix_result(self):
        """Полностью дедупнутая матрица — passed, а не «с пропусками»."""
        import forge_verify
        req = self.tmp / "request.json"
        req.write_text(json.dumps({
            "id": "verify-ddd", "task_id": "code-x", "root": str(self.tmp),
            "scope": "self", "max_parallel": 1, "timeout": 30, "created": "now",
            "tree_sha": "t" * 40,
            "checks": [{"id": "c1", "command": "echo hi", "skip": True,
                        "skip_status": "skipped_already_green",
                        "skip_reference": "verify-old/c1"}],
        }, ensure_ascii=False), encoding="utf-8")
        code = forge_verify.run(req)
        self.assertEqual(code, 0)
        result = json.loads((self.tmp / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["passed"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["checks"][0]["status"], "skipped_already_green")


def threading_lock():
    import threading
    return threading.Lock()


class FinishKeepsBlockedMergeHonest(unittest.TestCase):
    """«НЕ смёржено» — задача жива: классификатор не имеет права штамповать done.

    Инцидент из комментария в _finish_unlocked: подстрока «смёрж» матчилась в
    отрицании, и finish ставил done поверх мёржа, заблокированного гейтом
    (красные тесты, BLOCKED гнома). Порядок проверок: отрицания — первыми.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = forge.TASKS_DIR
        forge.TASKS_DIR = self.tmp / "tasks"
        forge.TASKS_DIR.mkdir(parents=True, exist_ok=True)
        self._orig_state = forge.STATE_DIR
        forge.STATE_DIR = self.tmp / "memory" / ".forge"
        forge.STATE_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        forge.TASKS_DIR = self._orig
        forge.STATE_DIR = self._orig_state

    def _proposal_task(self, **extra):
        d = forge.TASKS_DIR / "code-notmerged"
        d.mkdir(parents=True, exist_ok=True)
        row = _task_row("code-notmerged", str(self.tmp), proposal_id="prop-1",
                        cleanup="selfdev", source_git="", source_branch="",
                        worktree_root="", **extra)
        (d / "task.json").write_text(json.dumps(row, ensure_ascii=False),
                                     encoding="utf-8")
        return row

    def test_not_merged_submission_keeps_task_active(self):
        self._proposal_task()
        survey = {"changed": [], "notes": [], "verification_before": {}}
        with mock.patch.object(
                forge.selfdev, "submit",
                return_value="НЕ смёржено: гейт тестов красный, BLOCKED гнома"), \
             mock.patch.object(forge, "_finish_survey", return_value=survey):
            out = forge._finish_unlocked("code-notmerged", title="t",
                                         review="r", checked="c")
        self.assertIn("active", out.splitlines()[0])
        task = forge.get("code-notmerged")
        self.assertEqual(task["status"], "active",
                         "заблокированный гейтом мёрж — не повод закрывать задачу")
        self.assertEqual(task["finished"], "")

    def test_merged_submission_closes_the_task(self):
        self._proposal_task()
        survey = {"changed": [], "notes": [], "verification_before": {}}
        with mock.patch.object(
                forge.selfdev, "submit",
                return_value="Смёржено в proposal/prop-1 (main)"), \
             mock.patch.object(forge, "_finish_survey", return_value=survey):
            out = forge._finish_unlocked("code-notmerged", title="t",
                                         review="r", checked="c")
        self.assertIn("done", out.splitlines()[0])
        self.assertEqual(forge.get("code-notmerged")["status"], "done")


class ContractReceiptAtFinish(unittest.TestCase):
    """Finish-осмотр считает met/unmet/unknown; урок несёт вердикт контракта."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = forge.TASKS_DIR
        forge.TASKS_DIR = self.tmp / "tasks"
        forge.TASKS_DIR.mkdir(parents=True, exist_ok=True)
        self._orig_state = forge.STATE_DIR
        forge.STATE_DIR = self.tmp / "memory" / ".forge"
        forge.STATE_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        forge.TASKS_DIR = self._orig
        forge.STATE_DIR = self._orig_state

    def _task(self, task_id: str, criteria: list, commands: list) -> dict:
        row = _task_row(task_id, str(self.tmp),
                        success_criteria=criteria, verify_commands=commands)
        d = forge.TASKS_DIR / task_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "task.json").write_text(json.dumps(row, ensure_ascii=False),
                                     encoding="utf-8")
        return row

    def test_survey_carries_contract_summary(self):
        self._task("code-ctr01", ["тесты зелёные"],
                   ["pytest -q", "flake8 forge.py"])
        _green_unit("code-ctr01", "verify-g1", "t" * 40, "pytest -q")
        # flake8 не гонялся вовсе — unknown; отдельная матрица дедупнула pytest
        _green_unit("code-ctr01", "verify-g2", "t" * 40,
                    "pytest -q")  # заглушка ниже перекроет
        d = forge.TASKS_DIR / "code-ctr01" / "verifications" / "verify-g2"
        (d / "result.json").write_text(json.dumps({
            "status": "failed", "finished": "2026-10-06T04:00:00+00:00",
            "checks": [
                {"id": "c1", "command": "pytest -q", "status": "skipped_already_green",
                 "dedup": True, "skip_reference": "verify-g1/c1"},
                {"id": "c2", "command": "flake8 forge.py", "status": "failed"},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        survey = forge._finish_survey("code-ctr01")
        contract = survey.get("contract")
        self.assertIsNotNone(contract, "контракт был — сводка обязана быть")
        self.assertEqual(contract["criteria"], ["тесты зелёные"])
        # pytest: passed в g1 + skipped_already_green в g2 → met; flake8 failed → unmet
        self.assertEqual(contract["verify"], {"met": 1, "unmet": 1, "unknown": 0})

    def test_finish_reply_names_the_contract_verdict_and_lesson_gets_the_flag(self):
        self._task("code-ctr02", ["тесты зелёные"], ["pytest -q"])
        _green_unit("code-ctr02", "verify-g3", "t" * 40, "pytest -q")
        survey = forge._finish_survey("code-ctr02")
        with mock.patch.object(forge, "_finish_survey", return_value=survey):
            out = forge._finish_unlocked("code-ctr02", title="t", review="r",
                                         checked="c")
        self.assertIn("контракт выполнен", out)
        rows = [json.loads(line) for line in
                forge.STATE_DIR.joinpath("lessons.jsonl").read_text(encoding="utf-8")
                .splitlines()]
        lesson = next(r for r in rows if r.get("task_id") == "code-ctr02")
        self.assertEqual(lesson["contract"], "выполнен")
        self.assertTrue(str(lesson["lesson"]).strip().endswith("контракт выполнен"),
                        f"урок обязан нести вердикт: {lesson['lesson']!r}")

    def test_unmet_contract_finishes_but_says_so(self):
        """unmet НЕ блокирует finish (закон 3) — но исход назван честно."""
        self._task("code-ctr03", ["без провалов"], ["pytest -q"])
        d = forge.TASKS_DIR / "code-ctr03" / "verifications" / "verify-r1"
        d.mkdir(parents=True)
        (d / "request.json").write_text(json.dumps({
            "id": "verify-r1", "task_id": "code-ctr03", "status": "starting",
            "created": "2026-10-06T05:00:00+00:00", "tree_sha": "t" * 40,
            "checks": [{"id": "c1", "command": "pytest -q"}],
        }, ensure_ascii=False), encoding="utf-8")
        (d / "result.json").write_text(json.dumps({
            "status": "failed", "finished": "2026-10-06T05:01:00+00:00",
            "checks": [{"id": "c1", "command": "pytest -q", "status": "failed"}],
        }, ensure_ascii=False), encoding="utf-8")
        survey = forge._finish_survey("code-ctr03")
        self.assertEqual(survey["contract"]["verify"], {"met": 0, "unmet": 1, "unknown": 0})
        with mock.patch.object(forge, "_finish_survey", return_value=survey):
            out = forge._finish_unlocked("code-ctr03", title="t", review="r", checked="c")
        first = out.splitlines()[0]
        self.assertIn("done", first, "advisory-контракт не блокирует закрытие")
        self.assertIn("контракт не выполнен", out)

    def test_never_run_command_is_unknown(self):
        self._task("code-ctr04", ["хоть что-то"], ["flake8 forge.py"])
        survey = forge._finish_survey("code-ctr04")
        self.assertEqual(survey["contract"]["verify"], {"met": 0, "unmet": 0, "unknown": 1})


class ContractStatusIsOneSource(unittest.TestCase):
    """Вердикт контракта живёт в forge_learning.contract_status — один на finish и урок."""

    def test_verdicts(self):
        self.assertEqual(forge_learning.contract_status(
            {"criteria": ["x"], "verify": {"met": 2, "unmet": 0, "unknown": 0}}), "выполнен")
        self.assertEqual(forge_learning.contract_status(
            {"criteria": ["x"], "verify": {"met": 1, "unmet": 1, "unknown": 0}}), "не выполнен")
        self.assertEqual(forge_learning.contract_status(
            {"criteria": ["x"], "verify": {"met": 0, "unmet": 0, "unknown": 2}}), "неизвестно")
        self.assertEqual(forge_learning.contract_status(None), "неизвестно")
        self.assertEqual(forge_learning.contract_status({}), "неизвестно")


if __name__ == "__main__":
    unittest.main()
