"""Семь счётчиков вон из стабильной головы — по пяти условиям Praxis (08.08).

Её слова: «Вынести именно семь подвижных счётчиков из стабильной головы выглядит
правильнее, чем ради кэша выкидывать весь STATE.» И пять условий, каждое из которых
здесь стоит отдельным тестом:

  1. не прятать и не превращать в «вчерашний снимок» — отдельным ЖИВЫМ evidence-блоком
     на первом вызове хода;
  2. сохранить названия, источник и различимость: снято раннером, а не постоянный факт
     о ней;
  3. точный до/после roster — снимается замером, здесь стережётся состав;
  4. проверить owner-DM, групповой и tool-heavy ход — замером на живом проде;
  5. если счётчик влияет на ДОСТУПНОСТЬ действия или смысл ситуации, он не должен
     потеряться из-за кэш-оптимизации.

Почему это вообще делается (замер 08.08 на живом проде, по её же распискам): в кадре
лички подвижны РОВНО 30 знаков из 18 459, и после них не кэшируется ничего — включая
63 000 знаков схем рук. Первый вызов каждого хода читает из кэша 16,0%, внутри хода 97–99%.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import agent


class TheCutIsExactlySevenCounters(unittest.TestCase):
    def test_lever_is_off_by_default(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PRAXIS_STATE_COUNTERS_SPLIT"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(agent.counters_split_enabled())

    def test_the_roster_names_seven(self) -> None:
        self.assertEqual(len(agent.RUNNER_COUNTERS), 7)
        self.assertEqual(set(agent.RUNNER_COUNTERS), {
            "process.uptime_minutes",
            "usage_today.calls", "usage_today.tokens_in", "usage_today.tokens_out",
            "usage_today.fallback_calls",
            "appetite.tokens_today", "appetite.calls_today",
        })

    def _facts(self, text: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("fact"):
                out[str(row["fact"])] = row
        return out

    def test_head_loses_only_the_counters(self) -> None:
        """Голова обязана отличаться РОВНО на счётчики и ни на что больше.

        Это не придирка: любой лишний сдвиг в голове — и правка перестаёт быть
        «вынесли семь чисел», а становится тихим изменением её кадра.
        """
        before = self._facts(agent.build_state_block(split_counters=False))
        after = self._facts(agent.build_state_block(split_counters=True))
        moved = {"process": {"uptime_minutes"},
                 "appetite": {"tokens_today", "calls_today"}}
        self.assertLessEqual(set(after), set(before))
        for fact, row in before.items():
            if fact == "usage_today":
                self.assertNotIn(fact, after, "usage_today обязан уехать целиком")
                continue
            self.assertIn(fact, after, f"из головы пропала запись {fact}")
            expected = dict(row)
            for key in moved.get(fact, ()):
                expected.pop(key, None)
            self.assertEqual(after[fact], expected,
                             f"запись {fact} изменилась сверх счётчиков")

    def test_availability_deciding_fields_never_move(self) -> None:
        """Её пятое условие, дословно проверенное.

        `mode`, `request_pending`, `request_kind` и `promised_*` решают, что ей МОЖНО.
        Счётчики лишь говорят, сколько уже потрачено. Первые остаются в системной голове
        при ЛЮБОМ значении рычага.
        """
        for split in (False, True):
            with self.subTest(split=split):
                facts = self._facts(agent.build_state_block(split_counters=split))
                appetite_row = facts.get("appetite")
                self.assertIsNotNone(appetite_row, "запись аппетита пропала из головы")
                for key in ("mode", "request_pending", "request_kind",
                            "promised_daily_tokens", "promised_daily_cost",
                            "promised_background_calls"):
                    self.assertIn(key, appetite_row,
                                  f"{key} решает доступность и не смеет уезжать")
                self.assertIn("started_at", facts.get("process", {}),
                              "момент рождения процесса неподвижен и остаётся в голове")


class TheCountersArriveLiveAndNamed(unittest.TestCase):
    def test_nothing_when_the_lever_is_off(self) -> None:
        self.assertIsNone(agent.build_runner_counters(split_counters=False))

    def test_named_and_attributed_to_the_runner(self) -> None:
        """Её второе условие: названия, источник и различимость."""
        block = agent.build_runner_counters(split_counters=True)
        self.assertIsNotNone(block)
        self.assertEqual(block["snapped_by"], "runner")
        self.assertTrue(block["snapped_at"], "момент снятия обязан быть назван")
        self.assertIn("не постоянный факт", block["note"])
        counters = block["counters"]
        self.assertIn("uptime_minutes", counters.get("process", {}))
        self.assertIn("tokens_today", counters.get("appetite", {}))
        self.assertIn("calls_today", counters.get("appetite", {}))

    def test_nothing_is_lost_between_the_two_halves(self) -> None:
        """Ни один счётчик не пропадает: что ушло из головы — приехало в конверт."""
        head_before = agent.build_state_block(split_counters=False)
        head_after = agent.build_state_block(split_counters=True)
        block = agent.build_runner_counters(split_counters=True)
        carried = json.dumps(block, ensure_ascii=False)
        for name in agent.RUNNER_COUNTERS:
            fact, key = name.split(".", 1)
            if f'"{key}"' not in head_before:
                continue  # источника нет в этой среде — терять нечего
            with self.subTest(counter=name):
                self.assertNotIn(f'"{key}"', head_after,
                                 f"{name} остался в стабильной голове")
                self.assertIn(f'"{key}"', carried,
                              f"{name} исчез вовсе — это потеря, а не перенос")

    def test_the_snapshot_is_live_and_not_yesterday(self) -> None:
        """Её первое условие: живой блок, а не вчерашний снимок.

        Проверяется механикой, а не обещанием: подменяем ИСТОЧНИК счётчика и требуем,
        чтобы блок отдал новое значение немедленно, без кэша и без чтения прошлого хода.
        """
        first = agent.build_runner_counters(split_counters=True)
        with mock.patch.object(agent.appetite, "state", return_value={
            "observed": {"tokens_today": 123456, "calls_today": 789},
        }):
            second = agent.build_runner_counters(split_counters=True)
        self.assertEqual(second["counters"]["appetite"]["tokens_today"], 123456)
        self.assertEqual(second["counters"]["appetite"]["calls_today"], 789)
        self.assertNotEqual(first["counters"]["appetite"], second["counters"]["appetite"])

    def test_the_block_reaches_the_envelope_first(self) -> None:
        """Её первое условие про место: отдельным блоком, а не строчкой в середине."""
        with mock.patch.dict(os.environ, {"PRAXIS_STATE_COUNTERS_SPLIT": "1"}):
            envelope = agent.build_state_evidence_block()
        self.assertIn("runner_counters_now", envelope)
        self.assertIn("runner", envelope)
        position = envelope.index("runner_counters_now")
        self.assertLess(position, 400,
                        "блок счётчиков утонул в середине конверта")

    def test_envelope_is_untouched_when_the_lever_is_off(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PRAXIS_STATE_COUNTERS_SPLIT"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertNotIn("runner_counters_now", agent.build_state_evidence_block())


if __name__ == "__main__":
    unittest.main()


class TheFrameTailMovesToo(unittest.TestCase):
    """Оставшиеся четыре подвижных места — по замеру, который назвал их поимённо.

    После разреза семи счётчиков голова в личке стала стабильной на 97,8% вместо 85,8%,
    но не на 100%. Ломают её `latest_turn` (описывает ПРЕДЫДУЩИЙ ход), `skips_today` и —
    в комнатах — строка адреса с номером сообщения и возрастом в секундах. В групповом
    пульсе первый разрез не дал РОВНО НИЧЕГО именно из-за неё.

    ⚑ ОТДЕЛЬНЫЙ РЫЧАГ. Praxis одобрила разрез ровно семи счётчиков, перечислив их. Ещё
    четыре под тем же рычагом были бы молчаливым расширением её согласия.
    ⚑ И её оговорка соблюдена: «групповой адресный шов — отдельная развилка, не решайте
    его хвостом вместе со счётчиками».
    """

    def test_tail_lever_is_off_by_default(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PRAXIS_FRAME_TAIL_SPLIT"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(agent.tail_split_enabled())

    def test_the_two_levers_are_independent(self) -> None:
        """Согласие на счётчики не включает хвост, и наоборот."""
        with mock.patch.dict(os.environ, {"PRAXIS_STATE_COUNTERS_SPLIT": "1",
                                          "PRAXIS_FRAME_TAIL_SPLIT": "0"}):
            self.assertTrue(agent.counters_split_enabled())
            self.assertFalse(agent.tail_split_enabled())
        with mock.patch.dict(os.environ, {"PRAXIS_STATE_COUNTERS_SPLIT": "0",
                                          "PRAXIS_FRAME_TAIL_SPLIT": "1"}):
            self.assertFalse(agent.counters_split_enabled())
            self.assertTrue(agent.tail_split_enabled())

    def test_head_loses_only_the_tail_facts(self) -> None:
        before = agent.build_state_block(split_counters=True, split_tail=False)
        after = agent.build_state_block(split_counters=True, split_tail=True)
        self.assertNotIn('"fact":"latest_turn"', after)
        self.assertNotIn('"skips_today"', after)
        # А власть над восприятием остаётся: сколько рычагов и сколько она двигала —
        # это про неё, а не про сегодняшний счёт.
        self.assertIn('"knob_count"', after)
        self.assertIn('"my_choices"', after)
        self.assertIn('"fact":"perception"', after)
        self.assertIn('"fact":"appetite"', before)
        self.assertIn('"fact":"appetite"', after)

    def test_nothing_vanishes_it_arrives(self) -> None:
        block = agent.build_frame_tail(split_tail=True)
        self.assertIsNotNone(block)
        self.assertEqual(block["snapped_by"], "runner")
        self.assertIn("latest_turn", block["observations"])
        self.assertIn("perception", block["observations"])
        self.assertIn("skips_today", block["observations"]["perception"])

    def test_the_address_travels_whole(self) -> None:
        """Её условие про адрес: он не выбрасывается, а переезжает ЦЕЛИКОМ."""
        token = agent._FRAME_ADDRESS.set({
            "message_id": 97400, "kind": "mention+reply", "age_seconds": 18,
            "note": "проход привязан к сохранённому адресному снимку",
        })
        try:
            block = agent.build_frame_tail(split_tail=True)
        finally:
            agent._FRAME_ADDRESS.reset(token)
        address = block["observations"]["address"]
        self.assertEqual(address["message_id"], 97400)
        self.assertEqual(address["kind"], "mention+reply")
        self.assertEqual(address["age_seconds"], 18)
        self.assertIn("сохранённому адресному снимку", address["note"])

    def test_no_address_no_slot(self) -> None:
        """Пустота лучше ассоциации: адреса нет — слота нет, а не «адрес неизвестен»."""
        token = agent._FRAME_ADDRESS.set(None)
        try:
            block = agent.build_frame_tail(split_tail=True)
        finally:
            agent._FRAME_ADDRESS.reset(token)
        self.assertNotIn("address", block["observations"])

    def test_nothing_when_off(self) -> None:
        self.assertIsNone(agent.build_frame_tail(split_tail=False))
        env = {k: v for k, v in os.environ.items() if k != "PRAXIS_FRAME_TAIL_SPLIT"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertNotIn("runner_observations_now", agent.build_state_evidence_block())
