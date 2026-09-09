"""Политика оборота 3: что созрело, что ждёт и почему — без источника правды и без часов.

Эти тесты нарочно не знают, откуда взялась работа: движок строится ДО того, как она
выбрала канон (её леджер желаний или файловая карточка). Если однажды сюда просочится
`memory/desires` или `work_store` — покраснеет последний тест, и это его работа.
"""
from __future__ import annotations

import datetime as dt
import unittest
from pathlib import Path
from unittest import mock

import work_engine
from work_engine import Work


NOW = dt.datetime(2026, 8, 12, 12, 0, 0, tzinfo=dt.timezone.utc)


def at(minutes: float) -> str:
    return work_engine._iso(NOW + dt.timedelta(minutes=minutes))


class TheLadderIsTheOneWeAlreadyHave(unittest.TestCase):
    def test_three_free_attempts_then_doubling_up_to_an_hour(self):
        self.assertEqual([work_engine.backoff_seconds(n) for n in range(0, 8)],
                         [0.0, 0.0, 0.0, 60.0, 120.0, 240.0, 480.0, 960.0])
        self.assertEqual(work_engine.backoff_seconds(99), 3600.0)

    def test_it_matches_the_durable_resume_ladder_because_two_would_drift(self):
        import agent
        for attempts in range(0, 10):
            self.assertEqual(work_engine.backoff_seconds(attempts),
                             agent.resume_backoff_seconds(attempts),
                             "лестницы разошлись — одна беда, две лестницы")


class EveryHoldNamesItsReason(unittest.TestCase):
    def test_a_ripe_work_is_raised_and_says_why(self):
        verdict = work_engine.judge(
            Work(id="w1", goal="дожать зеркало", status="waiting",
                 interrupted_by="прогон умер"), now=NOW)
        self.assertTrue(verdict.raise_now)
        self.assertIn("работа оборвана", verdict.reason)
        self.assertIn("прогон умер", verdict.reason)

    def test_work_that_is_merely_wanted_is_refused_by_the_policy_too(self):
        """Пояс поверх ремня: отбор «только прерванное» живёт у источника, но политика
        обязана уметь отказать и сама — иначе новый источник однажды принесёт сюда
        «хочу когда-нибудь», и движок начнёт будить её по желаниям."""
        verdict = work_engine.judge(
            Work(id="w0", goal="хочу когда-нибудь", status="waiting"), now=NOW)
        self.assertFalse(verdict.raise_now)
        self.assertIn("ничем не оборвана", verdict.reason)

    def test_her_own_term_holds_the_work_and_names_the_moment(self):
        work = Work(id="w2", goal="ждать сборку", status="waiting", not_before=at(30), interrupted_by="прогон умер")
        verdict = work_engine.judge(work, now=NOW)
        self.assertFalse(verdict.raise_now)
        self.assertIn(at(30), verdict.reason)
        self.assertAlmostEqual(verdict.next_try_in_seconds, 1800, delta=2)

    def test_blocked_is_never_raised_by_the_clock(self):
        verdict = work_engine.judge(
            Work(id="w3", goal="нет доступа", status="blocked", interrupted_by="прогон умер"), now=NOW)
        self.assertFalse(verdict.raise_now)
        self.assertIn("не проходит оттого, что прошёл час", verdict.reason)

    def test_her_freeze_beats_everything(self):
        verdict = work_engine.judge(
            Work(id="w4", goal="моё, не трогай", status="waiting", frozen_by_her=True, interrupted_by="прогон умер"),
            now=NOW)
        self.assertFalse(verdict.raise_now)
        self.assertIn("не брать эту работу сама", verdict.reason)

    def test_backoff_holds_after_idle_raises_and_then_lets_go(self):
        work = Work(id="w5", goal="не двигается", status="waiting",
                    attempts=4, last_attempt=at(-1), interrupted_by="прогон умер")
        held = work_engine.judge(work, now=NOW)
        self.assertFalse(held.raise_now)
        self.assertIn("отсрочка", held.reason)
        later = work_engine.judge(work, now=NOW + dt.timedelta(minutes=3))
        self.assertTrue(later.raise_now)

    def test_the_first_three_attempts_are_free(self):
        work = Work(id="w6", goal="свежая", status="waiting", attempts=2,
                    last_attempt=at(-0.1), interrupted_by="прогон умер")
        self.assertTrue(work_engine.judge(work, now=NOW).raise_now)

    def test_many_idle_raises_ask_for_her_look_instead_of_burial(self):
        work = Work(id="w7", goal="мешает не время", status="waiting",
                    attempts=5, last_attempt=at(-600), interrupted_by="прогон умер")
        verdict = work_engine.judge(work, now=NOW)
        self.assertFalse(verdict.raise_now)
        self.assertTrue(verdict.attention)
        self.assertIn("мешает не время", verdict.reason)


class TheCapNeverTruncatesSilently(unittest.TestCase):
    def test_work_over_the_cap_is_held_with_a_reason_not_dropped(self):
        items = [Work(id="a", goal="первая", status="waiting", not_before=at(-30), interrupted_by="прогон умер"),
                 Work(id="b", goal="вторая", status="waiting", not_before=at(-20), interrupted_by="прогон умер"),
                 Work(id="c", goal="третья", status="waiting", not_before=at(-10), interrupted_by="прогон умер")]
        with mock.patch.dict("os.environ", {"PRAXIS_WORK_ENGINE_CAP": "2"}):
            raised, held = work_engine.plan(items, now=NOW)
        self.assertEqual([v.work.id for v in raised], ["a", "b"])
        self.assertEqual(len(raised) + len(held), 3, "работа исчезла из отчёта")
        self.assertIn("потолок движка", held[0].reason)

    def test_running_work_eats_the_room(self):
        items = [Work(id="a", goal="одна", status="waiting", interrupted_by="прогон умер")]
        with mock.patch.dict("os.environ", {"PRAXIS_WORK_ENGINE_CAP": "1"}):
            raised, held = work_engine.plan(items, running=1, now=NOW)
        self.assertEqual(raised, [])
        self.assertIn("потолок движка", held[0].reason)

    def test_the_oldest_term_goes_first_so_old_work_does_not_drown(self):
        items = [Work(id="new", goal="свежая", status="waiting", not_before=at(-1), interrupted_by="прогон умер"),
                 Work(id="old", goal="давняя", status="waiting", not_before=at(-500), interrupted_by="прогон умер")]
        with mock.patch.dict("os.environ", {"PRAXIS_WORK_ENGINE_CAP": "1"}):
            raised, _ = work_engine.plan(items, now=NOW)
        self.assertEqual([v.work.id for v in raised], ["old"])

    def test_the_frame_names_the_cap_before_it_bites(self):
        with mock.patch.dict("os.environ", {"PRAXIS_WORK_ENGINE": "on",
                                            "PRAXIS_WORK_ENGINE_CAP": "3"}):
            text = work_engine.describe([Work(id="a", goal="дело", status="waiting", interrupted_by="прогон умер")],
                                        now=NOW)
        self.assertIn("до 3 работ", text)
        self.assertIn("дело", text)

    def test_the_lever_down_means_silence(self):
        with mock.patch.dict("os.environ", {"PRAXIS_WORK_ENGINE": "off"}):
            self.assertEqual(work_engine.describe([Work(id="a", goal="д", status="waiting", interrupted_by="прогон умер")]), "")


class TheEngineKnowsNeitherTheClockNorTheOwnerOfTheTruth(unittest.TestCase):
    def test_it_does_not_schedule_anything_itself(self):
        source = Path(work_engine.__file__).read_text(encoding="utf-8")
        for forbidden in ("threading", "asyncio", "sched", "cron", "time.sleep", "Timer"):
            self.assertNotIn(forbidden, source,
                             "в движок приехало расписание — он тихо стал часами")

    def test_it_does_not_know_where_the_work_lives(self):
        """Источник правды выбирает ОНА. Пока не выбрала — зашивать догадку нельзя."""
        source = Path(work_engine.__file__).read_text(encoding="utf-8")
        for owner in ("import work_store", "import desires", "memory/work/tasks",
                      "CURRENT.md", "events.jsonl"):
            self.assertNotIn(owner, source,
                             "движок зашил канон, которого она ещё не называла")


if __name__ == "__main__":
    unittest.main()
