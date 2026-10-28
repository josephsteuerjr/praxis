# Живой пропуск 03.10 (Ouroboros): convo ≥50% чужих слов при разборе китайского
# корпуса заглушил реплику с внутрисловной склейкой «训练ный» — она ушла raw.
# Гейт комнаты должен защищать только ОБОСОБЛЕННЫЕ кластеры; внутрисловные
# склейки — сигнатура дефекта подстановки — чистятся в любой комнате.

import os
import unittest

os.environ["PRAXIS_CJK_VKLADKI_LLM"] = "0"  # детерминированно, без сети

import agent  # noqa: E402


FOREIGN_ROOM = "训练 数据 质量 模型 训练 数据 实验 训练 corpus"


class IntrawordInForeignRoom(unittest.TestCase):
    def test_glued_cluster_cleaned_in_foreign_room(self):
        out, note = agent._sanitize_cjk_substitutions(
            "в моём рантайме训练ный вклейка", FOREIGN_ROOM)
        self.assertIn("⟨вклейка:", out)
        self.assertNotIn("训练ный", out)
        self.assertTrue(note.startswith("cjk-inlay:"))

    def test_isolated_cluster_untouched_in_foreign_room(self):
        out, note = agent._sanitize_cjk_substitutions(
            "цитата отдельно: 训练 качество", FOREIGN_ROOM)
        self.assertEqual(out, "цитата отдельно: 训练 качество")
        self.assertEqual(note, "")

    def test_mostly_foreign_reply_still_untouched(self):
        reply = "训练 数据 модели речи"
        out, note = agent._sanitize_cjk_substitutions(reply, FOREIGN_ROOM)
        self.assertEqual(out, reply)
        self.assertEqual(note, "")

    def test_glued_cluster_cleaned_in_normal_room(self):
        out, note = agent._sanitize_cjk_substitutions(
            "в моём рантайме训练ный вклейка", "обычный русский разговор")
        self.assertIn("⟨вклейка:", out)
        self.assertNotIn("训练ный", out)

    def test_proof_convo_candidate_matches_ledger(self):
        # 03.10, блокер гномьего ревью: proof-сверка чистила с convo="" и
        # расходилась с гардом (convo комнаты) → dead_letter. Кандидат с
        # convo хода (_TURN_CONVO в run_direct_outbox_prepared) должен
        # совпадать с леджером.
        raw = "микс: 训练 отдельно и рантайм训练ный хвост"
        ledger, _ = agent._strip_generation_artifacts(raw, FOREIGN_ROOM)
        proof, _ = agent._strip_generation_artifacts(raw, FOREIGN_ROOM)
        self.assertEqual(ledger, proof)
        self.assertNotEqual(agent._strip_generation_artifacts(raw, "")[0], ledger)
        agent._TURN_CONVO.set(FOREIGN_ROOM)
        self.assertEqual(agent._strip_generation_artifacts(
            raw, str(agent._TURN_CONVO.get() or ""))[0], ledger)
        agent._TURN_CONVO.set("")


if __name__ == "__main__":
    unittest.main()
