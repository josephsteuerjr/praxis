"""Selected/full scope, real partitioned imports and native protected inputs."""
from pathlib import Path
from collections import Counter
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import source_update_selection as selection
from localharness import source_update_parallel as parallel
from localharness import source_verification_runner as runner
from localharness import verification_readonly as readonly
from localharness.package_notice import read,write


class Selection(unittest.TestCase):
    def fixture(self):
        bodies={n:'import unittest\n' for n in selection.FLOOR}
        bodies.update({'live/test_feature.py':'import feature\n',
                       'live/test_other.py':'import other\n',
                       'live/feature.py':'VALUE=1\n'})
        files={n:{'sha256':'fixture'} for n in bodies}
        lanes=[{'id':'core','kind':'unittest','root':'live','sources':sorted(n for n in bodies if n.startswith('live/test'))}]
        lanes += [{'id':n,'kind':'unittest-file','sources':[n]} for n in bodies if n.startswith('desk/tests/')]
        lanes += [{'id':'node','kind':'node','sources':['desk/app/test/a.mjs']}]
        return bodies,files,lanes

    def test_selected_floor_upstream_and_local_consumers(self):
        bodies,files,lanes=self.fixture()
        result=selection.select(lanes,files,bodies.__getitem__,{'upstream':['live/feature.py'],'local':[]})
        self.assertEqual(result['tier'],'selected')
        core=next(l for l in result['selected_lanes'] if l['id']=='core')
        self.assertIn('live/test_feature.py',core['sources'])
        self.assertNotIn('live/test_other.py',core['sources'])
        self.assertTrue(selection.FLOOR<=set(result['reasons']))
        self.assertEqual(result['omitted_core_sources'],['live/test_other.py'])
        self.assertIn('node',[l['id'] for l in result['selected_lanes']])

    def test_unknown_cutover_apparatus_and_nonpython_take_full(self):
        bodies,files,lanes=self.fixture()
        for name in ('live/unrepresented.py','desk/localharness/source_update_host.py','desk/static/ui.js',next(iter(selection.FLOOR))):
            with self.subTest(name=name):
                result=selection.select(lanes,files,bodies.__getitem__,{'change':[name]})
                self.assertEqual(result['tier'],'full');self.assertEqual(result['selected_lanes'],lanes)

    def test_widening_and_policy_identity(self):
        bodies,files,lanes=self.fixture()
        a=selection.select(lanes,files,bodies.__getitem__,{})
        b=selection.select(lanes,files,bodies.__getitem__,{}, {'full_tests':True,'reason':'dynamic impact'})
        self.assertNotEqual(a,b);self.assertEqual(b['tier'],'full')
        with self.assertRaises(ValueError):selection.select(lanes,files,bodies.__getitem__,{}, {'extra_tests':['missing'],'reason':'x'})
        with self.assertRaises(ValueError):selection.select(lanes,files,bodies.__getitem__,{}, {'full_tests':True})


class Runner(unittest.TestCase):
    def test_flat_module_partition_matches_real_full_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'live';root.mkdir()
            (root/'praxis_test.py').write_text('# fixture bootstrap\n')
            (root/'test_one.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_one(self):pass\n')
            (root/'test_two.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_two(self):pass\n')
            (root/'test_empty.py').write_text('VALUE=1\n')
            full=Path(tmp)/'full.json'
            command=[sys.executable,'-I','-S','-B',str(Path(runner.__file__)), '--root',str(root)]
            done=subprocess.run(command+['--report',str(full)],capture_output=True,timeout=20)
            self.assertEqual(done.returncode,0,done.stderr)
            lanes=[{'id':'core','kind':'unittest','root':'live','sources':['live/test_one.py','live/test_two.py','live/test_empty.py']}]
            parts=[]
            for group in parallel.groups(lanes,2):
                for _,lane in group:
                    modules=Path(tmp)/('modules'+str(lane['_shard_index'])+'.json');write(modules,lane['_module_sources'])
                    report=modules.with_suffix('.report.json')
                    done=subprocess.run(command+['--report',str(report),'--modules-file',str(modules),
                        '--shard-count','2','--shard-index',str(lane['_shard_index'])],capture_output=True,timeout=20)
                    self.assertEqual(done.returncode,0,done.stderr)
                    parts.append((lane,{'status':'passed','exit':0,'observation':read(report)}))
            aggregated=parallel.aggregate(lanes[0],parts)
            self.assertEqual(aggregated['status'],'passed')
            self.assertEqual(Counter(read(full)['collected']),Counter(aggregated['observation']['collected']))
            parts[0][1]['observation']['module_sources']=[]
            self.assertEqual(parallel.aggregate(lanes[0],parts)['status'],'failed')

    def test_package_hooks_keep_full_traversal_but_docstrings_allow_partition(self):
        files={'live/core/__init__.py':{},'live/test_one.py':{}}
        self.assertFalse(parallel.package_discovery(files,lambda _:b'"""inert package"""'))
        self.assertTrue(parallel.package_discovery(files,lambda _:b'def load_tests(loader,tests,pattern):return tests'))
        lane={'id':'core','kind':'unittest','root':'live','sources':['live/test_one.py'],'_legacy_discovery':True}
        self.assertTrue(all('_module_sources' not in part for group in parallel.groups([lane],2) for _,part in group))

    def test_one_slot_keeps_selected_core_modules(self):
        group=parallel.groups([{'id':'core','kind':'unittest','root':'live','sources':['live/test_selected.py']}],1)
        self.assertEqual(group[0][0][1]['_module_sources'],['live/test_selected.py'])


@unittest.skipUnless(os.name=='nt','Windows ACL consumer')
class Readonly(unittest.TestCase):
    def test_native_child_cannot_mutate_shared_inputs_and_cleanup_restores(self):
        with tempfile.TemporaryDirectory(prefix='helene-update-tests-') as tmp:
            work=Path(tmp);root=work/'shared-inputs';(root/'sub').mkdir(parents=True)
            path=root/'sub/value';path.write_text('original');cache=work/'cache';cache.mkdir()
            try:
                readonly.protect(root,cache)
                code="""import sys,json
from pathlib import Path
r=Path(sys.argv[1]);p=r/'sub/value';rows={}
for name,fn in [('write',lambda:p.write_text('bad')),('new',lambda:(r/'sub/new').touch()),('delete',p.unlink),('rename',lambda:p.rename(r/'moved')),('mkdir',lambda:(r/'newdir').mkdir())]:
 try:fn();rows[name]=False
 except PermissionError:rows[name]=True
print(json.dumps(rows))
"""
                done=subprocess.run([sys.executable,'-I','-c',code,str(root)],capture_output=True,text=True,timeout=20)
                self.assertEqual(done.returncode,0,done.stderr);self.assertTrue(all(json.loads(done.stdout).values()))
                self.assertEqual(path.read_text(),'original')
            finally:readonly.release(work,cache)
            path.write_text('restored');self.assertFalse((cache/'readonly-inputs.json').exists())


class Cleanup(unittest.TestCase):
    def fixture(self):
        import t_source_update_battery_2810 as fixtures
        value=fixtures.Battery();value.setUp();self.addCleanup(value.doCleanups)
        return value

    def test_late_stop_and_binding_failure_retire_settled_stands(self):
        from localharness import source_update_battery as battery
        for failure in ('stop','binding'):
            fixture=self.fixture();work=[];completed=[False]
            def execute(*args,**kwargs):
                work.append(Path(args[4]));completed[0]=True
                return [],[],[]
            stopped=lambda: failure=='stop' and completed[0]
            original=battery._binding;calls=[0]
            def binding(*args):
                calls[0]+=1
                if calls[0]>1 and failure=='binding':raise ValueError('late binding drift')
                return original(*args)
            with mock.patch.object(parallel,'run',side_effect=execute),mock.patch.object(battery,'_binding',side_effect=binding):
                with self.assertRaises((InterruptedError,ValueError)):
                    battery.prepare(fixture.operation,fixture.directory,{},stopped)
            self.assertTrue(work);self.assertFalse(work[0].exists())

    def test_attempt_publication_failure_does_not_leak_unrecorded_work(self):
        from localharness import source_update_battery as battery
        fixture=self.fixture();original=battery.write;work=[]
        def publish(path,value):
            if Path(path).name=='attempt.json':
                work.append(Path(value['work']));raise OSError('fixture disk full')
            return original(path,value)
        with mock.patch.object(battery,'write',side_effect=publish),self.assertRaises(OSError):
            battery.prepare(fixture.operation,fixture.directory,{},lambda:False)
        self.assertTrue(work);self.assertFalse(work[0].exists())


class Protocol(unittest.TestCase):
    def test_legacy_bundle_refuses_before_execution(self):
        from localharness import source_verification_bundle as bundle
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write(root/'manifest.json',{'format':'helene-verification-bundle.v1','version':'rc'})
            with self.assertRaisesRegex(ValueError,'No files applied'):
                bundle.load(root,bundle.digest(root/'manifest.json'),'rc')

    def test_actual_selected_prepare_is_not_full_pass_and_tampering_refuses(self):
        import t_source_update_battery_2810 as fixtures
        from localharness import source_update_battery as battery
        fixture=fixtures.Battery();fixture.setUp();self.addCleanup(fixture.doCleanups)
        sides={side:battery.bundle.inventory(fixture.core) for side in ('base','local','incoming')}
        app=battery.bundle.inventory(fixture.app)
        write(fixture.directory/'composition.json',{'base_app':app,
            'app_source':{'binding':{side:app for side in ('base','local','incoming')}}})
        with mock.patch.object(battery.merge,'validate_inputs',return_value={'files':sides}), \
                mock.patch.object(selection,'FLOOR',frozenset({'live/test_first.py','desk/tests/t_one.py'})):
            cache,report,verified=fixture.green_fixture()
            self.assertEqual(report['selection']['tier'],'selected')
            self.assertFalse(report['full_pass']);self.assertTrue(report['selected_pass'])
            self.assertTrue(verified['selected_pass']);self.assertFalse(verified['full_pass'])
            self.assertIn('desk/tests/test_two.py',report['selection']['omitted_lanes'])
            # Authentication covers selection as well as results.
            receipt=read(cache/'receipt.json');report['selection']['omitted_lanes']=[]
            write(cache/'report.json',report)
            with self.assertRaises(ValueError):battery.validate(fixture.operation,fixture.directory)


if __name__=='__main__':unittest.main()
