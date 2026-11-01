"""Actual partial test activity, without treating telemetry as a PASS receipt."""
import ast,hashlib,json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import source_update_parallel as parallel
from localharness.package_notice import read,write


class Progress(unittest.TestCase):
    def test_live_case_and_finished_count_arrive_before_blocked_case_finishes(self):
        with tempfile.TemporaryDirectory(prefix='test-progress-') as temporary:
            base=Path(temporary);work=base/'work';private=work/'slot-0';root=private/'repository';root.mkdir(parents=True)
            source=root/'fixture_progress.py';cache=base/'reports';cache.mkdir();lane='live-case-fixture'
            report=cache/(hashlib.sha256(lane.encode()).hexdigest()[:20]+'.cases.json')
            release=private/'release';started=private/'started'
            body='''import os,time,unittest
from pathlib import Path
class Cases(unittest.TestCase):
 def test_01_finishes(self):self.assertTrue(True)
 def test_02_waits(self):
  Path(os.environ['PROGRESS_STARTED']).write_text('executing')
  while not Path(os.environ['PROGRESS_RELEASE']).exists():time.sleep(.02)
'''
            source.write_text(body,encoding='utf8')
            methods=[node.name for node in ast.walk(ast.parse(body)) if isinstance(node,ast.FunctionDef) and node.name.startswith('test_')]
            adapter=Path(__file__).resolve().parents[1]/'localharness/source_verification_runner.py'
            env=dict(os.environ,PROGRESS_STARTED=str(started),PROGRESS_RELEASE=str(release),PYTHONDONTWRITEBYTECODE='1')
            with (base/'child.log').open('wb') as log:
                child=subprocess.Popen([sys.executable,'-B',str(adapter),'--root',str(root),'--source',str(source),
                    '--report',str(report),'--sandbox',str(private)],env=env,stdout=log,stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                try:
                    deadline=time.monotonic()+15
                    while time.monotonic()<deadline and not started.is_file():
                        self.assertIsNone(child.poll(),(base/'child.log').read_text(errors='replace'));time.sleep(.02)
                    self.assertTrue(started.is_file());self.assertIsNone(child.poll());self.assertFalse(report.exists())
                    observed=parallel.live_progress(work,cache,0,{'phase':'running','lane':lane})
                    self.assertEqual(observed['tests_collected'],len(methods))
                    self.assertEqual(observed['tests_completed'],len(methods)-1)
                    self.assertTrue(observed['current_test'].endswith(methods[-1]))
                    # A stale command cannot lend its progress to a successor.
                    self.assertEqual(parallel.live_progress(work,cache,0,{'phase':'running','lane':'another-command'}),{})
                    release.write_text('continue');self.assertEqual(child.wait(timeout=15),0,(base/'child.log').read_text(errors='replace'))
                    final=read(report);self.assertTrue(final['complete']);self.assertTrue(final['coverage_complete'])
                    self.assertEqual(len(final['collected']),len(methods));self.assertEqual(final['tests_run'],len(methods))
                finally:
                    release.write_text('continue')
                    if child.poll() is None:child.kill();child.wait(timeout=10)


if __name__=='__main__':unittest.main()
