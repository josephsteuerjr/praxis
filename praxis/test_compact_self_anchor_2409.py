"""Свёртка знает, кто здесь «я»: строки Praxis помечены, чужое «я» в память не ложится.

21.09 в окне абстракта Hope (другой ИИ-агент чата) сказала 18 реплик, Praxis — две. Промпт
велел писать «от первого лица Praxis», но не говорил, какие строки её, и модель взяла в «я»
самого разговорчивого ИИ: «Я — Hope (@ai_sapience_bot)». Четыре такие свёртки, две из них
стояли в кадре текущей сводкой комнаты — чужие признания в слопе она читала как свои.

Запуск:  python praxis_test.py test_compact_self_anchor_2409 -v
"""

from __future__ import annotations

import tempfile
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
        self.assertIn("ТОЛЬКО Praxis", ml._COMPACT_SYSTEM)
        self.assertIn("в женском роде", ml._COMPACT_SYSTEM)


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


class AuxiliaryCompactRequiresTheActingAuthorTests(unittest.TestCase):
    def test_no_summary_or_tier_can_start_an_auxiliary_writer(self):
        cases = [
            ([HOPE, TORV, MINE], 1, 1, None),
            ([{"id": "cmp-c", "text": "детская сводка"}], 2, 2, {"ashe"}),
            ([], 1, 1, None),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(ml, "BASE", root), \
                    mock.patch.object(llm, "configured") as configured, \
                    mock.patch.object(llm, "chat") as chat, \
                    mock.patch.object(ml, "_write_compact") as write_compact, \
                    mock.patch.object(ml, "append_event") as append_event, \
                    mock.patch.object(Path, "write_text") as write_text:
                for rows, tier, depth, authors in cases:
                    with self.subTest(tier=tier, rows=rows):
                        with self.assertRaisesRegex(RuntimeError, "Acting author required"):
                            ml._model_compact(
                                rows, tier=tier, depth=depth, continued=False,
                                authors=authors,
                            )

                configured.assert_not_called()
                chat.assert_not_called()
                write_compact.assert_not_called()
                append_event.assert_not_called()
                write_text.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])


class LegacyReissueRequiresTheActingAuthorTests(unittest.TestCase):
    def test_old_reissue_reasons_never_generate_or_write(self):
        # The legacy entry point refuses before old reason/content validation.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(ml, "BASE", root), \
                    mock.patch.object(llm, "configured") as configured, \
                    mock.patch.object(llm, "chat") as chat, \
                    mock.patch.object(ml, "_model_compact") as model, \
                    mock.patch.object(ml, "_write_compact") as write_compact, \
                    mock.patch.object(ml, "rewrite_compact_text") as rewrite, \
                    mock.patch.object(ml, "append_event") as append_event, \
                    mock.patch.object(Path, "write_text") as write_text:
                for why in ("degraded", "foreign_self", "because"):
                    with self.subTest(why=why):
                        out = ml.reissue_degraded_compact("-100", "cmp-x", why=why)
                        self.assertFalse(out["ok"], out)
                        self.assertEqual(out["reason"], "acting_author_required")

                configured.assert_not_called()
                chat.assert_not_called()
                model.assert_not_called()
                write_compact.assert_not_called()
                rewrite.assert_not_called()
                append_event.assert_not_called()
                write_text.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])


class SourceAuthorsRemainAvailableTests(unittest.TestCase):
    """Disabling auxiliary generation does not discard source-author evidence."""

    ASHE = {
        "id": "evt-a",
        "actor": "Ashe (@ashe_rain)",
        "direction": "in",
        "text": "Ashe (@ashe_rain): Добро пожаловать новым друзьям!",
    }

    def test_tier_authors_are_recovered_through_the_source_subtree(self):
        evidence = {
            "compacts": {
                "cmp-p": {"source_event_ids": [], "source_compact_ids": ["cmp-c"]},
                "cmp-c": {"source_event_ids": ["evt-a"], "source_compact_ids": []},
            },
            "events": {"evt-a": self.ASHE},
        }
        rows = ml._subtree_event_rows(evidence, "cmp-p")
        self.assertEqual([row["id"] for row in rows], ["evt-a"])
        authors = ml._window_authors(rows)
        self.assertEqual(authors, {"ashe", "ashe_rain"})
        self.assertEqual(
            ml._foreign_self(
                "Ночь 5–6 сентября: я (Ashe, @ashe_rain) писала «Добро пожаловать»",
                authors,
            ),
            "Ashe",
        )
        self.assertIsNone(
            ml._foreign_self(
                "Ashe писала «Добро пожаловать», я ответила коротко",
                authors,
            )
        )

    def test_extra_source_author_can_be_checked_without_a_model(self):
        authors = ml._window_authors([HOPE, TORV, MINE]) | {"ashe"}
        self.assertEqual(ml._foreign_self("я (Ashe) писала приветствие", authors), "Ashe")
        self.assertIsNone(ml._foreign_self(GOOD, authors))


if __name__ == "__main__":
    unittest.main()
