"""Current preparation errors survive support export without credentials."""
from pathlib import Path
from types import SimpleNamespace
import json,sys,tempfile,unittest,zipfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import first_start,startup_diagnostics,python_support


class Diagnostics(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='startup-diagnostic-');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'programme';self.root.mkdir()
        self.tree=self.base/'profile/Helene/data';self.tree.mkdir(parents=True)
        (self.root/'python-app.json').write_text('{"version":"fixture-version"}',encoding='utf8')
        self.config=self.root/'helene.json';self.secret='fixture-private-key-for-redaction'
        self.config.write_text(json.dumps({'model':{'key':self.secret}}),encoding='utf8')
        self.agent=SimpleNamespace(tree=self.tree,config=self.config)

    def test_preparation_failure_is_private_profile_state_not_programme_junk(self):
        try:raise ValueError('a real preparation failure')
        except ValueError as error:path=startup_diagnostics.record(self.root,self.tree,error)
        self.assertTrue(path.is_relative_to(self.tree))
        value=json.loads(path.read_text(encoding='utf8'))
        self.assertEqual(value['version'],'fixture-version')
        self.assertEqual(value['error_type'],'ValueError')
        self.assertIn('Traceback',value['traceback'])
        self.assertEqual(set(self.root.iterdir()),{self.config,self.root/'python-app.json'})

    def test_support_includes_current_failure_and_status_but_no_session_credentials_or_conversation(self):
        startup_diagnostics.record(self.root,self.tree,ValueError('failure '+self.secret))
        control=self.tree/'memory/.control';control.mkdir(parents=True)
        (control/'desktop-session.json').write_text(json.dumps({'state':'owner-stopped','pid':7,
            'program':str(self.root),'token':'fixture-session-token-not-in-config'}),encoding='utf8')
        private=self.tree/'memory/groups/private.jsonl';private.parent.mkdir();private.write_text('private conversation',encoding='utf8')
        (self.tree/'runner.log').write_text('old runner tail',encoding='utf8')
        with patch.object(first_start.Places,'current',return_value=SimpleNamespace(data=self.tree)):
            bundle=python_support.bundle(self.root,self.agent)
        self.addCleanup(lambda:bundle.unlink(missing_ok=True))
        with zipfile.ZipFile(bundle) as saved:
            text='\n'.join(saved.read(name).decode('utf8') for name in saved.namelist())
            self.assertIn('first-start-error.json',' '.join(saved.namelist()))
            self.assertNotIn(self.secret,text);self.assertNotIn('fixture-session-token-not-in-config',text)
            self.assertNotIn('private conversation',text)
            context=json.loads(saved.read('runtime-status.json'))
            self.assertEqual(context['session']['state'],'owner-stopped')
            self.assertEqual(context['data'],str(self.tree))
            self.assertNotIn('token',context['session'])


if __name__=='__main__':unittest.main()
