"""Portable update policy and adversarial trial observations, without wall sleeps."""
from pathlib import Path
import shutil,sys,tempfile,unittest
from unittest.mock import patch
from unittest.mock import MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import portable_switch as switch,source_merge as merge,source_update
from localharness.package_notice import write
from t_source_merge_211 import BASE,LOCAL,NEW,BOTH,put

class Trial(unittest.TestCase):
    def test_only_five_continuous_minutes_of_one_healthy_generation_admit_success(self):
        window=switch.HealthWindow()
        for at in range(switch.TRIAL_SECONDS):self.assertFalse(window.observe('generation-a',float(at)))
        self.assertTrue(window.observe('generation-a',float(switch.TRIAL_SECONDS)))
    def test_restart_failure_observation_gap_and_clock_reversal_reset_trial(self):
        for kind in ('restart','failure','gap','clock'):
            with self.subTest(kind=kind):
                window=switch.HealthWindow()
                for at in range(switch.TRIAL_SECONDS):window.observe('a',float(at))
                if kind=='restart':self.assertFalse(window.observe('b',switch.TRIAL_SECONDS))
                elif kind=='failure':self.assertFalse(window.observe(None,switch.TRIAL_SECONDS))
                elif kind=='gap':self.assertFalse(window.observe('a',switch.TRIAL_SECONDS+16))
                else:self.assertFalse(window.observe('a',0))
    def test_wall_time_and_previous_process_receipts_cannot_resume_trial(self):
        self.assertFalse(switch.HealthWindow().observe('same-tree',10**9))
    def test_resumed_host_still_requires_five_new_healthy_minutes_before_retirement(self):
        from types import SimpleNamespace
        from localharness import migration_lifecycle,python_source_updater
        class Clock:
            now=1000.0;gap=False
            def read(self):return self.now
            def sleep(self,seconds):
                if not self.gap:self.now+=switch.TRIAL_SECONDS*100;self.gap=True
                else:self.now+=seconds
        clock=Clock();progress=MagicMock()
        args=SimpleNamespace(old=Path('/old'),new=Path('/new'),pid=0)
        value={'note':{'id':'test-op'}};confirmed=[]
        def finish(_old,_new,_value,window,**_kwargs):
            self.assertGreaterEqual(clock.now-window.since,switch.TRIAL_SECONDS);confirmed.append(clock.now)
        with patch.object(switch.time,'monotonic',side_effect=clock.read),patch.object(switch.time,'sleep',side_effect=clock.sleep),\
             patch.object(migration_lifecycle,'stop'),patch.object(migration_lifecycle,'quiescent'),\
             patch.object(switch,'relocate_assets'),patch.object(switch.subprocess,'Popen'),\
             patch.object(switch,'read',return_value={'id':'test-op','state':'new_app_started'}),\
             patch.object(switch,'health',return_value='admitted-generation'),patch.object(switch,'finish',side_effect=finish),\
             patch.object(switch,'cleanup_staging'),patch.object(switch,'scratch_paths'),patch.object(python_source_updater,'_job_report'):
            self.assertEqual(switch._trial(args,value,progress,lambda:False),0)
        self.assertEqual(len(confirmed),1)
        self.assertGreaterEqual(confirmed[0],1000+switch.TRIAL_SECONDS*101)

class UpstreamDiff(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.base,self.local,self.incoming=[self.root/n for n in ('base','authored','upstream')]
        for path,text in ((self.base,BASE),(self.local,LOCAL),(self.incoming,NEW)):put(path,'module.py',text)
        self.plan=self.root/'third-tree';merge.create(self.base,self.local,self.incoming,self.plan)
    def test_real_git_result_applies_as_diff_to_upstream_and_does_not_touch_old(self):
        old=merge.inventory(self.local)
        with patch.object(source_update,'validate_ready',return_value={'state':'isolated-filesystem-fixture'}):
            merge.apply_upstream(self.plan,self.incoming)
        self.assertEqual((self.incoming/'module.py').read_text(),BOTH)
        self.assertEqual(merge.inventory(self.local),old)
        self.assertFalse((self.root/'code').exists())
    def test_missing_review_or_changed_upstream_refuses_before_first_replacement(self):
        before=merge.inventory(self.incoming)
        with self.assertRaises(ValueError):merge.apply_upstream(self.plan,self.incoming)
        self.assertEqual(merge.inventory(self.incoming),before)
        put(self.incoming,'unreviewed.py','changed=1\n');before=merge.inventory(self.incoming)
        with patch.object(source_update,'validate_ready',return_value={}):
            with self.assertRaises(ValueError):merge.apply_upstream(self.plan,self.incoming)
        self.assertEqual(merge.inventory(self.incoming),before)
    def test_target_cannot_be_the_live_authored_tree(self):
        before=merge.inventory(self.local)
        with patch.object(source_update,'validate_ready',return_value={}):
            with self.assertRaises(ValueError):merge.apply_upstream(self.plan,self.local)
        self.assertEqual(merge.inventory(self.local),before)

class SwitchIdentity(unittest.TestCase):
    def test_old_new_or_data_overlap_cannot_acquire_retirement_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';new=root/'new';data=root/'data'
            for p in (old,new,data):p.mkdir()
            value={'format':switch.FORMAT,'old':str(old),'new':str(new),
                'note':{'id':'fixture','old_root':str(old),'new_root':str(new),'portable_folder_switch':True,'data_paths':[str(data)]}}
            self.assertIs(switch.identity(old,new,value),value)
            for raw in (old,old/'data',new,new/'data'):
                value['note']['data_paths']=[str(raw)]
                with self.assertRaises(ValueError):switch.identity(old,new,value)

class Payload(unittest.TestCase):
    def test_undeclared_runtime_hook_or_changed_runtime_is_never_admitted(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);put(root/'tree','module.py','ok=1\n');(root/'runtime').mkdir()
            binary=root/'runtime/python.exe';binary.write_bytes(b'fixture runtime')
            (root/'code-upstream.zip').write_bytes(b'upstream fixture')
            write(root/'python-package-files.json',{'files':{'runtime/python.exe':{'sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}}})
            switch.payload(root)
            hook=put(root/'runtime','hostile.pth','import hostile\n')
            with self.assertRaisesRegex(ValueError,'undeclared'):switch.payload(root)
            hook.unlink();binary.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'differs'):switch.payload(root)


class ScratchCustody(unittest.TestCase):
    def setUp(self):
        import os
        from types import SimpleNamespace
        from localharness import python_source_updater
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.old=self.root/'old';self.new=self.root/'Helene-update-fixture/Helene';self.data=self.root/'data'
        for path in (self.old,self.new,self.data):path.mkdir(parents=True)
        self.args=SimpleNamespace(old=self.old,new=self.new,staging=self.new.parent,admission_name='updater-admitted.json')
        self.value={'format':switch.FORMAT,'old':str(self.old),'new':str(self.new),
            'note':{'id':'fixture','old_root':str(self.old),'new_root':str(self.new),'portable_folder_switch':True,'data_paths':[str(self.data)]},
            'scratch':switch.scratch_scope(self.old,self.new)}
        self.helper=put(self.new,'app/localharness/python_source_updater.py','fixture helper')
        put(self.new.parent,'python_source_updater.py',self.helper.read_text())
        write(self.new.parent/self.args.admission_name,{'target':str(self.old),'pid':os.getpid()})
        write(python_source_updater.prepared_install_path(self.new),self.value)
    def test_cleanup_deletes_only_exact_mechanical_entries_and_preserves_other_siblings(self):
        from localharness import windows_first_start as native
        unrelated=put(self.new.parent,'owner-notes.txt','owner material')
        other=put(self.new.parent/'another-folder','keep','owner folder')
        # Even an obsolete poisoned list cannot authorize sibling deletion.
        self.value['temporary_names']=[unrelated.name,other.parent.name]
        calls=[]
        def remove(path):
            self.assertEqual(path.parent,self.new.parent);self.assertTrue(path.is_file())
            calls.append(path);path.unlink()
        backend=MagicMock();backend.remove.side_effect=remove
        with patch.object(native,'Backend',return_value=backend):switch.cleanup_staging(self.args,self.value)
        self.assertEqual(unrelated.read_text(),'owner material');self.assertEqual(other.read_text(),'owner folder')
        self.assertTrue(self.helper.is_file());self.assertTrue(self.new.is_dir());self.assertTrue(calls)
    def test_bad_scope_or_changed_helper_refuses_before_stream_close_or_removal(self):
        from localharness import windows_first_start as native
        self.value['scratch']['folder']=str(self.root)
        with patch.object(switch.os,'close') as close,patch.object(native,'Backend') as backend:
            with self.assertRaises(ValueError):switch.cleanup_staging(self.args,self.value)
            close.assert_not_called();backend.assert_not_called()
        self.value['scratch']=switch.scratch_scope(self.old,self.new)
        (self.new.parent/'python_source_updater.py').write_text('changed helper')
        with patch.object(switch.os,'close') as close,patch.object(native,'Backend') as backend:
            with self.assertRaisesRegex(ValueError,'helper receipt changed'):switch.cleanup_staging(self.args,self.value)
            close.assert_not_called();backend.assert_not_called()
    def test_unowned_log_is_preserved_without_closing_the_callers_streams(self):
        from localharness import windows_first_start as native
        log=put(self.new.parent,'update.log','unrelated or replaced log')
        with patch.object(switch.os,'close') as close,patch.object(native,'Backend') as backend:
            with self.assertRaisesRegex(ValueError,'does not own'):switch.cleanup_staging(self.args,self.value)
            close.assert_not_called();backend.assert_not_called()
        self.assertEqual(log.read_text(),'unrelated or replaced log')
    def test_changed_new_payload_cannot_retire_old_even_after_a_healthy_interval(self):
        from localharness import code_transition,update_profile
        self.value['selection']={'reviewed-old':True};self.value['payload']={'reviewed-new':True}
        with patch.object(update_profile,'selection',return_value=self.value['selection']),patch.object(code_transition,'validate'),\
             patch.object(switch,'payload',return_value={'unreviewed-new':True}),patch.object(switch,'health') as health:
            with self.assertRaisesRegex(ValueError,'payload changed'):switch.finish(self.old,self.new,self.value,MagicMock())
            health.assert_not_called()
        self.assertTrue(self.old.is_dir());self.assertTrue(self.new.is_dir())
    def test_mechanical_subprocess_releases_its_actual_log_and_cleans_only_owned_files(self):
        import json,subprocess
        from localharness import python_source_updater
        kept=put(self.new.parent,'owner-keep.txt','owner material');result=self.root/'cleanup-result.json'
        request=self.root/'cleanup-request.json'
        write(request,{'value':self.value,'result':str(result),'admission':self.args.admission_name})
        script=r'''import json,os,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
from localharness import portable_switch as switch,windows_first_start as native
from localharness.package_notice import write
request=json.loads(Path(sys.argv[2]).read_text(encoding='utf8'));value=request['value']
args=SimpleNamespace(old=Path(value['old']),new=Path(value['new']),staging=Path(value['new']).parent,admission_name=request['admission'])
write(args.staging/args.admission_name,{'target':value['old'],'pid':os.getpid()})
def no_host_hooks(*args,**kwargs):raise AssertionError('Cleanup test has no host hook authority')
native.Backend.hooks=no_host_hooks
if os.name!='nt':
 # This is the Windows updater's mechanical descriptor-close test. Keep
 # real files/descriptors on POSIX, with only its Windows deletion boundary
 # supplied here; Windows still exercises the actual custody backend.
 class MechanicalFiles:
  def __init__(self,program):self.roots=[]
  def remove(self,path):
   assert self.roots==[path] and path.parent==args.staging and path.is_file()
   path.unlink()
 native.Backend=MechanicalFiles
try:switch.cleanup_staging(args,value)
except BaseException:
 import traceback
 Path(request['result']).write_text(json.dumps({'error':traceback.format_exc()}))
 raise
for number in (1,2):
 try:os.fstat(number)
 except OSError as error:
  import errno
  assert error.errno==errno.EBADF,error
 else:raise AssertionError('Mechanical log descriptor remains open: '+str(number))
Path(request['result']).write_text(json.dumps({'finished':True}))
'''
        log=self.new.parent/'update.log'
        with log.open('wb') as stream:
            process=subprocess.Popen([sys.executable,'-B','-c',script,str(Path(__file__).resolve().parents[1]),str(request)],
                stdout=stream,stderr=subprocess.STDOUT)
        self.assertEqual(process.wait(timeout=20),0,result.read_text() if result.exists() else 'No child receipt')
        self.assertEqual(json.loads(result.read_text()),{'finished':True})
        self.assertEqual(kept.read_text(),'owner material');self.assertTrue(self.helper.is_file())
        for path in (log,self.new.parent/'python_source_updater.py',self.new.parent/self.args.admission_name,python_source_updater.prepared_install_path(self.new)):
            self.assertFalse(path.exists(),path)

class AssetCustody(unittest.TestCase):
    def test_cross_volume_file_transfer_verifies_bytes_and_never_overwrites_conflicts(self):
        import errno
        from localharness.first_start import _move
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=put(root,'old/reference.wav','private audio fixture');destination=root/'other-volume/reference.wav'
            with patch.object(Path,'rename',side_effect=OSError(errno.EXDEV,'synthetic cross-volume boundary')):
                _move(source,destination)
            self.assertEqual(destination.read_text(),'private audio fixture');self.assertFalse(source.exists())
            source.write_text('newer different audio')
            with self.assertRaises(ValueError):_move(source,destination)
            self.assertEqual(source.read_text(),'newer different audio');self.assertEqual(destination.read_text(),'private audio fixture')
    def test_preparation_copies_trial_assets_and_leaves_old_references_usable(self):
        import datetime
        from localharness import code_transition,update_composition,first_start,windows_first_start,portable_retirement
        from t_first_start_3010 import Backend
        from localharness.package_notice import read
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';new=root/'new'
            places=first_start.Places(root/'User',(),(),root/'Downloads');data=places.data
            put(old/'tree','module.py',BASE);put(new/'tree','module.py',NEW);put(data,'memory/keep','owner data')
            backup=put(old,'backups/one.zip','backup');voice=put(old,'reference.wav','voice')
            write(old/'helene.json',{'mode':'local','tree':str(data),'code':str(old/'tree'),
                'backup':{'dir':'backups'},'voice':{'tts_reference':'reference.wav'}})
            original=(old/'helene.json').read_bytes()
            write(first_start.receipt(old),{'format':first_start.FORMAT,'state':'completed',
                'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'data':str(data)})
            def selected(_old,_new,note,**_):
                cfg=read(new/'helene.json');cfg['code']=str(new/'tree');write(new/'helene.json',cfg)
            with patch.object(code_transition,'validate'),patch.object(code_transition,'prepare',side_effect=selected),\
                 patch.object(first_start.Places,'current',return_value=places),patch.object(windows_first_start,'Backend',return_value=Backend()),\
                 patch.object(update_composition,'selected_files',return_value={}),patch.object(switch,'payload',return_value={'fixture':True}):
                value=switch.prepare(old,new)
            self.assertEqual((old/'helene.json').read_bytes(),original)
            self.assertTrue(backup.exists());self.assertTrue(voice.exists())
            self.assertFalse(value['relocations'])
            captured=read(portable_retirement.path(new));self.assertTrue(captured['assets'])
            for row in captured['assets']:
                self.assertTrue(Path(row['source']).exists());self.assertTrue(Path(row['destination']).exists())
                self.assertEqual(Path(row['source']).is_dir(),Path(row['destination']).is_dir())
            switch.identity(old,new,value);switch.relocate_assets(value)
            switch.relocate_assets(value,restore=True)
            self.assertEqual(backup.read_text(),'backup');self.assertEqual(voice.read_text(),'voice')
            value['relocations']=[{'source':str(root/'outside'),'destination':str(data/'retained-assets/fixture/voice')}]
            with self.assertRaises(ValueError):switch.identity(old,new,value)

if __name__=='__main__':unittest.main()
