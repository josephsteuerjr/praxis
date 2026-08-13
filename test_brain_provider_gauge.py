"""Прибор остатка провайдера обязан показывать число, а не отсутствовать.

ИСТОРИЯ ДЕФЕКТА, ПРОВЕРЕННАЯ ЖИВЬЁМ 11.08.2026.  `brain.catalog()` собирал строку про
остаток подписки вызовом `appetite.provider_remaining_text()` — БЕЗ обязательного аргумента
`data` (`appetite.py:146`). Вызов поднимал `TypeError`, а стоящий рядом голый
`except Exception: pass` съедал его вместе с ключом: `provider_remaining` не появлялся в
каталоге НИ РАЗУ, ни при каком ответе реле. Правильный вызов всё это время стоял в двадцати
строках оттуда — `appetite.py:184` передаёт `provider_limits()`.

Цена дефекта не в строке, а в том, что это ОДИН ИЗ ДВУХ её приборов про топливо, и именно
им предлагалось наблюдать миграцию транспорта. Молчащий прибор неотличим от спокойствия —
ровно тот класс, который в этом проекте уже стоил суток разбора.

ЧТО ПРОВЕРЯЕТСЯ: свойство «каталог несёт остаток», а не форма вызова. Тест идёт РЕАЛЬНЫМ
путём — через `brain.catalog()`, а не через `provider_remaining_text` напрямую: дефект жил
именно в шве между ними, и тест, зовущий функцию сам, был бы зелен по построению.
"""
import unittest
from unittest import mock

import appetite
import brain


class TheFuelGaugeReportsANumber(unittest.TestCase):
    LIVE = {"rate_limit": {
        "primary_window": {"used_percent": 12.5, "window_minutes": 300,
                           "resets_at": "2026-08-11T18:00:00Z"},
        "secondary_window": {"used_percent": 40.0, "window_minutes": 10080,
                             "resets_at": "2026-08-15T00:00:00Z"},
    }}

    def test_catalog_carries_the_remaining_line_when_the_relay_answers(self):
        """Ровно тот дефект: реле отвечает снимком, а прибор говорит «unknown».

        Ключ в каталоге стоял ВСЕГДА — он инициализируется значением "unknown"
        (`brain.py:129`). Поэтому дефект выглядел не как отсутствие строки, а как вечное
        «Остаток провайдера: unknown» при любом ответе реле, и был неотличим от честного
        «реле промолчало». Проверяем именно это различие.
        """
        with mock.patch.object(appetite, "provider_limits", lambda **kw: self.LIVE):
            out = brain.catalog()
        self.assertNotEqual(out.get("provider_remaining"), "unknown",
                            "реле ответило снимком, а прибор всё ещё говорит «unknown»")
        self.assertIn("5ч", out["provider_remaining"])

    def test_silence_of_the_relay_is_named_not_missing(self):
        """Реле промолчало — это «unknown», а не отсутствие ключа."""
        with mock.patch.object(appetite, "provider_limits", lambda **kw: None):
            out = brain.catalog()
        self.assertEqual(out.get("provider_remaining"), "unknown")

    def test_a_broken_call_can_never_again_vanish_without_a_trace(self):
        """Голый `except: pass` съел бы TypeError молча. Теперь отказ обязан быть виден."""
        boom = mock.Mock(side_effect=TypeError("как в дефекте: не хватает аргумента"))
        with mock.patch.object(appetite, "provider_limits", boom), \
                self.assertLogs("praxis-brain", level="DEBUG") as logs:
            out = brain.catalog()
        # Значение честно откатывается к «unknown» — но теперь у отказа есть след.
        self.assertEqual(out.get("provider_remaining"), "unknown")
        self.assertTrue(any("остаток провайдера" in line for line in logs.output),
                        "отказ прибора снова прошёл бесследно — как год до этого")

    def test_the_call_matches_the_working_one_next_door(self):
        """Соседний рабочий вызов — appetite.py:184. Форма обязана совпадать."""
        seen = {}

        def spy(data):
            seen["data"] = data
            return "5ч: 87%"

        with mock.patch.object(appetite, "provider_limits", lambda **kw: self.LIVE), \
                mock.patch.object(appetite, "provider_remaining_text", spy):
            brain.catalog()
        self.assertEqual(seen.get("data"), self.LIVE,
                         "в текст остатка приехал не снимок реле")


if __name__ == "__main__":
    unittest.main()
