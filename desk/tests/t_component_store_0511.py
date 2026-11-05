"""Real files/IO counters, persistent pins and one-time cross-volume import.

Tests use only disposable roots. EXDEV exercises the actual copy path without
pretending a second physical volume was mounted on this Windows machine.
"""
import errno
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import component_store as store, component_downloads as cd, app_migration, xtts_install


def written_bytes():
    if os.name == 'nt':
        import ctypes
        class Counters(ctypes.Structure):
            _fields_=[(name,ctypes.c_ulonglong) for name in ('reads','writes','other','read_bytes','write_bytes','other_bytes')]
        values=Counters();kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        kernel.GetProcessIoCounters.argtypes=[ctypes.c_void_p,ctypes.POINTER(Counters)]
        if not kernel.GetProcessIoCounters(kernel.GetCurrentProcess(),ctypes.byref(values)):raise ctypes.WinError()
        return values.write_bytes
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_oublock * 512


class ComponentStore(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name);self.store=self.home/'persistent'
        mocked=patch.object(store,'root',return_value=self.store);mocked.start();self.addCleanup(mocked.stop)
        self.old=self.home/'old';self.new=self.home/'new';self.old.mkdir();self.new.mkdir()

    def file(self,base,relative,data):
        path=base/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return path

    @unittest.skipUnless(os.name=='posix','native executable bits and launch')
    def test_cross_volume_runtime_keeps_executable_mode_and_data_blob_is_distinct(self):
        import subprocess
        body=b'#!/bin/sh\nprintf runtime-works'
        data=self.file(self.old,'data/same-bytes',body);data.chmod(0o644)
        executable=self.file(self.old,'runtime/bin/helper',body);executable.chmod(0o755)
        with patch.object(store.os,'link',side_effect=OSError(errno.EXDEV,'synthetic volume boundary')):
            data_ref,_=store.import_directory(data.parent)
            run_ref,_=store.import_directory(executable.parents[1])
        selected=store._reference(run_ref)/'bin/helper'
        self.assertEqual(selected.stat().st_mode & 0o111,0o111)
        self.assertEqual(subprocess.check_output([str(selected)]),b'runtime-works')
        self.assertFalse((store._reference(data_ref)/'same-bytes').stat().st_mode & 0o111)

    def test_legacy_import_then_repeated_update_has_zero_payload_writes_and_bounded_os_io(self):
        source=self.file(self.old,'voice/site-packages/weights.bin',b'weight-fixture\0'*600000)
        baseline=self.home/'legacy-copy'
        baseline_start=written_bytes();shutil.copytree(self.old/'voice',baseline)
        baseline_io=written_bytes()-baseline_start
        self.assertFalse(source.samefile(baseline/'site-packages/weights.bin'))
        first=cd.preserve_installed(self.old,self.new)
        self.assertEqual(sum(row['copied_bytes'] for row in first),0)
        selected=store.path(self.new,'voice')
        self.assertTrue(source.samefile(selected/'site-packages/weights.bin'))
        third=self.home/'third';third.mkdir()
        before_objects=set((self.store/'objects').iterdir());before_io=written_bytes()
        with (patch.object(store,'_inventory',side_effect=AssertionError('payload rehashed')),
                patch.object(shutil,'copyfile',side_effect=AssertionError('payload copied'))):
            second=cd.preserve_installed(self.new,third)
        actual_io=written_bytes()-before_io
        self.assertLess(actual_io,65536,'Actual process WriteTransferCount must not include weight bytes')
        self.assertEqual(set((self.store/'objects').iterdir()),before_objects)
        self.assertEqual(store.path(third,'voice'),selected)
        self.assertFalse((third/'voice').exists())
        self.assertEqual(sum(row['copied_bytes'] for row in second),0)
        self.assertLess(sum(p.stat().st_size for p in third.rglob('*') if p.is_file()),8192)
        evidence=os.environ.get('HELENE_STORAGE_EVIDENCE')
        if evidence:
            Path(evidence).write_text(json.dumps({'payload_bytes':source.stat().st_size,
                'legacy_copy_os_write_bytes':baseline_io,'repeated_update_os_write_bytes':actual_io,
                'repeated_update_new_payload_files':0,'repeated_update_program_bytes':sum(p.stat().st_size for p in third.rglob('*') if p.is_file()),
                'same_volume_import_shares_file_id':True,'second_update_reuses_object_directory':True},indent=2))

    def test_cross_volume_initial_copy_once_then_no_repeat(self):
        data=b'cross-volume-fixture'*100000
        source=self.file(self.old,'voice/site-packages/model.bin',data)
        copied=[];original=shutil.copy2
        def copying(src,dst,*args,**kwargs):copied.append(Path(src).stat().st_size);return original(src,dst,*args,**kwargs)
        real_link=os.link
        def crossing(src,dst):
            if Path(src).is_relative_to(self.old):raise OSError(errno.EXDEV,'fixture cross-volume')
            return real_link(src,dst)
        with (patch.object(os,'link',side_effect=crossing),
                patch.object(shutil,'copy2',side_effect=copying)):
            first=cd.preserve_installed(self.old,self.new)
            third=self.home/'third';third.mkdir();second=cd.preserve_installed(self.new,third)
        self.assertEqual(copied,[len(data)])
        self.assertEqual(sum(row['copied_bytes'] for row in first),len(data))
        self.assertEqual(sum(row['copied_bytes'] for row in second),0)
        self.assertFalse(source.samefile(store.path(self.new,'voice')/'site-packages/model.bin'))

    def test_different_runtime_has_new_pin_old_open_handle_and_rollback_survive(self):
        first=self.file(self.old,'voice/site-packages/runtime.dll',b'old runtime')
        cd.preserve_installed(self.old,self.new)
        old_path=store.path(self.new,'voice');old_ref=store.export_references(self.new)
        changed=self.home/'incoming';self.file(changed,'site-packages/runtime.dll',b'new runtime')
        with first.open('rb') as active:
            reference,_=store.import_directory(changed)
            store.pin(self.new,'voice',reference)
            self.assertEqual(active.read(),b'old runtime')
        self.assertNotEqual(old_path,store.path(self.new,'voice'))
        self.assertEqual((old_path/'site-packages/runtime.dll').read_bytes(),b'old runtime')
        self.assertEqual(store.path(self.old,'voice'),old_path)
        store.pin(self.new,'voice',old_ref['pins']['voice'])
        self.assertEqual(store.path(self.new,'voice'),old_path)

    def test_agent_model_migration_shares_weights_and_keeps_sample_and_memory(self):
        self.file(self.old,'models/xtts/model.pth',b'model-fixture'*200000)
        sample=self.file(self.old,'models/xtts/personal.wav',b'owner sample fixture')
        self.file(self.old,'memory/owner.md',b'irreplaceable memory')
        destination=self.home/'agent-next'
        app_migration.preserve_source(self.old,destination)
        self.assertFalse((destination/'models/xtts').exists())
        self.assertEqual((destination/'memory/owner.md').read_bytes(),b'irreplaceable memory')
        selected=store.model_path(destination,'xtts')
        self.assertTrue(sample.samefile(selected/'personal.wav'))
        second=self.home/'agent-third'
        with patch.object(store,'_inventory',side_effect=AssertionError('model imported twice')):
            app_migration.preserve_source(destination,second)
        self.assertEqual(store.model_path(second,'xtts'),selected)
        neighbour=self.home/'other-agent';neighbour.mkdir()
        store.preserve_models(destination,neighbour)
        self.assertEqual(store.model_path(neighbour,'xtts'),selected)

    def test_cache_legacy_import_is_once_and_survives_program_retirement(self):
        data=b'cached component fixture'*100000
        self.file(self.old,'.helene/component-downloads/pinned.zip',data)
        with patch.object(os,'link',side_effect=OSError(errno.EXDEV,'fixture')):
            cd.preserve_cache(self.old,self.new)
        cached=cd.cache_dir(self.new)/'pinned.zip'
        self.assertEqual(cached.read_bytes(),data)
        with patch.object(shutil,'copyfile',side_effect=AssertionError('cache copied twice')):
            cd.preserve_cache(self.old,self.new)
        shutil.rmtree(self.old)
        self.assertEqual(cached.read_bytes(),data)

    def test_corrupt_or_missing_pin_fails_closed(self):
        self.file(self.old,'voice/model',b'fixture');cd.preserve_installed(self.old,self.new)
        selected=store.path(self.new,'voice');(selected.parent/'receipt.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'unavailable'):store.path(self.new,'voice')

    def test_actual_update_overlay_changes_only_pin_and_rollback_restores_selection(self):
        from localharness import update_profile, update_overlay
        self.file(self.old,'helene.json',json.dumps({'tree':'data','code':'data','voice':{'voice':'fish'}}).encode())
        (self.old/'data').mkdir()
        self.file(self.old,'voice/site-packages/weights.bin',b'old weights'*100000)
        self.file(self.new,'voice/site-packages/weights.bin',b'new weights'*100000)
        cd.preserve_installed(self.old,self.home/'first-clone')
        backup=update_profile.settings_snapshot(self.old,'fixture-pins')
        with zipfile.ZipFile(backup['path']) as archived:
            self.assertIn(store.PINS,archived.namelist())
            self.assertFalse(any('weights' in name for name in archived.namelist()))
        prior=store.path(self.old,'voice');before=update_profile.selection(self.old)
        update_profile.prepare(self.old,self.new,snapshot=False)
        self.assertEqual(update_profile.selection(self.old),before)
        selected=store.path(self.new,'voice');self.assertNotEqual(prior,selected)
        names={p.relative_to(self.new).as_posix() for p in update_overlay._program_files(self.new)}
        self.assertIn(store.PINS,names);self.assertFalse(any(n.startswith('voice/') for n in names))
        recovery=self.home/'recovery'
        # Real updater journal/apply/rollback; no process launch or source policy.
        from localharness.python_source_updater import apply_files, rollback
        apply_files(self.new,self.old,recovery)
        self.assertEqual(store.path(self.old,'voice'),selected)
        rollback(self.old,recovery)
        self.assertEqual(store.path(self.old,'voice'),prior)

    def test_copy_program_selected_extension_uses_pin_without_physical_payload_clone(self):
        import python_migrate
        self.file(self.old,'voice/site-packages/weights.bin',b'fixture'*100000)
        python_migrate.copy_program(self.old,self.new,lambda value:None,['whisper'])
        self.assertTrue((store.path(self.new,'voice')/'site-packages/weights.bin').is_file())
        self.assertFalse((self.new/'voice').exists())
        base_only=self.home/'base-only'
        python_migrate.copy_program(self.old,base_only,lambda value:None,[])
        self.assertNotIn('voice',store.export_references(base_only)['pins'])

    def test_two_real_bundle_installs_share_exact_model_and_runtime(self):
        bundle=self.old/'components/xtts'
        for relative in ('runtime/python.exe','runtime/bin/python3','model/model.pth','model/voices.json'):
            self.file(bundle,relative,relative.encode())
        from localharness.app_components import digest
        files={p.relative_to(bundle).as_posix():digest(p) for p in bundle.rglob('*') if p.is_file()}
        self.file(bundle,'xtts-bundle.json',json.dumps({'format':'helene-xtts.v1','files':files}).encode())
        a=self.home/'agent-a';b=self.home/'agent-b'
        xtts_install.install(a,program=self.old);xtts_install.install(b,program=self.old)
        self.assertEqual(store.model_path(a,'xtts'),store.model_path(b,'xtts'))
        self.assertTrue((store.model_path(a,'xtts')/'model.pth').samefile(bundle/'model/model.pth'))
        self.assertFalse((a/'models').exists());self.assertFalse((b/'models').exists())

    def test_different_agent_metadata_reuses_weights_even_with_cross_volume_import(self):
        data=b'identical cross-agent weights'*100000
        a=self.home/'agent-a';b=self.home/'agent-b'
        for owner,choice in ((a,b'choice A'),(b,b'choice B')):
            self.file(owner,'models/whisper/model.bin',data)
            self.file(owner,'models/whisper/installed.json',choice)
        real_link=os.link
        def crossing(src,dst):
            if Path(src).is_relative_to(a) or Path(src).is_relative_to(b):raise OSError(errno.EXDEV,'fixture cross-volume')
            return real_link(src,dst)
        with patch.object(os,'link',side_effect=crossing):
            one=store.preserve_models(a,a);two=store.preserve_models(b,b)
        self.assertEqual(one[0]['copied_bytes'],len(data)+len(b'choice A'))
        self.assertEqual(two[0]['copied_bytes'],len(b'choice B'))
        self.assertTrue((store.model_path(a,'whisper')/'model.bin').samefile(store.model_path(b,'whisper')/'model.bin'))

    def test_changed_bundle_publishes_new_runtime_model_and_retains_personal_sample(self):
        from localharness.app_components import digest
        bundle=self.old/'components/xtts';tree=self.home/'agent'
        def write_bundle(version):
            for relative in ('runtime/python.exe','runtime/bin/python3','model/model.pth','model/voices.json'):
                path=bundle/relative
                if path.exists():path.unlink()
                self.file(bundle,relative,(version+relative).encode())
            files={p.relative_to(bundle).as_posix():digest(p) for p in bundle.rglob('*') if p.is_file() and p.name!='xtts-bundle.json'}
            note=bundle/'xtts-bundle.json'
            if note.exists():note.unlink()
            self.file(bundle,'xtts-bundle.json',json.dumps({'format':'helene-xtts.v1','files':files}).encode())
        write_bundle('old');xtts_install.install(tree,program=self.old)
        prior_model=store.model_path(tree,'xtts');prior_runtime=xtts_install.runtime_for(self.old)
        self.file(prior_model,'personal.wav',b'personal sample')
        write_bundle('new');xtts_install.install(tree,bundle=bundle,program=self.old)
        self.assertNotEqual(prior_model,store.model_path(tree,'xtts'))
        self.assertNotEqual(prior_runtime,xtts_install.runtime_for(self.old))
        self.assertEqual((prior_model/'model.pth').read_bytes(),b'oldmodel/model.pth')
        self.assertEqual((prior_runtime/'python.exe').read_bytes(),b'oldruntime/python.exe')
        self.assertEqual((store.model_path(tree,'xtts')/'personal.wav').read_bytes(),b'personal sample')

    def test_recognition_download_cache_is_permanent_and_outside_memory_backups(self):
        from localharness import voice
        parent=types.ModuleType('faster_whisper');parent.__path__=[]
        child=types.ModuleType('faster_whisper.utils');calls=[];written=[]
        def download(repo,cache_dir,**kwargs):
            calls.append(cache_dir)
            selected=Path(cache_dir)/('models--'+repo.replace('/','--'))/'snapshots/fixture'
            if not (selected/'model.bin').exists():
                self.file(selected,'model.bin',b'whisper fixture');written.append(15)
            return str(selected)
        child.download_model=download
        with (patch.dict(sys.modules,{'faster_whisper':parent,'faster_whisper.utils':child}),
              patch.object(voice,'library',return_value={'present':True})):
            first=voice.fetch(self.old,'small',quiet=True)
            second=voice.fetch(self.old,'small',quiet=True)
        self.assertEqual(calls[0],calls[1]);self.assertEqual(written,[15])
        self.assertTrue(Path(second['path']).is_relative_to(self.store))
        self.assertFalse(list((self.old/'memory').rglob('model.bin')))
        self.assertTrue(Path(first['path']).is_dir())


if __name__=='__main__':unittest.main()
