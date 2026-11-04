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

    def _worker_outcome(self,end_turn):
        from unittest import mock
        from types import SimpleNamespace
        import forge_worker
        import test_forge_wave2_server as fixtures
        fixture=fixtures.ServerWave2();fixture.setUp()
        try:
            directory,request=fixture.unit(status='starting',max_iters=1)
            finished=[]
            def chat(*args,**kwargs):
                if end_turn or kwargs.get('tools') is None:
                    return SimpleNamespace(model='fixture',stop_reason='end_turn',text='observed partial work',blocks=[],usage={})
                return SimpleNamespace(model='fixture',stop_reason='tool_use',text='',usage={},blocks=[
                    {'type':'tool_use','id':'one','name':'inspect','input':{'action':'read','path':'code.py'}}])
            with mock.patch.object(forge_worker.llm,'chat',side_effect=chat),mock.patch.object(forge_worker,'_system',return_value='fixture'), \
                 mock.patch.object(forge,'_event',side_effect=lambda *a,**k:finished.append((a,k))),mock.patch.object(forge,'emit_unit_event'):
                self.assertEqual(forge_worker.run(directory/'request.json'),0)
            return json.loads((directory/'result.json').read_text()),finished
        finally:fixture.doCleanups()

    def test_worker_distinguishes_two_ways_to_end(self):
        for end_turn,expected in ((True,'done'),(False,'stalled')):
            result,_=self._worker_outcome(end_turn)
            self.assertEqual(result['status'],expected)
            self.assertEqual(result['iters_used'],1)
            self.assertIn('tool_errors',result)

    def test_worker_event_does_not_say_done_on_exhaustion(self):
        result,events=self._worker_outcome(False)
        summaries=[kw['summary'] for args,kw in events if args[1]=='agent_finished']
        self.assertTrue(summaries)
        self.assertIn('stalled',summaries[-1])
        self.assertNotIn(' done',summaries[-1])

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
