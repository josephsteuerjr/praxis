"""Continuous-working trial observations; private files and fake process authority."""
import copy
from contextlib import nullcontext,redirect_stdout
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
import zipfile
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import portable_switch as switch,session_host,agents,owner_stop
from localharness import python_source_updater
from localharness import desktop_update_request as jobs
from localharness.package_notice import read,write


class WorkingTreeHealth(unittest.TestCase):
    NOW=1_800_000_000.0

    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='portable-health-private-')
        self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name)
        self.new=self.base/'New programme';self.tree=self.base/'Canonical data'
        self.code=self.new/'tree';self.code.mkdir(parents=True)
        (self.code/'agent.py').write_bytes(b'actual declared synthetic candidate source\n')
        (self.new/'app').mkdir();(self.new/'app/python_app.py').write_bytes(b'# private application payload\n')
        with zipfile.ZipFile(self.new/'code-upstream.zip','w') as archive:
            archive.writestr('agent.py',(self.code/'agent.py').read_bytes())
        write(self.new/'python-package-files.json',{'format':'helene-python-program-files.v1','files':{}})
        write(self.new/'python-app.json',{'format':'helene-python-app.v1','version':'fixture'})
        write(self.new/'helene.json',{'mode':'local','setup_complete':True,'tree':str(self.tree),
            'code':'tree','port':32001,'model':{'framework':'openai','model':'fixture','key':'fixture'}})
        self.descriptor={'program':str(self.new),'pid':41001,'birth':10.0,'generation':'a'*32}
        write(session_host.control_path(self.new),self.descriptor)
        self.reader_path=self.tree/'memory/.control/desk_inbox/.reader.json'
        self.marker=self.tree/'memory/.control/autonomy-paused.json'
        self.reader={'pid':41003,'at':self.NOW,'started':'2026-10-31T00:00:00Z',
            'code':str(self.code),'autonomy':{'protocol':1,'paused':False,'stopped':False},
            'busy':False,'run':'','since':0.0}
        write(self.reader_path,self.reader)
        self.proof={'id':'main','tree':str(self.tree),'code':str(self.code),
            'channel_pid':41002,'runner_pid':41003,'problems':{}}
        self.rpc=Mock(side_effect=lambda *_args,**_kw:copy.deepcopy(self.proof))
        self.enterContext(patch.object(session_host,'_rpc',side_effect=self.rpc))
        self.enterContext(patch.object(session_host,'process_state',return_value='alive'))
        self.enterContext(patch.object(switch.time,'time',return_value=self.NOW))
        self.births={41002:20.0,41003:30.0}
        self.process=Mock(side_effect=lambda pid:SimpleNamespace(create_time=lambda:self.births[pid]))
        self.no_process=type('NoSuchProcess',(Exception,),{})
        self.denied=type('AccessDenied',(Exception,),{})
        forbidden=Mock(side_effect=AssertionError('No live inventory/process/native authority'))
        self.enterContext(patch.dict(sys.modules,{'psutil':SimpleNamespace(Process=self.process,
            process_iter=forbidden,pids=forbidden,pid_exists=forbidden,
            NoSuchProcess=self.no_process,AccessDenied=self.denied)}))
        # Health must inspect every admitted data tree, not this process's
        # unrelated global owner_stop._tree or real Known Folder state.
        self.runtime_paused=owner_stop.paused
        self.enterContext(patch.object(owner_stop,'paused',side_effect=forbidden))

    def _reader(self,**changes):
        value=copy.deepcopy(self.reader);value.update(changes);write(self.reader_path,value)
        return value

    def test_fresh_unpaused_generation_is_stable_across_heartbeat_and_activity_updates(self):
        identity=switch.health(self.new)
        self._reader(at=self.NOW-1,busy=True,run='ordinary-work',since=self.NOW-20)
        self.assertEqual(switch.health(self.new),identity)
        self.assertTrue(identity)

    def test_live_runner_false_to_true_pause_and_stop_are_not_healthy_work(self):
        self.assertTrue(switch.health(self.new))
        for flag in ('paused','stopped'):
            with self.subTest(flag=flag):
                state={'protocol':1,'paused':False,'stopped':False};state[flag]=True
                self._reader(autonomy=state)
                with self.assertRaises(RuntimeError):switch.health(self.new)

    def test_pause_marker_presence_rejects_even_false_payload_and_config_does_not_override_resume(self):
        config=read(self.new/'helene.json');config['autonomy']={'paused':True};write(self.new/'helene.json',config)
        self.assertTrue(switch.health(self.new),'Runtime pause truth is not a stale configuration preference')
        write(self.marker,{'paused':False})
        with self.assertRaises(RuntimeError):switch.health(self.new)
        self.marker.unlink()
        self.assertTrue(switch.health(self.new))

    def test_pause_arriving_during_readiness_is_rejected_before_observation_returns(self):
        def pause(*_args,**_kwargs):
            write(self.marker,{'via':'owner pause while readiness was measured'})
            return copy.deepcopy(self.proof)
        self.rpc.side_effect=pause
        with self.assertRaises(RuntimeError):switch.health(self.new)

    def test_unreadable_pause_marker_is_not_treated_as_absence(self):
        stat=Path.stat
        def denied(path,*args,**kwargs):
            if path==self.marker:raise PermissionError('private synthetic pause marker denied')
            return stat(path,*args,**kwargs)
        with patch.object(Path,'stat',denied):
            with self.assertRaises(RuntimeError):switch.health(self.new)

    def test_pause_in_another_admitted_agent_tree_blocks_the_programme_trial(self):
        data=self.base/'Second canonical agent'
        config=read(self.new/'helene.json');config.update(tree=str(data),code=str(self.code),port=32002)
        write(self.new/'agents/second/helene.json',config)
        reader=copy.deepcopy(self.reader);reader['pid']=42003
        write(data/'memory/.control/desk_inbox/.reader.json',reader)
        proof={**self.proof,'id':'second','tree':str(data),'channel_pid':42002,'runner_pid':42003}
        self.births.update({42002:21.0,42003:31.0})
        self.rpc.side_effect=lambda _desc,_action,args,**_kw:copy.deepcopy(self.proof if args['agent']=='main' else proof)
        marker=data/'memory/.control/autonomy-paused.json'
        write(marker,{'via':'selected second agent pause'})
        with self.assertRaises(RuntimeError):switch.health(self.new)
        marker.unlink()
        self.assertTrue(switch.health(self.new))

    def test_unknown_stale_foreign_or_malformed_reader_cannot_confirm_work(self):
        for changes in ({'autonomy':{}},{'autonomy':{'protocol':1,'paused':'false','stopped':False}},
                        {'autonomy':{'protocol':2,'paused':False,'stopped':False}},
                        {'at':self.NOW-16},{'at':self.NOW+1},{'at':'unknown'},
                        {'pid':42003},{'code':str(self.base/'former tree')},{'started':''}):
            with self.subTest(changes=changes):
                self._reader(**changes)
                with self.assertRaises((RuntimeError,ValueError)):switch.health(self.new)

    def test_new_process_birth_changes_health_identity_even_with_same_pid_and_code(self):
        first=switch.health(self.new)
        self.births[41003]=31.0
        self.assertNotEqual(switch.health(self.new),first)

    def test_real_fast_pause_resume_changes_generation_without_heartbeat_or_mtime_credit(self):
        first=switch.health(self.new)
        window=switch.HealthWindow()
        for at in range(0,300,5):self.assertFalse(window.observe(first,float(at)))
        before=self.reader_path.read_bytes()
        pause=owner_stop.change_pause(self.tree,True,via='private health fixture')
        self.assertTrue(self.marker.is_file());self.assertIs(owner_stop.pause_generation(self.tree)['paused'],True)
        resume=owner_stop.change_pause(self.tree,False,via='private health fixture')
        self.assertFalse(self.marker.exists());self.assertNotEqual(pause['id'],resume['id'])
        self.assertEqual(self.reader_path.read_bytes(),before,'No new heartbeat is needed to preserve the real control transition')
        current=switch.health(self.new)
        self.assertNotEqual(current,first)
        self.assertFalse(window.observe(current,300.0),'Old healthy time must not survive an unseen fast Pause/Resume')
        self.assertEqual(window.since,300.0)

    def test_fast_pause_resume_during_readiness_is_detected_on_both_sides(self):
        def change(*_args,**_kwargs):
            owner_stop.change_pause(self.tree,True)
            owner_stop.change_pause(self.tree,False)
            return copy.deepcopy(self.proof)
        self.rpc.side_effect=change
        with self.assertRaises(RuntimeError):switch.health(self.new)

    def test_epoch_precedes_pause_marker_and_survives_a_marker_write_failure(self):
        from localharness import package_notice
        actual=package_notice.write
        def denied(path,value):
            if Path(path)==self.marker:
                self.assertIs(owner_stop.pause_generation(self.tree)['paused'],True)
                raise PermissionError('private marker mutation denied after durable pause')
            return actual(path,value)
        with patch.object(package_notice,'write',side_effect=denied):
            with self.assertRaises(PermissionError):owner_stop.change_pause(self.tree,True)
        self.assertFalse(self.marker.exists())
        self.assertIs(owner_stop.pause_generation(self.tree)['paused'],True)
        with self.assertRaises(RuntimeError):switch.health(self.new)
        with patch.object(owner_stop,'_tree',self.tree),patch.object(owner_stop,'stopped',return_value=False):
            self.assertTrue(self.runtime_paused(),'Runtime admission also respects durable pause if marker mutation failed')

    def test_resume_epoch_is_durable_before_marker_removal(self):
        owner_stop.change_pause(self.tree,True)
        actual=Path.unlink
        def checked(path,*args,**kwargs):
            if path==self.marker:self.assertIs(owner_stop.pause_generation(self.tree)['paused'],False)
            return actual(path,*args,**kwargs)
        with patch.object(Path,'unlink',checked):owner_stop.change_pause(self.tree,False)
        self.assertFalse(self.marker.exists());self.assertTrue(switch.health(self.new))
        with patch.object(owner_stop,'_tree',self.tree),patch.object(owner_stop,'stopped',return_value=False):
            self.assertFalse(self.runtime_paused())

    def test_malformed_epoch_and_unknown_process_birth_fail_closed(self):
        target=self.tree/'memory/.control/autonomy-generation.json'
        for value in ({},{'format':'helene-autonomy-generation.v1','id':'bad','paused':False},
                      {'format':'helene-autonomy-generation.v1','id':'f'*32,'paused':'false'}):
            with self.subTest(epoch=value):
                write(target,value)
                with self.assertRaises(RuntimeError):switch.health(self.new)
        target.unlink()
        for failure in (self.no_process,self.denied):
            with self.subTest(birth=failure.__name__):
                self.process.side_effect=failure('private synthetic birth observation failed')
                with self.assertRaises(RuntimeError):switch.health(self.new)

    def _controlled_job(self,*,helper=True):
        value={'id':'d'*32,'program':str(self.new),'tree':str(self.tree),'kind':'download',
            'state':'attention','source_mode':'clean','helper':{'pid':41004,'birth':40.0}}
        if not helper:value.pop('helper')
        jobs._save(self.tree,value)
        def private_lock(path):
            self.assertTrue(Path(path).resolve().is_relative_to(self.base))
            return nullcontext()
        self.enterContext(patch.object(jobs,'transaction',side_effect=private_lock))
        return value

    def test_live_session_pause_resume_uses_real_durable_generation_before_checkpoint(self):
        self._controlled_job(helper=False)
        host=object.__new__(session_host.Host)
        host.root=self.new;host.control_tree=self.tree;host.stop=threading.Event()
        host.owner=SimpleNamespace();host.migration_request=None
        before=switch.health(self.new)
        host.dispatch('pause_job',{})
        paused=owner_stop.pause_generation(self.tree)
        self.assertIs(paused['paused'],True);self.assertTrue(self.marker.is_file())
        host.dispatch('resume_job',{})
        resumed=owner_stop.pause_generation(self.tree)
        self.assertIs(resumed['paused'],False);self.assertFalse(self.marker.exists())
        self.assertNotEqual(paused['id'],resumed['id'])
        self.assertNotEqual(switch.health(self.new),before)

    def test_host_absent_direct_controls_preserve_fast_pause_resume_epoch(self):
        self._controlled_job()
        write(session_host.control_path(self.new),{**self.descriptor,'tree':str(self.tree)})
        state=lambda value:'alive' if value.get('pid')==41004 else 'absent'
        with patch.object(session_host,'process_state',side_effect=state),patch.dict(os.environ),redirect_stdout(io.StringIO()):
            self.assertEqual(session_host.main(['--root',str(self.new),'--control','pause']),0)
            paused=owner_stop.pause_generation(self.tree)
            self.assertIs(paused['paused'],True);self.assertTrue(self.marker.is_file())
            self.assertEqual(session_host.main(['--root',str(self.new),'--control','resume']),0)
        resumed=owner_stop.pause_generation(self.tree)
        self.assertIs(resumed['paused'],False);self.assertFalse(self.marker.exists())
        self.assertNotEqual(paused['id'],resumed['id'])

    def test_selected_tree_status_matches_runtime_for_epoch_only_malformed_and_resumed_states(self):
        from deskd import autonomy,control
        target=self.tree/'memory/.control/autonomy-generation.json'
        owner_stop.change_pause(self.tree,True);self.marker.unlink()
        for stage in ('epoch-only','malformed','resumed'):
            with self.subTest(stage=stage):
                if stage=='malformed':target.write_bytes(b'{broken public synthetic control epoch')
                elif stage=='resumed':owner_stop.change_pause(self.tree,False)
                expected=stage!='resumed'
                before={str(p.relative_to(self.tree)):p.read_bytes() for p in self.tree.rglob('*') if p.is_file()}
                self.assertIs(autonomy.state(self.tree)['paused'],expected)
                self.assertIs(control.fold_state(self.tree,'window')['paused'],expected)
                self.assertIs(autonomy.is_paused(self.tree),expected)
                with patch.object(owner_stop,'_tree',self.tree),patch.object(owner_stop,'stopped',return_value=False):
                    self.assertIs(self.runtime_paused(),expected)
                self.assertEqual({str(p.relative_to(self.tree)):p.read_bytes() for p in self.tree.rglob('*') if p.is_file()},before)

    def test_trial_pause_discards_prior_time_and_fresh_resume_needs_all_300_seconds(self):
        paused=[False];resumed=[];captured=[]
        class Clock:
            now=0.0
            def read(self):return self.now
            def sleep(clock,seconds):
                clock.now+=seconds
                if clock.now>1000:raise AssertionError('The private trial did not settle after explicit Resume')
                if paused[0] and not resumed:
                    self.assertIsNone(captured[0].since)
                    if clock.now>=315:
                        owner_stop.change_pause(self.tree,False,via='private explicit trial resume')
                        resumed.append(clock.now)
        clock=Clock();window_class=switch.HealthWindow
        def window():
            value=window_class();captured.append(value);return value
        def observed(*_args,**_kwargs):
            if clock.now>=295 and not paused[0]:
                write(self.marker,{'via':'real-shaped pause during trial'});paused[0]=True
            return copy.deepcopy(self.proof)
        self.rpc.side_effect=observed
        write(self.new/'python-migration.json',{'id':'fixture-trial','state':'new_app_started'})
        args=SimpleNamespace(old=self.base/'Old programme',new=self.new)
        value={'note':{'id':'fixture-trial'}};progress=SimpleNamespace(phase=Mock())
        process=SimpleNamespace(poll=lambda:None)
        with patch.object(switch,'HealthWindow',side_effect=window),\
             patch.object(switch.time,'monotonic',side_effect=clock.read),\
             patch.object(switch.time,'sleep',side_effect=clock.sleep),\
             patch.object(switch.subprocess,'Popen',return_value=process),\
             patch.object(python_source_updater,'_job_report'),\
             patch.object(switch,'scratch_paths'),patch.object(switch,'finish') as finish,\
             patch.object(switch,'cleanup_staging'):
            self.assertEqual(switch._trial_admitted(args,value,progress,lambda:False),0)
        finish.assert_called_once()
        self.assertEqual(len(captured),1)
        window=captured[0]
        self.assertTrue(paused[0]);self.assertTrue(resumed)
        self.assertGreaterEqual(window.since,resumed[0])
        self.assertGreaterEqual(clock.now-window.since,switch.TRIAL_SECONDS)
        self.assertEqual(window.identity,switch.health(self.new))
        self.assertFalse(self.marker.exists())
        self.assertTrue(any(call.args[0]=='paused' for call in progress.phase.call_args_list))


if __name__=='__main__':unittest.main()
