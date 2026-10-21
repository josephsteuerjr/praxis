"""Delivery boundary regressions; synthetic files, never the user's installation."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import python_updates as updates,python_source_updater as updater
from localharness.desktop_supervisor import runner_required,BASE_ID
import python_migrate


class ResumeTests(unittest.TestCase):
    def test_uncreated_recorded_roots_resume_after_interruption(self):
        from localharness import app_migration
        for copied_count in (0,1):
            with self.subTest(copied_count=copied_count),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);old=root/'old';new=root/'new';home=root/'saved'
                for name in ('data','source'):
                    (old/name).mkdir(parents=True);(old/name/'value').write_text(name)
                (old/'helene.json').write_text(json.dumps({'tree':'data','code':'source','runner':''}))
                original=app_migration.preserve_source;calls=[]
                def interrupted(source,destination,progress=None):
                    if len(calls)==copied_count:raise RuntimeError('power loss')
                    calls.append(source);return original(source,destination,progress)
                with patch.object(app_migration,'preserve_source',side_effect=interrupted):
                    with self.assertRaisesRegex(RuntimeError,'power loss'):app_migration.prepare(old,new,home)
                note=json.loads((new/'python-migration.json').read_text())
                resumed=app_migration.prepare(old,new,home,reuse=note)
                self.assertEqual(resumed['preserved_roots'],note['preserved_roots'])
                for source,destination in resumed['preserved_roots'].items():
                    self.assertEqual((Path(destination)/'value').read_text(),Path(source).name)

    def test_receipt_replace_failure_retains_valid_resume_map(self):
        from localharness import app_migration
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);before={'state':'preserving','data_paths':['retained']}
            app_migration.write_note(root,before)
            with patch.object(Path,'replace',side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError,'disk full'):app_migration.write_note(root,{'state':'prepared'})
            self.assertEqual(json.loads((root/'python-migration.json').read_text()),before)

    def test_changed_directory_link_cannot_overwrite_another_tree(self):
        from localharness import app_migration
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source';target=root/'retained';outside=root/'outside'
            (source/'folder').mkdir(parents=True);target.mkdir();outside.mkdir()
            (source/'folder/value').write_text('new');(outside/'value').write_text('unique')
            try:(target/'folder').symlink_to(outside,target_is_directory=True)
            except OSError as exc:self.skipTest(str(exc))
            with self.assertRaisesRegex(ValueError,'directory link'):
                app_migration.refresh_preserved(source,target,root/'delta')
            self.assertEqual((outside/'value').read_text(),'unique')

    def test_partial_data_copy_resumes_named_paths_without_another_tree(self):
        from localharness import app_migration
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';(old/'data').mkdir(parents=True)
            (old/'helene.json').write_text(json.dumps({'tree':'data','code':'data','runner':''}))
            (old/'data/value').write_text('current');new=root/'new';home=root/'saved'
            with patch.object(app_migration,'preserve_source',side_effect=RuntimeError('power loss')):
                with self.assertRaisesRegex(RuntimeError,'power loss'):app_migration.prepare(old,new,home)
            note=json.loads((new/'python-migration.json').read_text());self.assertEqual(note['state'],'preserving')
            selected=Path(note['data_paths'][0]);selected.mkdir(parents=True);(selected/'value').write_text('partial')
            (selected/'.desktop-owner.lock').write_text('active\n')
            resumed=app_migration.prepare(old,new,home,reuse=note)
            self.assertEqual(resumed['data_paths'],note['data_paths'])
            self.assertEqual((selected/'value').read_text(),'current');self.assertFalse((selected/'.desktop-owner.lock').exists())
            delta=home/resumed['id']/'previous-delta-0'
            self.assertEqual((delta/'value').read_text(),'partial');self.assertEqual((delta/'.desktop-owner.lock').read_text(),'active\n')

    def test_relocated_hf_model_manifest_selects_the_preserved_store(self):
        from localharness import voice
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);tree=root/'data';store=tree/'models/whisper';folder='models--owner--model';revision='a'*40
            model=store/folder/'snapshots'/revision;model.mkdir(parents=True);(model/'model.bin').write_bytes(b'weights')
            (store/'installed.json').write_text(json.dumps({'repo':'owner/model','path':str(root/'old/models/whisper'/folder/'snapshots'/revision)}))
            self.assertEqual(Path(voice.manifest(tree)['path']),model.resolve())

    def test_download_reuses_only_published_hash_and_preserves_other_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'Helene-1.9.1.zip';path.write_bytes(b'release')
            sha=hashlib.sha256(b'release').hexdigest();events=[]
            with patch.object(updates,'download_http') as http:
                self.assertTrue(updates._download('https://example.invalid/archive',path,sha,events.append)['reused'])
                with self.assertRaises(FileExistsError):updates._download('https://example.invalid/archive',path,'0'*64)
                http.assert_not_called()
            self.assertEqual(path.read_bytes(),b'release');self.assertTrue(events[-1]['reused'])

    def test_update_download_does_not_force_xtts_or_compiler_on_a_bare_app(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'app';root.mkdir();owner=types.SimpleNamespace(root=root,event=lambda *args:None)
            seen=[]
            def download(url,path,sha,progress):
                seen.append(path.name);return {'path':str(path),'sha256':sha}
            with patch.object(updates,'_download',side_effect=download),patch.object(updates,'asset_sha',return_value='f'*64):
                updates.update_download(owner,'https://example.invalid/v1.9.1/Helene-1.9.1.zip')
                self.assertEqual(seen,['Helene-1.9.1.zip'])
                seen.clear();updates.update_download(owner,'https://example.invalid/v1.9.1/Helene-1.9.1.zip',extensions=['whisper'])
                self.assertEqual(seen,['Helene-1.9.1.zip','Helene-components-whisper.zip'])

    def test_archive_escape_is_rejected_before_any_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);archive=root/'release.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('Helene/valid','first');z.writestr('Helene/../../escaped','no')
            with self.assertRaises(ValueError):updates._extract(archive,root/'stage')
            self.assertFalse((root/'stage/Helene/valid').exists());self.assertFalse((root/'escaped').exists())

    def test_extraction_progress_is_measured_in_uncompressed_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);archive=root/'release.zip';events=[]
            with zipfile.ZipFile(archive,'w') as z:z.writestr('Helene/payload',b'x'*400000)
            updates._extract(archive,root/'stage',events.append)
            self.assertEqual(events[-1]['bytes'],400000);self.assertEqual(events[-1]['total_bytes'],400000)

    def test_update_recovery_does_not_copy_equal_files_and_keeps_a_later_edit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'new';target=root/'working';source.mkdir();target.mkdir()
            for base in (source,target):(base/'same').write_text('unchanged')
            (target/'code.py').write_text('old');(source/'code.py').write_text('new')
            recovery=root/'recovery';updater.apply_files(source,target,recovery)
            rows=json.loads((recovery/'update-files.json').read_text());self.assertEqual([r['path'] for r in rows],['code.py'])
            (target/'code.py').write_text('new self edit');updater.rollback(target,recovery)
            self.assertEqual((target/'code.py').read_text(),'old')
            self.assertEqual((recovery/'later-self-edits/code.py').read_text(),'new self edit')

    def test_fresh_preparation_is_restartable_and_does_not_start_a_brain(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);package=root/'package';(package/'tree').mkdir(parents=True)
            (package/'tree/agent.py').write_text('# factory source')
            new=root/'new';note=python_migrate.fresh(package,new,root/'data',extensions=[],launch=False)
            cfg=json.loads((new/'helene.json').read_text());self.assertFalse(cfg['setup_complete'])
            self.assertFalse(runner_required(cfg,BASE_ID));self.assertEqual(Path(cfg['code']),new/'tree')
            repeated=python_migrate.fresh(package,new,root/'data',extensions=[],launch=False)
            self.assertEqual(note['id'],repeated['id']);self.assertEqual(note['data_paths'],repeated['data_paths'])

    def test_two_cli_instances_cannot_migrate_the_same_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'app'
            code='import sys;sys.path.insert(0,sys.argv[1]);import python_migrate;\ntry:\n with python_migrate.installation_lock(sys.argv[2]):print("unexpected")\nexcept Exception:print("blocked")'
            with python_migrate.installation_lock(target):
                output=subprocess.check_output([sys.executable,'-c',code,str(Path(python_migrate.__file__).parent),str(target)])
            self.assertEqual(output.strip(),b'blocked')
            with python_migrate.installation_lock(target):pass

if __name__=='__main__':unittest.main()
