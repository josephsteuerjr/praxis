"""Толкование аппетитов едет в кадр с датой и фактическим голосом, а не как текущий профиль.

24.09 она ответила в чате «reasoning у меня стоит medium (Terra)», хотя голос был glm-5.3 на low:
в каждом кадре лежало её толкование от 13.09 «Применяю профиль chat (gpt-5.6-terra, medium)»
без даты, рядом с настоящим мозгом.

Запуск:  python praxis_test.py test_appetite_view_2409 -v
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

import agent

TERRA = {"text": "Приняла просьбу как смену разговорного профиля на Terra medium. "
                 "Применяю профиль chat (gpt-5.6-terra, medium).",
         "plan": {"windows": False, "note": "Terra medium в обычном чате"},
         "ts": time.time() - 10 * 86400}


def _brain(model: str, effort: str = "low"):
    return mock.patch.multiple(
        agent.llm, role_model=mock.Mock(return_value=model),
        _config=mock.Mock(return_value={"roles": {"voice": {"reasoning_effort": effort}}}))


class AppetiteInterpretationViewTests(unittest.TestCase):
    def test_other_brain_in_the_record_is_named_as_not_in_force(self):
        with _brain("glm-5.3"):
            view = agent._appetite_interpretation_view(TERRA)
        self.assertEqual(view["text"], TERRA["text"])          # её текст не переписан
        self.assertIn("10 дн. назад", view["recorded"])
        self.assertEqual(view["voice_now"], "glm-5.3, reasoning low")
        self.assertIn("НЕ действует", view["stale"])
        self.assertIn("gpt-5.6-terra", view["stale"])

    def test_record_that_matches_the_voice_is_not_called_stale(self):
        with _brain("gpt-5.6-terra", "medium"):
            view = agent._appetite_interpretation_view(TERRA)
        self.assertNotIn("stale", view)
        self.assertEqual(view["voice_now"], "gpt-5.6-terra, reasoning medium")

    def test_record_without_a_model_gets_only_date_and_voice(self):
        plain = {"text": "Держу considerate, без новых окон.", "plan": {}, "ts": time.time()}
        with _brain("glm-5.3"):
            view = agent._appetite_interpretation_view(plain)
        self.assertNotIn("stale", view)
        self.assertIn("0 дн. назад", view["recorded"])

    def test_the_frame_builder_uses_the_view(self):
        import inspect
        self.assertIn('add("appetite_interpretation", _appetite_interpretation_view(interpretation))',
                      inspect.getsource(agent))


if __name__ == "__main__":
    unittest.main()
