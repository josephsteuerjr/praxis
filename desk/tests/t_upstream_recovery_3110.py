"""Pristine-v2 restore and owner/common-handoff boundaries, no product approval.

The stage uses real sealed A archives. Handoff tests spy only the boundary;
they never manufacture canonical review, health or full-battery receipts.
"""
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import authored_baseline,code_transition,upstream_recovery as recovery
from localharness import python_updates,program_state,update_process,python_platform
from localharness.package_notice import read,write


class ModernRecovery(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='restore-pristine-private-')
        self.addCleanup(self.temporary.cleanup);self.base=Path(self.temporary.name)
        self.root=self.base/'Helene';self.data=self.base/'profile/Helene/data'
        self.put(self.data/'memory/history.jsonl',b'owner conversation exact bytes\n')
        self.put(self.data/'memory/.control/autonomy-paused.json',b'{"paused":true}\n')
        self.factory={'python_app.py':b'FACTORY_APP = True\n','helene_setup.py':b'# factory helper\n',
            'localharness/feature.py':b'VALUE = "factory"\n','resources/soul/seed.md':b'factory instructions\n',
            'localharness/python_source_updater.py':b'# inert handoff boundary fixture, never execute\n'}
        for name,raw in self.factory.items():self.put(self.root/'app'/name,raw)
        self.put(self.root/'tree/agent.py',b'FACTORY_TREE = True\n')
        self.runtime='runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3'
        self.put(self.root/self.runtime,b'inert runtime bytes, never execute this fixture\n')
        write(self.root/'python-app.json',{'format':'helene-python-app.v1','delivery':'portable','version':'fixture',
                                          'target':python_platform.runtime_target()})
        write(self.root/'helene.json',{'mode':'local','tree':str(self.data),'code':'tree','autonomy':{'paused':True}})
        code_transition.seal(self.root);authored_baseline.seal(self.root)
        write(self.root/'source-inputs.json',{'files':{name:self.sha(self.root/'app'/name) for name in self.factory}})
        passport={p.relative_to(self.root).as_posix():self.sha(p) for p in self.root.rglob('*')
                  if p.is_file() and p.relative_to(self.root).parts[0]!='tree' and p.name!='helene.json'}
        passport[self.runtime]={'sha256':passport[self.runtime],'size':(self.root/self.runtime).stat().st_size}
        write(self.root/'python-package-files.json',{'files':passport})
        self.put(self.root/'app/python_app.py',b'AUTHORED_APP = True\n')
        self.put(self.root/'app/localharness/feature.py',b'VALUE = "authored"\n')
        (self.root/'app/resources/soul/seed.md').unlink()
        self.put(self.root/'app/localharness/added.py',b'AUTHORED_ADDITION = True\n')
        self.put(self.root/'tree/agent.py',b'AUTHORED_TREE = True\n')
        self.owner=SimpleNamespace(root=self.root,event=Mock(),exiting=False,stop=threading.Event())
        self.target=os.path.normcase(str(self.root.resolve()))
        self.addCleanup(python_updates._update_targets.discard,self.target)
        self.enterContext(patch('localharness.owner_stop.stopped',return_value=False))
        self.enterContext(patch('localharness.session_exit.requested',return_value=False))

    def put(self,path,raw):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    def sha(self,path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    def snapshot(self,path):return {p.relative_to(path).as_posix():p.read_bytes() for p in Path(path).rglob('*') if p.is_file()}
    def outside_stage(self,root,identity):return Path(root).parent/('.helene-update-'+identity)

    def test_whole_app_and_tree_are_exact_A_not_live_M_and_data_remains(self):
        old=self.snapshot(self.root);data=self.snapshot(self.data)
        new=recovery.stage(self.root,'a'*32)
        self.assertEqual(self.snapshot(new/'app'),self.factory)
        self.assertEqual((new/'tree/agent.py').read_bytes(),b'FACTORY_TREE = True\n')
        self.assertFalse((new/'app/localharness/added.py').exists())
        self.assertEqual((new/'python-package-files.json').read_bytes(),old['python-package-files.json'])
        for name in ('code-upstream.zip','code-upstream.json','app-upstream.zip','app-upstream.json','source-update-baseline.json'):
            self.assertEqual((new/name).read_bytes(),old[name])
        authored_baseline.validate(new)
        for name in self.factory:
            previous=self.root/'app'/name;restored=new/'app'/name
            if previous.exists():self.assertFalse(os.path.samefile(previous,restored))
        self.assertEqual(self.snapshot(self.data),data)
        for name,raw in old.items():self.assertEqual((self.root/name).read_bytes(),raw)
        self.assertFalse((new/'helene.json').exists());self.assertFalse((new/'data').exists())

    def test_nonapp_runtime_tamper_refuses_and_removes_only_own_failed_stage(self):
        self.put(self.root/self.runtime,b'changed runtime')
        data=self.snapshot(self.data)
        with self.assertRaisesRegex(ValueError,'Изменён файл'):recovery.stage(self.root,'b'*32)
        self.assertFalse(program_state.staging(self.root,'recovery-'+('b'*32)).exists())
        self.assertEqual(self.snapshot(self.data),data)

    def test_tampered_baseline_refuses_before_creating_stage(self):
        self.put(self.root/'app-upstream.zip',b'changed archive')
        with self.assertRaises(ValueError):recovery.stage(self.root,'c'*32)
        self.assertFalse(program_state.staging(self.root,'recovery-'+('c'*32)).exists())

    def test_owned_restore_delegates_clean_common_pipeline_and_shares_duplicate_guard(self):
        seen=[]
        def handoff(owner,destination,verified,progress,force,sourceMode=None):
            seen.append(destination)
            self.assertIs(owner,self.owner);self.assertEqual(sourceMode,'clean');self.assertFalse(force)
            self.assertEqual(verified,{})
            self.assertFalse(destination.is_relative_to(self.root))
            self.assertEqual(self.snapshot(destination/'Helene/app'),self.factory)
            return {'scope':'handoff boundary spy only'}
        with patch.object(recovery,'modern',return_value=True),patch.object(program_state,'staging',side_effect=self.outside_stage), \
             patch.object(python_updates,'_prepare_handoff',side_effect=handoff) as common:
            result=recovery.restore_owned(self.owner,operation='d'*32)
            self.assertEqual(result['scope'],'handoff boundary spy only')
            with self.assertRaisesRegex(RuntimeError,'already preparing'):recovery.restore_owned(self.owner)
            common.assert_called_once()
        self.assertEqual(len(seen),1)

    def test_failed_common_preparation_keeps_old_app_and_data_and_clears_guard(self):
        old=self.snapshot(self.root);data=self.snapshot(self.data)
        with patch.object(recovery,'modern',return_value=True),patch.object(program_state,'staging',side_effect=self.outside_stage), \
             patch.object(python_updates,'_prepare_handoff',side_effect=ValueError('real preparation boundary refused')):
            with self.assertRaisesRegex(ValueError,'boundary refused'):recovery.restore_owned(self.owner,operation='e'*32)
        self.assertNotIn(self.target,python_updates._update_targets)
        self.assertFalse(self.outside_stage(self.root,'recovery-'+('e'*32)).exists())
        self.assertEqual(self.snapshot(self.data),data)
        for name,raw in old.items():self.assertEqual((self.root/name).read_bytes(),raw)

    def test_common_clean_handoff_uses_new_runtime_and_waits_admission_before_exit(self):
        from localharness import python_source_updater
        data=self.snapshot(self.data);commands=[];prepared=[];order=[]
        child=SimpleNamespace(pid=9123,poll=lambda:None)
        timer=Mock()
        def prepare(old,new,**kwargs):
            prepared.append((old,new));self.assertEqual(self.snapshot(new/'app'),self.factory)
            self.assertEqual(read(new/'.helene/source-choice.json').get('mode'),'clean')
        def launch(command,**kwargs):
            commands.append((command,kwargs));order.append('launch');return child
        def admission(process,destination,target,**kwargs):
            self.assertIs(process,child);self.assertEqual(target,self.root)
            self.assertTrue(kwargs['receipt_name'].startswith('updater-admitted-'))
            order.append('await-bound-admission');return {'boundary_spy':True}
        def clock(seconds,callback):
            self.assertEqual(seconds,2);self.assertEqual(order,['launch','await-bound-admission'])
            order.append('schedule-old-exit');return timer
        with patch.object(recovery,'modern',return_value=True),patch.object(program_state,'staging',side_effect=self.outside_stage), \
             patch('python_migrate.installation_lock',return_value=nullcontext()), \
             patch.object(python_source_updater,'prepare_install',side_effect=prepare), \
             patch.object(python_updates.subprocess,'Popen',side_effect=launch), \
             patch.object(python_updates,'await_admission',side_effect=admission), \
             patch.object(python_updates.threading,'Timer',side_effect=clock):
            result=recovery.restore_owned(self.owner,operation='f'*32)
        self.assertEqual(len(prepared),1);self.assertEqual(len(commands),1)
        command,options=commands[0];new=prepared[0][1]
        self.assertEqual(Path(command[0]),update_process.interpreter_for(new))
        self.assertFalse(Path(command[0]).is_relative_to(self.root))
        self.assertEqual(Path(command[1]),new.parent/'python_source_updater.py')
        self.assertEqual(options['cwd'],new.parent);timer.start.assert_called_once()
        self.assertEqual(result['updater_pid'],child.pid);self.assertEqual(self.snapshot(self.data),data)

    def test_clean_pause_preparation_does_not_resume_agent_but_stop_and_exit_refuse(self):
        marker=self.data/'memory/.control/autonomy-paused.json';before=marker.read_bytes()
        self.owner.update_job={'tree':str(self.data),'source_mode':'preserve'}
        with patch('localharness.desktop_update_request.status',return_value={'state':'paused'}) as status:
            python_updates._wait_pause(self.owner,source_mode='clean');status.assert_not_called()
        self.assertEqual(marker.read_bytes(),before)
        self.owner.exiting=True
        with self.assertRaises(InterruptedError):python_updates._wait_pause(self.owner,source_mode='clean')
        self.owner.exiting=False;self.owner.stop.set()
        with self.assertRaises(InterruptedError):python_updates._wait_pause(self.owner,source_mode='clean')
        self.assertEqual(marker.read_bytes(),before)

    def test_modern_legacy_entry_refuses_old_runtime_worker(self):
        with patch.object(recovery,'modern',return_value=True),patch.object(recovery.subprocess,'Popen') as launch, \
             patch('localharness.python_source_updater.perform') as perform:
            with self.assertRaisesRegex(ValueError,'владельца сеанса'):recovery.launch(self.root)
            with self.assertRaisesRegex(ValueError,'владельца сеанса'):recovery.restore(self.root)
            launch.assert_not_called();perform.assert_not_called()

    def test_application_and_desktop_routes_require_confirmation_and_owner(self):
        import python_app
        from localharness import python_desktop_commands
        app=python_app.Application.__new__(python_app.Application);app.root=self.root
        app.host=SimpleNamespace(invoke=Mock(return_value={'owner':'authenticated session'}))
        with patch.object(recovery,'modern',return_value=True),patch.object(recovery,'launch') as legacy:
            with self.assertRaises(ValueError):app.invoke('restore_upstream',{})
            self.assertEqual(app.invoke('restore_upstream',{'confirmed':True})['owner'],'authenticated session')
            app.host.invoke.assert_called_once_with('restore_upstream',{'confirmed':True});legacy.assert_not_called()
        self.owner.current=Mock(side_effect=AssertionError('Restore must not read mutable agent configuration'))
        with patch.object(recovery,'modern',return_value=True), \
             patch('localharness.desktop_update_request.admit_restore',return_value={'boundary':'owner-job'}) as restore:
            with self.assertRaises(ValueError):python_desktop_commands.invoke(self.owner,'restore_upstream',{})
            self.assertEqual(python_desktop_commands.invoke(self.owner,'restore_upstream',{'confirmed':True}),{'boundary':'owner-job'})
            restore.assert_called_once_with(self.owner,confirmed=True)


if __name__=='__main__':unittest.main()
