"""Повторная неудача возобновления стоит времени, а не нового вызова через 45 секунд.

ЗАМЕР ПРОДА 10.08.2026.  Апстрим ответил 401 на всё; прогон `cb4898bf` повторил
`continue_checkpoint -> failed` 165 раз за 2 часа 20 минут, по три вызова модели на
попытку. В логе при этом стояла ровно та же INFO, что и при здоровой работе: ни одной
строки «я упёрлась». Это пятое возвращение той же семьи, и лечится оно отступлением, а не
пятым именем в списке.

Проверяются свойства, а не форма: отсрочка растёт, у неё есть потолок, движение снимает её
целиком, а новый прогон не наследует чужой штраф.
"""
import os
import pathlib
import tempfile
import unittest

# `mtproto_runner` строит TelegramClient на импорте — как и у соседних тестов раннера,
# сессия здесь одноразовая и пустая, наружу никто не ходит.
_SESSION_DIR = pathlib.Path(tempfile.mkdtemp(prefix="praxis-resume-backoff-"))
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ["TELEGRAM_SESSION"] = str(_SESSION_DIR / "telethon")
os.environ.setdefault("PRAXIS_TEST", "1")


class BackoffGrowsAndHasACeiling(unittest.TestCase):
    def setUp(self):
        import mtproto_runner
        self.runner = mtproto_runner

    def test_first_miss_costs_one_tick_not_zero(self):
        self.assertEqual(self.runner._resume_backoff_delay(1),
                         self.runner.RESUME_BACKOFF_BASE_SEC)

    def test_it_actually_grows(self):
        delays = [self.runner._resume_backoff_delay(n) for n in range(1, 6)]
        self.assertEqual(delays, sorted(delays))
        self.assertGreater(delays[-1], delays[0], "отсрочка не растёт — это прежний такт")

    def test_the_ceiling_holds(self):
        """Без потолка отсрочка уехала бы в сутки и прогон замолчал бы навсегда."""
        for n in (10, 50, 165):
            self.assertLessEqual(self.runner._resume_backoff_delay(n),
                                 self.runner.RESUME_BACKOFF_MAX_SEC)

    def test_no_misses_no_delay(self):
        self.assertEqual(self.runner._resume_backoff_delay(0), 0.0)

    def test_the_measured_disaster_would_have_been_bounded(self):
        """165 попыток подряд при этой лестнице занимают не 2 часа, а больше суток."""
        total = sum(self.runner._resume_backoff_delay(n) for n in range(1, 166))
        self.assertGreater(total, 24 * 3600,
                           "лестница слишком пологая — повтор 10.08 повторился бы")


class TheStateMachineForgetsWhenThingsMove(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import mtproto_runner
        self.runner = mtproto_runner
        self.runner._resume_failures.update(ids=frozenset(), n=0, not_before=0.0)
        self.addCleanup(self.runner._resume_failures.update,
                        ids=frozenset(), n=0, not_before=0.0)

    async def _pass(self, reports, at: float = 0.0):
        """Один проход часов в момент `at` по искусственным часам.

        Время подставляется намеренно: настоящая отсрочка отодвигает следующую попытку на
        минуты, и тест, который её не переживает, проверял бы не свойство, а свой sleep.
        """
        import asyncio
        from unittest import mock
        with mock.patch.object(self.runner.agent, "resume_durable_runs",
                               lambda limit=20: reports), \
                mock.patch.object(self.runner.time, "time", lambda: float(at)), \
                mock.patch.object(self.runner, "_ONE_MIND", asyncio.Lock()):
            await self.runner._durable_resume_once()

    async def test_repeated_failure_of_the_same_run_pushes_the_next_attempt_away(self):
        report = [{"run_id": "run-x", "status": "failed", "plan_kind": "continue_checkpoint"}]
        await self._pass(report, at=1000.0)
        first_wait = self.runner._resume_failures["not_before"] - 1000.0
        await self._pass(report, at=10000.0)
        second_wait = self.runner._resume_failures["not_before"] - 10000.0
        self.assertEqual(self.runner._resume_failures["n"], 2)
        self.assertGreater(second_wait, first_wait,
                           "вторая неудача подряд стоит не дороже первой")

    async def test_a_clean_pass_clears_the_penalty_entirely(self):
        await self._pass([{"run_id": "run-x", "status": "failed"}], at=1000.0)
        self.assertGreater(self.runner._resume_failures["n"], 0)
        await self._pass([{"run_id": "run-x", "status": "done"}], at=10000.0)
        self.assertEqual(self.runner._resume_failures["n"], 0)
        self.assertEqual(self.runner._resume_failures["not_before"], 0.0)

    async def test_a_different_run_does_not_inherit_the_penalty(self):
        """Штраф принадлежит поломанному прогону, а не механизму возобновления."""
        await self._pass([{"run_id": "run-x", "status": "failed"}], at=1000.0)
        await self._pass([{"run_id": "run-x", "status": "failed"}], at=10000.0)
        self.assertEqual(self.runner._resume_failures["n"], 2)
        await self._pass([{"run_id": "run-y", "status": "failed"}], at=100000.0)
        self.assertEqual(self.runner._resume_failures["n"], 1)

    async def test_while_the_penalty_holds_the_planner_is_not_even_called(self):
        """Смысл отсрочки — НЕ ходить в модель. Иначе это косметика в логе."""
        import asyncio
        from unittest import mock
        self.runner._resume_failures.update(
            ids=frozenset({"run-x"}), n=3, not_before=5000.0)
        called = {"n": 0}

        def planner(limit=20):
            called["n"] += 1
            return []

        with mock.patch.object(self.runner.agent, "resume_durable_runs", planner), \
                mock.patch.object(self.runner.time, "time", lambda: 1000.0), \
                mock.patch.object(self.runner, "_ONE_MIND", asyncio.Lock()):
            await self.runner._durable_resume_once()
        self.assertEqual(called["n"], 0, "отсрочка не удержала — цикл всё равно ходил в модель")


if __name__ == "__main__":
    unittest.main()
