"""28.09: внутрисловные CJK-кластеры переводятся с контекстом (inlay_ctx),
обособленные — как раньше слепым переводом (inlay). Промах контекстного
перевода без LLM = fallback на слепой перевод, не на кластер."""
import os, sys, unittest
os.environ["PRAXIS_CJK_VKLADKI_LLM"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cjk_vkladki
import agent

ORIG = {n: getattr(cjk_vkladki, n) for n in
        ("inlay", "inlay_ctx", "translate_cluster", "_cheap_translate_ctx",
         "_load_cache", "_save_cache")}


class CtxInlayTests(unittest.TestCase):
    def setUp(self):
        self.store = {}
        cjk_vkladki._load_cache = lambda: dict(self.store)
        cjk_vkladki._save_cache = lambda cache: self.store.update(cache)

    def tearDown(self):
        for k, v in ORIG.items():
            setattr(cjk_vkladki, k, v)

    def test_intraword_uses_ctx(self):
        calls = []
        cjk_vkladki.inlay_ctx = lambda c, l, r: calls.append((c, l, r)) or "⟨вклейка: эти две⟩"
        cjk_vkladki.inlay = lambda c: calls.append(("blind", c)) or "⟨вклейка: X⟩"
        cleaned, note = agent._sanitize_cjk_substitutions(
            "у тебя这两 вещи починены", "привет")
        self.assertEqual(cleaned, "у тебя⟨вклейка: эти две⟩ вещи починены")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "这两")
        self.assertIn("у тебя", calls[0][1])   # окно контекста передано
        self.assertIn("вещи", calls[0][2])
        self.assertIn("cjk-inlay", note)

    def test_standalone_stays_blind(self):
        calls = []
        cjk_vkladki.inlay_ctx = lambda *a: calls.append("ctx") or "⟨вклейка: ctx⟩"
        cjk_vkladki.inlay = lambda c: calls.append("blind") or f"⟨вклейка: {cjk_vkladki.TABLE[c]}⟩"
        cleaned, _ = agent._sanitize_cjk_substitutions(
            "Проверил 同步 и обновил", "привет")
        self.assertIn("⟨вклейка: синхронизация⟩", cleaned)
        self.assertEqual(calls, ["blind"])

    def test_ctx_translate_falls_back_to_blind(self):
        got = cjk_vkladki.translate_cluster_ctx("无效字", "у тебя ", " вещи")
        # без LLM: контекстный промах → слепой промах (нет в TABLE/кэше) → кластер
        self.assertEqual(got, "无效字")

    def test_ctx_llm_hit_is_cached(self):
        self.hits = 0
        def fake_cheap(c, l, r):
            self.hits += 1
            return "эти две"
        cjk_vkladki._cheap_translate_ctx = fake_cheap
        got = cjk_vkladki.translate_cluster_ctx("这两", "у тебя", "вещи")
        self.assertEqual(got, "эти две")
        got2 = cjk_vkladki.translate_cluster_ctx("这两", "у тебя", "вещи")
        self.assertEqual(got2, "эти две")
        self.assertEqual(self.hits, 1)  # второй раз — из кэша


if __name__ == "__main__":
    unittest.main()
