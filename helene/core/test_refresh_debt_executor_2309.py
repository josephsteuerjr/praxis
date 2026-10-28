"""Долг обновления платится в своём потоке и не душит общий пул `to_thread`.

Бут зовёт `_maybe_compact` на все буферы (242 на 23.09), а в его `finally` —
`_maybe_pay_refresh_debt`, который считает `refresh_debt` → `claim_evidence_index`
по тысячам файлов свёрток. Через общий пул это занимало все его потоки, а через тот же
пул идут `to_thread` обработки входящих. py-spy 23.09 09:35: шесть потоков из восьми в
`refresh_debt`, главный цикл свободен и ждёт — «на связи», но глухая.

Запуск:  python praxis_test.py test_refresh_debt_executor_2309 -v
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import unittest
from unittest import mock

import mtproto_runner as runner


class RefreshDebtHasItsOwnExecutorTests(unittest.TestCase):
    def test_debt_is_counted_outside_the_shared_pool(self):
        seen = {}

        def fake_debt(place):
            seen["thread"] = threading.current_thread().name
            return {"unresolved_group_count": 0}

        with mock.patch.object(runner.memory_life, "refresh_debt", fake_debt), \
                mock.patch.object(runner, "_REFRESH_LAST_PAID", {}), \
                mock.patch.object(runner, "_REFRESH_COOLDOWN_SEC", 600.0):
            asyncio.run(runner._maybe_pay_refresh_debt("room-2309"))
        self.assertTrue(seen["thread"].startswith("praxis-refresh-debt"), seen)

    def test_stuck_debt_does_not_block_to_thread(self):
        """Висящий платёж не мешает обычному `to_thread` — ровно то, что сломалось на буте."""
        release = threading.Event()

        def slow_debt(place):
            release.wait(5)
            return {"unresolved_group_count": 0}

        async def scenario():
            # Общий пул в один поток: старый код занимал бы его платежом, и `to_thread` ждал бы.
            asyncio.get_running_loop().set_default_executor(
                concurrent.futures.ThreadPoolExecutor(max_workers=1))
            debt = asyncio.create_task(runner._maybe_pay_refresh_debt("room-slow"))
            await asyncio.sleep(0.05)
            quick = await asyncio.wait_for(asyncio.to_thread(lambda: "ok"), timeout=2)
            release.set()
            await debt
            return quick

        with mock.patch.object(runner.memory_life, "refresh_debt", slow_debt), \
                mock.patch.object(runner, "_REFRESH_LAST_PAID", {}), \
                mock.patch.object(runner, "_REFRESH_COOLDOWN_SEC", 600.0):
            self.assertEqual(asyncio.run(scenario()), "ok")

    def test_payment_goes_through_the_same_executor(self):
        seen = []

        def debt(place):
            seen.append(("debt", threading.current_thread().name))
            return {"unresolved_group_count": 1}

        def pay(place):
            seen.append(("pay", threading.current_thread().name))
            return {"reason": "refreshed", "refreshed_count": 1, "target_count": 1,
                    "room_unresolved_group_count": 0}

        with mock.patch.object(runner.memory_life, "refresh_debt", debt), \
                mock.patch.object(runner.memory_life, "refresh_compacts", pay), \
                mock.patch.object(runner, "_REFRESH_LAST_PAID", {}), \
                mock.patch.object(runner, "_REFRESH_COOLDOWN_SEC", 600.0):
            asyncio.run(runner._maybe_pay_refresh_debt("room-pay"))
        self.assertEqual([k for k, _ in seen], ["debt", "pay"])
        self.assertTrue(all(name.startswith("praxis-refresh-debt") for _, name in seen), seen)


if __name__ == "__main__":
    unittest.main()
