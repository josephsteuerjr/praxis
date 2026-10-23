import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import migration_windows,python_windows


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

    def invoke(self,command,**args):
        return python_windows.invoke(self.owner,command,args)

    def test_exact_binding_unicode_spaces_apostrophe_and_remove(self):
        self.assertFalse(self.invoke('autostart_get'))
        self.invoke('autostart_set',on=True)
        binding=python_windows.shortcut_binding(self.startup)
        self.assertEqual(binding,{'target':str(self.install/'helene.exe'),'cwd':str(self.install),'arguments':'--launch-intent automatic'})
        self.assertTrue(self.invoke('autostart_get'))
        self.invoke('autostart_set',on=False)
        self.assertFalse(self.startup.exists())
        self.assertFalse(self.invoke('autostart_get'))

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

    def test_readback_mismatch_and_save_or_delete_denials_propagate(self):
        with patch.object(python_windows,'powershell',return_value=''),patch.object(python_windows,'shortcut_binding',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'did not match'):
                python_windows.shortcut(self.startup,self.install/'helene.exe',self.install)
        binding={'target':str(self.install/'helene.exe'),'cwd':str(self.install),'arguments':python_windows.AUTOMATIC_ARGUMENTS}
        for key in binding:
            wrong=dict(binding);wrong[key]='foreign'
            with self.subTest(key=key),patch.object(python_windows,'powershell',return_value=''),patch.object(python_windows,'shortcut_binding',return_value=wrong):
                with self.assertRaisesRegex(RuntimeError,'did not match'):
                    python_windows.shortcut(self.startup,self.install/'helene.exe',self.install,python_windows.AUTOMATIC_ARGUMENTS)
        with patch.object(python_windows,'powershell',side_effect=PermissionError('save denied')):
            with self.assertRaisesRegex(PermissionError,'save denied'):
                self.invoke('autostart_set',on=True)
        self.invoke('autostart_set',on=True)
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
            with patch.object(python_windows,'powershell',return_value=json.dumps(value)):
                with self.assertRaisesRegex(RuntimeError,'Invalid shortcut binding'):
                    self.invoke('autostart_get')

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
