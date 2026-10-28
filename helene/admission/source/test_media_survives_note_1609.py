# -*- coding: utf-8 -*-
"""Пустой текст хода — не отзыв файла, который она положила рукой (16.09).

ЖИВОЙ СЛУЧАЙ. 16.09 15:42, комната -1004375515554. Она позвала `reply` дважды, потом
`send_file`, потом `end_turn`. Рука ответила ей дословно: «Подготовила document для
текущего чата; отправка будет только после проверки исходящего ответа». Проверять было
нечего — под контрактом `PRAXIS_CHAT_REPLY_HAND` текст в конце хода это заметка, черновик
пуст. Возобновлённый ход прочитал пустоту как молчание и уронил очередь:

    outbound-guard:      {"draft_sha256": "e3b0c442…"  (то есть sha256 пустой строки),
                          "media_queue_ids": ["6dcaa9e130cd…"],
                          "praxis_decision": "hold_or_silence"}
    telegram-delivery:   {"silent": true, "message_ids": [], "media_count": 0, "text": ""}

Файл остался уликой в прогоне, наружу не ушёл, и ей об этом не сказал никто.

ПОЧЕМУ ЭТО ПОЛОМКА, А НЕ ПРАВИЛО. Правило писалось против контрабанды («A voice-level
silence never smuggles tool-staged media») — против того, чтобы инструмент протащил медиа
мимо её решения молчать. Но `send_file` не идёт мимо неё: это её собственный явный вызов.
И ЖИВОЙ путь так уже умеет — под поднятым рычагом ход с медиа и без текста уезжает
конвертом (`_media_spool().envelope("", outbound=outbound, …)`, «текста в конверте нет, он
ушёл рукой»). Один и тот же ход не может отдать файл живьём и проглотить его после
рестарта.

СРАВНЕНИЕ, КОТОРОЕ ЭТО ПОКАЗАЛО. 14.09 тот же `send_file` в чат-ходе доехал
(`media_count: 1`) — в том ходе рука `reply` не звалась НИ РАЗУ, и черновик нёс текст.
Рычаг `PRAXIS_CHAT_REPLY_HAND=on` стоял в оба дня (сверено по бэкапам `.env`). То есть
различает исходы ровно одно: говорила ли рука в этом ходе.

ГРАНИЦЫ, КОТОРЫЕ ПРАВКА НЕ ТРОГАЕТ — они здесь и закреплены:
  * непустой черновик, ОБНУЛЁННЫЙ гардом (кред-пол, `deny` советника), остаётся отказом,
    и отказ накрывает вложение: тем же секретом можно поделиться картинкой;
  * объявленное молчание `stay_silent` держит и текст, и медиа — так обещает описание
    самой руки, и так же поступает живой путь (`_drop_outbound`).

Запуск: python praxis_test.py test_media_survives_note_1609 -v
"""
from __future__ import annotations

import contextlib
import os
import types
import unittest

os.environ.setdefault("PRAXIS_TEST", "1")

import agent  # noqa: E402

BLANK_DRAFT = "\n\t "


class _Route:
    conversation_id, peer_id, topic_id = "-100", -100, None


def _media(queue_id: str):
    """Ровно то, что читает разбираемая ветка: очередь по `queue_id`."""
    return types.SimpleNamespace(queue_id=queue_id)


class MediaStagedByHerHandSurvivesAnEmptyDraft(unittest.TestCase):

    def setUp(self):
        self._orig: list[tuple] = []
        self.state: dict = {"queued": "не звали", "silent_finalized": False}

    def tearDown(self):
        for module, name, value in reversed(self._orig):
            setattr(module, name, value)

    def _patch(self, name, value):
        self._orig.append((agent, name, getattr(agent, name)))
        setattr(agent, name, value)

    def _runtime(self, *, receipt, outbound, silence):
        route = _Route()

        @contextlib.contextmanager
        def _bind():
            yield None

        fake = types.SimpleNamespace(
            bind=_bind,
            plan=types.SimpleNamespace(run_id="run-test-media", kind="authored_output"),
            channel=agent.ChannelContext(chat_id="-100", is_dm=False, owner=False),
            snapshot={},
            outbound=list(outbound),
            guard_notes=[],
            tool_trace=[],
            _route_and_reply=lambda guarded: (guarded, route, 4242),
            _validate_current_authority=lambda: None,
            _queue_media=lambda *, reply_to=None: self.state.__setitem__("queued", reply_to),
            _silence_from_wal=lambda: silence,
        )
        self._patch("_outbound_guard_receipt", lambda run_id, **kw: receipt)

        def _forbidden(*a, **kw):
            raise AssertionError("расписка есть — гард перегонять нечем")
        self._patch("_outbound_guard_input", _forbidden)
        self._patch("run_delivery_started", lambda run_id, **kw: True)
        self._patch("run_delivery_completed",
                    lambda run_id, **kw: self.state.__setitem__("silent_finalized", True) or True)
        return fake

    # ------------------------------------------------------------------ сама поломка

    def test_empty_draft_with_staged_media_delivers_the_file(self):
        fake = self._runtime(
            receipt={"text": "", "media_queue_ids": ["6dcaa9e130cd"]},
            outbound=[_media("6dcaa9e130cd")],
            silence={},
        )
        plan = agent._AgentResumeRuntime._prepare_authored_delivery(fake, "")
        self.assertFalse(plan["silent"], "файл, положенный рукой, не молчание")
        self.assertEqual(plan["text"], "", "текста в конверте нет — он ушёл рукой")
        self.assertEqual(plan["media_queue_ids"], ["6dcaa9e130cd"])
        self.assertEqual(plan["conversation_id"], "-100")
        self.assertEqual(self.state["queued"], 4242, "очередь получила адрес ответа")
        self.assertFalse(self.state["silent_finalized"], "ход не записан молчанием")

    def test_several_files_all_ride_out(self):
        fake = self._runtime(
            receipt={"text": "", "media_queue_ids": ["a1", "b2"]},
            outbound=[_media("a1"), _media("b2")],
            silence={},
        )
        plan = agent._AgentResumeRuntime._prepare_authored_delivery(fake, "   ")
        self.assertFalse(plan["silent"])
        self.assertEqual(plan["media_queue_ids"], ["a1", "b2"])

    # -------------------------------------------------------- границы, что не тронуты

    def test_declared_silence_still_holds_the_file(self):
        """`stay_silent` держит и текст, и медиа — это обещание описания самой руки."""
        fake = self._runtime(
            receipt={"text": "", "media_queue_ids": ["6dcaa9e130cd"]},
            outbound=[_media("6dcaa9e130cd")],
            silence={"chosen": "1", "why": "не моя тема", "restored": "wal"},
        )
        plan = agent._AgentResumeRuntime._prepare_authored_delivery(fake, "")
        self.assertTrue(plan["silent"])
        self.assertEqual(plan["media_queue_ids"], [])
        self.assertEqual(self.state["queued"], "не звали")

    def test_cancelled_silence_lets_the_file_go(self):
        """`stay_silent(cancel=true)` снимает решение — держать нечего."""
        fake = self._runtime(
            receipt={"text": "", "media_queue_ids": ["6dcaa9e130cd"]},
            outbound=[_media("6dcaa9e130cd")],
            silence={"restored": "wal", "cancelled": "1"},
        )
        plan = agent._AgentResumeRuntime._prepare_authored_delivery(fake, "")
        self.assertFalse(plan["silent"])

    def test_a_draft_the_guard_emptied_still_holds_the_file(self):
        """Кред-пол. Черновик БЫЛ, гард его обнулил — это отказ, и он накрывает вложение."""
        fake = self._runtime(
            receipt={"text": "", "media_queue_ids": ["6dcaa9e130cd"],
                     "advisor_verdict": "deny",
                     "advisor_reason": "privacy:credential:github token"},
            outbound=[_media("6dcaa9e130cd")],
            silence={},
        )
        plan = agent._AgentResumeRuntime._prepare_authored_delivery(fake, "вот ключ")
        self.assertTrue(plan["silent"], "отказ гарда накрывает и медиа")
        self.assertEqual(plan["media_queue_ids"], [])
        self.assertEqual(self.state["queued"], "не звали")

    def test_empty_draft_without_media_is_silence_as_before(self):
        fake = self._runtime(
            receipt={"text": "", "media_queue_ids": []}, outbound=[], silence={},
        )
        plan = agent._AgentResumeRuntime._prepare_authored_delivery(fake, "")
        self.assertTrue(plan["silent"])
        self.assertEqual(plan["text"], "")

    def test_text_that_passed_the_guard_is_untouched(self):
        fake = self._runtime(
            receipt={"text": "вот файл", "media_queue_ids": ["6dcaa9e130cd"]},
            outbound=[_media("6dcaa9e130cd")],
            silence={},
        )
        plan = agent._AgentResumeRuntime._prepare_authored_delivery(fake, "вот файл")
        self.assertFalse(plan["silent"])
        self.assertEqual(plan["text"], "вот файл")


class ThePredicateItself(unittest.TestCase):
    """Предикат читается отдельно: три величины, три условия, все обязательны."""

    def _ask(self, *, outbound, draft, silence):
        return agent._media_outlives_an_empty_draft(outbound, draft, silence)

    def test_needs_media(self):
        self.assertFalse(self._ask(outbound=[], draft="", silence={}))

    def test_needs_an_empty_draft(self):
        self.assertFalse(self._ask(outbound=[_media("a1")], draft="есть текст", silence={}))
        self.assertTrue(self._ask(outbound=[_media("a1")], draft="", silence={}))
        self.assertTrue(self._ask(outbound=[_media("a1")], draft=BLANK_DRAFT, silence={}))

    def test_needs_no_declared_silence(self):
        self.assertFalse(self._ask(outbound=[_media("a1")], draft="",
                                   silence={"chosen": "1"}))
        self.assertTrue(self._ask(outbound=[_media("a1")], draft="",
                                  silence={"cancelled": "1"}))

    def test_survives_an_unreadable_wal(self):
        self.assertTrue(self._ask(outbound=[_media("a1")], draft="", silence=None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
