"""Вечная петля возобновления: четвёртый рецидив одной семьи — и граница вместо имени.

⚠ ЧТО ИЗМЕРЕНО НА ЖИВОМ ПРОДЕ 09.08. Два рана от 06 и 07 августа крутились **55,8 часа**:
7 646 и 7 504 перехода `running → paused` при 7 и 11 вызовах модели, темп 137 событий в час,
7,9 МБ мусора в двух `events.jsonl`. Причина в манифесте названа дословно: `RunConflict:
receipt tool_side_effect_pending/tool-pending:…:call_… already has different content`,
а под ней — `MessageTooLongError`: её текст не влез в лимит Telegram.

Беда сложена из ДВУХ независимых дефектов, и чинить надо оба:

1. `MessageTooLongError` — это 400 «сам запрос негоден», а не 403 «сюда нельзя». В списке
   постоянных отказов такого класса не было, значит план остался «пригодным к повтору».
2. Расписка о неизвестности писалась `append_event_once` вместе с ЖИВЫМ текстом причины
   (`{класс}: {ошибка} (state=…)`), поэтому вторая попытка почти никогда не совпадала с
   первой байт в байт — и жёсткий конфликт убивал само возобновление.

⚑ И ГЛАВНОЕ: ЭТО ЧЕТВЁРТЫЙ РАЗ. 26.07 — ChatAdminRequiredError, 27.07 —
ChatGuestSendForbiddenError, 03.08 — медиа, 728 отказов за 13 часов, 06.08 — этот. Каждый
раз чинилось ДОБАВЛЕНИЕМ ИМЕНИ в список, и каждый раз следующий неизвестный класс заводил
петлю заново. Список по построению отстаёт от Telegram.

⚠ ПЕРВАЯ ВЕРСИЯ ЭТОГО ФАЙЛА ЗАКРЕПЛЯЛА ПОТОЛОК — «двенадцать холостых возвратов, и ран
больше не кандидат», — и адверсарная проверка снесла её ДО ВЫКАТА. Четыре блокера: потолок
считал ТЕКСТ причины паузы, который соседняя правка как раз убирала; он хоронил ровно те два
рана, ради которых писался; похороны были необратимы по построению; девять минут терпения
списывали бы любой ран при падении мозга. Здесь стоит ОТСРОЧКА вместо потолка и счёт по
факту «ран не сдвинулся» вместо текста причины. Стук не прекращается никогда — он редеет.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "x")
os.environ.setdefault(
    "TELEGRAM_SESSION", str(Path(tempfile.gettempdir()) / "praxis_test_resume_spin"))

import agent
import run_context
import run_manager
import run_resume
import work_loop

# Причина, которую пишет исполнитель возобновления. В коде она больше НЕ КОНСТАНТА и ни
# на что не влияет: страж считает факт «ран не сдвинулся», а не текст. Литерал стоит здесь
# ровно затем, чтобы стенд был похож на живой леджер.
STALL = "recovery executor stopped before transport intent"

from telethon.errors import (
    BadRequestError, ChatAdminRequiredError, FileReferenceExpiredError,
    MediaCaptionTooLongError, MessageEmptyError, MessageTooLongError, PeerFloodError,
)


class WhyThisPlanWillNeverFly(unittest.TestCase):
    """Две РАЗНЫЕ причины «повторять нечего», и она обязана слышать разные слова."""

    def test_a_message_over_the_limit_is_permanent_and_named_by_shape(self):
        """Тот самый класс, который 06.08 увёл два рана в 55,8-часовую петлю."""
        error = MessageTooLongError(request=None)
        self.assertTrue(agent._is_permanent_delivery_error(error),
                        "повтор того же слишком длинного текста не пройдёт никогда")
        self.assertEqual(agent.delivery_refusal_kind(error), "shape",
                         "это отказ ПО ФОРМЕ; сказать ей «нет прав» значит соврать о причине")

    def test_the_family_is_matched_by_shape_not_by_a_list_of_names(self):
        """Граница — суффикс класса, а не перечень: перечень отстаёт от Telegram."""
        for error in (MediaCaptionTooLongError(request=None), MessageEmptyError(request=None)):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(agent.delivery_refusal_kind(error), "shape")

    def test_a_route_refusal_is_still_a_route_refusal(self):
        """Урок 26.07 не отменён: 403-семейство по-прежнему постоянный отказ маршрута."""
        error = ChatAdminRequiredError(request=None)
        self.assertEqual(agent.delivery_refusal_kind(error), "route")

    def test_the_whole_four_hundred_family_was_NOT_taken(self):
        """⚠ Проверка НЕ вакуумна: доказывает, что мы не сгребли все 438 классов 400.

        `PeerFloodError` и `FileReferenceExpiredError` — тоже `BadRequestError`, но там
        повтор ОСМЫСЛЕН: переждать лимит, обновить ссылку. Назвать их «навсегда» — такая
        же ложь, как вечный стук, только в другую сторону: её слово молча не доедет.
        """
        for error in (PeerFloodError(request=None), FileReferenceExpiredError(request=None)):
            with self.subTest(error=type(error).__name__):
                self.assertIsInstance(error, BadRequestError,
                                      "фикстура протухла: класс перестал быть 400-м")
                self.assertEqual(agent.delivery_refusal_kind(error), "",
                                 "этот отказ повторим — объявлять его вечным нельзя")
                self.assertFalse(agent._is_permanent_delivery_error(error))

    def test_a_string_is_never_a_verdict(self):
        """Классификатор по построению отвечает «повторимо» на строку, а не на исключение.

        Это уже стоило ей 728 отказов за 13 часов 03.08 — медийный шов отдавал наверх
        СТРОКУ «queued for retry». Контракт остаётся прежним.
        """
        self.assertEqual(agent.delivery_refusal_kind("MessageTooLongError"), "")


class ARewrittenUncertaintyIsNotAContradiction(unittest.TestCase):
    """Вторая расписка о НЕИЗВЕСТНОСТИ не имеет права убивать возобновление."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="praxis-spin-receipt-")
        self.addCleanup(self._temp.cleanup)
        self.manager = run_manager.RunManager(Path(self._temp.name))
        self._prev = agent._RUN_MANAGER
        agent._RUN_MANAGER = self.manager
        self.addCleanup(lambda: setattr(agent, "_RUN_MANAGER", self._prev))
        self.context = run_context.RunContext.create(
            run_id="run-spin-receipt", kind="chat_turn", goal="pending twice",
            principal_id="100", scope="owner", origin_chat_id="100",
            origin_message_ids=(7,), delivery_chat_id="100", model_profile="voice",
        )
        self.persisted = self.manager.create(self.context, "# ctx\n")
        self.runtime = agent._AgentResumeRuntime.__new__(agent._AgentResumeRuntime)
        self.runtime.manager = self.manager

    def _pause(self, reason: str) -> None:
        agent._AgentResumeRuntime._pause_pending_outbox(
            self.runtime, self.persisted, "call_x", "send_message",
            agent.DurableSideEffectPending("telegram-outbox:run-spin-receipt:tool:call_x",
                                           reason),
        )

    def _kinds(self, kind: str) -> list[dict]:
        return [row for row in self.manager.iter_events(self.persisted.run_id)
                if row.get("kind") == kind]

    def test_the_second_pending_with_a_new_reason_does_not_explode(self):
        """Прежде здесь летел `RunConflict` — и уносил с собой всю попытку возобновления."""
        self._pause("MessageTooLongError: … (state=retrying)")
        self._pause("MessageTooLongError: … (state=retrying#2)")  # живой текст изменился

    def test_the_first_receipt_stays_the_witness(self):
        """Свидетельство — первая расписка. Переписывать её задним числом нельзя."""
        self._pause("первая причина")
        self._pause("вторая причина")
        receipts = self._kinds("tool_side_effect_pending")
        self.assertEqual(len(receipts), 1, "расписка о неизвестности обязана быть одна")
        self.assertEqual(receipts[0].get("reason"), "первая причина")

    def test_the_new_observation_is_recorded_next_to_it_not_swallowed(self):
        """Разночтение остаётся ВИДИМЫМ: молча проглотить его — потерять улику."""
        self._pause("первая причина")
        self._pause("вторая причина")
        again = self._kinds("tool_side_effect_pending_again")
        self.assertEqual([row.get("reason") for row in again], ["вторая причина"])

    def test_an_identical_repeat_still_writes_nothing_new(self):
        """Байт в байт та же расписка — по-прежнему одна запись и ноль наблюдений рядом."""
        self._pause("одна и та же причина")
        self._pause("одна и та же причина")
        self.assertEqual(len(self._kinds("tool_side_effect_pending")), 1)
        self.assertEqual(self._kinds("tool_side_effect_pending_again"), [])


class TheKnockingThinsOutButNeverStops(unittest.TestCase):
    """Отсрочка вместо потолка: редеющий стук, который никого не хоронит."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="praxis-spin-backoff-")
        self.addCleanup(self._temp.cleanup)
        self.manager = run_manager.RunManager(Path(self._temp.name))
        self._prev = agent._RUN_MANAGER
        agent._RUN_MANAGER = self.manager
        self.addCleanup(lambda: setattr(agent, "_RUN_MANAGER", self._prev))
        self.run_id = self._paused_run("a")

    def _paused_run(self, suffix: str) -> str:
        context = run_context.RunContext.create(
            run_id=f"run-backoff-{suffix}", kind="chat_turn", goal=f"backoff {suffix}",
            principal_id="100", scope="owner", origin_chat_id="100",
            origin_message_ids=(7,), delivery_chat_id="100", model_profile="voice",
        )
        persisted = self.manager.create(context, "# ctx\n")
        self.manager.transition(persisted.run_id, "running", expected="pending")
        self.manager.transition(persisted.run_id, "paused", expected="running",
                                reason=STALL,
                                details={work_loop.PAUSE_KIND_KEY:
                                         work_loop.PAUSE_PROCESS_RECOVERY})
        return persisted.run_id

    def _tick(self, *, pause_reason: str = STALL,
              progress: bool = False, elapsed: float = 0.0) -> list[str]:
        """Один такт часов возобновления. `progress` — попытка реально сдвинула ран."""
        seen: list[str] = []

        def fake(run_id: str) -> dict:
            seen.append(run_id)
            # Настоящий исполнитель всегда шевелит статусом; это НЕ продвижение.
            self.manager.append_event(run_id, "status_changed", from_status="paused",
                                      to_status="running", reason="resume lease claimed")
            self.manager.append_event(run_id, "status_changed", from_status="running",
                                      to_status="paused", reason=pause_reason)
            if progress:
                self.manager.store_result(run_id, "работа", name="tool", call_id="call_p")
            return {"run_id": run_id, "status": "paused", "effects_started": False}

        with mock.patch.object(agent, "resume_durable_run", side_effect=fake), \
                mock.patch.object(agent, "_seconds_since", return_value=elapsed):
            agent.resume_durable_runs(limit=20)
        return seen

    def _idle_marks(self) -> list[dict]:
        return [row for row in self.manager.iter_events(self.run_id)
                if row.get("kind") == agent._RESUME_IDLE_EVENT]

    def test_the_ladder_is_flat_then_doubles_then_caps_at_an_hour(self):
        self.assertEqual([agent.resume_backoff_seconds(n) for n in range(0, 8)],
                         [0.0, 0.0, 0.0, 60.0, 120.0, 240.0, 480.0, 960.0])
        self.assertEqual(agent.resume_backoff_seconds(50), agent.RESUME_BACKOFF_CAP_SECONDS)

    def test_the_first_attempts_are_free(self):
        """Прерванный ран обязан получить свои быстрые попытки: чаще всего он доезжает."""
        for _ in range(agent.RESUME_FREE_ATTEMPTS):
            self.assertIn(self.run_id, self._tick())

    def test_after_the_free_attempts_the_knocking_waits(self):
        for _ in range(agent.RESUME_FREE_ATTEMPTS):
            self._tick()
        self.assertNotIn(self.run_id, self._tick(elapsed=1.0),
                         "отсрочка не включилась — стук остался прежним")

    def test_waiting_writes_nothing_at_all(self):
        """Отсрочка не имеет права раздувать сама себя: 3 288 записей в сутки этим и были."""
        for _ in range(agent.RESUME_FREE_ATTEMPTS):
            self._tick()
        before = len(list(self.manager.iter_events(self.run_id)))
        for _ in range(5):
            self._tick(elapsed=1.0)
        self.assertEqual(len(list(self.manager.iter_events(self.run_id))), before)

    def test_the_run_is_NEVER_abandoned(self):
        """⚠ ГЛАВНОЕ ОТЛИЧИЕ ОТ ПОТОЛКА, КОТОРЫЙ СНЕСЛИ. Ран не хоронится никогда.

        Прежняя версия исключала его из кандидатов навсегда, и вернуть его было нечем:
        пять из семи «признаков продвижения» на `paused` физически не пишутся. Здесь
        время всегда рано или поздно наступает.
        """
        for _ in range(agent.RESUME_FREE_ATTEMPTS + 6):
            self._tick(elapsed=agent.RESUME_BACKOFF_CAP_SECONDS + 1.0)
        self.assertIn(self.run_id,
                      self._tick(elapsed=agent.RESUME_BACKOFF_CAP_SECONDS + 1.0))

    def test_the_pause_reason_does_not_matter_at_all(self):
        """⚠ ИМЕННО ЭТО СЛОМАЛО ПЕРВУЮ ВЕРСИЮ, и здесь оно под тестом.

        Терпимость к `RunConflict` увела паузу под другую причину — и страж, считавший
        ТЕКСТ, ослеп ровно к той петле, ради которой писался. Считается факт «не сдвинулся».
        """
        awaiting = "durable send_message intent awaits Telegram acceptance"
        for _ in range(agent.RESUME_FREE_ATTEMPTS):
            self._tick(pause_reason=awaiting)
        self.assertNotIn(self.run_id, self._tick(pause_reason=awaiting, elapsed=1.0),
                         "петля под другой причиной снова невидима — это и был блокер")

    def test_progress_resets_the_ladder(self):
        for _ in range(agent.RESUME_FREE_ATTEMPTS):
            self._tick()
        self._tick(progress=True, elapsed=agent.RESUME_BACKOFF_CAP_SECONDS + 1.0)
        self.assertIn(self.run_id, self._tick(elapsed=1.0),
                      "ран сдвинулся — отсрочка обязана обнулиться")

    def test_a_hand_saying_resume_beats_the_backoff(self):
        """Явное «возобнови» от неё или от Егора не упирается в автоматическую отсрочку."""
        for _ in range(agent.RESUME_FREE_ATTEMPTS):
            self._tick()
        self.assertNotIn(self.run_id, self._tick(elapsed=1.0))
        self.manager.append_event(self.run_id, "resume_authorized", actor="owner")
        self.assertIn(self.run_id, self._tick(elapsed=1.0),
                      "человеку ответили «ок» о работе, которая не начнётся — так было")

    def test_a_restart_starts_the_count_over(self):
        """После рестарта КОД ДРУГОЙ: судить его по попыткам прошлой жизни нельзя.

        Это же и лечит два прод-рана: их 3 848 холостых стуков остались в прошлой эпохе,
        и починенный путь до них доедет первым же тактом.
        """
        for _ in range(agent.RESUME_FREE_ATTEMPTS):
            self._tick()
        self.assertNotIn(self.run_id, self._tick(elapsed=1.0))
        with mock.patch.object(agent, "_RESUME_EPOCH", run_manager._utc_now()):
            self.assertIn(self.run_id, self._tick(elapsed=1.0))

    def test_twelve_real_claim_pause_idle_cycles_accumulate_and_warn(self):
        """Production contour must accumulate evidence, not reset it at each new pause."""
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            manifest = self.manager.manifest(self.run_id)
            self.manager.claim_resume(
                self.run_id, expected_revision=manifest["revision"],
                expected_event_seq=manifest["event_seq"], actor="runtime:test")
            self.manager.transition(
                self.run_id, "paused", expected="running", reason="same idle recovery",
                details={work_loop.PAUSE_KIND_KEY: work_loop.PAUSE_PROCESS_RECOVERY})
            mark, _ = agent._resume_guard_state(self.manager, self.run_id)
            self.manager.append_event(
                self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt + 1,
                boundary_seq=mark[3])
            self.assertEqual(agent._resume_guard_state(self.manager, self.run_id)[1],
                             attempt + 1)
        self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "warned")

    def test_two_phase_guard_warns_then_closes_only_after_ten_more_minutes(self):
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(
                self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt + 1)
        manifest = self.manager.manifest(self.run_id)
        revision = manifest["revision"]

        with mock.patch.object(agent, "_seconds_since", return_value=1.0):
            self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "warned")
            self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "warned")
        warned = self.manager.manifest(self.run_id)
        self.assertEqual(warned["status"], "paused")
        self.assertEqual(warned["resume_stale_guard"]["phase"], "warned")
        self.assertEqual(warned["revision"], revision + 1)

        with mock.patch.object(
            agent, "_seconds_since",
            return_value=agent.RESUME_STALE_QUIET_SECONDS + 1,
        ):
            self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "closed")
        self.assertEqual(self.manager.manifest(self.run_id)["status"], "cancelled")

    def test_guard_is_reason_agnostic_and_includes_preexisting_blocked_runs(self):
        blocked = self._paused_run("legacy-blocked")
        self.manager.transition(blocked, "blocked", expected="paused", reason="arbitrary prose")
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS + 700):
            self.manager.append_event(blocked, agent._RESUME_IDLE_EVENT, attempt=attempt + 1)
        self.assertEqual(agent._resume_stale_guard(self.manager, blocked), "warned")
        self.assertEqual(self.manager.manifest(blocked)["status"], "blocked")

    def test_markerless_legacy_recovery_pause_and_its_blocked_successor_are_guarded(self):
        """Old durable shapes use the planner's compatibility vocabulary, not all prose."""
        legacy = self._paused_run("actually-legacy")
        self.manager.resume(legacy, actor="test")
        self.manager.transition(
            legacy, "paused", expected="running",
            reason="process restarted; no uncertain side effect observed",
        )
        pause = list(self.manager.iter_events(legacy))[-1]
        self.assertNotIn(work_loop.PAUSE_KIND_KEY, pause.get("details") or {})
        self.manager.transition(legacy, "blocked", expected="paused", reason="old blocker text")
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(legacy, agent._RESUME_IDLE_EVENT, attempt=attempt)
        self.assertEqual(agent._resume_stale_guard(self.manager, legacy), "warned")

        arbitrary = self._paused_run("legacy-negative")
        self.manager.resume(arbitrary, actor="test")
        self.manager.transition(arbitrary, "paused", expected="running", reason="some old prose")
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(arbitrary, agent._RESUME_IDLE_EVENT, attempt=attempt)
        self.assertEqual(agent._resume_stale_guard(self.manager, arbitrary), "")

    def test_long_live_work_cannot_be_closed_by_an_old_warning(self):
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(
                self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt + 1)
        self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "warned")
        # A paused run can persist real work as a result even though tool_started is forbidden.
        self.manager.store_result(self.run_id, "progress", name="tool", call_id="long-work")
        with mock.patch.object(
            agent, "_seconds_since",
            return_value=agent.RESUME_STALE_QUIET_SECONDS * 100,
        ):
            self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "")
        self.assertEqual(self.manager.manifest(self.run_id)["status"], "paused")

    def test_guard_owned_closure_is_reopened_by_explicit_resume(self):
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(
                self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt + 1)
        agent._resume_stale_guard(self.manager, self.run_id)
        with mock.patch.object(
            agent, "_seconds_since",
            return_value=agent.RESUME_STALE_QUIET_SECONDS + 1,
        ):
            agent._resume_stale_guard(self.manager, self.run_id)
        closed = self.manager.manifest(self.run_id)
        reopened = self.manager.authorize_resume(
            self.run_id, actor="owner", expected_revision=closed["revision"])
        self.assertEqual(reopened["status"], "paused")
        self.assertEqual(reopened["terminal"], {})
        self.assertEqual(reopened["resume_stale_guard"], {})
        self.assertEqual(agent._resume_idle_streak(self.manager, self.run_id)[0], 0)

    def test_closed_previous_blocked_reopens_to_the_executable_paused_gate(self):
        self.manager.transition(self.run_id, "blocked", expected="paused")
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
        agent._resume_stale_guard(self.manager, self.run_id)
        with mock.patch.object(agent, "_seconds_since",
                               return_value=agent.RESUME_STALE_QUIET_SECONDS + 1):
            agent._resume_stale_guard(self.manager, self.run_id)
        self.assertEqual(self.manager.manifest(self.run_id)["resume_stale_guard"][
            "previous_status"], "blocked")
        reopened = self.manager.authorize_resume(self.run_id, actor="owner")
        self.assertEqual(reopened["status"], "paused")
        events = list(self.manager.iter_events(self.run_id))
        self.assertTrue(run_resume._is_recovery_pause(events, "paused", {}))

    def test_structural_progress_requires_twelve_entirely_new_idle_observations(self):
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
        # Not in the old reset whitelist: nevertheless this is structural progress.
        self.manager.append_event(self.run_id, "model_output", response_id="new")
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS - 1):
            self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
        self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "")
        self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=12)
        self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "warned")

    def test_explicit_resume_and_new_recovery_cycle_discard_old_idle_evidence(self):
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
        self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "warned")
        self.manager.authorize_resume(self.run_id, actor="owner")
        self.manager.claim_resume(
            self.run_id,
            expected_revision=self.manager.manifest(self.run_id)["revision"],
            expected_event_seq=self.manager.manifest(self.run_id)["event_seq"], actor="test")
        self.manager.transition(
            self.run_id, "paused", expected="running", reason="fresh failure",
            details={work_loop.PAUSE_KIND_KEY: work_loop.PAUSE_PROCESS_RECOVERY})
        self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=1)
        mark, idle = agent._resume_guard_state(self.manager, self.run_id)
        self.assertTrue(mark)
        self.assertEqual(idle, 1)
        self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "")

    def test_running_state_is_scoped_to_each_recovery_anchor_before_blocked(self):
        self.manager.resume(self.run_id, actor="test")
        self.manager.transition(
            self.run_id, "paused", expected="running", reason="second cycle",
            details={work_loop.PAUSE_KIND_KEY: work_loop.PAUSE_PROCESS_RECOVERY})
        self.manager.transition(self.run_id, "blocked", expected="paused")
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
        self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "warned")

    def test_guard_excludes_owner_pause_her_wait_and_untyped_blocked(self):
        owner = self._paused_run("owner")
        self.manager.request_pause(owner, actor="owner")
        her = self._paused_run("her")
        self.manager.resume(her, actor="test")
        self.manager.transition(
            her, "paused", expected="running", details={
                work_loop.PAUSE_KIND_KEY: work_loop.PAUSE_HER_WAIT})
        arbitrary = self._paused_run("arbitrary")
        self.manager.resume(arbitrary, actor="test")
        self.manager.transition(arbitrary, "blocked", expected="running")
        for run_id in (owner, her, arbitrary):
            for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
                self.manager.append_event(run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
            self.assertEqual(agent._resume_stale_guard(self.manager, run_id), "")

    def test_malformed_warning_time_fails_closed(self):
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
        self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "warned")
        with mock.patch.object(agent, "_seconds_since", return_value=float("inf")):
            self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "warned")
        self.assertEqual(self.manager.manifest(self.run_id)["status"], "paused")

    def test_outstanding_legacy_tool_is_quarantined_not_buried_or_retried(self):
        self.manager.append_event(
            self.run_id, "tool_started", call_id="unknown", tool="legacy",
            side_effect=True, idempotent=False)
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
        agent._resume_stale_guard(self.manager, self.run_id)
        with mock.patch.object(agent, "_seconds_since",
                               return_value=agent.RESUME_STALE_QUIET_SECONDS + 1):
            self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "attention")
        quarantined = self.manager.manifest(self.run_id)
        self.assertEqual(quarantined["status"], "paused")
        self.assertEqual(quarantined["resume_stale_guard"]["phase"], "attention")
        self.assertIn("unknown", quarantined["resume_stale_guard"]["blockers"][
            "outstanding_call_ids"])
        self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "attention")
        requested = self.manager.authorize_resume(self.run_id, actor="owner")
        self.assertEqual(requested["status"], "in_doubt")
        self.assertEqual(requested["resume_stale_guard"]["phase"], "attention")
        self.assertIn("unknown", self.manager.outstanding_tools(self.run_id))

    def test_blocked_attention_requires_reconcile_before_plan_and_claim(self):
        """Authorization records intent, but uncertainty needs an audited receipt first."""
        self.manager.append_event(
            self.run_id, "tool_started", call_id="blocked-unknown", tool="legacy",
            side_effect=True, idempotent=False)
        # A real checkpoint makes this resumable once (and only once) uncertainty is gone.
        checkpoint = {
            "schema": run_resume.CHECKPOINT_SCHEMA,
            "iteration": 1,
            "system": "exact system",
            "messages": [{"role": "user", "content": "continue"}],
            "tools": [{"name": "fs_read"}],
            "outbound": [],
        }
        self.manager.store_result(
            self.run_id, json.dumps(checkpoint), call_id="checkpoint-1",
            name="tool-loop-checkpoint", media_type="application/json",
            event_kind="run_checkpoint")
        self.manager.transition(self.run_id, "blocked", expected="paused")
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
        agent._resume_stale_guard(self.manager, self.run_id)
        with mock.patch.object(agent, "_seconds_since",
                               return_value=agent.RESUME_STALE_QUIET_SECONDS + 1):
            self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "attention")

        requested = self.manager.authorize_resume(self.run_id, actor="owner")
        self.assertEqual(requested["status"], "in_doubt")
        self.assertEqual(requested["resume_stale_guard"]["phase"], "attention")
        blocked_plan = run_resume.plan_resume(self.manager, self.run_id)
        self.assertEqual(blocked_plan.kind, "in_doubt")
        self.assertFalse(blocked_plan.auto_resume)
        with self.assertRaisesRegex(run_manager.RunConflict, "expected paused"):
            self.manager.claim_resume(
                self.run_id, expected_revision=blocked_plan.revision,
                expected_event_seq=blocked_plan.event_seq, actor="runtime:test")

        reconciled = self.manager.resolve_in_doubt(
            self.run_id, "blocked-unknown", "not_applied",
            evidence={"source": "owner audit", "effect_seen": False},
            reason="owner verified effect was not applied", actor="owner")
        self.assertEqual(reconciled["status"], "paused")
        authorized = self.manager.authorize_resume(self.run_id, actor="owner")
        self.assertEqual(authorized["resume_stale_guard"], {})
        plan = run_resume.plan_resume(self.manager, self.run_id)
        self.assertEqual(plan.kind, "continue_checkpoint")
        self.assertTrue(plan.auto_resume)
        claimed = self.manager.claim_resume(
            self.run_id, expected_revision=plan.revision,
            expected_event_seq=plan.event_seq, actor="runtime:test")
        self.assertEqual(claimed["status"], "running")

    def test_ordinary_blocked_cannot_be_authorized_or_claimed(self):
        ordinary = self._paused_run("ordinary-blocked")
        self.manager.transition(ordinary, "blocked", expected="paused")
        with self.assertRaisesRegex(run_manager.InvalidTransition, "requires paused"):
            self.manager.authorize_resume(ordinary, actor="owner")
        plan = run_resume.plan_resume(self.manager, ordinary)
        self.assertEqual(plan.kind, "blocked")
        self.assertFalse(plan.auto_resume)
        with self.assertRaisesRegex(run_manager.RunConflict, "expected paused"):
            self.manager.claim_resume(
                ordinary, expected_revision=plan.revision,
                expected_event_seq=plan.event_seq, actor="runtime:test")

    def test_crash_after_close_event_reloads_as_reversible_guard_closure(self):
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
        agent._resume_stale_guard(self.manager, self.run_id)
        warned = self.manager.manifest(self.run_id)
        mark = list(warned["resume_stale_guard"]["progress"])

        with mock.patch.object(run_manager, "_atomic_json",
                               side_effect=OSError("injected publication crash")):
            with self.assertRaisesRegex(OSError, "injected publication crash"):
                self.manager.close_resume_stale(
                    self.run_id, expected_revision=warned["revision"], progress=mark)

        # A fresh process replays the dedicated WAL record. It cannot observe a
        # terminal status with the old warned ownership marker.
        reloaded_manager = run_manager.RunManager(Path(self._temp.name))
        reloaded = reloaded_manager.manifest(self.run_id)
        self.assertEqual(reloaded["status"], "cancelled")
        self.assertEqual(reloaded["resume_stale_guard"]["phase"], "closed")
        reopened = reloaded_manager.authorize_resume(self.run_id, actor="owner")
        self.assertEqual(reopened["status"], "paused")
        self.assertEqual(reopened["resume_stale_guard"], {})

    def test_crash_during_guard_reopen_replays_one_consistent_transaction(self):
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
        agent._resume_stale_guard(self.manager, self.run_id)
        warned = self.manager.manifest(self.run_id)
        with mock.patch.object(agent, "_seconds_since",
                               return_value=agent.RESUME_STALE_QUIET_SECONDS + 1):
            agent._resume_stale_guard(self.manager, self.run_id)
        closed = self.manager.manifest(self.run_id)
        self.assertEqual(closed["status"], "cancelled")

        # Cache the raw terminal before the reopen transaction, exactly as the
        # production scanners do.  The same manager must invalidate that cache
        # when its failed authorization is replayed/retried as nonterminal.
        reloaded_manager = run_manager.RunManager(Path(self._temp.name))
        self.assertNotIn(self.run_id, reloaded_manager.live_run_ids())
        self.assertIn(self.run_id, reloaded_manager._settled)
        with mock.patch.object(run_manager, "_atomic_json",
                               side_effect=OSError("injected reopen publication crash")):
            with self.assertRaisesRegex(OSError, "injected reopen publication crash"):
                reloaded_manager.authorize_resume(
                    self.run_id, actor="owner", expected_revision=closed["revision"])

        # The dedicated WAL event is the complete reducer input.  Retrying on
        # this same object replays it and must not leave the paused run hidden by
        # the terminal fast-path cache populated above.
        recovered = reloaded_manager.authorize_resume(self.run_id, actor="owner")
        self.assertEqual(recovered["status"], "paused")
        self.assertIn(self.run_id, reloaded_manager.live_run_ids())
        self.assertEqual(recovered["terminal"], {})
        self.assertEqual(recovered["terminal_at"], "")
        self.assertEqual(recovered["recap"], {"status": "not_due"})
        self.assertEqual(recovered["control"], {})
        self.assertEqual(recovered["resume_stale_guard"], {})
        reopen_rows = [row for row in reloaded_manager.iter_events(self.run_id)
                       if row.get("kind") == "resume_stale_reopened"]
        self.assertEqual(len(reopen_rows), 1)

        # Retrying authorization after recovery is safe and cannot resurrect the
        # cancelled terminal metadata or append a second reopen transaction.
        retried = reloaded_manager.authorize_resume(self.run_id, actor="owner")
        self.assertEqual(retried["status"], "paused")
        self.assertEqual(retried["terminal"], {})
        self.assertEqual(retried["terminal_at"], "")
        fresh = run_manager.RunManager(Path(self._temp.name)).manifest(self.run_id)
        self.assertEqual(fresh["status"], "paused")
        self.assertEqual(fresh["terminal"], {})
        self.assertEqual(fresh["terminal_at"], "")
        self.assertEqual(fresh["recap"], {"status": "not_due"})
        self.assertEqual(fresh["control"], {})
        self.assertEqual(fresh["resume_stale_guard"], {})
        reopen_rows = [row for row in reloaded_manager.iter_events(self.run_id)
                       if row.get("kind") == "resume_stale_reopened"]
        self.assertEqual(len(reopen_rows), 1)

    def test_crash_reopen_replay_invalidates_a_different_scanners_terminal_cache(self):
        """A scanner may cache cancelled before another manager leaves reopen in WAL."""
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
        agent._resume_stale_guard(self.manager, self.run_id)
        warned = self.manager.manifest(self.run_id)
        with mock.patch.object(agent, "_seconds_since",
                               return_value=agent.RESUME_STALE_QUIET_SECONDS + 1):
            agent._resume_stale_guard(self.manager, self.run_id)
        closed = self.manager.manifest(self.run_id)

        scanner = run_manager.RunManager(Path(self._temp.name))
        self.assertNotIn(self.run_id, scanner.live_run_ids())
        self.assertIn(self.run_id, scanner._settled)

        writer = run_manager.RunManager(Path(self._temp.name))
        with mock.patch.object(run_manager, "_atomic_json",
                               side_effect=OSError("injected reopen publication crash")):
            with self.assertRaisesRegex(OSError, "injected reopen publication crash"):
                writer.authorize_resume(
                    self.run_id, actor="owner", expected_revision=closed["revision"])

        # A scanner created only after the crash has no previous signature to
        # compare. It must still detect that terminal manifest.event_seq trails
        # the WAL rather than cache the stale terminal together with the new WAL
        # signature.
        fresh_scanner = run_manager.RunManager(Path(self._temp.name))
        self.assertIn(self.run_id, fresh_scanner.live_run_ids())
        self.assertNotIn(self.run_id, fresh_scanner._settled)

        # The first operation on the already-warm scanner after the crash must
        # discover and replay the WAL-only reopen; callers do not know they need
        # an explicit manifest().
        self.assertIn(self.run_id, scanner.live_run_ids())
        self.assertNotIn(self.run_id, scanner._settled)
        replayed = scanner.manifest(self.run_id)
        self.assertEqual(replayed["status"], "paused")

    def test_terminal_scan_racing_wal_only_reopen_never_caches_stale_verdict(self):
        """A terminal read cannot be paired with a newer post-reopen signature."""
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
        agent._resume_stale_guard(self.manager, self.run_id)
        with mock.patch.object(agent, "_seconds_since",
                               return_value=agent.RESUME_STALE_QUIET_SECONDS + 1):
            agent._resume_stale_guard(self.manager, self.run_id)
        closed = self.manager.manifest(self.run_id)

        scanner = run_manager.RunManager(Path(self._temp.name))
        writer = run_manager.RunManager(Path(self._temp.name))
        real_read = run_manager._read_json
        raced = False

        def read_then_crash_reopen(path):
            nonlocal raced
            raw = real_read(path)
            if Path(path).name == "manifest.json" and not raced:
                raced = True
                with mock.patch.object(
                        run_manager, "_atomic_json",
                        side_effect=OSError("injected reopen publication crash")):
                    with self.assertRaisesRegex(
                            OSError, "injected reopen publication crash"):
                        writer.authorize_resume(
                            self.run_id, actor="owner",
                            expected_revision=closed["revision"])
            return raw

        # This is the exact TOCTOU: scanner has already observed cancelled, then
        # writer appends resume_stale_reopened but crashes before manifest
        # publication. Caching a signature after that append would make the old
        # terminal verdict look current forever.
        with mock.patch.object(run_manager, "_read_json",
                               side_effect=read_then_crash_reopen):
            self.assertIn(self.run_id, scanner.live_run_ids())
        self.assertTrue(raced)
        self.assertNotIn(self.run_id, scanner._settled)
        self.assertEqual(scanner.manifest(self.run_id)["status"], "paused")

    def test_terminal_scan_fails_open_when_wal_read_raises_run_error(self):
        terminal = self._paused_run("terminal-wal-read-error")
        self.manager.resume(terminal, actor="test")
        self.manager.transition(terminal, "done", expected="running")

        scanner = run_manager.RunManager(Path(self._temp.name))
        with mock.patch.object(
                scanner, "_last_event_seq",
                side_effect=run_manager.RunError("cannot read event stream")):
            self.assertIn(terminal, scanner.live_run_ids())
        self.assertNotIn(terminal, scanner._settled)

    def test_terminal_scan_fails_open_on_complete_corrupt_interior_wal_row(self):
        terminal = self._paused_run("terminal-corrupt-wal")
        self.manager.resume(terminal, actor="test")
        self.manager.transition(terminal, "done", expected="running")
        run_dir = self.manager._find(terminal)
        events_path = run_dir / "events.jsonl"
        rows = events_path.read_bytes().splitlines(keepends=True)
        self.assertGreaterEqual(len(rows), 2)
        events_path.write_bytes(b"".join(rows[:-1] + [b"{not-json}\n", rows[-1]]))

        # The newest valid row still agrees with manifest.event_seq. The scanner
        # must nevertheless validate the complete WAL before caching terminal.
        scanner = run_manager.RunManager(Path(self._temp.name))
        self.assertIn(terminal, scanner.live_run_ids())
        self.assertNotIn(terminal, scanner._settled)

    def test_terminal_scan_fails_open_on_complete_semantically_invalid_wal_rows(self):
        corruptions = {
            "blank": b"\n",
            "array": b"[]\n",
            "missing-seq": b'{"kind": "observation"}\n',
            "string-seq": b'{"seq": "2", "kind": "observation"}\n',
            "bool-seq": b'{"seq": true, "kind": "observation"}\n',
            "duplicate-seq": b'{"seq": 1, "kind": "observation"}\n',
            "gapped-seq": b'{"seq": 99, "kind": "observation"}\n',
        }
        for suffix, encoded in corruptions.items():
            with self.subTest(corruption=suffix):
                terminal = self._paused_run(f"terminal-semantic-{suffix}")
                self.manager.resume(terminal, actor="test")
                self.manager.transition(terminal, "done", expected="running")
                events_path = self.manager._find(terminal) / "events.jsonl"
                rows = events_path.read_bytes().splitlines(keepends=True)
                self.assertGreaterEqual(len(rows), 2)
                events_path.write_bytes(b"".join(rows[:-1] + [encoded, rows[-1]]))

                # A complete row which replay cannot interpret must prevent the
                # unlocked optimization from hiding the run as terminal.
                scanner = run_manager.RunManager(Path(self._temp.name))
                self.assertIn(terminal, scanner.live_run_ids())
                self.assertNotIn(terminal, scanner._settled)

    def test_terminal_scan_fails_open_on_complete_leading_blank_wal_row(self):
        terminal = self._paused_run("terminal-leading-blank")
        self.manager.resume(terminal, actor="test")
        self.manager.transition(terminal, "done", expected="running")
        events_path = self.manager._find(terminal) / "events.jsonl"
        events_path.write_bytes(b"\n" + events_path.read_bytes())

        scanner = run_manager.RunManager(Path(self._temp.name))
        self.assertIn(terminal, scanner.live_run_ids())
        self.assertNotIn(terminal, scanner._settled)

    def test_terminal_scan_tolerates_only_an_incomplete_trailing_fragment(self):
        terminal = self._paused_run("terminal-incomplete-tail")
        self.manager.resume(terminal, actor="test")
        self.manager.transition(terminal, "done", expected="running")
        events_path = self.manager._find(terminal) / "events.jsonl"
        with events_path.open("ab") as stream:
            stream.write(b'{"seq": false')

        scanner = run_manager.RunManager(Path(self._temp.name))
        self.assertNotIn(terminal, scanner.live_run_ids())
        self.assertIn(terminal, scanner._settled)

    def test_manifest_read_does_not_evict_a_genuinely_terminal_run(self):
        terminal = self._paused_run("terminal-cache")
        self.manager.resume(terminal, actor="test")
        self.manager.transition(terminal, "done", expected="running")
        self.assertNotIn(terminal, self.manager.live_run_ids())
        self.assertIn(terminal, self.manager._settled)
        cached_signature = self.manager._settled_signatures[terminal]

        reread = self.manager.manifest(terminal)
        self.assertEqual(reread["status"], "done")
        self.assertIn(terminal, self.manager._settled)
        self.assertEqual(self.manager._settled_signatures[terminal], cached_signature)
        self.assertNotIn(terminal, self.manager.live_run_ids())

    def test_successful_reopen_invalidates_a_different_scanners_terminal_cache(self):
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
        agent._resume_stale_guard(self.manager, self.run_id)
        with mock.patch.object(agent, "_seconds_since",
                               return_value=agent.RESUME_STALE_QUIET_SECONDS + 1):
            agent._resume_stale_guard(self.manager, self.run_id)

        scanner = run_manager.RunManager(Path(self._temp.name))
        self.assertNotIn(self.run_id, scanner.live_run_ids())
        self.assertIn(self.run_id, scanner._settled)

        writer = run_manager.RunManager(Path(self._temp.name))
        reopened = writer.authorize_resume(self.run_id, actor="owner")
        self.assertEqual(reopened["status"], "paused")

        self.assertIn(self.run_id, scanner.live_run_ids())
        self.assertNotIn(self.run_id, scanner._settled)

    def test_close_has_one_atomic_manifest_publication_and_one_revision(self):
        for attempt in range(agent.RESUME_STALE_WARN_ATTEMPTS):
            self.manager.append_event(self.run_id, agent._RESUME_IDLE_EVENT, attempt=attempt)
        agent._resume_stale_guard(self.manager, self.run_id)
        warned = self.manager.manifest(self.run_id)
        real = run_manager._atomic_json
        publications = []
        with mock.patch.object(run_manager, "_atomic_json",
                               side_effect=lambda path, data: (publications.append(dict(data)),
                                                               real(path, data))[1]), \
                mock.patch.object(agent, "_seconds_since",
                                  return_value=agent.RESUME_STALE_QUIET_SECONDS + 1):
            self.assertEqual(agent._resume_stale_guard(self.manager, self.run_id), "closed")
        self.assertEqual(len(publications), 1)
        self.assertEqual(publications[0]["revision"], warned["revision"] + 1)
        self.assertEqual(publications[0]["resume_stale_guard"]["phase"], "closed")
        self.assertEqual(publications[0]["status"], "cancelled")

    def test_the_idle_mark_counts_the_attempt_it_actually_is(self):
        """Число в улике — измерение, а не константа: прежняя версия всегда писала «12»."""
        for _ in range(agent.RESUME_FREE_ATTEMPTS):
            self._tick()
        self.assertEqual([int(row.get("attempt") or 0) for row in self._idle_marks()],
                         list(range(1, agent.RESUME_FREE_ATTEMPTS + 1)))

    def test_a_progressing_attempt_leaves_no_idle_mark(self):
        self._tick(progress=True)
        self.assertEqual(self._idle_marks(), [], "сдвинувшийся ран не холостой")

    def test_blocked_listing_does_not_claim_an_automatic_retry(self):
        """Historical idle evidence must not advertise a retry for blocked work."""
        for _ in range(agent.RESUME_FREE_ATTEMPTS + 4):
            self._tick(elapsed=agent.RESUME_BACKOFF_CAP_SECONDS + 1.0)
        manifest = self.manager.manifest(self.run_id)
        self.manager.transition(
            self.run_id, "blocked", expected="paused",
            reason="task_control(blocked): needs an external patch",
            details={"task_control": {"action": "blocked",
                                      "blocker": "needs an external patch"}},
        )
        with mock.patch.object(agent, "_seconds_since", return_value=1.0):
            listing = agent.tool_list_active_runs()
        self.assertIn("[blocked]", listing)
        self.assertNotIn("следующая попытка", listing)

    def test_she_can_see_the_delay_in_her_own_listing(self):
        """Отсроченный ран обязан отличаться от того, который дожимают каждые 45 секунд."""
        for _ in range(agent.RESUME_FREE_ATTEMPTS + 4):
            self._tick(elapsed=agent.RESUME_BACKOFF_CAP_SECONDS + 1.0)
        with mock.patch.object(agent, "_seconds_since", return_value=1.0):
            listing = agent.tool_list_active_runs()
        self.assertIn("следующая попытка", listing)


if __name__ == "__main__":
    unittest.main()
