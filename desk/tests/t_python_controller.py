"""Focused desktop command contracts against disposable roots, no live profile."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness.python_controller import Controller
from localharness import app_migration


class ControllerTests(unittest.TestCase):
    def test_saved_model_applies_after_relay_start_and_reports_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'helene.json').write_text('{"tree":"data"}')
            controller=Controller(root,lambda *args:None);order=[]
            with patch.object(controller,'refresh',side_effect=lambda:order.append('refresh')),patch('localharness.python_relay.hot_settings',side_effect=lambda agent:order.append('configure')):
                result=controller.invoke('config_save',{'config':json.dumps({'tree':'data','relay':{'enabled':True},'model':{'model':'gpt-test','reasoning_effort':'low'}})})
                self.assertTrue(result['ok']);self.assertEqual(order,['refresh','configure'])
                projected=json.loads((root/'data/memory/llm.json').read_text(encoding='utf-8'))
                self.assertEqual(projected['roles']['voice']['model'],'gpt-test')
            with patch.object(controller,'refresh'),patch('localharness.python_relay.hot_settings',side_effect=OSError('not started')):
                result=controller.invoke('config_save',{'config':json.dumps({'tree':'data','relay':{'enabled':True}})})
                self.assertTrue(result['ok']);self.assertIn('has not applied',result['warning'])

    def test_ui_shapes_config_stale_and_file_consumer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'data').mkdir()
            config=root/'helene.json';config.write_text(json.dumps({'tree':'data','runner':''}),encoding='utf-8')
            controller=Controller(root,lambda *a:None)
            first=controller.invoke('config_load',{})
            self.assertEqual(first['config']['tree'],'data')
            self.assertEqual(first['agent_id'],'main')
            self.assertIn('agents',controller.invoke('agents_list',{}))
            saved=controller.invoke('config_save',{'config':json.dumps({'tree':'data','port':12345}),'mtimeNs':first['mtime_ns']})
            self.assertTrue(saved['ok'])
            self.assertEqual(controller.invoke('config_save',{'config':'{}','mtimeNs':first['mtime_ns']})['code'],'stale')
            listing=controller.invoke('local_files_list',{'path':str(root)})
            self.assertIsInstance(listing,dict)

    def test_restart_has_no_native_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'helene.json').write_text('{"tree":"data"}')
            controller=Controller(root,lambda *a:None)
            with patch.object(controller,'close') as close,patch.object(controller,'start') as start:
                self.assertTrue(controller.invoke('engine_restart',{})['ok'])
                close.assert_called_once();start.assert_called_once()

    def test_preserved_code_is_selected_not_stock(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';code=old/'edited';code.mkdir(parents=True)
            (old/'data').mkdir();(old/'helene.json').write_text(json.dumps({'tree':'data','code':'edited','runner':'custom.py'}))
            (code/'agent.py').write_text('VALUE="self-authored"')
            def git(*args):return subprocess.check_output(['git','-C',str(code),*args])
            git('init');git('add','.');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','birth')
            (code/'agent.py').write_text('VALUE="dirty-self-authored"')
            (code/'untracked.py').write_text('VALUE=17')
            note=app_migration.prepare(old,root/'new',root/'home')
            cfg=json.loads((root/'new/helene.json').read_text())
            selected=Path(cfg['code'])
            self.assertNotEqual(selected,root/'new/tree')
            self.assertEqual((selected/'agent.py').read_text(),'VALUE="dirty-self-authored"')
            self.assertEqual((selected/'untracked.py').read_text(),'VALUE=17')
            self.assertTrue((selected/'.git').is_dir())
            with self.assertRaises(ValueError):app_migration.confirm_started(root/'new',note['id'],proof={})

if __name__=='__main__':unittest.main()
