"""Verified selected-data transfer, crash retry, custody and adversarial inputs."""
from pathlib import Path
from types import SimpleNamespace
import json,os,sys,tempfile,threading,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import data_relocation as transfer,windows_installation as installation
from localharness.package_notice import read,write


class DataRelocation(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='helene-data-move-');self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name);self.old=self.base/'старая программа';self.old.mkdir()
        self.source=self.old/'data';(self.source/'memory/.control').mkdir(parents=True)
        (self.source/'models/пустая папка').mkdir(parents=True)
        self.history=b'{"owner":"data transfer fixture"}\n';(self.source/'memory/history.jsonl').write_bytes(self.history)
        (self.source/'memory/.control/autonomy-paused.json').write_bytes(b'{"paused":true}')
        (self.source/'.desktop-owner.lock').write_bytes(b'clean\n')
        self.new=self.base/'новая программа';self.new.mkdir()
        self.destination=self.base/'Данные — Вера';self.receipt=self.new/'data-relocation.json'
        self.rows=[{'relative':'helene.json','tree':str(self.source),'value':{'mode':'local'}}]

    def prepare(self):
        plan=transfer.plan(self.rows,self.source,self.destination,self.new,self.old,self.receipt)
        return transfer.prepare(plan,self.receipt,lambda row:None)

    def test_verified_copy_keeps_original_until_new_application_proves_destination(self):
        result=self.prepare()
        self.assertEqual((self.destination/'memory/history.jsonl').read_bytes(),self.history)
        self.assertEqual((self.source/'memory/history.jsonl').read_bytes(),self.history)
        self.assertFalse((self.destination/'.desktop-owner.lock').exists())
        self.assertTrue((self.destination/'models/пустая папка').is_dir())
        self.assertEqual((self.destination/'memory/.control/autonomy-paused.json').read_bytes(),b'{"paused":true}')
        with self.assertRaises(ValueError):transfer.retire_sources(result,startup_proof={'tree':str(self.source)})
        self.assertTrue(self.source.exists())
        # New owner may legitimately append history after its startup proof.
        with (self.destination/'memory/history.jsonl').open('ab') as out:out.write(b'new owner\n')
        final=transfer.retire_sources(result,startup_proof={'tree':str(self.destination)})
        self.assertEqual(final['state'],'retired');self.assertFalse(self.source.exists())
        self.assertEqual((self.destination/'memory/history.jsonl').read_bytes(),self.history+b'new owner\n')

    def test_a_racing_destination_file_is_not_overwritten(self):
        publish=transfer._publish_copy;seen=[]
        def race(part,target):
            if not seen:
                target.write_bytes(b'other writer');seen.append(target)
            return publish(part,target)
        with patch.object(transfer,'_publish_copy',side_effect=race),self.assertRaises(OSError):self.prepare()
        self.assertEqual(seen[0].read_bytes(),b'other writer')
        self.assertEqual((self.source/'memory/history.jsonl').read_bytes(),self.history)

    def test_final_destination_tamper_is_refused_before_binding(self):
        plan=transfer.plan(self.rows,self.source,self.destination,self.new,self.old,self.receipt)
        def progress(row):
            if row.get('files')==row.get('total_files') and row.get('files',0)>0:
                (self.destination/'memory/history.jsonl').write_bytes(b'changed after copy')
        with self.assertRaisesRegex(RuntimeError,'Verified data changed'):
            transfer.prepare(plan,self.receipt,progress)
        self.assertEqual((self.source/'memory/history.jsonl').read_bytes(),self.history)

    def test_existing_other_profile_and_overlapping_paths_are_never_overwritten(self):
        self.destination.mkdir();(self.destination/'owner.txt').write_bytes(b'foreign data')
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual((self.destination/'owner.txt').read_bytes(),b'foreign data')
        for destination in (self.source,self.source/'nested',self.old,self.new/'data'):
            with self.subTest(destination=destination),self.assertRaises(ValueError):
                transfer.plan(self.rows,self.source,destination,self.new,self.old,self.receipt)

    def test_retry_reuses_only_byte_equal_own_copy_and_refuses_other_destination(self):
        first=self.prepare();second=self.prepare()
        self.assertEqual(first['id'],second['id'])
        with self.assertRaises(ValueError):
            transfer.plan(self.rows,self.source,self.base/'другое место',self.new,self.old,self.receipt)
        (self.destination/'memory/history.jsonl').write_bytes(b'tampered copy')
        with self.assertRaises(RuntimeError):self.prepare()
        self.assertEqual((self.source/'memory/history.jsonl').read_bytes(),self.history)

    def test_cancel_and_disk_full_leave_source_and_configuration_intact(self):
        plan=transfer.plan(self.rows,self.source,self.destination,self.new,self.old,self.receipt)
        stopped=threading.Event();stopped.set()
        with self.assertRaises(InterruptedError):transfer.prepare(plan,self.receipt,lambda row:None,stopped)
        with patch.object(transfer.shutil,'disk_usage',return_value=SimpleNamespace(free=0)):
            with self.assertRaises(OSError):transfer.prepare(plan,self.receipt,lambda row:None)
        self.assertEqual((self.source/'memory/history.jsonl').read_bytes(),self.history)
        self.assertFalse((self.new/'helene.json').exists())

    def test_changed_original_prevents_any_source_retirement(self):
        result=self.prepare();(self.source/'new-owner-file').write_bytes(b'another owner')
        with self.assertRaises(RuntimeError):transfer.retire_sources(result,startup_proof={'tree':str(self.destination)})
        self.assertEqual((self.source/'memory/history.jsonl').read_bytes(),self.history)
        self.assertEqual((self.source/'new-owner-file').read_bytes(),b'another owner')

    def test_symlink_in_source_is_refused_without_following_or_changing_target(self):
        foreign=self.base/'private';foreign.mkdir();(foreign/'secret').write_bytes(b'never transfer')
        try:(self.source/'linked').symlink_to(foreign,target_is_directory=True)
        except OSError as exc:self.skipTest(str(exc))
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual((foreign/'secret').read_bytes(),b'never transfer')
        self.assertFalse((self.destination/'linked/secret').exists())

    def test_multiple_profile_trees_are_mapped_without_copying_nested_profile_twice(self):
        nested=self.source/'nested';nested.mkdir();(nested/'data').write_bytes(b'nested')
        other=self.old/'agents/other/data';other.mkdir(parents=True);(other/'data').write_bytes(b'other')
        self.rows.extend([{'relative':'agents/nested/helene.json','tree':str(nested),'value':{}},
                          {'relative':'agents/other/helene.json','tree':str(other),'value':{}}])
        result=self.prepare()
        self.assertEqual(len(result['roots']),2)
        self.assertEqual(transfer.remap(nested,result),self.destination/'nested')
        self.assertEqual((transfer.remap(other,result)/'data').read_bytes(),b'other')
        transfer.retire_sources(result,startup_proof={'tree':str(self.destination)})
        self.assertFalse(self.source.exists());self.assertFalse(other.exists())

    def test_clean_installation_binds_new_tree_then_retires_source_only_after_startup(self):
        import t_migration_clean_windows_2810 as fixtures
        from localharness import migration_lifecycle as lifecycle,source_update_host
        old,new=fixtures.CleanMigration().fixture(self.base/'integration')
        source=old/'data';destination=self.base/'integration-new-data'
        value=read(old/'helene.json')
        value['voice']={'tts_reference':str(source/'voice/reference.wav')}
        value['backup']={'dir':str(source/'backups')}
        write(old/'helene.json',value)
        with patch.object(source_update_host,'verified_runtime',return_value={}), \
             patch.object(lifecycle,'stop',return_value={}),patch.object(lifecycle,'quiescent'), \
             patch.object(lifecycle,'cleanup_installation',return_value={}), \
             patch.object(lifecycle,'uninstaller_for',return_value=None,create=True):
            note=installation.run(new,new,old=old,launch=False,data_destination=destination)
        cfg=read(new/'helene.json')
        self.assertEqual(Path(cfg['tree']),destination)
        self.assertEqual(Path(cfg['backup']['dir']),destination/'backups')
        self.assertEqual(Path(cfg['voice']['tts_reference']),destination/'voice/reference.wav')
        self.assertTrue((source/'memory/history.jsonl').exists())
        self.assertEqual(note['state'],'prepared')
        note.update(state='new_app_started',startup_proof={'tree':str(destination),'channel_pid':123})
        with patch.object(lifecycle,'install_shortcuts',return_value=[]):
            final=installation.finish(new,note,remove_old=True,progress=lambda row:None)
        self.assertEqual(final['state'],'completed');self.assertEqual(final['data_relocation']['state'],'retired')
        self.assertFalse(source.exists());self.assertTrue((destination/'memory/history.jsonl').is_file())


@unittest.skipUnless(os.name=='nt','Windows PowerShell Unicode contract')
class WindowsUnicode(unittest.TestCase):
    def test_native_powershell_roundtrips_unicode_output_and_error(self):
        from localharness.python_windows import powershell
        value='C:\\Егор — рабочий стол\\Данные 代理'
        self.assertEqual(powershell('Write-Output $env:HELENE_UNICODE_TEST',dict(os.environ,HELENE_UNICODE_TEST=value)),value)
        self.assertEqual(powershell("Write-Output 'Вера — папка данных'"),'Вера — папка данных')
        with self.assertRaises(RuntimeError) as error:powershell("throw 'Нет доступа: Данные Веры'")
        self.assertIn('Данные Веры',str(error.exception));self.assertNotIn('\ufffd',str(error.exception))


if __name__=='__main__':unittest.main()
