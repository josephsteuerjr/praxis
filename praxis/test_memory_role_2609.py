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

    def test_legacy_compaction_never_borrows_the_voice_role(self):
        # Acting authorship supersedes the former auxiliary role fallback.
        inputs = [{"line": "source", "text": "source", "salience": 3}]
        with mock.patch.object(llm, "chat") as channel:
            with self.assertRaisesRegex(RuntimeError, "Acting author required"):
                ml._model_compact(inputs, tier=1, depth=1, continued=False)
        channel.assert_not_called()


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
