import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import migration_windows,python_windows,windows_shortcuts


class ShortcutStatusTests(unittest.TestCase):
    def test_nonnegative_hresult_values_are_success(self):
        for status in (0,1,2,0x7fffffff):
            with self.subTest(status=status):
                windows_shortcuts._check(status,'read target')

    def test_negative_hresult_values_keep_the_operation_and_error_code(self):
        for status in (-2147467259,-2147024891,-1):
            with self.subTest(status=status),self.assertRaisesRegex(
                    OSError,rf'read target failed \(HRESULT 0x{status & 0xffffffff:08x}\)'):
                windows_shortcuts._check(status,'read target')


@unittest.skipUnless(os.name=='nt','Windows shortcuts')
class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='helene-autostart-')
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.install=self.root/"Элен owner's app"
        self.install.mkdir()
        (self.install/'helene.exe').write_bytes(b'fixture-not-executable')
        self.owner=SimpleNamespace(root=self.install)
        self.startup=self.root/'appdata/Microsoft/Windows/Start Menu/Programs/Startup/Helene.lnk'
        self.environment=patch.dict(os.environ,APPDATA=str(self.root/'appdata'))
        self.environment.start()
        self.addCleanup(self.environment.stop)
        # The current implementation uses HKCU Run for new registrations.
        # APPDATA isolates shortcut files, not the Windows registry.
        self.registration=None
        def save(value):self.registration=value
        for stub in (patch.object(python_windows,'run_binding',side_effect=lambda:self.registration),
                     patch.object(python_windows,'set_run',side_effect=save)):
            stub.start();self.addCleanup(stub.stop)

    def invoke(self,command,**args):
        return python_windows.invoke(self.owner,command,args)

    def test_exact_binding_unicode_spaces_apostrophe_and_remove(self):
        self.assertFalse(self.invoke('autostart_get'))
        self.invoke('autostart_set',on=True)
        self.assertFalse(self.startup.exists())
        self.assertEqual(self.registration,python_windows.run_command(self.install))
        self.assertTrue(self.invoke('autostart_get'))
        self.invoke('autostart_set',on=False)
        self.assertFalse(self.startup.exists())
        self.assertFalse(self.invoke('autostart_get'))
        self.assertIsNone(self.registration)

    def test_on_is_boolean_not_truthy(self):
        for value in ('false',1,0,None,[],{}):
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.invoke('autostart_set',on=value)
        self.assertFalse(self.startup.exists())

    def test_foreign_target_cwd_or_arguments_are_not_modified(self):
        variants=[(self.root/'another/helene.exe',self.install,''),
                  (self.install/'helene.exe',self.root/'another',''),
                  (self.install/'helene.exe',self.install,'--panic --notify')]
        for target,cwd,arguments in variants:
            with self.subTest(target=target,cwd=cwd,arguments=arguments):
                python_windows.shortcut(self.startup,target,cwd,arguments)
                before=self.startup.read_bytes()
                self.assertFalse(self.invoke('autostart_get'))
                for requested in (True,False):
                    with self.assertRaisesRegex(RuntimeError,'another launch binding'):
                        self.invoke('autostart_set',on=requested)
                    self.assertEqual(self.startup.read_bytes(),before)

    def test_same_installation_legacy_shortcut_is_upgraded(self):
        python_windows.shortcut(self.startup,self.install/'helene.exe',self.install)
        self.assertFalse(self.invoke('autostart_get'))
        self.invoke('autostart_set',on=True)
        self.assertTrue(self.invoke('autostart_get'))
        self.assertFalse(self.startup.exists())
        self.assertEqual(self.registration,python_windows.run_command(self.install))

    def test_readback_mismatch_and_save_or_delete_denials_propagate(self):
        with patch('localharness.windows_shortcuts.write'),patch.object(python_windows,'shortcut_binding',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'did not match'):
                python_windows.shortcut(self.startup,self.install/'helene.exe',self.install)
        binding={'target':str(self.install/'helene.exe'),'cwd':str(self.install),'arguments':python_windows.AUTOMATIC_ARGUMENTS}
        for key in binding:
            wrong=dict(binding);wrong[key]='foreign'
            with self.subTest(key=key),patch('localharness.windows_shortcuts.write'),patch.object(python_windows,'shortcut_binding',return_value=wrong):
                with self.assertRaisesRegex(RuntimeError,'did not match'):
                    python_windows.shortcut(self.startup,self.install/'helene.exe',self.install,python_windows.AUTOMATIC_ARGUMENTS)
        with patch.object(python_windows,'set_run',side_effect=PermissionError('save denied')):
            with self.assertRaisesRegex(PermissionError,'save denied'):
                self.invoke('autostart_set',on=True)
        python_windows.shortcut(self.startup,self.install/'helene.exe',self.install,python_windows.AUTOMATIC_ARGUMENTS)
        before=self.startup.read_bytes()
        with patch.object(Path,'unlink',side_effect=PermissionError('delete denied')):
            with self.assertRaisesRegex(PermissionError,'delete denied'):
                self.invoke('autostart_set',on=False)
        self.assertEqual(self.startup.read_bytes(),before)
        self.assertTrue(self.invoke('autostart_get'))

    def test_malformed_shortcut_readback_is_not_a_boolean(self):
        self.startup.parent.mkdir(parents=True)
        self.startup.write_bytes(b'fixture')
        for value in ({'target':'foreign'},{'target':None,'cwd':'','arguments':''}):
            with patch('localharness.windows_shortcuts.read',return_value=value):
                with self.assertRaisesRegex(RuntimeError,'Invalid shortcut binding'):
                    self.invoke('autostart_get')

    def test_native_empty_target_is_unowned_and_kept_during_shortcut_discovery(self):
        folder=self.root/'namespace-links';folder.mkdir()
        foreign=folder/'Namespace.lnk'
        # A persisted shell link with no filesystem target is a real S_FALSE
        # GetPath result. It must never acquire ownership from its arguments.
        windows_shortcuts.write(foreign,'',self.install,
            '--root '+str(self.install/'app/python_app.py'))
        before=foreign.read_bytes()
        statuses=[]
        check=windows_shortcuts._check
        def observe(status,operation):
            statuses.append((operation,status))
            return check(status,operation)
        with patch.object(windows_shortcuts,'_check',side_effect=observe):
            self.assertIsNone(python_windows.shortcut_binding(foreign))
        self.assertIn(('read target',1),statuses)
        self.assertEqual(list(migration_windows.owned_shortcuts(self.install,[folder])),[])
        self.assertEqual(foreign.read_bytes(),before)

    def test_failed_getpath_hresult_propagates_through_normal_binding(self):
        python_windows.shortcut(self.startup,self.install/'helene.exe',self.install)
        before=self.startup.read_bytes()
        method=windows_shortcuts._method
        def fail_getpath(pointer,index,*types):
            if index==3:return lambda *args:-2147467259
            return method(pointer,index,*types)
        with patch.object(windows_shortcuts,'_method',side_effect=fail_getpath):
            with self.assertRaisesRegex(OSError,'read target failed.*0x80004005'):
                python_windows.shortcut_binding(self.startup)
        self.assertEqual(self.startup.read_bytes(),before)

    def test_migration_retargets_only_old_root_and_retains_launch_intent(self):
        old=self.root/'old';old.mkdir();(old/'helene.exe').write_bytes(b'fixture')
        roots=[self.root/name for name in ('user-programs','desktop','common-programs','public-desktop')]
        bindings=[(roots[0]/'Startup/Helene.lnk',old/'helene.exe',''),
                  (roots[2]/'Startup/Helene.lnk',old/'helene.exe',''),
                  (roots[1]/'automatic.lnk',old/'helene.exe','--launch-intent automatic'),
                  (roots[1]/'manual.lnk',old/'helene.exe',''),
                  (roots[1]/'panic.lnk',old/'helene.exe','--panic --notify'),
                  (roots[1]/'foreign.lnk',self.root/'old-neighbor/helene.exe','')]
        for link,target,arguments in bindings:python_windows.shortcut(link,target,old,arguments)
        foreign=bindings[-1][0].read_bytes()
        replaced=migration_windows.retarget_shortcuts(old,self.install,roots)
        self.assertEqual(len(replaced),5)
        for link,_,_ in bindings[:3]:
            self.assertTrue(python_windows.shortcut_matches(python_windows.shortcut_binding(link),self.install/'helene.exe',self.install,python_windows.AUTOMATIC_ARGUMENTS))
        self.assertEqual(python_windows.shortcut_binding(bindings[3][0])['arguments'],'')
        self.assertEqual(python_windows.shortcut_binding(bindings[4][0])['arguments'],'--panic --notify')
        self.assertEqual(bindings[-1][0].read_bytes(),foreign)


if __name__=='__main__':unittest.main()
