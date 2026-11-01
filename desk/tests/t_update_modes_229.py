"""Owner-agent resolution, explicit pristine source and desktop consent boundaries."""
from pathlib import Path
from types import SimpleNamespace
import json,sys,threading,unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).parent))
import t_source_update_pipeline_220 as fixtures
from localharness import update_source as policy,source_merge as merge,source_update as pipeline,code_transition
from localharness import agent_source_update as selected,desktop_update_request as requests,update_window
from localharness.package_notice import read,write

class UpdateModes(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.PipelineTests('runTest')
        # Construct this exact delivery before its first admission. Overwriting
        # python-app.json after setUp would erase the real v2 baseline anchor.
        self.f.incoming_version='2.2.9'
        self.f.setUp();self.addCleanup(self.f.doCleanups)

    def agent(self):
        f=self.f
        from localharness import source_update
        (f.old/'app/localharness').mkdir(parents=True,exist_ok=True)
        (f.old/'app/localharness/source_update.py').write_bytes(Path(source_update.__file__).read_bytes())
        policy.select(f.old,f.new,'preserve',by='window');f.preflight()
        f.request=read(pipeline.path(f.tree));f.directory=Path(f.request['directory'])
        return f

    def test_no_implicit_pristine_fallback_and_choice_binds_delivery(self):
        f=self.f
        self.assertEqual(policy.require(f.old,f.new)['mode'],'preserve')
        with self.assertRaises(ValueError):policy.select(f.old,f.new,'anything',by='window')
        policy.select(f.old,f.new,'preserve',by='window',require_review=True,resolution_owner='incoming')
        with self.assertRaisesRegex(ValueError,'already selected'):policy.select(f.old,f.new,'clean',by='window')
        upstream=read(f.new/'code-upstream.json');upstream['sha256']='0'*64;write(f.new/'code-upstream.json',upstream)
        with self.assertRaisesRegex(ValueError,'stale'):policy.selected(f.old,f.new)

    def test_clean_code_exact_pristine_old_code_profile_config_retained(self):
        f=self.f;write(f.tree/'memory/owner.json',{'keep':'authored data'})
        before=merge.inventory(f.code);settings=(f.old/'helene.json').read_bytes();memory=(f.tree/'memory/owner.json').read_bytes()
        write(f.new/'helene.json',read(f.old/'helene.json'))
        choice=policy.select(f.old,f.new,'clean',by='window');note={'id':'fixture','source_paths':[{'old':str(f.code),'selected':str(f.code)}]}
        policy.clean_prepare(f.old,f.new,note,choice);code_transition.validate(f.new)
        target=Path(note['source_paths'][0]['selected'])
        self.assertEqual(merge.inventory(target),merge.inventory(f.new/'tree'))
        self.assertNotEqual(merge.inventory(target),before);self.assertEqual(merge.inventory(f.code),before)
        self.assertEqual((f.old/'helene.json').read_bytes(),settings);self.assertEqual((f.tree/'memory/owner.json').read_bytes(),memory)
        (target/'module.py').write_text('changed=True\n')
        with self.assertRaisesRegex(ValueError,'incoming source changed'):code_transition.validate(f.new)

    def test_selected_agent_finishes_then_independent_review_without_merger_worker(self):
        f=self.agent();self.assertEqual(f.operation()['state'],'waiting_agent');self.assertFalse(f.spawns)
        status=pipeline.command(f.tree);self.assertIn('module.py',status['conflicts'])
        diff=pipeline.command(f.tree,'diff',path='module.py');self.assertIn('incoming/module.py',diff['text'])
        with self.assertRaisesRegex(ValueError,'Every write'):
            pipeline.command(f.tree,'resolve',path='module.py',content=fixtures.BOTH)
        pipeline.command(f.tree,'resolve',path='module.py',content=fixtures.BOTH,expected_candidate_sha256=status['candidate_sha256'])
        pipeline.command(f.tree,'finish')
        with self.assertRaises(ValueError):pipeline.validate_ready(f.directory)
        binding={**f.coordinator,'plan_id':f.directory.name}
        pipeline.ensure(f.tree,f.old,f.code,f.directory,'2.2.9',coordinator=binding)
        for _ in range(5):
            pipeline.tick(f.tree,f.forge,f.service,coordinator=binding)
            if f.operation()['state']=='reviewing':break
        self.assertEqual(f.operation()['state'],'reviewing',{k:f.operation().get(k) for k in ('state','finish_error','verification_error','error','continuation')});self.assertFalse(f.operation().get('task_id'))
        f.review_result();pipeline.tick(f.tree,f.forge,f.service,coordinator=binding)
        self.assertEqual(pipeline.validate_ready(f.directory)['state'],'ready_to_apply')
        self.assertEqual((f.directory/'result/module.py').read_text(),fixtures.BOTH)

    def test_clean_choice_refuses_new_authored_changes_after_consent(self):
        f=self.f;choice=policy.select(f.old,f.new,'clean',by='window')
        (f.code/'new.py').write_text('new_author_work=True\n')
        with self.assertRaisesRegex(ValueError,'changed after clean update consent'):
            policy.clean_prepare(f.old,f.new,{'id':'fixture','source_paths':[]},choice)

    def test_new_click_has_fresh_identity_without_reviving_cancelled_agent(self):
        import shutil
        f=self.agent();first=f.directory
        pipeline._cancel_marker(first,'Owner cancelled')
        second=f.root/'second-operation/Helene'
        # A fresh extracted delivery carries no authority/private state from
        # the cancelled operation whose programme files this fixture reuses.
        shutil.copytree(f.new,second,ignore=shutil.ignore_patterns('.helene','.source-updates'))
        policy.select(f.old,second,'preserve',by='window')
        code_transition.preflight(f.old,second)
        self.assertNotEqual(read(pipeline.path(f.tree))['id'],first.name)
        self.assertTrue((first/'cancel.json').is_file())

    def test_cancelled_agent_cannot_write_or_finish(self):
        f=self.agent();pipeline._cancel_marker(f.directory,'Owner cancelled')
        with self.assertRaisesRegex(ValueError,'cancelled'):pipeline.command(f.tree,'finish')

    def test_exact_patch_preserves_large_file_without_full_replacement(self):
        f=self.agent();status=pipeline.command(f.tree)
        with self.assertRaisesRegex(ValueError,'one exact'):
            pipeline.command(f.tree,'patch',path='module.py',before='missing',after='x',expected_candidate_sha256=status['candidate_sha256'])

    def test_clean_proposal_waits_for_explicit_owner_consent(self):
        f=self.f
        value=requests.request(f.tree,'plan',root=f.old,source_mode='clean')
        self.assertEqual(value['state'],'awaiting');self.assertEqual(value['source_mode'],'clean')
        with self.assertRaisesRegex(ValueError,'Direct owner'):requests.request(f.tree,'confirm',root=f.old)
        value=requests.request(f.tree,'confirm',root=f.old,identity=value['id'],by='window',source_mode='clean')
        self.assertEqual(value['state'],'confirmed')
        with self.assertRaisesRegex(ValueError,'No matching'):requests.request(f.tree,'confirm',root=f.old,by='window')

    def test_agent_clean_consent_and_consumer_are_single_admission(self):
        f=self.f
        value=requests.request(f.tree,'plan',root=f.old,source_mode='clean',owner_spoke=True,owner_words='обнови на чистый код без локальных правок')
        self.assertEqual(value['state'],'confirmed')
        agent=SimpleNamespace(tree=f.tree,enabled=True,conflict=False)
        from contextlib import nullcontext
        agent.id='base'
        owner=SimpleNamespace(root=f.old,stop=threading.Event(),event=mock.Mock(),selection=lambda _:nullcontext())
        from localharness import agents,owner_stop,python_updates
        import hashlib
        archive=f.root/'fixture.zip';archive.write_bytes(b'synthetic update archive')
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        with mock.patch.object(agents,'roster',return_value=[agent]),mock.patch.object(owner_stop,'stopped',return_value=False),mock.patch.object(python_updates,'release_endpoint',return_value='fixture'),mock.patch.object(python_updates,'update_check',return_value={'newer':True,'url':'fixture','sha256':digest}),mock.patch.object(python_updates,'update_download',return_value={'path':str(archive),'sha256':digest}),mock.patch.object(python_updates,'update_install',return_value={'updater_pid':123}) as install:
            requests.consume(owner);requests.consume(owner)
        install.assert_called_once_with(owner,str(archive),forceExtensions=False,sourceMode='clean');self.assertEqual(requests.status(f.tree)['state'],'handed_off')

    def test_updater_choice_is_bound_to_this_operation(self):
        f=self.f;stage=f.root/('.helene-update-'+'a'*32);stage.mkdir();f.new.rename(stage/'Helene');f.new=stage/'Helene'
        from localharness.update_progress import Progress
        report=Progress(f.root/(stage.name+'.progress.json'),'a'*32,f.old)
        report.phase('choosing_source',source_choice=policy.identity(f.old,f.new))
        update_window.choose_source(report.path,'clean')
        self.assertEqual(policy.require(f.old,f.new,report)['mode'],'clean')
        report.phase('switching')
        with self.assertRaisesRegex(ValueError,'not active'):update_window.choose_source(report.path,'preserve')

if __name__=='__main__':unittest.main()
