"""Свёртка знает, кто здесь «я»: строки Praxis помечены, чужое «я» в память не ложится.

21.09 в окне абстракта Hope (другой ИИ-агент чата) сказала 18 реплик, Praxis — две. Промпт
велел писать «от первого лица Praxis», но не говорил, какие строки её, и модель взяла в «я»
самого разговорчивого ИИ: «Я — Hope (@ai_sapience_bot)». Четыре такие свёртки, две из них
стояли в кадре текущей сводкой комнаты — чужие признания в слопе она читала как свои.

Запуск:  python praxis_test.py test_compact_self_anchor_2409 -v
"""

from __future__ import annotations

from test_authorship_support_1910 import AuthoredMemoryBase, assert_reissue_refused

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
    def test_helper_neither_corrects_nor_retries_foreign_self(self):
        with mock.patch.object(llm, "chat") as chat, \
                mock.patch.object(ml, "_write_compact") as write:
            with self.assertRaisesRegex(RuntimeError, "Acting author required"):
                ml._model_compact(
                    [HOPE, TORV, MINE], tier=1, depth=1, continued=False
                )
        chat.assert_not_called()
        write.assert_not_called()

    def test_even_a_clean_summary_does_not_authorize_a_hidden_call(self):
        with mock.patch.object(llm, "chat", return_value=_resp(GOOD)) as chat:
            with self.assertRaisesRegex(RuntimeError, "Acting author required"):
                ml._model_compact(
                    [HOPE, TORV, MINE], tier=1, depth=1, continued=False
                )
        chat.assert_not_called()



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
                return assert_reissue_refused(self, "-100", "cmp-x", why=why), model

    def test_clean_compact_is_not_reissued(self):
        out, model = self._reissue(GOOD)
        self.assertEqual(out["reason"], "acting_author_required")
        model.assert_not_called()

    def test_foreign_self_compact_requires_the_acting_author(self):
        out, model = self._reissue(BAD)
        self.assertEqual(out["reason"], "acting_author_required")
        model.assert_not_called()

    def test_degraded_reason_does_not_restore_the_helper(self):
        out, model = self._reissue(BAD, why="degraded")
        self.assertEqual(out["reason"], "acting_author_required")
        model.assert_not_called()

    def test_unknown_reason_never_writes(self):
        out, model = self._reissue(BAD, why="because")
        self.assertEqual(out["reason"], "acting_author_required")
        model.assert_not_called()






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
                return assert_reissue_refused(self, "-200", "cmp-p", why=why), model

    def test_degraded_tier_is_not_reissued_implicitly(self):
        out, model = self._reissue(
            "я (Ashe) писала", why="degraded", degraded=True
        )
        self.assertEqual(out["reason"], "acting_author_required")
        model.assert_not_called()

    def test_foreign_self_tier_is_not_reissued_implicitly(self):
        out, model = self._reissue(
            "Ночь 5–6 сентября: я (Ashe, @ashe_rain) писала «Добро пожаловать»"
        )
        self.assertEqual(out["reason"], "acting_author_required")
        model.assert_not_called()

    def test_clean_tier_compact_is_left_unchanged(self):
        out, model = self._reissue(
            "Ashe писала «Добро пожаловать», я ответила коротко"
        )
        self.assertEqual(out["reason"], "acting_author_required")
        model.assert_not_called()





class ExtraAuthorsReachTheGuardTests(unittest.TestCase):
    def test_name_known_only_from_the_subtree_is_caught(self):
        rows = [{"id": "cmp-c", "text": "детская сводка без авторов"}]
        authors = ml._window_authors(rows) | {"ashe"}
        self.assertEqual(
            ml._foreign_self("я (Ashe) писала приветствие", authors), "Ashe"
        )
        self.assertIsNone(ml._foreign_self(GOOD, authors))



if __name__ == "__main__":
    unittest.main()

class AuthoredSelfAnchor(AuthoredMemoryBase):
    def test_honest_author_text_is_retained_without_helper_rewriting(self):
        self.assert_authored_success(GOOD)
