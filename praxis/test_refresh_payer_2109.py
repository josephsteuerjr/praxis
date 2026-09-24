"""Долг обновления гасится сам: одна группа за проход, с потолком по времени.

Протокол погашения (`refresh_debt` / `refresh_compacts`) написан целиком и покрыт
тестами, но в проде его не звал НИКТО: замер 21.09 нашёл `refresh_debt` только в
`test_coverage_vs_current.py`. Поэтому свёртки, чьи сообщения потом правили, копились
месяцами, фронтир комнаты Ouroboros стоял с 08.09, а гасить долг приходил человек с
ручным дренажом. Плательщик теперь живёт в раннере, рядом со свёрткой.

Потолок по времени — не экономия, а ограда: ровно так выглядели все три мельницы этой
ночи, по вызову модели в минуту.

Запуск:  python praxis_test.py test_refresh_payer_2109 -v
"""

from __future__ import annotations

import asyncio
import unittest

import memory_life
import mtproto_runner as mr

PLACE = "-1003701205730"


class TheDebtPaysItself(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._debt = memory_life.refresh_debt
        self._refresh = memory_life.refresh_compacts
        self._cooldown = mr._REFRESH_COOLDOWN_SEC
        mr._REFRESH_LAST_PAID.clear()
        self.debt_calls: list[str] = []
        self.paid: list[str] = []
        memory_life.refresh_debt = self._answer_debt
        memory_life.refresh_compacts = self._answer_refresh
        self.groups = 3

    def tearDown(self) -> None:
        memory_life.refresh_debt = self._debt
        memory_life.refresh_compacts = self._refresh
        mr._REFRESH_COOLDOWN_SEC = self._cooldown
        mr._REFRESH_LAST_PAID.clear()

    def _answer_debt(self, place, *a, **k):
        self.debt_calls.append(str(place))
        return {"unresolved_group_count": self.groups, "needs_refresh": self.groups}

    def _answer_refresh(self, place, *a, **k):
        self.paid.append(str(place))
        self.groups = max(0, self.groups - 1)
        return {"ok": True, "reason": "target_refreshed", "refreshed_count": 75,
                "target_count": 75, "room_unresolved_group_count": self.groups}

    async def test_one_group_is_paid_per_pass(self):
        await mr._maybe_pay_refresh_debt(PLACE)
        self.assertEqual(self.paid, [PLACE])

    async def test_the_cooldown_holds_the_second_pass(self):
        await mr._maybe_pay_refresh_debt(PLACE)
        await mr._maybe_pay_refresh_debt(PLACE)
        self.assertEqual(len(self.paid), 1, "второй проход не подождал")

    async def test_after_the_cooldown_it_pays_again(self):
        mr._REFRESH_COOLDOWN_SEC = 0.01
        await mr._maybe_pay_refresh_debt(PLACE)
        await asyncio.sleep(0.02)
        await mr._maybe_pay_refresh_debt(PLACE)
        self.assertEqual(len(self.paid), 2)

    async def test_a_place_without_debt_costs_nothing(self):
        self.groups = 0
        await mr._maybe_pay_refresh_debt(PLACE)
        self.assertEqual(self.paid, [])
        self.assertEqual(self.debt_calls, [PLACE])

    async def test_a_broken_debt_reading_does_not_break_the_fold(self):
        def boom(*_a, **_k):
            raise RuntimeError("индекс не собрался")
        memory_life.refresh_debt = boom
        await mr._maybe_pay_refresh_debt(PLACE)
        self.assertEqual(self.paid, [])

    async def test_a_broken_payment_is_survived_too(self):
        def boom(*_a, **_k):
            raise RuntimeError("модель молчит")
        memory_life.refresh_compacts = boom
        await mr._maybe_pay_refresh_debt(PLACE)

    async def test_the_lever_turns_the_payer_off(self):
        mr._REFRESH_COOLDOWN_SEC = 0
        await mr._maybe_pay_refresh_debt(PLACE)
        self.assertEqual(self.debt_calls, [], "выключенный плательщик всё равно считал долг")


if __name__ == "__main__":
    unittest.main()
