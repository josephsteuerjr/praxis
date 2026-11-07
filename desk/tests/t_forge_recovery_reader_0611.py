"""The actual desktop reader validates reboot proofs without starting Forge."""
from contextlib import nullcontext
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'desk'))
from deskd import forge_reader as reader

spec = importlib.util.spec_from_file_location('reader_fixture_recovery',ROOT/'praxis/forge_recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveredReader(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.task = self.base/'memory/.forge/tasks/code-reader-recovery'
        self.unit = self.task/'agents/agent-old'
        self.unit.mkdir(parents=True)
        boot = int(dt.datetime.now(dt.timezone.utc).timestamp())-600
        old = dt.datetime.fromtimestamp(boot-3600,dt.timezone.utc).isoformat()
        self.put(self.task/'task.json',{'id':self.task.name,'status':'active','root':str(self.base/'workspace'),'scope':'self'})
        self.put(self.unit/'request.json',{'id':self.unit.name,'task_id':self.task.name,
            'root':str(self.base/'workspace'),'scope':'self','role':'worker','status':'running',
            'supervisor_pid':111,'supervisor_started_at':'old-birth','created':old})
        self.put(self.unit/'stop.json',{'state':'unresolved','note':'historical stop'})
        (self.task/'events.jsonl').write_text(json.dumps({'at':old,'kind':'agent_spawned','agent_id':self.unit.name})+'\n',encoding='utf-8')
        original = Path.read_text
        kernel = {'/proc/sys/kernel/random/boot_id':'fixture-boot',
                  '/proc/stat':'btime '+str(boot)+'\n',
                  '/sys/class/dmi/id/product_uuid':'fixture-host'}
        def read_text(path,*args,**kwargs):
            name = str(path).replace('\\','/')
            return kernel[name] if name in kernel else original(path,*args,**kwargs)
        patch = mock.patch.object(Path,'read_text',read_text)
        patch.start();self.addCleanup(patch.stop)
        patch = mock.patch.object(sys,'platform','linux')
        patch.start();self.addCleanup(patch.stop)

    def put(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value),encoding='utf-8')

    def prove(self):
        owner = SimpleNamespace(
            _unit_dir=lambda *args:self.unit,
            _mutation_lock=lambda *args:nullcontext(),
            _pid_alive=lambda *args:False,
            _atomic_json=self.put,
            _event=mock.Mock(),
            get=lambda *args:json.loads((self.task/'task.json').read_text(encoding='utf-8')))
        return recovery.reconcile_unit(owner,self.task.name,'agents',self.unit.name,
            actor='fixture-owner',reason='verified execution host reboot',
            host_binding='verified-local-execution-host')

    def row(self):
        return reader.task_detail(self.base,self.task.name)['agents'][0]

    def snapshot(self):
        return {str(path.relative_to(self.base)):path.read_bytes()
                for path in self.base.rglob('*') if path.is_file()}

    def test_valid_proof_releases_derived_pending_stop_and_retains_history(self):
        self.assertEqual(self.row()['status'],'stop_pending')
        self.prove()
        before = self.snapshot()
        had_forge = 'forge' in sys.modules
        row = self.row()
        self.assertEqual(row['status'],'lost')
        self.assertEqual(row['custody'],'recovered_boundary')
        self.assertTrue(row['custody_recovered'])
        self.assertEqual(row['stop']['state'],'unresolved')
        self.assertEqual(self.snapshot(),before)
        self.assertEqual('forge' in sys.modules,had_forge)

    def test_historical_failure_remains_failed(self):
        self.put(self.unit/'result.json',{'status':'failed','result':'fixture failure'})
        self.prove()
        before = self.snapshot()
        row = self.row()
        self.assertEqual(row['status'],'failed')
        self.assertTrue(row['custody_recovered'])
        self.assertEqual(self.snapshot(),before)

    def test_changed_subject_invalidates_recovery(self):
        self.prove()
        # Prime the reusable validator; its verdict must still use fresh inputs.
        self.assertTrue(self.row()['custody_recovered'])
        path = self.unit/'request.json'
        request = json.loads(path.read_text(encoding='utf-8'))
        self.put(path,dict(request,supervisor_pid=222))
        row = self.row()
        self.assertEqual(row['status'],'stop_pending')
        self.assertFalse(row.get('custody_recovered'))

    def test_persisted_flag_and_malformed_proof_do_not_release_custody(self):
        self.put(self.unit/'result.json',{'status':'lost','custody_recovered':True})
        (self.unit/recovery.FILE).write_text('{malformed',encoding='utf-8')
        row = self.row()
        self.assertEqual(row['status'],'stop_pending')
        self.assertFalse(row.get('custody_recovered'))
        self.assertEqual(len(reader.activity(self.base)['active']),1)

    def test_recovered_terminal_outcomes_leave_active_activity(self):
        for status in ('timed_out','stalled','passed','passed_with_skips'):
            with self.subTest(status=status):
                (self.unit/recovery.FILE).unlink(missing_ok=True)
                self.put(self.unit/'result.json',{'status':status})
                self.prove()
                before = self.snapshot()
                activity = reader.activity(self.base)
                self.assertEqual(activity['active'],[])
                self.assertEqual(activity['recent'][0]['agents'][0]['status'],status)
                self.assertEqual(self.snapshot(),before)

    def test_recovered_parent_does_not_hide_an_unrecovered_child(self):
        self.prove()
        child = self.task/'agents/agent-child'
        request = json.loads((self.unit/'request.json').read_text(encoding='utf-8'))
        self.put(child/'request.json',dict(request,id=child.name,parent_unit_id=self.unit.name))
        self.put(child/'stop.json',{'state':'unresolved'})
        row = reader.activity(self.base)['active'][0]
        states = {unit['id']:unit['status'] for unit in row['agents']}
        self.assertEqual(states,{self.unit.name:'lost',child.name:'stop_pending'})

    def test_installed_stock_tree_path_uses_the_same_validator(self):
        self.prove()
        stock = self.base/'programme/tree/forge_recovery.py'
        stock.parent.mkdir(parents=True)
        stock.write_bytes((ROOT/'praxis/forge_recovery.py').read_bytes())
        with mock.patch.object(reader,'__file__',str(self.base/'programme/app/deskd/forge_reader.py')):
            self.assertTrue(self.row()['custody_recovered'])
            stock.write_text('def read_recovery(folder): return None\n',encoding='utf-8')
            self.assertEqual(self.row()['status'],'stop_pending')
            stock.unlink()
            self.assertEqual(self.row()['status'],'stop_pending')


if __name__=='__main__':
    unittest.main()
