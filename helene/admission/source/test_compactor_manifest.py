"""
Пункт 6: свёртка не заявляет источником то, чего модель не видела.

Запуск:  python praxis_test.py test_compactor_manifest -v
"""

from __future__ import annotations

import unittest

import memory_life as ml


def _ev(i: int, size: int = 100) -> dict:
    return {"id": f"evt-{i:03d}", "ts": f"2026-07-25T00:{i:02d}:00Z",
            "line": f"{i}:" + "я" * size, "salience": 2}


class TestPromptManifest(unittest.TestCase):
    def test_manifest_splits_seen_clipped_and_omitted(self):
        events = [_ev(i, 50) for i in range(5)]
        events[2]["line"] = "2:" + "я" * (ml.EVENT_CLIP_CHARS + 500)
        body, man = ml._pack_compact_prompt(events)
        self.assertEqual(man["omitted"], [])
        self.assertEqual(man["clipped"], ["evt-002"])
        self.assertEqual(set(man["seen"]),
                         {"evt-000", "evt-001", "evt-003", "evt-004"})
        for e in events:
            self.assertIn(e["id"], body, "все влезшие обязаны быть в промпте")

    def test_events_that_do_not_fit_are_named_not_swallowed(self):
        """Ровно тот дефект: метаданные заявляли 35 источников, модель видела 27."""
        big = ml.EVENT_CLIP_CHARS
        count = (ml.PROMPT_BUDGET_CHARS // big) + 6
        events = [_ev(i, big) for i in range(count)]
        body, man = ml._pack_compact_prompt(events)
        self.assertTrue(man["omitted"], "часть событий обязана быть названа невлезшей")
        self.assertEqual(len(man["seen"]) + len(man["clipped"]) + len(man["omitted"]),
                         count, "манифест обязан покрывать вход без остатка")
        for ident in man["omitted"]:
            self.assertNotIn(ident, body, "невлезшее не может быть в промпте")
        self.assertLessEqual(len(body), ml.PROMPT_BUDGET_CHARS + big)

    def test_the_prefix_that_folds_is_the_OLD_one(self):
        """Свежее остаётся горячим. Обратный порядок оставлял бы горячими самые старые
        события, и они не влезали бы снова и снова — голодание вместо прогресса."""
        big = ml.EVENT_CLIP_CHARS
        count = (ml.PROMPT_BUDGET_CHARS // big) + 6
        events = [_ev(i, big) for i in range(count)]
        _body, man = ml._pack_compact_prompt(events)
        packed = man["seen"] + man["clipped"]
        self.assertIn("evt-000", packed, "самое старое сворачивается")
        self.assertIn(events[-1]["id"], man["omitted"], "самое свежее остаётся горячим")
        # и границa непрерывна: пакованное — это префикс входа
        order = [e["id"] for e in events]
        self.assertEqual(sorted(packed, key=order.index), order[:len(packed)])

    def test_single_oversized_event_still_folds(self):
        """Одно событие крупнее бюджета не должно заклинивать свёртку навсегда."""
        events = [_ev(0, ml.PROMPT_BUDGET_CHARS * 2)]
        body, man = ml._pack_compact_prompt(events)
        self.assertEqual(man["omitted"], [])
        self.assertEqual(man["clipped"], ["evt-000"])
        self.assertIn("evt-000", body)


class TestFoldPlanFitsOnePrompt(unittest.TestCase):
    """Её решение 21.08, п.4: порог свёртки задаётся размером упакованного префикса,
    а не числом событий.

    Живой замер 21.08 по AbstractDL: кольцо 3 802 записи при потолке 125. План просил
    свернуть `count - HOT_LO` = 3 752 события одним промптом; упаковщик брал префикс
    на 48 000 знаков и честно объявлял остальное невлезшим; следующий проход планировал
    те же 3 752. Сорок три вызова модели в сутки — ноль срезанных событий.
    """

    RING = 3802

    def _ring(self, count=None, size=400, *, out_every=0):
        count = self.RING if count is None else count
        rows = []
        for i in range(count):
            row = {"id": f"evt-{i:05d}",
                   "ts": f"2026-08-{1 + i // 1440:02d}T{(i // 60) % 24:02d}:{i % 60:02d}:00Z",
                   "line": f"{i}: " + "я" * size, "salience": 2}
            if out_every and i % out_every == 0:
                row["direction"] = "out"
            rows.append(row)
        return rows

    def test_the_planner_and_the_packer_measure_the_same_thing(self):
        """Две формулы размера — и план снова разойдётся с тем, что влезает."""
        ring = self._ring()
        fit = ml.budget_prefix(ring)
        _body, man = ml._pack_compact_prompt(ring[:fit])
        self.assertEqual(man["omitted"], [], "планировщик считает не то, что упаковщик")
        _body, man = ml._pack_compact_prompt(ring[:fit + 1])
        self.assertEqual(man["omitted"], [ring[fit]["id"]],
                         "префикс не максимальный: влезло бы ещё одно")

    def test_the_plan_never_asks_for_more_than_one_prompt_holds(self):
        ring = self._ring()
        plan = ml.plan_hot_fold(ring)
        self.assertTrue(plan["due"], plan)
        self.assertLessEqual(plan["fold"], ml.budget_prefix(ring), plan)
        _body, man = ml._pack_compact_prompt(ring[:plan["fold"]])
        self.assertEqual(man["omitted"], [],
                         "план отдал модели больше, чем она берёт — это и был тупик")

    def test_progress_is_monotone_on_the_runaway_ring(self):
        """Каждый успешный проход обязан уменьшать кольцо — её слово дословно."""
        for out_every in (0, 7):
            ring = self._ring(out_every=out_every)
            sizes = [len(ring)]
            for _ in range(15):
                plan = ml.plan_hot_fold(ring)
                if not plan.get("due"):
                    break
                self.assertGreaterEqual(plan["fold"], 1, plan)
                ring = ring[plan["fold"]:]
                sizes.append(len(ring))
            self.assertGreater(len(sizes), 2, f"свёртка встала на месте: {sizes}")
            self.assertEqual(sizes, sorted(sizes, reverse=True),
                             f"кольцо не убывает монотонно: {sizes}")
            self.assertEqual(len(set(sizes)), len(sizes),
                             f"проход не сдвинул кольцо: {sizes}")

    def test_the_runaway_ring_drains_to_its_window(self):
        """Не «стало меньше», а сошлось: кольцо доходит до окна за конечное число
        проходов. Именно это не происходило живьём сорок три раза в сутки."""
        ring = self._ring()
        passes = 0
        while passes < 500:
            plan = ml.plan_hot_fold(ring)
            if not plan.get("due"):
                break
            ring = ring[plan["fold"]:]
            passes += 1
        self.assertLess(len(ring), ml.HOT_HI, f"кольцо не сошлось за {passes} проходов")
        self.assertLess(passes, 500, "свёртка не сходится")

    def test_one_oversized_event_does_not_jam_the_fold(self):
        """Событие крупнее бюджета не имеет права заклинить свёртку навсегда:
        правило «первая строка входит всегда» обязано доехать и до планировщика."""
        ring = self._ring(count=300, size=200)
        ring[0]["line"] = "гигант: " + "я" * (ml.PROMPT_BUDGET_CHARS * 3)
        self.assertGreaterEqual(ml.budget_prefix(ring), 1)
        plan = ml.plan_hot_fold(ring)
        self.assertTrue(plan["due"], plan)
        self.assertGreaterEqual(plan["fold"], 1, plan)
        _body, man = ml._pack_compact_prompt(ring[:plan["fold"]])
        self.assertEqual(man["omitted"], [], man)

    def test_a_quiet_ring_is_untouched(self):
        """Потолок бюджета не смеет сам по себе объявлять свёртку нужной."""
        plan = ml.plan_hot_fold(self._ring(count=20, size=100))
        self.assertFalse(plan.get("due"), plan)


class TestContextSummaryPacksWholeBlocks(unittest.TestCase):
    def setUp(self):
        self._orig = ml._canonical_compact_graph
        self._state = ml._state_path
        self._cdir = ml._compact_dir

    def tearDown(self):
        ml._canonical_compact_graph = self._orig
        ml._state_path = self._state
        ml._compact_dir = self._cdir

    def _install(self, n: int, size: int):
        canonical = {}
        for i in range(n):
            meta = {"id": f"cmp-{i:03d}", "tier": 1, "depth": 1, "continued": False,
                    "degraded": False, "first_ts": f"2026-07-2{i}T00:00:00Z",
                    "created_at": f"2026-07-2{i}T00:00:00Z", "source_compact_ids": []}
            canonical[meta["id"]] = (meta, f"recap-{i} " + "ю" * size)
        ml._canonical_compact_graph = lambda chat_id: (canonical, False)

        class _P:
            def exists(self_inner):
                return True
        ml._state_path = lambda chat_id: _P()
        ml._compact_dir = lambda chat_id: _P()

    def test_never_starts_mid_sentence_and_says_what_it_dropped(self):
        self._install(6, 900)
        out = ml.context_summary("777", max_chars=3000)
        self.assertIn("СВОДКА ОБРЕЗАНА БЮДЖЕТОМ", out,
                      "выпавшие компакты обязаны быть названы")
        self.assertNotIn("cmp-000", out, "старые выпадают первыми")
        self.assertIn("cmp-005", out, "свежие остаются")
        # заголовок первого показанного блока цел — не огрызок вроде «-8a18ea57»
        first = out.split("[compact ", 1)[1]
        self.assertTrue(first.startswith("cmp-"), first[:40])

    def test_no_marker_when_everything_fits(self):
        self._install(2, 100)
        out = ml.context_summary("777", max_chars=7000)
        self.assertNotIn("СВОДКА ОБРЕЗАНА", out)
        self.assertIn("cmp-000", out)
        self.assertIn("cmp-001", out)


if __name__ == "__main__":
    unittest.main()
