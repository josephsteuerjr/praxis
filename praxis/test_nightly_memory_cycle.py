# active_workspace:praxis-repo/praxis/test_nightly_memory_cycle.py
"""Ночной цикл честности памяти — фокусные однопроцессные тесты (ТЗ 2026-10-05).

Запуск: python praxis_test.py test_nightly_memory_cycle -v
Никаких subprocess-спавнов и сети: llm.chat полностью заменяется заглушкой,
фикстуры — tmp-дерево с подменой каталогов memory_life/people/rooms/night_memory.

Что проверяется (номера — пункты тест-плана ордера):
1. Идемпотентность ночи: повторный run() того же дня = ноль дублей квитанций/вердиктов.
2. Жёсткий порог: pressure → offer (не авто-свёртка); fold_place → свёртка; reject → статус+причина.
3. Компакты через voice: заглушка llm.chat регистрирует роли; role == 'voice'.
4. Ночь end-to-end: три исхода сверки в досье; канонические записи датированы; квитанции в JSONL.
5. Гномы: вердикты в JSONL; в промпте нет дневника/диалогов/soul; канон не изменён (hash до/после).
6. Round-trip шапки people (last_verified переживает write); маркер возраста в _render_lifted.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import memory_life as ml
import night_memory as nm
import people
import rooms


class NightBase(unittest.TestCase):
    """tmp-дерево + подмена каталогов memory_life/people/rooms/night_memory."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_night_"))
        self._orig_ml = {name: getattr(ml, name) for name in
                         ("BASE", "MEM_DIR", "LIFE_DIR", "EVENTS_DIR", "COMPACTS_DIR",
                          "EPISODES_DIR", "CLAIMS_DIR", "PATCHES_DIR", "REFLECTIONS_DIR",
                          "STATE_DIR", "LEGACY_SUMMARIES_DIR", "DIALOGUES_DIR")}
        ml.BASE = self.tmp
        ml.MEM_DIR = self.tmp / "memory"
        ml.LIFE_DIR = ml.MEM_DIR / "life"
        ml.EVENTS_DIR = ml.LIFE_DIR / "events"
        ml.COMPACTS_DIR = ml.LIFE_DIR / "compacts"
        ml.EPISODES_DIR = ml.LIFE_DIR / "episodes"
        ml.CLAIMS_DIR = ml.LIFE_DIR / "claims"
        ml.PATCHES_DIR = ml.LIFE_DIR / "patches"
        ml.REFLECTIONS_DIR = ml.LIFE_DIR / "reflections"
        ml.STATE_DIR = ml.MEM_DIR / ".state" / "life"
        ml.LEGACY_SUMMARIES_DIR = ml.MEM_DIR / ".summaries"
        ml.DIALOGUES_DIR = ml.MEM_DIR / "dialogues"
        self._orig_nm = {name: getattr(nm, name) for name in
                         ("BASE", "MEM_DIR", "STATE_DIR", "RECEIPTS_PATH", "GNOME_VERDICTS_PATH")}
        nm.BASE = self.tmp
        nm.MEM_DIR = ml.MEM_DIR
        nm.STATE_DIR = ml.MEM_DIR / ".state"
        nm.RECEIPTS_PATH = nm.STATE_DIR / "sleep_receipts.jsonl"
        nm.GNOME_VERDICTS_PATH = nm.STATE_DIR / "gnome_verdicts.jsonl"
        self._orig_people = people.PEOPLE_DIR
        people.PEOPLE_DIR = ml.MEM_DIR / "people"
        self._orig_rooms = rooms.ROOMS_DIR
        rooms.ROOMS_DIR = ml.MEM_DIR / "rooms"
        # роли вызовов llm.chat — центральная заглушка
        self.llm_calls: list[dict] = []
        self.llm_responses: list[str] = []
        import llm
        self._llm = llm
        self._patchers = [
            mock.patch.object(llm, "chat", self._fake_chat),
            mock.patch.object(llm, "configured", self._fake_configured),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(self._restore)

    def _restore(self):
        for p in self._patchers:
            p.stop()
        ml.__dict__.update(self._orig_ml)
        nm.__dict__.update(self._orig_nm)
        people.PEOPLE_DIR = self._orig_people
        rooms.ROOMS_DIR = self._orig_rooms
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_configured(self, role: str = "") -> bool:
        return True

    def _fake_chat(self, role: str, system: str = "", messages=None, max_tokens: int = 0):
        self.llm_calls.append({"role": role, "system": system,
                               "user": (messages or [{}])[-1].get("content", ""),
                               "max_tokens": max_tokens})
        text = self.llm_responses.pop(0) if self.llm_responses else "{}"
        return mock.Mock(text=text)

    # helpers ------------------------------------------------------------- #

    def seed_person(self, slug: str, name: str, facts: list[str]) -> None:
        people.write(slug, name, {people.FACTS: "\n".join(f"- {f}" for f in facts)})

    def seed_hot(self, place: str, n: int) -> None:
        """Наполнить горячее окно места n записями через штатный record_message."""
        for i in range(n):
            ml.record_message(place, f"u{i}: сообщение {i} в {place}",
                              actor=f"u{i}", direction="in", source="test")


class TestReceiptsAndIdempotency(NightBase):
    def test_receipt_requires_reason_for_skip(self):
        with self.assertRaises(ValueError):
            nm.receipt("r1", "2026-10-05", "x", "skipped")
        nm.receipt("r1", "2026-10-05", "x", "skipped", reason="почта")
        self.assertEqual(len(nm.receipts("2026-10-05")), 1)

    def test_night_idempotent_rerun_zero_duplicates(self):
        """План 1: двойной прогон шагов того же дня — вторая ночь не пишет новых строк."""
        self.seed_person("anna", "Анна", ["работает юристом"])
        day = "2026-10-05"
        import praxis_time
        with mock.patch.object(praxis_time, "today",
                               return_value=__import__("datetime").date(2026, 10, 5)):
            run_id = nm._run_id()
            out1 = nm.resolve_fold_offers({})
            g1 = (nm.gnome_compact_audit(day), nm.gnome_contradiction_audit(day),
                  nm.freshness_candidates(day))
            v1 = nm.verify_pass(g1[2])
            # повтор
            out2 = nm.resolve_fold_offers({})
            g2 = (nm.gnome_compact_audit(day), nm.gnome_contradiction_audit(day),
                  nm.freshness_candidates(day))
            v2 = nm.verify_pass(g2[2])
        receipts_rows = nm.receipts(day)
        verdicts = nm._read_jsonl(nm.GNOME_VERDICTS_PATH)
        # вторая ночь не дублирует вердикты гномов по (gnome, subject, day)
        keys = [(r.get("gnome"), r.get("subject")) for r in verdicts]
        self.assertEqual(len(keys), len(set(keys)), f"дубли вердиктов: {keys}")
        self.assertEqual(out1, out2)
        # идемпотентность сверки: во второй ночи нет кандидатов (страж свежести
        # уже записал их в этот день) — ноль обработок, ноль записей канона
        self.assertEqual(v2.get("lines") or [], [])
        self.assertEqual(sum(v2.get(k, 0) for k in ("confirmed", "refuted", "unverifiable")), 0)


class TestHardThresholdOffer(NightBase):
    def test_hard_pressure_creates_offer_not_autofold(self):
        """План 2: жёсткое давление → offer, авто-свёртки нет."""
        place = "-1001"
        lo, hi, hard_hi, _cap = ml.hot_bounds(place)
        self.seed_hot(place, hard_hi + 5)
        out = ml.compact_if_due(place)
        self.assertTrue(out.get("offered"), f"ожидался offer, получено: {out}")
        self.assertIn(place, ml.fold_offers())
        self.assertEqual(out.get("hot"), hard_hi + 5, "авто-свёртка не должна была случиться")

    def test_fold_now_folds_and_clears_offer(self):
        place = "-1002"
        lo, hi, hard_hi, _cap = ml.hot_bounds(place)
        self.seed_hot(place, hard_hi + 5)
        ml.compact_if_due(place)
        self.llm_responses.append(json.dumps({
            "summary": "тестовая сводка", "open_threads": [], "claims": [], "episodes": []}))
        out = ml.fold_now(place)
        self.assertGreater(out.get("folded") or 0, 0, f"свёртка не прошла: {out}")
        self.assertNotIn(place, ml.fold_offers())

    def test_reject_fold_reason(self):
        place = "-1003"
        ml._note_fold_offer(place, {"count": 300, "tokens": 10, "fold": 100, "reason": "token_cap"})
        self.assertTrue(ml.reject_fold(place, "разговор ещё жив"))
        self.assertNotIn(place, ml.fold_offers())


class TestVoiceCompacts(NightBase):
    def test_compact_uses_voice_role(self):
        """План 3: compact_if_due идёт в модель ролью voice (не evaluator)."""
        place = "-1004"
        lo, hi, hard_hi, _cap = ml.hot_bounds(place)
        self.seed_hot(place, hard_hi + 5)
        ml.compact_if_due(place)
        self.llm_responses.append(json.dumps({
            "summary": "сводка голосом", "open_threads": [], "claims": [], "episodes": []}))
        out = ml.fold_now(place)
        self.assertGreater(out.get("folded") or 0, 0)
        roles = [c["role"] for c in self.llm_calls]
        self.assertIn("voice", roles, f"voice-роль не вызвана: {roles}")


class TestVerifyPass(NightBase):
    def test_three_verdicts_applied(self):
        """План 4: confirmed → last_verified; refuted → superseded + датированный факт;
        unverifiable → пометка «не проверяемо»."""
        self.seed_person("p1", "Первая", ["факт один"])
        self.seed_person("p2", "Вторая", ["живёт в Москве"])
        self.seed_person("p3", "Третья", ["работает врачом"])
        self.llm_responses.append(json.dumps({"verdicts": [
            {"subject": "p1", "verdict": "confirmed", "against": "упоминания в ленте"},
            {"subject": "p2", "verdict": "refuted", "against": "лента: переехала; старое: живёт в Москве"},
            {"subject": "p3", "verdict": "unverifiable", "against": "нет живых источников"},
        ]}))
        out = nm.verify_pass([
            {"kind": "person", "slug": "p1", "age_days": 40},
            {"kind": "person", "slug": "p2", "age_days": 40},
            {"kind": "person", "slug": "p3", "age_days": 40}])
        self.assertEqual(out["confirmed"], 1)
        self.assertEqual(out["refuted"], 1)
        self.assertEqual(out["unverifiable"], 1)
        v, against = people.last_verified("p1")
        self.assertTrue(v, "confirmed не поставил last_verified")
        self.assertIn("ленте", against)
        _, body2 = people.read("p2")
        self.assertIn("устарело", body2.get(people.FACTS, ""), "refuted не пометил устаревшее")
        self.assertIn("сверка", body2.get(people.FACTS, ""), "refuted не добавил датированный факт")
        v3, _ = people.last_verified("p3")
        self.assertTrue(v3)
        _, body3 = people.read("p3")
        # пометка «не проверяемо» читается кадром из against-строки шапки
        raw3 = people.read_text("p3")
        self.assertIn("не проверяемо", raw3)

    def test_unanswered_candidate_is_unverifiable(self):
        self.seed_person("px", "Икс", ["факт"])
        out = nm.verify_pass([{"kind": "person", "slug": "px", "age_days": 99}])
        self.assertEqual(out["unverifiable"], 1)


class TestGnomes(NightBase):
    def test_gnome_verdicts_jsonl_and_no_private_sources(self):
        """План 5: вердикты в JSONL; в промпте нет дневника/диалогов/soul; канон не тронут."""
        self.seed_person("gon", "Галя", ["любит чай", "не пьёт кофе"])
        # создаём один живой компакт для аудита
        self.seed_hot("-1005", 3)
        meta = ml._write_compact("-1005", {
            "summary": "разговор о чае", "open_threads": [], "claims": [], "episodes": [],
        }, tier=1, depth=1, source_events=[], source_compacts=[], event_count=3,
           continued=False)
        self.llm_responses.append(json.dumps(
            {"verdict": "ok", "detail": "суть сохранена"}))
        self.llm_responses.append(json.dumps({"conflicts": []}))
        before = {p.name: p.read_bytes() for p in people.PEOPLE_DIR.glob("*.md")}
        day = "2026-10-05"
        nm.gnome_compact_audit(day)
        nm.gnome_contradiction_audit(day)
        rows = nm._read_jsonl(nm.GNOME_VERDICTS_PATH)
        self.assertTrue(rows, "вердикты не записаны")
        for call in self.llm_calls:
            blob = call["system"] + call["user"]
            for banned in ("дневник", "diary", "soul/", "диалог"):
                self.assertNotIn(banned, blob.lower(), f"гному утёк {banned}")
        after = {p.name: p.read_bytes() for p in people.PEOPLE_DIR.glob("*.md")}
        self.assertEqual(before, after, "гномы изменили канон людей")

    def test_gnome_idempotent_same_day(self):
        self.seed_person("once", "Одна", ["факт а", "факт б"])
        day = "2026-10-05"
        self.llm_responses.append(json.dumps({"conflicts": []}))
        nm.gnome_contradiction_audit(day)
        n1 = len(nm._read_jsonl(nm.GNOME_VERDICTS_PATH))
        nm.gnome_contradiction_audit(day)
        n2 = len(nm._read_jsonl(nm.GNOME_VERDICTS_PATH))
        self.assertEqual(n1, n2, "повторный прогон того же дня продублировал вердикты")


class TestPeopleHeaderRoundTrip(NightBase):
    def test_last_verified_round_trip(self):
        """План 6: шапка last_verified переживает write→read→write."""
        people.set_last_verified("rt", "2026-10-01", "лента DM")
        v, against = people.last_verified("rt")
        self.assertEqual(v, "2026-10-01")
        self.assertEqual(against, "лента DM")
        nm_, body = people.read("rt")
        people.write("rt", nm_, body)          # вторая перезапись
        v2, against2 = people.last_verified("rt")
        self.assertEqual((v2, against2), (v, against))
        # снятие
        people.set_last_verified("rt", "")
        v3, _ = people.last_verified("rt")
        self.assertEqual(v3, "")

    def test_render_lifted_freshness_marker(self):
        people.set_last_verified("dm", "2026-10-01", "лента")
        import frame_shadow as fs
        import datetime as dt
        import praxis_time
        with mock.patch.object(praxis_time, "today", return_value=dt.date(2026, 10, 5)):
            src = {"path": people.path_for("dm"), "raw": b"# DM\n\n## \xd0\xa4\xd0\xb0\xd0\xba\xd1\x82\xd1\x8b\n- x\n",
                   "sha": "0" * 64, "body": "# DM\n\n## Факты\n- x\n",
                   "private_hidden": 0, "transfer": "свободно", "foot": ""}
            text, _rows = fs._render_lifted("header", src, limit=4000)
        self.assertIn("сверено: 4д", text)
        # без строки — «не сверено»
        people.set_last_verified("dm", "")
        src2 = dict(src, body="# DM2\n", raw=b"# DM2\n")
        with mock.patch.object(praxis_time, "today", return_value=dt.date(2026, 10, 5)):
            text2, _rows = fs._render_lifted("header", src2, limit=4000)
        self.assertIn("не сверено", text2)


class TestRoomsHeader(NightBase):
    def test_last_verified_header_survives_profile_update(self):
        rooms.profile_update("-2001", mode="normal")
        rooms.profile_update("-2001", last_verified="2026-10-05 подтверждено лентой")
        prof = rooms.profile_read("-2001")
        self.assertIn("подтверждено лентой", prof["header"].get("last_verified", ""))
        # повторная запись другого поля не стирает
        rooms.profile_update("-2001", greeted="yes")
        prof2 = rooms.profile_read("-2001")
        self.assertTrue(prof2["header"].get("last_verified"))


if __name__ == "__main__":
    unittest.main()
