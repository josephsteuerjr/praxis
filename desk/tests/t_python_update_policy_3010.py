"""Normal Python updates preserve edits; legacy native migration remains clean."""
import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).parent)]
from localharness import desktop_update_request as jobs,code_transition,update_source,python_updates
from localharness.package_notice import read,write

class Policy(unittest.TestCase):
    def test_unknown_programme_never_acquires_preserve_permission(self):
        self.assertEqual(jobs.new_source_mode(),'clean')
        self.assertEqual(jobs.new_source_mode('clean'),'clean')
        with self.assertRaises(ValueError):jobs.new_source_mode('preserve')
        with self.assertRaises(ValueError):jobs.new_source_mode('unknown')

    def test_confirm_keeps_exact_proposal_policy_without_silent_mode_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);home=root/'data';home.mkdir()
            write(root/'python-app.json',{'version':'2.3.0'})
            from localharness import authored_baseline
            (root/'tree').mkdir();(root/'tree/module.py').write_text('base=True\n')
            (root/'app').mkdir();(root/'app/fixture.py').write_text('app=True\n')
            code_transition.seal(root);authored_baseline.seal(root)
            proposed=jobs.request(home,'plan',root=root)
            self.assertEqual(proposed['source_mode'],'preserve')
            with self.assertRaisesRegex(ValueError,'Source policy changed'):
                jobs.request(home,'confirm',root=root,identity=proposed['id'],by='window',source_mode='clean')
            self.assertEqual(jobs.status(home)['state'],'awaiting')
            confirmed=jobs.request(home,'confirm',root=root,identity=proposed['id'],by='window')
            self.assertEqual(confirmed['source_mode'],'preserve');self.assertEqual(confirmed['state'],'confirmed')

    def test_legacy_native_admission_never_inherits_python_preserve_default(self):
        from localharness import migration_job,windows_installation
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);package=root/'new';old=root/'old';package.mkdir();old.mkdir()
            with patch.object(windows_installation,'default_data',return_value=str(root/'data')):
                # Refusal occurs at the explicit migration policy, before runtime,
                # process, data or reviewer admission can have side effects.
                with self.assertRaisesRegex(ValueError,'Migration installs clean code'):
                    migration_job.admit(package,package,old=old,source_mode='preserve')

if __name__=='__main__':unittest.main()
