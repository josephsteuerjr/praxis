"""Пустая очередь модерации обязана снимать приоритет — иначе он кормит сам себя.

Петля, замкнутая на себя, случилась ДВАЖДЫ. 27.08 её причиной было то, что у
пробуждений отобрали `end_turn`: разбор модерации падал, события копились. Причину
закрыли, петлю — нет, и 28.08 она собралась заново уже при ПУСТОЙ очереди:

    флаг поднят → каждый живой чат уходит в `_defer_pass`
                → `_defer_pass` кладёт задачу в `_debounce`
                → непустой `_debounce` запрещает запускать разбор
                → разбор — единственный, кто снимает флаг
                → флаг остаётся поднятым

Симптом снаружи: она не отвечает, и в логе НЕТ ни одной ошибки. Поэтому здесь
проверяется не «код не падает», а то, что петля разомкнута в своём звене.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

import mtproto_runner


class AnEmptyQueueLowersThePriority(unittest.TestCase):

    def setUp(self):
        self._saved = mtproto_runner._MODERATION_PRIORITY_PENDING
        self.addCleanup(setattr, mtproto_runner,
                        "_MODERATION_PRIORITY_PENDING", self._saved)
        self._saved_failures = mtproto_runner._MODERATION_TICK_FAILURES
        mtproto_runner._MODERATION_TICK_FAILURES = 0
        self.addCleanup(setattr, mtproto_runner,
                        "_MODERATION_TICK_FAILURES", self._saved_failures)

    def _run_tick(self, pending, *, passing=(), debounce=None):
        events = mock.Mock()
        events.enabled.return_value = True
        events.undelivered.return_value = pending
        module = mock.Mock(events=events)
        with mock.patch.dict("sys.modules", {"core": module, "core.events": events}), \
             mock.patch.object(mtproto_runner, "_passing", set(passing)), \
             mock.patch.object(mtproto_runner, "_debounce", dict(debounce or {})):
            asyncio.run(mtproto_runner._moderation_events_once())

    def test_an_empty_queue_clears_a_raised_flag(self):
        """Сердце починки: уступать больше нечему — и это надо сказать, а не промолчать."""
        mtproto_runner._MODERATION_PRIORITY_PENDING = True
        self._run_tick([])
        self.assertFalse(mtproto_runner._MODERATION_PRIORITY_PENDING,
                         "флаг остался поднятым при пустой очереди — петля цела")

    def test_a_raised_flag_survives_a_non_empty_queue(self):
        """Обратная сторона: пока событие ждёт, приоритет обязан стоять."""
        mtproto_runner._MODERATION_PRIORITY_PENDING = False
        self._run_tick([{"id": "e1"}], passing={"809306689"})
        self.assertTrue(mtproto_runner._MODERATION_PRIORITY_PENDING,
                        "непустая очередь перестала требовать следующий ход")

    def test_a_disabled_source_clears_a_stale_priority(self):
        """Выключенный источник не может законно задерживать любой живой чат."""
        mtproto_runner._MODERATION_PRIORITY_PENDING = True
        events = mock.Mock()
        events.enabled.return_value = False
        module = mock.Mock(events=events)
        with mock.patch.dict("sys.modules", {"core": module, "core.events": events}):
            asyncio.run(mtproto_runner._moderation_events_once())
        self.assertFalse(mtproto_runner._MODERATION_PRIORITY_PENDING,
                         "выключенная модерация оставила голос под ложным приоритетом")

    def test_the_flag_falls_even_while_chats_are_debounced(self):
        """Тот самый узел. Раньше непустой `_debounce` запирал единственного, кто
        снимает флаг, — а наполнял `_debounce` сам же поднятый флаг. Теперь снятие
        происходит ДО этой проверки, поэтому запертость её больше не удерживает."""
        mtproto_runner._MODERATION_PRIORITY_PENDING = True
        busy = mock.Mock()
        busy.done.return_value = False
        self._run_tick([], passing={"-1001240718803"}, debounce={"809306689": busy})
        self.assertFalse(mtproto_runner._MODERATION_PRIORITY_PENDING,
                         "занятость чатов снова удержала флаг — это и есть петля")

    def test_a_broken_event_store_does_not_strand_the_flag_raised(self):
        """Если хранилище событий вовсе не отвечает, прежний код уходил тем же голым
        `return`. Оставить флаг поднятым по ошибке чтения значит оглохнуть из-за сбоя
        прибора — а прибор не должен решать, слышит ли она."""
        mtproto_runner._MODERATION_PRIORITY_PENDING = True
        events = mock.Mock()
        events.enabled.return_value = True
        events.undelivered.side_effect = RuntimeError("хранилище недоступно")
        module = mock.Mock(events=events)
        with mock.patch.dict("sys.modules", {"core": module, "core.events": events}), \
             mock.patch.object(mtproto_runner, "_passing", set()), \
             mock.patch.object(mtproto_runner, "_debounce", {}):
            asyncio.run(mtproto_runner._moderation_events_once())
        # Здесь поведение НЕ меняется: сбой чтения не считается пустой очередью, и
        # флаг остаётся как был. Тест фиксирует это как решение, а не как случайность:
        # молча опустить приоритет по ошибке чтения значило бы проглотить модерацию.
        self.assertTrue(mtproto_runner._MODERATION_PRIORITY_PENDING,
                        "ошибка чтения опустила приоритет — модерация может пропасть")

    def _run_broken_tick(self):
        events = mock.Mock()
        events.enabled.return_value = True
        events.undelivered.side_effect = RuntimeError("хранилище недоступно")
        module = mock.Mock(events=events)
        with mock.patch.dict("sys.modules", {"core": module, "core.events": events}), \
             mock.patch.object(mtproto_runner, "_passing", set()), \
             mock.patch.object(mtproto_runner, "_debounce", {}):
            asyncio.run(mtproto_runner._moderation_events_once())

    def test_a_persistently_broken_store_lowers_the_flag_loudly(self):
        """У поднятого флага при нечитаемой очереди есть срок годности.

        28.08 адверсарка провела цепь целиком: один рваный байт в журнале —
        undelivered падает на КАЖДОМ тике — флаг стоит — все чаты в defer — ноль
        строк INFO — глухота до рестарта. Одиночный сбой флага по-прежнему не
        трогает (тест выше), но серия сбоев подряд обязана снять его вслух:
        сбой прибора не может бессрочно решать, слышит ли она."""
        mtproto_runner._MODERATION_PRIORITY_PENDING = True
        for _ in range(mtproto_runner._MODERATION_FAILURE_TICKS - 1):
            self._run_broken_tick()
        self.assertTrue(mtproto_runner._MODERATION_PRIORITY_PENDING,
                        "флаг упал раньше порога — её решение про одиночный сбой стёрто")
        with self.assertLogs(mtproto_runner.log, level="WARNING"):
            self._run_broken_tick()
        self.assertFalse(mtproto_runner._MODERATION_PRIORITY_PENDING,
                         "серия сбоев чтения не сняла флаг — глухота снова бессрочная")

    def test_one_good_tick_resets_the_failure_streak(self):
        """Серия — это ПОДРЯД: успешный тик обнуляет счёт, мигание не копится."""
        mtproto_runner._MODERATION_PRIORITY_PENDING = True
        for _ in range(mtproto_runner._MODERATION_FAILURE_TICKS - 1):
            self._run_broken_tick()
        self._run_tick([{"id": "e1"}], passing={"809306689"})  # очередь читается
        for _ in range(mtproto_runner._MODERATION_FAILURE_TICKS - 1):
            self._run_broken_tick()
        self.assertTrue(mtproto_runner._MODERATION_PRIORITY_PENDING,
                        "мигающие сбои сложились в серию — счёт не обнулился")


if __name__ == "__main__":
    unittest.main()
