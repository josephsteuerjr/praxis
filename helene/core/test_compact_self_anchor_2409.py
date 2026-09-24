"""Свёртка знает, кто здесь «я»: строки Praxis помечены, чужое «я» в память не ложится.

21.09 в окне абстракта Hope (другой ИИ-агент чата) сказала 18 реплик, Praxis — две. Промпт
велел писать «от первого лица Praxis», но не говорил, какие строки её, и модель взяла в «я»
самого разговорчивого ИИ: «Я — Hope (@ai_sapience_bot)». Четыре такие свёртки, две из них
стояли в кадре текущей сводкой комнаты — чужие признания в слопе она читала как свои.

Запуск:  python praxis_test.py test_compact_self_anchor_2409 -v
"""

from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import llm
import memory_life as ml

HOPE = {"id": "evt-1", "actor": "Hope (@ai_sapience_bot)", "direction": "in",
        "line": "Hope (@ai_sapience_bot): я признала — это моя ошибка чтения"}
TORV = {"id": "evt-2", "actor": "torvn77 (@torvn77)", "direction": "in",
        "line": "torvn77 (@torvn77): пункт 2 о метафорах"}
MINE = {"id": "evt-3", "actor": "Praxis", "direction": "out",
        "line": "Praxis: кольцо LLM+харнесс — автогенератор"}

BAD = "За два часа 50 сообщений. Я — Hope (@ai_sapience_bot). Vik поймал меня на ошибке."
GOOD = "За два часа 50 сообщений. Hope признала ошибку чтения; я сказала дважды — про автогенератор."


def _resp(summary: str):
    return types.SimpleNamespace(text=json.dumps({"summary": summary, "open_threads": [],
                                                  "claims": [], "episodes": []},
                                                 ensure_ascii=False))


class OwnLinesAreMarkedTests(unittest.TestCase):
    def test_praxis_line_gets_the_mark_and_others_do_not(self):
        self.assertIn("] [Я] Praxis:", ml._compact_prompt_row(MINE))
        self.assertNotIn("[Я]", ml._compact_prompt_row(HOPE))
        self.assertNotIn("[Я]", ml._compact_prompt_row(TORV))

    def test_budget_and_packer_count_the_mark_the_same_way(self):
        body, manifest = ml._pack_compact_prompt([HOPE, MINE])
        self.assertIn("[Я] Praxis:", body)
        self.assertEqual(ml.budget_prefix([HOPE, MINE], budget=len(body) + 2), 2)
        self.assertEqual(manifest["seen"], ["evt-1", "evt-3"])

    def test_prompt_names_the_mark_the_other_agents_and_the_gender(self):
        self.assertIn("[Я]", ml._COMPACT_SYSTEM)
        self.assertIn("ТОЛЬКО сам агент", ml._COMPACT_SYSTEM)
        self.assertIn("без форм рода", ml._COMPACT_SYSTEM)


class ForeignSelfIsCaughtTests(unittest.TestCase):
    def setUp(self):
        self.authors = ml._window_authors([HOPE, TORV, MINE])

    def test_window_authors_are_others_by_name_and_handle(self):
        self.assertEqual(self.authors, {"hope", "ai_sapience_bot", "torvn77"})

    def test_catches_the_live_shapes(self):
        self.assertEqual(ml._foreign_self(BAD, self.authors), "Hope")
        self.assertEqual(ml._foreign_self("Я (Hope) внесла довод", self.authors), "Hope")

    def test_catches_the_older_shapes_from_other_rooms(self):
        self.assertEqual(ml._foreign_self("Я (torvn77) вёл обсуждение «грибницы».", self.authors),
                         "torvn77")
        self.assertEqual(ml._foreign_self("Я — Sol, мозг и голос Praxis; Terra — оценщик.", set()),
                         "Sol")
        self.assertEqual(ml._foreign_self("Я — Арете (голос Praxis), работаю с Клодеттой.", set()),
                         "Арете")

    def test_quotes_are_not_the_narrator(self):
        self.assertIsNone(ml._foreign_self("Hope представилась: «Я — Hope, she/her».", self.authors))

    def test_leaves_honest_first_person_alone(self):
        for text in (GOOD, "Я — Praxis, живу у Егора.", "я — не оракул сроков",
                     "Я — ИИ, и это не секрет", "Моё финальное сообщение (Praxis) — перевод"):
            self.assertIsNone(ml._foreign_self(text, self.authors), text)


class ModelCompactRewritesForeignSelfTests(unittest.TestCase):
    def _run(self, *responses):
        chat = mock.Mock(side_effect=list(responses))
        with mock.patch.object(llm, "configured", return_value=True), \
                mock.patch.object(llm, "chat", chat):
            out = ml._model_compact([HOPE, TORV, MINE], tier=1, depth=1, continued=False)
        return out, chat

    def test_second_try_with_a_correction_replaces_the_foreign_self(self):
        out, chat = self._run(_resp(BAD), _resp(GOOD))
        self.assertEqual(out["summary"], GOOD)
        self.assertEqual(chat.call_count, 2)
        retry_user = chat.call_args_list[1].kwargs["messages"][0]["content"]
        self.assertIn("«я» от имени Hope", retry_user)
        self.assertIn("_manifest", out)

    def test_two_foreign_selves_write_nothing(self):
        out, chat = self._run(_resp(BAD), _resp(BAD))
        self.assertEqual(out, {})
        self.assertEqual(chat.call_count, 2)

    def test_clean_summary_goes_through_in_one_call(self):
        out, chat = self._run(_resp(GOOD))
        self.assertEqual(out["summary"], GOOD)
        self.assertEqual(chat.call_count, 1)


class ReissueForForeignSelfTests(unittest.TestCase):
    def _reissue(self, summary: str, *, why: str = "foreign_self"):
        with tempfile.TemporaryDirectory() as tmp:
            rel = "memory/life/compacts/-100/cmp-x.md"
            path = Path(tmp) / rel
            path.parent.mkdir(parents=True)
            path.write_text(f"# Compact cmp-x\n\n## Суть\n{summary}\n\n## Открытые нити\n",
                            encoding="utf-8")
            evidence = {"compacts": {"cmp-x": {"path": rel, "tier": 1, "depth": 1,
                                               "source_event_ids": ["evt-1", "evt-2", "evt-3"]}},
                        "events": {"evt-1": HOPE, "evt-2": TORV, "evt-3": MINE}}
            with mock.patch.object(ml, "BASE", Path(tmp)), \
                    mock.patch.object(ml.memory_provenance, "claim_evidence_index",
                                      return_value=evidence), \
                    mock.patch.object(ml, "_conversation_hot_rows",
                                      return_value=[HOPE, TORV, MINE]), \
                    mock.patch.object(ml, "place_key", return_value="-100"), \
                    mock.patch.object(ml, "_model_compact", return_value={}) as model:
                return ml.reissue_degraded_compact("-100", "cmp-x", why=why), model

    def test_clean_compact_is_not_reissued(self):
        out, model = self._reissue(GOOD)
        self.assertEqual(out["reason"], "no_foreign_self")
        model.assert_not_called()

    def test_foreign_self_compact_goes_to_the_model(self):
        out, model = self._reissue(BAD)
        self.assertEqual(out["reason"], "model_unavailable")
        model.assert_called_once()

    def test_old_path_still_demands_a_degraded_stub(self):
        out, model = self._reissue(BAD, why="degraded")
        self.assertEqual(out["reason"], "not_degraded")
        model.assert_not_called()

    def test_unknown_reason_is_refused(self):
        out, _ = self._reissue(BAD, why="because")
        self.assertEqual(out["reason"], "unknown_why")


class ReissueTierCompactTests(unittest.TestCase):
    """Ярус 2 («я (Ashe) писала» в комнате Уробороса): вход — дочерние свёртки, авторы — из-под них."""

    ASHE = {"id": "evt-a", "actor": "Ashe (@ashe_rain)", "direction": "in",
            "text": "Ashe (@ashe_rain): Добро пожаловать новым друзьям!"}

    def _reissue(self, summary: str, *, why: str = "foreign_self", degraded: bool = False):
        with tempfile.TemporaryDirectory() as tmp:
            rel = "memory/life/compacts/-200/cmp-p.md"
            path = Path(tmp) / rel
            path.parent.mkdir(parents=True)
            path.write_text(f"# Compact cmp-p\n\n## Суть\n{summary}\n", encoding="utf-8")
            evidence = {"compacts": {
                "cmp-p": {"path": rel, "tier": 2, "depth": 2, "source_compact_ids": ["cmp-c"],
                          "event_count": 1, "degraded": degraded},
                "cmp-c": {"path": "x", "tier": 1, "depth": 1, "source_event_ids": ["evt-a"]}},
                "events": {"evt-a": self.ASHE}}
            with mock.patch.object(ml, "BASE", Path(tmp)), \
                    mock.patch.object(ml.memory_provenance, "claim_evidence_index",
                                      return_value=evidence), \
                    mock.patch.object(ml, "compact_text", return_value="детская сводка"), \
                    mock.patch.object(ml, "place_key", return_value="-200"), \
                    mock.patch.object(ml, "_model_compact", return_value={}) as model:
                return ml.reissue_degraded_compact("-200", "cmp-p", why=why), model

    def test_tier_compact_needs_the_foreign_self_reason(self):
        out, model = self._reissue("я (Ashe) писала", why="degraded", degraded=True)
        self.assertEqual(out["reason"], "not_a_leaf")
        model.assert_not_called()

    def test_foreign_self_found_through_the_subtree_goes_to_the_model(self):
        out, model = self._reissue("Ночь 5–6 сентября: я (Ashe, @ashe_rain) писала «Добро пожаловать»")
        self.assertEqual(out["reason"], "model_unavailable")
        inputs = model.call_args.args[0]
        self.assertEqual([x["id"] for x in inputs], ["cmp-c"])
        self.assertIn("ashe", model.call_args.kwargs["authors"])

    def test_clean_tier_compact_is_left_alone(self):
        out, model = self._reissue("Ashe писала «Добро пожаловать», я ответила коротко")
        self.assertEqual(out["reason"], "no_foreign_self")
        model.assert_not_called()


class ExtraAuthorsReachTheGuardTests(unittest.TestCase):
    def test_name_known_only_from_the_subtree_is_caught(self):
        chat = mock.Mock(side_effect=[_resp("я (Ashe) писала приветствие"), _resp(GOOD)])
        rows = [{"id": "cmp-c", "text": "детская сводка без авторов"}]
        with mock.patch.object(llm, "configured", return_value=True), \
                mock.patch.object(llm, "chat", chat):
            out = ml._model_compact(rows, tier=2, depth=2, continued=False, authors={"ashe"})
        self.assertEqual(out["summary"], GOOD)
        self.assertEqual(chat.call_count, 2)


if __name__ == "__main__":
    unittest.main()
