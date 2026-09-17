# -*- coding: utf-8 -*-
"""Очередь медиа разбирается и в ИЗДАНИИ, а не только на границе mtproto (17.09).

ЗАЧЕМ ЭТОТ СТЕНД. В ядре спуленный файл забирает исходящая граница mtproto. В издании её
нет: харнесс поднимает свой транспорт, и до 17.09 спул не разбирал НИКТО. Пока ход шёл
живьём, файл уезжал конвертом (`runner._deliver_outbound`); ход, поднятый заново
(`resume_due` каждые 45 с), клал файл в спул — и он оставался там навсегда.

Это делало правку ядра «файл переживает пустой текст хода» не лучше, а ХУЖЕ: раньше файл
выбрасывался молча и ход закрывался, а с ней долг доставки не гасился бы и прогон не
терминализовался — поднимался бы каждые 45 секунд до конца времён.

ЧТО ИМЕННО ЗАКРЕПЛЕНО ЗДЕСЬ — порядок, в котором нельзя переставить ни одного шага:
доставить → durable-расписка → погасить предмет в спуле → досведение прогона. Расписка ДО
гашения: падение между ними возвращает предмет в очередь, а расписка не даёт отправить
второй раз. Обратный порядок терял бы доказательство доставки.
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "localharness"))

from continuity import Continuity  # noqa: E402


def _item(queue_id: str, path: str = "/tmp/file.pdf", caption: str = ""):
    return types.SimpleNamespace(queue_id=queue_id, path=path, caption=caption,
                                 kind="document", target_chat_id="", voice_note=False)


class _Spool:
    def __init__(self, items):
        self.items = list(items)
        self.discarded: list[str] = []

    def pending(self):
        return tuple(self.items)

    def discard(self, queue_id, *, receipt=None):
        self.discarded.append(str(queue_id))
        self.items = [i for i in self.items if i.queue_id != queue_id]
        return True


class _Agent:
    """Ровно та поверхность ядра, которую трогает разбираемая функция."""

    def __init__(self, plans, spool, policy="retry"):
        self.plans = plans
        self.spool = spool
        self.policy = policy
        self.calls: list[tuple] = []

    # — то, что читает доставщик —
    def run_pending_media_deliveries(self, *, limit=20):
        return self.plans

    def run_delivery_media_retry_policy(self, run_id, queue_id):
        return self.policy

    def _media_spool(self):
        return self.spool

    def _runs(self):
        ctx = types.SimpleNamespace(delivery_chat_id="window-abcdef12")
        return types.SimpleNamespace(
            context=lambda run_id: ctx,
            manifest=lambda run_id: {"status": "running", "control": {}},
        )

    # — то, что доставщик записывает —
    def run_delivery_media_result(self, run_id, queue_id, *, ok, message_id=None,
                                  error="", chat_id="", path="", caption=""):
        self.calls.append(("result", str(queue_id), bool(ok)))

    def run_delivery_finalize_recovered(self, run_id):
        self.calls.append(("finalize", str(run_id), True))
        return True


def _adapter(agent, sender):
    a = Continuity(agent, desks=None, config_path=Path("helene.json"),
                   activity=lambda run, room: None, media_sender=sender)
    # Владельца проверяет отдельный стенд; здесь судится порядок разбора очереди.
    a.verify_local_owner = lambda run_id, context: None
    return a


PLAN = [{"run_id": "run-1", "status": "running",
         "conversation_id": "window-abcdef12", "items": [_item("q-1")]}]


class TheQueueIsDrainedAtAll(unittest.TestCase):

    def test_without_a_sender_nothing_is_touched(self):
        """Доставщика нет — очередь честно НЕ разбирается, а не разбирается наполовину."""
        spool = _Spool([_item("q-1")])
        agent = _Agent(PLAN, spool)
        self.assertEqual(_adapter(agent, None).deliver_pending_media(), 0)
        self.assertEqual(spool.discarded, [])
        self.assertEqual(agent.calls, [])

    def test_a_delivered_file_is_receipted_then_discarded(self):
        spool = _Spool([_item("q-1")])
        agent = _Agent(PLAN, spool)
        sent = []
        out = _adapter(agent, lambda item, room: sent.append((item.queue_id, room)) or "msg-77")
        self.assertEqual(out.deliver_pending_media(), 1)
        self.assertEqual(sent, [("q-1", "window-abcdef12")])
        self.assertEqual(spool.discarded, ["q-1"])
        self.assertEqual(agent.calls,
                         [("result", "q-1", True), ("finalize", "run-1", True)],
                         "расписка обязана лечь ДО гашения предмета, а досведение — последним")


class AFailedSendKeepsTheDebt(unittest.TestCase):

    def test_the_item_stays_in_the_queue(self):
        """Канал не ответил — долг остаётся. Погасить его здесь значило бы оставить
        человека без обещанного файла и записать это доставкой."""
        spool = _Spool([_item("q-1")])
        agent = _Agent(PLAN, spool)

        def boom(item, room):
            raise OSError("канал не ответил")

        with self.assertLogs("helene.continuity", level="ERROR"):
            self.assertEqual(_adapter(agent, boom).deliver_pending_media(), 0)
        self.assertEqual(spool.discarded, [], "предмет погашен по ошибке канала")
        self.assertIn(("result", "q-1", False), agent.calls)


class AnAlreadyAnsweredFileIsNotSentTwice(unittest.TestCase):

    def test_ack_closes_the_slot_without_sending(self):
        """Расписка уже есть — повтор был бы ВТОРЫМ файлом человеку."""
        for policy in ("ack", "drop"):
            with self.subTest(policy=policy):
                spool = _Spool([_item("q-1")])
                agent = _Agent(PLAN, spool, policy=policy)
                sent = []
                out = _adapter(agent, lambda item, room: sent.append(item.queue_id) or "x")
                self.assertEqual(out.deliver_pending_media(), 0)
                self.assertEqual(sent, [], "файл ушёл второй раз")
                self.assertEqual(spool.discarded, ["q-1"], "долг не погашен — прогон вечен")


if __name__ == "__main__":
    unittest.main()
