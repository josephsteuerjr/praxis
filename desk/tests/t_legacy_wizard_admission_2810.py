from pathlib import Path
import ast,json,sys,tempfile,threading,time,unittest
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import python_source_updater as updater
from python_migration_window import MigrationWindow

class WizardAdmission(unittest.TestCase):
    def test_original_211_consumer_gets_shown_receipt_before_source_work(self):
        fixture=Path(__file__).parent/'fixtures/update211/python_updates.py'
        parsed=ast.parse(fixture.read_text(encoding='utf-8'))
        function=next(n for n in parsed.body if isinstance(n,ast.FunctionDef) and n.name=='await_admission')
        namespace={'Path':Path,'json':json,'time':time}
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(fixture),'exec'),namespace)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';old.mkdir();stage=root/'.helene-update-test';new=stage/'Helene';new.mkdir(parents=True)
            def shown(args):
                path=Path(args[args.index('--admission')+1]);path.write_text(json.dumps({'pid':987,'target':str(old),'wizard_opened':True,'preparation_completed':False}))
                return 0
            argv=['updater','--old',str(old),'--new',str(new),'--pid','123','--staging',str(stage)]
            with patch.object(sys,'argv',argv),patch('helene_setup.main',side_effect=shown),patch('localharness.source_admission.activate') as activate,patch('localharness.migration_source.verify') as verify:
                self.assertEqual(updater.main(),0)
                receipt=namespace['await_admission'](SimpleNamespace(pid=987,poll=lambda:None),stage,old)
                self.assertTrue(receipt['wizard_opened']);activate.assert_not_called();verify.assert_not_called()
    def click(self,cancel=False):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';old.mkdir();(old/'helene.json').write_text('{}')
            app=object.__new__(MigrationWindow);app.root=root/'new';app.running=threading.Lock();app.review_cancelled=threading.Event()
            app.progress=lambda value:None;events=[]
            def activate(path):
                events.append('activate')
                if cancel:app.review_cancelled.set()
            with patch('localharness.source_admission.activate',side_effect=activate),patch('localharness.migration_source.verify',side_effect=lambda path:events.append('verify')),patch('localharness.windows_installation.run',side_effect=lambda *a,**kw:events.append('install') or {'state':'completed'}):
                call=lambda:app.invoke('migration_install',{'old':str(old),'target':str(old),'data':str(root/'data'),'sourceMode':'preserve'})
                if cancel:
                    with self.assertRaisesRegex(RuntimeError,'отменено'):call()
                else:self.assertEqual(call()['state'],'completed')
            self.assertEqual(events,['activate','verify']+([] if cancel else ['install']))
            self.assertFalse(app.running.locked())
    def test_verification_after_click_before_any_installation(self):self.click()
    def test_cancel_during_verification_never_installs(self):self.click(True)

class PreparationLease(unittest.TestCase):
    def check(self,stale):
        from localharness import windows_installation as installation
        from python_migrate import installation_lock
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';old.mkdir();(old/'python-app.json').write_text('{}')
            new=root/'.helene-update-lease'/'Helene';new.mkdir(parents=True)
            events=[]
            def prepare(*args,**kwargs):
                # The unmodified old Python startup must acquire this lease.
                with installation_lock(old):events.append('old_startup')
            def validate(path):
                events.append('validate')
                if stale:raise ValueError('Local code changed')
            def stop(path):
                events.append('stop')
                with self.assertRaises(Exception):
                    with installation_lock(old):pass
                raise RuntimeError('transaction boundary reached')
            with patch('localharness.migration_source.verify'),patch('localharness.python_updates.prepare_code',side_effect=prepare),patch('localharness.code_transition.validate',side_effect=validate),patch.object(installation.migration_windows,'service',return_value=None),patch.object(installation.migration_windows,'stop',side_effect=stop):
                with self.assertRaisesRegex(ValueError if stale else RuntimeError,'Local code changed' if stale else 'transaction boundary reached'):
                    installation.in_place(new,old,progress=lambda stage:None)
            self.assertEqual(events,['old_startup','validate']+([] if stale else ['stop']))
    def test_old_startup_unlocked_then_replacement_locked(self):self.check(False)
    def test_stale_code_rejected_before_old_shutdown(self):self.check(True)

if __name__=='__main__':unittest.main()
