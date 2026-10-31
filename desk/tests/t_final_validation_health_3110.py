"""Owned real read threads/handles; synthetic health clock, no OS authority."""
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import importlib
import io
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest.mock import Mock,patch

sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
from localharness import portable_switch as switch,validation_read as reads
from localharness import first_start,update_profile,windows_first_start
import t_portable_switch_3010 as fixture

_POPEN=subprocess.Popen


class _ReadProcess:
    """Private fake for exact timeout/error dispatch, never an OS process."""
    def __init__(self,returncode=0,pending=False,failure=None):
        self.returncode=None if pending else returncode;self.failure=failure
        self.stdout=io.BytesIO();self.stderr=io.BytesIO();self.calls=[];self.killed=False
    def __enter__(self):return self
    def __exit__(self,*_args):self.stdout.close();self.stderr.close()
    def communicate(self,timeout=None):
        self.calls.append(timeout)
        if self.failure is not None:
            error=self.failure;self.failure=None;raise error
        if self.returncode is None and timeout is not None:
            raise subprocess.TimeoutExpired('private-read',timeout,output=b'part',stderr=b'part-error')
        return b'complete-output',b'complete-error'
    def poll(self):return self.returncode
    def kill(self):self.killed=True;self.returncode=-9


class MonitoredReads(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.ScratchCustody('runTest');self.addCleanup(self.fx.doCleanups);self.fx.setUp()
        self.old,self.new=self.fx.old,self.fx.new
        self.value={**self.fx.value,'selection':{'reviewed':True},'payload':{'reviewed':True}}
        self.clock=0.;self.samples=[];self.sampled=threading.Condition()
        self.window=switch.HealthWindow(since=-switch.TRIAL_SECONDS,previous=0.,identity='generation')
        self.path=self.fx.root/'read-only-payload.bin';self.path.write_bytes(b'actual read bytes\n')
        self.effects=[];self.backend=Mock()
        self.backend.assert_quiet=Mock()
        self.backend.remove.side_effect=lambda root:(self.effects.append(('remove',root)),root.rmdir())
        self.enterContext(patch.object(windows_first_start,'Backend',return_value=self.backend))
        self.enterContext(patch.object(switch,'cleanup_work'))
        self.enterContext(patch.object(first_start,'_write'))
        self.enterContext(patch.object(switch,'read',return_value={}))
        self.enterContext(patch.object(update_profile,'selection',return_value=self.value['selection']))
        self.enterContext(patch.object(switch,'VALIDATION_STOP_POLL',.01))

    def health(self,_program):
        with self.sampled:
            self.samples.append((self.clock,threading.current_thread()))
            self.sampled.notify_all()
        return 'generation'

    def start_finish(self,validate,*,stopped=None):
        errors=[]
        def run():
            try:switch.finish(self.old,self.new,self.value,self.window,stopped=stopped)
            except BaseException as error:errors.append(error)
        self.enterContext(patch.object(switch,'validate',side_effect=validate))
        thread=threading.Thread(target=run,name='private-admission-caller')
        thread.start()
        return thread,errors

    def test_long_real_read_keeps_caller_samples_and_does_not_create_its_own_gap(self):
        entered=threading.Event();release=threading.Event();read_threads=[]
        def validate(*_args):
            read_threads.append(threading.current_thread())
            with self.path.open('rb') as stream:
                entered.set()
                while not release.wait(.01):reads.checkpoint()
                self.assertEqual(stream.read(),b'actual read bytes\n')
        with patch.object(switch.time,'monotonic',side_effect=lambda:self.clock),patch.object(switch,'health',side_effect=self.health):
            thread,errors=self.start_finish(validate)
            try:
                self.assertTrue(entered.wait(3))
                seen=[]
                for now in (5.,10.,15.,20.):
                    with self.sampled:
                        self.clock=now
                        seen.append(self.sampled.wait_for(lambda:any(at==now for at,_ in self.samples),timeout=3))
                release.set();thread.join(3)
            finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assertTrue(all(seen),'Read-only validation must keep actual caller observations across its full duration')
        self.assertEqual(self.window.since,-switch.TRIAL_SECONDS)
        self.assertTrue(self.effects);self.assertTrue(all(owner is thread for _,owner in self.samples))
        self.assertTrue(all(not owner.is_alive() for owner in read_threads))

    def test_pause_gap_generation_and_unknown_health_cancel_join_without_effects(self):
        for kind in ('pause','gap','reversal','generation','unknown'):
            with self.subTest(kind=kind):
                self.clock=0.;self.samples=[];self.window=switch.HealthWindow(since=-300,previous=0,identity='generation')
                if kind=='reversal':self.window.previous=10.
                entered=threading.Event();workers=[];handles=[]
                def validate(*_args):
                    workers.append(threading.current_thread())
                    with self.path.open('rb') as stream:
                        handles.append(stream.fileno());entered.set()
                        while True:reads.checkpoint();stream.seek(0);stream.read();time.sleep(.002)
                def health(_program):
                    if kind=='pause':raise switch.TrialPaused('actual explicit pause proof')
                    if kind=='unknown':raise RuntimeError('unconfirmed heartbeat')
                    return 'new-generation' if kind=='generation' else 'generation'
                with patch.object(switch.time,'monotonic',side_effect=lambda:self.clock),patch.object(switch,'health',side_effect=health):
                    thread,errors=self.start_finish(validate)
                    self.assertTrue(entered.wait(3));self.clock=20. if kind=='gap' else 5.
                    thread.join(3)
                self.assertFalse(thread.is_alive());self.assertEqual(len(errors),1)
                expected={'pause':switch.TrialPaused,'gap':switch.TrialRestart,'reversal':switch.TrialRestart,
                    'generation':switch.TrialRestart,'unknown':RuntimeError}[kind]
                self.assertIsInstance(errors[0],expected)
                if kind=='unknown':self.assertNotIsInstance(errors[0],(switch.TrialPaused,switch.TrialRestart))
                self.assertTrue(self.old.is_dir());self.assertEqual(self.effects,[])
                self.assertTrue(all(not worker.is_alive() for worker in workers))
                for handle in handles:
                    with self.assertRaises(OSError):os.fstat(handle)

    def test_stop_between_real_chunks_joins_nested_pool_and_closes_every_handle(self):
        self.path.write_bytes(b'x'*(8*1024*1024))
        stop=threading.Event();entered=threading.Event();workers=[];handles=[];chunks=[]
        class SlowReader:
            def __init__(self,stream):self.stream=stream
            def read(inner,size):
                raw=inner.stream.read(min(size,65536));chunks.append(len(raw));entered.set();time.sleep(.02)
                return raw
        def validate(*_args):
            workers.append(threading.current_thread());check=reads.current()
            def measure(_item):
                workers.append(threading.current_thread())
                with self.path.open('rb') as stream:
                    handles.append(stream.fileno());return reads.digest(SlowReader(stream),check)
            return reads.parallel(measure,range(20),workers=2)
        actual=time.monotonic()
        self.window=switch.HealthWindow(since=actual-300,previous=actual,identity='generation')
        with patch.object(switch,'health',return_value='generation'):
            thread,errors=self.start_finish(validate,stopped=stop.is_set)
            self.assertTrue(entered.wait(3));at=time.monotonic();stop.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertLess(time.monotonic()-at,3)
        self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],InterruptedError)
        self.assertTrue(chunks);self.assertLess(sum(chunks),20*self.path.stat().st_size)
        self.assertTrue(all(not worker.is_alive() for worker in workers));self.assertTrue(self.old.is_dir())
        self.assertEqual(self.effects,[]);self.backend.remove.assert_not_called()
        for handle in handles:
            with self.assertRaises(OSError) as caught:os.fstat(handle)
            self.assertEqual(caught.exception.errno,errno.EBADF)
        moved=self.path.with_suffix('.released');self.path.rename(moved);moved.rename(self.path)

    def test_real_validation_error_is_not_relabelled_as_a_pause(self):
        entered=threading.Event()
        def validate(*_args):
            entered.set()
            try:
                while True:reads.checkpoint();time.sleep(.002)
            except reads.ReadCancelled:raise ValueError('actual malformed custody proof')
        with patch.object(switch.time,'monotonic',side_effect=lambda:self.clock),patch.object(switch,'health',side_effect=switch.TrialPaused('pause')):
            thread,errors=self.start_finish(validate);self.assertTrue(entered.wait(3));self.clock=5.;thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(len(errors),1)
        self.assertIsInstance(errors[0],ValueError);self.assertIn('malformed',str(errors[0]))
        self.assertEqual(self.effects,[])

    def test_fast_invalid_validation_refuses_before_any_readiness_rpc(self):
        with patch.object(switch,'validate',side_effect=ValueError('bad prepared payload')),patch.object(switch,'health') as health:
            with self.assertRaisesRegex(ValueError,'bad prepared payload'):
                switch.finish(self.old,self.new,self.value,self.window)
        health.assert_not_called();self.backend.remove.assert_not_called()

    def test_stop_has_priority_over_a_simultaneous_validation_error(self):
        stop=threading.Event();entered=threading.Event();workers=[]
        def validate(*_args):
            workers.append(threading.current_thread());entered.set()
            while not stop.wait(.002):reads.checkpoint()
            raise ValueError('real invalid bytes at Stop')
        with patch.object(switch,'health') as health:
            thread,errors=self.start_finish(validate,stopped=stop.is_set)
            self.assertTrue(entered.wait(3));stop.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(len(errors),1)
        self.assertIsInstance(errors[0],InterruptedError);health.assert_not_called()
        self.assertTrue(all(not worker.is_alive() for worker in workers));self.assertEqual(self.effects,[])

    def test_stop_inside_actual_selected_upstream_zip_read_closes_before_effects(self):
        from localharness import code_transition,source_merge
        from localharness.package_notice import write
        tree=self.new/'tree';tree.mkdir();content=b'#'+b'x'*(8*1024*1024)+b'\n'
        (tree/'module.py').write_bytes(content)
        files=source_merge.inventory(tree);archive=self.new/'code-upstream.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_STORED) as output:
            output.writestr('module.py',content)
        old_tree=self.old/'tree';old_tree.mkdir();(old_tree/'module.py').write_bytes(b'old=1\n')
        write(self.old/'helene.json',{'mode':'local','tree':str(self.fx.data),'code':'tree'})
        write(self.new/'code-upstream.json',{'files':files,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()})
        write(self.new/'code-transition-plan.json',{'format':'helene-code-plan.v1','old':str(self.old),
            'sources':[str(old_tree)],'incoming':files,'selected_upstream':{'tree':files},'plans':[]})
        stop=threading.Event();revoked=threading.Event();observed=[];workers=[]
        def observe(frame,event,argument):
            # Observe the actual completed C-level file read. No Path/open,
            # hash or validator replacement changes the selected-upstream path.
            stream=getattr(argument,'__self__',None)
            if (event!='c_return' or getattr(argument,'__name__','')!='read'
                    or str(getattr(stream,'name',''))!=str(archive)):
                return
            observed.append((stream.tell(),stream.fileno()));stop.set()
            until=time.monotonic()+2
            while time.monotonic()<until:
                try:reads.checkpoint()
                except reads.ReadCancelled:revoked.set();break
                time.sleep(.002)
        def validate():
            workers.append(threading.current_thread());previous=sys.getprofile();sys.setprofile(observe)
            try:code_transition.validate(self.new)
            finally:sys.setprofile(previous)
        actual=time.monotonic();window=switch.HealthWindow(since=actual-300,previous=actual,identity='generation')
        with patch.object(switch,'health') as health:
            with self.assertRaises(InterruptedError):
                switch.monitored_validation(self.new,window,validate,stopped=stop.is_set)
        self.assertTrue(revoked.is_set());self.assertTrue(observed)
        self.assertLess(max(size for size,_ in observed),archive.stat().st_size,
            'Stop must cancel after a completed chunk, before the whole ZIP is read')
        self.assertTrue(all(not worker.is_alive() for worker in workers));health.assert_not_called()
        for _,descriptor in observed:
            with self.assertRaises(OSError):os.fstat(descriptor)
        moved=archive.with_suffix('.released');archive.rename(moved);moved.rename(archive)
        self.assertTrue(self.old.is_dir());self.assertTrue(self.new.is_dir());self.assertEqual(self.effects,[])

    def test_unpromoted_legacy_adoption_never_runs_in_read_worker(self):
        self.value['selection']={'settings':{},'sources':{},'git':{}}
        with patch.object(switch,'_admit_legacy_clean') as adopt,patch.object(switch,'health') as health:
            with self.assertRaisesRegex(ValueError,'promoted'):
                switch.finish(self.old,self.new,self.value,self.window)
        adopt.assert_not_called();health.assert_not_called();self.backend.remove.assert_not_called()

    def test_reset_discards_scan_and_next_admission_reopens_actual_bytes(self):
        entered=threading.Event();count=[]
        def scan():
            count.append(self.path.read_bytes())
            if len(count)==1:
                entered.set()
                while True:reads.checkpoint();time.sleep(.002)
            with self.path.open('rb') as stream:return reads.digest(stream)
        errors=[]
        def first():
            try:switch.monitored_validation(self.new,self.window,scan)
            except BaseException as error:errors.append(error)
        with patch.object(switch.time,'monotonic',side_effect=lambda:self.clock),patch.object(switch,'health',side_effect=switch.TrialPaused('pause')):
            thread=threading.Thread(target=first);thread.start();self.assertTrue(entered.wait(3))
            self.clock=5.;thread.join(3)
        self.assertFalse(thread.is_alive());self.assertIsInstance(errors[0],switch.TrialPaused)
        changed=b'bytes changed after discarded scan';self.path.write_bytes(changed)
        # Only exercise admission with a newly earned synthetic window; this
        # does not claim native elapsed time or restore an old validation result.
        self.window=switch.HealthWindow(since=-300,previous=5,identity='generation')
        with patch.object(switch.time,'monotonic',return_value=5),patch.object(switch,'health',return_value='generation'):
            result=switch.monitored_validation(self.new,self.window,scan)
        self.assertEqual(result,hashlib.sha256(changed).hexdigest());self.assertEqual(len(count),2)

    def test_pause_after_retirement_stop_remains_partial_and_never_removes_old(self):
        from types import SimpleNamespace
        from localharness import portable_retirement as retirement,retirement_update,owner_stop,session_exit
        value={'mode':'archive','state':'probation','targets':[{'root':str(self.old)}],'removed':[]}
        def health(_program):
            if value['state']=='commit-started':raise switch.TrialPaused('pause after backend stop')
            return 'generation'
        actual=time.monotonic();window=switch.HealthWindow(since=actual-300,previous=actual,identity='generation')
        with patch.object(retirement,'validate'),patch.object(retirement_update,'cancelled',return_value=False), \
             patch.object(owner_stop,'stopped',return_value=False),patch.object(session_exit,'requested',return_value=False), \
             patch.object(switch,'health',side_effect=health):
            with self.assertRaises(switch.TrialPaused):
                retirement.commit(self.new,value,window,places=SimpleNamespace(data=self.fx.data),backend=self.backend)
        self.assertEqual(value['state'],'partial');self.backend.stop.assert_called_once()
        self.backend.remove.assert_not_called();self.assertTrue(self.old.is_dir());self.assertTrue(self.new.is_dir())


class ReadScope(unittest.TestCase):
    def test_bare_and_package_share_carrier_without_leaking_to_unrelated_threads(self):
        folder=str(Path(reads.__file__).parent);sys.path.insert(0,folder)
        try:bare=importlib.import_module('validation_read')
        finally:sys.path.remove(folder)
        self.assertIs(bare.ReadCancelled,reads.ReadCancelled)
        def revoked():raise reads.ReadCancelled('private revocation')
        other=[]
        with reads.scope(revoked):
            self.assertIs(bare.current(),revoked)
            with self.assertRaises(reads.ReadCancelled):bare.digest(io.BytesIO(b'private'))
            thread=threading.Thread(target=lambda:other.append(reads.digest(io.BytesIO(b'unrelated'))))
            thread.start();thread.join(3);self.assertFalse(thread.is_alive())
        self.assertIsNone(reads.current());self.assertIsNone(bare.current())
        self.assertEqual(other,[hashlib.sha256(b'unrelated').hexdigest()])

    def test_scope_restores_after_failure_on_a_reused_worker(self):
        def failed():
            with reads.scope(lambda:(_ for _ in ()).throw(reads.ReadCancelled())):
                reads.digest(io.BytesIO(b'cancelled'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(reads.ReadCancelled):pool.submit(failed).result()
            self.assertIsNone(pool.submit(reads.current).result())
            self.assertEqual(pool.submit(reads.digest,io.BytesIO(b'complete bytes')).result(),hashlib.sha256(b'complete bytes').hexdigest())


class ScopedGitRead(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='final-read-git-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=self.root/'.git'
        from localharness import source_merge
        self.merge=source_merge

    def test_actual_private_git_head_matches_unscoped_result(self):
        self.merge.git(self.repo,'init','--bare')
        tree=self.merge.git(self.repo,'write-tree').stdout.decode().strip()
        commit=self.merge.git(self.repo,'commit-tree',tree,data=b'private fixture commit\n').stdout.strip()
        self.merge.git(self.repo,'update-ref','HEAD',commit.decode())
        ordinary=self.merge.git(self.repo,'rev-parse','HEAD')
        with patch.object(subprocess,'run',side_effect=AssertionError('Scoped read must use its owned child')),reads.scope(lambda:None):
            observed=self.merge.git(self.repo,'rev-parse','HEAD')
        self.assertIsInstance(observed,subprocess.CompletedProcess)
        self.assertEqual((observed.returncode,observed.stdout,observed.stderr),
            (ordinary.returncode,ordinary.stdout,ordinary.stderr))
        self.assertEqual(observed.stdout.strip(),commit)

    def test_stop_reaps_exact_private_child_and_closes_pipes_and_held_file(self):
        script=self.root/'wait.py';ready=self.root/'ready';held=self.root/'held.bin'
        script.write_text('import os,pathlib,sys,time\n'
            'with pathlib.Path(sys.argv[1]).open("wb") as stream:\n'
            ' stream.write(b"private fixture");stream.flush()\n'
            ' ready=pathlib.Path(sys.argv[2]);pending=ready.with_suffix(".pending")\n'
            ' pending.write_text(str(os.getpid()));pending.replace(ready)\n'
            ' print("private stdout",flush=True);print("private stderr",file=sys.stderr,flush=True)\n'
            ' while True:time.sleep(1)\n',encoding='utf-8')
        children=[];errors=[];stop=threading.Event()
        def launch(_command,**kwargs):
            process=_POPEN([sys.executable,'-I','-u',str(script),str(held),str(ready)],**kwargs)
            children.append(process);return process
        def revoked():
            if stop.is_set():raise reads.ReadCancelled('private owner Stop')
        def work():
            try:
                with reads.scope(revoked):self.merge.git(self.repo,'rev-parse','HEAD')
            except BaseException as error:errors.append(error)
        with patch.object(subprocess,'Popen',side_effect=launch):
            thread=threading.Thread(target=work,name='private-final-git-caller');thread.start()
            try:
                until=time.monotonic()+10
                while not ready.exists() and time.monotonic()<until:
                    if children and children[0].poll() is not None:break
                    time.sleep(.01)
                self.assertTrue(ready.is_file(),'Actual private child must be ready before Stop timing begins')
                self.assertEqual(int(ready.read_text()),children[0].pid);self.assertIsNone(children[0].poll())
                started=time.monotonic();stop.set();thread.join(3)
                settled=not thread.is_alive();elapsed=time.monotonic()-started
                closed_before_cleanup=all(child.poll() is not None and child.stdout.closed and child.stderr.closed for child in children)
            finally:
                stop.set()
                for child in children:
                    if child.poll() is None:child.kill()
                thread.join(5)
                for child in children:
                    if child.poll() is None:child.wait(timeout=3)
                    for pipe in (child.stdout,child.stderr):
                        if pipe is not None and not pipe.closed:pipe.close()
        self.assertTrue(settled);self.assertLess(elapsed,3);self.assertFalse(thread.is_alive())
        self.assertTrue(closed_before_cleanup,'Production must settle the child and close pipes before fixture cleanup')
        self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],reads.ReadCancelled)
        self.assertTrue(all(child.poll() is not None and child.stdout.closed and child.stderr.closed for child in children))
        moved=held.with_suffix('.released');held.rename(moved);moved.rename(held)

    def test_only_exact_scoped_read_uses_cancellable_dispatch(self):
        result=subprocess.CompletedProcess([],0,b'head\n',b'')
        cases=[(None,('rev-parse','HEAD'),None),
            (lambda:None,('rev-parse','--verify','HEAD'),None),
            (lambda:None,('rev-parse','HEAD'),b'input')]
        for callback,args,data in cases:
            with self.subTest(args=args,data=data),reads.scope(callback), \
                 patch.object(subprocess,'run',return_value=result) as run, \
                 patch.object(subprocess,'Popen',side_effect=AssertionError('Other commands keep the original dispatch')):
                self.assertIs(self.merge.git(self.repo,*args,data=data),result)
                self.assertEqual(run.call_args.kwargs['timeout'],120)
                self.assertEqual(run.call_args.kwargs['input'],data)
        def cancelled():raise reads.ReadCancelled('before launch')
        with reads.scope(cancelled),patch.object(subprocess,'Popen') as launch:
            with self.assertRaises(reads.ReadCancelled):self.merge.git(self.repo,'rev-parse','HEAD')
        launch.assert_not_called()

    def test_scoped_read_timeout_remains_120_and_reaps_with_output(self):
        child=_ReadProcess(pending=True)
        with reads.scope(lambda:None),patch.object(subprocess,'Popen',return_value=child), \
             patch.object(self.merge.time,'monotonic',side_effect=(0.,0.,120.)):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:self.merge.git(self.repo,'rev-parse','HEAD')
        self.assertEqual(caught.exception.timeout,120)
        self.assertEqual((caught.exception.output,caught.exception.stderr),(b'complete-output',b'complete-error'))
        self.assertTrue(child.killed);self.assertEqual(child.calls,[.2,None])
        self.assertTrue(child.stdout.closed and child.stderr.closed)

    def test_scoped_read_preserves_check_false_and_real_errors_with_cleanup(self):
        for check in (True,False):
            child=_ReadProcess(returncode=17)
            with self.subTest(check=check),reads.scope(lambda:None),patch.object(subprocess,'Popen',return_value=child):
                if check:
                    with self.assertRaisesRegex(RuntimeError,'Source merge Git failed: complete-error'):
                        self.merge.git(self.repo,'rev-parse','HEAD')
                else:
                    result=self.merge.git(self.repo,'rev-parse','HEAD',check=False)
                    self.assertEqual((result.returncode,result.stdout,result.stderr),(17,b'complete-output',b'complete-error'))
            self.assertFalse(child.killed);self.assertTrue(child.stdout.closed and child.stderr.closed)
        child=_ReadProcess(pending=True,failure=OSError('actual pipe failure'))
        with reads.scope(lambda:None),patch.object(subprocess,'Popen',return_value=child):
            with self.assertRaisesRegex(OSError,'actual pipe failure'):self.merge.git(self.repo,'rev-parse','HEAD')
        self.assertTrue(child.killed);self.assertTrue(child.stdout.closed and child.stderr.closed)


class StopPollBudget(unittest.TestCase):
    """Real private file/Exit reads; pre-aged unit window is not native300."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='stop-poll-budget-');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.program=self.base/'programme';self.program.mkdir()
        self.data=self.base/'data';self.data.mkdir();self.payload=self.base/'payload';self.payload.mkdir()
        from localharness.package_notice import write
        write(self.program/'helene.json',{'tree':str(self.data),'code':str(self.payload),'mode':'local','agent':{'name':'Private fixture'}})
        self.file=self.payload/'payload.bin';self.file.write_bytes(b'private real read bytes\n')
        from localharness import session_exit
        self.exit=session_exit;self.marker=session_exit.path(self.program)
        self.window=switch.HealthWindow(since=time.monotonic()-300,previous=time.monotonic(),identity='generation')
    def publish_exit(self):
        self.marker.parent.mkdir(parents=True,exist_ok=True)
        pending=self.marker.with_suffix('.pending');pending.write_bytes(b'private Exit intent');pending.replace(self.marker)

    def test_health_file_checkpoints_do_not_repeat_real_negative_exit_roster_per_chunk(self):
        from localharness import agents,source_merge
        for number in range(96):(self.payload/('module-'+str(number)+'.py')).write_bytes(b'value=1\n')
        expected=source_merge.inventory(self.payload);checkpoints=[]
        with reads.scope(lambda:checkpoints.append(threading.current_thread())):
            self.assertEqual(source_merge.inventory(self.payload),expected)
        rosters=[];health_calls=[];actual_roster=agents.roster
        def roster(program):
            self.assertTrue(Path(program).resolve().is_relative_to(self.base))
            rosters.append(threading.current_thread());return actual_roster(program)
        def stopped():return self.exit.requested(self.program)
        def health(_program):
            health_calls.append(threading.current_thread())
            self.assertEqual(source_merge.inventory(self.payload),expected);return 'generation'
        with patch.object(agents,'roster',side_effect=roster),patch.object(switch,'health',side_effect=health):
            started=time.monotonic()
            value=switch.monitored_validation(self.program,self.window,lambda:self.file.read_bytes(),stopped=stopped)
            elapsed=time.monotonic()-started
        self.assertEqual(value,b'private real read bytes\n');self.assertTrue(health_calls)
        # Derived from the .2-second polling budget plus forced entry,
        # before/after each real health computation, and final return.
        budget=math.ceil(elapsed/switch.VALIDATION_STOP_POLL)+2*len(health_calls)+2
        self.assertLessEqual(len(rosters),budget,
            'File/checkpoint count must not multiply negative Exit roster discovery')
        self.assertLess(len(rosters),len(checkpoints)/4)
        self.assertTrue(all(owner is threading.current_thread() for owner in rosters+health_calls))

    def test_positive_exit_latches_while_real_health_chunks_and_worker_error_settle(self):
        self.file.write_bytes(b'x'*(16*1024*1024))
        worker_entered=threading.Event();chunk=threading.Event();observed=threading.Event()
        workers=[];handles=[];sizes=[];calls_after_positive=[]
        def validator():
            workers.append(threading.current_thread());worker_entered.set()
            try:
                while True:reads.checkpoint();time.sleep(.002)
            except reads.ReadCancelled:raise ValueError('real validator error during Stop settlement')
        def stopped():
            if observed.is_set():calls_after_positive.append(True)
            value=self.exit.requested(self.program)
            if value:self.marker.unlink();observed.set()
            return value
        class SlowReader:
            def __init__(self,stream):self.stream=stream
            def read(inner,size):
                block=inner.stream.read(min(size,65536));sizes.append(len(block));chunk.set();time.sleep(.005);return block
        def health(_program):
            self.assertTrue(worker_entered.wait(3))
            with self.file.open('rb') as stream:
                handles.append(stream.fileno());reads.digest(SlowReader(stream))
            return 'generation'
        actor=threading.Thread(target=lambda:(chunk.wait(3),self.publish_exit()),name='private-exit-intent')
        actor.start()
        try:
            with patch.object(switch,'VALIDATION_HEALTH_INTERVAL',.01),patch.object(switch,'health',side_effect=health):
                with self.assertRaises(InterruptedError):
                    switch.monitored_validation(self.program,self.window,validator,stopped=stopped)
        finally:actor.join(3)
        self.assertFalse(actor.is_alive());self.assertTrue(observed.is_set())
        self.assertTrue(chunk.is_set());self.assertTrue(sizes);self.assertTrue(handles)
        self.assertEqual(calls_after_positive,[],'A positive Stop must remain latched after its marker disappears')
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertLess(sum(sizes),self.file.stat().st_size);self.assertIsNone(self.window.identity)
        for handle in handles:
            with self.assertRaises(OSError):os.fstat(handle)
        renamed=self.file.with_suffix('.released');self.file.rename(renamed);renamed.rename(self.file)

    def test_forced_final_poll_refuses_an_exit_arriving_after_negative_health_boundary(self):
        in_health=False;injected=[];effects=[]
        def health(_program):
            nonlocal in_health
            in_health=True;return 'generation'
        def stopped():
            value=self.exit.requested(self.program)
            if in_health and not injected:
                self.assertFalse(value);self.publish_exit();injected.append(True)
            return value
        with patch.object(switch,'health',side_effect=health):
            with self.assertRaises(InterruptedError):
                switch.monitored_validation(self.program,self.window,lambda:self.file.read_bytes(),stopped=stopped)
                effects.append('effect after admission')
        self.assertEqual(effects,[]);self.assertEqual(injected,[True]);self.assertIsNone(self.window.identity)

    def test_checkpoint_clock_reversal_forces_poll_inside_negative_cache(self):
        clock=[0.];continued=[];in_health=False
        window=switch.HealthWindow(since=-300,previous=0.,identity='generation')
        def health(_program):
            with self.file.open('rb') as stream:
                clock[0]=.10;reads.checkpoint();self.assertTrue(stream.read(1))
                self.publish_exit();clock[0]=.05;reads.checkpoint();continued.append(True)
            return 'generation'
        with patch.object(switch.time,'monotonic',side_effect=lambda:clock[0]),patch.object(switch,'health',side_effect=health):
            with self.assertRaises(InterruptedError):
                switch.monitored_validation(self.program,window,lambda:self.file.read_bytes(),stopped=lambda:self.exit.requested(self.program))
        self.assertEqual(continued,[]);self.assertIsNone(window.identity)

    def test_slow_negative_lookup_cache_starts_at_completion_not_call_start(self):
        clock=[0.];polls=[];health_calls=[]
        self.file.write_bytes(b'private byte chunks\n'*64)
        window=switch.HealthWindow(since=-300,previous=0.,identity='generation')
        def stopped():
            value=self.exit.requested(self.program);self.assertFalse(value)
            polls.append(True);clock[0]+=1.;return value
        class Chunked:
            def __init__(self,stream):self.stream=stream
            def read(inner,size):return inner.stream.read(min(size,16))
        def health(_program):
            health_calls.append(True)
            with self.file.open('rb') as stream:reads.digest(Chunked(stream))
            return 'generation'
        with patch.object(switch.time,'monotonic',side_effect=lambda:clock[0]),patch.object(switch,'health',side_effect=health):
            result=switch.monitored_validation(self.program,window,lambda:self.file.read_bytes(),stopped=stopped)
        self.assertEqual(result,self.file.read_bytes())
        self.assertLessEqual(len(polls),2*len(health_calls)+2)
        self.assertEqual(window.since,-300)


class ReadSamplingPriority(unittest.TestCase):
    """Actual nested file readers park for caller health; no OS authority."""
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='health-read-priority-');self.addCleanup(temp.cleanup)
        self.file=Path(temp.name)/'private.bin';self.file.write_bytes(b'x'*(4*1024*1024))
        self.expected=hashlib.sha256(self.file.read_bytes()).hexdigest()
        now=time.monotonic();self.window=switch.HealthWindow(since=now-300,previous=now,identity='generation')

    def nested_reads(self,check,entered,inside,attempts,crossed,condition,workers,handles,cancelled):
        def measured(_number):
            workers.append(threading.current_thread())
            def observed_check():
                began_during_health=inside.is_set()
                if began_during_health:
                    with condition:attempts.add(threading.get_ident());condition.notify_all()
                try:check()
                except reads.ReadCancelled:
                    cancelled.append(threading.current_thread());raise
                if began_during_health and inside.is_set():
                    with condition:crossed.append(threading.get_ident());condition.notify_all()
            class Chunked:
                def __init__(self,stream):self.stream=stream
                def read(inner,size):
                    block=inner.stream.read(min(size,65536));entered.set();time.sleep(.003);return block
            with self.file.open('rb') as stream:
                handles.append(stream.fileno());return reads.digest(Chunked(stream),observed_check)
        return reads.parallel(measured,range(2),workers=2)

    def test_nested_actual_readers_do_not_cross_checkpoints_during_caller_health(self):
        entered=threading.Event();inside=threading.Event();condition=threading.Condition()
        attempts=set();crossed=[];workers=[];handles=[];cancelled=[];health_calls=[]
        caller=threading.current_thread()
        def validator():
            return self.nested_reads(reads.current(),entered,inside,attempts,crossed,condition,workers,handles,cancelled)
        def health(_program):
            self.assertIs(threading.current_thread(),caller);health_calls.append(True)
            if len(health_calls)==1:
                self.assertTrue(entered.wait(3));inside.set()
                try:
                    with condition:
                        self.assertTrue(condition.wait_for(lambda:len(attempts)==2,timeout=3))
                        self.assertFalse(condition.wait_for(lambda:bool(crossed),timeout=.05),
                            'Scan readers must park while actual caller health is in progress')
                finally:inside.clear()
            return 'generation'
        with patch.object(switch,'VALIDATION_HEALTH_INTERVAL',.01),patch.object(switch,'VALIDATION_STOP_POLL',.005), \
             patch.object(switch,'health',side_effect=health):
            result=switch.monitored_validation(self.file.parent,self.window,validator)
        self.assertEqual(result,[self.expected,self.expected]);self.assertEqual(crossed,[])
        self.assertEqual(len(attempts),2);self.assertEqual(cancelled,[])
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        for handle in handles:
            with self.assertRaises(OSError):os.fstat(handle)

    def test_stop_during_health_wakes_parked_nested_readers_and_joins_before_return(self):
        entered=threading.Event();inside=threading.Event();stop=threading.Event();condition=threading.Condition()
        attempts=set();crossed=[];workers=[];handles=[];cancelled=[];effects=[]
        def validator():
            return self.nested_reads(reads.current(),entered,inside,attempts,crossed,condition,workers,handles,cancelled)
        def health(_program):
            self.assertTrue(entered.wait(3));inside.set()
            try:
                with condition:self.assertTrue(condition.wait_for(lambda:len(attempts)==2,timeout=3))
                stop.set()
            finally:inside.clear()
            return 'generation'
        with patch.object(switch,'VALIDATION_HEALTH_INTERVAL',.01),patch.object(switch,'VALIDATION_STOP_POLL',.005), \
             patch.object(switch,'health',side_effect=health):
            with self.assertRaises(InterruptedError):
                switch.monitored_validation(self.file.parent,self.window,validator,stopped=stop.is_set)
                effects.append('retirement effect')
        self.assertEqual(effects,[]);self.assertEqual(crossed,[]);self.assertEqual(len(attempts),2)
        self.assertTrue(cancelled);self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertIsNone(self.window.identity)
        for handle in handles:
            with self.assertRaises(OSError):os.fstat(handle)
        released=self.file.with_suffix('.released');self.file.rename(released);released.rename(self.file)

    def test_next_sample_uses_start_time_instead_of_adding_health_duration(self):
        clock=[0.];entered=threading.Event();release=threading.Event();sampled=threading.Condition()
        starts=[];errors=[];owners=[]
        window=switch.HealthWindow(since=-300,previous=0.,identity='generation')
        def validator():
            entered.set()
            with self.file.open('rb') as stream:
                while not release.wait(.001):reads.checkpoint()
                return reads.digest(stream)
        def health(_program):
            with sampled:
                starts.append(clock[0]);owners.append(threading.current_thread())
                if len(starts)==1:clock[0]=11.
                sampled.notify_all()
            return 'generation'
        def run():
            try:switch.monitored_validation(self.file.parent,window,validator)
            except BaseException as error:errors.append(error)
        with patch.object(switch.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(switch,'VALIDATION_STOP_POLL',.005),patch.object(switch,'health',side_effect=health):
            caller=threading.Thread(target=run,name='private-sample-cadence');caller.start()
            try:
                self.assertTrue(entered.wait(3))
                with sampled:
                    clock[0]=5.
                    self.assertTrue(sampled.wait_for(lambda:len(starts)>=2,timeout=3),
                        'A six-second health sample must not add another five seconds before observing again')
            finally:release.set();caller.join(3)
        self.assertFalse(caller.is_alive());self.assertEqual(errors,[])
        self.assertEqual(starts[:2],[5.,11.]);self.assertTrue(all(owner is caller for owner in owners))
        self.assertEqual(window.since,-300)


if __name__=='__main__':unittest.main()
