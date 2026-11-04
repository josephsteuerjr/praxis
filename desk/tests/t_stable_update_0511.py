"""Causal portable-update regressions against disposable source/data/processes."""
from pathlib import Path
from types import SimpleNamespace
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
               str(Path(__file__).resolve().parents[2] / 'helene/core'),
               str(Path(__file__).resolve().parent)]
from localharness import source_update_battery as battery, source_verification_bundle as bundle
from localharness import update_overlay, update_composition as composition, update_source
from localharness.package_notice import write


class AppRemoval(unittest.TestCase):
    def test_preserve_removes_absent_module_and_rollback_restores_mode(self):
        from t_update_contract_230 import CompositionTests, put
        f = CompositionTests('runTest'); f.setUp(); self.addCleanup(f.doCleanups)
        obsolete = put(f.old, 'app/localharness/obsolete.py', 'OLD = True\n')
        obsolete.chmod(0o755)
        obsolete_resource=put(f.old, 'app/resources/soul/obsolete.txt', 'old factory resource')
        obsolete_lock=put(f.old, 'app/resources/obsolete.lock', 'old bundled lock')
        browser_cache=put(f.old, 'app/.migration-webview/current/cache', 'live browser cache')
        put(f.old, 'app/localharness/feature.py', 'answer=0\n')
        owner = 'app/localharness/owner.py'
        composition.repair(f.directory, owner, 'AUTHORED = True\n',
                           expected=composition.signature(f.directory), reason='Retain authored module')
        write(f.program/'code-transition-plan.json', {'plans':[{'directory':str(f.directory)}]})
        composition.overlay(f.program, [{'directory':str(f.directory)}])
        write(update_source.choice_path(f.program), {'mode':'preserve', 'old':str(f.old.resolve()), 'new':str(f.program.resolve())})
        private = put(f.old, 'app/private-data/memory.txt', 'PRIVATE')
        discovered = {'agents':[{'tree':str(private.parent), 'value':{'mode':'local'}}]}
        recovery = f.root/'removed-module-recovery'
        with patch('localharness.app_migration.discover', return_value=discovered):
            update_overlay.prepare(f.program, f.old, recovery)
            update_overlay.apply(f.program, f.old, recovery)
        self.assertFalse(obsolete.exists(), 'A module absent from accepted M remains importable')
        self.assertFalse(obsolete_resource.exists())
        self.assertFalse(obsolete_lock.exists())
        self.assertEqual(browser_cache.read_text(), 'live browser cache')
        self.assertEqual((f.old/owner).read_text(), 'AUTHORED = True\n')
        self.assertEqual(private.read_text(), 'PRIVATE')
        from localharness.python_source_updater import rollback
        rollback(f.old, recovery)
        self.assertEqual(obsolete.read_text(), 'OLD = True\n')
        self.assertEqual(obsolete_resource.read_text(), 'old factory resource')
        self.assertEqual(obsolete_lock.read_text(), 'old bundled lock')
        if os.name != 'nt': self.assertEqual(obsolete.stat().st_mode & 0o777, 0o755)
        self.assertFalse((f.old/owner).exists())
        self.assertEqual(private.read_text(), 'PRIVATE')


class CompositionMode(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX executable mode consumer')
    def test_mapped_app_inventory_and_execution_match_accepted_modes(self):
        with tempfile.TemporaryDirectory(prefix='helene-mapped-mode-') as folder:
            root=Path(folder); repo=root/'repo'; core=root/'core'; app=root/'app'
            for p in (repo/'live', repo/'praxis', repo/'helene/core', repo/'desk', core, app):
                p.mkdir(parents=True, exist_ok=True)
            (core/'logic.py').write_text('VALUE=1\n')
            helper=app/'resources/helper'; helper.parent.mkdir()
            helper.write_text('#!/bin/sh\nprintf mapped-executable\n'); helper.chmod(0o755)
            regular=app/'resources/regular'; regular.write_text('plain'); regular.chmod(0o644)
            value={k:{'root':str(p),'files':bundle.inventory(p)} for k,p in [('core',core),('app',app)]}
            _core,desk=battery._copy_composition(value,repo,{'files':{}})
            actual=bundle.inventory(desk)
            self.assertEqual(actual, value['app']['files'])
            self.assertEqual(subprocess.check_output([str(desk/'resources/helper')]).decode(), 'mapped-executable')


@unittest.skipIf(os.name == 'nt', 'Native POSIX guardian and flock consumer')
class NativeCustody(unittest.TestCase):
    def test_publisher_started_after_two_local_leases_does_not_keep_disabled_peer(self):
        from localharness.python_controller import Controller
        from localharness.posix_process import tree_hold
        import fcntl
        with tempfile.TemporaryDirectory(prefix='helene-publisher-scope-') as folder:
            root=Path(folder); a=root/'a'; b=root/'b'; remote=root/'remote'
            for p in (a,b,remote):p.mkdir()
            script=root/'app/localharness/remote_computer_client.py'; script.parent.mkdir(parents=True)
            script.write_text('import time\ntime.sleep(60)\n')
            agent=SimpleNamespace(id='remote',enabled=True,conflict=False,config=remote/'helene.json',tree=remote)
            cfg={'mode':'remote','computer':{'remote':{'enabled':True}}}
            controller=Controller(root,lambda *args:None)
            def available(tree):
                code='import fcntl,os,sys;f=os.open(sys.argv[1],os.O_RDWR);fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
                return subprocess.run([sys.executable,'-I','-c',code,str(tree/'.desktop-owner.lock')],
                                      stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
            with tree_hold(a), contextlib.ExitStack() as held:
                held.enter_context(tree_hold(b))
                try:
                    with patch('localharness.python_controller.agents.roster',return_value=[agent]), \
                         patch('localharness.python_controller.load',return_value=cfg):
                        controller.sync_publishers()
                    self.assertIsNone(controller.publishers['remote'].poll())
                    held.close()
                    self.assertTrue(available(b), 'Unrelated remote publisher retained local B lease')
                    self.assertFalse(available(a), 'Actual local owner lease was incorrectly released')
                    self.assertIsNone(controller.publishers['remote'].poll())
                finally:controller.close()

    def _launcher_death(self, stop_tree):
        import psutil
        from command_process import CommandProcess
        with tempfile.TemporaryDirectory(prefix='helene-verifier-death-') as folder:
            root=Path(folder); ready=root/'ready.json'; result=root/'result.json'
            nested='import time;time.sleep(60)'
            command=('import subprocess,sys,time,json,os,psutil;from pathlib import Path;'
                     'p=subprocess.Popen([sys.executable,"-I","-c",'+repr(nested)+'],start_new_session=True);'
                     'Path('+repr(str(ready))+').write_text(json.dumps({"root":[os.getpid(),psutil.Process().create_time()],'
                     '"nested":[p.pid,psutil.Process(p.pid).create_time()]}));time.sleep(60)')
            worker=root/'launcher.py'
            desk=Path(__file__).resolve().parents[1]; core=desk.parent/'helene/core'
            worker.write_text('import sys,os,json\nfrom pathlib import Path\nsys.path[:0]='+repr([str(desk),str(core)])+
                '\nfrom localharness.source_update_battery import _execute\n'
                'value=_execute([sys.executable,"-c",'+repr(command)+'],Path('+repr(str(root))+'),dict(os.environ),Path('
                +repr(str(root/'output.log'))+'),lambda:False)\nPath('+repr(str(result))+').write_text(json.dumps(value))\n')
            captured={}
            with (root/'launcher.log').open('wb') as output:
                proc=CommandProcess([sys.executable,'-B',str(worker)],cwd=root,stdout=output,stderr=output)
            try:
                deadline=time.monotonic()+15
                while not ready.exists() and time.monotonic()<deadline:
                    if proc.poll() is not None:break
                    time.sleep(.03)
                self.assertTrue(ready.exists(), (root/'launcher.log').read_text())
                captured=json.loads(ready.read_text())
                if stop_tree:self.assertTrue(proc.stop_tree()[0])
                else:os.kill(proc.pid,signal.SIGKILL);proc.wait(5)
                deadline=time.monotonic()+10
                def live(identity):
                    try:
                        p=psutil.Process(identity[0])
                        return p.create_time()==identity[1] and p.status()!=psutil.STATUS_ZOMBIE
                    except psutil.NoSuchProcess:return False
                while any(live(p) for p in captured.values()) and time.monotonic()<deadline:time.sleep(.03)
                self.assertFalse(any(live(p) for p in captured.values()), 'Test sessions survived launcher death')
                self.assertFalse(result.exists(), 'Dead launcher published successful execution evidence')
            finally:
                proc.stop_tree()
                for pid,birth in captured.values():
                    try:
                        p=psutil.Process(pid)
                        if p.create_time()==birth:p.kill()
                    except psutil.NoSuchProcess:pass

    def test_outer_stop_retires_nested_test_sessions(self):self._launcher_death(True)
    def test_launcher_sigkill_retires_nested_test_sessions(self):self._launcher_death(False)


if __name__ == '__main__':unittest.main()
