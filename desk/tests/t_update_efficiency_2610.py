"""Update I/O, freshness and observer consumers on disposable files only."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request
import zipfile

DESK=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(DESK))
from localharness import parallel_files, update_io, update_overlay, update_profile, update_progress, update_window
from localharness import python_source_updater as updater, python_updates as updates


def put(root,name,data=b'fixture'):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(data);return path


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.new=self.root/'incoming';self.old=self.root/'application';self.recovery=self.root/'recovery'
        self.new.mkdir();self.old.mkdir()

    def test_extraction_receipt_reuses_hash_only_while_change_identity_matches(self):
        archive=self.root/'incoming.zip'
        with zipfile.ZipFile(archive,'w') as output:output.writestr('Helene/file',b'GOOD')
        receipts=parallel_files.extract(archive,self.new,workers=1)
        source=self.new/'Helene/file';receipt=receipts[str(source)]
        self.assertEqual(receipt['sha256'],hashlib.sha256(b'GOOD').hexdigest())
        if receipt['stat']['change_time'] is not None:
            with patch.object(update_io,'measured_digest',side_effect=AssertionError('redundant incoming hash')):
                self.assertEqual(update_io.proof(source,receipt),receipt)
        else:
            # POSIX timestamp granularity is not a content-change counter.
            with patch.object(update_io,'measured_digest',wraps=update_io.measured_digest) as digest:
                self.assertEqual(update_io.proof(source,receipt),receipt)
                digest.assert_called_once()
        old=source.stat();source.write_bytes(b'EVIL');os.utime(source,ns=(old.st_atime_ns,old.st_mtime_ns))
        fresh=update_io.proof(source,receipt)
        self.assertNotEqual(fresh['sha256'],receipt['sha256'])
        self.assertEqual(fresh['sha256'],hashlib.sha256(b'EVIL').hexdigest())

    def test_raw_stale_sha_map_cannot_skip_different_same_size_incoming(self):
        source=put(self.new,'file',b'EVIL');put(self.old,'file',b'GOOD')
        updater.apply_files(self.new,self.old,self.recovery,verified={str(source):hashlib.sha256(b'GOOD').hexdigest()})
        self.assertEqual((self.old/'file').read_bytes(),b'EVIL')
        updater.rollback(self.old,self.recovery)
        self.assertEqual((self.old/'file').read_bytes(),b'GOOD')

    def test_overlapping_archives_replace_receipt_and_later_config_write_invalidates(self):
        proof={}
        for version in (b'OLD!',b'NEW!'):
            archive=self.root/(version.decode().replace('!','')+'.zip')
            with zipfile.ZipFile(archive,'w') as output:output.writestr('Helene/config.json',version)
            proof.update(parallel_files.extract(archive,self.new,workers=1))
        path=self.new/'Helene/config.json'
        self.assertEqual(update_io.proof(path,proof[str(path)])['sha256'],hashlib.sha256(b'NEW!').hexdigest())
        path.write_bytes(b'EDIT')
        self.assertEqual(update_io.proof(path,proof[str(path)])['sha256'],hashlib.sha256(b'EDIT').hexdigest())

    def test_fresh_receipt_elides_earlier_read_but_actual_copy_is_hashed(self):
        source=put(self.new,'file',b'GOOD');put(self.old,'file',b'OLD!')
        receipts={str(source):update_io.measured_digest(source)}
        progress=update_progress.Progress(self.root/'.helene-update-test.progress.json','test',self.old)
        update_overlay.prepare(self.new,self.old,self.recovery,receipts,progress)
        expected_read = 0 if receipts[str(source)]['stat']['change_time'] is not None else 4
        self.assertEqual(progress.value['read_bytes'].get('incoming',0),expected_read)
        update_overlay.apply(self.new,self.old,self.recovery,progress=progress)
        self.assertEqual(progress.value['read_bytes']['publish_copy'],4)
        # Without a measured change counter, POSIX rehashes the saved preimage
        # at publication validation as well as at its initial copy.
        self.assertEqual(progress.value['read_bytes']['preimage_verify'],4 if receipts[str(source)]['stat']['change_time'] is not None else 8)
        self.assertEqual((self.old/'file').read_bytes(),b'GOOD')

    def test_same_size_incoming_change_after_preparation_refuses_without_effect(self):
        source=put(self.new,'file',b'NEW!');put(self.old,'file',b'OLD!')
        update_overlay.prepare(self.new,self.old,self.recovery)
        before=source.stat();source.write_bytes(b'EVIL');os.utime(source,ns=(before.st_atime_ns,before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'incoming file changed'):update_overlay.apply(self.new,self.old,self.recovery)
        self.assertEqual((self.old/'file').read_bytes(),b'OLD!')

    def test_late_program_edit_after_preparation_is_not_restored_over(self):
        put(self.new,'file',b'NEW!');dest=put(self.old,'file',b'OLD!')
        update_overlay.prepare(self.new,self.old,self.recovery)
        dest.write_bytes(b'LATE')
        with self.assertRaisesRegex(ValueError,'Working program changed'):update_overlay.apply(self.new,self.old,self.recovery)
        self.assertEqual(dest.read_bytes(),b'LATE')

    def test_actual_stream_changed_after_validation_never_publishes(self):
        put(self.new,'file',b'NEW!');put(self.old,'file',b'OLD!')
        update_overlay.prepare(self.new,self.old,self.recovery)
        real=update_overlay.validate
        def validated(*args,**kwargs):
            value=real(*args,**kwargs)
            (self.new/'file').write_bytes(b'EVIL')
            return value
        with patch.object(update_overlay,'validate',side_effect=validated),self.assertRaisesRegex(ValueError,'Copied update stream changed'):
            update_overlay.apply(self.new,self.old,self.recovery)
        self.assertEqual((self.old/'file').read_bytes(),b'OLD!')

    def test_saved_preimage_corruption_refuses_apply_and_interrupted_recovery(self):
        put(self.new,'file',b'NEW!');put(self.old,'file',b'OLD!')
        update_overlay.prepare(self.new,self.old,self.recovery)
        (self.recovery/'file').write_bytes(b'BAD!')
        with self.assertRaisesRegex(ValueError,'preimage changed'):update_overlay.apply(self.new,self.old,self.recovery)
        with self.assertRaisesRegex(ValueError,'preimage changed'):updater.rollback(self.old,self.recovery)
        self.assertEqual((self.old/'file').read_bytes(),b'OLD!')

    def test_failed_complete_journal_never_replaces_first_program_file(self):
        put(self.new,'file',b'NEW!');put(self.old,'file',b'OLD!')
        real=Path.write_text
        def write(path,*a,**kw):
            if path.name=='update-files.tmp':raise OSError('journal controlled failure')
            return real(path,*a,**kw)
        with patch.object(Path,'write_text',write),self.assertRaisesRegex(OSError,'journal controlled'):
            updater.apply_files(self.new,self.old,self.recovery)
        self.assertEqual((self.old/'file').read_bytes(),b'OLD!')

    @unittest.skipIf(os.name=='nt','POSIX mode identity')
    def test_mode_only_overlay_and_rollback_keep_exact_executable_bits(self):
        put(self.new,'file',b'SAME').chmod(0o755);put(self.old,'file',b'SAME').chmod(0o640)
        updater.apply_files(self.new,self.old,self.recovery)
        self.assertEqual((self.old/'file').stat().st_mode&0o777,0o755)
        updater.rollback(self.old,self.recovery)
        self.assertEqual((self.old/'file').stat().st_mode&0o777,0o640)

    def test_rollback_removes_only_new_readonly_git_object_not_source(self):
        source=put(self.new,'code/.git/objects/ab/object',b'immutable')
        source.chmod(0o444)
        updater.apply_files(self.new,self.old,self.recovery)
        self.assertTrue((self.old/'code/.git/objects/ab/object').exists())
        updater.rollback(self.old,self.recovery)
        self.assertFalse((self.old/'code/.git/objects/ab/object').exists())
        self.assertEqual(source.read_bytes(),b'immutable')


class Profile(unittest.TestCase):
    def fixture(self,root):
        old=root/'application';new=root/'incoming';code=root/'authored';data=root/'profile'
        put(old,'tree/module.py',b'x=1\n');put(new,'tree/module.py',b'x=2\n');put(code,'module.py',b'x=1\n')
        put(data,'memory/runs/large',b'r'*131072);put(data,'memory/telegram-media/large',b'm'*131072);put(data,'models/large',b'w'*131072)
        put(old,'helene.json',json.dumps({'tree':str(data),'code':str(code),'setup_complete':True}).encode())
        put(old,'python-app.json',b'{"version":"2.1.1","delivery":"portable"}')
        put(new,'python-app.json',b'{"version":"2.2.0","delivery":"portable"}')
        return old,new,code,data

    def test_backup_is_configs_only_and_manual_essential_inventory_unchanged(self):
        from localharness import essential_backup
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old,new,code,data=self.fixture(root)
            note=update_profile.prepare(old,new)
            with zipfile.ZipFile(note['backup']) as reader:self.assertEqual(reader.namelist(),['helene.json'])
            self.assertEqual(note['data_paths'],[str(data)])
            self.assertEqual(note['copied_data'],[])
            full={name for _,name in essential_backup.inventory(old)}
            self.assertIn('data/memory/runs/large',full)
            self.assertIn('data/memory/telegram-media/large',full)
            self.assertFalse(any('models' in name for name in full))

    def test_configuration_or_selected_source_change_refuses_before_any_switch(self):
        for kind in ('config','code','git'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);old,new,code,data=self.fixture(root)
                if kind=='git':put(code,'.git/HEAD',b'ref: refs/heads/test\n')
                # Fake .git isn't a repository; source preservation is not under
                # test for this one metadata-only selection case.
                if kind=='git':
                    before=update_profile.selection(old);put(code,'.git/HEAD',b'ref: refs/heads/late\n')
                    self.assertNotEqual(before,update_profile.selection(old));continue
                updater.prepare_install(old,new)
                before=(old/'python-app.json').read_bytes()
                if kind=='config':
                    config=json.loads((old/'helene.json').read_text());config['setup_complete']=False
                    (old/'helene.json').write_text(json.dumps(config))
                else:(code/'module.py').write_bytes(b'x=3\n')
                with self.assertRaisesRegex(ValueError,'Settings or selected source changed'):updater.install(old,new)
                self.assertEqual((old/'python-app.json').read_bytes(),before)
                self.assertFalse(updater.active_update(old).exists())

    def test_preclose_preparation_failure_launches_no_helper_and_keeps_old_owner(self):
        from localharness import update_extensions, python_platform, code_transition
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old,new,code,data=self.fixture(root)
            destination=root/'.helene-update-test'
            destination.mkdir();new.rename(destination/'Helene');new=destination/'Helene'
            put(new,'app/python_app.py');put(new,'runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')
            owner=SimpleNamespace(root=old,event=lambda *a:None,close=lambda: self.fail('old owner closed'))
            progress=update_progress.Progress(root/'.helene-update-test.progress.json','test',old)
            with patch.object(python_platform,'require_runtime'),patch.object(update_extensions,'check'),patch.object(code_transition,'preflight',return_value={'state':'ready'}),patch.object(updater,'prepare_install',side_effect=OSError('preimage failure')),patch.object(updates.subprocess,'Popen') as launch:
                with self.assertRaisesRegex(OSError,'preimage failure'):updates._prepare_handoff(owner,destination,{},progress,False)
                launch.assert_not_called()

    def test_preclose_never_repairs_an_interrupted_overlay_under_a_live_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old,new,code,data=self.fixture(root)
            before=(old/'helene.json').read_bytes()
            updater.active_update(old).write_text('{"interrupted":true}')
            with patch.object(updater,'recover_interrupted',side_effect=AssertionError('must wait for startup')):
                with self.assertRaisesRegex(RuntimeError,'startup recovery'):updater.prepare_install(old,new)
            self.assertEqual((old/'helene.json').read_bytes(),before)
            self.assertTrue(updater.active_update(old).exists())


class Observer(unittest.TestCase):
    def test_root_bound_status_and_durations_survive_new_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/'application';target.mkdir()
            report=update_progress.Progress(root/'.helene-update-one.progress.json','one',target)
            report.phase('preimages');report.add_read('existing',123);report.phase('switching');report.phase('rollback',error='controlled')
            value=update_progress.status(target)
            self.assertEqual(value['id'],'one');self.assertEqual(value['read_bytes'],{'existing':123})
            self.assertEqual(value['stage'],'rollback');self.assertGreaterEqual(value['stages'][0]['seconds'],0)
            self.assertIsNone(update_progress.status(root/'other'))
            index=update_progress.pointer(target);ref=json.loads(index.read_text());ref['id']='other';index.write_text(json.dumps(ref))
            with self.assertRaisesRegex(ValueError,'identity differs'):update_progress.status(target)

    def test_browser_observer_reads_real_fixed_receipt_through_rollback_without_owner_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/'application';target.mkdir();path=root/'.helene-update-one.progress.json'
            progress=update_progress.Progress(path,'one',target);progress.phase('prepared')
            ready=threading.Event();url=[]
            def open_browser(address):
                url.append(address)
                page=urllib.request.urlopen(address).read().decode()
                self.assertIn('HÉLÈNE',page);self.assertNotIn('Controller',page)
                return True
            def transaction():
                self.assertTrue(ready.is_set())
                current=update_progress.Progress(path);current.phase('rollback',error='controlled startup failure')
                value=json.load(urllib.request.urlopen(url[0]+'/status'))
                self.assertEqual(value['stage'],'rollback');self.assertEqual(value['id'],'one')
                current.phase('failed',rollback_completed=True)
                return 'restored'
            with patch('webbrowser.open',side_effect=open_browser),patch.object(update_window.time,'sleep'):
                self.assertEqual(update_window.run(transaction,path,ready,browser=True),'restored')
            self.assertTrue(update_progress.status(target)['rollback_completed'])

    def test_native_window_bridge_has_only_one_receipt_and_painted_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/'application';target.mkdir();path=root/'.helene-update-one.progress.json'
            progress=update_progress.Progress(path,'one',target);progress.phase('prepared')
            ready=threading.Event();seen={}
            class Window:
                def destroy(self):seen['destroyed']=True
            def create(*args,**kwargs):seen.update(kwargs);return Window()
            def start(**kwargs):
                self.assertFalse(ready.is_set())
                seen['js_api'].ready()
                kwargs['func']()
            webview=SimpleNamespace(create_window=create,start=start)
            def transaction():
                self.assertTrue(ready.wait(.1));self.assertEqual(seen['js_api'].status()['id'],'one')
                update_progress.Progress(path).phase('completed')
                return 7
            with patch.dict(sys.modules,{'webview':webview}),patch.object(update_window.time,'sleep'):
                self.assertEqual(update_window.run(transaction,path,ready),7)
            self.assertTrue(seen['destroyed'])
            self.assertGreaterEqual(seen['width'], seen['min_size'][0])
            self.assertIn('Golos Text', seen['html'])
            self.assertIn('Zen Kurenaido', seen['html'])
            self.assertNotIn('invoke',dir(seen['js_api']))


if __name__=='__main__':unittest.main(verbosity=2)
