# -*- coding: utf-8 -*-
"""Результаты руки `computer` на Mac — словами Mac (25.09, D2).

Схему тула правит `describe_for_mac` (стенд t_tool_text_mac); здесь — то, что модель
читает в ОТВЕТАХ руки: строка состояния тела и отказы маршрутов. У людей на Mac из
«Windows body … Session 0» рождалось «UI Automation на Mac не реализовано» — AX
реализован, врали слова.
"""
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))

import body  # noqa: E402


class ResultWords(unittest.TestCase):
    def test_state_line_and_route_refusals_speak_mac(self):
        line = body.mac_result_text("Windows body 0.1.0: interactive / high; praxis.body.v2; interactive=ready")
        self.assertTrue(line.startswith("Body (macOS) 0.1.0"), line)
        refusal = body.mac_result_text("click: нужен execution=interactive (desktop недоступен из Session 0)")
        self.assertNotIn("Session 0", refusal)
        self.assertIn("экран сейчас не у этой сессии", refusal)
        note = body.mac_result_text("for your own eyes use computer action=read_window (UI Automation tree with names, values and centre coordinates)")
        self.assertNotIn("UI Automation", note)
        self.assertIn("Accessibility tree", note)

    def test_idempotent_and_leaves_other_text_alone(self):
        text = "Windows body: offline (timeout)"
        once = body.mac_result_text(text)
        self.assertEqual(once, body.mac_result_text(once))
        self.assertEqual(body.mac_result_text("Отправлено → Мира"), "Отправлено → Мира")

    def test_state_line_is_wrapped_once_and_by_module_name(self):
        calls = []

        def state_line():
            calls.append(1)
            return "Windows body: controller token не настроен"

        client = types.SimpleNamespace(state_line=state_line)
        agent = types.SimpleNamespace(body_client=client)
        self.assertTrue(body.results_for_mac(agent))
        self.assertFalse(body.results_for_mac(agent), "второй раз обёртка не удваивается")
        self.assertEqual(agent.body_client.state_line(), "Body (macOS): controller token не настроен")
        self.assertEqual(calls, [1])
        self.assertIs(agent.body_client.state_line.__wrapped__, state_line)


if __name__ == "__main__":
    unittest.main()
