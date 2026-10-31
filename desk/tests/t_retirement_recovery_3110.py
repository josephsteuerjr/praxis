"""Recovery after updater death, using private files and fake OS/Job custody."""
import copy
from contextlib import contextmanager,ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import python_app
from localharness import desktop_supervisor,desktop_update_request as jobs
from localharness import authored_baseline,code_transition,first_start as start,migration_lifecycle,owner_stop
from localharness import portable_retirement as retirement,portable_switch as switch
from localharness import python_controller,python_source_updater as updater,retirement_update as recovery
from localharness import session_exit,session_host,update_process,update_profile,windows_first_start
from localharness.package_notice import read,write
import t_portable_retirement_3110 as fixture


class HelperLost(BaseException):pass


class Clock:
    def __init__(self):self.now=1000.0;self.sleeps=[]
    def read(self):return self.now
    def sleep(self,seconds):self.sleeps.append(seconds);self.now+=seconds


class Recovery(unittest.TestCase):
    def setUp(self):
        self.audit=fixture.PortableRetirement();self.addCleanup(self.audit.doCleanups);self.audit.setUp()
        self.fx=self.audit.fx;self.data=self.audit.data
        # These recovery cases model real working health. The shared data
        # preservation fixture begins paused; explicitly resume only its tree.
        owner_stop.change_pause(self.data,False,via='private working recovery fixture')
        root=self.fx.downloads/'Helene-update-recovery-fixture/Helene'
        root.parent.mkdir();self.fx.root.rename(root);self.fx.root=root;self.program=root
        self.audit._put(root/'app/localharness/python_source_updater.py',b'# incoming helper fixture\n')
        # Complete shipped A is built before admission/capture, never blessed
        # from a working generation after a review or simulated helper loss.
        code_transition.seal(root);authored_baseline.seal(root)
        self.job={'id':'recovery-operation','program':str(self.fx.downloads/'Helene old portable'),'tree':str(self.data),
            'kind':'source-update','state':'handed_off','phase':'probation'}
        write(jobs.path(self.data),self.job)
        self.scenario=self.audit._scenario();self.old=self.scenario.programs[2]
        self.producer={'pid':9101,'birth':123.25}
        self.args=SimpleNamespace(old=self.old,new=root,staging=root.parent,
            admission_name='updater-admitted.json',pid=0,job=jobs.path(self.data),job_id='recovery-operation')
        self.note={'format':'helene-python-migration.v1','id':self.args.job_id,'state':'new_app_started',
            'old_root':str(self.old),'new_root':str(root),'portable_folder_switch':True,
            'data_paths':[str(self.data)],'source_paths':[]}
        write(root/'python-migration.json',self.note)
        self.value={'format':switch.FORMAT,'old':str(self.old),'new':str(root),
            'note':self.note,'selection':update_profile.selection(self.old),'payload':switch.payload(root),
            'relocations':[],'scratch':switch.scratch_scope(self.old,root),
            'retirement':{'id':self.scenario.value['id'],'sha256':retirement.authority(self.scenario.value)}}
        write(updater.prepared_install_path(root),self.value)
        self.helper=self.audit._put(root.parent/'python_source_updater.py',(root/'app/localharness/python_source_updater.py').read_bytes())
        self.log=self.audit._put(root.parent/'update.log',b'old updater log\n')
        self.admission=root.parent/self.args.admission_name
        write(self.admission,{'pid':os.getpid(),'target':str(self.old)})
        self.enterContext(patch.object(start.Places,'current',return_value=self.fx.places))
        self.enterContext(patch.object(windows_first_start,'Backend',return_value=self.fx.backend))
        self.enterContext(patch.object(owner_stop,'stopped',return_value=False))
        self.enterContext(patch.object(session_exit,'requested',return_value=False))
        self.enterContext(patch.object(switch,'_same_file',return_value=True))
        self.enterContext(patch.object(recovery,'_self',return_value=self.producer))
        self.leases=[]
        @contextmanager
        def lease(program,identity,**kwargs):
            self.leases.append(('enter',identity,kwargs.get('timeout_ms')))
            try:yield True
            finally:self.leases.append(('exit',identity,None))
        self.enterContext(patch.object(retirement,'lease',side_effect=lease))
        with self.assertRaises(HelperLost):
            with recovery.owner(self.args,self.value):raise HelperLost('producer died after NEW launched')
        self.journal=read(retirement.path(root));self.clock=Clock();self.health_observations=[]
        self.fx.backend.actions.clear()

    def run_recovery(self,*,producer='absent',health=None,lease=None):
        def healthy(program):
            self.assertEqual(Path(program),self.program)
            self.health_observations.append(self.clock.now)
            return health(self.clock.now) if health else 'new-generation'
        with ExitStack() as stack:
            stack.enter_context(patch.object(session_host,'process_state',side_effect=lambda proof:producer(proof) if callable(producer) else producer))
            stack.enter_context(patch.object(switch,'health',side_effect=healthy))
            stack.enter_context(patch.object(retirement.time,'monotonic',side_effect=self.clock.read))
            stack.enter_context(patch.object(retirement.time,'sleep',side_effect=self.clock.sleep))
            stack.enter_context(patch.object(sys.modules['psutil'],'Process',return_value=SimpleNamespace(create_time=lambda:456.75)))
            if lease:stack.enter_context(patch.object(retirement,'lease',side_effect=lease))
            return retirement.run(self.program,self.journal['id'],retirement.authority(self.journal))

    def commit(self,*,value=None):
        value=self.journal if value is None else value
        return self.audit._commit(value)

    def assert_old_present(self):
        for root in self.scenario.containers:self.assertTrue(root.is_dir(),root)

    def test_known_pause_records_waiting_and_resets_the_fresh_recovery_window(self):
        pause=[False];resumed=[False];removed=[]
        original_sleep=self.clock.sleep;original_remove=self.fx.backend.remove
        def sleep(seconds):
            original_sleep(seconds)
            if 1005<=self.clock.now<1020 and not pause[0]:
                owner_stop.change_pause(self.data,True,via='private trial pause');pause[0]=True
            if pause[0] and self.clock.now<1020:
                self.assert_old_present()
            if pause[0] and self.clock.now>=1020 and not resumed[0]:
                self.assertEqual(read(retirement.path(self.program))['state'],'paused')
                owner_stop.change_pause(self.data,False,via='private explicit trial resume');resumed[0]=True
        def remove(path):removed.append(self.clock.now);return original_remove(path)
        self.clock.sleep=sleep
        with patch.object(self.fx.backend,'remove',side_effect=remove):self.run_recovery()
        self.assertTrue(pause[0] and resumed[0]);self.assertTrue(removed)
        self.assertGreaterEqual(min(removed),1020+switch.TRIAL_SECONDS)

    def assert_scratch_present(self):
        for path in (self.helper,self.log,self.admission,updater.prepared_install_path(self.program)):
            self.assertTrue(path.is_file(),path)

    def test_pause_at_commit_admission_requires_new_window_before_any_effect(self):
        boundary=[0];paused_until=[0];removed=[]
        original_sleep=self.clock.sleep;original_remove=self.fx.backend.remove
        def health(now):
            if now>=1000+switch.TRIAL_SECONDS:
                boundary[0]+=1
                if boundary[0]==2:
                    self.assertEqual(self.fx.backend.actions,[])
                    owner_stop.change_pause(self.data,True,via='private final admission pause')
                    paused_until[0]=now+20
                    raise switch.TrialPaused('Explicit pause at commit admission')
            return 'new-generation'
        def sleep(seconds):
            original_sleep(seconds)
            if paused_until[0] and self.clock.now>=paused_until[0]:
                owner_stop.change_pause(self.data,False,via='private explicit resume')
        def remove(path):removed.append(self.clock.now);return original_remove(path)
        self.clock.sleep=sleep
        with patch.object(self.fx.backend,'remove',side_effect=remove):self.run_recovery(health=health)
        self.assertTrue(paused_until[0]);self.assertTrue(removed)
        self.assertGreaterEqual(min(removed),paused_until[0]+switch.TRIAL_SECONDS)

    def test_transient_typed_pause_restarts_recovery_after_immediate_resume(self):
        paused_at=[];removed=[];original=self.fx.backend.remove
        def health(now):
            if not paused_at and now>=1005:
                paused_at.append(now)
                raise switch.TrialPaused('Observed Pause, then immediate owner Resume')
            return 'new-generation'
        def remove(path):removed.append(self.clock.now);return original(path)
        with patch.object(self.fx.backend,'remove',side_effect=remove):self.run_recovery(health=health)
        self.assertTrue(paused_at);self.assertTrue(removed)
        self.assertGreaterEqual(min(removed),paused_at[0]+switch.TRIAL_SECONDS)

    def test_owner_seals_exact_prepared_job_scratch_and_producer_without_recursive_hash(self):
        context=self.journal['completion']
        self.assertEqual(context['prepared'],recovery.prepared_material(self.value))
        self.assertNotIn('retirement',context['prepared'])
        self.assertEqual(context['producer'],self.producer)
        self.assertEqual(context['job']['id'],self.job['id'])
        self.assertEqual({Path(row['path']) for row in context['files']},{self.helper,self.log,self.admission})
        self.assertNotIn('finalized_at',self.journal)
        retirement.validate(self.program,self.journal,self.fx.places)

    def test_available_mutex_with_live_producer_cannot_write_probation_or_observe_health(self):
        before=retirement.path(self.program).read_bytes()
        with self.assertRaisesRegex(RuntimeError,'exit remains unconfirmed'):self.run_recovery(producer='alive')
        self.assertEqual(self.fx.backend.actions,[]);self.assertEqual(self.health_observations,[])
        self.assertEqual(retirement.path(self.program).read_bytes(),before)
        self.assert_old_present();self.assert_scratch_present()
        self.assertLessEqual(self.clock.now-1000,15.5)

    def test_unknown_producer_is_not_death_even_with_abandoned_available_lease(self):
        with self.assertRaisesRegex(RuntimeError,'exit remains unconfirmed'):self.run_recovery(producer='unknown')
        self.assertEqual(self.fx.backend.actions,[]);self.assertEqual(self.health_observations,[])
        self.assert_old_present();self.assert_scratch_present()

    def test_recovery_earns_fresh_continuous_health_then_completes_exact_batch_and_job(self):
        before=self.audit._baseline();targets=copy.deepcopy(self.journal['targets'])
        before['data'].pop(jobs.path(self.data).relative_to(self.data).as_posix())
        original_remove=self.fx.backend.remove;removed_at=[]
        def remove(path):
            removed_at.append((Path(path),self.clock.now));return original_remove(path)
        with patch.object(self.fx.backend,'remove',side_effect=remove):self.run_recovery()
        self.assertGreaterEqual(max(self.health_observations)-min(self.health_observations),switch.TRIAL_SECONDS)
        self.assertTrue(removed_at)
        self.assertGreaterEqual(min(at for _,at in removed_at)-min(self.health_observations),switch.TRIAL_SECONDS)
        complete=read(retirement.path(self.program));self.assertEqual(complete['targets'],targets)
        self.assertEqual(complete['state'],'completed');self.assertIn('finalized_at',complete)
        self.assertEqual(read(jobs.path(self.data))['state'],'completed')
        self.assertEqual(read(self.program/'python-migration.json')['state'],'completed')
        for root in self.scenario.containers:self.assertFalse(root.exists())
        for path in (self.helper,self.log,self.admission,updater.prepared_install_path(self.program)):self.assertFalse(path.exists())
        self.audit._assert_current(before)

    def test_generation_change_restarts_the_health_interval_before_any_cleanup(self):
        self.run_recovery(health=lambda now:'generation-a' if now<1120 else 'generation-b')
        self.assertGreaterEqual(self.clock.now,1120+switch.TRIAL_SECONDS)
        self.assertIn('finalized_at',read(retirement.path(self.program)))

    def test_changed_existing_prepared_receipt_blocks_before_health_or_actions(self):
        value=read(updater.prepared_install_path(self.program));value['selection']['app']['unreviewed.py']={'sha256':'0'*64,'mode':'100644'}
        write(updater.prepared_install_path(self.program),value)
        with self.assertRaisesRegex(ValueError,'Prepared update was replaced'):self.run_recovery()
        self.assertEqual(self.fx.backend.actions,[]);self.assertFalse(self.health_observations);self.assert_old_present()

    def test_deleted_app_baseline_blocks_retirement_despite_healthy_working_code(self):
        working=switch.payload(self.program)['app'];(self.program/'app-upstream.zip').unlink()
        from localharness.update_composition import app_inventory
        self.assertEqual(app_inventory(self.program/'app'),working)
        with self.assertRaises((OSError,ValueError)):self.run_recovery()
        self.assertEqual(self.fx.backend.actions,[]);self.assertFalse(self.health_observations);self.assert_old_present()

    def test_payload_binds_each_v2_baseline_file_as_exact_bytes(self):
        names={'python-app.json','code-upstream.json','app-upstream.zip','app-upstream.json','source-update-baseline.json'}
        captured=switch.payload(self.program)['baseline']
        self.assertEqual(captured,{name:hashlib.sha256((self.program/name).read_bytes()).hexdigest() for name in names})

    def test_code_manifest_and_admission_record_whitespace_changes_are_not_resealed(self):
        for name in ('code-upstream.json','source-update-baseline.json'):
            with self.subTest(file=name):
                path=self.program/name;original=path.read_bytes();path.write_bytes(original+b' \n')
                with self.assertRaises(ValueError):self.run_recovery()
                path.write_bytes(original)
        self.assertEqual(self.fx.backend.actions,[]);self.assertFalse(self.health_observations);self.assert_old_present()

    def test_tampered_baseline_manifest_cannot_pass_as_healthy_working_generation(self):
        path=self.program/'app-upstream.json';manifest=read(path);manifest['sha256']='0'*64;write(path,manifest)
        with self.assertRaises(ValueError):self.run_recovery()
        self.assertEqual(self.fx.backend.actions,[]);self.assertFalse(self.health_observations);self.assert_old_present()

    def test_internally_valid_changed_metadata_is_not_the_captured_delivery(self):
        metadata=read(self.program/'python-app.json');metadata['delivery_label']='new unrelated bytes';write(self.program/'python-app.json',metadata)
        authored_baseline.validate(self.program)
        with self.assertRaisesRegex(ValueError,'programme/runtime changed'):self.run_recovery()
        self.assertEqual(self.fx.backend.actions,[]);self.assertFalse(self.health_observations);self.assert_old_present()

    def test_marker_downgrade_with_v2_carriers_cannot_bypass_baseline_validation(self):
        metadata=read(self.program/'python-app.json');metadata.pop('source_update_protocol');write(self.program/'python-app.json',metadata)
        with self.assertRaises(ValueError):self.run_recovery()
        self.assertEqual(self.fx.backend.actions,[]);self.assertFalse(self.health_observations);self.assert_old_present()

    def test_removing_all_v2_carriers_does_not_downgrade_an_admitted_payload(self):
        metadata=read(self.program/'python-app.json');metadata.pop('source_update_protocol');metadata.pop('source_update_baseline_sha256')
        write(self.program/'python-app.json',metadata)
        for name in ('app-upstream.zip','app-upstream.json','source-update-baseline.json'):(self.program/name).unlink()
        with self.assertRaisesRegex(ValueError,'programme/runtime changed'):self.run_recovery()
        self.assertEqual(self.fx.backend.actions,[]);self.assertFalse(self.health_observations);self.assert_old_present()

    def test_missing_prepared_file_recovers_only_from_the_sealed_snapshot(self):
        updater.prepared_install_path(self.program).unlink()
        self.run_recovery()
        self.assertIn('finalized_at',read(retirement.path(self.program)))
        for root in self.scenario.containers:self.assertFalse(root.exists())

    def test_replaced_scratch_inode_is_preserved_and_blocks_whole_batch(self):
        saved=self.helper.read_bytes();moved=self.helper.with_suffix('.previous');self.helper.rename(moved)
        self.helper.write_bytes(saved)
        with self.assertRaisesRegex(ValueError,'scratch file was replaced'):self.run_recovery()
        self.assertEqual(self.fx.backend.actions,[]);self.assert_old_present();self.assertTrue(moved.exists())

    def test_log_may_grow_but_another_log_generation_is_not_owned(self):
        with self.log.open('ab') as stream:stream.write(b'producer final log append\n')
        recovery._context(self.program,self.journal)
        self.log.rename(self.log.with_suffix('.previous'));self.log.write_bytes(b'foreign log')
        with self.assertRaisesRegex(ValueError,'scratch file was replaced'):self.run_recovery()
        self.assertEqual(self.fx.backend.actions,[]);self.assertEqual(self.log.read_bytes(),b'foreign log')

    def test_tampered_completion_or_seal_cannot_change_the_cleanup_scope(self):
        changed=copy.deepcopy(self.journal);changed['completion']['files'][0]['path']=str(self.data/'workspace/project/Cargo.lock')
        write(retirement.path(self.program),changed)
        with self.assertRaises(ValueError):self.run_recovery()
        self.assertEqual(self.fx.backend.actions,[]);self.assert_old_present()

    def test_new_migration_note_blocks_before_retirement_or_scratch_deletion(self):
        note=read(self.program/'python-migration.json');note['id']='newer-operation';write(self.program/'python-migration.json',note)
        with self.assertRaisesRegex(ValueError,'Migration receipt changed'):self.run_recovery()
        self.assertEqual(self.fx.backend.actions,[]);self.assert_old_present();self.assert_scratch_present()

    def test_stale_current_job_is_preserved_while_the_exact_retirement_completes(self):
        newer=dict(self.job,id='different-owner-job',program=str(self.program),state='running')
        write(jobs.path(self.data),newer)
        self.run_recovery()
        self.assertEqual(read(jobs.path(self.data)),newer)
        self.assertIn('finalized_at',read(retirement.path(self.program)))

    def test_exact_job_cancel_request_blocks_before_probation_or_backend_effects(self):
        current=read(jobs.path(self.data));current.update(cancel_requested=True,state='cancelled');write(jobs.path(self.data),current)
        before=retirement.path(self.program).read_bytes()
        with self.assertRaises(InterruptedError):self.run_recovery()
        self.assertEqual(self.fx.backend.actions,[]);self.assertFalse(self.health_observations)
        self.assertEqual(retirement.path(self.program).read_bytes(),before);self.assert_old_present()
        self.assertEqual(read(jobs.path(self.data)),current)

    def test_exact_durable_cancel_marker_blocks_even_after_current_job_pointer_changes(self):
        marker=jobs.path(self.data).parent/'desktop-update-jobs'/(self.job['id']+'.cancel.json')
        write(marker,{'cancel_requested':True})
        newer=dict(self.job,id='newer-job',program=str(self.program),state='running');write(jobs.path(self.data),newer)
        with self.assertRaises(InterruptedError):self.run_recovery()
        self.assertEqual(self.fx.backend.actions,[]);self.assertFalse(self.health_observations);self.assert_old_present()
        self.assertEqual(read(jobs.path(self.data)),newer)

    def test_cancellation_after_first_delete_prevents_the_next_root_deletion(self):
        marker=jobs.path(self.data).parent/'desktop-update-jobs'/(self.job['id']+'.cancel.json')
        original=self.fx.backend.remove;removed=[]
        def remove(path):
            result=original(path);removed.append(Path(path));write(marker,{'cancel_requested':True});return result
        with patch.object(self.fx.backend,'remove',side_effect=remove):
            with self.assertRaises(InterruptedError):self.commit()
        self.assertEqual(len(removed),1)
        partial=read(retirement.path(self.program));self.assertEqual(partial['state'],'partial')
        self.assertEqual(partial['removed'],[str(removed[0])])
        for row in partial['targets']:
            if Path(row['root'])!=removed[0]:self.assertTrue(Path(row['root']).is_dir())

    def test_cancel_after_completed_deletion_retains_cancelled_and_reports_committed_effect(self):
        self.commit();completed=read(retirement.path(self.program))
        current=read(jobs.path(self.data));current.update(cancel_requested=True,state='cancelled');write(jobs.path(self.data),current)
        recovery.finalize(self.program,completed,backend=self.fx.backend,observe=lambda _: 'absent')
        result=read(jobs.path(self.data));self.assertEqual(result['state'],'cancelled')
        self.assertEqual(result['result']['effect'],'committed');self.assertTrue(result['cancel_requested'])
        self.assertIn('finalized_at',read(retirement.path(self.program)))
        self.assertFalse(updater.prepared_install_path(self.program).exists())

    def test_owner_exit_before_recovery_prevents_all_writes_and_effects(self):
        before=retirement.path(self.program).read_bytes()
        with patch.object(session_exit,'requested',return_value=True):
            with self.assertRaises(InterruptedError):self.run_recovery()
        self.assertEqual(self.fx.backend.actions,[]);self.assertFalse(self.health_observations)
        self.assertEqual(retirement.path(self.program).read_bytes(),before);self.assert_old_present()

    def test_owner_exit_while_waiting_original_producer_is_responsive(self):
        with patch.object(session_exit,'requested',side_effect=lambda root:self.clock.now>=1001):
            with self.assertRaises(InterruptedError):self.run_recovery(producer='alive')
        self.assertLess(self.clock.now,1002);self.assertEqual(self.fx.backend.actions,[])
        self.assertFalse(self.health_observations);self.assert_old_present()

    def test_held_original_lease_waits_in_bounded_slices_and_owner_exit_wakes_it(self):
        attempted=[]
        @contextmanager
        def held(program,identity,**kwargs):
            attempted.append((identity,kwargs.get('timeout_ms')))
            if identity.startswith('watcher-'):yield True
            else:self.clock.sleep(.25);yield False
        with patch.object(session_exit,'requested',side_effect=lambda root:self.clock.now>=1001):
            with self.assertRaises(InterruptedError):self.run_recovery(lease=held)
        self.assertGreaterEqual(len(attempted),2)
        self.assertTrue(all(timeout==250 for identity,timeout in attempted if not identity.startswith('watcher-')))
        self.assertFalse(self.health_observations);self.assertEqual(self.fx.backend.actions,[])

    def test_partial_retirement_failure_recovers_only_captured_targets_with_new_300_seconds(self):
        first=Path(self.journal['targets'][1]['root']);original=self.fx.backend.remove;failed=[]
        def remove(path):
            if Path(path)==first and not failed:failed.append(path);raise OSError('synthetic partial removal')
            return original(path)
        with patch.object(self.fx.backend,'remove',side_effect=remove):
            with self.assertRaisesRegex(OSError,'partial removal'):self.commit()
        partial=read(retirement.path(self.program));self.assertEqual(partial['state'],'partial')
        late=self.audit._protected_copy(self.fx.downloads,'Helene copied after failure');late_before=self.audit._snapshot(late)
        self.fx.backend.actions.clear();self.clock.now=9000;self.run_recovery()
        complete=read(retirement.path(self.program));self.assertIn('finalized_at',complete)
        self.assertEqual(complete['targets'],self.journal['targets'])
        self.assertGreaterEqual(max(self.health_observations)-min(self.health_observations),switch.TRIAL_SECONDS)
        self.assertEqual(self.audit._snapshot(late),late_before)

    def test_later_target_replacement_during_earlier_cleanup_is_not_deleted(self):
        first,second=[Path(row['root']) for row in self.journal['targets'][:2]]
        original=self.fx.backend.remove;replaced=[]
        def remove(path):
            result=original(path)
            if Path(path)==first and not replaced:
                previous=second.with_name(second.name+' held original generation')
                second.rename(previous);shutil.copytree(previous,second);replaced.append(previous)
            return result
        with patch.object(self.fx.backend,'remove',side_effect=remove):
            with self.assertRaisesRegex(ValueError,'replaced|изменилась'):self.commit()
        self.assertTrue(replaced);self.assertTrue(second.is_dir());self.assertTrue(replaced[0].is_dir())
        self.assertFalse(first.exists())
        self.assertFalse(any(kind=='remove' and Path(path)==second for kind,path in self.fx.backend.actions))

    def test_target_replaced_during_registration_retirement_is_not_removed(self):
        first=Path(self.journal['targets'][0]['root']);original=self.fx.backend.retire;replaced=[]
        def retire(path):
            result=original(path)
            if Path(path)==first and not replaced:
                previous=first.with_name(first.name+' retained original generation')
                first.rename(previous);shutil.copytree(previous,first);replaced.append(previous)
            return result
        with patch.object(self.fx.backend,'retire',side_effect=retire):
            with self.assertRaisesRegex(ValueError,'replaced|изменилась'):self.commit()
        self.assertTrue(replaced);self.assertTrue(first.is_dir());self.assertTrue(replaced[0].is_dir())
        self.assertFalse(any(kind=='remove' and Path(path)==first for kind,path in self.fx.backend.actions))

    def test_existing_watcher_makes_duplicate_worker_exit_without_actions(self):
        @contextmanager
        def duplicate(program,identity,**kwargs):yield False
        self.run_recovery(lease=duplicate)
        self.assertFalse(self.health_observations);self.assertEqual(self.fx.backend.actions,[]);self.assert_old_present()

    def test_completed_targets_resume_partial_scratch_cleanup_without_redeleting_old(self):
        self.commit();self.journal=read(retirement.path(self.program));original=self.fx.backend.remove;failed=[]
        def remove(path):
            if Path(path)==self.log and not failed:failed.append(path);raise OSError('synthetic log removal')
            return original(path)
        with patch.object(self.fx.backend,'remove',side_effect=remove):
            with self.assertRaisesRegex(OSError,'log removal'):
                recovery.finalize(self.program,self.journal,backend=self.fx.backend,observe=lambda _: 'absent')
        self.assertEqual(read(jobs.path(self.data))['state'],'handed_off')
        self.assertNotIn('finalized_at',read(retirement.path(self.program)))
        self.fx.backend.actions.clear();self.run_recovery()
        self.assertFalse(self.health_observations)
        self.assertIn('finalized_at',read(retirement.path(self.program)))
        old_roots={Path(row['root']) for row in self.journal['targets']}
        self.assertFalse(any(kind=='remove' and Path(path) in old_roots for kind,path in self.fx.backend.actions))

    def test_owner_loss_after_commit_does_not_falsely_mark_tail_finalized(self):
        with self.assertRaises(HelperLost):
            with recovery.owner(self.args,self.value):
                self.commit();raise HelperLost('producer died before work/job/scratch tail')
        completed=read(retirement.path(self.program));self.assertEqual(completed['state'],'completed')
        self.assertNotIn('finalized_at',completed);self.assert_scratch_present()
        self.assertEqual(read(jobs.path(self.data))['state'],'handed_off')
        self.run_recovery()
        self.assertFalse(self.health_observations)
        self.assertIn('finalized_at',read(retirement.path(self.program)))
        self.assertEqual(read(jobs.path(self.data))['state'],'completed')

    def test_retire_itself_removed_root_before_helper_loss_resumes_exact_deleting_intent(self):
        first=Path(self.journal['targets'][0]['root']);original=self.fx.backend.retire
        def retire(path):
            original(path)
            if Path(path)==first:
                shutil.rmtree(path);raise HelperLost('native retirement removed captured root before helper loss')
        with patch.object(self.fx.backend,'retire',side_effect=retire):
            with self.assertRaises(HelperLost):self.commit()
        partial=read(retirement.path(self.program))
        self.assertEqual(partial['state'],'partial');self.assertEqual(partial['deleting'],str(first))
        self.assertNotIn(str(first),partial['removed']);self.assertFalse(first.exists())
        self.fx.backend.actions.clear();self.run_recovery()
        self.assertIn('finalized_at',read(retirement.path(self.program)))
        self.assertFalse(any(kind in ('retire','remove') and Path(path)==first for kind,path in self.fx.backend.actions))
        self.assertGreaterEqual(max(self.health_observations)-min(self.health_observations),switch.TRIAL_SECONDS)


class WorkerCustody(unittest.TestCase):
    def setUp(self):
        self.fixture=Recovery();self.addCleanup(self.fixture.doCleanups);self.fixture.setUp()
        self.program=self.fixture.program
        self.owner=object.__new__(python_controller.Controller)
        self.owner.root=self.program;self.owner.lock=threading.RLock();self.owner.stop=threading.Event()
        self.owner.exiting=False;self.owner.helpers=[];self.owner.thread=None;self.owner.error=None;self.owner.publishers={}
        self.child=SimpleNamespace(pid=777,handle=object(),poll=lambda:None)

    def test_retirement_worker_is_owned_by_controller_job_and_duplicate_start_reuses_it(self):
        with patch.object(desktop_supervisor.JobChild,'spawn',return_value=self.child) as spawn:
            started=retirement.start(self.program,owner=self.owner)
            duplicate=retirement.start(self.program,owner=self.owner)
        self.assertTrue(started['started']);self.assertTrue(duplicate['running']);spawn.assert_called_once()
        self.assertEqual(self.owner.helpers,[self.child]);self.assertIs(self.owner.retirement_helper,self.child)
        args,kwargs=spawn.call_args;self.assertIn(str(self.program/'app/localharness/portable_retirement.py'),args[0])
        self.assertEqual(kwargs['cwd'],self.program)

    def test_exit_latch_prevents_any_new_worker_admission(self):
        for mode in ('exiting','stopped'):
            with self.subTest(mode=mode):
                self.owner.exiting=mode=='exiting';self.owner.stop.clear()
                if mode=='stopped':self.owner.stop.set()
                with patch.object(desktop_supervisor.JobChild,'spawn') as spawn:
                    self.assertFalse(retirement.start(self.program,owner=self.owner)['started'])
                spawn.assert_not_called()

    def test_controller_close_stops_retirement_job_even_if_cancel_bookkeeping_fails(self):
        self.child=Mock();self.child.handle=object();self.child.poll.return_value=None
        self.owner.helpers=[self.child];self.owner.retirement_helper=self.child
        def stop(children):
            self.assertTrue(self.owner.stop.is_set());self.assertIn(self.child,children.values());self.child.handle=None
        from localharness import python_updates
        with patch.object(python_updates,'cancel_waiting',side_effect=RuntimeError('bookkeeping fault')),\
             patch.object(desktop_supervisor,'stop_children',side_effect=stop) as stop_children:
            self.owner.close()
        stop_children.assert_called_once();self.assertEqual(self.owner.helpers,[])

    def test_session_client_asks_authenticated_host_to_start_worker(self):
        client=object.__new__(session_host.SessionClient);client._request=Mock(return_value={'started':True})
        self.assertEqual(retirement.start(self.program,client),{'started':True})
        client._request.assert_called_once_with('retirement_start')

    def test_application_owner_exit_closes_the_controller_owned_retirement_job(self):
        from t_application_exit_3110 import ApplicationExit
        self.child=Mock();self.child.handle=object();self.child.poll.return_value=None
        self.owner.helpers=[self.child];self.owner.retirement_helper=self.child
        host=object.__new__(session_host.Host);host.root=self.program;host.owner=self.owner
        host.stop=threading.Event();host.exit_lock=threading.Lock();host.exit_result=None;host.exit_owner=False
        host.descriptor={'pid':1002,'birth':99,'program':str(self.program)};host.publish=Mock()
        host.cancel_owned_jobs=Mock(return_value=(False,{}))
        client=object.__new__(session_host.SessionClient);client.descriptor=host.descriptor;client.close=Mock()
        client._request=lambda action,args=None,**kwargs:host.dispatch(action,args or {})
        ui=ApplicationExit();ui.setUp();self.addCleanup(ui.doCleanups);app=ui.app()
        app.root=self.program;app.host=client
        stopped=[]
        def stop(children):
            self.assertTrue(self.owner.exiting);self.assertTrue(self.owner.stop.is_set())
            self.assertIn(self.child,children.values());self.child.handle=None;stopped.append(True)
        with patch.object(session_exit,'record'),patch.object(session_host,'process_state',return_value='absent'),\
             patch.object(desktop_supervisor,'stop_children',side_effect=stop):
            app.close()
        self.assertEqual(stopped,[True]);self.assertEqual(self.owner.helpers,[])
        self.assertTrue(self.owner.exiting);self.assertTrue(host.stop.is_set());client.close.assert_called_once()
        with patch.object(desktop_supervisor.JobChild,'spawn') as spawn:
            self.assertFalse(retirement.start(self.program,owner=self.owner)['started'])
        spawn.assert_not_called()

    def test_programme_wide_recovery_does_not_depend_on_selected_observer_agent(self):
        from localharness import agents
        host=object.__new__(session_host.Host);host.root=self.program;host.owner=self.owner;host.stop=threading.Event()
        self.owner._default=Mock(return_value='deleted-observer-agent')
        with patch.object(agents,'find',return_value=None) as find,\
             patch.object(desktop_supervisor.JobChild,'spawn',return_value=self.child) as spawn:
            self.assertTrue(host.dispatch('retirement_start',{'agent':'deleted-observer-agent'})['started'])
        find.assert_not_called();spawn.assert_called_once()

    def test_unconfirmed_job_shutdown_keeps_handle_and_reports_failure(self):
        self.child=Mock();self.child.handle=object();self.child.poll.return_value=None
        self.owner.helpers=[self.child];self.owner.retirement_helper=self.child
        with patch.object(desktop_supervisor,'stop_children',side_effect=RuntimeError('Job custody remains unknown')):
            with self.assertRaisesRegex(RuntimeError,'custody remains unknown'):self.owner.close()
        self.assertEqual(self.owner.helpers,[self.child]);self.assertIsNotNone(self.child.handle)
        self.assertTrue(self.owner.stop.is_set())


if __name__=='__main__':unittest.main()
