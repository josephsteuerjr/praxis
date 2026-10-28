"""Канон работы — ЕЁ леджер желаний. Движок читает его и не пишет в него ни строки.

Её решение 12.08 дословно: «Желания — мой канон… work_store пусть остаётся узким
operational-слоем: активная работа конкретного прогона, её attention/отсрочка/состояние и
ссылка на желание. Не второй жизнью работы.» Эти тесты охраняют именно эту границу.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import desires
import run_context
import run_manager
import work_engine
import work_source
import work_store


class WorkSourceBase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="praxis-work-source-")
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)
        self.previous_store = work_store.BASE
        work_store.BASE = self.base
        self.addCleanup(self._restore)
        self.ledger = desires.DesireLedger(self.base / "memory")
        patcher = mock.patch.object(desires, "_ledger", return_value=self.ledger)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore(self):
        work_store.BASE = self.previous_store

    def _desire(self, statement: str, *, next_move: str = "проверить живьём",
                activate: bool = True) -> dict:
        # `notice`/`want` возвращают СОБЫТИЕ (её леджер append-only), состояние лежит внутри.
        event = self.ledger.notice(statement, source="тест", why_it_matters="тест")
        desire_id = str(event.get("desire_id") or "")
        if activate:
            event = self.ledger.want(desire_id, note="выбрано", next_move=next_move)
        return {"id": desire_id, "event": event}

    def _parked(self, statement: str, *, wake_on: str = "жду условие",
                next_move: str = "проверить живьём") -> dict:
        """Работа, которую ОНА припарковала словом «жду» — то есть прерванная."""
        row = self._desire(statement, next_move=next_move)
        card = work_store.open_card(goal=statement[:60], desire_id=row["id"],
                                    run_id="run-parked")
        work_store.transition(card["id"], "waiting", wake={"wake_on": wake_on})
        return row


class TheEngineRaisesInterruptedWorkNotWishes(WorkSourceBase):
    """⚑ Главный урок холостого прогона 12.08.

    Первая редакция спрашивала «чего она хочет» и собралась поднять две работы, у которых
    её же следующий ход начинается с условия («когда identity-load снизится до L <= 7.25»,
    «не запускать до accept-package gate»). Вычислителя условий нет — значит она получила
    бы окно на работу, которую сама отложила. Правильный вопрос: «что оборвалось».
    """

    def test_a_wanted_but_untouched_desire_is_not_raised(self):
        self._desire("Хочу когда-нибудь дожать зеркало")
        self.assertEqual(work_source.works(), [],
                         "движок поднял желаемое вместо прерванного")

    def test_work_she_parked_herself_is_raised_and_says_so(self):
        row = self._desire("Хочу дожать зеркало")
        card = work_store.open_card(goal="зеркало", desire_id=row["id"], run_id="run-1")
        work_store.transition(card["id"], "waiting", wake={"wake_on": "жду транспорт"})
        works = work_source.works()
        self.assertEqual(len(works), 1)
        self.assertEqual(works[0].source, "desire")
        self.assertIn("жду транспорт", works[0].interrupted_by)
        self.assertTrue(work_engine.judge(works[0]).raise_now)

    def test_a_desire_without_a_next_move_is_not_a_task_for_the_engine(self):
        """Намерение без поверхности действия — не задание. Час уже выдумывал их однажды."""
        row = self._desire("Хочу разобраться вообще", next_move="")
        self.assertTrue(row["id"])
        self.assertEqual(work_source.works(), [])

    def test_a_latent_desire_is_never_raised_for_her(self):
        """«Похоже, я хочу» — ещё не выбранное. Поднять его значит решить за неё."""
        self._desire("Похоже, я хочу", activate=False)
        self.assertEqual(work_source.works(), [])

    def test_the_raised_frame_quotes_her_own_words_and_names_the_source(self):
        row = self._parked("Хочу довести PR", next_move="дождаться транспорта",
                           wake_on="жду авторизованный транспорт")
        work = work_source.works()[0]
        goal = work_source.goal_for(work)
        self.assertIn("Хочу довести PR", goal)
        self.assertIn("дождаться транспорта", goal)
        self.assertIn("движок в него не пишет", goal)
        self.assertIn("жду авторизованный транспорт", goal)
        self.assertIn("ОБОРВАЛАСЬ", goal)
        self.assertEqual(row["id"], work.id)

    def test_a_desire_whose_run_died_for_real_is_raised(self):
        """Второй вид обрыва: работа была в полёте, и прогон умер по-настоящему."""
        row = self._desire("Хочу дожать сборку")
        self.ledger.choose(row["id"], note="выбрала", next_move="дожать")
        self.ledger.act(row["id"], note="взялась", run_id="run-dead-one")
        manager = run_manager.RunManager(self.base)
        context = run_context.RunContext.create(
            run_id="run-dead-one", kind="task_window", goal="дожать сборку",
            principal_id="praxis:self", scope="owner")
        manager.create(context, "# Context\n")
        manager.transition("run-dead-one", "running", expected="pending")
        manager.transition("run-dead-one", "failed", expected="running", reason="упал")

        works = work_source.works()
        self.assertEqual(len(works), 1)
        self.assertIn("умер со статусом «failed»", works[0].interrupted_by)

    def test_a_desire_whose_run_finished_well_is_left_alone(self):
        row = self._desire("Хочу дожать другое")
        self.ledger.choose(row["id"], note="выбрала", next_move="дожать")
        self.ledger.act(row["id"], note="взялась", run_id="run-fine-one")
        manager = run_manager.RunManager(self.base)
        context = run_context.RunContext.create(
            run_id="run-fine-one", kind="task_window", goal="дожать другое",
            principal_id="praxis:self", scope="owner")
        manager.create(context, "# Context\n")
        manager.transition("run-fine-one", "running", expected="pending")
        manager.transition("run-fine-one", "done", expected="running", reason="сделано")
        self.assertEqual(work_source.works(), [])


class TheEngineNeverWritesHerCanon(WorkSourceBase):
    def test_raising_work_touches_only_the_operational_layer(self):
        self._parked("Хочу проверить реле")
        work = work_source.works()[0]
        before = len(self.ledger.events(work.id)) if hasattr(self.ledger, "events") else None
        work_source.note_attempt(work)
        card = work_store.for_desire(work.id)
        self.assertEqual(card["attempts"], 1)
        self.assertEqual(card["desire_id"], work.id)
        self.assertTrue(card["fingerprint"])
        if before is not None:
            self.assertEqual(len(self.ledger.events(work.id)), before,
                             "движок дописал в её канон")

    def test_the_module_that_names_the_canon_is_the_only_one(self):
        engine = Path(work_engine.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import desires", engine,
                         "политика узнала канон — она обязана оставаться слепой")


class ProgressIsJudgedByHerRecordsNotOurs(WorkSourceBase):
    def test_an_idle_raise_accumulates_and_backs_off(self):
        self._parked("Хочу не двигаться")
        work = work_source.works()[0]
        for _ in range(4):
            work_source.note_attempt(work)
        again = work_source.works()[0]
        self.assertEqual(again.attempts, 4)
        verdict = work_engine.judge(again)
        self.assertFalse(verdict.raise_now)
        self.assertIn("отсрочка", verdict.reason)

    def test_her_own_move_resets_the_count(self):
        row = self._parked("Хочу сдвинуться")
        work = work_source.works()[0]
        work_source.note_attempt(work)
        self.assertEqual(work_store.for_desire(work.id)["attempts"], 1)
        self.ledger.choose(row["id"], note="сдвинулась сама", next_move="дальше")
        self.assertEqual(work_source.reconcile(), [work.id])
        self.assertEqual(work_store.for_desire(work.id)["attempts"], 0)

    def test_no_move_keeps_the_count(self):
        self._parked("Хочу стоять")
        work = work_source.works()[0]
        work_source.note_attempt(work)
        self.assertEqual(work_source.reconcile(), [])
        self.assertEqual(work_store.for_desire(work.id)["attempts"], 1)

    def test_after_enough_idle_raises_it_asks_for_her_look(self):
        self._parked("Хочу застрять")
        work = work_source.works()[0]
        for _ in range(5):
            work_source.note_attempt(work)
        verdict = work_engine.judge(work_source.works()[0])
        self.assertTrue(verdict.attention)
        self.assertFalse(verdict.raise_now)


class TheClockOnlyAsksWhetherSomethingIsDue(unittest.TestCase):
    def test_the_runner_tick_does_not_decide_what_to_work_on(self):
        source = Path("mtproto_runner.py").read_text(encoding="utf-8")
        start = source.index("async def _work_engine_once")
        body = source[start:source.index("def _clock_jobs", start)]
        self.assertIn("work_source.plan_now", body)
        self.assertIn("note_attempt", body)
        for forbidden in ("desires.", "statement", "next_move"):
            self.assertNotIn(forbidden, body,
                             "часы полезли решать, чем ей заниматься")

    def test_the_tick_is_registered_and_off_by_default(self):
        source = Path("mtproto_runner.py").read_text(encoding="utf-8")
        self.assertIn('"work_engine": (300.0, _work_engine_once)', source)
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("PRAXIS_WORK_ENGINE", None)
            self.assertFalse(work_engine.enabled())


if __name__ == "__main__":
    unittest.main()
