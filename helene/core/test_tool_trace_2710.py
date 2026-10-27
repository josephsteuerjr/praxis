"""Observed execution must differ from requested input without changing tool behavior."""
import contextlib
import json
import os
os.environ['PRAXIS_TEST'] = '1'
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import agent


class ToolTrace(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.manager = mock.Mock()
        self.stack.enter_context(mock.patch.object(agent, '_runs', return_value=self.manager))
        token = agent._TOOL_EXECUTION.set({'run_id': 'run-test', 'call_id': 'call-test', 'tool': 'shell'})
        self.addCleanup(agent._TOOL_EXECUTION.reset, token)

    def events(self, kind):
        return [call.kwargs for call in self.manager.append_event.call_args_list if call.args[1] == kind]

    def test_dispatch_is_observed_in_actual_worker_not_at_argument_admission(self):
        threads = []
        def action(command):
            threads.append(threading.get_ident())
            self.assertEqual(len(self.events('tool_dispatch')), 1)
            return command
        with mock.patch.object(agent, 'TOOL_CEILING_SEC', 2):
            answer = agent._call_tool_with_ceiling_inner('shell', action, {})
            self.assertIn('НЕ выполнен', answer)
            self.assertFalse(self.events('tool_dispatch'))
            self.assertEqual(agent._call_tool_with_ceiling_inner('shell', action, {'command': 'hello'}), 'hello')
        self.assertNotEqual(threads[0], threading.get_ident())
        self.assertEqual(self.events('tool_dispatch')[0]['receipt']['status'], 'entered')

    def test_shell_receipt_records_actual_wrapped_argv_and_failed_launch_separately(self):
        for target, name, value in (
            (agent.stewardship, 'check', None), (agent.stewardship, 'touches_path_mentioned', False),
            (agent, '_core_edit_check_on', False), (agent.selfgit, 'safety_point', None),
            (agent, '_pre_shell_dirt', set()), (agent, '_autocommit_self_edit', None),
            (agent, '_shell_workdir', Path('/fixture-home'))):
            self.stack.enter_context(mock.patch.object(target, name, return_value=value))
        actual = []
        def run(argv, **kwargs):
            actual.append((argv, kwargs['cwd']))
            return subprocess.CompletedProcess(argv, 7, b'actual output')
        with mock.patch.object(agent.subprocess, 'run', side_effect=run):
            self.assertEqual(agent.tool_shell('echo requested'), 'actual output')
        receipt = self.events('tool_execution')[0]['receipt']
        self.assertEqual(receipt['argv'], actual[0][0])
        self.assertNotEqual(receipt['argv'][-1], 'echo requested', 'home wrapper is real harness behavior')
        self.assertEqual(receipt['exit_code'], 7)
        self.assertEqual(receipt['status'], 'returned')
        with mock.patch.object(agent.subprocess, 'run', side_effect=FileNotFoundError('bash absent')):
            agent.tool_shell('echo requested')
        failed = self.events('tool_execution')[-1]['receipt']
        self.assertEqual(failed['status'], 'launch_failed')
        self.assertNotIn('argv', failed, 'unlaunched process has only requested argv')

    def test_trace_failure_cannot_prevent_or_repeat_an_action(self):
        self.manager.append_event.side_effect = OSError('display storage unavailable')
        action = mock.Mock(return_value='done')
        with mock.patch.object(agent, 'TOOL_CEILING_SEC', 0):
            self.assertEqual(agent._call_tool_with_ceiling_inner('shell', action, {'command':'hello'}), 'done')
        action.assert_called_once_with(command='hello')

    def test_schema_is_the_offered_definition_and_wrapped_critical_input_stays_redacted(self):
        definition = {'name':'call', 'description':'offered in this iteration', 'input_schema':{'type':'object'}}
        raw = {'name':'call', 'input':{'name':'telegram_account', 'args_json':'secret-otp-471928'}}
        safe = {'request':'auth.SignInRequest', 'params':{'_praxis_redacted':{'schema':'safe'}}}
        with mock.patch.object(agent, '_critical_telegram_tool_input', return_value=True), \
             mock.patch.object(agent, '_durable_tool_input', return_value=safe):
            projection = agent._tool_request_projection(raw, [definition], 'telegram_account', {'params':{'otp':'secret-otp-471928'}})
        self.assertNotIn('secret-otp-471928', json.dumps(projection))
        self.assertEqual(projection['definition'], definition)
        definition['description'] = 'later catalog mutation'
        self.assertEqual(projection['definition']['description'], 'offered in this iteration')
        self.assertIn('secret-otp-471928', raw['input']['args_json'], 'live input remains intact')


if __name__ == '__main__': unittest.main()
