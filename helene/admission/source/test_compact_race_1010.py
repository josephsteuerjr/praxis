"""Гонка свёртки против входящих: план → модель → commit (10.10).

Живой случай (runner.log, Самара): 15:31:56 начат manual fold группы,
15:32:01 модель ответила HTTP 200, 15:32:52 — ok:false / folded:0 / state_changed;
повтор 15:33:24 → 15:34:13 свёрнуто 158. Между планом и commit в чат пришло
новое сообщение — и результат модельного вызова был выброшен целиком,
а владельцу выставлена расписка «нажми ещё раз».

Здесь — детерминистичный стенд: пауза ВНУТРИ подменённого `_model_compact`,
мутация ленты в паузе, сравнение. Матрица: чистый append в хвост; событие
с ts ВНУТРИ префикса (бэклог/перенос времени); правка в префиксе; конкурирующая
свёртка; отсутствие ответа модели; обрыв между компактом и сохранением
состояния; дважды конфликт. Отдельно — единица сверки `_prefix_conflict`
(идентичность строк вместо позиционного среза dict-ов) и расписки `_fold_run`.

Запуск:  python praxis_test.py test_compact_race_1010 -v
"""

from __future__ import annotations

import ast
import contextlib
import datetime as dt
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import memory_life as ml

from test_coverage_vs_current import Base, ROOM

#: Живой размер свёртки из инцидента — 158 событий — здесь уменьшен до
#: управляемого окна: важна форма гонки, не масса ленты.
N_ROWS = 24          # сообщений в кольце до свёртки
GAP_AT = 6           # перед этим индексом разрыв эпизода → план свернёт ровно 6
EPISODE_JUMP = 7200  # секунд: больше EPISODE_GAP_SEC (45 мин по умолчанию)


def _seed_ts(i: int) -> float:
    return 1_000_000.0 + 120.0 * i + (EPISODE_JUMP if i >= GAP_AT else 0.0)


class RaceBase(Base):
    """Кольцо + управляемая пауза внутри модельного вызова."""

    def seed(self, n: int = N_ROWS) -> list[str]:
        for i in range(n):
            self._msg(i, f"сообщение {i}", ts=_seed_ts(i))
        return [str(r["id"]) for r in ml._load_state(ROOM)["hot"]]

    def prepare(self, *, limit: int = 50) -> dict:
        prepared = ml.prepare_authored_fold(ROOM, limit=limit)
        self.assertTrue(prepared.get("ok"), prepared)
        self.assertEqual(prepared.get("kind"), "hot", prepared)
        self.assertTrue(prepared.get("shown_complete"), prepared)
        self.assertTrue(prepared.get("sources"), prepared)
        self.assertRegex(prepared.get("selection", ""), r"^[0-9a-f]{64}$")
        return prepared

    @staticmethod
    def selected_ids(prepared: dict) -> list[str]:
        return [str(row["id"]) for row in prepared["sources"]]

    def assert_conflict(self, result: dict, text: str):
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("reason"), "state_changed", result)
        self.assertEqual(result.get("folded"), 0, result)
        self.assertTrue(result.get("conflict"), result)
        self.assertEqual(result.get("retained_text"), text, result)
        self.assertFalse(result.get("retried"), result)

    def assert_coverage(self, ids):
        counts = self.coverage_counts()
        self.assertEqual(set(counts), set(ids), counts)
        self.assertTrue(all(count == 1 for count in counts.values()), counts)


    # ------------------------------------------------------------- помощь
    def hot_ids(self) -> set[str]:
        return {str(r["id"]) for r in ml._load_state(ROOM)["hot"]}

    def coverage_counts(self) -> dict[str, int]:
        """Сколько раз каждый id покрыт компактом места. Компакт — .md с шапкой
        `<!-- praxis-compact: {meta} -->` (формат не менять: свёртки 921ad192 и
        др. читаются тем же парсером)."""
        import re as _re
        counts: dict[str, int] = {}
        meta_re = _re.compile(r"^<!--\s*praxis-compact:\s*(\{.*\})\s*-->\s*$")
        if not ml.COMPACTS_DIR.exists():
            return counts
        for path in sorted(ml.COMPACTS_DIR.rglob("*.md")):
            try:
                first = path.read_text(encoding="utf-8").splitlines()[0]
            except (OSError, IndexError, UnicodeError):
                continue
            m = meta_re.match(first.strip())
            if not m:
                continue
            try:
                meta = json.loads(m.group(1))
            except ValueError:
                m = None
                continue
            for sid in meta.get("source_event_ids") or []:
                counts[str(sid)] = counts.get(str(sid), 0) + 1
        return counts


class CleanTailAppendKeepsCommit(RaceBase):
    """Приход после prepare не отменяет целые выбранные источники."""

    def test_tail_message_commits_and_stays_hot(self):
        ids = self.seed()
        prepared = self.prepare()
        selected = self.selected_ids(prepared)
        self.assertEqual(selected, ids[:GAP_AT])
        self.assertFalse(self.coverage_counts(), "prepare ещё ничего не покрывает")

        tail = self._msg(500, "новое в хвост", ts=_seed_ts(N_ROWS) + 60.0)
        text = "Я прочитал выбранное начало разговора; это моя выжимка."
        out = ml.apply_authored_fold(ROOM, prepared["selection"], text)

        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("folded"), GAP_AT, out)
        self.assertEqual(out.get("source_ids"), selected, out)
        self.assertEqual(ml.compact_text(out["compact_id"], ROOM), text)
        ml.rebuild_state(ROOM)
        self.assertEqual(self.hot_ids(), set(ids[GAP_AT:]) | {str(tail["id"])})
        self.assert_coverage(selected)


class MidPrefixInsertIsNotAConflict(RaceBase):
    """Бэклог меняет позиции, но не идентичность уже прочитанных строк."""

    def test_backdated_event_commits_and_stays_hot(self):
        ids = self.seed()
        prepared = self.prepare()
        selected = self.selected_ids(prepared)
        self.assertEqual(selected, ids[:GAP_AT])

        backlog = self._msg(501, "бэклог из середины", ts=_seed_ts(3) + 60.0)
        out = ml.apply_authored_fold(
            ROOM, prepared["selection"], "Выжимка исходного выбранного окна.")

        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("source_ids"), selected, out)
        self.assertEqual(out.get("folded"), GAP_AT, out)
        ml.rebuild_state(ROOM)
        self.assertEqual(self.hot_ids(), set(ids[GAP_AT:]) | {str(backlog["id"])})
        self.assert_coverage(selected)


class EditInPrefixRequiresFreshAuthorship(RaceBase):
    """Правка запрещает старую выжимку; новый текст появляется только явно."""

    def test_conflict_then_explicit_prepare_and_new_text(self):
        ids = self.seed()
        prepared = self.prepare()
        old_id = ids[2]
        stale_text = "Выжимка, написанная до правки источника."
        changed = self._edit(2, "исправлено после плана", ts=10_000_000.0)

        with mock.patch.object(ml, "_write_compact",
                               wraps=ml._write_compact) as writer:
            refused = ml.apply_authored_fold(
                ROOM, prepared["selection"], stale_text)
            self.assert_conflict(refused, stale_text)
            writer.assert_not_called()

        self.assertFalse(self.coverage_counts())
        self.assertNotIn(old_id, self.hot_ids())
        self.assertIn(str(changed["id"]), self.hot_ids())

        # Это новый явный акт чтения/авторства, не автоматический replan.
        fresh = self.prepare()
        fresh_ids = self.selected_ids(fresh)
        self.assertNotEqual(fresh["selection"], prepared["selection"])
        self.assertNotIn(old_id, fresh_ids)
        fresh_text = "Новая выжимка после повторного чтения актуальных источников."
        out = ml.apply_authored_fold(ROOM, fresh["selection"], fresh_text)

        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("source_ids"), fresh_ids, out)
        self.assertEqual(ml.compact_text(out["compact_id"], ROOM), fresh_text)
        self.assert_coverage(fresh_ids)
        self.assertNotIn(stale_text, ml.context_summary(ROOM))


class CompetingFoldRequiresFreshSelection(RaceBase):
    """Два автора прочитали начало; победившее покрытие нельзя записать дважды."""

    def test_competing_coverage_then_explicit_new_selection(self):
        ids = self.seed()
        first = self.prepare()
        first_ids = self.selected_ids(first)
        self.assertEqual(first_ids, ids[:GAP_AT])

        # Другой автор явно читает и сворачивает первые три источника.
        competitor = self.prepare(limit=3)
        competitor_ids = self.selected_ids(competitor)
        self.assertEqual(competitor_ids, first_ids[:3])
        won = ml.apply_authored_fold(
            ROOM, competitor["selection"], "Выжимка другого автора по трём строкам.")
        self.assertTrue(won.get("ok"), won)
        self.assert_coverage(competitor_ids)

        stale_text = "Мой текст по исходному шестистрочному окну."
        with mock.patch.object(ml, "_write_compact",
                               wraps=ml._write_compact) as writer:
            refused = ml.apply_authored_fold(
                ROOM, first["selection"], stale_text)
            self.assert_conflict(refused, stale_text)
            writer.assert_not_called()
        self.assert_coverage(competitor_ids)

        fresh = self.prepare()
        fresh_ids = self.selected_ids(fresh)
        self.assertFalse(set(fresh_ids) & set(competitor_ids))
        out = ml.apply_authored_fold(
            ROOM, fresh["selection"], "Новая выжимка только ещё непокрытого окна.")
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("source_ids"), fresh_ids, out)
        ml.rebuild_state(ROOM)
        self.assert_coverage(set(competitor_ids) | set(fresh_ids))
        self.assertTrue(set(ids) - set(self.hot_ids()) ==
                        set(competitor_ids) | set(fresh_ids))


class NoAuthorAnswerDoesNotFold(RaceBase):
    """Ни предложение, ни пустой ответ не создают запасную механическую сводку."""

    def test_offer_and_empty_answer_leave_every_source_hot(self):
        ids = self.seed()

        with mock.patch.object(
                ml, "_model_compact",
                side_effect=AssertionError("скрытый автор запрещён")) as helper:
            offer = ml.compact_if_due(ROOM, force=True)
            self.assertTrue(offer.get("ok"), offer)
            self.assertTrue(offer.get("author_required"), offer)
            self.assertEqual(offer.get("folded"), 0, offer)
            self.assertFalse(self.coverage_counts())

            prepared = self.prepare()
            for text in ("", " \n\t "):
                with self.subTest(text=repr(text)):
                    out = ml.apply_authored_fold(
                        ROOM, prepared["selection"], text)
                    self.assertFalse(out.get("ok"), out)
                    self.assertEqual(out.get("reason"),
                                     "text_and_selection_required", out)
                    self.assertEqual(out.get("folded"), 0, out)
            helper.assert_not_called()

        ml.rebuild_state(ROOM)
        self.assertEqual(self.hot_ids(), set(ids))
        self.assertFalse(self.coverage_counts())


class CrashBetweenCompactAndStateConverges(RaceBase):
    """Настоящий apply записал канон; сохранение производного state оборвалось."""

    def test_rebuild_after_crash_excludes_covered_exactly_once(self):
        ids = self.seed()
        prepared = self.prepare()
        selected = self.selected_ids(prepared)
        self.assertEqual(selected, ids[:GAP_AT])
        written = []
        real_write = ml._write_compact
        real_save = ml._save_state

        def write_then_mark(*args, **kwargs):
            meta = real_write(*args, **kwargs)
            written.append(meta)
            return meta

        def save_until_compact_exists(*args, **kwargs):
            # prepare/apply могут сохранять пересобранный state ДО записи.
            if written:
                raise OSError("injected failure after compact, before state save")
            return real_save(*args, **kwargs)

        with mock.patch.object(ml, "_write_compact", write_then_mark), \
                mock.patch.object(ml, "_save_state", save_until_compact_exists):
            with self.assertRaisesRegex(OSError, "injected failure"):
                ml.apply_authored_fold(
                    ROOM, prepared["selection"], "Авторская выжимка до обрыва.")

        self.assertEqual(len(written), 1, written)
        self.assertTrue((ml.BASE / written[0]["path"]).is_file())
        ml.rebuild_state(ROOM)
        self.assertEqual(self.hot_ids(), set(ids[GAP_AT:]))
        self.assert_coverage(selected)

        # Повтор старого selection не создаёт второй канонический компакт.
        text = "Авторская выжимка до обрыва."
        with mock.patch.object(ml, "_write_compact",
                               wraps=ml._write_compact) as writer:
            refused = ml.apply_authored_fold(ROOM, prepared["selection"], text)
            self.assert_conflict(refused, text)
            writer.assert_not_called()
        self.assert_coverage(selected)


class RepeatedConflictsNeverReuseStaleText(RaceBase):
    """Новый конфликт — новый отказ, без скрытого цикла и stale-text retry."""

    def test_two_explicit_conflicts_commit_nothing(self):
        ids = self.seed()
        first = self.prepare()
        self.assertIn(ids[2], self.selected_ids(first))
        self._edit(2, "правка раз", ts=10_000_000.0)
        first_text = "Первая уже устаревшая авторская выжимка."
        self.assert_conflict(
            ml.apply_authored_fold(ROOM, first["selection"], first_text),
            first_text)

        second = self.prepare()
        self.assertNotEqual(second["selection"], first["selection"])
        self.assertIn(ids[3], self.selected_ids(second))
        self._edit(3, "правка два", ts=10_000_100.0)
        second_text = "Вторая выжимка после нового чтения, тоже устаревшая."
        with mock.patch.object(ml, "_write_compact",
                               wraps=ml._write_compact) as writer:
            self.assert_conflict(
                ml.apply_authored_fold(ROOM, second["selection"], second_text),
                second_text)
            writer.assert_not_called()

        self.assertFalse(self.coverage_counts())
        self.assertIn(ids[4], self.hot_ids(), "неизменённый источник сохранён")
        self.assertNotIn(ids[2], self.hot_ids())
        self.assertNotIn(ids[3], self.hot_ids())


class PrefixConflictUnit(unittest.TestCase):
    """Единица сверки: идентичность строк (id+line+direction) в порядке ленты,
    устойчивая к вставкам и обогащению метадат; гость/правка/перестановка — конфликт."""

    @staticmethod
    def _row(i: int, line: str = "текст", **extra) -> dict:
        row = {"id": f"e{i}", "ts": 1000.0 + i, "line": line, "actor": "A",
               "direction": "in", "salience": 2, "tokens": 7, "chat": "c",
               "meta": {"observed_at": "x"}}
        row.update(extra)
        return row

    def test_tail_and_mid_insert_and_enrichment_are_alive(self):
        inputs = [self._row(i) for i in range(6)]
        tail = inputs + [self._row(99)]
        self.assertIsNone(ml._prefix_conflict(inputs, tail))
        mid = inputs[:3] + [self._row(98)] + inputs[3:]
        self.assertIsNone(ml._prefix_conflict(inputs, mid))
        enriched = [dict(r, tokens=999, meta={"observed_at": "y", "new": 1},
                         salience=int(r["salience"]))
                    for r in mid]
        self.assertIsNone(ml._prefix_conflict(inputs, enriched),
                          "обогащение метадат — не конфликт")

    def test_gone_changed_and_reordered_are_conflicts(self):
        inputs = [self._row(i) for i in range(6)]
        gone = [self._row(i) for i in range(6) if i != 2]
        self.assertEqual(ml._prefix_conflict(inputs, gone)["kind"], "gone")
        changed = [dict(self._row(i), line="другой текст") if i == 2 else self._row(i)
                   for i in range(6)]
        self.assertEqual(ml._prefix_conflict(inputs, changed)["kind"], "changed")
        swapped = [self._row(i) for i in (0, 2, 1, 3, 4, 5)]
        self.assertEqual(ml._prefix_conflict(inputs, swapped)["kind"], "reordered")


def _runner_source() -> str | None:
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        path = cand / "desk" / "localharness" / "runner.py"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return None


@unittest.skipUnless(_runner_source(), "исходник desk/localharness/runner.py не найден")
class FoldRunReceiptsAndBusy(unittest.TestCase):
    """Расписки кнопки: занятость снимается на каждом исходе; после переноса
    автоповтора в compact_if_due state_changed в расписке означает ДВА конфликта."""

    def setUp(self):
        src = _runner_source()
        tree = ast.parse(src)
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_fold_run")
        module = ast.Module(body=[fn], type_ignores=[])
        ast.fix_missing_locations(module)
        self.ns = {
            "dt": dt, "json": json, "os": os, "contextlib": contextlib,
            "Path": Path, "log": logging.getLogger("fold-test"), "STREAM": "stream",
        }
        exec(compile(module, "<runner _fold_run>", "exec"), self.ns)
        self.tmp = Path(tempfile.mkdtemp(prefix="fold_run_1010_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ns["_FOLD_BUSY"] = [False]
        self.ns["_FOLD_ACTIVE"] = [None]
        claimed = self.tmp / "memory" / ".control" / "fold.processing.json"
        claimed.parent.mkdir(parents=True, exist_ok=True)
        claimed.write_text("{}", encoding="utf-8")
        self.claimed = claimed

    def _run(self, out: dict | Exception):
        if isinstance(out, Exception):
            def fold_now(room):
                raise out
        else:
            def fold_now(room):
                return dict(out)
        self.ns["_life"] = _Life(fold_now)
        self.ns["_FOLD_BUSY"] = [True]
        self.ns["_fold_run"](self.tmp, {"room": "room", "id": "r1", "by": "owner",
                                        "at": "t"}, self.claimed)
        receipt = json.loads((self.tmp / "memory" / ".state" / "fold-receipt.json")
                             .read_text(encoding="utf-8"))
        return receipt, self.ns["_FOLD_BUSY"][0]

    def test_state_changed_receipt_and_busy_cleared(self):
        receipt, busy = self._run({"ok": False, "reason": "state_changed"})
        self.assertEqual(receipt["state"], "retry")
        self.assertFalse(busy, "занятость обязана сниматься")

    def test_done_receipt_mentions_second_pass(self):
        receipt, busy = self._run({"ok": True, "folded": 158, "retried": True})
        self.assertEqual(receipt["state"], "done")
        self.assertEqual(receipt["folded"], 158)
        self.assertIn("второго захода", receipt["note"])
        self.assertFalse(busy)

    def test_plain_done_receipt(self):
        receipt, busy = self._run({"ok": True, "folded": 12})
        self.assertEqual(receipt["state"], "done")
        self.assertNotIn("второго захода", receipt["note"])
        self.assertFalse(busy)

    def test_exception_receipt_and_busy_cleared(self):
        receipt, busy = self._run(RuntimeError("модель ушла"))
        self.assertEqual(receipt["state"], "failed")
        self.assertFalse(busy)

    def test_nothing_to_fold_receipt(self):
        receipt, busy = self._run({"ok": True, "folded": 0,
                                   "plan": {"reason": "within_window"}})
        self.assertEqual(receipt["state"], "nothing")
        self.assertFalse(busy)

    def test_claimed_request_file_removed_on_every_outcome(self):
        self._run({"ok": True, "folded": 1})
        self.assertFalse(self.claimed.exists())


class _Life:
    def __init__(self, fold_now):
        self.fold_now = fold_now


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
