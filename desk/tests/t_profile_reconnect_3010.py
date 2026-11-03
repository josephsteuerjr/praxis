"""Preserved settings reconnect real data; no new identity or old code is copied."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import profile_reconnect as reconnect


class Reconnect(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='helene-reconnect-');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.data=self.base/'old/data';self.new=self.base/'new'
        self.data.mkdir(parents=True);self.new.mkdir()
        (self.data/'memory').mkdir();(self.data/'memory/history.jsonl').write_bytes(b'old history\n')
        self.saved={'format':reconnect.FORMAT,'data':str(self.data),'old_program':str(self.data.parent),
            'config':{'mode':'local','tree':'data','setup_complete':True,
                'model':{'framework':'openai','model':'fixture-model','key':'fixture-key'},
                'autonomy':{'paused':True},'name':'original agent','installed':{'service':True},
                'relay':{'enabled':False}},
            'settings':{'agent-default.json':base64.b64encode(b'{"id":"main"}').decode()}}
        reconnect.record(self.data).parent.mkdir(parents=True)
        self.save()
    def save(self):reconnect.record(self.data).write_text(json.dumps(self.saved),encoding='utf8')
    def test_real_restoration_preserves_auth_name_pause_data_and_rebases_program_paths(self):
        before=(self.data/'memory/history.jsonl').read_bytes();record=reconnect.record(self.data).read_bytes()
        self.assertTrue(reconnect.restore(self.new,self.data))
        config=json.loads((self.new/'helene.json').read_text('utf8'))
        self.assertEqual(config['model'],self.saved['config']['model'])
        self.assertEqual(config['autonomy'],{'paused':True});self.assertEqual(config['name'],'original agent')
        self.assertEqual(Path(config['tree']),self.data);self.assertTrue(config['setup_complete'])
        self.assertEqual((self.new/config['app']).resolve(),self.new/'app/deskapp.py')
        self.assertFalse(config['installed']['service'])
        self.assertEqual((self.new/'agent-default.json').read_bytes(),b'{"id":"main"}')
        self.assertEqual((self.data/'memory/history.jsonl').read_bytes(),before)
        self.assertEqual(reconnect.record(self.data).read_bytes(),record)
        self.assertFalse((self.new/'data').exists())
        with self.assertRaisesRegex(ValueError,'перезаписываются'):reconnect.restore(self.new,self.data)
    def test_wrong_data_identity_and_bad_optional_settings_refuse_before_config_write(self):
        for change in ({'data':str(self.base/'other')},{'settings':{'../helene.json':'e30='}},
                       {'settings':{'agent-default.json':'not base64!'}}):
            with self.subTest(change=change):
                original=dict(self.saved);self.saved.update(change);self.save()
                with self.assertRaises((ValueError,TypeError)):
                    reconnect.restore(self.new,self.data)
                self.assertFalse((self.new/'helene.json').exists())
                self.saved=original
    def test_missing_record_is_not_permission_to_invent_recovered_settings(self):
        reconnect.record(self.data).unlink()
        self.assertFalse(reconnect.restore(self.new,self.data));self.assertEqual(list(self.new.iterdir()),[])


if __name__=='__main__':unittest.main()
