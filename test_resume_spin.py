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
                                reason=STALL)
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

    def test_the_idle_mark_counts_the_attempt_it_actually_is(self):
        """Число в улике — измерение, а не константа: прежняя версия всегда писала «12»."""
        for _ in range(agent.RESUME_FREE_ATTEMPTS):
            self._tick()
        self.assertEqual([int(row.get("attempt") or 0) for row in self._idle_marks()],
                         list(range(1, agent.RESUME_FREE_ATTEMPTS + 1)))

    def test_a_progressing_attempt_leaves_no_idle_mark(self):
        self._tick(progress=True)
        self.assertEqual(self._idle_marks(), [], "сдвинувшийся ран не холостой")

    def test_she_can_see_the_delay_in_her_own_listing(self):
        """Отсроченный ран обязан отличаться от того, который дожимают каждые 45 секунд."""
        for _ in range(agent.RESUME_FREE_ATTEMPTS + 4):
            self._tick(elapsed=agent.RESUME_BACKOFF_CAP_SECONDS + 1.0)
        with mock.patch.object(agent, "_seconds_since", return_value=1.0):
            listing = agent.tool_list_active_runs()
        self.assertIn("следующая попытка", listing)


if __name__ == "__main__":
    unittest.main()
