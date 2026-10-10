"""No agent proof, watcher or verdict can gate an application update."""
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
DESK=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(DESK),str(DESK/'localharness')]
import updates
from deskd import control

class NoTrial(unittest.TestCase):
    def test_tool_funnel_is_untouched_and_no_verdict_is_written(self):
        with tempfile.TemporaryDirectory() as folder:
            tree=Path(folder);calls=[]
            def funnel(name,impl,args):calls.append(name);return impl(**args)
            agent=types.SimpleNamespace(TOOL_IMPL={},BASE_TOOLS=[],HAND_PURPOSE={},_call_tool_with_ceiling=funnel)
            with mock.patch.object(updates,'on_server',return_value=False):updates.install(agent,tree,{})
            self.assertIs(agent._call_tool_with_ceiling,funnel)
            self.assertEqual(agent.BASE_TOOLS[0]['input_schema']['properties']['action']['enum'],['status'])
            ctl=tree/'memory/.control';ctl.mkdir(parents=True)
            (ctl/control.UPDATE_RECEIPT).write_text(json.dumps({'id':'old','desktop':True,'state':'trial','trial':{'key':'old-key'}}))
            hand=agent.TOOL_IMPL[updates.TOOL_NAME]
            for action in ('accept','reject'):
                self.assertIn('приёмки приложения больше нет',hand(action=action,report='some deeds'))
            self.assertFalse((ctl/control.UPDATE_VERDICT).exists())
            self.assertEqual(hand(action='status').count('отменено'),1)
    def test_open_legacy_trial_never_starts_a_detached_watcher(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'backups').mkdir();(root/'backups/update-trial.json').write_text('{"phase":"trial"}')
            spawn=mock.Mock()
            for now in (1000,1200,5000):self.assertEqual(updates.ensure_watcher(root,root/'data',now=now,spawn=spawn),'')
            spawn.assert_not_called()
    def test_done_notice_requests_inspection_without_an_acceptance_gate(self):
        text=updates.report_note({'state':'done','from_version':'1.3.7','to_version':'1.5.2'},owner='Owner')
        self.assertIn('Осмотри новую версию',text);self.assertIn('не ждёт твоей приёмки',text)

if __name__=='__main__':unittest.main(verbosity=2)
