"""26.09 — свёртку памяти пишет её голос, а не несуществующая роль `memory`.

Прод 26.09: в логе `ValueError: llm: неизвестная роль 'memory'` из `memory_life._model_compact`,
и с 25.09 15:06 каждая свёртка — запасная сводка без модели (degraded, «опорные строки, не
синтез»): 8 штук, 7 в Ouroboros AI, где ярус 2 пытался снова каждые 20–40 с. Корень:
`_memory_role()` спрашивал `llm.configured("memory")`, а тот отвечает «да» и без роли —
пустой конфиг роли берёт фреймворк по умолчанию, ключ anthropic у неё есть. `llm.chat`
при этом сверяет роль с `llm.ROLES` (voice, evaluator) и падает.

Прибито: роль `memory` берётся, только если она есть в `llm.ROLES` и настроена; иначе
`voice`. Свёртка с живым каналом не уходит в запасную сводку.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

import llm
import memory_life as ml


def _strict_chat(calls):
    """Как настоящий `llm.chat`: неизвестная роль — отказ; иначе годная сводка JSON."""
    def chat(role, *, system=None, messages, **kw):
        if role not in llm.ROLES:
            raise ValueError(f"llm: неизвестная роль {role!r}")
        calls.append(role)
        return llm.LLMResponse(text=json.dumps({"summary": "Сводка модели.", "open_threads": [],
                                                "claims": [], "episodes": []}, ensure_ascii=False))
    return chat


class MemoryRoleFollowsLlmRoles(unittest.TestCase):
    def test_absent_memory_role_falls_back_to_her_voice(self):
        self.assertNotIn("memory", llm.ROLES)
        with mock.patch.object(llm, "_client_for", return_value=object()):
            self.assertTrue(llm.configured("memory"),
                            "предпосылка: configured() отвечает «да» и без роли")
            self.assertEqual(ml._memory_role(), "voice")

    def test_declared_memory_role_is_used(self):
        with mock.patch.object(llm, "ROLES", (*llm.ROLES, "memory")), \
                mock.patch.object(llm, "_client_for", return_value=object()):
            self.assertEqual(ml._memory_role(), "memory")

    def test_compaction_reaches_the_model_instead_of_the_degraded_fallback(self):
        calls: list[str] = []
        inputs = [{"line": "Егор: привет", "text": "привет", "salience": 3},
                  {"line": "Она: здравствуй", "text": "здравствуй", "salience": 2}]
        with mock.patch.object(llm, "_client_for", return_value=object()), \
                mock.patch.object(llm, "chat", side_effect=_strict_chat(calls)), \
                mock.patch.object(ml, "_anchor_self", side_effect=lambda data, **kw: data):
            result = ml._model_compact(inputs, tier=1, depth=1, continued=False)
        self.assertEqual(calls, ["voice"])
        self.assertEqual(result.get("summary"), "Сводка модели.")
        self.assertFalse(result.get("degraded"), result)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
