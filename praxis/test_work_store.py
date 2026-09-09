"""Работа переживает прогон: файловая доска и «сделано» как запрос, а не объявление.

ГРАНИЦА, КОТОРУЮ ЭТИ ТЕСТЫ ОХРАНЯЮТ.  Доска умеет хранить и отвечать «что открыто». Она
НИКОГО НЕ БУДИТ. Как только сюда приедет расписание, доска тихо станет часами — а часы у
этого дома уже есть, и именно от них мы уходим.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent
import run_context
import run_manager
import media
import work_loop
import work_store


class WorkStoreBase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="praxis-work-store-")
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)
        self.previous_base = work_store.BASE
        work_store.BASE = self.base
        self.addCleanup(self._restore_base)

    def _restore_base(self):
        work_store.BASE = self.previous_base


class TheCardOutlivesTheRun(WorkStoreBase):
    def test_a_card_keeps_its_runs_which_is_the_seam_that_did_not_exist(self):
        row = work_store.open_card(goal="довести зеркало до GitHub", run_id="run-A")
        work_store.attach_run(row["id"], "run-B")
        again = work_store.card(row["id"])
        self.assertEqual(again["run_ids"], ["run-A", "run-B"])
        self.assertEqual(work_store.for_run("run-B")["id"], row["id"])
        self.assertIsNone(work_store.for_run("run-неизвестный"))

    def test_the_canon_is_the_card_and_the_board_says_so_about_itself(self):
        work_store.open_card(goal="работа один", run_id="r1")
        blocked = work_store.open_card(goal="работа два", run_id="r2")
        work_store.transition(blocked["id"], "blocked", blocker="нет доступа к хосту")
        text = work_store.board()
        self.assertIn("Источник истины", text)
        self.assertIn("нет доступа к хосту", text)
        self.assertIn("работа один", text)

    def test_a_stale_revision_is_refused_instead_of_overwriting(self):
        row = work_store.open_card(goal="гонка", run_id="r")
        work_store.transition(row["id"], "waiting")
        with self.assertRaises(work_store.WorkConflict):
            work_store.transition(row["id"], "done", expected_revision=row["revision"])

    def test_notes_survive_the_turn_they_were_said_in(self):
        row = work_store.open_card(goal="заметки", run_id="r")
        work_store.note(row["id"], "проверила две ветки, третья под вопросом")
        body = work_store.card(row["id"])["body"]
        self.assertIn("проверила две ветки", body)

    def test_an_unfinished_write_is_named_not_guessed(self):
        """Хвост `intent` без `committed` — это «я не знаю», и оно обязано быть выразимо."""
        row = work_store.open_card(goal="обрыв", run_id="r")
        work_store._append(row["id"], "intent", to_status="done", revision=99)
        reports = work_store.recover()
        self.assertEqual(reports, [{"id": row["id"], "outcome": "attention"}])
        kinds = [e["kind"] for e in work_store.events(row["id"])]
        self.assertEqual(kinds[-1], "attention")
        self.assertEqual(work_store.card(row["id"])["status"], "running")

    def test_a_write_that_did_land_is_committed_after_the_restart(self):
        row = work_store.open_card(goal="долетело", run_id="r")
        work_store.transition(row["id"], "waiting")
        path = work_store._dir(row["id"]) / "events.jsonl"
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                  for r in rows[:-1]) + "\n", encoding="utf-8")
        self.assertEqual(work_store.recover(),
                         [{"id": row["id"], "outcome": "committed_after_restart"}])

    def test_the_board_does_not_schedule_anything(self):
        """Сторож границы: расписание здесь — это тихое превращение доски в часы."""
        source = Path(work_store.__file__).read_text(encoding="utf-8")
        for forbidden in ("import time\n", "sched", "cron", "wake_at", "threading.Timer"):
            self.assertNotIn(forbidden, source.replace("import time", "", 1)
                             if forbidden == "import time\n" else source)


class DoneIsARequestNotAnAnnouncement(WorkStoreBase):
    def test_done_without_a_trace_is_refused(self):
        accepted, why = work_store.dod_verdict(None, "   ")
        self.assertFalse(accepted)
        self.assertIn("наблюдаемое", why)

    def test_done_with_a_trace_is_accepted_and_nothing_is_judged(self):
        accepted, why = work_store.dod_verdict(None, "тесты 3819 OK, коммит 3e7fcdc7")
        self.assertTrue(accepted)
        self.assertEqual(why, "")

    def test_her_own_criteria_are_repeated_back_verbatim(self):
        row = work_store.open_card(
            goal="срез", run_id="r",
            dod=["гейт зелёный", "прод перезапущен", "документ отдан"])
        accepted, why = work_store.dod_verdict(work_store.card(row["id"]), "ок")
        self.assertFalse(accepted)
        for criterion in ("гейт зелёный", "прод перезапущен", "документ отдан"):
            self.assertIn(criterion, why)


class HerWordWritesTheCard(WorkStoreBase):
    """Через ЖИВУЮ руку: в отдельном потоке, за потолком времени."""

    def setUp(self):
        super().setUp()
        self.runs_dir = self.base / "runs-home"
        self.manager = run_manager.RunManager(self.runs_dir)
        self.spool = media.MediaSpool(self.base / "spool")
        self.previous = (agent._RUN_MANAGER, agent._MEDIA_SPOOL)
        agent._RUN_MANAGER, agent._MEDIA_SPOOL = self.manager, self.spool
        self.addCleanup(self._restore_agent)

    def _restore_agent(self):
        agent._RUN_MANAGER, agent._MEDIA_SPOOL = self.previous

    def _run(self, suffix: str):
        context = run_context.RunContext.create(
            run_id=f"run-card-{suffix}", kind="task_window",
            goal="довести зеркало до GitHub", principal_id=agent.PRAXIS_SELF_PRINCIPAL,
            scope="owner", delivery_chat_id=None, model_profile="voice",
        )
        persisted = self.manager.create(context, "# Context\n")
        self.manager.transition(persisted.run_id, "running", expected="pending")
        return self.manager.context(persisted.run_id)

    def _say(self, context, **args) -> str:
        with run_context.bind_run(context):
            work_loop.reset()
            return agent._call_tool_with_ceiling(
                "task_control", agent.tool_task_control, args)

    def test_wait_opens_a_card_because_the_work_outlives_the_run(self):
        context = self._run("wait")
        out = self._say(context, action="wait", wake_on="жду сборку")
        self.assertIn("карточка", out)
        row = work_store.for_run(context.run_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "waiting")
        self.assertEqual(row["goal"], "довести зеркало до GitHub")
        self.assertEqual(row["wake"]["wake_on"], "жду сборку")

    def test_blocked_opens_a_card_and_names_the_obstacle(self):
        context = self._run("blocked")
        self._say(context, action="blocked", blocker="на хосте нет такого root")
        row = work_store.for_run(context.run_id)
        self.assertEqual(row["status"], "blocked")
        self.assertEqual(row["blocker"], "на хосте нет такого root")

    def test_done_alone_opens_no_card_at_all(self):
        """Работа кончилась вместе с ходом — карточке нечего переживать."""
        context = self._run("done")
        self._say(context, action="done", evidence="гейт 3819 OK")
        self.assertIsNone(work_store.for_run(context.run_id))
        self.assertEqual(work_store.task_ids(), [])

    def test_done_closes_a_card_that_already_existed(self):
        context = self._run("cycle")
        self._say(context, action="wait", wake_on="жду")
        opened = work_store.for_run(context.run_id)
        with run_context.bind_run(context):
            work_loop.reset()
        self._say(context, action="done", evidence="сборка зелёная, лог result-0007")
        closed = work_store.card(opened["id"])
        self.assertEqual(closed["status"], "done")
        self.assertIn("сборка зелёная, лог result-0007", closed["evidence"])

    def test_done_without_a_trace_does_not_close_the_turn(self):
        """Главное поведение среза: ход возвращается ей, а не закрывается молча."""
        context = self._run("no-evidence")
        out = self._say(context, action="done", summary="всё сделала")
        self.assertIn("не принято", out)
        with run_context.bind_run(context):
            self.assertIsNone(work_loop.taken())

    def test_the_lever_returns_the_previous_behaviour(self):
        context = self._run("lever-off")
        with mock.patch.dict("os.environ", {"PRAXIS_WORK_DOD": "off"}):
            out = self._say(context, action="done", summary="всё сделала")
        self.assertIn("принято: done без evidence", out)
        with run_context.bind_run(context):
            self.assertEqual((work_loop.taken() or {}).get("action"), "done")

    def test_a_broken_board_never_takes_her_hand_away(self):
        context = self._run("broken-board")
        with mock.patch.object(work_store, "open_card",
                               side_effect=OSError("диск кончился")):
            out = self._say(context, action="wait", wake_on="жду")
        self.assertIn("карточка НЕ записалась", out)
        with run_context.bind_run(context):
            self.assertEqual((work_loop.taken() or {}).get("action"), "wait")


if __name__ == "__main__":
    unittest.main()
