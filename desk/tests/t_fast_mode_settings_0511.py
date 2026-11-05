"""Phone/MiniApp Fast mode uses the same optimistic owner config writer."""
from pathlib import Path
import copy
import json
import sys
import tempfile
import unittest
import uuid
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deskd import phone_settings as settings
from localharness import config_cli
from localharness.python_desktop_commands import probe_model


class FastModeSettings(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.path=self.root/'helene.json'
        self.tree=self.root/'data';self.tree.mkdir()
        self.config={'tree':str(self.tree),'port':31234,'mode':'local',
            'agent':{'name':'Controlled'},'model':{'framework':'openai','provider':'api',
                'base_url':'https://example.invalid/v1','model':'configured-model','reasoning_effort':'high',
                'custom':{'retained':True}},'unrelated':{'keep':['existing']}}
        self.path.write_text(json.dumps(self.config),encoding='utf8')
        self.patchers=[patch.object(settings.readers,'config_path',return_value=self.path),
            patch.object(settings.readers,'tree',return_value=self.tree)]
        for item in self.patchers:item.start();self.addCleanup(item.stop)

    def test_schema_exposes_explicit_off_fast_and_provider_defaults(self):
        spec=settings.BY_PATH['model.service_tier']
        self.assertEqual([row[0] for row in spec['choices']],['','default','priority'])
        self.assertIn('лимитов',spec['hint'])
        self.assertIn('управления компьютером',spec['hint'])

    def test_absent_legacy_config_stays_absent_on_unrelated_save(self):
        row=next(row for row in settings.snapshot()['fields'] if row['path']=='model.service_tier')
        self.assertEqual(row['value'],'')
        result=settings.prepare(self.config,{'agent.name':'Updated'})
        self.assertNotIn('service_tier',result['model'])
        self.assertEqual(result['model'],self.config['model'])

    def test_explicit_fast_roundtrips_through_normal_optimistic_writer(self):
        config=settings.prepare(self.config,{'provider':'chatgpt'})
        self.path.write_text(json.dumps(config),encoding='utf8')
        view=settings.snapshot()
        body={'changes':{'model.service_tier':'priority'},'mtime_ns':view['mtime_ns'],
            'scope_id':view['scope_id'],'operation':uuid.uuid4().hex}
        def commit(path,tree,value,stamp,operation):
            self.assertEqual(tree,self.tree)
            return config_cli.save(path,json.dumps(value),stamp)
        with patch.object(settings,'_desktop_commit',side_effect=commit) as writer:
            self.assertTrue(settings.save(body)['ok'])
        self.assertEqual(writer.call_count,1)
        saved=json.loads(self.path.read_text(encoding='utf8'))
        self.assertEqual(saved['model']['service_tier'],'priority')
        self.assertEqual(saved['model']['reasoning_effort'],'high')
        self.assertEqual(saved['model']['custom'],self.config['model']['custom'])
        self.assertEqual(saved['unrelated'],self.config['unrelated'])
        row=next(row for row in settings.snapshot()['fields'] if row['path']=='model.service_tier')
        self.assertEqual(row['value'],'priority')

    def test_off_and_reset_are_distinct_and_alias_is_canonicalized(self):
        config=settings.prepare(self.config,{'provider':'chatgpt'})
        selected=settings.prepare(config,{'model.service_tier':'fast'})
        self.assertEqual(selected['model']['service_tier'],'priority')
        normal=settings.prepare(selected,{'model.service_tier':'default'})
        self.assertEqual(normal['model']['service_tier'],'default')
        reset=settings.prepare(normal,{'model.service_tier':''})
        self.assertNotIn('service_tier',reset['model'])
        self.assertEqual(reset['model']['reasoning_effort'],'high')

    def test_unknown_saved_tier_and_effort_survive_unrelated_edits(self):
        config=copy.deepcopy(self.config)
        config['model'].update(service_tier='provider-custom',reasoning_effort='future-effort')
        result=settings.prepare(config,{'owner.name':'Controlled owner'})
        self.assertEqual(result['model'],config['model'])

    def test_invalid_explicit_values_cannot_reach_owner_writer(self):
        view=settings.snapshot()
        for value in ('unsupported',True,7,{},[]):
            body={'changes':{'model.service_tier':value},'mtime_ns':view['mtime_ns'],
                'scope_id':view['scope_id'],'operation':uuid.uuid4().hex}
            with self.subTest(value=value),patch.object(settings,'_desktop_commit') as writer:
                with self.assertRaises(settings.SettingsError):settings.save(body)
                writer.assert_not_called()

    def test_new_fast_on_non_codex_route_is_rejected_before_owner_write(self):
        view=settings.snapshot()
        for value in ('priority','fast'):
            body={'changes':{'model.service_tier':value},'mtime_ns':view['mtime_ns'],
                'scope_id':view['scope_id'],'operation':uuid.uuid4().hex}
            with self.subTest(value=value),patch.object(settings,'_desktop_commit') as writer:
                with self.assertRaises(settings.SettingsError):settings.save(body)
                writer.assert_not_called()
        legacy=copy.deepcopy(self.config)
        legacy['model']['service_tier']='priority'
        self.assertEqual(settings.prepare(legacy,{'agent.name':'Updated'})['model']['service_tier'],'priority')

    def test_provider_switch_prunes_tier_but_unrelated_save_retains_it(self):
        config=copy.deepcopy(self.config)
        config['relay']={'enabled':True,'port':5011,'key':'controlled-loop-key'}
        config['model'].update(provider='chatgpt',key='controlled-loop-key',base_url='http://127.0.0.1:5011',service_tier='priority')
        self.assertEqual(settings.prepare(config,{'agent.name':'Updated'})['model']['service_tier'],'priority')
        other=settings.prepare(config,{'provider':'anthropic'})
        self.assertNotIn('service_tier',other['model'])

    def test_codex_advertised_ultra_is_supported_and_none_is_rejected(self):
        config=copy.deepcopy(self.config)
        config['relay']={'enabled':True,'port':5011,'key':'controlled-loop-key'}
        config['model'].update(provider='chatgpt',key='controlled-loop-key',base_url='http://127.0.0.1:5011')
        self.assertEqual(settings.prepare(config,{'model.reasoning_effort':'ultra'})['model']['reasoning_effort'],'ultra')
        for value in ('none','future-unsupported'):
            with self.subTest(value=value),self.assertRaises(settings.SettingsError):
                settings.prepare(config,{'model.reasoning_effort':value})

    def test_old_codex_none_is_not_rewritten_by_unrelated_phone_edit(self):
        config=copy.deepcopy(self.config)
        config['relay']={'enabled':True,'port':5011,'key':'controlled-loop-key'}
        config['model'].update(provider='chatgpt',key='controlled-loop-key',base_url='http://127.0.0.1:5011',reasoning_effort='none')
        result=settings.prepare(config,{'agent.name':'Changed'})
        self.assertEqual(result['model']['reasoning_effort'],'none')


class ProbeMetadata(unittest.TestCase):
    def test_local_catalog_retains_capabilities_without_private_instructions(self):
        payload={'data':[{'id':'controlled-codex','reasoning_efforts':['low','ultra','none'],
            'default_reasoning_effort':'ultra','service_tiers':[{'id':'priority','name':'Fast','description':'Faster processing','private':'omit'}],
            'additional_speed_tiers':['fast'],'base_instructions':'private instructions'},
            {'id':'legacy-model'},{'id':'known-unsupported','service_tiers':[],'reasoning_efforts':[]}]}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body=json.dumps(payload).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
            def log_message(self,*_):pass
        server=HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:result=probe_model('http://127.0.0.1:'+str(server.server_port))
        finally:server.shutdown();server.server_close();thread.join()
        self.assertTrue(result['ok'])
        rows={row['id']:row for row in result['model_details']}
        self.assertEqual(rows['controlled-codex']['reasoning_efforts'],payload['data'][0]['reasoning_efforts'])
        self.assertEqual(rows['controlled-codex']['service_tiers'],[{'id':'priority','name':'Fast','description':'Faster processing'}])
        self.assertEqual(rows['controlled-codex']['additional_speed_tiers'],['fast'])
        self.assertNotIn('base_instructions',json.dumps(result))
        self.assertEqual(rows['legacy-model'],{'id':'legacy-model'})
        self.assertEqual(rows['known-unsupported']['service_tiers'],[])


if __name__=='__main__':unittest.main()
