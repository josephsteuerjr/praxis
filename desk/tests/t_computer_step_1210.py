"""One model tool call for guarded input and observation; no real desktop input."""
import ast
import json
from pathlib import Path
import sys
import types
import unittest

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "localharness"))
import computer_steps


class Step(unittest.TestCase):
    def setup_hand(self, platform="win32", allowed=True, results=None):
        calls=[]
        def backend(capability, payload, **kwargs):
            calls.append((capability,payload,kwargs))
            return (results or {}).get(capability,{"ok":True,"observed":False,"observation":{"error":"window unavailable"}})
        agent=types.SimpleNamespace(TOOL_IMPL={"computer":lambda **kwargs:"original"},
            BASE_TOOLS=[{"name":"computer","description":"Read and act","params":{"action":{"type":"string","enum":["read_window"]}}}],
            _computer_allowed=lambda scope: allowed, _computer_actor=lambda:'owner')
        self.assertTrue(computer_steps.install(agent,backend,platform=platform))
        return agent,calls

    def test_windows_combines_guarded_input_and_read_in_one_native_call(self):
        agent,calls=self.setup_hand()
        result=json.loads(agent.TOOL_IMPL['computer'](action='step',idempotency_key='test-intent',hwnd='0x123',expected_pid=42,events=[{'type':'key','key':'End'}],settle_ms=0))
        self.assertTrue(result['ok']);self.assertFalse(result['observed'])
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][0],'desktop.input.perform_and_read')
        self.assertEqual(calls[0][1]['input']['expected_foreground'],'0x123')
        self.assertEqual(calls[0][1]['input']['expected_pid'],42)
        self.assertEqual(calls[0][1]['read']['hwnd'],'0x123')

    def test_permission_target_and_invalid_delay_refuse_before_input(self):
        agent,calls=self.setup_hand(allowed=False)
        agent.TOOL_IMPL['computer'](action='step',idempotency_key='test-intent',hwnd='0x123',events=[{'kind':'key','key':'End'}])
        self.assertFalse(calls)
        agent._computer_allowed=lambda scope:True
        for kwargs in ({'events':[{}]}, {'hwnd':'0x123','events':[{}],'settle_ms':-1}, {'hwnd':'0x123','events':[{}],'execution':'system'}):
            agent.TOOL_IMPL['computer'](action='step',idempotency_key='test-intent',**kwargs)
        self.assertFalse(calls)

    def test_posix_observation_failure_does_not_repeat_or_hide_successful_input(self):
        for platform in ('darwin','linux'):
            agent,calls=self.setup_hand(platform,results={'desktop.input.perform':{'ok':True},'desktop.window.read':{'ok':False,'error':'unavailable'}})
            result=json.loads(agent.TOOL_IMPL['computer'](action='step',idempotency_key='test-intent',hwnd='0x123',events=[{'kind':'key','key':'End'}],settle_ms=0))
            self.assertTrue(result['ok']);self.assertFalse(result['observed'])
            self.assertEqual([c[0] for c in calls],['desktop.input.perform','desktop.window.read'])

    def test_posix_failed_input_never_observes_or_retries(self):
        agent,calls=self.setup_hand('linux',results={'desktop.input.perform':{'ok':False}})
        result=json.loads(agent.TOOL_IMPL['computer'](action='step',idempotency_key='test-intent',hwnd='0x123',events=[{}],settle_ms=0))
        self.assertFalse(result['ok']);self.assertEqual(len(calls),1)

    def test_existing_actions_and_idempotent_registration_are_preserved(self):
        agent,calls=self.setup_hand()
        self.assertEqual(agent.TOOL_IMPL['computer'](action='read_window'),'original')
        before=agent.BASE_TOOLS[0]['description']
        self.assertFalse(computer_steps.install(agent,lambda *a:None,platform='win32'))
        self.assertEqual(agent.BASE_TOOLS[0]['description'],before)
        self.assertIn('step',agent.BASE_TOOLS[0]['params']['action']['enum'])
        self.assertFalse(calls)

    def test_manual_retry_uses_exact_native_ids_and_unknown_field_never_acts(self):
        agent,calls=self.setup_hand()
        ask={'action':'step','idempotency_key':'same-intent','hwnd':'0x123',
             'events':[{'type':'key','key':'End'}],'settle_ms':0}
        agent.TOOL_IMPL['computer'](**ask); agent.TOOL_IMPL['computer'](**ask)
        self.assertEqual(calls[0][2],calls[1][2])
        self.assertEqual(calls[0][1],calls[1][1])
        agent.TOOL_IMPL['computer'](**dict(ask,kind='uia'))
        agent.TOOL_IMPL['computer'](**dict(ask,idempotency_key=''))
        self.assertEqual(len(calls),2)

    def test_core_recovery_classifies_step_as_a_mutation(self):
        core=DESK.parent/'helene/core/agent.py'
        parsed=ast.parse(core.read_text(encoding='utf8'))
        node=next(n for n in parsed.body if isinstance(n,ast.FunctionDef) and n.name=='_tool_has_side_effect')
        scope={'_READ_ONLY_TOOLS':frozenset()}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'agent.py','exec'),scope)
        self.assertTrue(scope['_tool_has_side_effect']('computer',{'action':'step'}))


if __name__=='__main__':unittest.main(verbosity=2)
