"""Покрытие и показ — два разных вопроса. Её решение 21.08.2026.

Одна проверка отвечала на оба, и потому одна правка одного сообщения делала
недействительной ВСЮ свёртку — вместе с сотней чужих событий. Замер того же дня по
AbstractDL: 445 ключей в составе места, 311 файлов компактов, принято 65, убито 245;
события мёртвых свёрток возвращались в горячее кольцо на КАЖДОЙ правке, и оно выросло
до 3 802 при потолке 125 — единственное такое из 225 колец.

Её слово: «coverage graph… последующая ревизия сообщения не отменяет факт, что именно
это событие уже было свёрнуто» и «presentable/current graph остаётся строгим… только он
допускается в context_summary, formation, обычный recall/FTS и цитирование».

Здесь — её же минимальный гейт первого диффа, семь пунктов.

Запуск:  python praxis_test.py test_coverage_vs_current -v
"""

from __future__ import annotations

import contextlib
import multiprocessing
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import memory_fts
import memory_life as ml
import memory_provenance
import telegram_routes as tr

ROOM = "-100999999999999999"
OTHER = "-1009999999999"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_cov_"))
        self._orig = {name: getattr(ml, name) for name in
                      ("BASE", "MEM_DIR", "LIFE_DIR", "EVENTS_DIR", "COMPACTS_DIR",
                       "EPISODES_DIR", "CLAIMS_DIR", "PATCHES_DIR", "REFLECTIONS_DIR",
                       "STATE_DIR", "LEGACY_SUMMARIES_DIR", "DIALOGUES_DIR", "_FOLD_OFFERS")}
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
        ml._FOLD_OFFERS = ml.MEM_DIR / ".state" / "fold_offers.json"
        self._routes = tr.DIR
        tr.DIR = self.tmp / "memory" / ".state" / "group_context"

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(ml, name, value)
        tr.DIR = self._routes
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ помощь
    def _msg(self, mid: int, text: str, *, chat: str = ROOM, ts: float | None = None):
        return ml.record_message(chat, f"Николай: {text}", actor="Николай",
                                 direction="in", source_id=str(mid),
                                 ts=float(mid) if ts is None else ts,
                                 dedupe_key=f"telegram:{chat}:{mid}:in")

    def _compact(self, rows, *, chat: str = ROOM, summary: str = "старая сводка"):
        return ml._write_compact(
            chat, {"summary": summary, "open_threads": [], "claims": [], "episodes": []},
            tier=1, depth=1, source_events=[r["id"] for r in rows], source_compacts=[],
            event_count=len(rows), continued=False,
            first_ts=min(str(r["ts"]) for r in rows),
            last_ts=max(str(r["ts"]) for r in rows))

    def _edit(self, mid: int, text: str, *, chat: str = ROOM, ts: float = 10_000.0):
        row = ml.record_message(chat, f"Николай: {text}", actor="Николай",
                                direction="in", source_id=f"{mid}:edit:1", ts=ts,
                                dedupe_key=f"telegram:{chat}:{mid}:edit:in")
        ml.note_message_revision(chat, mid, f"Николай: {text}")
        return row

    def _delete(self, mid: int, *, chat: str = ROOM, ts: float = 10_000.0):
        line = f"Telegram [deleted #{mid}]: message removed"
        row = ml.record_message(chat, line, actor="Telegram", direction="in",
                                source_id=f"{mid}:delete", ts=ts,
                                dedupe_key=f"telegram:{chat}:{mid}:delete:in")
        ml.note_message_revision(chat, mid, line)
        return row

    def _hundred(self):
        rows = [self._msg(i, f"реплика {i}") for i in range(1, 101)]
        return rows, self._compact(rows)

    def _evidence(self):
        return memory_provenance.claim_evidence_index(ml.MEM_DIR)


class TestOneEditDoesNotUndoTheWholeFold(Base):
    """Пункт 1 её гейта."""

    def test_ninety_nine_folded_events_stay_folded(self):
        rows, compact = self._hundred()
        self._edit(50, "переписала пятидесятую")

        state = ml.rebuild_state(ROOM)
        hot = [str(r.get("source_id")) for r in state["hot"]]
        self.assertEqual(hot, ["50:edit:1"],
                         "в горячее вернулась не только новая ревизия")

        resolved = memory_provenance.compact_coverage(compact["id"], self._evidence())
        self.assertTrue(resolved["valid"], "покрытие потеряно от одной правки")
        self.assertEqual(len(resolved["leaves"]), 100, "покрыты не все сто событий")
        self.assertEqual(resolved["superseded"], [rows[49]["id"]],
                         "причина названа неточно")

    def test_the_new_revision_is_hot_and_the_old_one_is_not(self):
        rows, _compact = self._hundred()
        new = self._edit(50, "переписала пятидесятую")
        state = ml.rebuild_state(ROOM)
        ids = {str(r.get("id")) for r in state["hot"]}
        self.assertIn(new["id"], ids)
        self.assertNotIn(rows[49]["id"], ids,
                         "старая ревизия обязана остаться свёрнутой")


class TestTheStaleRecapNeverReachesTheFrame(Base):
    """Пункт 2: покрытие — не разрешение показывать."""

    def test_it_is_absent_from_frame_citation_and_search(self):
        _rows, compact = self._hundred()
        self.assertIn(compact["id"], ml.context_summary(ROOM), "исходно свёртка видна")
        self._edit(50, "переписала пятидесятую")

        evidence = self._evidence()
        self.assertFalse(
            memory_provenance.compact_evidence(compact["id"], evidence)["valid"],
            "цитирование ослаблено — этого делать было нельзя")
        self.assertNotIn(compact["id"], ml.context_summary(ROOM),
                         "устаревшая сводка уехала в живой кадр")
        path = ml._compact_dir(ROOM) / f"{compact['id']}.md"
        self.assertTrue(path.exists())
        self.assertFalse(
            memory_fts._compact_markdown_current(path, ml.MEM_DIR, evidence=evidence),
            "устаревшая сводка осталась в обычном поиске")

    def test_a_fresh_compact_is_visible_everywhere(self):
        """Обратная сторона: без правок ничего не пропадает."""
        _rows, compact = self._hundred()
        evidence = self._evidence()
        self.assertTrue(
            memory_provenance.compact_evidence(compact["id"], evidence)["valid"])
        self.assertTrue(
            memory_provenance.compact_coverage(compact["id"], evidence)["valid"])
        self.assertIn(compact["id"], ml.context_summary(ROOM))
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)


class TestDeletionIsBoundedTheSameWay(Base):
    """Пункт 3."""

    def test_deletion_returns_only_its_own_tombstone(self):
        rows, compact = self._hundred()
        self._delete(50)

        state = ml.rebuild_state(ROOM)
        self.assertEqual([str(r.get("source_id")) for r in state["hot"]], ["50:delete"])
        resolved = memory_provenance.compact_coverage(compact["id"], self._evidence())
        self.assertTrue(resolved["valid"])
        self.assertEqual(resolved["superseded"], [rows[49]["id"]])

    def test_the_deleted_body_is_not_in_the_current_context(self):
        rows, _compact = self._hundred()
        self.assertIn("реплика 50", ml.context_summary(ROOM) + str(rows[49]["text"]))
        self._delete(50)
        summary = ml.context_summary(ROOM)
        self.assertNotIn("реплика 50", summary,
                         "тело удалённого сообщения осталось в живом контексте")
        hot_lines = "\n".join(str(r.get("line") or "")
                              for r in ml.rebuild_state(ROOM)["hot"])
        self.assertNotIn("реплика 50", hot_lines)


class TestCoverageStillRejectsABrokenGraph(Base):
    """Пункт 4: ослаблена ровно одна проверка и ровно для одного вопроса."""

    def _coverage_valid(self, compact_id) -> bool:
        return bool(memory_provenance.compact_coverage(
            compact_id, self._evidence())["valid"])

    def test_a_missing_source_event_is_rejected(self):
        rows, _ = self._hundred()
        forged = ml._write_compact(
            ROOM, {"summary": "подделка", "open_threads": [], "claims": [], "episodes": []},
            tier=1, depth=1, source_events=[rows[0]["id"], "evt-которого-нет"],
            source_compacts=[], event_count=2, continued=False,
            first_ts=str(rows[0]["ts"]), last_ts=str(rows[0]["ts"]))
        self.assertFalse(self._coverage_valid(forged["id"]))

    def test_an_event_from_another_place_is_rejected(self):
        rows, _ = self._hundred()
        stranger = self._msg(7001, "чужая комната", chat=OTHER, ts=7001.0)
        forged = ml._write_compact(
            ROOM, {"summary": "чужое", "open_threads": [], "claims": [], "episodes": []},
            tier=1, depth=1, source_events=[rows[0]["id"], stranger["id"]],
            source_compacts=[], event_count=2, continued=False,
            first_ts=min(str(rows[0]["ts"]), str(stranger["ts"])),
            last_ts=max(str(rows[0]["ts"]), str(stranger["ts"])))
        self.assertFalse(self._coverage_valid(forged["id"]),
                         "покрытие приняло событие чужого места")

    def test_a_wrong_event_count_is_rejected(self):
        rows, _ = self._hundred()
        forged = ml._write_compact(
            ROOM, {"summary": "счёт не сходится", "open_threads": [],
                   "claims": [], "episodes": []},
            tier=1, depth=1, source_events=[r["id"] for r in rows[:3]],
            source_compacts=[], event_count=99, continued=False,
            first_ts=str(rows[0]["ts"]), last_ts=str(rows[2]["ts"]))
        self.assertFalse(self._coverage_valid(forged["id"]))

    def test_a_broken_parent_is_rejected(self):
        rows, _ = self._hundred()
        forged = ml._write_compact(
            ROOM, {"summary": "сирота", "open_threads": [], "claims": [], "episodes": []},
            tier=2, depth=2, source_events=[rows[0]["id"]],
            source_compacts=["cmp-которого-нет"], event_count=1, continued=False,
            first_ts=str(rows[0]["ts"]), last_ts=str(rows[0]["ts"]))
        self.assertFalse(self._coverage_valid(forged["id"]))

    def test_a_cycle_is_rejected(self):
        rows, _ = self._hundred()
        first = ml._write_compact(
            ROOM, {"summary": "кольцо А", "open_threads": [], "claims": [], "episodes": []},
            tier=2, depth=2, source_events=[rows[0]["id"]], source_compacts=[],
            event_count=1, continued=False,
            first_ts=str(rows[0]["ts"]), last_ts=str(rows[0]["ts"]))
        second = ml._write_compact(
            ROOM, {"summary": "кольцо Б", "open_threads": [], "claims": [], "episodes": []},
            tier=3, depth=3, source_events=[rows[1]["id"]], source_compacts=[first["id"]],
            event_count=1, continued=False,
            first_ts=str(rows[1]["ts"]), last_ts=str(rows[1]["ts"]))
        path = ml._compact_dir(ROOM) / f"{first['id']}.md"
        # Шапка пишется компактным JSON без пробелов — подменяем ровно её форму.
        head = path.read_text(encoding="utf-8")
        patched = head.replace('"source_compact_ids":[]',
                               f'"source_compact_ids":["{second["id"]}"]')
        self.assertNotEqual(head, patched, "кольцо не собралось: форма шапки другая")
        path.write_text(patched, encoding="utf-8")
        self.assertFalse(self._coverage_valid(first["id"]), "цикл принят покрытием")


class TestRestartAndRepeatedRevisions(Base):
    """Пункт 5."""

    def test_repeated_rebuilds_and_revisions_do_not_grow_hot(self):
        _rows, _compact = self._hundred()
        self.assertEqual(len(ml.rebuild_state(ROOM)["hot"]), 0)
        sizes = []
        for i, mid in enumerate((10, 20, 30), start=1):
            self._edit(mid, f"правка {mid}", ts=20_000.0 + i)
            ml.rebuild_state(ROOM)                       # «рестарт» ещё раз
            sizes.append(len(ml.rebuild_state(ROOM)["hot"]))
        self.assertEqual(sizes, [1, 2, 3],
                         "горячее растёт старым содержимым, а не новыми ревизиями")


class TestStatusNamesThreeNumbers(Base):
    """Пункт 6: covered, presentable, needs_refresh — три разных числа."""

    def test_debt_is_named_with_reason_and_events(self):
        _rows, compact = self._hundred()
        self._edit(50, "переписала")
        debt = ml.refresh_debt(ROOM)
        self.assertEqual(debt["covered"], 1)
        self.assertEqual(debt["presentable"], 0)
        self.assertEqual(debt["needs_refresh"], 1)
        self.assertFalse(debt["sample_truncated"])
        row = debt["sample"][0]
        self.assertEqual(row["compact_id"], compact["id"])
        self.assertEqual(row["reason"], "superseded_source_revision")
        self.assertEqual(row["superseded_total"], 1)

    def test_status_carries_all_three(self):
        self._hundred()
        self._edit(50, "переписала")
        compacts = ml.stream_status(ROOM)["compacts"]
        self.assertEqual(
            {"covered": 1, "presentable": 0, "needs_refresh": 1}, compacts)

    def test_truncation_is_named_not_passed_off_as_exact(self):
        """Нижняя граница не имеет права читаться как точное число — её условие."""
        rows = [self._msg(i, f"реплика {i}") for i in range(1, 60)]
        for i, row in enumerate(rows):
            self._compact([row], summary=f"свёртка {i}")
        for i in range(1, 60):
            self._edit(i, f"правка {i}", ts=30_000.0 + i)
        debt = ml.refresh_debt(ROOM)
        self.assertEqual(debt["needs_refresh"], 59, "число обязано быть точным")
        self.assertEqual(len(debt["sample"]), ml._REFRESH_SAMPLE)
        self.assertTrue(debt["sample_truncated"], "урезание не названо")


class TestCitationWasNotWeakened(Base):
    """Пункт 7."""

    def test_strict_path_rejects_everything_it_rejected_before(self):
        rows, compact = self._hundred()
        evidence = self._evidence()
        self.assertTrue(memory_provenance.compact_evidence(compact["id"], evidence)["valid"])
        self._edit(50, "переписала")
        evidence = self._evidence()
        strict = memory_provenance.compact_evidence(compact["id"], evidence)
        self.assertFalse(strict["valid"])
        self.assertEqual(strict["leaves"], [])

    def test_both_modes_agree_on_a_foreign_place(self):
        rows, _ = self._hundred()
        stranger = self._msg(7001, "чужая комната", chat=OTHER, ts=7001.0)
        forged = ml._write_compact(
            ROOM, {"summary": "чужое", "open_threads": [], "claims": [], "episodes": []},
            tier=1, depth=1, source_events=[stranger["id"]], source_compacts=[],
            event_count=1, continued=False,
            first_ts=str(stranger["ts"]), last_ts=str(stranger["ts"]))
        evidence = self._evidence()
        self.assertFalse(memory_provenance.compact_evidence(forged["id"], evidence)["valid"])
        self.assertFalse(memory_provenance.compact_coverage(forged["id"], evidence)["valid"])


class TestCoverageProvesEveryEvent(unittest.TestCase):
    """Её блокер P0. Публичный вход, без песочницы и без файлов: индекс собран руками.

    Раньше эти проверки никто не писал — их неявно сторожило членство в
    `current_event_ids`, куда попадают только `conversation_message` с телеграмной
    идентичностью. Сняв членство ради покрытия, мы сняли и сторожа: `compact_coverage`
    начал принимать `run_episode`, чужую схему и строку, чей `id` не равен ключу.

    Эти тесты КРАСНЫЕ на c7e5a1f7 и зелёные после узкой правки.
    """

    ROOM = "-100999999999999999"
    # Строка должна быть НАСТОЯЩЕЙ телеграмной: без dedupe_key/meta у неё нет
    # телеграмной идентичности, а без идентичности событие и не может быть
    # вытеснено ревизией — такой случай в жизни не встречается.
    GOOD = {"schema": "praxis.life.event.v1", "id": "evt-1",
            "kind": "conversation_message", "chat_id": ROOM,
            "ts": "2026-08-01T00:00:00Z", "source": "telegram",
            "direction": "in", "source_id": "1",
            "meta": {"is_dm": False, "time_quality": "observed"}}

    def _evidence(self, *rows, current=()):
        # Ключ индекса задаётся ПОЗИЦИЕЙ, а не полем `id` строки: иначе подмена
        # `id` не проверялась бы вовсе — строка находилась бы под своим же именем,
        # и тест был бы вакуумным.
        events = {f"evt-{i + 1}": r for i, r in enumerate(rows)}
        return {
            "memory_dir": ".", "events": events,
            "current_event_ids": set(current),
            "compacts": {"cmp-1": {
                "id": "cmp-1", "chat_id": self.ROOM, "tier": 1, "depth": 1,
                "legacy": False, "degraded": False, "continued": False,
                "event_count": len(rows), "first_ts": "2026-08-01T00:00:00Z",
                "last_ts": "2026-08-01T00:00:00Z",
                "source_event_ids": [f"evt-{i + 1}" for i in range(len(rows))],
                "source_compact_ids": [],
            }},
            "duplicate_events": {}, "duplicate_compacts": {}, "places": {},
        }

    def _both(self, ev):
        return (bool(memory_provenance.compact_evidence("cmp-1", ev)["valid"]),
                bool(memory_provenance.compact_coverage("cmp-1", ev)["valid"]))

    def test_a_healthy_event_is_covered_current_or_not(self):
        strict, cover = self._both(self._evidence(dict(self.GOOD), current=("evt-1",)))
        self.assertEqual((strict, cover), (True, True))
        strict, cover = self._both(self._evidence(dict(self.GOOD)))
        self.assertEqual((strict, cover), (False, True),
                         "покрытие обязано пережить правку сообщения")

    def test_a_foreign_kind_is_refused_by_coverage(self):
        ev = self._evidence(dict(self.GOOD, kind="run_episode"))
        self.assertEqual(self._both(ev), (False, False),
                         "покрытие приняло событие чужого вида")

    def test_a_foreign_schema_is_refused_by_coverage(self):
        ev = self._evidence(dict(self.GOOD, schema="praxis.something.else.v9"))
        self.assertEqual(self._both(ev), (False, False),
                         "покрытие приняло чужую схему")

    def test_a_row_whose_id_does_not_match_the_key_is_refused(self):
        ev = self._evidence(dict(self.GOOD, id="evt-другое"))
        self.assertEqual(self._both(ev), (False, False),
                         "покрытие приняло строку с чужим id")

    def test_a_non_dict_row_is_refused(self):
        self.assertEqual(self._both(self._evidence("не словарь")), (False, False))

    def test_two_revisions_of_one_message_inside_one_compact_are_refused(self):
        """Членство молча гарантировало ещё одно: внутри ОДНОЙ свёртки не могло быть
        двух ревизий одного сообщения. Иначе текущая ревизия оказалась бы «уже
        свёрнутой» — то есть невидимой и негорячей одновременно."""
        first = dict(self.GOOD, id="evt-1", source_id="41",
                     meta={"is_dm": False, "time_quality": "observed"})
        second = dict(self.GOOD, id="evt-2", source_id="41:edit:1",
                      meta={"is_dm": False, "time_quality": "observed"})
        ev = self._evidence(first, second, current=("evt-2",))
        self.assertEqual(self._both(ev), (False, False),
                         "две ревизии одного сообщения в одной свёртке приняты")

    def test_a_legitimate_history_across_compacts_is_not_refused(self):
        """Обратная сторона: старая ревизия в одной свёртке и новая в другой — это
        история, а не подделка. Запрет действует ТОЛЬКО внутри одной свёртки."""
        old = dict(self.GOOD, id="evt-1", source_id="41",
                   meta={"is_dm": False, "time_quality": "observed"})
        new = dict(self.GOOD, id="evt-2", source_id="41:edit:1",
                   meta={"is_dm": False, "time_quality": "observed"})
        ev = self._evidence(old, current=("evt-2",))
        ev["events"]["evt-2"] = new
        ev["compacts"]["cmp-2"] = dict(ev["compacts"]["cmp-1"], id="cmp-2",
                                       source_event_ids=["evt-2"], event_count=1)
        self.assertTrue(
            memory_provenance.compact_coverage("cmp-1", ev)["valid"])
        self.assertTrue(
            memory_provenance.compact_coverage("cmp-2", ev)["valid"])


class TestNestedStaleParent(Base):
    """Пробел, названный ею: валидный ярусный родитель над устаревшим ребёнком."""

    def _tree(self):
        kids, rows = [], []
        for k in range(4):
            batch = [self._msg(k * 10 + i, f"ветка {k} реплика {i}")
                     for i in range(1, 11)]
            rows.extend(batch)
            kids.append(self._compact(batch, summary=f"ребёнок {k}"))
        parent = ml._write_compact(
            ROOM, {"summary": "родитель", "open_threads": [], "claims": [], "episodes": []},
            tier=2, depth=2, source_events=[], source_compacts=[k["id"] for k in kids],
            event_count=40, continued=False,
            first_ts=min(str(r["ts"]) for r in rows),
            last_ts=max(str(r["ts"]) for r in rows))
        return rows, kids, parent

    def test_a_parent_over_a_stale_child_covers_but_is_not_shown(self):
        rows, kids, parent = self._tree()
        ev = self._evidence()
        self.assertTrue(memory_provenance.compact_evidence(parent["id"], ev)["valid"])
        self._delete(5)
        ev = self._evidence()
        cover = memory_provenance.compact_coverage(parent["id"], ev)
        self.assertTrue(cover["valid"], "родитель потерял покрытие от правки внука")
        self.assertEqual(cover["superseded"], [rows[4]["id"]],
                         "устаревшее событие не всплыло к родителю")
        self.assertFalse(memory_provenance.compact_evidence(parent["id"], ev)["valid"])
        self.assertNotIn(parent["id"], ml.context_summary(ROOM))
        state = ml.rebuild_state(ROOM)
        self.assertEqual([str(r.get("source_id")) for r in state["hot"]], ["5:delete"],
                         "устаревший родитель вернул в горячее чужие сорок событий")


class TestManyRevisionsOfOneMessage(Base):
    """Пробел, названный ею: несколько ревизий одного сообщения подряд."""

    def test_edit_edit_delete_keeps_exactly_one_row_hot(self):
        rows, compact = self._hundred()
        seen = []
        for step, action in enumerate((lambda: self._edit(50, "первая правка", ts=20_001.0),
                                       lambda: self._edit(50, "вторая правка", ts=20_002.0),
                                       lambda: self._delete(50, ts=20_003.0)), start=1):
            action()
            state = ml.rebuild_state(ROOM)
            seen.append(len(state["hot"]))
            cover = memory_provenance.compact_coverage(compact["id"], self._evidence())
            self.assertTrue(cover["valid"], f"шаг {step}: покрытие потеряно")
            self.assertEqual(len(cover["leaves"]), 100, f"шаг {step}: покрыто не всё")
        self.assertEqual(seen, [1, 1, 1],
                         "каждая новая ревизия обязана вытеснять предыдущую, а не копиться")


class TestTheDeletedBodyIsNotInsideTheRecap(Base):
    """Прежний тест был вакуумным: он проходил и на заведомо дырявой сборке.

    Теперь тело сообщения лежит ДОСЛОВНО внутри recap свёртки, и проверяется
    присутствие ДО и отсутствие ПОСЛЕ — то есть сам текст, а не имя компакта."""

    BODY = "секретная строка про пароль от котельной"

    def test_the_body_lives_in_the_recap_and_leaves_with_the_deletion(self):
        rows = [self._msg(i, f"реплика {i}") for i in range(1, 21)]
        rows.append(self._msg(50, self.BODY))
        self._compact(rows, summary=f"Николай сказал: «{self.BODY}» — на этом стояло всё")
        before = ml.context_summary(ROOM)
        self.assertIn(self.BODY, before,
                      "тело не попало в recap — тест снова стал бы вакуумным")
        self._delete(50)
        after = ml.context_summary(ROOM)
        self.assertNotIn(self.BODY, after, "тело удалённого осталось в живом кадре")
        hot_lines = "\n".join(str(r.get("line") or "")
                               for r in ml.rebuild_state(ROOM)["hot"])
        self.assertNotIn(self.BODY, hot_lines)
        path = ml._compact_dir(ROOM) / f"{ml.refresh_debt(ROOM)['sample'][0]['compact_id']}.md"
        self.assertIn(self.BODY, path.read_text(encoding="utf-8"),
                      "аудит обязан сохранить свёртку целиком — гасится показ, не файл")


class TestTheDebtIsHonestlyDescribed(Base):
    """Опись долга: свежие первыми, оба усечения названы, числа точные."""

    def test_the_sample_is_newest_first_and_says_so(self):
        rows = [self._msg(i, f"реплика {i}") for i in range(1, 6)]
        made = [self._compact([row], summary=f"свёртка {i}") for i, row in enumerate(rows)]
        for i in range(1, 6):
            self._edit(i, f"правка {i}", ts=30_000.0 + i)
        debt = ml.refresh_debt(ROOM)
        self.assertEqual(debt["sample_order"], "newest_first")
        self.assertEqual(debt["needs_refresh"], 5)
        newest = max(item["id"] for item in made)
        self.assertEqual(debt["sample"][0]["compact_id"], newest,
                         "в описи снова двадцать самых древних долгов")

    def test_superseded_ids_are_truncated_with_an_exact_total(self):
        rows = [self._msg(i, f"реплика {i}") for i in range(1, 41)]
        self._compact(rows)
        for i in range(1, 41):
            self._edit(i, f"правка {i}", ts=40_000.0 + i)
        row = ml.refresh_debt(ROOM)["sample"][0]
        self.assertEqual(row["superseded_total"], 40, "точное число обязано быть точным")
        self.assertTrue(row["superseded_truncated"], "усечение перечня не названо")
        self.assertEqual(len(row["superseded_event_ids"]), ml._REFRESH_SAMPLE)


class _AuthoredRefreshMixin:
    """Drive real source selection and real acting-author commits, never a model alias."""

    def _selection(self, compact_id="", *, limit=50):
        selected = ml.prepare_authored_fold(
            ROOM, compact_id=compact_id, limit=limit)
        self.assertTrue(selected["ok"], selected)
        self.assertEqual(selected["kind"], "refresh", selected)
        self.assertTrue(selected["shown_complete"], selected)
        self.assertTrue(selected["sources"], selected)
        ids = [row["id"] for row in selected["sources"]]
        self.assertEqual(len(ids), len(set(ids)), selected)
        return selected

    def _apply_selection(self, selected, text):
        ids = [row["id"] for row in selected["sources"]]
        out = ml.apply_authored_fold(ROOM, selected["selection"], text)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["source_ids"], ids, out)
        self.assertEqual(out["folded"], len(ids), out)
        self.assertEqual(ml.compact_text(out["compact_id"], ROOM), text.strip())
        evidence = self._evidence()
        self.assertTrue(
            memory_provenance.compact_evidence(
                out["compact_id"], evidence)["valid"],
            out)
        return out

    def _author_refresh(self, compact_id="", text="fresh", *, limit=50):
        selected = self._selection(compact_id, limit=limit)
        return selected, self._apply_selection(selected, text)

    def _refresh_artifacts(self):
        return (
            {path.name: path.read_bytes()
             for path in ml._compact_dir(ROOM).glob("*.md")},
            list(ml.iter_events(
                chat_id=ROOM, kinds={"memory_compact_refresh"})),
        )


class TestAddressedCompactRefresh(_AuthoredRefreshMixin, Base):
    """The next layer: immutable stale audit, bounded current-only replacement."""


    def test_edit_refresh_uses_latest_body_and_keeps_old_bytes(self):
        rows = [self._msg(i, f"исходная реплика {i}") for i in range(1, 7)]
        compact = self._compact(rows, summary="STALE RECAP исходная реплика 3")
        path = ml._compact_dir(ROOM) / f"{compact['id']}.md"
        before = path.read_bytes()
        latest = self._edit(3, "ТОЛЬКО НОВАЯ РЕДАКЦИЯ", ts=60_001.0)

        selected, out = self._author_refresh(
            compact["id"], "сводка с ТОЛЬКО НОВОЙ РЕДАКЦИЕЙ")
        shown = "\n".join(row["text"] for row in selected["sources"])

        self.assertEqual(out["remaining_count"], 0, out)
        self.assertIn("ТОЛЬКО НОВАЯ РЕДАКЦИЯ", shown)
        self.assertNotIn("исходная реплика 3", shown)
        self.assertEqual(
            [row["id"] for row in selected["sources"]].count(latest["id"]), 1)
        self.assertEqual(path.read_bytes(), before,
                         "old compact Markdown was rewritten")
        frame = ml.context_summary(ROOM)
        self.assertIn("сводка с ТОЛЬКО НОВОЙ РЕДАКЦИЕЙ", frame)
        self.assertNotIn("STALE RECAP", frame)
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)

    def test_delete_never_sends_or_presents_the_old_body(self):
        body = "УДАЛЁННОЕ ТЕЛО НЕ ДОЛЖНО ВЕРНУТЬСЯ"
        rows = [self._msg(1, body), self._msg(2, "остаётся")]
        compact = self._compact(rows, summary=f"STALE RECAP {body}")
        old_path = ml._compact_dir(ROOM) / f"{compact['id']}.md"
        before = old_path.read_bytes()
        self._delete(1, ts=61_001.0)

        selected, out = self._author_refresh(
            compact["id"], "сводка после tombstone")
        shown = "\n".join(row["text"] for row in selected["sources"])

        self.assertIn("[deleted #1]", shown)
        self.assertNotIn(body, shown)
        self.assertEqual(out["remaining_count"], 0, out)
        self.assertNotIn(body, ml.context_summary(ROOM))
        self.assertEqual(old_path.read_bytes(), before)
        self.assertFalse(memory_fts._compact_markdown_current(
            old_path, ml.MEM_DIR, evidence=self._evidence()))

    def test_nested_parent_claims_stale_child_and_never_models_stale_recaps(self):
        _rows, kids, parent = TestNestedStaleParent._tree(self)
        self._edit(5, "новая пятая", ts=62_001.0)

        selected, out = self._author_refresh(parent["id"], "fresh nested")
        shown = "\n".join(row["text"] for row in selected["sources"])

        self.assertEqual(out["remaining_count"], 0, out)
        self.assertNotIn("родитель", shown)
        self.assertNotIn("ребёнок", shown)
        self.assertIn("новая пятая", shown)
        receipts = list(ml.iter_events(
            chat_id=ROOM, kinds={"memory_compact_refresh"}))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(
            receipts[0]["meta"]["stale_compact_ids"],
            [parent["id"], kids[0]["id"]])
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)
        self.assertNotIn("родитель", ml.context_summary(ROOM))

    def test_many_revisions_project_latest_exactly_once(self):
        rows = [self._msg(1, "original"), self._msg(2, "stable")]
        compact = self._compact(rows, summary="stale original")
        for revision, text, stamp in (
                (1, "first edit", 63_001.0),
                (2, "LATEST EDIT", 63_003.0),
                (3, "late older", 63_002.0)):
            ml.record_message(
                ROOM, f"Николай: {text}", actor="Николай", direction="in",
                source_id=f"1:edit:{revision}", ts=stamp,
                revision_order=revision,
                dedupe_key=f"telegram:{ROOM}:1:edit:{revision}:in")
        ml.note_message_revision(ROOM, 1, "Николай: LATEST EDIT")

        selected, _out = self._author_refresh(compact["id"], "latest recap")
        shown = "\n".join(row["text"] for row in selected["sources"])

        self.assertEqual(shown.count("LATEST EDIT"), 1)
        self.assertNotIn("original", shown)
        self.assertNotIn("first edit", shown)
        self.assertNotIn("late older", shown)

    def test_required_model_aborts_before_any_replacement_or_receipt(self):
        """The removed auxiliary writer refuses; it must not invoke a model."""
        rows = [self._msg(i, "x" * 80 + f" row {i}") for i in range(1, 5)]
        stale = self._compact(rows, summary="degraded stale chunked")
        self._edit(1, "y" * 80 + " latest", ts=63_901.0)
        before = self._refresh_artifacts()
        original = ml._model_compact
        ml._model_compact = lambda *_a, **_kw: self.fail(
            "removed auxiliary author was invoked")
        try:
            for target in (stale["id"], None):
                with self.subTest(target=target):
                    out = ml.refresh_compacts(ROOM, target, max_chunks=2)
                    self.assertFalse(out["ok"], out)
                    self.assertEqual(out["reason"], "acting_author_required", out)
                    self.assertEqual(out["chunks_written"], 0, out)
        finally:
            ml._model_compact = original
        self.assertEqual(self._refresh_artifacts(), before)
        self.assertGreater(ml.refresh_debt(ROOM)["needs_refresh"], 0)

    def test_addressed_degraded_model_result_is_not_committed(self):
        """No fallback recap: an author must supply valid text for its selection."""
        rows = [self._msg(1, "old"), self._msg(2, "stable")]
        stale = self._compact(rows, summary="degraded stale")
        self._edit(1, "new", ts=63_951.0)
        selected = self._selection(stale["id"])
        before = self._refresh_artifacts()
        original = ml._model_compact
        ml._model_compact = lambda *_a, **_kw: self.fail(
            "invalid author text fell back to an auxiliary writer")
        try:
            for text, reason in (
                    ("", "text_and_selection_required"),
                    ("## reserved section", "recap_h2_reserved_use_h3")):
                with self.subTest(text=text):
                    out = ml.apply_authored_fold(
                        ROOM, selected["selection"], text)
                    self.assertFalse(out["ok"], out)
                    self.assertEqual(out["reason"], reason, out)
                    self.assertEqual(out["folded"], 0, out)
                    self.assertEqual(self._refresh_artifacts(), before)
        finally:
            ml._model_compact = original
        self.assertGreater(ml.refresh_debt(ROOM)["needs_refresh"], 0)

    def test_one_chunk_makes_monotonic_progress_then_completion_is_idempotent(self):
        rows = [self._msg(i, "x" * 80 + f" row {i}") for i in range(1, 5)]
        compact = self._compact(rows, summary="stale chunked")
        self._edit(1, "y" * 80 + " latest", ts=64_001.0)
        remaining = []
        selected_ids = []

        for number in range(len(rows)):
            selected = self._selection(compact["id"], limit=1)
            self.assertEqual(len(selected["sources"]), 1, selected)
            before = self._refresh_artifacts()
            out = self._apply_selection(selected, f"bounded chunk {number}")
            after = self._refresh_artifacts()
            self.assertEqual(len(after[0]) - len(before[0]), 1)
            self.assertEqual(len(after[1]) - len(before[1]), 1)
            remaining.append(out["remaining_count"])
            selected_ids.extend(out["source_ids"])
            if out["remaining_count"] == 0:
                break

        self.assertEqual(remaining, [3, 2, 1, 0])
        self.assertEqual(len(selected_ids), len(set(selected_ids)))
        at_completion = self._refresh_artifacts()
        again = ml.prepare_authored_fold(
            ROOM, compact_id=compact["id"], limit=1)
        self.assertTrue(again["ok"], again)
        self.assertEqual(again["reason"], "already_refreshed", again)
        self.assertEqual(again["sources"], [], again)
        self.assertNotIn("selection", again)
        self.assertEqual(self._refresh_artifacts(), at_completion)

    def test_later_overlap_cannot_regress_durable_partial_progress(self):
        old = [self._msg(i, f"old {i}") for i in range(1, 6)]
        stale = self._compact(old, summary="stale all five")
        latest = [self._edit(i, f"new {i}", ts=64_100.0 + i)
                  for i in range(1, 6)]
        left = self._compact(latest[:2], summary="current positions 0,1")
        right = self._compact(latest[2:4], summary="current positions 2,3")
        before = ml._refresh_snapshot(ROOM)
        group_id = before["artifact_group"][stale["id"]]
        group = before["groups"][group_id]
        replacements, covered = ml._refresh_choose_replacements(group, before)
        self.assertEqual(replacements, [left["id"], right["id"]])
        self.assertEqual(covered, {0, 1, 2, 3})
        receipt = ml._append_refresh_receipt(ROOM, group, replacements)
        partial = ml._refresh_snapshot(ROOM)["groups"][group_id]
        self.assertEqual(partial["refreshed_count"], 4)
        self.assertEqual(partial["remaining_count"], 1)

        crossing = self._compact(
            latest[:3], summary="later overlapping positions 0,1,2")
        after = ml._refresh_snapshot(ROOM)["groups"][group_id]

        self.assertEqual(after["receipt"]["receipt_event_id"], receipt["id"])
        self.assertEqual(after["receipt"]["refreshed_count"], 4)
        self.assertEqual(after["replacement_compact_ids"], [left["id"], right["id"]])
        self.assertNotIn(crossing["id"], after["replacement_compact_ids"])
        self.assertEqual(after["refreshed_count"], 4)
        self.assertEqual(after["remaining_count"], 1)
        selected = self._selection(stale["id"], limit=1)
        self.assertEqual(
            [row["id"] for row in selected["sources"]], [latest[4]["id"]])
        before_receipts = len(list(ml.iter_events(
            chat_id=ROOM, kinds={"memory_compact_refresh"})))
        out = self._apply_selection(selected, "current position 4")
        completed = ml._refresh_snapshot(ROOM)["groups"][group_id]

        self.assertEqual(out["folded"], 1, out)
        self.assertEqual(out["remaining_count"], 0, out)
        self.assertEqual(len(list(ml.iter_events(
            chat_id=ROOM, kinds={"memory_compact_refresh"}))),
            before_receipts + 1)
        self.assertTrue(completed["receipt_complete"], completed)
        self.assertEqual(completed["remaining_count"], 0)
        self.assertEqual(
            completed["replacement_compact_ids"][:2],
            [left["id"], right["id"]])
        self.assertNotIn(crossing["id"], completed["replacement_compact_ids"])
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)

    def test_orphan_strict_replacement_is_reconciled_without_model(self):
        rows = [self._msg(1, "old"), self._msg(2, "stable")]
        stale = self._compact(rows, summary="old recap")
        latest = self._edit(1, "new", ts=65_001.0)
        orphan = self._compact([latest, rows[1]], summary="orphan current recap")
        before = self._refresh_artifacts()
        self.assertEqual(len(before[1]), 0)
        original = ml._model_compact
        ml._model_compact = lambda *_a, **_kw: self.fail(
            "reconciliation invoked an auxiliary author")
        try:
            out = ml.prepare_authored_fold(
                ROOM, compact_id=stale["id"], limit=1)
        finally:
            ml._model_compact = original

        self.assertTrue(out["ok"], out)
        self.assertEqual(out["reason"], "refresh_reconciled", out)
        self.assertEqual(out["sources"], [], out)
        self.assertNotIn("selection", out)
        after = self._refresh_artifacts()
        self.assertEqual(after[0], before[0])
        self.assertEqual(len(after[1]), 1)
        self.assertEqual(
            after[1][0]["meta"]["replacement_compact_ids"], [orphan["id"]])
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)

    def test_wrong_digest_receipt_cannot_clear_debt(self):
        rows = [self._msg(1, "old"), self._msg(2, "stable")]
        stale = self._compact(rows, summary="old recap")
        latest = self._edit(1, "new", ts=66_001.0)
        replacement = self._compact([latest, rows[1]], summary="current recap")
        snapshot = ml._refresh_snapshot(ROOM)
        group = snapshot["groups"][snapshot["artifact_group"][stale["id"]]]
        ml.append_event(
            "memory_compact_refresh", chat_id=ROOM,
            text=f"Compact refresh group: {group['group_id']}", source="memory_life",
            source_id=group["group_id"], refs=group["stale_ids"] + [replacement["id"]],
            meta={
                "schema": ml._COMPACT_REFRESH_SCHEMA,
                "group_id": group["group_id"],
                "coverage_root_ids": group["root_ids"],
                "stale_compact_ids": group["stale_ids"],
                "logical_target_count": len(group["logical_ids"]),
                "logical_target_ids_sha256": ml._refresh_digest(group["logical_ids"]),
                "target_event_count": len(group["target_ids"]),
                "target_event_ids_sha256": "0" * 64,
                "replacement_compact_ids": [replacement["id"]],
            })
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 1)

    def test_new_edit_after_completion_reopens_debt(self):
        rows = [self._msg(1, "old"), self._msg(2, "stable")]
        stale = self._compact(rows, summary="old recap")
        self._edit(1, "new", ts=67_001.0)
        self._author_refresh(stale["id"], "fresh")
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)

        self._edit(2, "newer again", ts=67_002.0)
        debt = ml.refresh_debt(ROOM)
        self.assertGreater(debt["needs_refresh"], 0)
        self.assertGreater(debt["sample"][0]["remaining_count"], 0)

    def test_unknown_target_does_not_fall_back_to_another_group(self):
        row = self._msg(1, "old")
        stale = self._compact([row], summary="old recap")
        self._edit(1, "new", ts=67_101.0)
        before = self._refresh_artifacts()

        out = ml.prepare_authored_fold(
            ROOM, compact_id="cmp-does-not-exist", limit=1)

        self.assertFalse(out["ok"], out)
        self.assertEqual(out["reason"], "no_refresh_group_for_target", out)
        self.assertEqual(out["sources"], [], out)
        self.assertNotIn("selection", out)
        self.assertEqual(self._refresh_artifacts(), before)
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 1)
        self.assertEqual(
            ml.refresh_debt(ROOM)["sample"][0]["compact_id"], stale["id"])


class TestAddressedRefreshAdversarial(_AuthoredRefreshMixin, Base):
    """Measured regressions from the rejected first implementation."""


    def test_target_change_during_evaluator_never_claims_success(self):
        """A revision between source selection and authored apply invalidates it."""
        rows = [self._msg(1, "old"), self._msg(2, "stable")]
        stale = self._compact(rows, summary="stale old")
        self._edit(1, "new", ts=70_001.0)
        selected = self._selection(stale["id"])
        self._edit(2, "changed during authoring", ts=70_002.0)
        before = self._refresh_artifacts()

        out = ml.apply_authored_fold(
            ROOM, selected["selection"], "obsolete authored plan")

        self.assertFalse(out["ok"], out)
        self.assertEqual(out["reason"], "state_changed", out)
        self.assertEqual(out["folded"], 0, out)
        self.assertEqual(out["retained_text"], "obsolete authored plan", out)
        self.assertEqual(self._refresh_artifacts(), before)
        self.assertGreater(ml.refresh_debt(ROOM)["needs_refresh"], 0)

    def test_blocked_evaluator_does_not_block_unrelated_append(self):
        """The acting author can think between prepare/apply without a held lock."""
        rows = [self._msg(1, "old"), self._msg(2, "stable")]
        stale = self._compact(rows, summary="stale old")
        self._edit(1, "new", ts=71_001.0)
        entered = threading.Event()
        release = threading.Event()
        appended = threading.Event()
        errors = []
        outputs = {}

        def author():
            try:
                selected = self._selection(stale["id"])
                entered.set()
                if not release.wait(10.0):
                    raise TimeoutError("acting author was not released")
                outputs["apply"] = ml.apply_authored_fold(
                    ROOM, selected["selection"], "fresh")
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        def append_other():
            try:
                self._msg(99, "unrelated", chat=OTHER, ts=71_002.0)
                appended.set()
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        author_thread = threading.Thread(target=author)
        append_thread = threading.Thread(target=append_other)
        try:
            author_thread.start()
            self.assertTrue(entered.wait(5.0))
            append_thread.start()
            self.assertTrue(
                appended.wait(1.0),
                "source preparation retained a lock during authoring")
        finally:
            release.set()
            for thread in (author_thread, append_thread):
                if thread.ident is not None:
                    thread.join(35.0)
        self.assertFalse(author_thread.is_alive())
        self.assertFalse(append_thread.is_alive())
        self.assertFalse(errors, errors)
        self.assertTrue(outputs["apply"]["ok"], outputs)

    def test_slow_hot_compaction_does_not_block_same_place_cross_process_append(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires fork to inherit the isolated memory fixture")

        for mid in range(1, 7):
            self._msg(mid, f"row {mid}", ts=float(mid * 10_000))

        ctx = multiprocessing.get_context("fork")
        prepared = ctx.Event()
        release = ctx.Event()
        child_out = ctx.Queue()
        self.addCleanup(child_out.close)

        def compact_child():
            try:
                selected = ml.prepare_authored_fold(ROOM, limit=1)
                if not selected.get("ok") or selected.get("kind") != "hot":
                    raise AssertionError(selected)
                if not selected.get("sources"):
                    raise AssertionError("hot selection was empty")
                # Authoring happens after prepare returns, outside its transaction.
                prepared.set()
                if not release.wait(10.0):
                    raise TimeoutError("writer did not release the acting author")
                applied = ml.apply_authored_fold(
                    ROOM, selected["selection"], "Authored slow hot compact")
                child_out.put(("ok", {
                    "source_ids": [row["id"] for row in selected["sources"]],
                    "applied": applied,
                }))
            except Exception as exc:  # pragma: no cover - asserted below
                child_out.put(("error", repr(exc)))

        writer_done = threading.Event()
        errors = []
        written = {}

        def write_same_place():
            try:
                written["row"] = self._msg(
                    99, "during slow compact", ts=99_000.0)
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                writer_done.set()

        process = ctx.Process(target=compact_child)
        writer = None
        process.start()
        try:
            self.assertTrue(prepared.wait(5.0),
                            "child never finished authored selection")
            writer = threading.Thread(target=write_same_place)
            writer.start()
            self.assertTrue(
                writer_done.wait(1.0),
                "authoring retained the same-place cross-process transaction")
        finally:
            release.set()
            if writer is not None:
                writer.join(35.0)
            process.join(15.0)
            if process.is_alive():
                process.terminate()
                process.join(5.0)

        self.assertIsNotNone(writer)
        self.assertFalse(writer.is_alive(), "same-place writer did not settle")
        self.assertFalse(errors, errors)
        self.assertEqual(process.exitcode, 0)
        status, payload = child_out.get(timeout=3.0)
        self.assertEqual(status, "ok", payload)
        applied = payload["applied"]
        self.assertTrue(applied["ok"], applied)
        self.assertEqual(applied["source_ids"], payload["source_ids"])
        self.assertEqual(applied["folded"], len(payload["source_ids"]))
        self.assertNotIn(written["row"]["id"], applied["source_ids"])

        event_ids = {
            row["id"] for row in ml.iter_events(
                chat_id=ROOM, kinds={"conversation_message"})
        }
        self.assertIn(written["row"]["id"], event_ids)
        # Check both the persisted cursor and an independent reconstruction.
        for state in (ml._load_state(ROOM), ml.rebuild_state(ROOM)):
            hot_ids = {row["id"] for row in state["hot"]}
            self.assertIn(written["row"]["id"], hot_ids)
            self.assertTrue(set(payload["source_ids"]).isdisjoint(hot_ids))
        self.assertEqual(
            ml.compact_text(applied["compact_id"], ROOM),
            "Authored slow hot compact")

    def test_hot_compaction_and_refresh_share_one_commit_domain(self):
        old = self._msg(1, "old")
        latest = self._edit(1, "new", ts=99_100.0)
        self._msg(2, "stable tail", ts=99_200.0)

        # No stale compact yet: the real selector can choose the latest hot row.
        hot = ml.prepare_authored_fold(ROOM, limit=1)
        self.assertTrue(hot["ok"], hot)
        self.assertEqual(hot["kind"], "hot", hot)
        self.assertEqual([row["id"] for row in hot["sources"]], [latest["id"]])
        stale = self._compact([old], summary="stale old")
        refresh = ml.prepare_authored_fold(ROOM, compact_id=stale["id"])
        self.assertTrue(refresh["ok"], refresh)
        self.assertEqual(refresh["kind"], "refresh", refresh)
        self.assertEqual(
            [row["id"] for row in refresh["sources"]], [latest["id"]])

        refresh_adopted = threading.Event()
        allow_refresh_commit = threading.Event()
        hot_in_commit = threading.Event()
        release_hot = threading.Event()
        refresh_attempted = threading.Event()
        refresh_done = threading.Event()
        outputs = {}
        errors = []
        original_adopt = ml.adopt_place
        original_guard = ml._state_write_guard
        original_write = ml._write_compact

        def staged_adopt(chat):
            place = original_adopt(chat)
            if threading.current_thread().name == "refresh-thread":
                # Preserve real adoption, but stage the contender immediately
                # before apply reaches its own state transaction.
                refresh_adopted.set()
                if not allow_refresh_commit.wait(10.0):
                    raise TimeoutError("hot author never entered its commit")
            return place

        @contextlib.contextmanager
        def observed_guard(chat):
            if (threading.current_thread().name == "refresh-thread"
                    and refresh_adopted.is_set()):
                refresh_attempted.set()
            with original_guard(chat):
                yield

        def paused_write(*args, **kwargs):
            if threading.current_thread().name == "compact-thread":
                hot_in_commit.set()
                if not release_hot.wait(10.0):
                    raise TimeoutError("refresh never attempted its commit")
            return original_write(*args, **kwargs)

        def run_refresh():
            try:
                outputs["refresh"] = ml.apply_authored_fold(
                    ROOM, refresh["selection"], "obsolete refresh")
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                refresh_done.set()

        def run_hot():
            try:
                outputs["compact"] = ml.apply_authored_fold(
                    ROOM, hot["selection"], "ordinary hot compact")
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        refresh_thread = threading.Thread(
            target=run_refresh, name="refresh-thread")
        compact_thread = threading.Thread(
            target=run_hot, name="compact-thread")
        ml.adopt_place = staged_adopt
        ml._state_write_guard = observed_guard
        ml._write_compact = paused_write
        try:
            refresh_thread.start()
            self.assertTrue(refresh_adopted.wait(5.0),
                            "refresh never completed real place adoption")
            compact_thread.start()
            self.assertTrue(hot_in_commit.wait(5.0),
                            "hot author never entered the real compact writer")
            allow_refresh_commit.set()
            self.assertTrue(refresh_attempted.wait(5.0),
                            "refresh never attempted its commit state guard")
            self.assertFalse(
                refresh_done.wait(0.3),
                "refresh bypassed the hot author's state commit domain")
        finally:
            allow_refresh_commit.set()
            release_hot.set()
            for thread in (refresh_thread, compact_thread):
                if thread.ident is not None:
                    thread.join(35.0)
            ml.adopt_place = original_adopt
            ml._state_write_guard = original_guard
            ml._write_compact = original_write

        self.assertFalse(refresh_thread.is_alive())
        self.assertFalse(compact_thread.is_alive())
        self.assertFalse(errors, errors)
        self.assertTrue(outputs["compact"]["ok"], outputs)
        self.assertEqual(outputs["compact"]["folded"], 1, outputs)
        self.assertEqual(
            outputs["compact"]["source_ids"], [latest["id"]], outputs)
        refused = outputs["refresh"]
        self.assertFalse(refused["ok"], outputs)
        self.assertEqual(refused["reason"], "state_changed", outputs)
        self.assertEqual(refused["conflict"]["kind"], "changed_refresh", outputs)
        self.assertEqual(refused["folded"], 0, outputs)
        self.assertEqual(refused["retained_text"], "obsolete refresh", outputs)
        self.assertEqual(len(list(ml.iter_events(
            chat_id=ROOM, kinds={"memory_compact_refresh"}))), 0)

        # A losing apply is not silently replanned. A separate prepare may
        # reconcile the already-covered target, without writing another recap.
        reconciled = ml.prepare_authored_fold(
            ROOM, compact_id=stale["id"])
        self.assertTrue(reconciled["ok"], reconciled)
        self.assertEqual(reconciled["reason"], "refresh_reconciled", reconciled)
        self.assertEqual(reconciled["sources"], [], reconciled)
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)
        self.assertEqual(len(list(ml.iter_events(
            chat_id=ROOM, kinds={"memory_compact_refresh"}))), 1)

        strict = [
            meta for meta, _recap in ml._canonical_compact_graph(ROOM)[0].values()
            if meta.get("source_event_ids") == [latest["id"]]
        ]
        self.assertEqual(len(strict), 1, strict)
        frame = ml.context_summary(ROOM)
        self.assertEqual(frame.count("ordinary hot compact"), 1, frame)
        self.assertNotIn("obsolete refresh", frame)

    def test_hot_compaction_and_refresh_share_cross_process_commit_domain(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires fork to inherit the isolated memory fixture")

        old = self._msg(1, "old")
        latest = self._edit(1, "new", ts=99_300.0)
        self._msg(2, "stable tail", ts=99_400.0)
        hot = ml.prepare_authored_fold(ROOM, limit=1)
        self.assertTrue(hot["ok"], hot)
        self.assertEqual(hot["kind"], "hot", hot)
        self.assertEqual([row["id"] for row in hot["sources"]], [latest["id"]])
        stale = self._compact([old], summary="stale old")
        refresh = ml.prepare_authored_fold(ROOM, compact_id=stale["id"])
        self.assertTrue(refresh["ok"], refresh)
        self.assertEqual(refresh["kind"], "refresh", refresh)
        self.assertEqual(
            [row["id"] for row in refresh["sources"]], [latest["id"]])

        ctx = multiprocessing.get_context("fork")
        compact_adopted = ctx.Event()
        allow_compact_commit = ctx.Event()
        refresh_in_commit = ctx.Event()
        release_refresh = ctx.Event()
        compact_attempted = ctx.Event()
        compact_done = ctx.Event()
        child_out = ctx.Queue()
        self.addCleanup(child_out.close)

        def refresh_child():
            original_write = ml._write_compact

            def paused_write(*args, **kwargs):
                # apply has already acquired its actual state commit guard.
                refresh_in_commit.set()
                if not release_refresh.wait(10.0):
                    raise TimeoutError("hot author never attempted its commit")
                return original_write(*args, **kwargs)

            ml._write_compact = paused_write
            try:
                result = ml.apply_authored_fold(
                    ROOM, refresh["selection"], "cross-process refresh")
                child_out.put(("refresh", "ok", result))
            except Exception as exc:  # pragma: no cover - asserted below
                child_out.put(("refresh", "error", repr(exc)))
            finally:
                ml._write_compact = original_write

        def compact_child():
            original_adopt = ml.adopt_place
            original_guard = ml._state_write_guard

            def staged_adopt(chat):
                place = original_adopt(chat)
                compact_adopted.set()
                if not allow_compact_commit.wait(10.0):
                    raise TimeoutError("refresh never entered its commit")
                return place

            @contextlib.contextmanager
            def observed_guard(chat):
                if compact_adopted.is_set():
                    compact_attempted.set()
                with original_guard(chat):
                    yield

            ml.adopt_place = staged_adopt
            ml._state_write_guard = observed_guard
            try:
                result = ml.apply_authored_fold(
                    ROOM, hot["selection"], "cross-process hot compact")
                child_out.put(("compact", "ok", result))
            except Exception as exc:  # pragma: no cover - asserted below
                child_out.put(("compact", "error", repr(exc)))
            finally:
                compact_done.set()
                ml.adopt_place = original_adopt
                ml._state_write_guard = original_guard

        compact_process = ctx.Process(target=compact_child)
        refresh_process = ctx.Process(target=refresh_child)
        compact_process.start()
        try:
            self.assertTrue(compact_adopted.wait(5.0),
                            "hot author never completed real place adoption")
            refresh_process.start()
            self.assertTrue(refresh_in_commit.wait(5.0),
                            "refresh never entered the real compact writer")
            allow_compact_commit.set()
            self.assertTrue(compact_attempted.wait(5.0),
                            "hot author never attempted its commit state guard")
            self.assertFalse(
                compact_done.wait(0.3),
                "hot author bypassed refresh's cross-process commit domain")
        finally:
            allow_compact_commit.set()
            release_refresh.set()
            for process in (refresh_process, compact_process):
                if process.pid is not None:
                    process.join(15.0)
                    if process.is_alive():
                        process.terminate()
                        process.join(5.0)

        self.assertEqual(refresh_process.exitcode, 0)
        self.assertEqual(compact_process.exitcode, 0)
        results = {}
        for _ in range(2):
            name, status, payload = child_out.get(timeout=3.0)
            self.assertEqual(status, "ok", payload)
            results[name] = payload

        self.assertTrue(results["refresh"]["ok"], results)
        self.assertEqual(results["refresh"]["folded"], 1, results)
        self.assertEqual(
            results["refresh"]["source_ids"], [latest["id"]], results)
        self.assertEqual(results["refresh"]["remaining_count"], 0, results)
        refused = results["compact"]
        self.assertFalse(refused["ok"], results)
        self.assertEqual(refused["reason"], "state_changed", results)
        self.assertEqual(refused["conflict"]["kind"], "gone", results)
        self.assertEqual(refused["folded"], 0, results)
        self.assertEqual(
            refused["retained_text"], "cross-process hot compact", results)

        strict = [
            meta for meta, _recap in ml._canonical_compact_graph(ROOM)[0].values()
            if meta.get("source_event_ids") == [latest["id"]]
        ]
        self.assertEqual(len(strict), 1, strict)
        frame = ml.context_summary(ROOM)
        self.assertEqual(frame.count("cross-process refresh"), 1, frame)
        self.assertNotIn("cross-process hot compact", frame)
        self.assertEqual(len(list(ml.iter_events(
            chat_id=ROOM, kinds={"memory_compact_refresh"}))), 1)
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)

    def test_same_place_waiter_does_not_convoy_unrelated_place(self):
        self._msg(1, "seed room")
        self._msg(1, "seed other", chat=OTHER)
        holder_ready = threading.Event()
        release = threading.Event()
        same_attempted = threading.Event()
        same_done = threading.Event()
        other_done = threading.Event()
        errors = []
        original_save = ml._save_state
        original_guard = ml._state_write_guard

        def paused_save(state):
            if threading.current_thread().name == "room-holder" and not holder_ready.is_set():
                holder_ready.set()
                if not release.wait(3.0):
                    raise TimeoutError("room holder was not released")
            return original_save(state)

        @contextlib.contextmanager
        def observed_guard(chat):
            if threading.current_thread().name == "same-place-writer":
                same_attempted.set()
            with original_guard(chat):
                yield

        def holder():
            try:
                ml.rebuild_state(ROOM)
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        def writer(chat, done, text):
            try:
                ml.record_message(chat, text, actor="Николай", direction="in",
                                  source_id="2", dedupe_key=f"telegram:{chat}:2:in")
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                done.set()

        ml._save_state = paused_save
        ml._state_write_guard = observed_guard
        try:
            holder_thread = threading.Thread(target=holder, name="room-holder")
            holder_thread.start()
            self.assertTrue(holder_ready.wait(1.0))
            same_thread = threading.Thread(
                target=writer, args=(ROOM, same_done, "Николай: same"),
                name="same-place-writer")
            same_thread.start()
            self.assertTrue(same_attempted.wait(1.0))
            other_thread = threading.Thread(
                target=writer, args=(OTHER, other_done, "Николай: other"),
                name="other-place-writer")
            other_thread.start()
            self.assertTrue(other_done.wait(0.8),
                            "same-place waiter retained the global append lock")
            self.assertFalse(same_done.is_set())
            release.set()
            for thread in (holder_thread, same_thread, other_thread):
                thread.join(5.0)
        finally:
            release.set()
            ml._save_state = original_save
            ml._state_write_guard = original_guard
        self.assertFalse(errors, errors)

    def test_repeated_revisions_collapse_historical_roots(self):
        row = self._msg(1, "v0")
        current = self._compact([row], summary="v0")
        original_paths = {}

        for revision in range(1, 6):
            old_path = ml._compact_dir(ROOM) / f"{current['id']}.md"
            original_paths[old_path] = old_path.read_bytes()
            latest = ml.record_message(
                ROOM, f"Николай: v{revision}", actor="Николай", direction="in",
                source_id=f"1:edit:{revision}", ts=72_000.0 + revision,
                revision_order=revision,
                dedupe_key=f"telegram:{ROOM}:1:edit:{revision}:in")
            ml.note_message_revision(ROOM, 1, f"Николай: v{revision}")

            selected, out = self._author_refresh(
                current["id"], f"current {revision}")
            self.assertEqual(
                [source["id"] for source in selected["sources"]],
                [latest["id"]])
            self.assertEqual(out["remaining_count"], 0, out)
            self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)
            current = {"id": out["compact_id"]}

        receipts = list(ml.iter_events(
            chat_id=ROOM, kinds={"memory_compact_refresh"}))
        self.assertEqual(len(receipts), 5)
        for path, original_bytes in original_paths.items():
            self.assertEqual(path.read_bytes(), original_bytes)

    def test_presentable_superset_is_reused_without_model(self):
        rows = [self._msg(i, f"row {i}") for i in range(1, 4)]
        stale = self._compact(rows[:2], summary="old A B")
        latest = self._edit(1, "new A", ts=73_001.0)
        strict = ml._write_compact(
            ROOM, {"summary": "strict A B C", "open_threads": [],
                   "claims": [], "episodes": []},
            tier=1, depth=1,
            source_events=[latest["id"], rows[1]["id"], rows[2]["id"]],
            source_compacts=[], event_count=3, continued=False,
            first_ts=min(rows[1]["ts"], rows[2]["ts"], latest["ts"]),
            last_ts=max(rows[1]["ts"], rows[2]["ts"], latest["ts"]))
        before = self._refresh_artifacts()
        original = ml._model_compact
        ml._model_compact = lambda *_a, **_kw: self.fail(
            "auxiliary author called despite strict superset")
        try:
            out = ml.prepare_authored_fold(
                ROOM, compact_id=stale["id"], limit=1)
        finally:
            ml._model_compact = original

        self.assertTrue(out["ok"], out)
        self.assertEqual(out["reason"], "refresh_reconciled", out)
        self.assertEqual(out["sources"], [], out)
        after = self._refresh_artifacts()
        self.assertEqual(after[0], before[0])
        self.assertEqual(len(after[1]) - len(before[1]), 1)
        self.assertIn(
            strict["id"], after[1][-1]["meta"]["replacement_compact_ids"])
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)

    def test_addressless_result_names_remaining_room_debt(self):
        for mid in range(1, 4):
            row = self._msg(mid, f"old {mid}")
            self._compact([row], summary=f"old {mid}")
            self._edit(mid, f"new {mid}", ts=74_000.0 + mid)
        before = ml.refresh_debt(ROOM)
        self.assertEqual(before["unresolved_group_count"], 3)

        selected, out = self._author_refresh(text="one selected group", limit=1)

        self.assertEqual(len(selected["sources"]), 1, selected)
        self.assertEqual(out["remaining_count"], 0, out)
        after = ml.refresh_debt(ROOM)
        self.assertEqual(after["unresolved_group_count"], 2, after)
        self.assertEqual(after["needs_refresh"], 2, after)

    def test_many_chunks_append_at_most_one_receipt(self):
        """One authored apply writes one recap and at most one refresh receipt."""
        rows = [self._msg(i, "x" * 80 + str(i)) for i in range(1, 6)]
        stale = self._compact(rows, summary="stale")
        self._edit(1, "y" * 80, ts=75_001.0)
        selected = self._selection(stale["id"], limit=100)
        self.assertEqual(len(selected["sources"]), 5, selected)
        before = self._refresh_artifacts()

        out = self._apply_selection(selected, "one authored recap of five rows")

        after = self._refresh_artifacts()
        self.assertEqual(out["folded"], 5, out)
        self.assertEqual(out["remaining_count"], 0, out)
        self.assertEqual(len(after[0]) - len(before[0]), 1)
        self.assertEqual(len(after[1]) - len(before[1]), 1)
        self.assertEqual(
            after[1][-1]["meta"]["replacement_compact_ids"],
            [out["compact_id"]])


    def test_three_hundred_roots_addressed_snapshot_is_linear_by_counts(self):
        roots = []
        for mid in range(1, 301):
            row = ml.append_event(
                "conversation_message", chat_id=ROOM, actor="Николай",
                direction="in", text=f"Николай: old {mid}", source="telegram",
                source_id=str(mid), ts=80_000.0 + mid,
                dedupe_key=f"telegram:{ROOM}:{mid}:in",
                meta={"time_quality": "observed"})
            roots.append(self._compact([row], summary=f"old {mid}"))
            ml.append_event(
                "conversation_message", chat_id=ROOM, actor="Николай",
                direction="in", text=f"Николай: new {mid}", source="telegram",
                source_id=f"{mid}:edit:1", ts=81_000.0 + mid,
                dedupe_key=f"telegram:{ROOM}:{mid}:edit:1:in",
                meta={"time_quality": "observed", "revision_order": 1})
        counts = {"logical": 0, "receipt": 0}
        originals = (ml._refresh_logical_id, ml._refresh_validate_receipt)

        def logical(row):
            counts["logical"] += 1
            return originals[0](row)

        def receipt(row, group, snapshot):
            counts["receipt"] += 1
            return originals[1](row, group, snapshot)

        ml._refresh_logical_id, ml._refresh_validate_receipt = logical, receipt
        try:
            selected = ml.prepare_authored_fold(
                ROOM, compact_id=roots[-1]["id"], limit=1)
        finally:
            ml._refresh_logical_id, ml._refresh_validate_receipt = originals

        self.assertTrue(selected["ok"], selected)
        self.assertEqual(selected["kind"], "refresh", selected)
        self.assertTrue(selected["shown_complete"], selected)
        self.assertEqual(len(selected["sources"]), 1, selected)
        self.assertLessEqual(counts["logical"], 10 * 300 + 20, counts)
        self.assertEqual(counts["receipt"], 0, counts)
        self.assertEqual(len(list(ml._compact_dir(ROOM).glob("*.md"))), 300)


    def test_future_partial_receipt_cannot_override_complete_receipt(self):
        rows = [self._msg(1, "old"), self._msg(2, "stable")]
        stale = self._compact(rows, summary="old recap")
        latest = self._edit(1, "new", ts=82_001.0)
        _selected, done = self._author_refresh(
            stale["id"], "complete authored replacement")
        self.assertTrue(done["ok"], done)
        self.assertEqual(done["remaining_count"], 0, done)
        partial = self._compact([latest], summary="partial current")
        snapshot = ml._refresh_snapshot(ROOM)
        group = snapshot["groups"][snapshot["artifact_group"][stale["id"]]]
        ml.append_event(
            "memory_compact_refresh", chat_id=ROOM,
            text=f"Compact refresh group: {group['group_id']}", source="memory_life",
            source_id=group["group_id"], refs=group["stale_ids"] + [partial["id"]],
            ts=9_999_999_999.0,
            meta={
                "schema": ml._COMPACT_REFRESH_SCHEMA,
                "group_id": group["group_id"],
                "coverage_root_ids": group["root_ids"],
                "stale_compact_ids": group["stale_ids"],
                "logical_target_count": len(group["logical_ids"]),
                "logical_target_ids_sha256": ml._refresh_digest(group["logical_ids"]),
                "target_event_count": len(group["target_ids"]),
                "target_event_ids_sha256": ml._refresh_digest(group["target_ids"]),
                "replacement_compact_ids": [partial["id"]],
            })
        debt = ml.refresh_debt(ROOM)
        self.assertEqual(debt["needs_refresh"], 0, debt)
        selected = ml._refresh_snapshot(ROOM)["groups"][group["group_id"]]["receipt"]
        self.assertEqual(selected["remaining_count"], 0, selected)

    def test_reversed_replacement_order_cannot_clear_debt(self):
        rows = [self._msg(1, "old A"), self._msg(2, "old B")]
        stale = self._compact(rows, summary="old A B")
        latest_a = self._edit(1, "new A", ts=82_101.0)
        latest_b = self._edit(2, "new B", ts=82_102.0)
        replacement_a = self._compact([latest_a], summary="current A")
        replacement_b = self._compact([latest_b], summary="current B")
        snapshot = ml._refresh_snapshot(ROOM)
        group = snapshot["groups"][snapshot["artifact_group"][stale["id"]]]
        canonical, covered = ml._refresh_choose_replacements(
            group, snapshot, [replacement_a["id"], replacement_b["id"]])
        self.assertEqual(canonical, [replacement_a["id"], replacement_b["id"]])
        self.assertEqual(len(covered), 2)
        reversed_ids = list(reversed(canonical))
        ml.append_event(
            "memory_compact_refresh", chat_id=ROOM,
            text=f"Compact refresh group: {group['group_id']}", source="memory_life",
            source_id=group["group_id"], refs=group["stale_ids"] + reversed_ids,
            meta={
                "schema": ml._COMPACT_REFRESH_SCHEMA,
                "group_id": group["group_id"],
                "coverage_root_ids": group["root_ids"],
                "stale_compact_ids": group["stale_ids"],
                "logical_target_count": len(group["logical_ids"]),
                "logical_target_ids_sha256": ml._refresh_digest(group["logical_ids"]),
                "target_event_count": len(group["target_ids"]),
                "target_event_ids_sha256": ml._refresh_digest(group["target_ids"]),
                "replacement_compact_ids": reversed_ids,
            })
        refreshed = ml._refresh_snapshot(ROOM)
        self.assertEqual(refreshed["groups"][group["group_id"]]["receipt"], {})
        self.assertGreater(ml.refresh_debt(ROOM)["needs_refresh"], 0)

    def test_alternate_sorted_partition_cannot_clear_debt(self):
        rows = [self._msg(1, "old A"), self._msg(2, "old B")]
        stale = self._compact(rows, summary="old A B")
        latest_a = self._edit(1, "new A", ts=82_201.0)
        latest_b = self._edit(2, "new B", ts=82_202.0)
        whole = self._compact([latest_a, latest_b], summary="current A B")
        replacement_a = self._compact([latest_a], summary="current A")
        replacement_b = self._compact([latest_b], summary="current B")
        snapshot = ml._refresh_snapshot(ROOM)
        group = snapshot["groups"][snapshot["artifact_group"][stale["id"]]]
        canonical, covered = ml._refresh_choose_replacements(group, snapshot)
        self.assertEqual(canonical, [whole["id"]])
        self.assertEqual(len(covered), 2)
        alternative = [replacement_a["id"], replacement_b["id"]]
        ml.append_event(
            "memory_compact_refresh", chat_id=ROOM,
            text=f"Compact refresh group: {group['group_id']}", source="memory_life",
            source_id=group["group_id"], refs=group["stale_ids"] + alternative,
            meta={
                "schema": ml._COMPACT_REFRESH_SCHEMA,
                "group_id": group["group_id"],
                "coverage_root_ids": group["root_ids"],
                "stale_compact_ids": group["stale_ids"],
                "logical_target_count": len(group["logical_ids"]),
                "logical_target_ids_sha256": ml._refresh_digest(group["logical_ids"]),
                "target_event_count": len(group["target_ids"]),
                "target_event_ids_sha256": ml._refresh_digest(group["target_ids"]),
                "replacement_compact_ids": alternative,
            })
        refreshed = ml._refresh_snapshot(ROOM)
        self.assertEqual(refreshed["groups"][group["group_id"]]["receipt"], {})
        self.assertGreater(ml.refresh_debt(ROOM)["needs_refresh"], 0)

    def test_later_better_compact_does_not_invalidate_completed_receipt(self):
        rows = [self._msg(1, "old A"), self._msg(2, "old B")]
        stale = self._compact(rows, summary="old A B")
        latest_a = self._edit(1, "new A", ts=82_301.0)
        latest_b = self._edit(2, "new B", ts=82_302.0)
        replacement_a = self._compact([latest_a], summary="current A")
        replacement_b = self._compact([latest_b], summary="current B")
        snapshot = ml._refresh_snapshot(ROOM)
        group = snapshot["groups"][snapshot["artifact_group"][stale["id"]]]
        canonical, covered = ml._refresh_choose_replacements(group, snapshot)
        self.assertEqual(canonical, [replacement_a["id"], replacement_b["id"]])
        self.assertEqual(len(covered), 2)
        receipt = ml._append_refresh_receipt(ROOM, group, canonical)
        before = ml._refresh_snapshot(ROOM)["groups"][group["group_id"]]
        self.assertTrue(before["receipt_complete"], before)
        later_whole = self._compact([latest_a, latest_b], summary="later current A B")
        after = ml._refresh_snapshot(ROOM)["groups"][group["group_id"]]
        self.assertTrue(after["receipt_complete"], after)
        self.assertEqual(after["receipt"]["receipt_event_id"], receipt["id"])
        self.assertEqual(after["receipt"]["replacement_compact_ids"], canonical)
        self.assertNotIn(later_whole["id"], after["receipt"]["replacement_compact_ids"])
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)

    def test_retry_reconciles_state_after_crash_post_receipt(self):
        rows = [self._msg(1, "old")]
        stale = self._compact(rows, summary="old recap")
        latest = self._edit(1, "new", ts=83_001.0)
        selected = self._selection(stale["id"])
        original_append = ml._append_refresh_receipt
        crashed = []

        def crash_after_receipt(chat, group, replacement_ids):
            receipt = original_append(chat, group, replacement_ids)
            crashed.append(receipt["id"])
            raise RuntimeError("simulated crash after durable receipt")

        ml._append_refresh_receipt = crash_after_receipt
        try:
            with self.assertRaisesRegex(
                    RuntimeError, "simulated crash after durable receipt"):
                ml.apply_authored_fold(
                    ROOM, selected["selection"], "durable authored recap")
        finally:
            ml._append_refresh_receipt = original_append

        self.assertEqual(len(crashed), 1)
        self.assertIn(latest["id"], {
            row["id"] for row in ml._load_state(ROOM)["hot"]})
        before_retry = self._refresh_artifacts()
        self.assertEqual(len(before_retry[0]), 2)
        self.assertEqual(len(before_retry[1]), 1)

        retry = ml.prepare_authored_fold(ROOM, compact_id=stale["id"])

        self.assertTrue(retry["ok"], retry)
        self.assertEqual(retry["reason"], "already_refreshed", retry)
        self.assertEqual(retry["sources"], [], retry)
        self.assertEqual(self._refresh_artifacts(), before_retry)
        self.assertNotIn(latest["id"], {
            row["id"] for row in ml._load_state(ROOM)["hot"]})
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)

    def test_retry_reconciliation_cannot_overwrite_newer_revision_state(self):
        old = self._msg(1, "old", ts=100.0)
        stale = self._compact([old], summary="stale old")
        rev1 = ml.record_message(
            ROOM, "Николай: rev1", actor="Николай", direction="in",
            source_id="1:edit:1", ts=200.0, revision_order=1,
            dedupe_key=f"telegram:{ROOM}:1:edit:1:in")
        ml.note_message_revision(ROOM, 1, "Николай: rev1")
        _selected, first = self._author_refresh(stale["id"], "rev1 recap")
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["remaining_count"], 0, first)
        self.assertNotIn(rev1["id"], {
            row.get("id") for row in ml._load_state(ROOM, rebuild=False)["hot"]})

        entered = threading.Event()
        attempted = threading.Event()
        release = threading.Event()
        retry_done = threading.Event()
        writer_done = threading.Event()
        errors = []
        outputs = {}
        original_save = ml._save_state
        original_guard = ml._state_write_guard

        def paused_save(state):
            if threading.current_thread().name == "retry-thread" and not entered.is_set():
                entered.set()
                if not release.wait(3.0):
                    raise TimeoutError("writer did not test the state guard")
            return original_save(state)

        @contextlib.contextmanager
        def observed_guard(chat):
            if threading.current_thread().name == "writer-thread":
                attempted.set()
            with original_guard(chat):
                yield

        def retry():
            try:
                outputs["retry"] = ml.prepare_authored_fold(
                    ROOM, compact_id=stale["id"])
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                retry_done.set()

        def write_rev2():
            try:
                outputs["rev2"] = ml.record_message(
                    ROOM, "Николай: rev2", actor="Николай", direction="in",
                    source_id="1:edit:2", ts=300.0, revision_order=2,
                    dedupe_key=f"telegram:{ROOM}:1:edit:2:in")
                ml.note_message_revision(ROOM, 1, "Николай: rev2")
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                writer_done.set()

        ml._save_state = paused_save
        ml._state_write_guard = observed_guard
        try:
            retry_thread = threading.Thread(target=retry, name="retry-thread")
            retry_thread.start()
            self.assertTrue(entered.wait(1.0))
            writer_thread = threading.Thread(target=write_rev2, name="writer-thread")
            writer_thread.start()
            self.assertTrue(attempted.wait(1.0), "writer never attempted the state guard")
            self.assertFalse(writer_done.wait(0.2),
                             "new revision bypassed the reconciliation state guard")
            release.set()
            retry_thread.join(5.0)
            writer_thread.join(5.0)
        finally:
            release.set()
            ml._save_state = original_save
            ml._state_write_guard = original_guard
        self.assertTrue(retry_done.is_set())
        self.assertTrue(writer_done.is_set())
        self.assertFalse(errors, errors)
        self.assertEqual(
            outputs["retry"]["reason"], "already_refreshed", outputs)
        self.assertEqual(outputs["retry"]["sources"], [], outputs)
        final = ml._load_state(ROOM, rebuild=False)
        self.assertIn(outputs["rev2"]["id"], {row.get("id") for row in final["hot"]})
        debt = ml.refresh_debt(ROOM)
        self.assertGreater(debt["needs_refresh"], 0, debt)

    def test_non_writer_shaped_receipt_cannot_clear_debt(self):
        rows = [self._msg(1, "old")]
        stale = self._compact(rows, summary="old recap")
        latest = self._edit(1, "new", ts=84_001.0)
        replacement = self._compact([latest], summary="current")
        snapshot = ml._refresh_snapshot(ROOM)
        group = snapshot["groups"][snapshot["artifact_group"][stale["id"]]]
        ml.append_event(
            "memory_compact_refresh", chat_id=ROOM,
            text=f"Compact refresh group: {group['group_id']}", source="memory_life",
            source_id=group["group_id"], refs=group["stale_ids"] + [replacement["id"]],
            salience=1, dedupe_key="forged-non-writer-shape",
            meta={
                "schema": ml._COMPACT_REFRESH_SCHEMA,
                "group_id": group["group_id"],
                "coverage_root_ids": group["root_ids"],
                "stale_compact_ids": group["stale_ids"],
                "logical_target_count": len(group["logical_ids"]),
                "logical_target_ids_sha256": ml._refresh_digest(group["logical_ids"]),
                "target_event_count": len(group["target_ids"]),
                "target_event_ids_sha256": ml._refresh_digest(group["target_ids"]),
                "replacement_compact_ids": [replacement["id"]],
            })
        refreshed = ml._refresh_snapshot(ROOM)
        self.assertEqual(refreshed["groups"][group["group_id"]]["receipt"], {})
        self.assertGreater(ml.refresh_debt(ROOM)["needs_refresh"], 0)

    def test_concurrent_same_group_commits_only_one_compact_and_receipt(self):
        row = self._msg(1, "old")
        stale = self._compact([row], summary="old recap")
        self._edit(1, "new", ts=85_001.0)
        # Both authors actually receive the source before either commits.
        selections = [self._selection(stale["id"]) for _ in range(2)]
        self.assertEqual(
            selections[0]["selection"], selections[1]["selection"])
        entered = threading.Barrier(2)
        outputs = []
        errors = []

        def run(index):
            text = f"NEW worker-{index}"
            try:
                entered.wait(5.0)
                out = ml.apply_authored_fold(
                    ROOM, selections[index]["selection"], text)
                outputs.append((text, out))
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        before = self._refresh_artifacts()
        threads = [
            threading.Thread(target=run, args=(index,), name=f"worker-{index}")
            for index in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(35.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertFalse(errors, errors)
        self.assertEqual(len(outputs), 2)
        self.assertEqual(sorted(out["folded"] for _text, out in outputs), [0, 1])
        winners = [(text, out) for text, out in outputs if out["ok"]]
        losers = [(text, out) for text, out in outputs if not out["ok"]]
        self.assertEqual(len(winners), 1, outputs)
        self.assertEqual(len(losers), 1, outputs)
        loser_text, refused = losers[0]
        self.assertEqual(refused["reason"], "state_changed", refused)
        self.assertEqual(refused["retained_text"], loser_text, refused)

        after = self._refresh_artifacts()
        self.assertEqual(len(after[0]) - len(before[0]), 1)
        self.assertEqual(len(after[1]) - len(before[1]), 1)
        frame = ml.context_summary(ROOM)
        self.assertEqual(frame.count("NEW worker-"), 1, frame)
        self.assertIn(winners[0][0], frame)
        self.assertNotIn(loser_text, frame)
        self.assertEqual(ml.refresh_debt(ROOM)["needs_refresh"], 0)


class TestOneSnapshotPerAnswer(Base):
    """Её блокер P1: один снимок индекса и один разбор файлов на ответ."""

    def _count(self, call):
        counts = {"index": 0, "parse": 0, "filter": 0}
        originals = (memory_provenance.claim_evidence_index,
                     ml._parse_place_compacts, ml._filter_compact_graph)

        def wrap(key, orig):
            def inner(*a, **kw):
                counts[key] += 1
                return orig(*a, **kw)
            return inner

        memory_provenance.claim_evidence_index = wrap("index", originals[0])
        ml._parse_place_compacts = wrap("parse", originals[1])
        ml._filter_compact_graph = wrap("filter", originals[2])
        try:
            call()
        finally:
            (memory_provenance.claim_evidence_index, ml._parse_place_compacts,
             ml._filter_compact_graph) = originals
        return counts

    def test_status_builds_the_index_once(self):
        rows = [self._msg(i, f"реплика {i}") for i in range(1, 31)]
        self._compact(rows)
        self._edit(5, "правка", ts=50_001.0)
        ml.rebuild_state(ROOM)
        counts = self._count(lambda: ml.stream_status(ROOM))
        self.assertEqual(counts["index"], 1,
                         f"глобальный индекс строится больше одного раза: {counts}")
        self.assertEqual(counts["parse"], 1,
                         f"файлы компактов разбираются больше одного раза: {counts}")
        self.assertEqual(counts["filter"], 2, counts)

    def test_aggregate_status_never_enters_exact_resolvers(self):
        rows = [self._msg(i, f"реплика {i}") for i in range(1, 31)]
        self._compact(rows)
        self._edit(5, "правка", ts=50_001.0)
        ml.rebuild_state(ROOM)

        originals = (memory_provenance.claim_evidence_index,
                     ml._parse_place_compacts, ml._filter_compact_graph,
                     memory_provenance.compact_evidence,
                     memory_provenance.compact_coverage)

        def forbidden(name):
            def inner(*_args, **_kwargs):
                raise AssertionError(f"aggregate status entered {name}")
            return inner

        (memory_provenance.claim_evidence_index,
         ml._parse_place_compacts,
         ml._filter_compact_graph,
         memory_provenance.compact_evidence,
         memory_provenance.compact_coverage) = tuple(
             forbidden(name) for name in
             ("index", "parse", "filter", "strict resolver", "coverage resolver")
         )
        try:
            out = ml.status()
        finally:
            (memory_provenance.claim_evidence_index,
             ml._parse_place_compacts,
             ml._filter_compact_graph,
             memory_provenance.compact_evidence,
             memory_provenance.compact_coverage) = originals

        stream = next(item for item in out["streams"] if item["chat_id"] == ROOM)
        self.assertEqual(stream["hot"], 1)
        self.assertEqual(stream["frontier"], 0)
        self.assertNotIn("compacts", stream,
                         "aggregate panel must not pretend its saved cursor is exact debt")

    def test_the_two_graphs_come_from_one_snapshot(self):
        rows = [self._msg(i, f"реплика {i}") for i in range(1, 21)]
        self._compact(rows)
        counts = self._count(lambda: ml.rebuild_state(ROOM))
        self.assertEqual(counts["index"], 1, counts)
        self.assertEqual(counts["parse"], 1, counts)



class TestTierRequiresActingAuthor(Base):
    """Pressure never invents an upper-tier author; exact source recaps are selectable."""

    def _eight_tier1(self):
        children = []
        for n in range(ml.TIER_HI):
            rows = [self._msg(100 * n + i, f"source {n}-{i}") for i in (1, 2)]
            children.append(self._compact(rows, summary=f"complete recap {n}"))
        ml.rebuild_state(ROOM)
        return children

    def test_pressure_cannot_write_a_fallback_parent(self):
        self._eight_tier1()
        before = {p: p.read_bytes() for p in ml._compact_dir(ROOM).glob("*.md")}
        original = ml._model_compact
        ml._model_compact = lambda *a, **k: self.fail("auxiliary author called")
        try:
            self.assertEqual(ml._fold_tiers_transactional(ROOM), ([], []))
            out = ml.compact_if_due(ROOM, force=True)
        finally:
            ml._model_compact = original
        self.assertTrue(out["author_required"], out)
        self.assertEqual(out["folded"], 0)
        self.assertEqual({p: p.read_bytes() for p in ml._compact_dir(ROOM).glob("*.md")}, before)

    def test_real_tier_selection_has_complete_sources_and_valid_parent(self):
        children = self._eight_tier1()
        selected = ml.prepare_authored_fold(ROOM, limit=2)
        self.assertEqual(selected["kind"], "tier", selected)
        self.assertEqual(len(selected["sources"]), 2)
        ids = [r["id"] for r in selected["sources"]]
        for row in selected["sources"]:
            self.assertIn(ml.compact_text(row["id"], ROOM), row["text"])
        out = ml.apply_authored_fold(ROOM, selected["selection"], "My synthesis of two complete recaps.")
        self.assertTrue(out["ok"], out)
        evidence = self._evidence()
        parent = evidence["compacts"][out["compact_id"]]
        self.assertEqual(parent["source_compact_ids"], ids)
        self.assertEqual(parent["event_count"], 4)
        self.assertEqual(parent["depth"], 2)
        self.assertTrue(memory_provenance.compact_coverage(out["compact_id"], evidence)["valid"])
        chosen = [c for c in children if c["id"] in ids]
        self.assertEqual(parent["first_ts"], min(c["first_ts"] for c in chosen))
        self.assertEqual(parent["last_ts"], max(c["last_ts"] for c in chosen))

    def test_drain_refuses_without_writing(self):
        import compact_places as cp
        self._eight_tier1()
        before = {p: p.read_bytes() for p in ml._compact_dir(ROOM).glob("*.md")}
        out = cp.drain(ROOM, limit=5)
        self.assertEqual(out["stopped"], "acting_author_required", out)
        self.assertEqual(out["compacts"], 0)
        self.assertEqual({p: p.read_bytes() for p in ml._compact_dir(ROOM).glob("*.md")}, before)


class TestRefreshBindsBranchKeyBeforeWriting(Base):
    """Регрессия пробки Ouroboros 21.09: мельница писала чанки, которые сама не принимала.

    Правка сообщения может приехать под веточным ключом места (…__topic__N): текущей
    ревизией становится событие с chat_id=ветка, ещё не привязанной в places.json.
    refresh_compacts пишет новый чанк от имени корневого ключа, и без bind_place ДО
    _write_compact провенанс не признаёт ветку родной: свежий чанк невалиден с первой
    секунды, presentable пуст, refreshed_count не растёт, needs_refresh стоит
    (группа e0b1e8ed: 75 целей, 0 refreshed за 10 проходов).
    """

    def test_refresh_chunk_with_branch_revision_becomes_presentable(self):
        root, branch = f"{ROOM}", f"{ROOM}__topic__947"
        rows = [self._msg(1, "первая реплика"), self._msg(2, "вторая реплика")]
        stale = self._compact(rows, summary="старая сводка")
        # Правка приезжает через топик: текущая ревизия несёт веточный ключ,
        # message id пир-широкий — lineage тот же, событие вытесняет исходное.
        ml.record_message(branch, "Николай: новая редакция", actor="Николай",
                          direction="in", source_id="1:edit:1", ts=99_100.0,
                          dedupe_key=f"telegram:{branch}:1:in")
        ml.note_message_revision(branch, 1, "Николай: новая редакция")

        debt = ml.refresh_debt(root)
        self.assertGreaterEqual(debt["needs_refresh"], 1, debt)
        self.assertNotIn(branch, ml.bindings())

        selected = ml.prepare_authored_fold(root, compact_id=stale["id"])
        self.assertTrue(selected["shown_complete"], selected)
        result = ml.apply_authored_fold(root, selected["selection"], "свежий refresh")

        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(result["folded"], 1, result)
        self.assertIn(branch, ml.bindings(),
                      "refresh не привязал веточный ключ места")
        made = [result["compact_id"]]
        self.assertTrue(made, result)
        evidence = self._evidence()
        for compact_id in made:
            resolved = memory_provenance.compact_evidence(compact_id, evidence)
            self.assertTrue(resolved["valid"],
                            f"свежий чанк {compact_id} невалиден: {resolved}")
        after = ml.refresh_debt(root)
        self.assertEqual(after["needs_refresh"], 0, after)


if __name__ == "__main__":
    unittest.main()
