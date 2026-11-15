"""Real helper boundaries, independent languages and disposable persistence."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import brain
import frame_language as fl
import llm
import perception as pp

LANGS = ('ru', 'en', 'fr', 'zh')
RAW = 'Authored Русский 中文 <value>'


class CognitiveLanguage(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.config = self.base/'helene.json'
        self.enterContext(patch.dict(os.environ, HELENE_CONFIG=str(self.config)))
        for mod, name, value in (
            (pp,'STATE_PATH',self.base/'perception.json'),
            (pp,'SKIPS_PATH',self.base/'skips.jsonl'),
            (pp,'JOURNAL_DIR',self.base/'journal'),
            (brain,'JOURNAL_DIR',self.base/'brain-journal')):
            self.enterContext(patch.object(mod,name,value))
        self.enterContext(patch.dict(pp._CACHE, {'mtime': None, 'data': {}}, clear=True))
        self.enterContext(patch.dict(pp._LAST_SKIP, {}, clear=True))
        self.enterContext(patch.dict(pp._AMBIENT, {}, clear=True))
        self.spine=self.enterContext(patch.object(pp,'_spine'))
        self.enterContext(patch.object(brain,'_spine'))
        self.choose('ru','ru')

    def choose(self,ui,frame):
        self.config.write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf-8')

    def test_actual_read_and_validation_helpers_follow_all16_axes(self):
        reply={'active_slot':'primary','configured_slots':2,
               'slots':[{'slot':RAW,'active':True,'cooldown_seconds_left':120}]}
        for ui in LANGS:
            for frame in LANGS:
                with self.subTest(ui=ui,frame=frame):
                    self.choose(ui,frame)
                    with patch.object(brain,'_relay_call',return_value=copy.deepcopy(reply)):
                        result=brain.accounts()
                    self.assertIn(RAW,result)
                    with fl.use(frame):
                        expected=fl.text('helper.brain.accounts.402',p0=brain._slots_text(reply['slots']),p1='primary',p2='2')
                    self.assertEqual(result,expected)
                    bad=pp.set_knob('UNRECOGNIZED_'+RAW,'bad')
                    self.assertFalse(bad['ok']);self.assertIn(RAW,bad['error'])
                    self.assertFalse(pp.STATE_PATH.exists())
                    empty=pp.skips_text()
                    self.assertEqual(empty,fl.text('helper.perception.skips_text.435'))
                    self.assertNotIn('всё, что доходило',empty)

    def test_real_persisted_values_reasons_and_sources_stay_literal(self):
        for ui,frame in [('fr','zh'),('zh','en'),('en','ru'),('ru','fr')]:
            self.choose(ui,frame)
            result=pp.set_knob('debounce_sec',0.25,by=RAW,reason=RAW)
            self.assertEqual(result['new'],0.25)
            stored=json.loads(pp.STATE_PATH.read_text())['overrides']['debounce_sec']
            self.assertEqual((stored['by'],stored['reason']),(RAW,RAW))
            rows=pp.effective();row=next(r for r in rows if r['knob']=='debounce_sec')
            self.assertEqual(row['desc'],fl.text(row['desc_key']))
            self.assertEqual((row['source'],row['reason']),(RAW,RAW))
            self.assertEqual(pp.reset_knob('debounce_sec',by=RAW)['ok'],True)

    def test_panel_read_uses_interface_scope_without_mutating_agent_settings(self):
        self.choose('fr','zh');before=self.config.read_bytes()
        row=pp.panel_state()['knobs'][0]
        with fl.use('fr'): expected=fl.text(row['desc_key'])
        self.assertEqual(row['desc'],expected)
        self.assertEqual(fl.language(),'zh')
        self.assertEqual(before,self.config.read_bytes())

    def test_write_failure_reports_failure_and_has_no_spine_success(self):
        self.choose('zh','en')
        with patch.object(pp,'_save_state',side_effect=OSError(RAW)):
            result=pp.set_knob('debounce_sec',0.5,reason=RAW)
        self.assertFalse(result['ok']);self.assertIn('OSError',result['error'])
        self.spine.assert_not_called()
        self.assertFalse(pp.STATE_PATH.exists())

    def test_subscription_uncertainty_and_invalid_receipt_do_not_claim_success(self):
        for frame in LANGS:
            self.choose('en',frame)
            with patch.object(brain,'_relay_call',side_effect=TimeoutError(RAW)), patch.object(brain,'_journal') as journal:
                answer=brain.use_account('secondary',why=RAW)
            self.assertIn(RAW,answer);journal.assert_not_called()
            with patch.object(brain,'_relay_call',return_value={}), patch.object(brain,'_journal') as journal:
                answer=brain.use_account('secondary',why=RAW)
            self.assertEqual(answer,fl.text('helper.brain.account_unconfirmed'))
            journal.assert_not_called()

    def test_subscription_actual_reply_is_not_replayed_and_language_is_pinned(self):
        self.choose('fr','en')
        reply={'previous_slot':'primary','active_slot':'secondary','slots':[]}
        def changed(*args,**kwargs):
            self.choose('fr','zh')
            return copy.deepcopy(reply)
        with patch.object(brain,'_relay_call',side_effect=changed) as call, patch.object(brain,'_journal') as journal:
            answer=brain.use_account('secondary',why=RAW)
        call.assert_called_once_with('/v1/account/switch',{'slot':'secondary'})
        self.assertTrue(answer.startswith('Relay reports:'))
        self.assertIn(RAW,journal.call_args.args[0])
        self.assertEqual(fl.language(),'zh')
        self.assertEqual(reply,{'previous_slot':'primary','active_slot':'secondary','slots':[]})

    def test_switch_failed_ping_restores_exact_config_without_provider_call(self):
        self.choose('zh','fr')
        previous={'framework':'openai','model':'old','fallback_model':'backup','fallback_framework':''}
        cfg={'roles':{'voice':copy.deepcopy(previous)}}
        with patch.object(llm,'_config',return_value=cfg), patch.object(brain,'_target_framework',return_value='openai'), \
             patch.object(llm,'update_config') as update, patch.object(llm,'ping',return_value=(False,RAW)) as ping, \
             patch.object(brain,'note_call'), patch.object(brain,'_journal'):
            result=brain.switch('voice','new',why=RAW)
        self.assertFalse(result['ok']);self.assertIn(RAW,result['error'])
        self.assertEqual(update.call_args_list[1].args[0],{'roles':{'voice':previous}})
        ping.assert_called_once_with('voice')

    def test_describe_does_not_assert_reasoning_is_disabled_without_a_setting(self):
        cat={'roles':{'voice':{'framework':RAW,'model':RAW}},'frameworks':{},
             'stats':{},'current_profiles':[],'usage_7d':{},'provider_remaining':RAW}
        self.choose('ru','en')
        with patch.object(brain,'catalog',return_value=cat):result=brain.describe()
        self.assertIn('reasoning effort unspecified',result)
        self.assertIn(RAW,result);self.assertNotIn('disabled',result)

    def test_skip_codes_and_recorded_provenance_are_not_translated(self):
        self.choose('en','zh')
        pp.note_skip('fixture','мой_ритм',chat_id=42,detail=RAW,meta={'mode_reason':RAW})
        record=json.loads(pp.SKIPS_PATH.read_text().splitlines()[0])
        self.assertEqual(record['class'],'мой_ритм')
        self.assertEqual(record['meta']['mode_reason'],RAW)
        self.assertIn(RAW,pp.skips_text())
