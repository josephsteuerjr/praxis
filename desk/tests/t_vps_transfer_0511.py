"""VPS identity, Stop acceptance and crash-recovery admission contracts."""
from pathlib import Path
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK/'localharness'), str(DESK/'server'), str(DESK)]
from server_destination import destination, accepted
import server_transfer as transfer
import import_agent
import carry
import boot
from deskd import readers
import phone_access
sys.path.insert(0,str(DESK/'server/updater'))
import updater
if os.name!='nt':import transfer_recover


class Admission(unittest.TestCase):
    def test_restarting_container_is_a_failure_even_while_owner_stop_is_preserved(self):
        self.assertTrue(updater.doomed({'owner_stopped':True},{'restarting':True,'restart_count':3}))
    def test_only_cold_transfer_carries_menu_lease_and_never_ephemeral_readiness(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);config=root/'helene.json';config.write_text('{"tree":"data","telegram":{"bot_token":"synthetic","owner_id":123}}')
            tree=root/'data';home=tree/'memory/.state/phone-access';home.mkdir(parents=True)
            lease={'identity':hashlib.sha256(b'synthetic:123').hexdigest(),'previous':{'type':'commands'},'installed':{'type':'web_app','web_app':{'url':'https://old.invalid/tg/'}}}
            (home/'telegram-menu.json').write_text(json.dumps(lease));(home/'status.json').write_text('ephemeral-ready')
            (home.parent/'server-transfer.json').write_text('{"phase":"ready"}')
            ordinary=root/'ordinary.zip';moved=root/'moved.zip'
            carry.export(config,ordinary);carry.export(config,moved,preserve_phone_lease=True)
            with zipfile.ZipFile(ordinary) as z:self.assertFalse(any('phone-access' in n for n in z.namelist()))
            with zipfile.ZipFile(moved) as z:
                self.assertEqual(json.loads(z.read('data/memory/.state/phone-access/telegram-menu.json')),lease)
                self.assertFalse(any(n.endswith(('status.json','server-transfer.json')) for n in z.namelist()))
            publisher=phone_access.PhoneAccess(home,lambda:{},8094,owner_token=True)
            self.assertTrue(publisher.transfer_pending())
            (home.parent/'server-transfer.json').write_text('{"phase":"server-stopped"}')
            self.assertFalse(publisher.transfer_pending())
            lease['identity']='someone-else';(home/'telegram-menu.json').write_text(json.dumps(lease))
            with self.assertRaisesRegex(ValueError,'another bot'):carry.export(config,root/'wrong.zip',preserve_phone_lease=True)

    def test_current_distribution_manifest_is_visible_to_the_server_channel(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            row={'version':'2.3.0','files':{'deskapp.py':'a'*64},'digest':'b'*64,'static':'c'*64}
            (path/'desk.json').write_text(json.dumps(row))
            self.assertEqual(readers.desk_build(path)['version'],'2.3.0')
            row['product']='someone-else';(path/'desk.json').write_text(json.dumps(row))
            self.assertEqual(readers.desk_build(path),{})

    def test_local_runner_cannot_be_restarted_through_another_agents_global_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            tree=Path(folder);state=tree/'memory/.state';state.mkdir(parents=True)
            receipt=state/'server-transfer.json';receipt.write_text('{"phase":"ready"}')
            with self.assertRaises(boot.LayoutError):boot.claim_tree(tree)
            receipt.write_text('{corrupt')
            with self.assertRaises(boot.LayoutError):boot.claim_tree(tree)

    def test_unrelated_paths_commands_and_privileged_ports_are_refused(self):
        for directory in ('/', '/opt', '/srv', '/etc/helene', '/opt/../etc', '/opt//helene', '/opt/a;touch x'):
            with self.subTest(directory=directory), self.assertRaises(ValueError):destination({'deployment':{'directory':directory}})
        for value in ({'project':'other;cmd'}, {'container':'$(cmd)'}, {'port':22}):
            with self.assertRaises(ValueError):destination({'deployment':value})
        self.assertEqual(destination({'deployment':{'directory':'/srv/helene-alice','project':'alice','port':19981}})['container'],'alice')

    def test_stop_acceptance_requires_channel_version_and_fresh_supervisor(self):
        state={'agent':{'name':'test'},'desk':{'version':'2.3.0'},'runner':{'alive':False}}
        supervisor={'kind':'serverboot','alive':True,'children':[{'id':'channel','alive':True},{'id':'runner','alive':False}]}
        self.assertTrue(accepted(state,supervisor,{'stopped':True},'2.3.0'))
        self.assertFalse(accepted(state,{**supervisor,'alive':False},{'stopped':True},'2.3.0'))
        self.assertFalse(accepted(state,supervisor,{'stopped':True},'other'))
        supervisor['children'].append({'id':'relay','alive':True})
        self.assertFalse(accepted(state,supervisor,{'stopped':True},'2.3.0'))
        self.assertFalse(accepted(state,supervisor,{'stopped':False},'2.3.0'))

    def test_corrupt_receipt_fails_closed_and_two_helpers_cannot_export(self):
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'helene.json';config.write_text('{}')
            with transfer.custody(config):
                with self.assertRaises(transfer.TransferError):
                    with transfer.custody(config):self.fail('second helper acquired custody')
            transfer.receipt_path(config).write_text('{broken')
            with self.assertRaises(transfer.TransferError):transfer.saved_transfer(config)

    def test_receipt_binds_target_and_host_key(self):
        raw={'host':'server.test','fingerprint':'SHA256:one'}
        row={'id':'a'*24,'destination':list(transfer.parameters(raw)),'fingerprint':'SHA256:one','deployment':destination(raw)}
        self.assertTrue(transfer.same_destination(row,raw))
        self.assertFalse(transfer.same_destination(row,{**raw,'fingerprint':'SHA256:two'}))
        self.assertFalse(transfer.same_destination(row,{**raw,'deployment':{'port':19982}}))


@unittest.skipIf(os.name=='nt','real rename/fsync/import transaction belongs to Linux stand')
class ImportRecovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'installation';self.root.mkdir()
        for name in ('app','tree','data'):(self.root/name).mkdir()
        (self.root/'data/baseline.txt').write_text('old local data')
        (self.root/'helene.json').write_text('{"tree":"data","mode":"local"}')
        self.archive=self.root.parent/'agent.zip'
        with zipfile.ZipFile(self.archive,'w') as z:
            z.writestr('helene-carry.json',json.dumps({'schema':'helene.carry.v1'}))
            z.writestr('helene.json','{"model":{"key":"synthetic-private"},"agent":{"name":"Stand"}}')
            z.writestr('data/memory/story.md','Exact synthetic memory\n')
        self.original=carry.import_

    def test_crash_in_commit_reuses_prepared_snapshot_and_preserves_old_data(self):
        rename=Path.rename;failed=False
        def crash(source,target):
            nonlocal failed
            if source==self.root/'.transfer-import/incoming/data' and not failed:
                failed=True;raise OSError('synthetic power loss between renames')
            return rename(source,target)
        with patch.object(Path,'rename',crash),patch.object(carry,'import_',wraps=self.original) as imported:
            with self.assertRaises(OSError):import_agent.install(self.root,self.archive)
            self.assertEqual(imported.call_count,1)
        with patch.object(carry,'import_',wraps=self.original) as imported:
            import_agent.install(self.root,self.archive);imported.assert_not_called()
            (self.root/'data/memory/story.md').write_text('life after import')
            import_agent.install(self.root,self.archive);imported.assert_not_called()
        self.assertEqual((self.root/'data/memory/story.md').read_text(),'life after import')
        self.assertEqual((self.root/'.transfer-import/previous/data/baseline.txt').read_text(),'old local data')
        self.assertTrue((self.root/'data/.owner-stop/stop.json').is_file())
        self.assertEqual(json.loads((self.root/'helene.json').read_text())['model']['key'],'synthetic-private')

    def test_bad_archive_leaves_original_installation_and_retry_uses_same_hash(self):
        self.archive.write_bytes(b'incomplete upload')
        with self.assertRaises(zipfile.BadZipFile):import_agent.install(self.root,self.archive)
        self.assertEqual((self.root/'data/baseline.txt').read_text(),'old local data')
        self.archive.write_bytes(b'changed after failure')
        with self.assertRaisesRegex(ValueError,'another archive'):import_agent.install(self.root,self.archive)


@unittest.skipIf(os.name=='nt','root-side retirement uses the Linux installer flock')
class Retirement(unittest.TestCase):
    def test_inflight_installer_blocks_retirement_before_any_container_action(self):
        import fcntl
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            row={'id':'a'*24,'archive_sha':'b'*64,'phase':'installing','deployment':{'directory':str(root),'project':'stand','container':'stand'}}
            (root/'.window-transfer.json').write_text(json.dumps(row))
            with (root/'.install.lock').open('w') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with patch.object(transfer_recover,'command') as command:
                    with self.assertRaises(BlockingIOError):transfer_recover.recover(row)
                    command.assert_not_called()
            self.assertFalse((root/'.window-transfer-stopped').exists())

    def test_empty_owned_transfer_is_fenced_against_a_late_installer_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            row={'id':'a'*24,'archive_sha':'b'*64,'phase':'installing','deployment':{'directory':str(root),'project':'stand','container':'stand'}}
            (root/'.window-transfer.json').write_text(json.dumps(row))
            with patch.object(transfer_recover,'command',return_value=''):
                reply=transfer_recover.recover(row)
            self.assertTrue(reply['ok']);self.assertTrue(reply['data_kept'])
            self.assertEqual(json.loads((root/'.window-transfer-stopped').read_text())['id'],row['id'])

    def test_a_failure_before_remote_install_can_return_without_creating_a_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'absent'
            row={'phase':'archive','deployment':{'directory':str(root)}}
            with patch.object(transfer_recover,'command') as command:
                self.assertTrue(transfer_recover.recover(row)['never_installed']);command.assert_not_called()
            self.assertFalse(root.exists())


if __name__=='__main__':unittest.main()
