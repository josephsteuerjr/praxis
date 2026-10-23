"""Projected configured accounts -> actual switch_brain/profile, not a fake brain."""
import os,sys,json,copy,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];os.environ['PRAXIS_TEST']='1'
sys.path[:0]=[str(ROOT/'desk'),str(ROOT/'helene/core'),str(ROOT/'praxis')]
import _sandbox
_sandbox.activate_if_testing()
import llm,brain,agent
from localharness import boot
class Endpoints(unittest.TestCase):
    def configure(self,protocol='openai'):
        product={'model':{'framework':protocol,'base_url':'https://main.invalid/v1','key':'MAIN-TEST','model':'main','fallback_framework':protocol,'fallback_base_url':'https://spare.invalid/v1','fallback_key':'SPARE-TEST','fallback_model':'spare'},'evaluator':{'framework':protocol,'base_url':'https://judge.invalid/v1','key':'JUDGE-TEST','model':'judge'}}
        llm.save_config(boot._brain_config(product))
        return copy.deepcopy(llm._config())
    def setUp(self):
        self.stack=__import__('contextlib').ExitStack();self.addCleanup(self.stack.close)
        for obj,name,value in [(llm,'_available_models',lambda fw:[]),(llm,'ping',lambda role:(True,'')),(brain,'_journal',lambda *a:None),(brain,'_spine',lambda *a:None),(agent,'_is_sovereign_actor',lambda:False),(agent,'_active_scope',lambda:'owner')]:self.stack.enter_context(patch.object(obj,name,value))
    def test_switch_tool_keeps_previous_exact_account_both_protocols(self):
        for protocol in ('openai','anthropic'):
            before=self.configure(protocol)
            answer=agent.tool_switch_brain('switch',role='voice',model='spare',why='fixture')
            self.assertIn('voice_fallback/spare',answer)
            after=llm._config();r=after['roles']['voice']
            self.assertEqual(r['framework'],'voice_fallback');self.assertEqual(r['fallback_framework'],'voice_primary');self.assertEqual(r['fallback_model'],'main')
            self.assertEqual(after['frameworks'],before['frameworks']);self.assertEqual(after['roles']['evaluator'],before['roles']['evaluator'])
    def test_profile_tool_preserves_exact_previous_and_effort(self):
        self.configure();target=brain.PROFILES['deep-review']['model']
        llm.update_config({'roles':{'evaluator':{'model':target}}})
        answer=agent.tool_switch_brain('profile',profile='deep-review',why='fixture')
        self.assertIn('применён',answer);r=llm._config()['roles']['voice']
        self.assertEqual(r['framework'],'evaluator_primary');self.assertEqual(r['fallback_framework'],'voice_primary');self.assertEqual(r['reasoning_effort'],'high')
    def test_same_endpoint_and_ping_rollback(self):
        self.configure()
        before=copy.deepcopy(llm._config()['roles']['voice'])
        with patch.object(llm,'_available_models',side_effect=lambda fw:['next'] if fw=='voice_primary' else []):
            self.assertTrue(brain.switch('voice','next',why='fixture')['ok'])
        r=llm._config()['roles']['voice'];self.assertEqual(r['framework'],'voice_primary');self.assertEqual(r['fallback_framework'],before['fallback_framework'])
        before=copy.deepcopy(r)
        with patch.object(llm,'ping',return_value=(False,'fixture refusal')):
            self.assertFalse(brain.switch('voice','spare',why='fixture')['ok'])
        self.assertEqual(llm._config()['roles']['voice'],before)
    def test_ambiguous_accounts_never_choose_insertion_order(self):
        self.configure();cfg=llm._config();cfg['frameworks']['evaluator_primary']['base_url']=cfg['frameworks']['voice_fallback']['base_url'];llm.save_config(cfg)
        with patch.object(llm,'_available_models',side_effect=lambda fw:['ambiguous'] if fw in ('voice_fallback','evaluator_primary') else []),patch.object(llm,'ping') as ping:
            before=copy.deepcopy(llm._config());reply=brain.switch('voice','ambiguous',why='fixture')
            self.assertFalse(reply['ok']);self.assertIn('several configured accounts',reply['error']);self.assertEqual(llm._config(),before);ping.assert_not_called()
    def test_catalog_keeps_account_identity_not_credentials(self):
        self.configure();result=brain.catalog()
        self.assertIn('voice_fallback',result['frameworks']);self.assertIn('evaluator_primary',result['frameworks'])
        self.assertEqual(result['roles']['voice']['fallback_framework'],'voice_fallback')
        for key in ('MAIN-TEST','SPARE-TEST','JUDGE-TEST'):self.assertNotIn(key,json.dumps(result))
if __name__=='__main__':unittest.main()
