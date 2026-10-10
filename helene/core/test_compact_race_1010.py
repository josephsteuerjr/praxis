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

    def _fold_async(self, *mutators):
        """compact_if_due(force=True) в потоке; каждый мутатор исполняется
        в паузе СВОЕГО модельного вызова (i-й — внутри i-го вызова)."""
        calls: list[list[str]] = []
        holds: list[threading.Event] = []
        wake = threading.Event()

        def fake(inputs, *, tier, depth, continued, authors=None):
            ids = [str(x["id"]) for x in inputs]
            calls.append(ids)
            hold = threading.Event()
            holds.append(hold)
            wake.set()
            if len(holds) <= len(mutators):
                self.assertTrue(hold.wait(10), "пауза модели не дождалась")
            return {"summary": f"сводка захода {len(calls)} по {len(ids)} событиям",
                    "open_threads": [], "claims": [], "episodes": [],
                    "_manifest": {"seen": ids, "omitted": []}}

        box: dict = {}

        def run():
            try:
                box["out"] = ml.compact_if_due(ROOM, force=True)
            except Exception as exc:  # noqa: BLE001 — причина нужна тесту
                box["exc"] = exc

        with mock.patch.object(ml, "_model_compact", fake):
            t = threading.Thread(target=run, name="fold-race", daemon=True)
            t.start()
            try:
                for idx, mutate in enumerate(mutators, 1):
                    deadline = time.monotonic() + 10
                    while len(holds) < idx:
                        if time.monotonic() > deadline:
                            self.fail(f"модельный вызов {idx} не начался")
                        wake.wait(0.05)
                    mutate()
                    holds[idx - 1].set()
                t.join(60)
            finally:
                for hold in holds:
                    hold.set()
                t.join(10)
        self.assertFalse(t.is_alive(), "свёртка не вернулась из паузы")
        self.assertIsNone(box.get("exc"), f"compact_if_due упал: {box.get('exc')!r}")
        return box.get("out") or {}, calls

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
    """Чистый append в хвост НЕ срывает свёртку — ни до фикса, ни после."""

    def test_tail_message_commits_and_stays_hot(self):
        ids = self.seed()
        out, calls = self._fold_async(
            lambda: self._msg(500, "новое в хвост", ts=_seed_ts(N_ROWS) + 60.0))
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("folded"), GAP_AT, out)
        self.assertEqual(len(calls), 1, f"хвост не повод для второго захода: {calls}")
        self.assertTrue(set(calls[0]) <= set(ids))
        tail = [i for i in self.hot_ids() if i not in set(ids)]
        self.assertEqual(len(tail), 1, "новое хвостовое событие должно остаться горячим")
        counts = self.coverage_counts()
        self.assertTrue(counts, "свёртка должна быть записана")
        self.assertTrue(all(c == 1 for c in counts.values()), counts)


class MidPrefixInsertIsNotAConflict(RaceBase):
    """Событие с ts ВНУТРИ префикса (бэклог/перенос времени): позиционный срез
    меняется, идентичности строк — нет. До фикса это ровно механизм живого
    срыва 15:32:52 (HTTP 200 → state_changed при целых источниках); после — коммит."""

    def test_backdated_event_commits_and_stays_hot(self):
        ids = self.seed()
        mid_id = None

        def insert_backlog():
            nonlocal mid_id
            mid_id = str(self._msg(501, "бэклог из середины", ts=_seed_ts(3) + 60.0)["id"])

        out, calls = self._fold_async(insert_backlog)
        if out.get("reason") == "state_changed":
            # ДО ФИКСА (репро): сверка позиции пошла по пересборке ленты (порядок
            # событий), а план брал префикс из файла состояния (порядок прибытия).
            provable = ml._drop_unprovable_inputs(
                ml.rebuild_state(ROOM)["hot"], ROOM)
            first_ids = [str(r["id"]) for r in provable[:GAP_AT]]
            self.assertNotEqual(first_ids, calls[0],
                                "позиционный префикс обязан был разъехаться")
            self.assertTrue(set(calls[0]) <= {str(r["id"]) for r in provable},
                            "источники целы — срыв ложный")
            self.assertFalse(self.coverage_counts(), "результат модели выброшен целиком")
            raise self.failureException(f"репро 10.10: ложный state_changed — {out}")
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("folded"), GAP_AT, out)
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(set(calls[0]), set(ids[:GAP_AT]))
        self.assertIn(mid_id, self.hot_ids(), "бэклог остаётся горячим")
        counts = self.coverage_counts()
        self.assertEqual(set(counts), set(calls[0]), counts)
        self.assertTrue(all(v == 1 for v in counts.values()), counts)


class EditInPrefixConflictsOnceThenFreshPlan(RaceBase):
    """Правка источника — реальный конфликт: результат модели нельзя применять.
    Ровно один автоматический повтор НОВЫМ планом, без ручного нажатия."""

    def test_one_retry_new_plan_covers_only_living_ids(self):
        ids = self.seed()
        old_id = ids[2]
        out, calls = self._fold_async(
            lambda: self._edit(2, "исправлено после плана", ts=10_000_000.0))
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("retried"), out)
        self.assertEqual(len(calls), 2, calls)
        self.assertNotIn(old_id, calls[1], "повтор обязан планировать заново")
        counts = self.coverage_counts()
        self.assertNotIn(old_id, counts, "правленая ревизия не покрывается старым id")
        self.assertTrue(all(v == 1 for v in counts.values()), counts)
        self.assertNotIn(old_id, self.hot_ids())
        self.assertTrue(set(calls[1]) <= set(counts))


class CompetingFoldConflictsOnce(RaceBase):
    """Конкурирующая свёртка покрыла начало префикса — реальный конфликт и один
    повтор, покрывающий только непокрытый остаток, без двойного покрытия.
    До фикса этот же случай требовал ручного повторного нажатия."""

    def test_competing_coverage_then_one_retry(self):
        self.seed()

        def mutator():
            provable = ml._drop_unprovable_inputs(ml._load_state(ROOM)["hot"], ROOM)
            self._compact(provable[:3])

        out, calls = self._fold_async(mutator)
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("retried"), out)
        self.assertEqual(len(calls), 2, calls)
        self.assertFalse(set(calls[1]) & set(calls[0][:3]),
                         "повтор не имеет права накрывать уже покрытое")
        counts = self.coverage_counts()
        self.assertTrue(all(v == 1 for v in counts.values()), counts)
        first3 = {i for i in calls[0][:3]}
        self.assertTrue(first3 <= set(counts), "конкурирующая свёртка должна остаться")


class NoModelAnswerFallsBackDegraded(RaceBase):
    """Модель молчит — запасная сводка честно помечается degraded и коммитится;
    занятость не остаётся висеть (для кнопки это проверяет файл расписок)."""

    def test_degraded_commit_without_model(self):
        self.seed()
        with mock.patch.object(ml, "_model_compact", lambda *a, **kw: {}):
            out = ml.compact_if_due(ROOM, force=True)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("folded"), GAP_AT, out)
        self.assertTrue(out.get("degraded"), out)
        counts = self.coverage_counts()
        self.assertTrue(all(v == 1 for v in counts.values()), counts)


class CrashBetweenCompactAndStateConverges(RaceBase):
    """Обрыв после записи компакта, до сохранения состояния (restart-mid-fold):
    состояние производное — первая же пересборка сходится, дублей нет."""

    def test_rebuild_after_crash_excludes_covered_exactly_once(self):
        ids = self.seed()
        hot = {str(r["id"]): r for r in ml._load_state(ROOM)["hot"]}
        self._compact([hot[i] for i in ids[:GAP_AT]])  # компакт есть, state_file нет
        ml.rebuild_state(ROOM)
        remaining = self.hot_ids()
        self.assertEqual(remaining, set(ids[GAP_AT:]),
                         "пересборка обязана исключить покрытое и сохранить остальное")
        counts = self.coverage_counts()
        self.assertEqual(set(counts), set(ids[:GAP_AT]), counts)


class DoubleConflictGivesUpHonestly(RaceBase):
    """Два конфликта подряд — не спин: ровно один автоповтор, честный
    state_changed с причиной, ничего не закоммичено по протухшему плану."""

    def test_second_conflict_returns_state_changed_nothing_committed(self):
        ids = self.seed()
        out, calls = self._fold_async(
            lambda: self._edit(2, "правка раз", ts=10_000_000.0),
            lambda: self._edit(3, "правка два", ts=10_000_100.0))
        self.assertFalse(out.get("ok"), out)
        self.assertEqual(out.get("reason"), "state_changed", out)
        self.assertTrue(out.get("retried"), out)
        self.assertEqual(len(calls), 2, calls)
        self.assertIn("conflict", out, "причина конфликта должна быть названа")
        self.assertFalse(self.coverage_counts(), "по протухшему плану писать нельзя")
        self.assertIn(ids[4], self.hot_ids(), "кольцо живо")


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
