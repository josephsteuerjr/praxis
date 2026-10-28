"""Ступень рассуждения — ЕЁ рычаг (19.08). Три обязательства:

словарь — дословно словарь реле (REASONING_EFFORTS в chat_completions.rs: реле по
умолчанию гасит рассуждение, per-request поле сильнее); явный thinking кода в
конкретном вызове сильнее фоновой ступени роли; смена без «зачем» не имеет
провенанса. Повод — её же слова в Уроборосе 18.08 16:00: «reasoning лучше менять
не наугад» — теперь есть чем менять не наугад.
"""
from __future__ import annotations

import types
import unittest
from unittest import mock

import agent
import brain
import llm


class _FakeAnthropic:
    """Минимальный фейк anthropic-SDK (как в test_llm): .messages.create -> ответ."""

    def __init__(self):
        self.messages = self

    def create(self, **kw):
        return types.SimpleNamespace(
            stop_reason="end_turn",
            content=[types.SimpleNamespace(type="text", text="ок")],
            usage=types.SimpleNamespace(input_tokens=10, output_tokens=3))


class TheDictionaryIsTheRelays(unittest.TestCase):

    def test_the_tiers_are_the_relay_words_verbatim(self):
        # Литерал, а не чтение чужого исходника: прод-гейт дерева реле не несёт.
        # Разъедется со словарём реле — красный здесь, обновлять оба конца руками.
        self.assertEqual(llm.REASONING_EFFORTS,
                         ("none", "minimal", "low", "medium", "high", "xhigh"))

    def test_explicit_thinking_beats_the_role_tier(self):
        self.assertEqual(llm._effective_effort(9000, "low"), "high")
        self.assertEqual(llm._effective_effort(1000, "xhigh"), "low")

    def test_the_role_tier_fills_the_silence_and_junk_is_dropped(self):
        self.assertEqual(llm._effective_effort(None, "xhigh"), "xhigh")
        self.assertEqual(llm._effective_effort(None, " Medium "), "medium")
        self.assertIsNone(llm._effective_effort(None, "turbo"))
        self.assertIsNone(llm._effective_effort(None, ""))


class TheTierRidesTheCall(unittest.TestCase):

    def test_chat_threads_the_role_tier_into_the_transport(self):
        cfg = {"frameworks": {"openai": {"api_key": "x", "base_url": "http://relay"}},
               "roles": {"voice": {"framework": "openai", "model": "gpt-5.6-sol",
                                   "max_tokens": 100, "reasoning_effort": "high"}}}
        seen = {}

        def fake_call(fw, model, **kw):
            seen.update(kw, framework=fw, model=model)
            return llm.LLMResponse(text="ок", blocks=[], stop_reason="end_turn",
                                   usage={}, framework=fw, model=model)

        with mock.patch.object(llm, "_config", return_value=cfg), \
                mock.patch.object(llm, "_call", side_effect=fake_call):
            resp = llm.chat("voice", messages=[{"role": "user", "content": "привет"}])
        self.assertEqual(resp.text, "ок")
        self.assertEqual(seen.get("reasoning_effort"), "high",
                         "фоновая ступень роли не доехала до транспорта")

    def test_the_anthropic_path_accepts_and_ignores_the_tier(self):
        # Общий kw-путь: ступень роли не смеет ронять glm-вызов незнакомым аргументом.
        resp = llm._call_anthropic(_FakeAnthropic(), "glm-5.2", system=None,
                                   messages=[{"role": "user", "content": "х"}],
                                   tools=None, max_tokens=50, thinking=None,
                                   reasoning_effort="xhigh")
        self.assertEqual(resp.text, "ок")


class TheHandHasProvenance(unittest.TestCase):
    """Писатель llm.json обязан вернуть песочницу как было: шардовая песочница ОБЩАЯ
    на процесс, и созданный здесь файл переключил бы соседей с env-фолбэка на файл —
    класс «гейт краснеет от состава шарда» (у пин-тестов кадра он уже стоил дня)."""

    def setUp(self):
        self._path = llm.CONFIG_PATH
        self._had = self._path.exists()
        self._bytes = self._path.read_bytes() if self._had else b""
        self.addCleanup(self._restore_config)

    def _restore_config(self):
        if self._had:
            self._path.write_bytes(self._bytes)
        elif self._path.exists():
            self._path.unlink()
        llm._CACHE.update(mtime=None, cfg=None)

    def test_a_change_without_a_why_is_refused(self):
        res = brain.set_reasoning("voice", "high")
        self.assertFalse(res["ok"])
        self.assertIn("зачем", res["error"])

    def test_junk_tier_names_the_dictionary(self):
        res = brain.set_reasoning("voice", "turbo", why="проба")
        self.assertFalse(res["ok"])
        self.assertIn("minimal", res["error"])

    def test_set_show_and_clear_round_trip(self):
        with mock.patch.object(agent, "_is_sovereign_actor", return_value=True):
            out = agent.tool_switch_brain(action="reasoning", role="voice",
                                          effort="medium", why="проба глубины")
            self.assertIn("«medium»", out)
            self.assertIn("кэш префикса не рвётся", out)
            status = agent.tool_switch_brain(action="status")
            self.assertIn("рассуждение medium", status)
            out2 = agent.tool_switch_brain(action="reasoning", role="voice",
                                           effort="", why="снимаю после пробы")
            self.assertIn("погашено", out2)

    def test_the_lever_is_a_principal_door_not_a_public_one(self):
        with mock.patch.object(agent, "_is_sovereign_actor", return_value=False), \
                mock.patch.object(agent, "_active_scope", return_value="group"):
            out = agent.tool_switch_brain(action="reasoning", role="voice",
                                          effort="high", why="чужая просьба")
        self.assertIn("Не отсюда", out)


if __name__ == "__main__":
    unittest.main()
