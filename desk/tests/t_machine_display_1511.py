"""Actual live machine producers keep verdicts and raw evidence across both languages."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

DESK=Path(__file__).resolve().parents[1];sys.path[:0]=[str(DESK),str(DESK/'localharness')]
import frame_language as fl
from localharness import body,fence,machine_words as words

LANGS=('ru','en','fr','zh')
RAW='<img src=x onerror=globalThis.injected=1> Русский 中文 {port}'

class MachineDisplay(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.config=self.root/'helene.json'
        self.enterContext(patch.dict(os.environ,HELENE_CONFIG=str(self.config)))
        self.enterContext(patch.dict(body.STATE,clear=True));self.enterContext(patch.dict(fence.STATE,clear=True))
        self.enterContext(patch.dict(body._TOKENS,clear=True));self.enterContext(patch.object(body,'HAS_BODY',True))
        self.choose('ru','ru')
        self.agent=SimpleNamespace(FRAME_TEXT_BOUNDARIES_API=1,frame_language=fl)

    def choose(self,ui,frame):self.config.write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf8')

    def test_actual_absent_disabled_and_port_preflights_never_spawn(self):
        engine=body.Body(self.root,self.root/'home',{'computer':{'enabled':True,'port':9490}})
        with patch.object(body,'_exe_pair',return_value=None),patch.object(body._Child,'spawn') as spawn:
            self.assertFalse(engine.start());self.assertEqual(body.STATE['display_reason']['key'],'body.absent')
            self.assertIn(body.BRIDGE_EXE,body.STATE['reason']);spawn.assert_not_called()
        with patch.object(body,'_exe_pair',return_value=(Path('bridge'),Path('body'))),patch.object(body,'pick_port',return_value=None),patch.object(body._Child,'spawn') as spawn:
            self.assertFalse(engine.start());self.assertEqual(body.STATE['display_reason']['values'],{'first':9490,'last':9490+body.PORT_SPAN});spawn.assert_not_called()
        with patch.object(body,'_exe_pair',return_value=(Path('bridge'),Path('body'))),patch.object(body.Body,'start') as start:
            self.assertIsNone(body.launch(self.root,self.root/'home',{'computer':{'enabled':False}}));start.assert_not_called()
            self.assertEqual(body.STATE['display_reason']['key'],'body.disabled')

    def test_actual_probe_frame16axes_tcc_and_raw_diagnostic_preservation(self):
        engine=body.Body(self.root,self.root/'home',{});engine.port=9490;engine.children=[SimpleNamespace(alive=lambda:True)]
        by_frame={}
        for ui in LANGS:
            for frame in LANGS:
                self.choose(ui,frame);body.STATE.update(enabled=True,available=True,scopes=['computer.apps'],port=9490)
                with patch.object(body,'call',return_value={'ok':True,'identity':{'kind':RAW,'session_id':7}}) as rpc,patch.object(body,'ASKS_TCC',False):
                    self.assertTrue(engine.probe());rpc.assert_called_once_with('body.status',{},timeout=4.0)
                body.STATE['tcc']={'screen_recording':False,'accessibility':True};before=copy.deepcopy(body.STATE)
                projected=words.frame(self.agent,body.windows_display(),body.windows_truth())
                self.assertIn(RAW,projected);self.assertIn('computer.apps',projected)
                self.assertEqual(by_frame.setdefault(frame,projected),projected);self.assertEqual(body.STATE,before)
                self.assertEqual(body.STATE['identity']['kind'],RAW)
        self.assertEqual(len(set(by_frame.values())),4)
        with patch.object(body,'call',return_value={'ok':False,'error':{'literal':RAW}}),patch.object(body,'ASKS_TCC',False):
            self.assertFalse(engine.probe())
        self.assertEqual(body.STATE['display_reason']['key'],'body.body_wait');self.assertIn(RAW,words.frame(self.agent,body.STATE['display_reason'],''))
        self.assertFalse(body.STATE['connected'])

    def test_nested_frame_render_pins_one_language_and_leaves_legacy_literal(self):
        self.choose('zh','en');actual=fl.text;calls=[]
        value=words.join(words.make('body.bridge_name'),words.make('body.no_response'))
        def changing(key,**params):
            calls.append(key);self.choose('en','fr');return actual(key,**params)
        with patch.object(fl,'text',side_effect=changing):answer=words.frame(self.agent,value,'OLD '+RAW)
        self.assertEqual(answer,'bridge; no response');self.assertEqual(len(calls),2);self.assertEqual(fl.language(),'fr')
        self.assertEqual(words.frame(SimpleNamespace(),value,'OLD '+RAW),'OLD '+RAW)
        self.assertEqual(words.render({'schema':words.SCHEMA,'key':'body.connected','values':{}},fl.text),'')
        value['values']['extra']='forged';self.assertEqual(words.render(value,fl.text),'')
        with self.assertRaises(ValueError):words.make('body.initial',extra=RAW)

    def test_actual_remote_selection_and_failed_confirmation_keep_target_and_effect_count(self):
        engine=body.RemoteBody(self.root,{})
        selected='a'*32
        picture={'selected':selected,'devices':[{'id':selected,'name':RAW,'connected':True,'scopes':['computer.read'],'identity':{}}]}
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=json.dumps(picture).encode()
        opener=Mock();opener.open.return_value=response
        with patch.object(body,'_REMOTE_CONTROLLER','fixture-controller'),patch.object(body,'_direct_opener',return_value=opener):
            self.assertTrue(engine.metadata(force=True));opener.open.assert_called_once()
        self.assertEqual(body._TOKENS['device'],selected);self.assertEqual(body.STATE['display_reason']['key'],'body.remote_connected')
        self.assertIn(RAW,words.frame(self.agent,body.windows_display(),''));self.assertNotIn('fixture-controller',str(body.windows_display()))
        with patch.object(engine,'metadata',return_value=True),patch.object(body,'_raw_call',return_value={'ok':False}) as rpc:
            self.assertFalse(engine.probe());rpc.assert_called_once_with('body.status',{},timeout=3.0)
        self.assertEqual(body._TOKENS['device'],selected);self.assertFalse(body.STATE['connected']);self.assertEqual(body.STATE['display_reason']['key'],'body.remote_unconfirmed')

    def test_live_hand_verdicts_modules_caveats_and_stale_names_have_source_owned_recipes(self):
        hands={key:Mock() for key in fence.MACHINE_HANDS};hands['windows_mcp_Click']=Mock();hands['fs_read']._helene_fenced=True
        # Mock auto-attributes must not stand in for real installed wrappers.
        for key,fn in hands.items():
            if key!='fs_read':fn._helene_fenced=False
        hands.pop('host_ctl');agent=SimpleNamespace(TOOL_IMPL=hands)
        modules={'command_execution':SimpleNamespace(home_runner=lambda:None),'workshop':SimpleNamespace(subprocess=object()),'hands':SimpleNamespace(subprocess=object())}
        with patch.dict(sys.modules,modules),patch.object(fence,'_shim_in',side_effect=lambda name:name=='command_execution'):
            fence.STATE.update(container=True,reason='RAW '+RAW,display_reason=words.make('fence.active',mechanism='bubblewrap',network=words.make('fence.no')))
            rows={r['name']:r for r in fence.hands_report(agent)}
            self.assertEqual(rows['shell']['fence'],'container');self.assertEqual(rows['fs_read']['fence'],'path')
            self.assertEqual(rows['project']['fence'],'outside');self.assertEqual(rows['host_ctl']['fence'],'unknown')
            self.assertEqual(rows['windows_mcp_Click']['display_why']['key'],'why.mcp')
            before=copy.deepcopy(rows)
            for ui in LANGS:
                for frame in LANGS:
                    self.choose(ui,frame)
                    for row in rows.values():
                        self.assertTrue(words.frame(self.agent,row['display_touches'],''));self.assertTrue(words.frame(self.agent,row['display_why'],''))
            self.assertEqual(rows,before)
            fence.STATE.update(container=False,display_reason=words.make('fence.command_failed',detail=RAW))
            row=next(r for r in fence.hands_report(agent) if r['name']=='shell')
            self.assertEqual(row['fence'],'outside');self.assertIn(RAW,words.frame(self.agent,row['display_why'],''))

    def test_snapshot_uses_actual_body_without_mutation_and_every_catalog_is_closed(self):
        body.STATE.update(remote=True,remote_name=RAW,scopes=[],display_reason=words.make('body.remote_offline'))
        # fence's legacy direct import resolves the same body used by the runner.
        with patch.dict(sys.modules,body=body):
            before=copy.deepcopy((body.STATE,fence.STATE));picture=fence.state()
        self.assertEqual(picture['display_windows'],body.windows_display());self.assertEqual((body.STATE,fence.STATE),before)
        self.assertNotIn('display_windows',fence.STATE)
        for lang in LANGS:
            for root in (DESK/'lang',DESK.parent/'helene/core/frame_text'):
                catalog=json.loads((root/(lang+'.json')).read_text(encoding='utf8'))
                self.assertEqual({key.removeprefix('machine.') for key in catalog if key.startswith('machine.')},set(words.FIELDS))

if __name__=='__main__':unittest.main()
