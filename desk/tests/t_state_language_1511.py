"""Actual current-state branches keep effects and raw evidence independent of UI words."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

DESK=Path(__file__).resolve().parents[1];sys.path[:0]=[str(DESK),str(DESK/'localharness')]
from deskd import readers,state_words,autonomy
import deskapp

RAW='Literal Русский 中文 <script>{state}</script>'


class StateLanguages(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.tree=Path(temporary.name)
        self.state=self.tree/'memory/.state';self.state.mkdir(parents=True)
        self.product={'model':'fixture','base_url':'https://fixture.invalid','key_present':True,'relay_enabled':False}
        self.voice={'model':'fixture','framework':'openai'};self.framework={'openai':{'base_url':'https://fixture.invalid'}}
        self.model='fixture'
        self.runner={'alive':True,'busy':False,'run':'','since':0}
        self.agenda=[];self.key=True
        self.enterContext(patch.object(readers,'tree',return_value=self.tree))
        self.enterContext(patch.object(readers,'product_config',side_effect=lambda:copy.deepcopy(self.product)))
        self.enterContext(patch.object(readers,'reader_status',side_effect=lambda *args:copy.deepcopy(self.runner)))
        self.enterContext(patch.object(readers,'_key_present',side_effect=lambda *args:self.key))
        self.enterContext(patch.object(readers,'agenda',side_effect=lambda:{'active':copy.deepcopy(self.agenda)}))
        for name,result in [('mode_state',{}),('health',{'alarms':[]}),('desk_build',{}),('runner_activity',None)]:
            self.enterContext(patch.object(readers,name,return_value=result))

    def write(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf8')

    def read(self):
        self.write(self.state/'anatomy.json',{'agent_name':RAW,'owner_name':RAW,'model':{'model':self.model}})
        self.write(self.tree/'memory/llm.json',{'roles':{'voice':self.voice},'frameworks':self.framework})
        before={p.relative_to(self.tree):p.read_bytes() for p in self.tree.rglob('*') if p.is_file()}
        result=readers._state_impl()
        self.assertEqual({p.relative_to(self.tree):p.read_bytes() for p in self.tree.rglob('*') if p.is_file()},before)
        self.assertEqual(result['agent'],RAW);self.assertEqual(result['owner'],RAW)
        self.assertEqual(result['runner'],self.runner)
        return result

    def test_actual_liveness_model_quota_error_sleep_wake_branches_and_raw_evidence(self):
        self.assertEqual(self.read()['display_phrase'],state_words.make('online'))
        self.runner['alive']=False;view=self.read();self.assertEqual(view['display_phrase'],state_words.make('not_running'))
        self.assertEqual(view['action']['target'],'restart');self.assertEqual(view['action']['label'],'Перезапустить')
        self.assertEqual(view['action']['display_label'],state_words.make('action.restart'))
        self.runner['alive']=True;self.key=False;self.assertEqual(self.read()['display_phrase'],state_words.make('key_missing'))
        self.key=True
        self.model='';self.voice['model']='';self.product['model']='';self.key=False
        self.assertEqual(self.read()['display_phrase'],state_words.make('model_missing'))
        self.model='fixture';self.voice['model']='fixture';self.product['model']='fixture';self.key=True
        self.product.update(relay_enabled=True,relay_port=5011);self.framework['openai']['base_url']='http://localhost:5011'
        self.assertEqual(self.read()['display_phrase'],state_words.make('relay_signin'))
        self.product['relay_enabled']=False;self.framework['openai']['base_url']='https://fixture.invalid'
        reset=time.time()+3600;hold={'holds':[{'framework':'openai','endpoint':'https://fixture.invalid','until':reset,'words':RAW,'code':'limit'}]}
        self.write(self.state/'quota.json',hold)
        with patch.object(readers,'_fallback_elsewhere',return_value=True):view=self.read()
        self.assertEqual(view['display_phrase'],state_words.make('quota_fallback',until=reset))
        self.assertIn(RAW,view['phrase']);self.assertEqual(view['brain']['quota']['words'],RAW)
        with patch.object(readers,'_fallback_elsewhere',return_value=False):view=self.read()
        self.assertEqual(view['display_phrase'],state_words.make('quota_no_fallback',until=reset))
        self.write(self.state/'quota.json',{})
        row={'ts':time.time(),'ok':False,'err':'401 '+RAW}
        (self.state/'llm_calls.jsonl').write_text(json.dumps(row)+'\n',encoding='utf8')
        view=self.read();self.assertEqual(view['display_phrase'],state_words.make('error.auth'))
        self.assertEqual(view['brain']['last_error_raw'],'401 '+RAW);self.assertIn(view['brain']['last_error'],view['phrase'])
        (self.state/'llm_calls.jsonl').unlink()
        self.runner.update(busy=True,run='sleep');self.assertEqual(self.read()['display_phrase'],state_words.make('sleeping'))
        self.runner['run']='real-run';self.assertEqual(self.read()['display_phrase'],state_words.make('thinking'))
        self.runner.update(busy=False,run='');wake=dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=2)
        self.agenda=[{'kind':'wake','when':wake.isoformat()}]
        self.assertEqual(self.read()['display_phrase'],state_words.make('wake_wait',at=wake.timestamp()))

    def test_error_hint_ownership_is_attached_at_existing_predicate_and_not_to_raw_message(self):
        vectors={'401':'auth','429':'quota','timed out':'timeout','getaddrinfo':'network','404':'address',
                 'unsupported_parameter':'parameter','context_length':'context','content_filter':'policy',
                 'invalid_request_error':'request','503':'server',RAW:'unknown'}
        for diagnostic,kind in vectors.items():
            self.assertIsInstance(readers._short_error(diagnostic),str)
            self.assertEqual(readers._short_error(diagnostic,owned=True),state_words.make('error.'+kind))

    def test_paused_overlay_replaces_stale_recipe_without_mutating_reader_snapshot(self):
        source=self.read();source['display_phrase']=state_words.make('not_running');before=copy.deepcopy(source)
        with patch.object(readers,'state',return_value=source),patch.object(autonomy,'state',return_value={'paused':True,'available':True}):
            result=deskapp._state_with_autonomy()
        self.assertEqual(result['display_phrase'],state_words.make('paused'));self.assertEqual(result['level'],'off')
        self.assertIsNone(result['action']);self.assertEqual(source,before)

    def test_reader_fallback_is_owned_and_invalid_numeric_recipe_refuses(self):
        with patch.object(readers,'_state_impl',side_effect=RuntimeError(RAW)):
            view=readers.state()
        self.assertEqual(view['display_phrase'],state_words.make('reader_failed'))
        self.assertEqual(view['alarms'][0]['display_text'],state_words.make('alarm.reader_state'))
        for kind,values in [('unknown',{}),('online',{'extra':1}),('quota_fallback',{'until':True}),
                            ('wake_wait',{'at':float('nan')}),('alarm.debt',{'minutes':1,'count':RAW})]:
            with self.assertRaises(ValueError):state_words.make(kind,**values)


if __name__=='__main__':unittest.main()
