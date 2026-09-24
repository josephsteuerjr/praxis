# -*- coding: utf-8 -*-
"""Запасной провайдер со своим ключом, проекция мозга без затирания и слова о лимите (25.09).

Баги Сергея (C.2/C.4): `fallback_framework` указывал в пустой блок фреймворка — ключа
и адреса там не было, и запасной провайдер не работал никогда; `merged.update(built)`
затирал ручные правки вложенных блоков llm.json; лимит подписки приходил английским
текстом реле как реплика агента.
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / "localharness")]
import boot  # noqa: E402
from deskd import readers  # noqa: E402


def _cfg(**model):
    base = {"framework": "openai", "base_url": "http://127.0.0.1:5011", "key": "sk-frame-x",
            "model": "gpt-5.6-sol"}
    base.update(model)
    return {"model": base, "owner": {"name": "Сергей"}, "agent": {"name": "Феофан"}}


class FallbackProvider(unittest.TestCase):
    def test_other_framework_gets_its_own_address_and_key(self):
        built = boot._brain_config(_cfg(fallback_framework="anthropic", fallback_model="glm-5.3",
                                        fallback_base_url="https://api.z.ai/api/anthropic",
                                        fallback_key="zai-key"))
        self.assertEqual(built["frameworks"]["anthropic"],
                         {"base_url": "https://api.z.ai/api/anthropic", "api_key": "zai-key"})
        self.assertEqual(built["roles"]["voice"]["fallback_framework"], "anthropic")
        self.assertEqual(built["roles"]["voice"]["fallback_model"], "glm-5.3")
        # Основная нога не тронута.
        self.assertEqual(built["frameworks"]["openai"]["api_key"], "sk-frame-x")

    def test_same_framework_fallback_keeps_one_client_per_framework(self):
        built = boot._brain_config(_cfg(fallback_framework="openai", fallback_model="gpt-5.6-luna",
                                        fallback_base_url="https://api.openai.com/v1",
                                        fallback_key="sk-own"))
        # Один клиент на фреймворк — второй адрес того же протокола выразить нельзя,
        # и проекция честно не притворяется, что может.
        self.assertEqual(built["frameworks"]["openai"]["base_url"], "http://127.0.0.1:5011")
        self.assertEqual(built["frameworks"]["openai"]["api_key"], "sk-frame-x")
        self.assertEqual(built["roles"]["voice"]["fallback_framework"], "openai")

    def test_no_fallback_leaves_the_other_framework_empty(self):
        built = boot._brain_config(_cfg())
        self.assertEqual(built["frameworks"]["anthropic"], {"base_url": "", "api_key": ""})
        self.assertNotIn("fallback_framework", built["roles"]["voice"])


class ProjectionMerge(unittest.TestCase):
    def test_manual_nested_edits_survive_and_dropped_fallback_is_dropped(self):
        current = {
            "frameworks": {"openai": {"base_url": "http://old", "api_key": "old"},
                           "anthropic": {"base_url": "https://manual", "api_key": "manual-key"}},
            "roles": {"voice": {"framework": "openai", "model": "gpt-old",
                                "fallback_framework": "anthropic", "fallback_model": "glm-5.3",
                                "vision_models": {"openai": "gpt-5.6-terra"}},
                      "evaluator": {"framework": "openai", "model": "gpt-old"}},
            "limits": {"max_tool_iters": 20}, "pricing": {"gpt-old": {"in": 1, "out": 2}},
        }
        built = boot._brain_config(_cfg(model="gpt-new"))
        merged = boot._merge_brain(current, built)
        # Ручной второй фреймворк пережил проекцию: пустое из проекции не затирает.
        self.assertEqual(merged["frameworks"]["anthropic"],
                         {"base_url": "https://manual", "api_key": "manual-key"})
        self.assertEqual(merged["frameworks"]["openai"]["api_key"], "sk-frame-x")
        voice = merged["roles"]["voice"]
        self.assertEqual(voice["model"], "gpt-new")
        self.assertEqual(voice["vision_models"], {"openai": "gpt-5.6-terra"}, "её ручка осталась")
        self.assertNotIn("fallback_framework", voice, "фолбэк убран в окне — убран и здесь")
        self.assertEqual(voice["fallback_model"], "")
        self.assertIn("pricing", merged, "верхний pricing из проекции переезжает как раньше")

    def test_project_brain_is_idempotent_and_keeps_manual_role_knobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            cfg = _cfg()
            first = boot.project_brain(tree, cfg)
            self.assertIn("записан", first)
            self.assertIn("не трогаю", boot.project_brain(tree, cfg))
            target = tree / "memory" / "llm.json"
            data = json.loads(target.read_text(encoding="utf-8"))
            data["roles"]["voice"]["vision_models"] = {"openai": "gpt-5.6-terra"}
            target.write_text(json.dumps(data), encoding="utf-8")
            cfg["model"]["model"] = "gpt-5.6-luna"
            self.assertIn("записан", boot.project_brain(tree, cfg))
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(data["roles"]["voice"]["model"], "gpt-5.6-luna")
            self.assertEqual(data["roles"]["voice"]["vision_models"], {"openai": "gpt-5.6-terra"})


class QuotaWords(unittest.TestCase):
    def test_active_hold_is_read_and_expired_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quota.json"
            now = time.time()
            path.write_text(json.dumps({"holds": [
                {"framework": "openai", "code": "subscription_window_exhausted",
                 "words": "подписка исчерпана до 14:35", "until": now + 600}]}),
                encoding="utf-8")
            hold = readers._quota_hold(path, now)
            self.assertEqual(hold["words"], "подписка исчерпана до 14:35")
            self.assertEqual(hold["framework"], "openai")
            path.write_text(json.dumps({"holds": [
                {"framework": "openai", "words": "вчера", "until": now - 1}]}), encoding="utf-8")
            self.assertIsNone(readers._quota_hold(path, now))
            self.assertIsNone(readers._quota_hold(Path(tmp) / "нет.json", now))


if __name__ == "__main__":
    unittest.main()
