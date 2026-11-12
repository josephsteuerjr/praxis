"""Public Computer arguments reach Body process lifetime, independent of RPC wait."""
import ast
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'helene/core')]
import body_client


def public_hand():
    # Execute the actual public function without starting the full agent runtime.
    source = ast.parse((ROOT / 'helene/core/agent.py').read_text(encoding='utf-8'))
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'tool_computer')
    scope = {'body_client': body_client, 'json': json, '_computer_allowed': lambda _: True,
             '_is_sovereign_actor': lambda: True,
             '_COMPUTER_ACTION_SCOPES': dict.fromkeys(('run', 'poll', 'stop', 'read_window', 'activate'), 'computer.process')}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<actual Computer hand>', 'exec'), scope)
    return scope['tool_computer']


class Deadline(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(sys.modules, {'computer_inventory': types.ModuleType('computer_inventory')})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.hand = public_hand()

    def test_requested_lifetime_is_sent_in_body_payload_not_rpc_timeout(self):
        for requested, seconds in ((5000, 5), (1, 1), (1001, 2), (0, 0), (None, 0)):
            with self.subTest(timeout_ms=requested), mock.patch.object(body_client, 'call',
                    return_value={'ok': True, 'operation_id': 'one-operation', 'status': 'starting'}) as call:
                args = {} if requested is None else {'timeout_ms': requested}
                receipt = json.loads(self.hand('run', command='fixture', **args))
                self.assertEqual(receipt['process_timeout_ms'], seconds * 1000)
                call.assert_called_once_with('process.start', {
                    'shell': 'power_shell', 'args': [], 'env': {}, 'timeout_s': seconds, 'command': 'fixture'},
                    execution='interactive', request_id='', operation_id='', timeout=60)

    def test_invalid_deadline_never_dispatches_and_poll_stop_keep_operation(self):
        with mock.patch.object(body_client, 'call', return_value={'ok': True}) as call:
            for value in (-1, '5000', 1.5, True):
                self.assertIn('не запускалась', self.hand('run', command='fixture', timeout_ms=value))
            call.assert_not_called()
            self.hand('poll', operation_id='same-operation')
            self.assertEqual(call.call_args.args[:2], ('process.status', {'operation_id': 'same-operation', 'tail': 16000}))
            self.hand('stop', operation_id='same-operation')
            self.assertEqual(call.call_args.args[:2], ('process.cancel', {'operation_id': 'same-operation'}))

    def test_read_text_limit_and_existing_ui_default_reach_body(self):
        with mock.patch.object(body_client, 'desktop_window_read', return_value={}) as read, \
                mock.patch.object(body_client, 'format_window_read', return_value='read'):
            self.assertEqual(self.hand('read_window', max_text_chars=14000), 'read')
            self.assertEqual(read.call_args.kwargs['max_text_chars'], 14000)
            self.assertEqual(read.call_args.kwargs['timeout_ms'], 1500)


if __name__ == '__main__':
    unittest.main()
