"""Native process ownership and unkillable-call honesty across exact-run reset."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import process_scope
import step_control
from run_context import RunContext
from run_manager import RunManager


class EditionStop(unittest.TestCase):
    def test_native_pending_is_not_acknowledged_and_sibling_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);manager=RunManager(base)
            ctx=RunContext.create(kind='chat_turn',goal='native ownership',principal_id='owner',scope='owner')
            manager.create(ctx,'fixture');manager.transition(ctx.run_id,'running')
            registry=step_control.start(base)
            future=concurrent.futures.Future();future.set_running_or_notify_cancel()
            selected=sibling=None
            try:
                with process_scope.bind(ctx.run_id+'/tool'):
                    selected=process_scope.spawn([sys.executable,'-c','import time; time.sleep(60)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                        **({'creationflags':0x08000000} if os.name=='nt' else {}))
                with process_scope.bind(ctx.run_id+'-sibling/tool'):
                    sibling=process_scope.spawn([sys.executable,'-c','import time; time.sleep(60)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                        **({'creationflags':0x08000000} if os.name=='nt' else {}))
                registry.track(ctx.run_id,process_scope.StepHandle(future,ctx.run_id+'/tool'))
                path=base/'memory/.control/run-cancel'/(ctx.run_id+'.json')
                step_control._write(path,dict(id='native-fixture',run_id=ctx.run_id,action='cancel'))
                deadline=time.monotonic()+5
                while selected.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                self.assertIsNotNone(selected.poll())
                self.assertIsNone(sibling.poll())
                with process_scope.bind(ctx.run_id+'/tool/late'):
                    with self.assertRaises(RuntimeError):process_scope.spawn([sys.executable,'-c','pass'])
                time.sleep(.15)
                receipt=path.with_suffix('.receipt')
                def confirmed():
                    try:return json.loads(receipt.read_text())['ok']
                    except OSError:return False  # Windows atomic-replace sharing boundary.
                self.assertFalse(confirmed())
                future.set_result('finished native operation')
                deadline=time.monotonic()+5
                while not confirmed() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(confirmed())
                self.assertIsNone(sibling.poll())
            finally:
                if not future.done():future.set_result(None)
                for proc in (selected,sibling):
                    if proc is not None:
                        process_scope.terminate(proc);proc.wait(5);process_scope.release(proc)
                registry.close()

    @unittest.skipUnless(os.name=='nt','Windows Job accounting')
    def test_job_remains_pending_after_parent_exits_with_a_live_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            marker=Path(directory)/'child-ready'
            code="import pathlib,time; pathlib.Path(%r).write_text('ready'); time.sleep(60)" % str(marker)
            script="import subprocess,sys; subprocess.Popen([sys.executable,'-c',%r],creationflags=0x08000000)" % code
            scope='run-'+Path(directory).name+'/tool'
            with process_scope.bind(scope):
                proc=process_scope.spawn([sys.executable,'-c',script],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
            future=concurrent.futures.Future();future.set_result(None)
            handle=process_scope.StepHandle(future,scope)
            try:
                proc.wait(5)
                deadline=time.monotonic()+5
                while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(marker.exists())
                self.assertIsNone(handle.poll())
                handle.stop_tree()
                deadline=time.monotonic()+5
                while handle.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                self.assertEqual(handle.poll(),0)
            finally:
                process_scope.terminate(proc);process_scope.release(proc)


if __name__=='__main__':unittest.main()
