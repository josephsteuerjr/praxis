"""Свёртку пишет она (25.09, слово Егора).

Раньше свёртку писал «хроникёр» — отдельный промпт про неё в третьем лице, ролью
evaluator. Теперь свёртку пишет сама действующая Praxis: prepare выбирает точные источники,
fold(selection, text) сохраняет её текст. Вспомогательная модель и фоновый refold
не пишут за неё. list / read / rewrite сохраняют доступ к прежним свёрткам.

Запуск:  python praxis_test.py test_her_compacts_2509 -v
"""

from __future__ import annotations

import json
import sys
import types
import unittest
from unittest import mock

import memory_life as ml
import memory_provenance
from test_coverage_vs_current import Base, ROOM


def _fake_llm(role_ok=("voice", "memory"), summary="я записала это сама",
              roles=("voice", "evaluator", "memory")):
    """Фейк `llm` с контрактом живого: объявленные роли (`ROLES`) и отказ `chat` на чужой.

    ⚠ 26.09: прежний фейк принимал в `chat` ЛЮБУЮ роль и не знал `ROLES` — поэтому стенд не
    увидел, что живой `llm` роли `memory` не знает: с 25.09 15:06 каждая свёртка у неё
    уходила в запасную сводку без модели. `roles` — роли, которые этот мир объявил."""
    captured = {}

    class _Resp:
        text = json.dumps({"summary": summary, "open_threads": ["нить"], "claims": [],
                           "episodes": []}, ensure_ascii=False)

    class _LLM:
        ROLES = tuple(roles)

        @staticmethod
        def configured(role):
            return role in role_ok

        @staticmethod
        def chat(role, system, messages, max_tokens, **kw):
            # 05.10: живой llm.chat уже несёт reasoning_effort и может добавить ещё —
            # фейк терпит лишние kwargs, иначе стенд снова разъедется с контрактом (как 26.09).
            if role not in _LLM.ROLES:
                raise ValueError(f"llm: неизвестная роль {role!r}")
            captured.update(role=role, system=system, user=messages[0]["content"],
                            max_tokens=max_tokens)
            captured["calls"] = captured.get("calls", 0) + 1
            return _Resp()
    return _LLM(), captured


class TheSystemIsHerOwnVoice(Base):
    def _soul(self, text="# Конституция Praxis\n\nЯ — Praxis. Тест."):
        soul = self.tmp / "soul"
        soul.mkdir(parents=True, exist_ok=True)
        (soul / "SOUL.md").write_text(text, encoding="utf-8")

    def test_persona_and_first_person_task_and_default_rules(self):
        self._soul()
        system = ml.compact_system()
        self.assertTrue(system.startswith("# Конституция Praxis"), "персона — в голове system")
        self.assertIn("Это моя память, и записываю её я сама", system)
        self.assertNotIn("хроникёр, не её публичный голос", system)
        self.assertIn("[Я]", system)
        self.assertIn("ТОЛЬКО Praxis", system)
        self.assertIn("soul/memory_style.md", system)
        self.assertIn('"summary"', system)

    def test_her_style_file_replaces_the_default_rules(self):
        self._soul()
        (self.tmp / "soul" / "memory_style.md").write_text(
            "Мой вкус: коротко, зло, с цитатами.", encoding="utf-8")
        system = ml.compact_system()
        self.assertIn("Мой вкус: коротко, зло, с цитатами.", system)
        self.assertNotIn("1 500–3 000 знаков", system, "умолчание уступает её файлу целиком")
        self.assertIn("Это моя память", system, "задача и форма остаются")
        self.assertIn('"summary"', system)

    def test_without_soul_the_task_still_stands(self):
        system = ml.compact_system()
        self.assertTrue(system.startswith("Это моя память"))

    def test_compact_system_constant_keeps_the_marks_for_older_stands(self):
        for word in ("[Я]", "ТОЛЬКО Praxis", "в женском роде", "ЦИТИРУЮ ДОСЛОВНО",
                     "1 500–3 000 знаков", "не выдумывать", "continued"):
            self.assertIn(word, ml._COMPACT_SYSTEM, word)


class TheModelCallIsHers(Base):
    def test_no_auxiliary_role_can_write_the_authors_recap(self):
        fake, captured = _fake_llm(role_ok=("memory", "voice", "evaluator"))
        with mock.patch.dict(sys.modules, {"llm": fake}):
            with self.assertRaisesRegex(RuntimeError, "Acting author required"):
                ml._model_compact([], tier=1, depth=1, continued=False)
        self.assertNotIn("calls", captured)


class RewriteInPlace(Base):
    def _rows(self, n=3):
        return [self._msg(mid, "реплика %d" % mid) for mid in range(1, n + 1)]

    def test_rewrite_keeps_id_header_and_provenance_and_files_history(self):
        rows = self._rows()
        meta = self._compact(rows, summary="старая сводка хроникёра")
        before = (ml.BASE / meta["path"]).read_text(encoding="utf-8")
        out = ml.rewrite_compact_text(meta["id"], "Я сама записала: три реплики Николая, ничего важного.",
                                      chat_id=ROOM, open_threads=["дождаться ответа"])
        self.assertTrue(out["ok"], out)
        after = (ml.BASE / meta["path"]).read_text(encoding="utf-8")
        self.assertEqual(before.splitlines()[0], after.splitlines()[0], "шапка байт в байт")
        self.assertIn("Я сама записала", after)
        self.assertNotIn("старая сводка хроникёра", after)
        self.assertIn("- дождаться ответа", after)
        self.assertIn("## Происхождение", after)
        evidence = memory_provenance.claim_evidence_index(ml.MEM_DIR)
        self.assertEqual(evidence["compacts"][meta["id"]]["source_event_ids"], meta["source_event_ids"])
        self.assertTrue(memory_provenance.compact_coverage(meta["id"], evidence)["valid"])
        history = ml.BASE / out["history"]
        self.assertTrue(history.exists(), "прежний текст — в истории")
        self.assertEqual(history.read_text(encoding="utf-8"), before)
        self.assertEqual(len(ml.rebuild_state(ROOM).get("hot") or []), 0, "кольцо не выросло")
        rec = [e for e in ml.iter_events(chat_id=ROOM, kinds={"memory_compact"})]
        self.assertTrue(any(e.get("meta", {}).get("rewritten") for e in rec), "событие о переписке")

    def test_history_is_invisible_to_the_evidence_index(self):
        rows = self._rows()
        meta = self._compact(rows)
        ml.rewrite_compact_text(meta["id"], "новая суть", chat_id=ROOM)
        ml.rewrite_compact_text(meta["id"], "ещё новее", chat_id=ROOM)
        evidence = memory_provenance.claim_evidence_index(ml.MEM_DIR)
        self.assertEqual(len(evidence["compacts"]), 1)
        self.assertEqual(ml.read_compact(meta["id"], ROOM)["recap"], "ещё новее")

    def test_empty_or_unknown_is_refused(self):
        rows = self._rows()
        meta = self._compact(rows)
        self.assertEqual(ml.rewrite_compact_text(meta["id"], "   ", chat_id=ROOM)["reason"], "empty_summary")
        self.assertEqual(ml.rewrite_compact_text("cmp-20260101T000000000000Z-deadbeef", "x", chat_id=ROOM)["reason"],
                         "unknown_compact")

    def test_list_and_read(self):
        rows = self._rows()
        meta = self._compact(rows, summary="первые слова сути")
        listed = ml.list_compacts(ROOM)
        self.assertEqual([r["id"] for r in listed], [meta["id"]])
        self.assertEqual(listed[0]["events"], 3)
        self.assertTrue(listed[0]["head"].startswith("первые слова"))
        self.assertEqual(ml.list_compacts(ROOM, tier=2), [])
        read = ml.read_compact(meta["id"], ROOM)
        self.assertTrue(read["ok"])
        self.assertIn("# Compact " + meta["id"], read["text"])


class RefoldInHerVoice(Base):
    def setUp(self):
        super().setUp()
        self._model = ml._model_compact
        self.calls = []

        def stub(inputs, **kw):
            self.calls.append([str(x.get("id")) for x in inputs])
            return {"summary": "перевыпущено моим голосом", "open_threads": [], "claims": [],
                    "episodes": []}
        ml._model_compact = stub
        self.addCleanup(lambda: setattr(ml, "_model_compact", self._model))

    def _rows(self, n=3, start=1):
        return [self._msg(mid, "реплика %d" % mid) for mid in range(start, start + n)]

    def test_refold_plan_leaves_before_parents_and_filters(self):
        a = self._compact(self._rows(2, 1))
        b = self._compact(self._rows(2, 3))
        parent = ml._write_compact(
            ROOM, {"summary": "родитель", "open_threads": [], "claims": [], "episodes": []},
            tier=2, depth=2, source_events=[], source_compacts=[a["id"], b["id"]],
            event_count=4, continued=False, first_ts=a["first_ts"], last_ts=b["last_ts"])
        plan = ml.refold_plan(ROOM)
        self.assertEqual(plan[-1][1], parent["id"], "родитель после листьев")
        self.assertEqual({cid for _, cid in plan}, {a["id"], b["id"], parent["id"]})
        self.assertEqual([cid for _, cid in ml.refold_plan(ROOM, tier=2)], [parent["id"]])
        self.assertEqual(len(ml.refold_plan(ROOM, limit=1)), 1)
        self.assertEqual(ml.refold_plan("-1000000"), [])

    def test_refold_one_keeps_recap_for_the_acting_author(self):
        meta = self._compact(self._rows(3), summary="old retained recap")
        out = ml.refold_one(ROOM, meta["id"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "acting_author_required")
        self.assertEqual(self.calls, [])
        self.assertEqual(ml.read_compact(meta["id"], ROOM)["recap"], "old retained recap")

    def test_refold_skips_a_leaf_whose_sources_are_no_longer_current(self):
        rows = self._rows(2)
        meta = self._compact(rows)
        self._edit(1, "исправленная реплика")
        out = ml.refold_one(ROOM, meta["id"])
        self.assertEqual(out.get("reason"), "acting_author_required")
        self.assertEqual(self.calls, [], "модель не звалась")

    def test_refold_start_runs_in_background_and_reports(self):
        rows = self._rows(2)
        meta = self._compact(rows)
        with mock.patch.object(ml.threading, "Thread") as thread:
            out = ml.refold_start(ROOM, pause_sec=0)
            self.assertTrue(out["ok"])
            self.assertEqual(out["planned"], 1)
            worker, kwargs = thread.call_args[1]["target"], thread.call_args[1]["args"]
        worker(*kwargs)
        st = ml.refold_status()
        self.assertFalse(st["running"])
        self.assertEqual((st["done"], st["failed"], st["skipped"]), (0, 1, 0))
        self.assertEqual(st["last"]["id"], meta["id"])
        self.assertTrue((ml.MEM_DIR / ".state" / "refold.json").exists())
        self.assertNotEqual(ml.read_compact(meta["id"], ROOM)["recap"], "перевыпущено моим голосом")

    def test_second_refold_while_running_is_refused_and_stop_flags(self):
        ml._REFOLD.clear()
        ml._REFOLD.update({"running": True, "stop": False, "planned": 5, "done": 1})
        self.addCleanup(ml._REFOLD.clear)
        self.assertEqual(ml.refold_start(ROOM)["reason"], "already_running")
        self.assertTrue(ml.refold_stop()["stopping"])
        self.assertTrue(ml._REFOLD["stop"])


class TheHandIsWired(unittest.TestCase):
    def test_schema_impl_purpose_and_english(self):
        import agent
        import tool_text_en
        names = [t["name"] for t in agent.OWNER_TOOLS]
        self.assertIn("memory_compact", names)
        self.assertIn("memory_compact", [t["name"] for t in agent.PRAXIS_SELF_TOOLS])
        self.assertIs(agent.TOOL_IMPL["memory_compact"], agent.tool_memory_compact)
        self.assertIn("memory_compact", agent.HAND_PURPOSE)
        cov = tool_text_en.coverage([agent.MEMORY_COMPACT_TOOL])
        self.assertEqual(cov["stale_translations"], [])
        self.assertEqual(cov["ru_descriptions"], [])
        self.assertEqual(cov["undescribed_params"], [])

    def test_hand_receipts(self):
        import agent
        with mock.patch.object(agent.memory_life, "list_compacts", return_value=[]):
            self.assertIn("не нашла", agent.tool_memory_compact("list", place="-1"))
        # соседний стенд в том же процессе мог оставить контекст хода — окно без чата задаём явно
        with mock.patch.object(agent, "_active_chat", return_value=None):
            self.assertIn("укажи place", agent.tool_memory_compact("list"))
        self.assertIn("нужны compact_id и text", agent.tool_memory_compact("rewrite", place="-1"))
        with mock.patch.object(agent.memory_life, "rewrite_compact_text",
                               return_value={"ok": True, "chars": 12, "history": "h.md"}), \
             mock.patch.object(agent, "tool_journal") as journal:
            out = agent.tool_memory_compact("rewrite", place="-1", compact_id="cmp-x", text="моя суть")
        self.assertIn("Переписала cmp-x", out)
        journal.assert_called()
        with mock.patch.object(agent, "_active_chat", return_value=None):
            self.assertIn("prepare", agent.tool_memory_compact("refold"))
        with mock.patch.object(agent.memory_life, "refold_status", return_value={"planned": 0}):
            self.assertIn("не запускался", agent.tool_memory_compact("status"))



class HistoryIsOutsideTheRecallCorpus(unittest.TestCase):
    """Ревью V4 F4 / V1-4 (25.09): прежний текст переписанной свёртки лежит в `_history/`
    под той же шапкой — в корпус recall он попадать не должен."""

    def test_memory_files_skip_history_dirs(self):
        import tempfile
        from pathlib import Path
        import memory_fts
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        mem = Path(tmp.name)
        live = mem / "life" / "compacts" / "-100"
        hist = mem / "life" / "compacts" / "_history" / "-100"
        live.mkdir(parents=True); hist.mkdir(parents=True)
        (live / "cmp-1.md").write_text("x", encoding="utf-8")
        (hist / "cmp-1.20260925T000000Z.md").write_text("x", encoding="utf-8")
        got = {p.relative_to(mem).as_posix() for p in memory_fts._memory_files(mem, "*.md", include_runs=False)}
        self.assertIn("life/compacts/-100/cmp-1.md", got)
        self.assertFalse(any("_history" in g for g in got), got)
        got_runs = {p.relative_to(mem).as_posix() for p in memory_fts._memory_files(mem, "*.md", include_runs=True)}
        self.assertFalse(any("_history" in g for g in got_runs), got_runs)


class FoldByHand(unittest.TestCase):
    """25.09, слово Егора: свёртка предлагается, а делает её она — рукой memory_compact(fold)."""

    def test_fold_applies_only_the_authors_selected_text(self):
        import agent
        out = {"ok": True, "folded": 3, "compact_id": "cmp-x", "hot": 250}
        with mock.patch.object(agent.memory_life, "apply_authored_fold", return_value=out) as fn:
            text = agent.tool_memory_compact("fold", place="-100777", selection="selected", text="My recap")
        fn.assert_called_once_with("-100777", "selected", "My recap")
        self.assertEqual(json.loads(text), out)

    def test_fold_without_a_place_asks_for_one(self):
        import agent
        with mock.patch.object(agent, "_active_chat", return_value=None):
            self.assertIn("place", agent.tool_memory_compact("fold"))

    def test_fold_is_in_the_hand_schema(self):
        import agent
        self.assertIn("fold", agent.MEMORY_COMPACT_TOOL["input_schema"]["properties"]["action"]["enum"])


if __name__ == "__main__":
    unittest.main()
