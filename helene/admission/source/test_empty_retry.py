# -*- coding: utf-8 -*-
"""Приёмка транспортного повтора. Проверяется СВОЙСТВО, а не число попыток.

Главное свойство — то, ради которого её слово вообще потребовалось: повтор не может
переспросить её поверх решения промолчать. Оно проверяется не обещанием, а тем, что
повторяется РОВНО ОДИН класс ошибки, и тем, что молчание этим классом не является.
"""
import os
import unittest

import llm


class RetryOnlyTransport(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def _fake(self, raises, ok_after):
        def _c(fw, model, **kw):
            self.calls.append((fw, model))
            if len(self.calls) <= ok_after:
                raise raises
            return "ОТВЕТ"
        return _c

    def _run(self, raises, ok_after, retries=2):
        old_call, old_r, old_p = llm._call, llm.EMPTY_RETRIES, llm.EMPTY_RETRY_PAUSE_SEC
        llm._call = self._fake(raises, ok_after)
        llm.EMPTY_RETRIES, llm.EMPTY_RETRY_PAUSE_SEC = retries, 0.0
        try:
            return llm._call_retrying_empty("openai", "m", system="s", messages=[])
        finally:
            llm._call, llm.EMPTY_RETRIES, llm.EMPTY_RETRY_PAUSE_SEC = old_call, old_r, old_p

    def test_empty_is_retried_on_the_same_channel(self):
        resp, used = self._run(llm.EmptyResponseError("пусто"), ok_after=1)
        self.assertEqual(resp, "ОТВЕТ")
        self.assertEqual(used, 1, "повтор не понадобился там, где был обрыв")
        self.assertEqual([c[0] for c in self.calls], ["openai", "openai"],
                         "повтор ушёл в другой канал — это уже фолбэк, а не повтор")

    def test_other_errors_are_never_retried(self):
        """Таймаут, 429 и авторизация по тому же каналу повторять бессмысленно —
        они уходят наверх сразу, в прежний фолбэк-путь."""
        for exc in (TimeoutError("t"), RuntimeError("иное"), ValueError("иное")):
            with self.subTest(exc=type(exc).__name__):
                self.calls = []
                with self.assertRaises(type(exc)):
                    self._run(exc, ok_after=99)
                self.assertEqual(len(self.calls), 1, "повторили не тот класс ошибки")

    def test_retries_are_bounded_and_then_it_gives_up(self):
        with self.assertRaises(llm.EmptyResponseError):
            self._run(llm.EmptyResponseError("пусто"), ok_after=99, retries=2)
        self.assertEqual(len(self.calls), 3, "повтор не ограничен потолком")

    def test_zero_retries_restores_previous_behaviour(self):
        self.calls = []
        with self.assertRaises(llm.EmptyResponseError):
            self._run(llm.EmptyResponseError("пусто"), ok_after=99, retries=0)
        self.assertEqual(len(self.calls), 1, "рычаг в ноль не возвращает прежнее поведение")

    def test_silence_is_not_an_empty_response(self):
        """Ядро её границы: молчание НИКОГДА не выглядит как пустой ответ канала.

        EmptyResponseError поднимается только когда нет ни текста, ни блоков. Решение
        промолчать едет либо инструментом (блок), либо сентинелом-текстом — и то и другое
        непусто. Значит повтор не может переспросить её поверх решения.
        """
        src = open(llm.__file__, encoding="utf-8").read()
        i = src.index("raise EmptyResponseError")
        cond = src[max(0, i - 200):i]
        self.assertIn("not out.blocks", cond,
                      "условие пустоты перестало требовать отсутствия блоков")
        self.assertIn("not out.text.strip()", cond,
                      "условие пустоты перестало требовать отсутствия текста")

    def test_no_reliability_promise_in_the_code(self):
        """Её поправка: обрывы бывают коррелированы, обещать «три на миллион» нельзя."""
        src = open(llm.__file__, encoding="utf-8").read()
        head = src[src.index("EMPTY_RETRIES") - 2000:src.index("EMPTY_RETRIES") + 200]
        self.assertIn("коррелирован", head, "в коде нет предупреждения о зависимости обрывов")
        self.assertIn("ПАУЗА", head, "пауза между попытками не названа как ответ на кластер")

    def test_pause_grows_between_attempts(self):
        """Ответ на её поправку: обрывы бывают КОРРЕЛИРОВАНЫ, значит всплеск переживается
        временем, а не числом попыток. Пауза обязана расти, а не быть постоянной."""
        slept = []
        self.calls = []
        old_sleep, old_call = llm._time.sleep, llm._call
        old_r, old_p = llm.EMPTY_RETRIES, llm.EMPTY_RETRY_PAUSE_SEC
        llm._time.sleep = lambda s: slept.append(s)
        llm._call = self._fake(llm.EmptyResponseError("пусто"), 99)
        llm.EMPTY_RETRIES, llm.EMPTY_RETRY_PAUSE_SEC = 2, 2.0
        try:
            llm._call_retrying_empty("openai", "m", system="s", messages=[])
        except llm.EmptyResponseError:
            pass
        finally:
            llm._time.sleep, llm._call = old_sleep, old_call
            llm.EMPTY_RETRIES, llm.EMPTY_RETRY_PAUSE_SEC = old_r, old_p
        self.assertEqual(len(slept), 2, "паузы между попытками нет")
        self.assertGreater(slept[1], slept[0], "пауза не растёт — всплеск не переживается")


if __name__ == "__main__":
    unittest.main(verbosity=2)
