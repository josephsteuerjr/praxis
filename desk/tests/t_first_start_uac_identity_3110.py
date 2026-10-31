"""Real scoped worker dispatch, private files and fake UAC/native authority."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
from localharness import first_start as policy,windows_first_start as native,migration_windows,windows_hooks
import t_first_start_audit_3110 as fixtures

_BACKEND=native.Backend
_RETIRE=windows_hooks.retire


@unittest.skipUnless(os.name=='nt','Windows filesystem paths and native UAC custody')
class UacIdentity(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.FirstStartAudit('runTest')
        self.addCleanup(self.fixture.doCleanups);self.fixture.setUp()
        self.fx=self.fixture.fx;self.old=self.fx.local/'Helene scoped';self.old.mkdir()
        self.put(self.old/'obsolete.py',b'old source bytes\n')
        self.put(self.old/'helene.exe',b'MZ private mechanical marker')
        self.data=self.fx.places.data;self.put(self.data/'memory/owner.txt',b'canonical owner bytes')
        self.put(self.fx.root/'app/localharness/windows_first_start.py',b'never executed; dispatch is injected')
        self.enterContext(patch.object(native,'Backend',_BACKEND))
        self.backend=_BACKEND(self.fx.root);self.backend.roots=[self.old]
        self.backend.protected=[self.data];self.backend.keep=[self.data]
        self.requests=[];self.worker_errors=[];self.grants=[];self.gap=lambda _request:None
        self.enterContext(patch.object(migration_windows,'admin',return_value=True))
        self.enterContext(patch.object(native.subprocess,'run',side_effect=self.launch))

    def put(self,path,raw):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        return path

    def receipt(self,root):
        policy._write(policy.receipt(root),{'format':policy.FORMAT,'state':'completed',
            'completed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'data':str(self.data)})

    def replacement(self):
        self.old.rename(self.fx.base/'captured-old-generation')
        self.old.mkdir();self.put(self.old/'memory/helene-profile.json',b'new generation private bytes')
        self.receipt(self.old)

    def launch(self,command,**_kwargs):
        if command[0]=='icacls':self.grants.append(command);return SimpleNamespace(returncode=0)
        self.assertEqual(command[-2],'--request')
        request=json.loads(command[-1]);self.requests.append(copy.deepcopy(request));self.gap(request)
        try:native.privileged_action(request)
        except Exception as exc:
            self.worker_errors.append(exc);return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    def assert_rejected(self,action,path=None,**values):
        with self.assertRaises(RuntimeError):self.backend.privileged(action,path,**values)
        self.assertTrue(self.worker_errors)

    def test_real_emitted_request_stable_remove_preserves_current_and_profile(self):
        self.receipt(self.old)
        self.backend.privileged('remove',self.old)
        self.assertFalse(self.old.exists());self.assertFalse(self.worker_errors)
        self.assertEqual((self.data/'memory/owner.txt').read_bytes(),b'canonical owner bytes')
        self.assertTrue((self.fx.root/'app/python_app.py').is_file())
        self.assertEqual(self.requests[0]['custody']['format'],native._CUSTODY)

    def test_uac_wait_directory_replacement_with_new_receipt_is_preserved(self):
        self.receipt(self.old);before=self.old.stat().st_ino
        self.gap=lambda _request:self.replacement()
        self.assert_rejected('remove',self.old)
        self.assertNotEqual(self.old.stat().st_ino,before)
        self.assertTrue(policy.ready(self.old,self.fx.places))
        self.assertEqual((self.old/'memory/helene-profile.json').read_bytes(),b'new generation private bytes')
        self.assertIn('generation',str(self.worker_errors[-1]))

    def test_worker_never_recaptures_replacement_after_entry_validation(self):
        original=_BACKEND.custody;changed=[]
        def custody(backend,*args,**kwargs):
            if backend._elevated and not changed:
                changed.append(True);self.replacement()
            return original(backend,*args,**kwargs)
        with patch.object(_BACKEND,'custody',custody):self.assert_rejected('remove',self.old)
        self.assertTrue(changed);self.assertTrue(policy.ready(self.old,self.fx.places))
        self.assertEqual((self.old/'memory/helene-profile.json').read_bytes(),b'new generation private bytes')

    def test_wrapper_member_receipt_generation_is_bound(self):
        member=self.old/'Helene nested';member.mkdir();self.receipt(member)
        def replace_member(_request):
            member.rename(self.fx.base/'previous-member');member.mkdir()
            self.put(member/'memory/owner.txt',b'new member data');self.receipt(member)
        self.gap=replace_member
        self.assert_rejected('remove',self.old)
        self.assertTrue(policy.ready(member,self.fx.places))
        self.assertEqual((member/'memory/owner.txt').read_bytes(),b'new member data')

    def test_receipt_change_or_disappearance_in_same_directory_is_preserved(self):
        for change in ('replace','remove'):
            with self.subTest(change=change):
                self.receipt(self.old);before=self.old.stat().st_ino
                def gap(_request):
                    if change=='replace':
                        record=policy.load(policy.receipt(self.old));record['new_receipt_generation']='changed'
                        policy._write(policy.receipt(self.old),record)
                    else:policy.receipt(self.old).unlink()
                self.gap=gap;self.assert_rejected('remove',self.old)
                self.assertEqual(self.old.stat().st_ino,before)
                self.assertTrue((self.old/'obsolete.py').is_file())

    def test_source_file_hash_change_in_same_inode_is_preserved(self):
        target=self.old/'obsolete.py';before=target.stat().st_ino
        self.gap=lambda _request:target.write_bytes(b'new authored source bytes\n')
        self.assert_rejected('remove',target)
        self.assertEqual(target.stat().st_ino,before)
        self.assertEqual(target.read_bytes(),b'new authored source bytes\n')

    def test_known_absence_cannot_adopt_file_created_during_uac(self):
        target=self.old/'previously-absent.txt'
        self.gap=lambda _request:self.put(target,b'new owner file')
        self.assert_rejected('remove',target)
        self.assertEqual(target.read_bytes(),b'new owner file')

    def test_missing_or_unknown_custody_and_scope_tampering_refuse(self):
        captured=self.backend.custody(self.old)
        request={'action':'remove',**captured['scope'],'custody':captured}
        for kind in ('missing','missing-path','unknown-kind','different-source'):
            value=copy.deepcopy(request)
            if kind=='missing':value.pop('custody')
            elif kind=='missing-path':value['custody']['generations'].pop(str(self.old))
            elif kind=='unknown-kind':value['custody']['generations'][str(self.old)]={'kind':'unconfirmed'}
            else:value['source']=str(self.old/'obsolete.py')
            with self.subTest(kind=kind),self.assertRaises(ValueError):native.privileged_action(value)
        self.assertTrue(self.old.is_dir());self.assertFalse(self.grants)

    def test_remove_and_prune_capture_before_original_attempt_not_after_failure(self):
        original=shutil.rmtree
        def denied(path,*args,**kwargs):
            if Path(path)==self.old:
                self.replacement();raise PermissionError('private original delete failure')
            return original(path,*args,**kwargs)
        with patch.object(native.shutil,'rmtree',side_effect=denied):
            with self.assertRaisesRegex(ValueError,'generation'):self.backend.remove(self.old)
        self.assertFalse(self.requests);self.assertTrue(policy.ready(self.old,self.fx.places))

    def test_prune_failure_does_not_recapture_new_generation(self):
        def denied(*_args,**_kwargs):self.replacement();raise PermissionError('private prune failure')
        with patch.object(policy,'_prune_program',side_effect=denied):
            with self.assertRaisesRegex(ValueError,'generation'):self.backend.prune(self.old,[])
        self.assertFalse(self.requests);self.assertTrue(policy.ready(self.old,self.fx.places))

    def test_local_prune_keeps_original_authority_between_children(self):
        original=_BACKEND.custody;children=[]
        def custody(backend,path=None,**kwargs):
            if backend is self.backend and path is not None and Path(path)!=self.old:
                children.append(Path(path))
                if len(children)==2:
                    self.replacement();self.put(self.old/'helene.exe',b'new programme must remain')
            return original(backend,path,**kwargs)
        with patch.object(_BACKEND,'custody',custody),self.assertRaisesRegex(ValueError,'generation'):
            self.backend.prune(self.old,[])
        self.assertEqual(len(children),2);self.assertTrue(policy.ready(self.old,self.fx.places))
        self.assertEqual((self.old/'helene.exe').read_bytes(),b'new programme must remain')

    def test_release_only_metadata_cannot_authorize_file_mutation_or_untyped_kill(self):
        target=self.old/'obsolete.py';captured=self.backend.custody(target,content=False)
        generation=captured['generations'][str(target)]
        self.assertGreater(generation['inode'],0);self.assertNotIn('sha256',generation)
        request={**captured['scope'],'custody':captured}
        for action in ('remove','prune','data-access','hooks'):
            with self.subTest(action=action),self.assertRaisesRegex(ValueError,'Metadata-only'):
                native.privileged_action({**request,'action':action})
        self.assertTrue(target.is_file());self.assertFalse(self.grants)
        with patch.object(native,'owned',return_value=[]),patch.object(native,'restart_holders',
                return_value=[{'pid':9123,'birth':42.0,'kind':0}]),patch.object(native,'terminate') as kill:
            native.privileged_action({**request,'action':'terminate','pid':9123,'birth':42.0})
            kill.assert_called_once_with(9123,42.0);kill.reset_mock()
            with self.assertRaises(ValueError):
                native.privileged_action({**request,'action':'terminate','pid':9123,'birth':41.0})
            kill.assert_not_called()

    def test_stable_prune_and_exact_data_grant_keep_protected_bytes(self):
        old_data=self.old/'data';self.put(old_data/'keep',b'private old data')
        self.backend.keep=[self.data,old_data];self.backend.protected=[self.data,old_data]
        self.backend.privileged('prune',self.old,keep=[str(old_data)])
        self.assertFalse((self.old/'obsolete.py').exists())
        self.assertEqual((old_data/'keep').read_bytes(),b'private old data')
        self.backend.privileged('data-access',old_data,sid='S-1-5-21-123-456-789-1001')
        self.assertEqual(len(self.grants),1);self.assertEqual(self.grants[0][1],str(old_data))
        def replace_data(_request):
            old_data.rename(self.old/'old-data-generation');old_data.mkdir()
            self.put(old_data/'new-owner',b'new data generation')
        self.gap=replace_data
        self.assert_rejected('data-access',old_data,sid='S-1-5-21-123-456-789-1001')
        self.assertEqual(len(self.grants),1)

    def test_preparing_receipt_can_be_removed_before_later_prune_children(self):
        policy._write(policy.receipt(self.old),{'format':policy.FORMAT,'state':'preparing','data':str(self.data)})
        later=self.put(self.old/'later.py',b'later obsolete code')
        old_data=self.old/'data';self.put(old_data/'keep',b'private data')
        self.backend.keep=[self.data,old_data];self.backend.protected=[self.data,old_data]
        original=Path.iterdir
        def listing(path):
            if path==self.old:
                return iter([p for p in (self.old/'.helene',self.old/'obsolete.py',self.old/'helene.exe',later,old_data) if p.exists()])
            return original(path)
        with patch.object(Path,'iterdir',listing):self.backend.privileged('prune',self.old,keep=[str(old_data)])
        self.assertFalse(policy.receipt(self.old).exists());self.assertFalse(later.exists())
        self.assertEqual((old_data/'keep').read_bytes(),b'private data')

    def test_incoming_and_profile_and_unprotected_grant_remain_forbidden(self):
        for target in (self.fx.root,self.data):
            with self.subTest(target=target):self.assert_rejected('remove',target)
        self.assert_rejected('data-access',self.old,sid='S-1-5-21-123-456-789-1001')
        self.assertFalse(self.grants);self.assertTrue(self.fx.root.exists());self.assertTrue(self.data.exists())

    def test_typed_pid_birth_guard_and_changed_root_remain_closed(self):
        def processes(roots,protected=()):
            return [{'pid':9123,'create_time':42.0}] if self.old in roots else []
        with patch.object(native,'owned',side_effect=processes),patch.object(native,'terminate') as terminate:
            self.assert_rejected('terminate',pid=9123,birth=41.0)
            terminate.assert_not_called()
            self.backend.privileged('terminate',pid=9123,birth=42.0)
            terminate.assert_called_once_with(9123,42.0);terminate.reset_mock()
            self.gap=lambda _request:self.replacement()
            self.assert_rejected('terminate',pid=9123,birth=42.0)
            terminate.assert_not_called()

    def test_hooks_check_root_generation_before_each_typed_mutation(self):
        removed=[];outer=self
        class Hooks:
            def services(self):
                outer.replacement()
                return [{'name':'Helene scope','command':'"'+str(outer.old/'helene.exe')+'"'}]
            def tasks(self):return []
            def autoruns(self):return []
            def remove_service(self,row):removed.append(row)
        with patch.object(windows_hooks,'Windows',return_value=Hooks()),patch.object(windows_hooks,'retire',_RETIRE):
            self.assert_rejected('hooks')
        self.assertFalse(removed);self.assertTrue(policy.ready(self.old,self.fx.places))


if __name__=='__main__':unittest.main()
