"""Hermetic app merger API and actual incoming-host protocol proof.

The small verification fixture is deliberately not product release approval.
"""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import source_update_worker as worker, source_merge as merge
from localharness import update_composition as composition
from localharness.package_notice import read, write
import t_app_source_update_3110 as fixtures


class ApplicationMergerAPI(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AppSourceUpdate('runTest')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.owner_edit()
        self.f.put(self.f.new, 'app/localharness/feature.py',
                   fixtures.FEATURE.replace('owner = "factory"', 'owner = "upstream"'))
        self.f.put(self.f.old,'app/resources/icon.bin',b'\x00authored-icon\xfe')
        self.f.put(self.f.new,'app/resources/icon.bin',b'\x00incoming-icon\xfd')
        if self._testMethodName=='test_full_app_plan_selects_core_app_directory_and_guards_explicit_surfaces':
            self.f.put(self.f.old,'tree/app/foo.py','CORE = "authored"\n')
            self.f.put(self.f.new,'tree/app/foo.py','CORE = "incoming"\n')
        self.f.prepare_pair()
        self.operation = read(self.f.directory/'pipeline.json')
        self.operation.update(state='merging', task_id='fixture-worker')
        write(self.f.directory/'pipeline.json', self.operation)
        self.request = {'task_id':'fixture-worker'}
        self.adapter = worker.SourceMergeAdapter({'root':str(self.f.directory),
            'plan_id':self.f.directory.name, 'input_sha256':self.operation['input_sha256']})
        self.adapter.framework = 'hermetic-fixture'
        self.enterContext(patch('localharness.owner_stop.stopped', return_value=False))
        self.enterContext(patch('localharness.owner_stop.present', return_value=False))

    def call(self, action, **args):
        return json.loads(self.adapter.dispatch(self.request,'merge_source',
            {'action':action,'expected_input_sha256':self.operation['input_sha256'],**args}))

    def test_application_reads_exact_immutable_sides_and_rejects_unbound_writes(self):
        before = self.f.snapshot(self.f.directory/'compound/app')
        result = self.call('read',path='app/localharness/feature.py')
        self.assertIn('owner = "factory"',result['base']['text'])
        self.assertIn('owner = "authored"',result['local']['text'])
        self.assertIn('owner = "upstream"',result['incoming']['text'])
        status = self.call('status')
        self.assertIn('localharness/feature.py',status['app_conflicts'])
        for args in ({}, {'expected_composition_sha256':'0'*64,'reason':'stale'},
                     {'expected_composition_sha256':status['composition_sha256']}):
            with self.subTest(args=args),self.assertRaises(ValueError):
                self.call('resolve',path='app/localharness/feature.py',content='owner = "merged"\n',**args)
        self.assertEqual(before,self.f.snapshot(self.f.directory/'compound/app'))
        with self.assertRaisesRegex(ValueError,'expected_input'):
            self.adapter.dispatch(self.request,'merge_source',{'action':'resolve',
                'path':'app/localharness/feature.py','side':'incoming',
                'expected_input_sha256':'0'*64,'expected_composition_sha256':status['composition_sha256'],
                'reason':'wrong source input'})

    def test_application_compile_and_path_failures_preserve_candidate_and_pristine_B(self):
        incoming = self.f.snapshot(self.f.new)
        before = self.f.snapshot(self.f.directory/'compound/app')
        for path,text in (('app/localharness/feature.py','def broken(:\n'),
                          ('app/../python-app.json','{}'),('app/private/live.json','{}'),
                          ('app/verification/manifest.json','{}')):
            with self.subTest(path=path),self.assertRaises((ValueError,SyntaxError)):
                self.call('resolve',path=path,content=text,reason='rejected invalid repair',
                    expected_composition_sha256=self.call('status')['composition_sha256'])
        self.assertEqual(before,self.f.snapshot(self.f.directory/'compound/app'))
        self.assertEqual(incoming,self.f.snapshot(self.f.new))

    def test_finish_requires_both_surfaces_and_stamps_full_composition(self):
        with self.assertRaises(ValueError):self.call('finish')
        status=self.call('status')
        self.call('resolve',path='app/localharness/feature.py',content='owner = "merged"\n',
            reason='Preserve authored behavior with upstream API',
            expected_composition_sha256=status['composition_sha256'])
        self.call('resolve',path='app/resources/icon.bin',side='local',
            reason='Keep exact authored icon resource',
            expected_composition_sha256=self.call('status')['composition_sha256'])
        self.assertTrue(self.adapter.completion_feedback(self.request))
        with self.assertRaises(ValueError):self.adapter.result_stamp()
        self.call('finish')
        self.assertEqual(self.adapter.completion_feedback(self.request),'')
        result=self.adapter.result_stamp()
        self.assertTrue(result['candidate_verified'])
        self.assertTrue(result['composition_verified'])
        self.assertEqual(result['composition_sha256'],composition.signature(self.f.directory))
        self.assertEqual(self.f.new.joinpath('app/localharness/feature.py').read_text(),
            fixtures.FEATURE.replace('owner = "factory"','owner = "upstream"'))

    def test_binary_side_choice_and_nonconflict_repair_bind_current_composition(self):
        original=self.f.old.joinpath('app/resources/icon.bin').read_bytes()
        status=self.call('status')
        self.assertTrue(self.call('read',path='app/resources/icon.bin')['base']['binary'])
        with self.assertRaisesRegex(ValueError,'immutable'):
            self.call('resolve',path='app/resources/icon.bin',side='local',content='unexpected override',
                reason='Ambiguous request',expected_composition_sha256=status['composition_sha256'])
        self.call('resolve',path='app/resources/icon.bin',side='local',reason='Exact authored icon bytes',
                  expected_composition_sha256=status['composition_sha256'])
        self.assertEqual(self.f.directory.joinpath('compound/app/resources/icon.bin').read_bytes(),original)
        self.assertNotEqual(original,self.f.new.joinpath('app/resources/icon.bin').read_bytes())
        self.call('resolve',path='app/localharness/new_feature.py',content='NEW = True\n',
            reason='Explicit added integration dependency',
            expected_composition_sha256=self.call('status')['composition_sha256'])
        self.assertEqual(self.f.directory.joinpath('app-merge/result/localharness/new_feature.py').read_text(),'NEW = True\n')
        with self.assertRaisesRegex(ValueError,'composition'):
            self.call('resolve',path='app/resources/icon.bin',side='incoming',reason='Stale snapshot',
                      expected_composition_sha256=status['composition_sha256'])

    def test_new_factory_templates_can_be_read_without_exposing_authored_credentials(self):
        public='Authored instructions shipped as a factory template.\n'
        for name in ('resources/soul/AGENTS.md','resources/SOUL.md'):
            self.call('resolve',path='app/'+name,content=public,reason='Add reviewed factory resource template',
                expected_composition_sha256=self.call('status')['composition_sha256'])
            result=self.call('read',path='app/'+name)
            self.assertFalse(result['base']['exists'])
            self.assertFalse(result['incoming']['exists'])
            self.assertEqual(result['result']['text'],public)
            self.assertFalse(self.f.old.joinpath('app',name).exists())
            self.assertFalse(self.f.new.joinpath('app',name).exists())
        for name,text in (('resources/soul/AGENTS.md','api_key = "sk-'+('a'*28)+'"\n'),
                          ('localharness/AGENTS.md','Private authored rules.\n')):
            self.call('resolve',path='app/'+name,content=text,reason='Fixture credential custody boundary',
                expected_composition_sha256=self.call('status')['composition_sha256'])
            row=self.call('read',path='app/'+name)['result']
            self.assertIn('withheld',row)
            self.assertNotIn('text',row)

    def test_core_only_app_named_directory_retains_original_merge_contract(self):
        directory=self.f.root/('b'*64)
        sides={side:self.f.root/('core-'+side) for side in ('base','local','incoming')}
        for side,value in (('base','factory'),('local','authored'),('incoming','incoming')):
            self.f.put(sides[side],'app/module.py','VALUE = '+repr(value)+'\n')
        plan=merge.create(sides['base'],sides['local'],sides['incoming'],directory,exe=self.f.git)
        operation={'state':'merging','task_id':'core-app-directory','input_sha256':merge.input_signature(plan),
                   'tree':str(self.f.data),'program':str(self.f.new)}
        write(directory/'pipeline.json',operation)
        adapter=worker.SourceMergeAdapter({'root':str(directory),
            'plan_id':directory.name,'input_sha256':operation['input_sha256']})
        adapter.framework='hermetic-fixture'
        request={'task_id':'core-app-directory'}
        def call(action,**args):
            return json.loads(adapter.dispatch(request,'merge_source',{
                'action':action,'expected_input_sha256':operation['input_sha256'],**args}))
        self.assertEqual(call('read',path='app/module.py')['local']['text'],"VALUE = 'authored'\n")
        call('resolve',path='app/module.py',side='incoming')
        call('finish')
        self.assertEqual(directory.joinpath('result/app/module.py').read_text(),"VALUE = 'incoming'\n")
        self.assertTrue(adapter.result_stamp()['candidate_verified'])
        self.assertNotIn('composition_verified',adapter.result_stamp())

    def test_full_app_plan_selects_core_app_directory_and_guards_explicit_surfaces(self):
        before=composition.signature(self.f.directory)
        status=self.call('status')
        self.assertEqual(status['path_surfaces'],['core','app'])
        self.assertIn('app/foo.py',status['conflicts'])
        for invalid in ('tree','',None,[],1):
            with self.subTest(surface=invalid),self.assertRaisesRegex(ValueError,'surface'):
                self.call('resolve',surface=invalid,path='app/foo.py',content='CORE = "wrong"\n')
        self.assertEqual(before,composition.signature(self.f.directory))
        result=self.call('read',surface='core',path='app/foo.py')
        self.assertEqual(result['local']['text'],'CORE = "authored"\n')
        self.assertEqual(result['incoming']['text'],'CORE = "incoming"\n')
        self.assertIn('owner = "authored"',self.call('read',surface='app',path='localharness/feature.py')['local']['text'])
        for path,side in (('localharness/feature.py','local'),('resources/icon.bin','incoming')):
            with self.subTest(path=path),self.assertRaises(ValueError):
                self.call('resolve',surface='app',path=path,side=side,reason='Missing current composition')
        with self.assertRaises(ValueError):
            self.call('resolve',surface='core',path='agent.py',side='incoming')
        self.call('resolve',surface='core',path='app/foo.py',content='CORE = "merged"\n')
        self.call('resolve',surface='app',path='localharness/feature.py',content='owner = "merged"\n',
            expected_composition_sha256=self.call('status')['composition_sha256'],reason='Reconcile application intent')
        self.call('resolve',surface='app',path='resources/icon.bin',side='local',
            expected_composition_sha256=self.call('status')['composition_sha256'],reason='Exact authored resource')
        self.call('finish')
        self.assertEqual(self.f.directory.joinpath('result/app/foo.py').read_text(),'CORE = "merged"\n')
        self.assertFalse(self.f.directory.joinpath('compound/app/foo.py').exists())
        self.assertEqual(self.f.directory.joinpath('compound/app/resources/icon.bin').read_bytes(),b'\x00authored-icon\xfe')
        self.assertTrue(self.adapter.result_stamp()['composition_verified'])

    def test_selected_durable_pause_or_unconfirmed_epoch_blocks_read_and_repair(self):
        from localharness import owner_stop
        selected=self.f.data/'memory/.control/autonomy-generation.json'
        unrelated=self.f.root/'other-profile'
        write(unrelated/'memory/.control/autonomy-generation.json',{
            'format':'helene-autonomy-generation.v1','id':'2'*32,'paused':False})
        before=composition.signature(self.f.directory)
        with patch.object(owner_stop,'_tree',unrelated):
            for value in ({'format':'helene-autonomy-generation.v1','id':'1'*32,'paused':True},
                          {'format':'damaged-generation','paused':False}):
                write(selected,value)
                current=composition.signature(self.f.directory)
                for action,args in (('read',{}),('resolve',{'content':'owner = "forbidden"\n',
                    'reason':'Must not execute while durable pause is unconfirmed',
                    'expected_composition_sha256':current})):
                    with self.subTest(epoch=value,action=action),self.assertRaisesRegex(ValueError,'Stop/Pause'):
                        self.call(action,surface='app',path='localharness/feature.py',**args)
            self.assertEqual(before,composition.signature(self.f.directory))
            write(selected,{'format':'helene-autonomy-generation.v1','id':'3'*32,'paused':False})
            write(unrelated/'memory/.control/autonomy-generation.json',{
                'format':'helene-autonomy-generation.v1','id':'4'*32,'paused':True})
            self.assertIn('local',self.call('read',surface='app',path='localharness/feature.py'))


@unittest.skipIf(os.name=='nt','Real detached incoming host runs only in isolated Linux')
class IncomingApplicationProcess(unittest.TestCase):
    def test_app_conflict_real_failed_battery_review_repair_install_and_rollback(self):
        import t_update_host_process_224 as host_fixture
        source='localharness/update_app_fixture.py'
        resource='resources/update-app-fixture.txt'
        def code(value):return 'def presentation():\n    return '+repr(value)+'\n'
        final=code('authored/v2')
        files={source:{'base':code('factory/v1'),'local':code('authored/v1'),
                       'incoming':code('factory/v2'),'merged':final},
               resource:{'base':'factory resource/v1\n','local':'authored resource/v1\n',
                         'incoming':'factory resource/v2\n','merged':'authored resource/v2\n'}}
        def sequence(attempt):
            if attempt==1:
                return [{'action':'read','path':'upgrade_fixture.py'},
                    {'action':'resolve','path':'upgrade_fixture.py','content':host_fixture.MERGED},
                    {'action':'status'},
                    {'action':'resolve','path':'compatibility_fixture.py',
                     'content':host_fixture.LOCAL_FEATURE+host_fixture.HELPER,
                     'reason':'Restore shared dependency while retaining authored feature'},
                    {'action':'read','path':'app/'+source},
                    {'action':'read','path':'app/'+resource},{'action':'status'},
                    {'action':'resolve','path':'app/'+source,'content':code('authored/v2-broken'),
                     'reason':'Fixture initial integration intentionally violates its actual executable contract'},
                    {'action':'resolve','path':'app/'+resource,'content':'factory resource/v2\n',
                     'reason':'Fixture initial integration retains upstream resource but loses authored intent'},
                    {'action':'finish'}]
            if attempt==2:
                return [{'action':'status'},{'action':'read','path':'app/'+source},
                    {'action':'resolve','path':'app/'+source,'content':final,
                     'reason':'Repair actual failed presentation contract; preserve authored identity and new version'},
                    {'action':'finish'}]
            if attempt==3:
                return [{'action':'status'},{'action':'read','path':'app/'+resource},
                    {'action':'resolve','path':'app/'+resource,'content':files[resource]['merged'],
                     'reason':'Independent inspection found authored resource intent missing from the integration'},
                    {'action':'finish'}]
            raise AssertionError('Unexpected further merger attempt: '+str(attempt))
        test=('import unittest\nfrom localharness.update_app_fixture import presentation\n'
              'class ApplicationContract(unittest.TestCase):\n'
              ' def test_authored_identity_uses_new_upstream_contract(self):\n'
              '  self.assertEqual(presentation(), "authored/v2")\n')
        host_fixture.LegacyHostProcess._run_host_fixture(self,{
            'files':files,'desk_test':test,'sequence':sequence,
            'expected':{'harness/'+source:final,'harness/'+resource:files[resource]['merged']}})


if __name__=='__main__':unittest.main()
