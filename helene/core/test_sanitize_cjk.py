"""CJK-санитайзер вклеек glm-5.3 (договор с torvn77, 27.09): обособленные
кластеры иероглифов в не-CJK речи заменяются переводной вклейкой
«⟨вклейка: перевод⟩»; CJK-речь и CJK-комнаты не трогаются.
Промах словаря без LLM (PRAXIS_CJK_VKLADKI_LLM=0) = fallback на исходный
кластер в тех же скобках."""

import os
import unittest

os.environ["PRAXIS_CJK_VKLADKI_LLM"] = "0"  # тесты детерминированы, без сети

import agent  # noqa: E402


class CjkInlayTests(unittest.TestCase):

    def test_table_hit(self):
        out, note = agent._sanitize_cjk_substitutions(
            "Ответ по делу: 包括 ту запись за 25.09", "разбор дневника, второй ход")
        self.assertIn("⟨вклейка: включая⟩", out)
        self.assertNotIn("包括", out)
        self.assertTrue(note.startswith("cjk-inlay:"))

    def test_fallback_keeps_cluster(self):
        # промах словаря, LLM выключен — кластер остаётся в скобках
        out, note = agent._sanitize_cjk_substitutions(
            "странная 龍夢 вставка", "контекст русский")
        self.assertIn("⟨вклейка: 龍夢⟩", out)
        self.assertTrue(note.startswith("cjk-inlay:"))

    def test_multiple_clusters(self):
        out, note = agent._sanitize_cjk_substitutions(
            "по\u534a часа назад, потом \u6211 ответ", "контекст русский")
        self.assertIn("⟨вклейка: половина⟩", out)
        self.assertIn("⟨вклейка: я⟩", out)
        self.assertNotIn("\u534a", out)

    def test_cjk_room_not_touched(self):
        text = "Ответ: 了解しました，马上做"
        out, note = agent._sanitize_cjk_substitutions(
            text, "日本語の会話が続いています、昨日も話した")
        self.assertEqual(out, text)
        self.assertEqual(note, "")

    def test_mostly_cjk_reply_not_touched(self):
        # сама реплика CJK — это осознанная речь, не дефект
        text = "深層学習は難しいですね"
        out, note = agent._sanitize_cjk_substitutions(text, "давай про нейросети")
        self.assertEqual(out, text)
        self.assertEqual(note, "")

    def test_empty(self):
        self.assertEqual(agent._sanitize_cjk_substitutions("", "контекст"),
                         ("", ""))

    def test_ratio_function(self):
        self.assertEqual(agent._cjk_ratio(""), 0.0)
        self.assertEqual(agent._cjk_ratio("абв"), 0.0)
        self.assertAlmostEqual(agent._cjk_ratio("а半"), 0.5)


class CjkVkladkiModuleTests(unittest.TestCase):

    def test_cache_roundtrip(self):
        import cjk_vkladki, tempfile, json, pathlib
        with tempfile.TemporaryDirectory() as td:
            orig = agent.MEM_DIR
            agent.MEM_DIR = pathlib.Path(td)
            try:
                cjk_vkladki._save_cache({"测试": "тест"})
                self.assertEqual(cjk_vkladki._load_cache(), {"测试": "тест"})
                # кэш-хит без LLM
                self.assertEqual(cjk_vkladki.translate_cluster("测试"), "тест")
                # промах в кэше как пусто = прошлый отказ, кластер возвращается
                cjk_vkladki._save_cache({"龍": ""})
                self.assertEqual(cjk_vkladki.translate_cluster("龍"), "龍")
            finally:
                agent.MEM_DIR = orig


if __name__ == "__main__":
    unittest.main()
