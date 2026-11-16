"""A run id passed as task_id is named as such; the home scope is never used silently.

Observed with a local model: fs_read/fs_ls/fs_search/python received task_id=run-… copied from
a ResultRef footer and answered only "coding task not found". The same call without task_id
worked. The refusal now names the difference and the home route; no fallback is invented.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent
import forge
import frame_language as fl

RUN = 'run-20261115T195826555382Z-42b69efe'


class TaskIdRunRef(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        state = Path(temp.name)
        self.enterContext(patch.object(forge, 'STATE_DIR', state))
        self.enterContext(patch.object(forge, 'TASKS_DIR', state / 'tasks'))

    def test_run_id_and_unknown_task_refusals_in_every_language(self):
        for language in fl.LANGUAGES:
            with fl.use(language), patch.object(agent, '_home_fs_read',
                                                side_effect=AssertionError('home fallback')), \
                    patch.object(agent.workshop, 'fs_ls', side_effect=AssertionError('home')), \
                    patch.object(agent.workshop, 'fs_search', side_effect=AssertionError('home')):
                expected = fl.text('forge.io.run_id_not_task', task_id=RUN)
                self.assertEqual(str(agent.tool_fs_read('soul/SOUL.md', task_id=RUN)), expected)
                self.assertEqual(str(agent.tool_fs_ls('memory/runs', task_id=RUN)), expected)
                self.assertEqual(str(agent.tool_fs_search('patch', '**/*', task_id=RUN)), expected)
                self.assertEqual(str(forge.inspect('code-absent', action='list')),
                                 fl.text('forge.io.3', v1='code-absent'))
                self.assertIn('read_run_result', expected)
                self.assertIn('task_id', expected)

    def test_result_footer_says_run_is_not_a_task_id(self):
        ref = {'run_id': RUN, 'result_id': 'result-0001', 'path': 'results/x.log',
               'sha256': 'a' * 64, 'size': 3, 'line_count': 1,
               'inline': {'head': 'out', 'tail': '', 'truncated': False}}
        for language in fl.LANGUAGES:
            with fl.use(language):
                text = agent._result_for_model(ref)
                self.assertIn(f'run={RUN}', text, 'the locator itself stays exact')
                self.assertIn(fl.text('result.cursor_hint'), text)


if __name__ == '__main__':
    unittest.main()
