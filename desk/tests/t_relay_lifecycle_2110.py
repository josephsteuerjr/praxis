"""Deterministic supervisor-loop/login races; no installed children or credentials."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from queue import Queue
import sys
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.platform == 'win32':
    from localharness import desktop_supervisor as supervisor
    from localharness import desktop_process, owner_stop, python_relay


@unittest.skipUnless(sys.platform == 'win32', 'Windows supervisor')
class LifecycleTests(unittest.TestCase):
    def test_proxy_refresh_keeps_nonrelay_jobs_for_base_and_neighbor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=root/'helene.json';config.write_text('{}')
            trees={name:root/name for name in ('main','neighbor')}
            for tree in trees.values():tree.mkdir()
            def plan(generation):
                result={}
                for name,tree in trees.items():
                    specs={f'{name}:{role}':supervisor.Spec(role,(f'{name}:{role}',),tree,
                        {'proxy':str(generation)} if role=='relay' else {},tree/'log',0)
                        for role in ('channel','runner','relay','xtts')}
                    result[name]=(tree,specs)
                return result
            old,new=plan(1),plan(2);commands=Queue();spawned=[];holds=[];cleaned=[]
            class Child:
                def __init__(self,argv):self.role=argv[0];self.stops=0;self.pid=len(spawned)+1
                def poll(self):return None
                def stop(self):self.stops+=1
            def spawn(argv,**kwargs):
                child=Child(argv);spawned.append(child);return child
            @contextmanager
            def hold(tree):
                holds.append(tree)
                yield lambda:cleaned.append(tree)
            outer=self
            class Pump(Event):
                waits=0
                def wait(self,timeout=None):
                    self.waits+=1
                    if self.waits==1:commands.put('refresh')
                    elif self.waits==2:
                        outer.assertEqual(len(spawned),10)
                        outer.assertEqual(len(holds),2)
                        outer.assertEqual(cleaned,[])
                        for child in spawned[:8]:
                            outer.assertEqual(child.stops,1 if child.role.endswith(':relay') else 0)
                        self.set()
            stop=Pump()
            with patch.object(supervisor,'_plans',side_effect=[old,old,new]), \
                    patch.object(supervisor,'_tree_hold',side_effect=hold), \
                    patch.object(supervisor,'_port_busy',return_value=False), \
                    patch.object(supervisor,'_relay_login_generation',return_value=None), \
                    patch.object(supervisor,'_desktop_status'), \
                    patch.object(owner_stop,'stopped',return_value=False), \
                    patch.object(supervisor.JobChild,'spawn',side_effect=spawn):
                self.assertEqual(supervisor.run(config,stop=stop,desktop_roster=True,
                    status_nonce='a'*32,commands=commands),0)
            self.assertEqual(len(cleaned),2)
            self.assertTrue(all(child.stops==1 for child in spawned))

    def test_login_uses_existing_relay_owner_without_spawning_a_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=root/'helene.json';config.write_text('{}')
            home=root/'relay';auth=home/'local_auth/accounts/secondary/auth.json'
            owner=SimpleNamespace(root=root,current=lambda:SimpleNamespace(tree=root,config=config),
                                  stop=Event(),helpers=[])
            child=SimpleNamespace(handle=123,poll=lambda:0)
            def spawn(*args,**kwargs):
                auth.parent.mkdir(parents=True,exist_ok=True)
                auth.write_text('{"tokens":{"access_token":"synthetic-only"}}')
                return child
            with patch.object(desktop_process.JobChild,'spawn') as spawn_child,patch.object(python_relay,'control',return_value={'login':{'pending':False},'accounts':[{'slot':'secondary','configured':True}]}) as control:
                python_relay.invoke(owner,'relay_login',{'slot':'secondary'})
                self.assertEqual(python_relay.invoke(owner,'relay_status',{'slot':'secondary'}),'authorized')
                self.assertEqual(control.call_args_list[0].args[1],'login')
                self.assertEqual(control.call_args_list[0].kwargs,{'slot':'secondary'})
                spawn_child.assert_not_called()


if __name__=='__main__':unittest.main()
