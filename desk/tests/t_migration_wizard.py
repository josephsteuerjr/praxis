"""Actual offline engine behind the GUI; synthetic private state only."""
import json
import os
import subprocess
import time
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import migration_wizard as wizard, app_migration, python_platform


class WizardEngine(unittest.TestCase):
    def fixture(self,base):
        old=base/'Offline profile with spaces';new=base/'New Helene';home=base/'Preserved data'
        (old/'data/memory').mkdir(parents=True);(old/'code').mkdir();new.mkdir()
        (old/'data/memory/one.txt').write_text('history',encoding='utf-8')
        (old/'code/agent.py').write_text('SELF_EDIT=1',encoding='utf-8')
        (old/'helene.json').write_text(json.dumps({'tree':'data','code':'code','runner':'','model':{'key':'synthetic-secret'}}),encoding='utf-8')
        (new/'helene.json').write_text('{"setup_complete":false}',encoding='utf-8')
        (new/'python-app.json').write_text(json.dumps({'target':python_platform.runtime_target()}),encoding='utf-8')
        return {'source':str(old),'destination':str(new),'data_home':str(home),'aliases':{},'resume':False}

    def test_preview_is_read_only_and_redacts_config_values(self):
        with tempfile.TemporaryDirectory() as d:
            request=self.fixture(Path(d));before={str(p):p.read_bytes() for p in Path(d).rglob('*') if p.is_file()}
            result=wizard.preview(request)
            self.assertEqual(result['state'],'ready');self.assertNotIn('synthetic-secret',json.dumps(result))
            self.assertEqual(before,{str(p):p.read_bytes() for p in Path(d).rglob('*') if p.is_file()})
            self.assertFalse(Path(request['data_home']).exists())

    def test_actual_copy_reports_progress_and_retains_source(self):
        with tempfile.TemporaryDirectory() as d:
            request=self.fixture(Path(d));old=Path(request['source']);before={p.relative_to(old):p.read_bytes() for p in old.rglob('*') if p.is_file()}
            progress=[];result=wizard.execute(request,progress.append)
            self.assertEqual(result['state'],'prepared');self.assertTrue(progress)
            now=json.loads((Path(request['destination'])/'helene.json').read_text())
            self.assertEqual((Path(now['tree'])/'memory/one.txt').read_text(),'history')
            self.assertEqual((Path(now['code'])/'agent.py').read_text(),'SELF_EDIT=1')
            self.assertEqual(now['model']['key'],'synthetic-secret')
            for relative,data in before.items():self.assertEqual((old/relative).read_bytes(),data)
            self.assertNotIn('startup_proof',result)

    def test_interrupted_copy_recovers_input_and_explicitly_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            request=self.fixture(Path(d));new=Path(request['destination'])
            with patch.object(app_migration,'preserve_source',side_effect=OSError('simulated disk full')):
                with self.assertRaisesRegex(OSError,'disk full'):wizard.execute(request,lambda _:None)
            recovered=wizard.saved_request(new);self.assertTrue(recovered['resume'])
            self.assertEqual(wizard.preview(recovered)['state'],'ready_to_resume')
            result=wizard.execute(recovered,lambda _:None)
            self.assertEqual(result['state'],'prepared');self.assertIsNone(wizard.saved_request(new))

    def test_hard_kill_before_and_after_journal_keeps_ordinary_wizard_entry(self):
        import python_app
        desk=Path(__file__).resolve().parents[1]
        for boundary in ('snapshot','preserve_source'):
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as d:
                request=self.fixture(Path(d));new=Path(request['destination'])
                (new/'app/static').mkdir(parents=True)
                (new/'app/static/index.html').write_text('<html></html>')
                template=(new/'helene.json').read_bytes();marker=Path(d)/'backup-boundary'
                # Kill an actual OS process: no Python finally/restore handler runs.
                script='''import json,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from localharness import app_migration,migration_wizard
request=json.loads(sys.argv[2]);boundary=sys.argv[3];marker=Path(sys.argv[4])
original=getattr(app_migration,boundary)
def blocked(*args,**kwargs):
    if boundary=='snapshot':original(*args,**kwargs)
    marker.write_text('ready')
    while True:time.sleep(1)
setattr(app_migration,boundary,blocked)
migration_wizard.execute(request,lambda _:None)
'''
                child=subprocess.Popen([sys.executable,'-c',script,str(desk),json.dumps(request),boundary,str(marker)],
                                       stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                try:
                    deadline=time.monotonic()+20
                    while not marker.exists() and child.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                    self.assertTrue(marker.exists(),'Child did not reach '+boundary)
                finally:
                    if child.poll() is None:child.kill()
                    _,errors=child.communicate(timeout=10)
                self.assertNotEqual(child.returncode,0,errors.decode(errors='replace'))
                if boundary=='snapshot':
                    self.assertEqual((new/'helene.json').read_bytes(),template)
                    self.assertFalse((new/'python-migration.json').exists())
                    self.assertIsNone(wizard.saved_request(new))
                    retry=request
                else:
                    self.assertFalse((new/'helene.json').exists())
                    retry=wizard.saved_request(new);self.assertTrue(retry['resume'])
                # Invoke normal startup (no explicit wizard flag). Stop at the UI
                # boundary so this source test neither needs Qt nor starts an agent.
                import helene_setup
                with patch.object(python_app.sys,'platform','linux'), \
                        patch.object(wizard,'run',return_value=False) as entry, \
                        patch.object(helene_setup,'main',return_value=0) as short_entry, \
                        patch.object(python_app,'Application') as app:
                    self.assertEqual(python_app._main(['--root',str(new)]),0)
                    if boundary=='snapshot':
                        entry.assert_called_once_with(new.resolve());short_entry.assert_not_called()
                    else:
                        short_entry.assert_called_once_with(['--root',str(new.resolve()),'--source-mode','preserve'])
                        entry.assert_not_called()
                    app.assert_not_called()
                result=wizard.execute(retry,lambda _:None)
                self.assertEqual(result['state'],'prepared')
                selected=json.loads((new/'helene.json').read_text())
                self.assertEqual((Path(selected['tree'])/'memory/one.txt').read_text(),'history')

    def test_engine_refuses_config_without_exact_fresh_template(self):
        with tempfile.TemporaryDirectory() as d:
            request=self.fixture(Path(d));old=Path(request['source']);new=Path(request['destination']);home=Path(request['data_home'])
            with self.assertRaises(FileExistsError):app_migration.prepare(old,new,home)
            with self.assertRaisesRegex(ValueError,'template changed'):
                app_migration.prepare(old,new,home,fresh_template=b'{"setup_complete": false}')
            self.assertFalse(home.exists());self.assertFalse((new/'python-migration.json').exists())

    def test_path_validation_refuses_overlap_existing_and_changed_resume(self):
        with tempfile.TemporaryDirectory() as d:
            request=self.fixture(Path(d));source=Path(request['source']);new=Path(request['destination'])
            for home in (source,new,source/'inside',new.parent):
                with self.subTest(home=home),self.assertRaises(ValueError):wizard.preview(dict(request,data_home=str(home)))
            with self.assertRaises(ValueError):wizard.preview(dict(request,aliases={'C:\\old':str(Path(d)/'outside')}))
            (new/'helene.json').write_text('{"setup_complete":true}')
            with self.assertRaisesRegex(ValueError,'fresh'):wizard.preview(request)

    def test_absent_destination_config_does_not_hide_existing_agent(self):
        for child in ('data','agents/other'):
            for config_state in ('absent','empty'):
                with self.subTest(child=child,config_state=config_state),tempfile.TemporaryDirectory() as d:
                    request=self.fixture(Path(d));new=Path(request['destination'])
                    if config_state=='absent':(new/'helene.json').unlink()
                    else:(new/'helene.json').write_bytes(b'')
                    (new/child).mkdir(parents=True)
                    preferences=new/'window-preferences.json';preferences.write_text('{"scale":0.7}')
                    with self.assertRaises(ValueError):wizard.preview(request)
                    self.assertFalse((new/'python-migration.json').exists())
                    self.assertEqual(preferences.read_text(),'{"scale":0.7}')

    def test_normal_start_binds_prepared_migration_before_controller(self):
        import python_app
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'app/static').mkdir(parents=True);(root/'app/static/index.html').write_text('<html></html>')
            data=root/'selected data';(data/'memory').mkdir(parents=True)
            (root/'helene.json').write_text(json.dumps({'setup_complete':True,'tree':str(data)}))
            (root/'python-migration.json').write_text('{"state":"prepared","id":"fixture-operation"}')
            observed=[]
            class FakeApplication:
                def __init__(self,*args):self.origin='fixture';self.event=lambda *_:None;observed.append(self)
            class ReachedController(Exception):pass
            from localharness import session_host
            with patch.object(python_app,'Application',FakeApplication),patch.object(session_host,'attach',side_effect=ReachedController):
                with self.assertRaises(ReachedController):python_app._main(['--root',str(root),'--browser'])
            self.assertEqual(observed[0].migration_id,'fixture-operation')


if __name__=='__main__':unittest.main(verbosity=2)
