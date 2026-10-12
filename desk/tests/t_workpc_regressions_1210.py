"""Work PC failures: client panic, malformed computer calls, imported dialogue."""
import ast
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK/'localharness'), str(DESK)]
import body
import dialogue_continuity as continuity
import emergency
import owner_context
import owner_stop
import update_inspection
from tests import t_body


class WorkPC(unittest.TestCase):
    def test_client_stop_without_service_or_admin_never_spawns_or_calls_broker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cfg = root/'helene.json'
            cfg.write_text(json.dumps({'tree':'data','installed':{'service':False}}))
            (root/'helene.exe').write_bytes(b'not-executed')
            client = root/'user-state/owner-stop.json'
            with patch.object(emergency, 'WINDOWS', True), patch.object(owner_stop, 'client_file', return_value=client), \
                    patch.object(owner_stop, 'stopped', side_effect=lambda: client.exists()), \
                    patch.object(emergency.subprocess, 'run') as spawn, patch('broker.call_service') as pipe:
                result = emergency.request(cfg, via='phone')
                initial = client.read_bytes()
                repeated = emergency.request(cfg, via='telegram')
            self.assertEqual(result['scope'], 'client'); self.assertTrue(repeated['latched'])
            self.assertEqual(initial, client.read_bytes()); spawn.assert_not_called(); pipe.assert_not_called()

    def test_reported_body_argument_kind_is_refused_without_executing_the_body(self):
        source = ast.parse((DESK.parent/'helene/core/agent.py').read_text(encoding='utf8'))
        node = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'tool_computer')
        namespace = {}; exec(compile(ast.Module(body=[node],type_ignores=[]), 'agent.py', 'exec'), namespace)
        ground = t_body.Ground(t_body._cfg()); self.addCleanup(ground.close)
        agent = t_body._fake_agent([]); agent.TOOL_IMPL['computer'] = namespace['tool_computer']
        with patch.object(body, '_BODY', Mock()), patch.dict(body.STATE, {'available':True,'connected':True}), \
                patch.dict(sys.modules, {'body_client':t_body._fake_body_client()}):
            body.install(agent, ground.tree, t_body._cfg(), config_path=ground.config)
            result = agent.TOOL_IMPL['computer'](action='read_window', kind='uia', hwnd='123')
        self.assertIn('kind', result); self.assertIn('Действие не выполнено', result)

    def test_imported_recent_answer_recovers_roles_instead_of_only_initial_greeting(self):
        hot = [{'direction':'out','line':'Initial hello','actor':'Jarvis','ts':1},
               {'direction':'in','line':'Current request','actor':'Owner','ts':5}]
        canonical = [dict(outgoing=True,text='Initial hello',timestamp='2026-10-08T10:00:00Z'),
            dict(outgoing=False,text='Do the task',timestamp='2026-10-12T10:00:00Z'),
            dict(outgoing=True,text='Task already done',timestamp='2026-10-12T10:01:00Z'),
            dict(outgoing=False,text='Current request',timestamp='2026-10-12T10:02:00Z')]
        rows = continuity.reconcile(hot, [json.dumps(r) for r in canonical])
        self.assertEqual(rows[-2]['direction'], 'out'); self.assertEqual(rows[-2]['line'], 'Task already done')
        self.assertEqual(hot[0]['line'], 'Initial hello')

    def test_retired_birth_directive_stays_out_of_later_model_turns(self):
        birth = {'direction':'in','actor':'Hélène','line':'Hélène: Это твой первый запуск здесь. Поздоровайся.'}
        owner = dict(birth, actor='Owner')
        self.assertEqual(continuity.reconcile([birth, owner], []), [owner])
        self.assertEqual(continuity.reconcile([birth], [], birth=True), [birth])

    def test_policy_migration_recovers_after_crash_without_duplicate_receipt(self):
        life = types.SimpleNamespace(hot_bounds=lambda p:(150,250,300,40000),tape_chars_for=lambda p:5500)
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp); actual = update_inspection.write
            def interrupted(path, value):
                if Path(path).name == 'context-policy.json': raise OSError('simulated interrupted write')
                actual(path, value)
            with patch.object(update_inspection, 'write', side_effect=interrupted):
                with self.assertRaises(OSError): owner_context.configure(life, {}, tree=tree)
            owner_context.configure(life, {}, tree=tree)
            events = (tree/'memory/context-policy.events.jsonl').read_text(encoding='utf8').splitlines()
            self.assertEqual(len(events), 1)
            event = json.loads(events[0]); self.assertEqual(event['before']['tape_chars'],5500)
            self.assertTrue(event['author'].startswith('deploy:'))
            self.assertEqual(life.hot_bounds('window')[2],600)
            self.assertFalse((tree/'memory/.context-policy-change.json').exists())
            self.assertFalse(update_inspection.read(tree/'memory/.state/context-policy-notice.json')['done'])

    def test_user_stop_and_protected_machine_stop_are_both_observed(self):
        with tempfile.TemporaryDirectory() as tmp:
            machine = Path(tmp)/'machine.json'; client = Path(tmp)/'client.json'
            with patch.object(owner_stop,'seed_file',return_value=machine), \
                    patch.object(owner_stop,'client_file',return_value=client):
                if sys.platform != 'win32': self.skipTest('Windows dual-latch contract')
                self.assertFalse(owner_stop.stopped()); client.write_text('{}')
                self.assertTrue(owner_stop.stopped()); machine.write_text('{}'); client.unlink()
                self.assertTrue(owner_stop.stopped())


if __name__ == '__main__': unittest.main()
