"""Real child startup: candidate hooks cannot precede the trusted write fence."""
import json,os,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import source_update_battery as battery

class TrustedStartup(unittest.TestCase):
    def run_child(self,hook):
        temporary=tempfile.TemporaryDirectory(prefix='trusted-startup-')
        self.addCleanup(temporary.cleanup);base=Path(temporary.name).resolve()
        slot=base/'slot';repository=slot/'repository';cache=base/'evidence'
        for path in (repository/'live',repository/'desk',cache):path.mkdir(parents=True)
        outside=base/'outside-slot.txt';inside=slot/'inside-slot.txt'
        (repository/'live/sitecustomize.py').write_text(
            'from pathlib import Path\nPath('+repr(str(outside if hook=='outside' else inside))+').write_text("candidate hook")\n',encoding='utf8')
        test=repository/'desk/t_actual_sdk.py'
        test.write_text('import unittest\nclass SDK(unittest.TestCase):\n def test_package(self):\n  import psutil\n  self.assertTrue(psutil.__version__)\n',encoding='utf8')
        # This unit's only SDK requirement is the real psutil import above.
        # The product's complete version-bound lock is tested by its full gate.
        (cache/'executor-lock.json').write_text(json.dumps({'platform':sys.platform,
            'python_version':sys.version,'python_packages':{}}),encoding='utf8')
        env=battery._environment(slot/'home',repository/'live',repository/'desk')
        lane={'id':'trusted-startup-'+hook,'kind':'unittest-file','root':'desk',
              'source':'desk/t_actual_sdk.py','sources':['desk/t_actual_sdk.py']}
        runner=Path(battery.__file__).with_name('source_verification_runner.py')
        result=battery._run_lane(lane,repository,runner,{'python':sys.executable},env,cache,lambda:False)
        detail='\n'.join((cache/name).read_text(encoding='utf8',errors='replace') for name in result.get('logs',{}))
        return result,outside,inside,detail

    def test_candidate_startup_write_outside_inner_slot_is_refused(self):
        result,outside,inside,detail=self.run_child('outside')
        self.assertFalse(outside.exists(),detail)
        self.assertEqual(result['status'],'failed',detail)
        self.assertIn('Verification write outside isolated work',detail)

    def test_owned_hook_and_actual_runtime_sdk_work_after_fence(self):
        result,outside,inside,detail=self.run_child('inside')
        self.assertEqual(result['status'],'passed',detail)
        self.assertEqual(inside.read_text(),'candidate hook')
        self.assertFalse(outside.exists())

if __name__=='__main__':unittest.main()
