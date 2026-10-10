# -*- coding: utf-8 -*-
"""Durable-медиа хода (10.10): 429 больше не теряет файл и не врёт «отправлено».

ЗАЧЕМ ЭТОТ СТЕНД. Живой сбой #113214 (10.10 15:52): sendPhoto → 429 Too Many
Requests, retry_after=19. `runner._deliver_outbound` глотал ошибку исключением
«медиа хода не доставилось» и молча: файл потерян, ошибка не дошла ни агенту,
ни владельцу, ход закрыт «отправлено». `botapi.deliver_file`, в отличие от
`deliver_text`, не имел 429-ветки вовсе.

ЧТО ЗДЕСЬ ЗАКРЕПЛЕНО — порядок, который нельзя переставлять:
намерение durable (media_queue_ids) → enqueue предмета в спул → попытка →
на успех: расписка ok=True с message_id ДО гашения спула; на ошибку: расписка
ok=False при предмете, ОСТАЮЩЕМСЯ в спуле. Успех заявляется только по
принятому message_id. Повтор — без дублей: policy-опрос (ack/drop) гасит слот
без второй отправки.
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "localharness"))

import botapi  # noqa: E402
import runner  # noqa: E402
from telegram_retry import Cooldown


class _Refusal(str):
    pass


@contextmanager
def _tmpfile():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "shot.png"
        path.write_bytes(b"png")
        yield path


def _bot():
    """BotTransport с замоканным client.upload и agent.DirectSendRefusal."""
    transport = object.__new__(botapi.BotTransport)
    transport.client = types.SimpleNamespace(upload=None)
    transport.agent = types.SimpleNamespace(DirectSendRefusal=_Refusal)
    transport.rooms = types.SimpleNamespace(record=lambda *a, **k: None)
    transport.contacts = types.SimpleNamespace(label=lambda peer: "Владелец")
    transport.before_send = None
    clock = types.SimpleNamespace(now=0.0)
    def wait(seconds):
        botapi.time.sleep(seconds)
        clock.now += seconds
    transport._cooldown = Cooldown(wait=wait, clock=lambda: clock.now)
    return transport


# --------------------------------------------------------------------- botapi

class File429RespectsCooldown(unittest.TestCase):
    """Repeated explicit refusal is retried until acceptance, without early sends."""

    def test_retry_after_429_then_success_returns_receipt_with_message_id(self):
        bot = _bot()
        calls = []

        def upload(method, field, path, **params):
            calls.append((method, field))
            if len(calls) == 1:
                raise botapi.BotApiError("sendPhoto", 429,
                                         "Too Many Requests", retry_after=0.1)
            return {"message_id": 113216}

        bot.client.upload = upload
        with _tmpfile() as path:
            with mock.patch.object(botapi.time, "sleep") as slept:
                receipt = bot.deliver_file(path, chat_id="123", media_kind="photo")
        self.assertEqual(len(calls), 2, "ровно один повтор, не больше")
        slept.assert_called_once_with(0.6)  # full retry_after + 0.5
        self.assertIn("113216", receipt)
        self.assertNotIsInstance(receipt, _Refusal)

    def test_second_429_is_retried_automatically(self):
        bot = _bot()
        calls = []

        def upload(method, field, path, **params):
            calls.append(1)
            if len(calls) < 3:
                raise botapi.BotApiError("sendPhoto", 429,
                                         "Too Many Requests", retry_after=0.1)
            return {'message_id': 123}

        bot.client.upload = upload
        with _tmpfile() as path:
            with mock.patch.object(botapi.time, "sleep"):
                receipt = bot.deliver_file(path, chat_id="123", media_kind="photo")
        self.assertEqual(len(calls), 3)
        self.assertIn('123', receipt)

    def test_permanent_refusal_is_returned_not_raised(self):
        bot = _bot()

        def upload(method, field, path, **params):
            raise botapi.BotApiError("sendPhoto", 403, "bot was blocked by user")

        bot.client.upload = upload
        with _tmpfile() as path:
            receipt = bot.deliver_file(path, chat_id="123", media_kind="photo")
        self.assertIsInstance(receipt, _Refusal)

    def test_big_retry_after_is_waited_in_full_and_interruptible(self):
        bot = _bot()
        attempts = []

        def upload(method, field, path, **params):
            attempts.append(1)
            if len(attempts) == 1:
                raise botapi.BotApiError("sendPhoto", 429,
                                         "Too Many Requests", retry_after=120)
            return {'message_id': 71}

        bot.client.upload = upload
        with _tmpfile() as path:
            with mock.patch.object(botapi.time, "sleep") as slept:
                receipt = bot.deliver_file(path, chat_id="123", media_kind="photo")
        self.assertEqual(len(attempts), 2)
        self.assertIn('71', receipt)
        self.assertAlmostEqual(sum(call.args[0] for call in slept.call_args_list), 120.5)
        self.assertTrue(all(call.args[0] <= 1 for call in slept.call_args_list))


class ReceiptMessageId(unittest.TestCase):
    """Формат квитанции — контракт: message_id извлекается из обоих транспортов."""

    def test_both_transport_receipt_formats(self):
        self.assertEqual(runner._media_receipt_message_id(
            "Отправлен файл → Владелец (id 113216)"), "113216")
        self.assertEqual(runner._media_receipt_message_id(
            "Отправлено → Владелец (окно Frame, id 7)"), "7")
        self.assertEqual(runner._media_receipt_message_id("без id"), "")


# ------------------------------------------------------------------- runner

class _Item:
    def __init__(self, queue_id: str, target: str = "123"):
        self.queue_id = queue_id
        self.path = "/tmp/shot.png"
        self.caption = "снимок"
        self.kind = "photo"
        self.target_chat_id = target
        self.voice_note = False
        self.scope = "owner"


class _Envelope:
    def __init__(self, run_id: str, outbound):
        self.run_id, self.text = run_id, ""
        self.outbound = list(outbound)
        self.deferred = self.failed = False


class _Spool:
    """Перезагрузка runner (restart-симуляция) делает _Spool() заново — состояние
    переживает её общим словарём, как настоящий media-ledger переживает рестарт."""

    state: dict = {}

    def __init__(self):
        self.__class__.state = self.__class__.state or {"items": {}}
        self._items = self.__class__.state["items"]
        self.discarded = []
        self.failed = []

    def enqueue(self, item):
        if item.queue_id in self._items:
            raise ValueError("duplicate outbound queue_id")
        self._items[item.queue_id] = item
        return item

    def pending(self):
        return tuple(self._items.values())

    def discard(self, queue_id, *, receipt=None):
        self.discarded.append((str(queue_id), receipt))
        self._items.pop(str(queue_id), None)
        return True

    def fail(self, queue_id, *, reason=""):
        self.failed.append((str(queue_id), str(reason)))
        self._items.pop(str(queue_id), None)
        return True


def _reset_spool_state():
    _Spool.state = {"items": {}}


class _Agent:
    """Поверхность ядра, которой касается _deliver_outbound."""

    DirectSendRefusal = _Refusal

    def __init__(self, spool, policy="retry"):
        self.spool = spool
        self.policy = policy
        self.calls = []
        self.started = {}

    def run_delivery_started(self, run_id, **kw):
        self.started[run_id] = dict(kw)
        self.calls.append(("started", run_id, dict(kw)))

    def _media_spool(self):
        return self.spool

    def run_delivery_media_retry_policy(self, run_id, queue_id):
        return self.policy

    def run_delivery_media_result(self, run_id, queue_id, *, ok, message_id=None,
                                  error="", permanent=False, chat_id="",
                                  path="", caption=""):
        self.calls.append(("result", str(queue_id), bool(ok),
                           bool(permanent), str(message_id or "")))

    def run_delivery_finalize_recovered(self, run_id):
        self.calls.append(("finalize", run_id))
        return True


class _AgentPatchMixin:
    """_deliver_outbound читает глобаль runner._agent — подменяем и возвращаем."""

    def setUp(self):
        self._saved_agent = runner._agent
        _reset_spool_state()
        return super().setUp()

    def tearDown(self):
        runner._agent = self._saved_agent
        return super().tearDown()

    def _bind(self, agent):
        runner._agent = agent
        return agent


class TheIntentIsDurableBeforeTheFirstSend(_AgentPatchMixin, unittest.TestCase):

    def test_success_closes_the_slot_with_message_id(self):
        spool = _Spool()
        agent = _Agent(spool)
        self._bind(agent)
        env = _Envelope("run-1", [_Item("q-1")])
        with mock.patch.object(runner, "deliver_one_media",
                               return_value="Отправлен файл → Владелец (id 77)"):
            got = runner._deliver_outbound(env, "123")
        self.assertEqual(got, 1)
        started = agent.started.get("run-1")
        self.assertIsNotNone(started, "намерение обязано лечь ДО отправки")
        self.assertEqual(started["media_queue_ids"], ["q-1"])
        self.assertEqual(started["text_chars"], 0)
        kinds = [c[0] for c in agent.calls]
        self.assertEqual(kinds, ["started", "result", "finalize"],
                         "успех: started → result(ok) → finalize")
        result = agent.calls[1]
        self.assertEqual(result[2], True)
        self.assertEqual(result[4], "77", "успех — только по принятому message_id")
        self.assertEqual(spool.discarded, [("q-1", {"message_id": "77"})])

    def test_result_precedes_discard(self):
        """Расписка ДО гашения: краш между ними вернёт предмет, расписка не даст дубль."""
        spool = _Spool()
        agent = _Agent(spool)
        self._bind(agent)
        env = _Envelope("run-1", [_Item("q-1")])
        order = []
        real_result = agent.run_delivery_media_result

        def traced_result(run_id, queue_id, **kw):
            order.append("result")
            return real_result(run_id, queue_id, **kw)

        agent.run_delivery_media_result = traced_result
        real_discard = spool.discard

        def traced_discard(queue_id, *, receipt=None):
            order.append("discard")
            return real_discard(queue_id, receipt=receipt)

        spool.discard = traced_discard
        with mock.patch.object(runner, "deliver_one_media",
                               return_value="Отправлен файл → Владелец (id 5)"):
            runner._deliver_outbound(env, "123")
        self.assertEqual(order, ["result", "discard"])

    def test_error_keeps_the_item_in_the_spool(self):
        """Канал не принял: долг живёт, успеха нет, finalize нет."""
        spool = _Spool()
        agent = _Agent(spool)
        self._bind(agent)
        env = _Envelope("run-1", [_Item("q-1")])

        def boom(item, chat_id):
            raise botapi.BotApiError("sendPhoto", 429, "Too Many Requests",
                                     retry_after=19)

        with mock.patch.object(runner, "deliver_one_media", boom):
            got = runner._deliver_outbound(env, "123")
        self.assertEqual(got,0)
        self.assertIn(("result", "q-1", False, False, ""), agent.calls)
        self.assertEqual(spool.discarded, [], "предмет погашен по ошибке канала")
        self.assertNotIn(("finalize", "run-1"), agent.calls,
                         "прогон не закрывается, пока долг жив")
        self.assertTrue(any(p.queue_id == "q-1" for p in spool.pending()),
                        "файл переживает неудачу в спуле")

    def test_permanent_refusal_closes_the_slot_without_retry(self):
        spool = _Spool()
        agent = _Agent(spool)
        self._bind(agent)
        env = _Envelope("run-1", [_Item("q-1")])
        refusal = _Refusal("файл не отправлен: Telegram отказал — blocked")
        with mock.patch.object(runner, "deliver_one_media", return_value=refusal):
            got = runner._deliver_outbound(env, "123")
        self.assertEqual(got, 0)
        self.assertIn(("result", "q-1", False, True, ""), agent.calls)
        self.assertEqual(spool.discarded, [])
        self.assertIn(("finalize", "run-1"), agent.calls,
                      "отказ навсегда — наблюдаемое завершение слота")


class RetryAfterRestartDoesNotDuplicate(_AgentPatchMixin, unittest.TestCase):
    """Restarts-симуляция: тот же долг, новый заход — отправки нет, слот гасится."""

    def test_ack_policy_discards_without_second_send(self):
        spool = _Spool()
        agent = _Agent(spool, policy="ack")
        self._bind(agent)
        spool.enqueue(_Item("q-1"))
        env = _Envelope("run-1", [_Item("q-1")])
        sent = []
        with mock.patch.object(runner, "deliver_one_media",
                               side_effect=lambda *a: sent.append(1) or "x"):
            got = runner._deliver_outbound(env, "123")
        self.assertEqual(sent, [], "файл ушёл второй раз")
        self.assertEqual(spool.discarded, [("q-1", {"policy": "ack"})])
        self.assertEqual(got, 1)

    def test_failed_intent_returns_zero_and_sends_nothing(self):
        spool = _Spool()
        agent = _Agent(spool)
        self._bind(agent)
        env = _Envelope("run-1", [_Item("q-1")])
        agent.run_delivery_started = mock.Mock(
            side_effect=RuntimeError("run is paused"))
        sent = []
        with mock.patch.object(runner, "deliver_one_media",
                               side_effect=lambda *a: sent.append(1) or "x"):
            got = runner._deliver_outbound(env, "123")
        self.assertEqual(got, 0)
        self.assertEqual(sent, [], "без намерения отправка стала бы потерей")

    def test_deferred_envelope_skips_the_intent_and_sends(self):
        """Чекпойнт-ход: intent не пишется (прогон в паузе — start_tool откажет),
        но отправка реальна, значит расписки результата обязаны лечь."""
        spool = _Spool()
        agent = _Agent(spool)
        self._bind(agent)
        env = _Envelope("run-1", [_Item("q-1")])
        env.deferred = True
        with mock.patch.object(runner, "deliver_one_media",
                               return_value="Отправлен файл → Владелец (id 9)"):
            got = runner._deliver_outbound(env, "123")
        self.assertEqual(got, 1)
        kinds = [c[0] for c in agent.calls]
        self.assertNotIn("started", kinds, "start_tool в paused/in_doubt отказал бы")
        self.assertEqual(kinds, ["result", "finalize"])
        self.assertEqual(spool.discarded, [("q-1", {"message_id": "9"})])


class AgentStatusAfterFailure(_AgentPatchMixin, unittest.TestCase):
    """Агентский статус: attempt_failed в расписке, не «отправлено»."""

    def test_failed_attempt_is_recorded_as_not_ok(self):
        spool = _Spool()
        agent = _Agent(spool)
        self._bind(agent)
        env = _Envelope("run-1", [_Item("q-1")])

        def boom(item, chat_id):
            raise OSError("сеть моргнула")

        with mock.patch.object(runner, "deliver_one_media", boom):
            runner._deliver_outbound(env, "123")
        kinds = [c[0] for c in agent.calls]
        self.assertIn("result", kinds)
        result = next(c for c in agent.calls if c[0] == "result")
        self.assertFalse(result[2], "неуспех не записывается как успех")
        self.assertFalse(result[3], "сетевая ошибка — не permanent")


if __name__ == "__main__":
    unittest.main()
