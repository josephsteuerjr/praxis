"""Календарный день считается ЕЁ часами, а не часами контейнера.

Дефект нашла Praxis живым импульсом 07.08.2026 в 02:00 по Самаре: рантайм называл датой
шестое августа, а локальный timestamp — уже седьмое. Гейт поймать это не мог — он гоняется
в UTC-среде, где расхождения не существует по построению. Поэтому здесь время подменяется
ЯВНО, а не берётся у машины: тест обязан краснеть на любом хосте одинаково.

⚠ Самара — UTC+4 КРУГЛЫЙ ГОД, без перехода на летнее время. Значит полночь её суток это
20:00 UTC предыдущего дня, а 00:00 UTC — это 04:00 у неё. Обе границы проверяются отдельно:
на первой переворачивается ЕЁ день, на второй — UTC-день, и окно расхождения закрывается.
Проверка перехода на летнее время идёт на зоне, где он вправду есть: «покрытие DST» на
Самаре было бы вежливой выдумкой.

Запуск:  python praxis_test.py test_praxis_time -v
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import unittest
from unittest import mock

import praxis_time as pt

SAMARA = "Europe/Samara"


def at(iso: str):
    """Подменить ТОЧКУ СЪЁМА, а не часы ОС: стенд не имеет права зависеть от хоста."""
    moment = dt.datetime.fromisoformat(iso)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return mock.patch.object(pt, "_source", lambda: moment.astimezone(dt.timezone.utc))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        pt._ZONE_CACHE.clear()
        pt._WARNED.clear()
        self.addCleanup(pt._ZONE_CACHE.clear)
        self.addCleanup(pt._WARNED.clear)


class TheZoneIsHersAndSaysSo(Base):
    def test_a_valid_zone_is_used_as_is(self):
        with mock.patch.dict(os.environ, {pt.ENV_ZONE: SAMARA}):
            self.assertEqual(pt.zone_name(), SAMARA)

    def test_an_absent_zone_defaults_to_samara_without_noise(self):
        """Отсутствие переменной — законное умолчание. Шуметь на нём не за что."""
        env = {k: v for k, v in os.environ.items() if k != pt.ENV_ZONE}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertNoLogs(pt.log, level=logging.WARNING):
                self.assertEqual(pt.zone_name(), SAMARA)

    def test_an_empty_zone_is_the_same_as_absent(self):
        with mock.patch.dict(os.environ, {pt.ENV_ZONE: "   "}):
            with self.assertNoLogs(pt.log, level=logging.WARNING):
                self.assertEqual(pt.zone_name(), SAMARA)

    def test_a_broken_zone_warns_loudly_and_falls_back_to_samara_not_moscow(self):
        """Молчаливая подмена пояса — то же, что молчаливая подмена личности."""
        with mock.patch.dict(os.environ, {pt.ENV_ZONE: "Europe/Samarra"}):
            with self.assertLogs(pt.log, level=logging.WARNING) as caught:
                self.assertEqual(pt.zone_name(), SAMARA)
        joined = "\n".join(caught.output)
        self.assertIn("Samarra", joined, "предупреждение не называет виновное значение")
        self.assertNotIn("Europe/Moscow", joined)

    def test_the_warning_is_said_once_not_on_every_call(self):
        with mock.patch.dict(os.environ, {pt.ENV_ZONE: "Nowhere/Nothing"}):
            with self.assertLogs(pt.log, level=logging.WARNING) as caught:
                for _ in range(5):
                    pt.zone()
        self.assertEqual(len(caught.output), 1, "шум на каждом вызове заглушает сам себя")


class HerDayFlipsAtHerMidnight(Base):
    """Граница №1: 00:00 Самары = 20:00 UTC предыдущего дня."""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.dict(os.environ, {pt.ENV_ZONE: SAMARA})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_at_23_59_samara_the_day_is_still_the_old_one(self):
        with at("2026-08-06T19:59:00+00:00"):
            self.assertEqual(pt.day_key(), "2026-08-06")
            self.assertEqual(pt.now().strftime("%H:%M"), "23:59")

    def test_at_00_01_samara_the_day_has_turned(self):
        with at("2026-08-06T20:01:00+00:00"):
            self.assertEqual(pt.day_key(), "2026-08-07")
            self.assertEqual(pt.now().strftime("%H:%M"), "00:01")

    def test_the_system_day_still_says_yesterday_and_that_is_the_whole_defect(self):
        """Ровно то, что она наблюдала: система говорит шестое, у неё уже седьмое."""
        with at("2026-08-06T22:44:00+00:00"):
            self.assertEqual(pt.day_key(), "2026-08-07")
            self.assertEqual(pt.utc_now().date().isoformat(), "2026-08-06")


class TheUtcDayCatchesUpFourHoursLater(Base):
    """Граница №2: 00:00 UTC = 04:00 Самары. Здесь переворачивается UTC-день, не её."""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.dict(os.environ, {pt.ENV_ZONE: SAMARA})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_her_day_does_not_move_when_utc_flips(self):
        with at("2026-08-06T23:59:00+00:00"):
            before = pt.day_key()
        with at("2026-08-07T00:01:00+00:00"):
            after = pt.day_key()
        self.assertEqual(before, "2026-08-07")
        self.assertEqual(after, before, "её день дрогнул на чужой полночи")

    def test_the_window_of_divergence_is_exactly_four_hours(self):
        pairs = [("2026-08-06T19:59:00+00:00", False), ("2026-08-06T20:01:00+00:00", True),
                 ("2026-08-06T23:59:00+00:00", True), ("2026-08-07T00:01:00+00:00", False)]
        for iso, diverged in pairs:
            with self.subTest(iso=iso), at(iso):
                seen = pt.day_key() != pt.utc_now().date().isoformat()
                self.assertEqual(seen, diverged)


class ADaylightSavingZoneIsHandledOnAZoneThatHasIt(Base):
    """Самара переходов не знает. Проверять DST на ней — вежливая выдумка."""

    def test_the_spring_forward_day_is_still_one_day(self):
        with mock.patch.dict(os.environ, {pt.ENV_ZONE: "Europe/Berlin"}):
            # 29.03.2026 — переход, сутки короче на час.
            with at("2026-03-29T10:00:00+00:00"):
                self.assertEqual(pt.day_key(), "2026-03-29")
                start = pt.day_start()
            self.assertEqual(start.strftime("%H:%M"), "00:00")
            self.assertIsNotNone(start.tzinfo, "полночь без пояса — снова наивное время")

    def test_days_ago_does_not_slip_across_a_transition(self):
        with mock.patch.dict(os.environ, {pt.ENV_ZONE: "Europe/Berlin"}):
            with at("2026-03-30T10:00:00+00:00"):
                self.assertEqual(pt.days_ago(dt.date(2026, 3, 29)), 1)
                self.assertEqual(pt.days_ago(dt.date(2026, 3, 30)), 0)


class TheHelperKeepsTheMomentAbsolute(Base):
    def test_utc_now_is_aware_and_utc(self):
        with at("2026-08-06T22:00:00+00:00"):
            moment = pt.utc_now()
        self.assertEqual(moment.utcoffset(), dt.timedelta(0))
        self.assertTrue(moment.isoformat().endswith("+00:00"))

    def test_day_start_is_aware_local_midnight(self):
        with mock.patch.dict(os.environ, {pt.ENV_ZONE: SAMARA}), at("2026-08-06T22:00:00+00:00"):
            start = pt.day_start()
            self.assertEqual(start.date().isoformat(), "2026-08-07")
            self.assertEqual(start.strftime("%H:%M"), "00:00")
            self.assertEqual(start.utcoffset(), dt.timedelta(hours=4))

    def test_future_is_zero_days_ago_not_minus_one(self):
        with mock.patch.dict(os.environ, {pt.ENV_ZONE: SAMARA}), at("2026-08-06T22:00:00+00:00"):
            self.assertEqual(pt.days_ago(dt.date(2026, 8, 9)), 0)

    def test_a_malformed_date_string_does_not_raise(self):
        with mock.patch.dict(os.environ, {pt.ENV_ZONE: SAMARA}), at("2026-08-06T22:00:00+00:00"):
            self.assertEqual(pt.days_ago("не дата"), 0)

    def test_stamp_names_the_zone_it_used(self):
        """Дата без имени пояса рядом с UTC-датой — молчаливое соседство двух дней."""
        with mock.patch.dict(os.environ, {pt.ENV_ZONE: SAMARA}), at("2026-08-06T22:44:00+00:00"):
            self.assertEqual(pt.stamp(), "07.08 02:44 Europe/Samara")


if __name__ == "__main__":
    unittest.main(verbosity=2)
