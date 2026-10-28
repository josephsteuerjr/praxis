"""Real agent -> installed integration -> durable source review after self-edit."""
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]
os.environ['PRAXIS_TEST']='1'
sys.path[:0]=[str(ROOT/'desk'),str(ROOT/'helene/core'),str(ROOT/'praxis')]
import _sandbox
_sandbox.activate_if_testing()
import agent
from localharness import self_review_integration as integration


class InstalledCallback(unittest.TestCase):
    def test_actual_agent_autocommit_reaches_installed_source_review(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root=Path(tmp);tree=root/'home';code=root/'code';program=root/'program'
            tree.mkdir();code.mkdir();(program/'app').mkdir(parents=True)
            (code/'agent.py').write_text('changed = True\n')
            (program/'app/runner.py').write_text('runner = True\n')
            config=root/'helene.json';config.write_text(json.dumps({'self_review':{'depth':'quick','after_self_edit':True}}))
            for name,value in [('SELF_EDIT_REVIEW_HOOK',None),('BASE_TOOLS',list(agent.BASE_TOOLS)),
                               ('TOOL_IMPL',dict(agent.TOOL_IMPL)),('HAND_PURPOSE',dict(agent.HAND_PURPOSE))]:
                stack.enter_context(mock.patch.object(agent,name,value))
            stack.enter_context(mock.patch.object(agent.selfgit,'changed_paths',return_value=['agent.py']))
            snapshot=stack.enter_context(mock.patch.object(agent.selfgit,'snapshot_paths',return_value='commit-sha'))
            stack.enter_context(mock.patch.object(agent,'tool_journal'))
            legacy=stack.enter_context(mock.patch.object(agent.immune,'enqueue'))
            runtime=integration.Integration(tree,code,program,types.SimpleNamespace(SELF_REVIEW_API=1),config,agent).install()
            agent._autocommit_self_edit({})
            records=runtime.service.status()
            self.assertEqual(len(records),1,'Real self-edit did not reach installed review callback')
            self.assertEqual(records[0]['trigger'],'after_self_edit')
            self.assertEqual(records[0]['operations'],['commit-sha'])
            self.assertTrue(integration.status(tree)['runtime']['self_edit_supported'])
            snapshot.assert_called_once();legacy.assert_not_called()


if __name__=='__main__':unittest.main()
