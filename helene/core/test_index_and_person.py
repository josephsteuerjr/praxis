# -*- coding: utf-8 -*-
"""Приёмка трёх решений от 10.08. Проверяется СВОЙСТВО, а не формулировка.

Именно поэтому здесь нигде не пиниться дословный текст промпта или индекса: третий раз
за неделю тест, закрепивший СЛОВА, охранял беду (петля возобновления, час как социальный
импульс, прививка стенда). Проверяются наблюдаемые свойства: лицо в промпте, наличие
раздела в индексе, тело в ярусе, потолок, откат рычагом.
"""
import importlib
import os
import unittest
from pathlib import Path

import agent
import formation
import frame_layout
import memory_catalog


class ClaimsAreWrittenInHerVoice(unittest.TestCase):
    """1. Расписки о ней пишутся её голосом, а не голосом наблюдателя за ней."""

    def _phases(self, mod):
        return {"harvest": mod._HARVEST_SYS, "dig": mod._DIG_SYS, "attack": mod._ATTACK_SYS}

    def test_all_three_phases_say_they_write_as_her(self):
        for name, text in self._phases(formation).items():
            with self.subTest(phase=name):
                self.assertIn("AS the agent", text,
                              "%s: писателю не сказано, что он пишет ЕЮ, а не о ней" % name)

    def test_first_person_rule_reaches_every_phase(self):
        for name, text in self._phases(formation).items():
            with self.subTest(phase=name):
                self.assertIn("first person", text,
                              "%s: правило первого лица до фазы не доехало" % name)
                self.assertIn("Claims about OTHER people stay in third person", text,
                              "%s: чужие люди обязаны остаться в третьем лице" % name)

    def test_lever_returns_the_old_behaviour(self):
        os.environ["PRAXIS_CLAIMS_FIRST_PERSON"] = "off"
        try:
            mod = importlib.reload(formation)
            self.assertEqual(mod._FIRST_PERSON_RULE, "",
                             "рычаг off обязан снимать правило целиком")
            for name, text in self._phases(mod).items():
                with self.subTest(phase=name):
                    self.assertNotIn("first person", text)
        finally:
            os.environ.pop("PRAXIS_CLAIMS_FIRST_PERSON", None)
            importlib.reload(formation)

    def test_old_records_are_not_touched(self):
        """Её условие дословно: «старые записи не переписывать»."""
        src = Path(memory_catalog.__file__).parent / "formation.py"
        text = src.read_text("utf-8")
        for forbidden in ("rewrite_claims", "migrate_claims", "backfill"):
            self.assertNotIn(forbidden, text,
                             "появился переписыватель старых расписок: %s" % forbidden)


class IndexKnowsWhatSheOwns(unittest.TestCase):
    """2. В индексе есть то, чего в нём не было: стол, хребет, навыки, почта."""

    def test_belongings_section_is_built_from_disk(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "soul").mkdir()
            (base / "soul" / "SOUL.md").write_text("я", encoding="utf-8")
            (base / "soul" / "skills").mkdir()
            (base / "soul" / "skills" / "INDEX.md").write_text("навыки", encoding="utf-8")
            (base / "workspace").mkdir()
            (base / "workspace" / "ПЕРЕДАЧА.md").write_text("текст", encoding="utf-8")
            (base / "memory").mkdir()
            (base / "memory" / "mailbox.json").write_text("{}", encoding="utf-8")
            lines = memory_catalog._render_belongings(base)
            joined = "\n".join(lines)
            self.assertIn("workspace", joined, "стол в индексе не значится")
            self.assertIn("soul", joined, "хребет в индексе не значится")
            self.assertIn("mailbox.json", joined, "почта в индексе не значится")
            self.assertIn("ПЕРЕДАЧА.md", joined,
                          "свежие имена документов не показаны — по имени она их и ищет")

    def test_nothing_is_promised_when_nothing_is_there(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(memory_catalog._render_belongings(Path(tmp)), [],
                             "раздел обещает содержимое там, где его нет")


class IndexRidesInTheFrame(unittest.TestCase):
    """3. Ярус «Карта памяти» несёт тело индекса, а не записку о нём.

    ⚠ Тест НЕ смотрит на живой memory/INDEX.md. Прошлая редакция смотрела — и в песочнице
    молча пропускалась (`skipTest`), то есть проверка формально была, а свойство никто не
    проверял. Это ровно тот класс, из-за которого гейт краснеет от среды и зеленеет от
    отсутствия проверки. Здесь индекс подставной и известный.
    """

    MARKER = "# INDEX — карта памяти\n\n- строка, которой больше нигде нет: 7f3a91"

    def setUp(self):
        import tempfile
        for key in ("PRAXIS_FRAME_INDEX_BODY", "PRAXIS_FRAME_INDEX_MAX"):
            os.environ.pop(key, None)
        self._tmp = tempfile.TemporaryDirectory()
        self._idx = Path(self._tmp.name) / "INDEX.md"
        self._idx.write_text(self.MARKER, encoding="utf-8")
        self._saved = agent.INDEX_MD
        agent.INDEX_MD = self._idx

    def tearDown(self):
        agent.INDEX_MD = self._saved
        self._tmp.cleanup()
        for key in ("PRAXIS_FRAME_INDEX_BODY", "PRAXIS_FRAME_INDEX_MAX"):
            os.environ.pop(key, None)

    def test_body_rides_when_she_turns_it_on(self):
        os.environ["PRAXIS_FRAME_INDEX_BODY"] = "on"
        got = agent._memory_navigation_hint()
        self.assertIn("7f3a91", got, "в ярусе не тело индекса")
        self.assertNotIn("Generated navigation is available", got,
                         "в ярусе по-прежнему записка о том, что индекс где-то есть")

    def test_lever_off_returns_locator(self):
        os.environ["PRAXIS_FRAME_INDEX_BODY"] = "off"
        got = agent._memory_navigation_hint()
        self.assertIn("Generated navigation is available", got,
                      "рычаг off не возвращает прежнее поведение")
        self.assertNotIn("7f3a91", got, "при off тело всё равно едет")

    def test_cap_falls_back_and_says_so(self):
        """Потолок обязан не просто сработать, а НАЗВАТЬ себя: молча пухнуть нельзя."""
        os.environ["PRAXIS_FRAME_INDEX_BODY"] = "on"
        os.environ["PRAXIS_FRAME_INDEX_MAX"] = "10"
        got = agent._memory_navigation_hint()
        self.assertIn("переросло потолок", got, "потолок сработал молча")
        self.assertIn("Generated navigation", got, "при потолке нет и локатора")
        self.assertNotIn("7f3a91", got, "потолок не удержал тело")

    def test_missing_index_gives_nothing_not_a_lie(self):
        agent.INDEX_MD = Path(self._tmp.name) / "нет-такого.md"
        self.assertEqual(agent._memory_navigation_hint(), "",
                         "нет индекса — ярус обязан отсутствовать, а не обещать карту")

    def test_default_is_off_because_her_own_rule_says_so(self):
        """⚠ Способность выключена по умолчанию НЕ из осторожности.

        Её решение 10.08 — «класть индекс целиком». Её же правило, закреплённое
        `test_index_body_is_never_prompt_authority`, — сгенерированный markdown не
        становится властью промпта. Решение принималось, не зная про правило. Пока она
        не разрешит противоречие сама, по умолчанию действует ПРАВИЛО, а не наш ход.
        """
        os.environ.pop("PRAXIS_FRAME_INDEX_BODY", None)
        self.assertFalse(agent._index_body_on(),
                         "тело индекса поехало бы в кадр без её слова")
        self.assertIn("Generated navigation is available", agent._memory_navigation_hint())

    def test_slot_follows_the_lever_not_our_intention(self):
        """Пятый вопрос кадра — «почему это здесь». Подпись слота обязана называть
        наблюдаемое: при выключенном рычаге там локатор, и подпись это и говорит."""
        import importlib
        os.environ.pop("PRAXIS_FRAME_INDEX_BODY", None)
        mod = importlib.reload(frame_layout)
        row = next((r for r in mod._TIERS if r[0] == "Карта памяти"), None)
        self.assertIsNotNone(row, "ярус карты пропал из росписи")
        self.assertEqual(row[3], "локатор, не содержимое",
                         "рычаг выключен, а подпись обещает тело")
        os.environ["PRAXIS_FRAME_INDEX_BODY"] = "on"
        try:
            mod = importlib.reload(frame_layout)
            row = next((r for r in mod._TIERS if r[0] == "Карта памяти"), None)
            self.assertIn("тело индекса", row[3], "рычаг включён, а подпись зовёт это локатором")
            self.assertIn("PRAXIS_FRAME_INDEX_BODY", row[3], "подпись не называет откат")
        finally:
            os.environ.pop("PRAXIS_FRAME_INDEX_BODY", None)
            importlib.reload(frame_layout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
