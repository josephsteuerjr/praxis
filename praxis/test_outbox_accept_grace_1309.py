# -*- coding: utf-8 -*-
"""Двойной ответ 13.09 (#104546 → #104554): два шва, оба закрыты здесь.

1. Рука ждала приёмку Telegram 30 с; отправка на лагающей петле заняла 33 с; рука объявила
   `DurableSideEffectPending` через 13 мс после того, как Telegram уже принял сообщение
   («TimeoutError (state=accepted)»), и прогон встал в паузу. Теперь приёмке дают несколько
   секунд догнать таймаут (`_accepted_after_timeout`), и поздняя приёмка = успех.
2. Проекция принятого шла тиком `direct_outbox` в общем последовательном проходе часов и
   ждала тяжёлые заботы 9–23 минуты; её же ответа не было в кадре. Теперь `direct_outbox`
   живёт на своём тике (`_clock_side`), таблица `_clock_jobs()` не меняется.

Запуск: python praxis_test.py test_outbox_accept_grace_1309 -v
"""
from __future__ import annotations

import asyncio
import os
import time
import unittest
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import mtproto_runner as mr  # noqa: E402


class _Outbox:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = 0

    def get(self, key, *, verify_file=True):
        self.calls += 1
        return self.rows.pop(0) if self.rows else None


class AcceptedAfterTimeout(unittest.TestCase):
    def test_late_acceptance_is_success_with_the_outbox_row(self):
        ob = _Outbox([None, {"key": "k", "state": "pending"},
                      {"key": "k", "state": "accepted", "receipt": {"message_id": 104546}}])
        with mock.patch.object(mr, "_direct_outbox", lambda: ob):
            row = mr._accepted_after_timeout("k", TimeoutError(), grace=3.0, poll=0.05)
        self.assertIsNotNone(row)
        self.assertEqual(row.get("state"), "accepted")
        self.assertEqual(row["receipt"]["message_id"], 104546)
        self.assertEqual(ob.calls, 3, "перечитывает запись, пока не accepted")

    def test_other_errors_do_not_wait_at_all(self):
        ob = _Outbox([{"key": "k", "state": "accepted"}])
        with mock.patch.object(mr, "_direct_outbox", lambda: ob):
            self.assertIsNone(mr._accepted_after_timeout("k", RuntimeError("boom"), grace=3.0))
        self.assertEqual(ob.calls, 0, "не таймаут — ящик даже не читается")

    def test_never_accepted_gives_up_after_grace(self):
        ob = _Outbox([])
        started = time.time()
        with mock.patch.object(mr, "_direct_outbox", lambda: ob):
            self.assertIsNone(mr._accepted_after_timeout("k", TimeoutError(), grace=0.25, poll=0.05))
        self.assertLess(time.time() - started, 2.0)
        self.assertGreaterEqual(ob.calls, 2)

    def test_unreadable_outbox_is_not_fatal(self):
        class Broken:
            def get(self, key, *, verify_file=True):
                raise OSError("locked")
        with mock.patch.object(mr, "_direct_outbox", lambda: Broken()):
            self.assertIsNone(mr._accepted_after_timeout("k", TimeoutError(), grace=0.1, poll=0.05))


class SideCares(unittest.TestCase):
    def test_direct_outbox_leaves_the_shared_pass_and_the_table_is_intact(self):
        jobs = mr._clock_jobs()
        main, side = mr._split_clock_jobs(jobs)
        self.assertIn("direct_outbox", side)
        self.assertNotIn("direct_outbox", main)
        self.assertIn("durable_resume", main, "возобновление остаётся в общем проходе")
        self.assertEqual(set(main) | set(side), set(jobs))
        self.assertEqual(side["direct_outbox"], jobs["direct_outbox"])

    def test_side_tick_is_not_starved_by_a_slow_care(self):
        ticks: list[float] = []

        async def fast() -> None:
            ticks.append(time.monotonic())

        async def scenario() -> None:
            task = asyncio.create_task(mr._clock_side("fast", 0.02, fast))
            # «тяжёлая забота» держит общий проход 0,3 с — свой тик этого не замечает
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        with mock.patch.object(mr, "_CLOCK_STARTUP_DUE", frozenset({"fast"})):
            asyncio.run(scenario())
        self.assertGreaterEqual(len(ticks), 5, ticks)

    def test_side_tick_survives_a_failing_care(self):
        calls: list[int] = []

        async def boom() -> None:
            calls.append(1)
            raise RuntimeError("упала")

        async def scenario() -> None:
            task = asyncio.create_task(mr._clock_side("boom", 0.02, boom))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        with mock.patch.object(mr, "_CLOCK_STARTUP_DUE", frozenset({"boom"})):
            asyncio.run(scenario())
        self.assertGreaterEqual(len(calls), 3, "упавшая забота логируется и не убивает тик")


class SendTimeoutPaths(unittest.TestCase):
    def test_all_three_sends_accept_during_grace_or_retry_journal(self):
        from contextlib import ExitStack
        from types import SimpleNamespace
        import tempfile
        from pathlib import Path
        import agent
        import telegram_outbox
        for tool in ("send_message", "reply", "send_file"):
            for at_journal in (False, True):
                with self.subTest(tool=tool, at_journal=at_journal), tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
                    ob = telegram_outbox.TelegramOutbox(Path(tmp) / "outbox")
                    path = Path(tmp) / "hello.txt"
                    path.write_text("hello", encoding="utf-8")
                    ent = SimpleNamespace(id=42)
                    route = SimpleNamespace(peer_id=42, topic_id=None)
                    execution = {"run_id": "run-grace", "call_id": "call", "tool": tool}
                    def patch(obj, name, **kw):
                        return stack.enter_context(mock.patch.object(obj, name, **kw))
                    patch(mr, "_direct_outbox", return_value=ob)
                    patch(mr, "_direct_tool_execution", return_value=execution)
                    patch(mr, "_route_from_reference", return_value=route)
                    patch(mr, "_marked_peer_id", return_value=42)
                    patch(mr, "_entity_kind", return_value="user")
                    patch(mr, "_ent_label", return_value="peer")
                    patch(mr, "OWNER_ID", new=42)
                    patch(mr, "_durable_outbox_projection", side_effect=lambda ex, live: live)
                    patch(agent, "_active_chat", return_value="42")
                    patch(agent, "run_direct_outbox_prepared")
                    project = patch(agent, "project_direct_outbox_acceptance")
                    patch(agent.notes, "said_recently", return_value=False)
                    patch(mr.social_pulse, "allow_outbound", return_value=(True, ""))
                    resolve = (route, ent, "") if tool == "send_file" else ent
                    send = patch(mr, "_threadsafe_result", side_effect=[resolve, TimeoutError()])
                    accepted = {}
                    def settle(key, exc):
                        if not at_journal:
                            accepted.update(ob.mark_accepted(key, message_id=77))
                        return None if at_journal else accepted
                    patch(mr, "_accepted_after_timeout", side_effect=settle)
                    record_failure = mr._record_direct_outbox_failure
                    def journal_acceptance(key, exc):
                        # Real ledger acceptance between final grace read and retry write.
                        accepted.update(ob.mark_accepted(key, message_id=77))
                        return record_failure(key, exc)
                    journal = patch(mr, "_record_direct_outbox_failure", side_effect=journal_acceptance)
                    if tool == "send_file":
                        result = mr._sync_send_file(str(path), to="42")
                    elif tool == "reply":
                        result = mr._sync_reply("42", "hello")
                    else:
                        result = mr._sync_send_message("42", "hello")
                    self.assertIn("77", result)
                    project.assert_called_once_with(accepted)
                    self.assertEqual(send.call_count, 2)  # resolve + one send, never resend
                    self.assertEqual(journal.call_count, int(at_journal))


class ClockOwnership(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_clock_cancels_side_and_resume_only_runs_once(self):
        started = asyncio.Event()
        stopped = asyncio.Event()
        blocked = asyncio.Event()
        resumes = []
        async def side():
            started.set()
            try:
                await blocked.wait()
            finally:
                stopped.set()
        async def resume():
            resumes.append(1)
            await blocked.wait()
        jobs = {"direct_outbox": (0.01, side), "durable_resume": (0.01, resume)}
        with mock.patch.object(mr, "_clock_jobs", return_value=jobs), mock.patch.object(mr, "_CLOCK_STARTUP_DUE", frozenset(jobs)):
            task = asyncio.create_task(mr._clock())
            try:
                await asyncio.wait_for(started.wait(), 1)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertTrue(stopped.is_set())
            self.assertEqual(resumes, [1])

    def test_grace_uses_monotonic_and_caps_last_sleep(self):
        ob = _Outbox([])
        with mock.patch.object(mr, "_direct_outbox", return_value=ob), mock.patch.object(mr.time, "monotonic", side_effect=[10, 10, 10.1]), mock.patch.object(mr.time, "sleep") as sleep:
            self.assertIsNone(mr._accepted_after_timeout("k", TimeoutError(), grace=0.1, poll=5))
        self.assertAlmostEqual(sleep.call_args.args[0], 0.1)


if __name__ == "__main__":
    unittest.main()
