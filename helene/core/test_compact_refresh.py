"""Адресный ремонт устаревших свёрток — её шаг 4.

Слой покрытия/показа честно перестал показывать свёртку, чьё исходное сообщение
потом правили: recap не едет ни в кадр, ни в поиск, ни в цитату. Самовосстановление
через раздувание горячего кольца она запретила осознанно, и потому потеря названа
ДОЛГОМ. Ремонт — единственный путь этот долг закрыть.

Её спека дословно: «пересобрать только затронутые leaf-компакты из ТЕКУЩЕЙ проекции
их исходных message lineage, после чего обычной свёрткой поднять новые родители.
Старые immutable-компакты остаются аудитом, но не frontier текущей памяти. Новый
компакт — новый артефакт, а не переписывание старого файла». И отдельно: «не
запускать молча модельную регенерацию».

Запуск:  python praxis_test.py test_compact_refresh -v
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import memory_life as ml
import memory_provenance
import telegram_routes as tr

ROOM = "-1001240718803"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_refresh_"))
        self._orig = {name: getattr(ml, name) for name in
                      ("BASE", "MEM_DIR", "LIFE_DIR", "EVENTS_DIR", "COMPACTS_DIR",
                       "EPISODES_DIR", "CLAIMS_DIR", "PATCHES_DIR", "REFLECTIONS_DIR",
                       "STATE_DIR", "LEGACY_SUMMARIES_DIR", "DIALOGUES_DIR",
                       "REFRESH_QUIET_SEC", "_model_compact")}
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
        ml.REFRESH_QUIET_SEC = 0.0
        self.calls = []
        ml._model_compact = self._model
        self._routes = tr.DIR
        tr.DIR = self.tmp / "memory" / ".state" / "group_context"
        self.addCleanup(self._restore)

    def _restore(self):
        for name, value in self._orig.items():
            setattr(ml, name, value)
        tr.DIR = self._routes
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _model(self, inputs, **kw):
        """Заглушка модели: пересказывает ровно то, что ей показали."""
        self.calls.append([str(x.get("id")) for x in inputs])
        shown = " | ".join(str(x.get("line") or "") for x in inputs)
        return {"summary": f"НОВАЯ СВОДКА({len(inputs)}): {shown}"[:4000],
                "open_threads": [], "claims": [], "episodes": []}

    # ------------------------------------------------------------------ сцена
    def _msg(self, mid, text, *, ts=None):
        return ml.record_message(ROOM, f"Николай: {text}", actor="Николай",
                                 direction="in", source_id=str(mid),
                                 ts=float(mid) if ts is None else ts,
                                 dedupe_key=f"telegram:{ROOM}:{mid}:in")

    def _compact(self, rows, summary="старая сводка"):
        return ml._write_compact(
            ROOM, {"summary": summary, "open_threads": [], "claims": [], "episodes": []},
            tier=1, depth=1, source_events=[r["id"] for r in rows], source_compacts=[],
            event_count=len(rows), continued=False,
            first_ts=min(str(r["ts"]) for r in rows),
            last_ts=max(str(r["ts"]) for r in rows))

    def _edit(self, mid, text, ts=10_000.0):
        ml.record_message(ROOM, f"Николай: {text}", actor="Николай", direction="in",
                          source_id=f"{mid}:edit:1", ts=ts,
                          dedupe_key=f"telegram:{ROOM}:{mid}:edit:in")
        ml.note_message_revision(ROOM, mid, f"Николай: {text}")

    def _delete(self, mid, ts=10_000.0):
        line = f"Telegram [deleted #{mid}]: message removed"
        ml.record_message(ROOM, line, actor="Telegram", direction="in",
                          source_id=f"{mid}:delete", ts=ts,
                          dedupe_key=f"telegram:{ROOM}:{mid}:delete:in")
        ml.note_message_revision(ROOM, mid, line)

    def _scene(self, count=100, edit_at=50):
        rows = [self._msg(i, f"реплика {i}") for i in range(1, count + 1)]
        compact = self._compact(rows)
        if edit_at:
            self._edit(edit_at, "переписала")
        return rows, compact


class TestTheDebtActuallyCloses(Base):
    def test_a_stale_leaf_is_rebuilt_and_the_debt_reaches_zero(self):
        _rows, old = self._scene()
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 1)
        out = ml.refresh_compacts(ROOM, dry_run=False)
        self.assertEqual(out["blocked"], [], out)
        self.assertEqual(len(out["done"]), 1)
        debt = ml.refresh_debt(ROOM)
        self.assertEqual(debt["needs_refresh"], 0, "долг не закрылся")
        self.assertEqual(debt["retired"], 1, "снятие не записано")
        self.assertGreaterEqual(debt["presentable"], 1, "кадру нечего показать")

    def test_the_old_file_stays_on_disk_as_audit(self):
        """Компакт неизменяем: файл не переписывается и не удаляется никогда."""
        _rows, old = self._scene()
        path = ml._compact_dir(ROOM) / f"{old['id']}.md"
        before = path.read_bytes()
        ml.refresh_compacts(ROOM, dry_run=False)
        self.assertTrue(path.exists(), "старый компакт удалён — это не аудит")
        self.assertEqual(path.read_bytes(), before, "старый компакт переписан")

    def test_the_successor_is_a_new_artifact(self):
        _rows, old = self._scene()
        out = ml.refresh_compacts(ROOM, dry_run=False)
        new_ids = out["done"][0]["successors"]
        self.assertTrue(new_ids)
        self.assertNotIn(old["id"], new_ids)
        for new_id in new_ids:
            self.assertTrue((ml._compact_dir(ROOM) / f"{new_id}.md").exists())

    def test_the_frame_gets_the_new_recap(self):
        self._scene()
        self.assertNotIn("НОВАЯ СВОДКА", ml.context_summary(ROOM))
        ml.refresh_compacts(ROOM, dry_run=False)
        self.assertIn("НОВАЯ СВОДКА", ml.context_summary(ROOM),
                      "кадр не восстановился — ремонт бесполезен")


class TestTheInvariantsHold(Base):
    def test_no_event_becomes_hot_again_or_disappears(self):
        rows, _old = self._scene()
        ml.refresh_compacts(ROOM, dry_run=False)
        state = ml.rebuild_state(ROOM)
        self.assertEqual([str(r.get("source_id")) for r in state["hot"]], ["50:edit:1"],
                         "ремонт вернул в горячее чужие события")
        presentable, coverage, _l, _r, _p = ml._both_graphs(ROOM)
        covered = {str(e) for _c, (m, _x) in coverage.items()
                   for e in (m.get("source_event_ids") or [])}
        hot_ids = {str(r.get("id")) for r in state["hot"]}
        self.assertEqual(covered & hot_ids, set(),
                         "событие одновременно горячее и свёрнутое")
        current = {str(r.get("id")) for r in
                   memory_provenance.current_conversation_events(
                       ml.iter_events(chat_id=ROOM, kinds={"conversation_message"}))}
        self.assertEqual(current - covered - hot_ids, set(), "событие пропало")

    def test_the_superseded_body_never_reaches_the_model(self):
        """Единственная дверь, через которую тело правленого могло бы вернуться, —
        показ модели старого recap или старой строки. Она закрыта."""
        rows, _old = self._scene()
        ml.refresh_compacts(ROOM, dry_run=False)
        self.assertTrue(self.calls, "модель не звалась вовсе")
        shown = set(sum(self.calls, []))
        self.assertNotIn(rows[49]["id"], shown,
                         "модели показали вытесненную ревизию")
        self.assertNotIn("реплика 50", ml.context_summary(ROOM))

    def test_a_deletion_is_bounded_the_same_way(self):
        rows = [self._msg(i, f"реплика {i}") for i in range(1, 21)]
        self._compact(rows, summary="старая сводка про секрет")
        self._delete(7)
        ml.refresh_compacts(ROOM, dry_run=False)
        summary = ml.context_summary(ROOM)
        self.assertNotIn("реплика 7", summary, "тело удалённого вернулось в кадр")
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)


class TestNothingHappensSilently(Base):
    def test_dry_run_is_the_default_and_never_calls_the_model(self):
        self._scene()
        out = ml.refresh_compacts(ROOM)
        self.assertTrue(out["dry_run"])
        self.assertEqual(self.calls, [], "модель позвана без явного разрешения")
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 1)
        self.assertEqual(out["plan"]["model_calls_total"], 1, out["plan"])

    def test_the_plan_names_the_price_before_paying_it(self):
        self._scene()
        plan = ml.refresh_plan(ROOM)
        self.assertEqual(self.calls, [])
        row = plan["rows"][0]
        self.assertEqual(row["action"], "rebuild")
        self.assertEqual(row["kind"], "leaf")
        self.assertEqual(row["dropped"], 1)
        self.assertEqual(row["survivors"], 99)

    def test_a_silent_model_stops_the_run_and_says_so(self):
        self._scene()
        ml._model_compact = lambda *a, **kw: {}
        out = ml.refresh_compacts(ROOM, dry_run=False)
        self.assertEqual(out["done"], [])
        self.assertEqual(out["blocked"][0]["reason"], "model_unavailable")
        debt = ml.refresh_debt(ROOM)
        self.assertEqual(debt["needs_refresh"], 1, "долг закрылся без работы")
        self.assertEqual(debt["blocked"], 1, "отказ не назван в описи")

    def test_a_blocked_row_is_not_retried_for_free(self):
        self._scene()
        ml._model_compact = lambda *a, **kw: {}
        ml.refresh_compacts(ROOM, dry_run=False)
        plan = ml.refresh_plan(ROOM)
        self.assertEqual(plan["model_calls_total"], 0,
                         "заблокированная строка снова просит модель")
        self.assertEqual(plan["rows"][0]["action"], "blocked")


class TestThePlanIsHonestAboutSize(Base):
    def test_a_huge_leaf_becomes_several_successors(self):
        """Один устаревший лист не обязан влезать в один промпт: на живых данных
        такой лист несёт полторы тысячи событий, а в промпт входит около полутора
        сотен. Иначе ремонт встал бы ровно там же, где стояла свёртка."""
        rows = [self._msg(i, "реплика " + "я" * 900) for i in range(1, 61)]
        self._compact(rows)
        self._edit(3, "переписала")
        plan = ml.refresh_plan(ROOM)
        self.assertGreater(plan["rows"][0]["parts"], 1, plan["rows"][0])
        self.assertEqual(plan["model_calls_total"], plan["rows"][0]["parts"])
        out = ml.refresh_compacts(ROOM, dry_run=False)
        self.assertGreater(len(out["done"][0]["successors"]), 1)
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)

    def test_a_parent_costs_no_model_call(self):
        rows = [self._msg(i, f"реплика {i}") for i in range(1, 21)]
        kid = self._compact(rows[:10], summary="ребёнок один")
        kid2 = self._compact(rows[10:], summary="ребёнок два")
        ml._write_compact(ROOM, {"summary": "родитель", "open_threads": [],
                                 "claims": [], "episodes": []},
                          tier=2, depth=2, source_events=[],
                          source_compacts=[kid["id"], kid2["id"]], event_count=20,
                          continued=False, first_ts=str(rows[0]["ts"]),
                          last_ts=str(rows[-1]["ts"]))
        self._edit(3, "переписала")
        plan = ml.refresh_plan(ROOM)
        parents = [r for r in plan["rows"] if r["kind"] == "parent"]
        self.assertTrue(parents, plan)
        self.assertEqual(parents[0]["model_calls"], 0,
                         "родитель просит модель — он поднимается свёрткой, не ремонтом")

    def test_a_second_run_does_nothing(self):
        self._scene()
        ml.refresh_compacts(ROOM, dry_run=False)
        before = len(self.calls)
        out = ml.refresh_compacts(ROOM, dry_run=False)
        self.assertEqual(out["done"], [])
        self.assertEqual(len(self.calls), before, "ремонт повторил работу")


class TestTheDebtDescribesItselfHonestly(Base):
    def test_an_empty_place_is_told_apart_from_an_unknown_one(self):
        rows = [self._msg(i, f"реплика {i}") for i in range(1, 6)]
        self._compact(rows)
        живое = ml.refresh_debt(ROOM)
        self.assertTrue(живое["known"])
        self.assertEqual(живое["needs_refresh"], 0)
        чужое = ml.refresh_debt("-1009999999999")
        self.assertFalse(чужое["known"], "неизвестное место выдано за чистое")
        self.assertEqual(чужое["tree"], str(ml.MEM_DIR))

    def test_the_debt_counts_leaves_parents_and_files(self):
        self._scene()
        debt = ml.refresh_debt(ROOM)
        self.assertEqual(debt["leaf_debt"], 1)
        self.assertEqual(debt["parent_debt"], 0)
        self.assertGreaterEqual(debt["parsed"], 1)
        self.assertEqual(debt["broken"], 0)


if __name__ == "__main__":
    unittest.main()
