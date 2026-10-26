# Дефект #111596 (живой, 04.10, AbstractDL #111596): внутрисловная склейка
# «—昨а был водолаз» собиралась как «—⟨вклейка: вчера⟩а был водолаз» — шовная
# кириллическая буква («а»-союз из _SEAM_LETTERS) приклеивалась к маркеру
# вклейки и в таком виде уходила в доставленный текст. Причина в сборке,
# не в детекторе: прогон правильно не поглощает шовную букву («с明确了»
# хранит предлог), но маркер обязан отойти от неё пробелом.
#
# Фикс 05.10 (proposal a4acfd6b): в собранном тексте за «⟩» может стоять
# только не-буква или конец строки; шов получает пробел. Левая склейка
# («история⟨вклейка: любви⟩») — рабочий экспонат, её не трогаем.

import os
import unittest

os.environ["PRAXIS_CJK_VKLADKI_LLM"] = "0"  # детерминированно, без сети

import agent  # noqa: E402

RU_ROOM = "обычный русский разговор про водолазов и историю"
FOREIGN_ROOM = "训练 数据 质量 模型 训练 数据 实验 训练 corpus"


class SeamGlueAssembly(unittest.TestCase):
    def test_right_seam_letter_detached_from_marker(self):
        # живой случай #111596: «—昨а» → маркер и шов раздельно
        out, note = agent._sanitize_cjk_substitutions(
            "—昨а был водолаз", RU_ROOM)
        self.assertIn("⟨вклейка:", out)
        # офлайн-словарь может не знать кластер — fallback кладёт сам кластер
        # внутрь маркера («⟨вклейка: 昨⟩»); перевод — не предмет этого теста
        self.assertFalse(
            out.endswith("⟩а") or "⟩а " in out or "⟩а" in out,
            f"маркер приклеен к шовной букве: {out!r}")
        # шовная буква жива и стоит отдельно после маркера
        self.assertRegex(out, r"⟩\sа")

    def test_right_seam_letter_detached_in_foreign_room(self):
        # тот же класс в чужой комнате: внутрисловные чистятся всегда
        out, _ = agent._sanitize_cjk_substitutions(
            "история昨а продолжается", FOREIGN_ROOM)
        self.assertNotIn("⟩а", out, f"склейка маркера с швом: {out!r}")
        self.assertRegex(out, r"⟩\sа")

    def test_left_glue_is_working_exponent(self):
        # левая склейка — рабочий класс, не дефект: «история昨» даёт
        # «история⟨вклейка: …⟩» без вставленного пробела
        out, _ = agent._sanitize_cjk_substitutions(
            "история昨 была", RU_ROOM)
        self.assertIn("⟨вклейка:", out)
        self.assertIn("история⟨вклейка:", out)

    def test_seam_preposition_preserved_before_cluster(self):
        # «с明确了» — предлог-шов перед кластером хранится (не поглощается),
        # и после фикса он тоже не склеивается с маркером
        out, _ = agent._sanitize_cjk_substitutions(
            "справа с明确了 всё", RU_ROOM)
        self.assertIn("⟨вклейка:", out)
        self.assertNotIn("⟩в", out)
        self.assertNotIn("⟩с", out)

    def test_punctuation_after_cluster_unchanged(self):
        # не-буква после кластера — пробел не вставляется, «⟩,» и «⟩ »
        # остаются как были
        out, _ = agent._sanitize_cjk_substitutions(
            "вот так半, и точка", RU_ROOM)
        self.assertIn("⟩,", out)
        self.assertNotIn("⟩ ,", out)

    def test_end_of_string_no_padding(self):
        # кластер в конце строки: «⟩» и так законен, ничего не добавляем
        out, _ = agent._sanitize_cjk_substitutions("закон半", RU_ROOM)
        self.assertTrue(out.endswith("⟩"))


if __name__ == "__main__":
    unittest.main()
