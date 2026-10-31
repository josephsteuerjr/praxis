"""Real private slot copies and barriers; no model or production processes."""
from pathlib import Path
import hashlib,json,os,shutil
import sys,tempfile,threading,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import source_update_parallel as parallel,source_update_battery as battery
from localharness.package_notice import read


class Parallel(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=self.root/'template';self.repo.mkdir()
        (self.repo/'logic.py').write_text('VALUE=1\n')
        self.files=battery.bundle.inventory(self.repo)
        self.cache=self.root/'cache';self.cache.mkdir();self.work=self.root/'work';self.work.mkdir()

    def test_quiescent_measurement_follows_settled_parallel_commands_in_a_fresh_copy(self):
        self.work.rename(self.root/'helene-update-tests-quiescent');self.work=self.root/'helene-update-tests-quiescent'
        lanes=[{'id':'latency','kind':'script','sources':['logic.py'],'isolation':'quiescent'},
               *[{'id':str(i),'kind':'script','sources':['logic.py']} for i in range(2)]]
        barrier=threading.Barrier(2);finished=set();events=[];lock=threading.Lock()
        def execute(lane,repo,runner,tools,env,*args):
            if lane['id']=='latency':
                self.assertEqual(finished,{'0','1'})
                self.assertFalse((repo.parent/'previous-command').exists())
                self.assertEqual(len(list(self.work.glob('slot-*'))),1)
            else:
                (repo.parent/'previous-command').touch();barrier.wait(timeout=5)
                with lock:finished.add(lane['id'])
            with lock:events.append(lane['id'])
            return {'id':lane['id'],'status':'passed','exit':0}
        with patch.object(battery,'_run_lane',side_effect=execute):
            rows,changes,_=parallel.run(battery,lanes,self.repo,self.files,self.work,None,{},
                {'verification_executor':{'parallel_lanes':2}},self.cache,lambda:False,{'executor':{}},{})
        self.assertEqual(events[-1],'latency')
        self.assertEqual([row['id'] for row in rows],[row['id'] for row in lanes])
        self.assertFalse(changes)
        progress=read(self.cache/'progress.json')
        self.assertEqual(progress['completed_lanes'],len(lanes));self.assertEqual(progress['state'],'completed')

    def test_unconfirmed_parallel_child_never_admits_quiescent_work(self):
        lanes=[{'id':'functional','kind':'script','sources':['logic.py']},
               {'id':'latency','kind':'script','sources':['logic.py'],'isolation':'quiescent'}]
        seen=[]
        def execute(lane,*args):
            seen.append(lane['id']);raise battery.CustodyUnconfirmed('owned child not settled')
        with patch.object(battery,'_run_lane',side_effect=execute),self.assertRaises(battery.CustodyUnconfirmed):
            parallel.run(battery,lanes,self.repo,self.files,self.work,None,{}, {},self.cache,lambda:False,{'executor':{}},{})
        self.assertEqual(seen,['functional'])
        self.assertEqual(read(self.cache/'progress.json')['state'],'interrupted')

    def test_source_declared_measurement_isolation_is_part_of_the_frozen_recipe(self):
        from localharness import source_verification_bundle as bundle
        payload={'live/praxis_test.py':'','live/test_base.py':'import unittest\n',
            'desk/tests/t_measurement.py':"HELENE_TEST_ISOLATION='quiescent'\nimport unittest\n",
            'desk/app/test/sample.mjs':''}
        for name,text in payload.items():
            path=self.repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        files=bundle.inventory(self.repo);logical=bundle.recipe(files)
        row=next(r for r in parallel.execution_lanes(logical,self.repo,files) if r.get('source')=='desk/tests/t_measurement.py')
        self.assertEqual(row['isolation'],'quiescent')
        self.assertNotIn('isolation',next(r for r in logical if r.get('source')=='desk/tests/t_measurement.py'))
        with self.assertRaisesRegex(ValueError,'differs from frozen source'):
            parallel.execution_lanes([{'source':'desk/tests/t_measurement.py','isolation':'parallel'}],self.repo,files)

    def test_twelve_overlapping_slots_have_private_sources_and_homes_and_ordered_results(self):
        lanes=[{'id':f'case-{i}','kind':'script','sources':['logic.py']} for i in range(12)]
        barrier=threading.Barrier(12);lock=threading.Lock();seen=[]
        def execute(lane,repo,runner,tools,env,*args):
            with lock:seen.append((str(repo),env['HOME'],env['TEMP']))
            barrier.wait(timeout=10)
            self.assertEqual((repo/'logic.py').read_text(),'VALUE=1\n')
            return {'id':lane['id'],'status':'passed','exit':0}
        with patch.object(battery,'_run_lane',side_effect=execute):
            rows,changes,_=parallel.run(battery,lanes,self.repo,self.files,self.work,None,{}, {},self.cache,lambda:False,{'executor':{}},{})
        self.assertEqual([row['id'] for row in rows],[row['id'] for row in lanes])
        self.assertEqual(len({row[0] for row in seen}),12)
        self.assertEqual(len({row[1] for row in seen}),12)
        self.assertEqual(len({row[2] for row in seen}),12)
        self.assertFalse(changes);self.assertEqual(len(list(self.cache.glob('*.lane.json'))),12)

    def test_core_is_partitioned_into_twelve_and_dependency_chains_keep_order(self):
        core={'id':'core','kind':'unittest','sources':['logic.py']}
        lanes=[{'id':'npm1','kind':'npm-prepare'},core,{'id':'node','kind':'node'},{'id':'npm2','kind':'npm-build'},
               {'id':'cargo1','kind':'cargo'},{'id':'cargo2','kind':'cargo-build'}]
        groups=parallel.groups(lanes,12)
        self.assertEqual(len(groups),12)
        self.assertEqual(sum('_shard_index' in lane for group in groups for _,lane in group),12)
        self.assertEqual([lane['id'] for group in groups for _,lane in group if lane['kind'] in {'npm-prepare','node','npm-build'}],['npm1','node','npm2'])
        self.assertEqual(len(parallel.groups(lanes,12,external_native=True)),1)

    def test_mismatched_shard_discovery_cannot_be_a_full_pass(self):
        parts=[]
        for i in range(2):
            observation={'discovered':['a','b'] if i==0 else ['a','different'],
                'collected':['a'] if i==0 else ['b'],'executed':['a'] if i==0 else ['b'],
                'complete':True,'coverage_complete':True,'outcomes':[],'tests_run':1}
            parts.append(({'_shard_index':i,'_shard_count':2},{'status':'passed','exit':0,'observation':observation}))
        self.assertEqual(parallel.aggregate({'id':'core','kind':'unittest'},parts)['status'],'failed')

    def test_private_cli_capabilities_do_not_globally_serialize_native_recipe(self):
        binding = {'executor': {'native_inputs': {'native_env': {'PRAXIS_HANDS': '/owned/hands'}}}}
        self.assertFalse(parallel.shared_native_session(binding))
        core = [{'id': 'core', 'kind': 'unittest', 'sources': ['logic.py']}]
        self.assertEqual(len(parallel.groups(core, 12, parallel.shared_native_session(binding))), 12)
        binding['executor']['native_inputs']['native_env']['DISPLAY'] = ':97'
        self.assertTrue(parallel.shared_native_session(binding))
        self.assertEqual(len(parallel.groups(core,12)),12)
        scoped,_=parallel.native_scope(core[0],{'DISPLAY':':97'},binding['executor']['native_inputs'])
        self.assertNotIn('DISPLAY',scoped)

    def native(self):
        base=self.root/'native';base.mkdir()
        suffix='.exe' if os.name=='nt' else ''
        paths={name:base/(name+suffix) for name in ('helene-body','helene-bridge','host','hands')}
        for path in paths.values():path.write_bytes(b'synthetic owned binary, never executed')
        value={'native_env':{'HELENE_BODY_DIR':str(base),'HELENE_BODY_BIN':str(paths['helene-body']),
            'DISPLAY':':owned-test','DBUS_SESSION_BUS_ADDRESS':'unix:path=/owned/test',
            'XDG_RUNTIME_DIR':str(base),'HELENE_HOST_BINARY':str(paths['host']),'PRAXIS_HANDS':str(paths['hands'])},
            'native_files':{str(path):battery.bundle.digest(path) for path in paths.values()}}
        return battery._native_inputs(value)

    def test_shared_desktop_inputs_keep_twelve_private_lanes_and_then_one_quiet_consumer(self):
        source=self.repo/'desk/tests/t_desktop.py';source.parent.mkdir(parents=True)
        source.write_text("HELENE_TEST_NATIVE_INPUTS=('DISPLAY',)\n")
        self.files=battery.bundle.inventory(self.repo);native=self.native()
        private=[{'id':str(i),'kind':'script','sources':['logic.py']} for i in range(12)]
        lanes=[{'id':'desktop','kind':'script','source':'desk/tests/t_desktop.py','sources':['desk/tests/t_desktop.py']},*private]
        barrier=threading.Barrier(12);finished=set();lock=threading.Lock();seen=[]
        def execute(lane,repo,runner,tools,env,cache,stopped,inputs):
            scoped,pins=parallel.native_scope(lane,env,inputs)
            if lane['id']=='desktop':
                self.assertEqual(finished,{str(i) for i in range(12)})
                self.assertEqual(scoped['DISPLAY'],':owned-test')
                self.assertNotIn('HELENE_BODY_DIR',scoped)
                self.assertEqual(len(list(self.work.glob('slot-*'))),1)
            else:
                self.assertFalse(set(scoped)&battery.bundle.SHARED_NATIVE_INPUTS)
                barrier.wait(timeout=10)
                with lock:finished.add(lane['id'])
            with lock:seen.append((lane['id'],str(repo),env['HOME']))
            return {'id':lane['id'],'status':'passed','exit':0}
        self.work.rename(self.root/'helene-update-tests-native');self.work=self.root/'helene-update-tests-native'
        with patch.object(battery,'_run_lane',side_effect=execute):
            rows,changes,_=parallel.run(battery,lanes,self.repo,self.files,self.work,None,{},
                {'verification_executor':{'parallel_lanes':12,**native}},self.cache,lambda:False,
                {'executor':{'native_inputs':native}},{})
        self.assertEqual(seen[-1][0],'desktop')
        self.assertEqual(len({row[1] for row in seen[:-1]}),12)
        self.assertEqual(len({row[2] for row in seen[:-1]}),12)
        self.assertEqual([row['id'] for row in rows],[row['id'] for row in lanes]);self.assertFalse(changes)

    def test_actual_undeclared_child_receives_no_shared_native_session_or_body(self):
        native=self.native();source=self.repo/'desk/probe.py';source.parent.mkdir()
        source.write_text('import os,json\nprint(json.dumps({k:v for k,v in os.environ.items() if k in '+repr(sorted(battery.bundle.SHARED_NATIVE_INPUTS|{'PRAXIS_HANDS','HELENE_HOST_BINARY'}))+'}))\n')
        env=battery._environment(self.root/'home',self.repo/'live',self.repo/'desk',{'verification_executor':native})
        lane={'id':'probe','kind':'script','source':'desk/probe.py','argv':[]}
        result=battery._run_lane(lane,self.repo,None,{'python':sys.executable},env,self.cache,lambda:False,native)
        self.assertEqual(result['status'],'passed')
        value=json.loads(next(self.cache.glob('*.output')).read_text())
        self.assertEqual(set(value),{'PRAXIS_HANDS','HELENE_HOST_BINARY'})
        self.assertTrue(set(env)&battery.bundle.SHARED_NATIVE_INPUTS,'Original slot environment is not mutated')

    def test_scoped_shared_body_requires_exact_pins_and_does_not_grant_other_sessions(self):
        native=self.native();lane={'native_inputs':['HELENE_BODY_DIR','HELENE_BODY_BIN']}
        env,pins=parallel.native_scope(lane,native['native_env'],native)
        self.assertIn('HELENE_BODY_BIN',env);self.assertNotIn('DISPLAY',env)
        self.assertEqual(battery._native_inputs(pins),pins)
        missing=dict(pins,native_files={})
        with self.assertRaises(ValueError):battery._native_inputs(missing)
        for names in (['UNKNOWN'],['DISPLAY','DISPLAY'],['HELENE_BODY_BIN']):
            with self.assertRaises(ValueError):parallel.native_scope({'native_inputs':names},{},native)
        with self.assertRaises(ValueError):parallel.native_scope({'native_inputs':['DISPLAY']},{'DISPLAY':':unbound'},None)

    def test_native_contract_is_frozen_in_inventory_recipe_and_private_copy(self):
        payload={'live/praxis_test.py':'','live/test_base.py':'import unittest\n',
            'desk/tests/t_desktop.py':"HELENE_TEST_NATIVE_INPUTS=('DISPLAY',)\nimport unittest\n",
            'desk/app/test/sample.mjs':''}
        for name,text in payload.items():
            path=self.repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        files=battery.bundle.inventory(self.repo);lanes=battery.bundle.recipe(files)
        self.assertEqual(files['desk/tests/t_desktop.py']['native_inputs'],['DISPLAY'])
        self.assertEqual(parallel.inventory(battery,self.repo,lambda:False),files)
        desktop=next(lane for lane in parallel.execution_lanes(lanes,self.repo,files) if lane['id']=='desk/tests/t_desktop.py')
        self.assertEqual(desktop['isolation'],'quiescent');self.assertEqual(desktop['native_inputs'],['DISPLAY'])
        bad={name:dict(row) for name,row in files.items()};bad['desk/tests/t_desktop.py'].pop('native_inputs')
        with self.assertRaisesRegex(ValueError,'metadata differs'):parallel.execution_lanes(lanes,self.repo,bad)
        root=self.root/'bundle';shutil.copytree(self.repo,root/'repository');(root/'runner').mkdir()
        manifest={'format':battery.bundle.FORMAT,'version':'fixture','files':files,'runners':{},'lanes':lanes}
        from localharness.package_notice import write
        write(root/'manifest.json',manifest);battery.bundle.load(root,battery.bundle.digest(root/'manifest.json'),'fixture')
        manifest['files']=bad;manifest['lanes']=battery.bundle.recipe(bad);write(root/'manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'source/runner changed'):
            battery.bundle.load(root,battery.bundle.digest(root/'manifest.json'),'fixture')

    def test_invalid_or_duplicate_source_capability_declarations_refuse(self):
        source=self.repo/'test_contract.py'
        for declaration in ("['DISPLAY']","('UNKNOWN',)","('DISPLAY','DISPLAY')","('HELENE_BODY_BIN',)","tuple(['DISPLAY'])"):
            source.write_text('HELENE_TEST_NATIVE_INPUTS='+declaration+'\n')
            with self.assertRaises(ValueError):battery.bundle.inventory(self.repo)
        source.write_text("HELENE_TEST_NATIVE_INPUTS=('DISPLAY',)\nHELENE_TEST_NATIVE_INPUTS=()\n")
        with self.assertRaises(ValueError):battery.bundle.inventory(self.repo)

    def test_stop_during_copy_does_not_launch_a_lane(self):
        calls=[0]
        def stopped():calls[0]+=1;return calls[0]>3
        with self.assertRaises(InterruptedError):parallel._copy(self.repo,self.work/'copy',stopped)
        self.assertEqual((self.repo/'logic.py').read_text(),'VALUE=1\n')

    def test_custody_failure_outranks_cancellation_and_all_threads_settle(self):
        lanes=[{'id':str(i),'kind':'script','sources':['logic.py']} for i in range(2)]
        barrier=threading.Barrier(2);settled=[]
        def execute(lane,*args):
            barrier.wait(timeout=5)
            try:
                if lane['id']=='0':raise battery.CustodyUnconfirmed('fixture unknown child')
                raise InterruptedError('fixture Stop')
            finally:settled.append(lane['id'])
        with patch.object(battery,'_run_lane',side_effect=execute),self.assertRaises(battery.CustodyUnconfirmed):
            parallel.run(battery,lanes,self.repo,self.files,self.work,None,{}, {},self.cache,lambda:False,{'executor':{}},{})
        self.assertEqual(set(settled),{'0','1'})

    def test_invalid_policy_and_duplicate_identity_refused(self):
        for value in [0,13,True,'12']:
            with self.assertRaises(ValueError):parallel.policy({'verification_executor':{'parallel_lanes':value}})
        with self.assertRaises(ValueError):parallel.groups([{'id':'same'},{'id':'same'}],12)

    def test_finished_file_is_published_while_the_same_slot_runs_its_next_file(self):
        lanes=[{'id':str(i),'kind':'script','sources':['logic.py']} for i in range(2)]
        active=threading.Event();release=threading.Event();errors=[]
        def execute(lane,*args):
            if lane['id']=='1':active.set();release.wait(timeout=10)
            return {'id':lane['id'],'status':'passed','exit':0,
                    'observation':{'tests_run':3,'outcomes':[{'status':'passed'}]*3}}
        def run():
            try:parallel.run(battery,lanes,self.repo,self.files,self.work,None,{},
                {'verification_executor':{'parallel_lanes':1}},self.cache,lambda:False,{'executor':{}},{})
            except BaseException as exc:errors.append(exc)
        with patch.object(battery,'_run_lane',side_effect=execute):
            thread=threading.Thread(target=run);thread.start()
            try:
                self.assertTrue(active.wait(timeout=10))
                progress=read(self.cache/'progress.json')
                self.assertEqual(progress['completed_lanes'],1)
                self.assertEqual(progress['tests_completed'],3)
                self.assertEqual(progress['active'][0]['lane'],'1')
                self.assertGreater(progress['copied_files'],0)
            finally:release.set();thread.join(timeout=15)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assertEqual(read(self.cache/'progress.json')['completed_lanes'],2)

    def test_a_free_slot_takes_files_that_would_wait_behind_a_slow_slot(self):
        lanes=[{'id':str(i),'kind':'script','sources':['logic.py']} for i in range(4)]
        active=threading.Event();release=threading.Event();finished=threading.Event();seen=set();lock=threading.Lock();errors=[]
        def execute(lane,*args):
            if lane['id']=='0':active.set();release.wait(timeout=10)
            else:
                with lock:
                    seen.add(lane['id'])
                    if seen=={'1','2','3'}:finished.set()
            return {'id':lane['id'],'status':'passed','exit':0}
        def run():
            try:parallel.run(battery,lanes,self.repo,self.files,self.work,None,{},
                {'verification_executor':{'parallel_lanes':2}},self.cache,lambda:False,{'executor':{}},{})
            except BaseException as exc:errors.append(exc)
        with patch.object(battery,'_run_lane',side_effect=execute):
            thread=threading.Thread(target=run);thread.start()
            try:
                self.assertTrue(active.wait(timeout=10))
                self.assertTrue(finished.wait(timeout=5),'Independent files idled behind a slow static group')
            finally:release.set();thread.join(timeout=15)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])

if __name__=='__main__':unittest.main()
