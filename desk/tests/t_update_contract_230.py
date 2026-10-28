"""Causal update contracts: semantic preserve, composition, exact overlay and Stop."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import source_merge as merge, source_update as pipeline
from localharness import agent_source_update as hand, update_composition as composition
from localharness import update_overlay, desktop_update_request as jobs, python_updates
from localharness.package_notice import read, write


def put(root,name,text):
    path=Path(root)/name;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(text.encode() if isinstance(text,str) else text)
    return path


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='helene-composition-test-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.program=self.root/'incoming/Helene';self.old=self.root/'installed'
        self.tree=self.root/'independent-data';self.tree.mkdir();self.old.mkdir()
        base=self.root/'A';local=self.root/'L';incoming=self.program/'tree'
        put(base,'module.py','base=1\n');put(local,'module.py','base=1\nauthored=2\n')
        put(incoming,'module.py','base=1\n');put(incoming,'new.py','upstream=3\n')
        self.local=local
        self.directory=self.tree/'memory/.source-updates'/('a'*64)
        self.plan=merge.create(base,local,incoming,self.directory)
        put(self.program,'app/localharness/feature.py','answer=1\n')
        put(self.program,'app/resources/example.txt','published\n')
        put(self.program,'app/resources/soul/skills/update.md','Factory guidance, not a private agent soul.\n')
        put(self.program,'app/resources/example.ico',b'\x00\x01published-icon')
        runtime=put(self.program,'runtime/python.exe',b'synthetic-runtime')
        app=composition.app_inventory(self.program/'app')
        write(self.program/'python-app.json',{'version':'2.3.0'})
        write(self.program/'source-inputs.json',{'files':{name:row['sha256'] for name,row in app.items()}})
        package={p.relative_to(self.program).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.program.rglob('*') if p.is_file()}
        write(self.program/'python-package-files.json',{'files':package})
        self.operation={'id':self.directory.name,'directory':str(self.directory),'program':str(self.old),
            'tree':str(self.tree),'source':str(local.resolve()),'input_sha256':merge.input_signature(self.plan),
            'state':'waiting_agent','resolution_owner':'agent','attempts':[], 'created':time.time()-999999,
            'version':'2.3.0',
            'coordinator':{'kind':'incoming-update-runtime.v1','program':str(self.program.resolve()),'test_battery':1}}
        write(self.directory/'pipeline.json',self.operation)
        write(pipeline.path(self.tree),self.operation)
        write(hand.marker(self.directory),{'format':'helene-agent-resolution.v1','input_sha256':self.operation['input_sha256'],
            'source':str(local.resolve()),'owner_update_requested':True})
        composition.prepare(self.directory,self.operation)

    def test_modified_preserve_without_text_conflict_is_strict(self):
        self.assertEqual(self.plan['conflicts'],[])
        self.assertTrue(pipeline.requires_gate(self.plan))
        target=self.root/'independent-candidate';shutil.copytree(self.local,target)
        with self.assertRaisesRegex(ValueError,'ready|review'):
            merge.apply(self.directory,target)
        self.assertEqual(merge.inventory(target),self.plan['files']['local'])
        self.assertIsNone(pipeline.budget_seconds(self.operation))

    def test_app_repair_changes_whole_composition_but_not_frozen_release(self):
        before=composition.signature(self.directory)
        base=(self.program/'app/localharness/feature.py').read_bytes()
        composition.repair(self.directory,'app/localharness/feature.py','answer=42\n',expected=before,reason='Fix actual app/core coupling')
        self.assertNotEqual(composition.signature(self.directory),before)
        self.assertEqual((self.program/'app/localharness/feature.py').read_bytes(),base)
        self.assertEqual((self.directory/'compound/base-app/localharness/feature.py').read_bytes(),base)
        self.assertIn('resources/example.txt',composition.harness(self.directory)['harness']['merge_files'])
        put(self.program,'runtime/python.exe',b'changed')
        with self.assertRaisesRegex(ValueError,'runtime changed'):composition.signature(self.directory)

    def test_compound_overlay_applies_exact_app_and_rolls_back_without_touching_B(self):
        before=composition.signature(self.directory)
        composition.repair(self.directory,'app/localharness/feature.py','answer=42\n',expected=before,reason='Repair consumer')
        before=composition.signature(self.directory)
        composition.repair(self.directory,'app/resources/example.txt',delete=True,expected=before,reason='Remove broken published resource')
        write(self.program/'code-transition-plan.json',{'plans':[{'directory':str(self.directory)}]})
        composition.overlay(self.program,[{'directory':str(self.directory)}])
        put(self.old,'app/localharness/feature.py','answer=0\n');put(self.old,'app/resources/example.txt','old resource\n')
        recovery=self.root/'recovery'
        update_overlay.prepare(self.program,self.old,recovery)
        update_overlay.apply(self.program,self.old,recovery)
        self.assertEqual((self.old/'app/localharness/feature.py').read_text(),'answer=42\n')
        self.assertFalse((self.old/'app/resources/example.txt').exists())
        self.assertEqual((self.program/'app/localharness/feature.py').read_text(),'answer=1\n')
        from localharness.python_source_updater import rollback
        rollback(self.old,recovery)
        self.assertEqual((self.old/'app/localharness/feature.py').read_text(),'answer=0\n')
        self.assertEqual((self.old/'app/resources/example.txt').read_text(),'old resource\n')

    def test_overlay_map_cannot_replace_an_undeclared_file_or_reuse_old_composition(self):
        write(self.program/'code-transition-plan.json',{'plans':[{'directory':str(self.directory)}]})
        composition.overlay(self.program,[{'directory':str(self.directory)}])
        pointer=self.program.parent/'compound-overlay.json';value=read(pointer)
        value['files']['app/localharness/feature.py']={'source':str(self.program/'runtime/python.exe'),'sha256':hashlib.sha256(b'synthetic-runtime').hexdigest()}
        write(pointer,value)
        with self.assertRaisesRegex(ValueError,'declared candidate'):composition.selected_files(self.program)

    def test_finish_does_not_trigger_review_and_verify_requires_exact_composition(self):
        hand.command(self.tree,'finish')
        operation=read(self.directory/'pipeline.json')
        self.assertEqual(operation['state'],'waiting_agent');self.assertNotIn('review_id',operation)
        with self.assertRaisesRegex(ValueError,'expected_composition'):hand.command(self.tree,'verify',expected_composition_sha256='old')
        hand.command(self.tree,'verify',expected_composition_sha256=composition.signature(self.directory))
        self.assertEqual(read(self.directory/'pipeline.json')['state'],'verification_requested')
        with self.assertRaisesRegex(ValueError,'frozen'):hand.command(self.tree,'patch',path='module.py',before='base=1',after='base=2')

    def test_source_only_or_ordinary_clear_cannot_authorize_apply(self):
        operation=read(self.directory/'pipeline.json');operation.update(review_clear=True,state='waiting_agent')
        write(self.directory/'pipeline.json',operation)
        with self.assertRaises((ValueError,KeyError,FileNotFoundError)):
            hand.command(self.tree,'apply',expected_composition_sha256=composition.signature(self.directory))
        ordinary={'subject_kind':'task_result','trigger':'manual','execution':'done','verdict':'clear','subject_sha256':'x'}
        with self.assertRaisesRegex(ValueError,'Mandatory'):pipeline._clear(Mock(),ordinary,'x')
        self.assertNotEqual(read(self.directory/'pipeline.json')['state'],'ready_to_apply')

    def test_cancelled_original_stays_cancelled_and_cannot_finish(self):
        pipeline._cancel_marker(self.directory,'Owner cancelled')
        with self.assertRaisesRegex(ValueError,'cancelled'):hand.command(self.tree,'finish')
        self.assertTrue((self.directory/'cancel.json').is_file())

    def test_unknown_existing_critic_is_not_dropped_for_a_new_candidate(self):
        operation=read(self.directory/'pipeline.json')
        operation.update(state='needs_attention',review_id='review-unknown')
        write(self.directory/'pipeline.json',operation)
        with self.assertRaisesRegex(ValueError,'live or unknown'):
            hand.command(self.tree,'finish')
        self.assertEqual(read(self.directory/'pipeline.json')['review_id'],'review-unknown')

    def test_declared_offline_verification_dependencies_use_private_bound_target(self):
        from localharness import source_update_battery,source_verification_bundle
        import sys
        operation=dict(self.operation)
        digest='d'*64
        fields={'python_dependencies':'fixture-target','dependency_receipt':'fixture-receipt','dependency_receipt_sha256':'e'*64}
        with patch.object(source_update_battery,'_bundle',return_value=(self.program/'verification',digest,{'dependencies':{sys.platform:{'pinned':True}}})), \
             patch.object(source_verification_bundle,'provision',return_value=fields) as acquire:
            pipeline._verification_dependencies(operation,self.directory,lambda:False)
        self.assertEqual(operation['verification_executor'],fields)
        self.assertEqual(acquire.call_args.args[4],self.directory/'verification-dependencies'/digest)

    def test_age_never_cancels_a_current_admitted_semantic_job(self):
        forge=Mock();service=Mock()
        with patch.object(pipeline,'_advance') as advance:
            pipeline.tick(self.tree,forge,service,coordinator=self.operation['coordinator'])
        advance.assert_called_once()
        self.assertFalse((self.directory/'cancel.json').exists())
        self.assertEqual(read(self.directory/'pipeline.json')['state'],'waiting_agent')

    def test_review_materials_capture_factory_resources_and_exact_release_evidence(self):
        from localharness import self_review
        sources=composition.harness(self.directory,self.operation)
        roots=self_review._source_roots(sources,opaque_published=True,composition_sha256=composition.signature(self.directory))
        destination=self.root/'frozen-review'
        manifest,gaps=self_review.collect(roots,destination)
        self.assertEqual(gaps,[])
        self.assertTrue((destination/'harness/resources/soul/skills/update.md').is_file())
        self.assertEqual((destination/'distribution/python-package-files.json').read_bytes(),(self.program/'python-package-files.json').read_bytes())
        icon=next(row for row in manifest if row['path']=='harness/resources/example.ico')
        self.assertIn('opaque',icon);self.assertFalse((destination/icon['path']).exists())

    def test_compound_only_change_has_a_new_review_operation_identity(self):
        from localharness import source_update_battery
        service=Mock();service.request.return_value={'id':'review-fixture','subject_sha256':'f'*64}
        operation=dict(self.operation,candidate_sha256=merge.candidate_signature(self.directory))
        with patch.object(source_update_battery,'validate'),patch.object(source_update_battery,'prepare',return_value={}):
            pipeline._review_request(operation,self.directory,service)
            first=service.request.call_args.kwargs['operation_id']
            composition.repair(self.directory,'app/localharness/feature.py','answer=43\n',
                expected=composition.signature(self.directory),reason='Fix app-only finding')
            pipeline._review_request(operation,self.directory,service)
        self.assertNotEqual(first,service.request.call_args.kwargs['operation_id'])
        self.assertEqual(operation['candidate_sha256'],merge.candidate_signature(self.directory))

    def test_cancel_during_atomic_overlay_restores_every_applied_preimage(self):
        put(self.old,'a','old-a');put(self.old,'b','old-b')
        source=self.root/'simple-source';put(source,'a','new-a');put(source,'b','new-b')
        recovery=self.root/'simple-recovery';update_overlay.prepare(source,self.old,recovery)
        checks=iter([False,True])
        with self.assertRaises(InterruptedError):
            update_overlay.apply(source,self.old,recovery,stopped=lambda:next(checks))
        self.assertEqual((self.old/'a').read_text(),'old-a');self.assertEqual((self.old/'b').read_text(),'old-b')


class DurableAdmissionTests(unittest.TestCase):
    def test_cancelled_attention_releases_slot_only_after_named_producer_terminal(self):
        from localharness import session_host
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp)/'data';root=Path(tmp)/'program';root.mkdir();write(root/'python-app.json',{'version':'2.3.0'})
            previous={'id':'f'*32,'kind':'install','state':'attention','tree':str(tree),'program':str(root),
                'error':'Startup failed and previous program restored','helper':{'pid':123,'birth':1.0}}
            jobs._save(tree,previous)
            with patch.object(session_host,'process_state',return_value='alive'):
                result=jobs.request(tree,'cancel',root=root,identity=previous['id'])
                self.assertEqual(result['state'],'attention')
                with self.assertRaisesRegex(ValueError,'already admitted'):jobs.request(tree,'plan',root=root)
            with patch.object(session_host,'process_state',return_value='absent'):
                next_job=jobs.request(tree,'plan',root=root)
            self.assertNotEqual(next_job['id'],previous['id'])
            old=read(jobs.path(tree).parent/'desktop-update-jobs'/(previous['id']+'.json'))
            self.assertEqual(old['state'],'cancelled');self.assertIn('cancel_settlement',old)
            self.assertEqual(old['error'],previous['error'])
            self.assertTrue(jobs.cancelled(tree,previous['id']))

    def test_cancel_is_monotonic_and_resume_cannot_revive_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp)/'data';root=Path(tmp)/'program';root.mkdir();write(root/'python-app.json',{'version':'2.3.0'})
            value=jobs.request(tree,'plan',root=root,owner_words='Update preserving my code',owner_spoke=True)
            jobs.request(tree,'cancel',root=root,identity=value['id'])
            value=jobs.status(tree);value['state']='attention';write(jobs.path(tree),value)
            with self.assertRaisesRegex(ValueError,'Cancelled'):jobs.request(tree,'resume',root=root,identity=value['id'])
            self.assertTrue(jobs.cancelled(tree,value['id']))

    def test_progress_does_not_report_close_as_cancellation(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'program';tree=Path(tmp)/'data';root.mkdir()
            owner=SimpleNamespace(root=root,current=lambda:SimpleNamespace(tree=tree))
            with patch('localharness.update_progress.status',return_value={'stage':'waiting_review','id':'same'}):
                self.assertEqual(python_updates.update_progress(owner)['stage'],'waiting_review')

    def test_linked_git_preservation_uses_real_background_flag_callers(self):
        from localharness import app_migration,update_process
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);source=base/'source';target=base/'copy';gitdir=base/'git-worktree'
            gitdir.mkdir();put(source,'.git','gitdir: '+str(gitdir));put(source,'keep.py','keep=True\n')
            def output(argv,**kwargs):
                return (str(gitdir) if '--absolute-git-dir' in argv else 'a'*40).encode()
            def run(argv,**kwargs):
                if 'clone' in argv:target.mkdir();(target/'.git').mkdir()
                return subprocess.CompletedProcess(argv,0)
            with patch.object(app_migration.subprocess,'check_output',side_effect=output) as read_git, \
                 patch.object(app_migration.subprocess,'run',side_effect=run) as run_git:
                app_migration.preserve_source(source,target,source_only=True)
            for call in [*read_git.call_args_list,*run_git.call_args_list]:
                self.assertEqual(call.kwargs['creationflags'],update_process.background_flags())


if __name__=='__main__':unittest.main()
