"""Real owned children: restart remains usable before the inbox reader exists."""
from pathlib import Path
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
from localharness import desktop_supervisor as supervisor, runner_startup, owner_stop
from localharness.desktop_control import Control
from localharness.package_notice import read, write
from deskd import control


def wait_for(predicate, timeout=90):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.03)
    raise AssertionError('Required observed state did not arrive')


class Bootstrap(unittest.TestCase):
    def test_status_export_denial_does_not_stop_children_or_repeat_a_request(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp);write(tree/'helene.json',{'lang':'en'})
            spec=supervisor.Spec('runner',(),tree,{},tree/'runner.log',0)
            policy=supervisor.Coordinator({'main:runner':'runner'})
            active={'main:runner':SimpleNamespace(pid=123)}
            stop=Mock();service=Control(stop,supervisor.ChildState)
            request={'id':'same-owner-request','action':'restart','target':'runner'}
            with patch.object(control,'beat',side_effect=PermissionError('sharing denial')), patch.object(control,'take_request',return_value=request), patch.object(control,'receipt'):
                self.assertTrue(service.tick({'main':(tree,{'main:runner':spec})},active,policy,lambda:False))
                active['main:runner']=SimpleNamespace(pid=124)
                self.assertFalse(service.tick({'main':(tree,{'main:runner':spec})},active,policy,lambda:False))
                stop.assert_called_once()
                self.assertEqual(active['main:runner'].pid,124)

    def test_reader_is_the_only_ready_proof_and_must_match_owned_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            write(tree/'memory/.state/runner-startup.json', {'pid': 123, 'phase': 'sandbox', 'at': time.time()-180})
            reader = tree/'memory/.control/desk_inbox/.reader.json'
            write(reader, {'pid': 122, 'at': time.time()})
            self.assertEqual(runner_startup.observe(tree, 123)['state'], 'starting')
            self.assertGreaterEqual(runner_startup.observe(tree, 123)['elapsed'], 180)
            write(reader, {'pid': 123, 'at': time.time()+60})
            self.assertEqual(runner_startup.observe(tree, 123)['state'], 'starting')
            write(reader, {'pid': 123, 'at': time.time()})
            self.assertEqual(runner_startup.observe(tree, 123)['state'], 'ready')
            write(reader, {'pid': 123, 'at': time.time()-60})
            self.assertNotEqual(runner_startup.observe(tree, 123)['state'], 'ready')
            self.assertEqual(runner_startup.observe(tree, 124)['phase'], 'launch')

    def test_cleanup_fault_keeps_handle_and_never_reports_restart_success(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp); write(tree/'helene.json', {'lang':'en'})
            spec = supervisor.Spec('runner', (), tree, {'HELENE_CONFIG':str(tree/'helene.json')}, tree/'runner.log', 0)
            policy = supervisor.Coordinator({'main:runner':'runner'})
            active = {'main:runner':SimpleNamespace(pid=123)}
            def fail(_): raise RuntimeError('owned handle remains uncertain')
            service = Control(fail, supervisor.ChildState)
            service.tick({'main':(tree, {'main:runner':spec})}, active, policy, lambda:False)
            self.assertTrue(control.ask(tree, 'runner')['ok'])
            with self.assertRaisesRegex(RuntimeError, 'uncertain'):
                service.tick({'main':(tree, {'main:runner':spec})}, active, policy, lambda:False)
            self.assertIn('main:runner', active)
            self.assertFalse(control.supervisor_state(tree)['receipt']['done'])

    def test_real_owner_restarts_blocked_startup_and_preserves_other_agent(self):
        with tempfile.TemporaryDirectory(prefix='supervisor-boot-') as tmp:
            root = Path(tmp); trees = {name:root/name for name in ('main','peer')}
            for tree in trees.values():
                tree.mkdir(); write(tree/'helene.json', {'lang':'en'})
            script = root/'blocked.py'
            script.write_text('''import sys,os,time,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from localharness import runner_startup
from localharness.package_notice import write
tree=Path(sys.argv[2])
runner_startup.phase(tree,'sandbox')
while not (tree/'release').exists():time.sleep(.03)
runner_startup.phase(tree,'ready')
while True:
 write(tree/'memory/.control/desk_inbox/.reader.json',{'pid':os.getpid(),'at':time.time()})
 time.sleep(.1)
''', encoding='utf-8')
            def plans(*args, **kwargs):
                return {name:(tree,{f'{name}:runner':supervisor.Spec('runner',
                    (sys.executable,'-B',str(script),str(DESK),str(tree)),root,
                    dict(os.environ,HELENE_TREE=str(tree),HELENE_CONFIG=str(tree/'helene.json')),
                    tree/'runner.log',0)}) for name,tree in trees.items()}
            stop = threading.Event(); errors = []; seen = []
            def run():
                try: supervisor.run(trees['main']/'helene.json', stop=stop,
                    desktop_roster=True, status_nonce='b'*32, status_observer=seen.append)
                except BaseException as error: errors.append(error)
            with patch.object(supervisor,'_plans',side_effect=plans), patch.object(supervisor,'_port_busy',return_value=False), patch.object(owner_stop,'stopped',return_value=False):
                thread=threading.Thread(target=run);thread.start()
                try:
                    def starting(name):
                        rows=control.supervisor_state(trees[name])['children']
                        return rows[0] if rows and rows[0].get('phase')=='sandbox' else None
                    first=wait_for(lambda:starting('main')); peer=wait_for(lambda:starting('peer'))
                    self.assertEqual(first['state'],'starting')
                    self.assertFalse((trees['main']/'memory/.control/desk_inbox/.reader.json').exists())
                    request=control.ask(trees['main'],'runner');self.assertTrue(request['ok'])
                    second=wait_for(lambda:(row if (row:=starting('main')) and row['pid']!=first['pid'] else None))
                    receipt=control.supervisor_state(trees['main'])['receipt']
                    self.assertEqual(receipt['id'],request['request']['id']);self.assertTrue(receipt['done'])
                    self.assertIn('not yet confirmed',receipt['note'])
                    self.assertEqual(starting('peer')['pid'],peer['pid'])
                    self.assertEqual(second['state'],'starting')
                    (trees['main']/'release').touch()
                    wait_for(lambda:runner_startup.observe(trees['main'],second['pid'])['state']=='ready')
                    wait_for(lambda:seen and seen[-1]['agents']['main']['startup']['state']=='ready')
                finally:
                    stop.set();thread.join(75)
                self.assertFalse(thread.is_alive());self.assertEqual(errors,[])


if __name__ == '__main__': unittest.main()
