# -*- coding: utf-8 -*-
"""Приёмка: молчаливых срезов не осталось, и потолок не называется завершением.

Проверяется СВОЙСТВО: «усечение обязано назвать себя» и «исход обязан отличаться от
завершения», а не конкретные слова. Поэтому тесты ищут ЧИСЛА (сколько показано из
скольких) и РАЗЛИЧИЕ статусов, а не дословные формулировки.
"""
import json
import os
import unittest

import agent
import forge


class CutsSayTheirName(unittest.TestCase):
    """Срез в её кадре и в её руке обязан называть себя."""

    def _states(self, n):
        return [{"id": "desire-%03d" % i, "stage": "want", "status": "active",
                 "statement": "намерение %d" % i, "next_move": "шаг %d" % i,
                 "timeline": [{"at": "t%d" % k} for k in range(9)]}
                for i in range(n)]

    def _block(self, n, limit=10):
        class _Ledger:
            def __init__(self, *a, **kw):
                pass

            def list(self, statuses=()):
                return states
        states = self._states(n)
        old_ledger, old_elig = agent.desires.DesireLedger, \
            agent.memory_provenance.desire_state_normative_eligible
        agent.desires.DesireLedger = _Ledger
        agent.memory_provenance.desire_state_normative_eligible = lambda s: True
        try:
            return agent._active_desires_block(limit)
        finally:
            agent.desires.DesireLedger = old_ledger
            agent.memory_provenance.desire_state_normative_eligible = old_elig

    def test_frame_names_the_cut_with_both_numbers(self):
        block = self._block(27)
        self.assertIn("27", block, "кадр не назвал, сколько намерений всего")
        self.assertIn("10", block, "кадр не назвал, сколько показано")
        self.assertIn("manage_desire", block, "не названа рука, которой видно остальное")

    def test_no_cut_no_claim(self):
        """Когда резать нечего, приписка не появляется: лишнее слово тоже неправда."""
        block = self._block(3)
        self.assertNotIn("из", block.split("Причинную")[0].split("\n")[-1],
                         "обещает срез там, где его нет")

    def test_hand_names_both_of_its_cuts(self):
        """У руки manage_desire срезов ДВА: по числу желаний и по длине ленты."""
        src = open(agent.__file__, encoding="utf-8").read()
        head = src[src.index("if action in {\"list\", \"status\", \"\"}:"):][:2000]
        self.assertIn("timeline_срез", head, "срез ленты остался молчаливым")
        self.assertIn("_срез", head, "срез по числу желаний остался молчаливым")


class CeilingIsNotCompletion(unittest.TestCase):
    """Исчерпание потолка ходов — не «сделано» и не «упал»."""

    def test_worker_distinguishes_two_ways_to_end(self):
        src = open(os.path.join(os.path.dirname(agent.__file__), "forge_worker.py"),
                   encoding="utf-8").read()
        self.assertIn("stopped_himself", src, "воркер не различает исходы цикла")
        self.assertIn('"status": "done" if stopped_himself else "stalled"', src,
                      "статус по-прежнему безусловно done")
        self.assertIn("iters_used", src, "не записано, сколько ходов израсходовано")
        self.assertIn("tool_errors", src, "не видно, сколько рук упало внутри прогона")

    def test_worker_event_does_not_say_done_on_exhaustion(self):
        src = open(os.path.join(os.path.dirname(agent.__file__), "forge_worker.py"),
                   encoding="utf-8").read()
        chunk = src[src.index("agent_finished"):][:400]
        self.assertIn("исчерпал", chunk, "событие всё ещё сообщает о завершении")

    def test_her_invitation_has_three_outcomes_not_two(self):
        items = [{"priority": "normal", "status": "done", "goal": "g", "task_id": "t",
                  "summary": "s"},
                 {"priority": "normal", "status": "stalled", "goal": "g", "task_id": "t",
                  "summary": "s"},
                 {"priority": "normal", "status": "error", "goal": "g", "task_id": "t",
                  "summary": "s"}]
        text = forge.wake_invitation(items)
        self.assertIn("закончил", text)
        self.assertIn("потолок", text, "исчерпание не отличено от завершения")
        self.assertIn("упал (error)", text, "настоящее падение перестало называться падением")
        self.assertNotIn("упал (stalled)", text,
                         "исчерпание названо падением — это вторая неправда вместо первой")


if __name__ == "__main__":
    unittest.main(verbosity=2)
