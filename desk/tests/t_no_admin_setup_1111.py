"""Restricted work-PC startup: no elevation for inventory or protected cleanup.

No real services, tasks, registrations, accounts or foreign processes are changed.
"""
from pathlib import Path
from types import SimpleNamespace
import os,sys,tempfile,unittest
from unittest.mock import patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import windows_hooks as hooks,windows_first_start as native,first_start as start,migration_windows as windows


class Inventory(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','real Windows PowerShell, synthetic COM only')
    def test_denied_task_folder_keeps_readable_neighbor_and_logical_errors_fail(self):
        original=windows.powershell
        fixture=r'''
$script:fixtureTask=[pscustomobject]@{Name='ordinary task';Definition=[pscustomobject]@{Actions=@([pscustomobject]@{Type=0;Path='C:\Other\service.exe';Arguments=$null;WorkingDirectory=$null})}}
$script:denied=[pscustomobject]@{Path='\protected\'}
$script:denied|Add-Member ScriptMethod GetTasks {param($flags) if($env:HELENE_FAKE_LOGICAL -eq 'yes'){throw 'Invalid task binding'};throw [UnauthorizedAccessException]::new('Denied fixture')}
$script:denied|Add-Member ScriptMethod GetFolders {param($flags) return @()}
$script:readable=[pscustomobject]@{Path='\readable\'}
$script:readable|Add-Member ScriptMethod GetTasks {param($flags) return $script:fixtureTask}
$script:readable|Add-Member ScriptMethod GetFolders {param($flags) return @()}
$fixtureFolder=[pscustomobject]@{Path='\'}
$fixtureFolder|Add-Member ScriptMethod GetTasks {param($flags) return @()}
$fixtureFolder|Add-Member ScriptMethod GetFolders {param($flags) return @($script:denied,$script:readable)}
$fixtureScheduler=[pscustomobject]@{}
$fixtureScheduler|Add-Member ScriptMethod Connect {}
$fixtureScheduler|Add-Member ScriptMethod GetFolder {param($path) if($path -cne '\'){throw 'Wrong fixture folder'};return $fixtureFolder}
function New-Object {param([string]$TypeName,[string]$ComObject)
 if($ComObject -ceq 'Schedule.Service'){return $fixtureScheduler}
 if($TypeName -ceq 'System.Collections.Generic.List[object]'){return ,([System.Collections.Generic.List[object]]::new())}
 throw 'Unexpected fixture constructor'
}
'''
        for logical in (False,True):
            backend=hooks.Windows()
            def fake_com_only(script,env):return original(fixture+script,{**env,'HELENE_FAKE_LOGICAL':'yes' if logical else 'no'})
            with patch.object(windows,'powershell',side_effect=fake_com_only),patch.object(windows,'run_privileged',side_effect=AssertionError('UAC forbidden')):
                if logical:
                    with self.assertRaises(RuntimeError):backend.tasks()
                else:
                    rows=backend.tasks()
                    self.assertEqual([r['name'] for r in rows],['ordinary task'])
                    self.assertIn('task folder',backend.unreadable)

    def test_unreadable_service_is_retained_without_hiding_readable_services(self):
        import psutil
        protected=SimpleNamespace(name=lambda:'Protected corporate service',binpath=MagicMock(side_effect=psutil.AccessDenied()))
        ordinary=SimpleNamespace(name=lambda:'Other service',binpath=lambda:r'C:\Other\service.exe')
        backend=hooks.Windows()
        with patch.object(psutil,'win_service_iter',return_value=iter([protected,ordinary]),create=True):
            rows=backend.services()
        self.assertEqual(rows,[{'name':'Other service','command':r'C:\Other\service.exe'}])
        self.assertTrue(backend.unreadable)

    def test_protected_registry_view_does_not_hide_current_user_autorun(self):
        fake=MagicMock();fake.KEY_READ=1;fake.KEY_WOW64_64KEY=256;fake.KEY_WOW64_32KEY=512
        fake.HKEY_CURRENT_USER=100;fake.HKEY_LOCAL_MACHINE=200;fake.REG_SZ=1;fake.REG_EXPAND_SZ=2
        context=MagicMock();fake.QueryInfoKey.return_value=(0,1,0)
        fake.EnumValue.return_value=('Own launcher',r'C:\qa\old\helene.exe',1)
        def open_key(hive,*args):
            if hive==200:raise PermissionError(5,'machine registry denied')
            return context
        fake.OpenKey.side_effect=open_key;backend=hooks.Windows()
        with patch.dict(sys.modules,winreg=fake):rows=backend.autoruns()
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['hive']=='HKEY_CURRENT_USER' for r in rows))
        self.assertTrue(backend.unreadable)


@unittest.skipUnless(os.name=='nt','Windows first-start backend')
class Cleanup(unittest.TestCase):
    def test_locked_old_file_is_named_without_stopping_or_elevating_its_holder(self):
        with tempfile.TemporaryDirectory(prefix='helene-no-admin-') as temporary:
            root=Path(temporary);incoming=root/'incoming';incoming.mkdir();old=root/'old';old.mkdir()
            locked=old/'locked.dll';locked.write_bytes(b'protected old file')
            backend=native.Backend(incoming);backend.roots=[old]
            real=Path.unlink
            def refuse(self,*a,**kw):
                if self==locked:raise PermissionError(13,'locked by another account')
                return real(self,*a,**kw)
            with patch.object(Path,'unlink',refuse),patch.object(backend,'privileged',side_effect=AssertionError('UAC forbidden')),\
                    patch.object(backend,'stop',side_effect=AssertionError('cleanup must not stop processes')),\
                    patch.object(backend,'release',side_effect=AssertionError('cleanup must not terminate file holders')):
                backend.remove(locked)
            self.assertEqual(locked.read_bytes(),b'protected old file')
            self.assertIn(locked,backend.leftovers)


@unittest.skipUnless(os.name=='nt','Windows first-start backend')
class Preparation(unittest.TestCase):
    def fixture(self):
        from t_first_start_3010 import FirstStart
        fixture=FirstStart();fixture.setUp();self.addCleanup(fixture.doCleanups)
        backend=native.Backend(fixture.root)
        self.enterContext(patch.object(backend,'registered',return_value=[]))
        self.enterContext(patch.object(backend,'shortcuts'))
        self.enterContext(patch.object(backend,'privileged',side_effect=AssertionError('UAC forbidden')))
        self.enterContext(patch.object(windows,'run_privileged',side_effect=AssertionError('UAC forbidden')))
        return fixture,backend

    def test_clean_install_never_inventories_or_stops_old_owners(self):
        fixture,backend=self.fixture();events=[]
        with patch.object(backend,'hooks',side_effect=AssertionError('fresh install must not inspect system hooks')),\
                patch.object(native,'owned',side_effect=AssertionError('fresh install must not scan old owners')):
            result=start.prepare(fixture.root,places=fixture.places,backend=backend,progress=events.append)
        self.assertEqual(result['state'],'completed')
        self.assertTrue(start.ready(fixture.root,fixture.places))
        self.assertFalse(any(e['stage']=='stopping' for e in events))

    def test_upgrade_carries_data_and_completes_with_protected_program_file(self):
        fixture,backend=self.fixture();old,data=fixture.old()
        locked=old/'app/old.py';expected=locked.read_bytes()
        original=os.unlink
        def refuse(path,*a,**kw):
            if Path(path)==locked:raise PermissionError(5,'protected fixture')
            return original(path,*a,**kw)
        with patch.object(hooks.Windows,'services',return_value=[]),patch.object(hooks.Windows,'tasks',return_value=[]),\
                patch.object(hooks.Windows,'autoruns',return_value=[]),patch.object(native,'owned',return_value=[]),\
                patch.object(os,'unlink',side_effect=refuse):
            result=start.prepare(fixture.root,places=fixture.places,backend=backend)
        self.assertEqual(result['state'],'completed')
        self.assertTrue(start.ready(fixture.root,fixture.places))
        self.assertEqual(locked.read_bytes(),expected)
        self.assertEqual((fixture.places.data/'memory/groups/window.jsonl').read_bytes(),b'old complete conversation\n')
        self.assertTrue(any(str(old) in note for note in result['cleanup_errors']))

    def test_upgrade_completes_when_windows_itself_denies_deletion_of_open_old_file(self):
        import ctypes as c
        from ctypes import wintypes as w
        fixture,backend=self.fixture();old,data=fixture.old();locked=old/'app/old.py'
        api=c.WinDLL('kernel32',use_last_error=True)
        api.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE];api.CreateFileW.restype=w.HANDLE
        api.CloseHandle.argtypes=[w.HANDLE];api.CloseHandle.restype=w.BOOL
        handle=api.CreateFileW(str(locked),0x80000000,1,None,3,0,None)
        self.assertNotEqual(handle,c.c_void_p(-1).value,c.get_last_error())
        try:
            with patch.object(hooks.Windows,'services',return_value=[]),patch.object(hooks.Windows,'tasks',return_value=[]),\
                    patch.object(hooks.Windows,'autoruns',return_value=[]),patch.object(native,'owned',return_value=[]):
                result=start.prepare(fixture.root,places=fixture.places,backend=backend)
            self.assertEqual(result['state'],'completed')
            self.assertTrue(locked.is_file())
            self.assertTrue(any(str(old) in note for note in result['cleanup_errors']))
            self.assertEqual((fixture.places.data/'memory/groups/window.jsonl').read_bytes(),b'old complete conversation\n')
        finally:api.CloseHandle(handle)

    def test_denied_selected_autorun_is_reported_and_other_hooks_still_retire(self):
        fixture,backend=self.fixture();old,data=fixture.old()
        backend.roots=[old];backend.protected=[data];backend.keep=[data]
        import subprocess
        command=subprocess.list2cmdline([str(old/'helene.exe')])
        rows=[{'name':'protected','command':command},{'name':'ordinary','command':command}]
        calls=[]
        def remove(row):
            calls.append(row['name'])
            if row['name']=='protected':raise PermissionError(5,'protected fixture')
        with patch.object(hooks.Windows,'services',return_value=[]),patch.object(hooks.Windows,'tasks',return_value=[]),\
                patch.object(hooks.Windows,'autoruns',return_value=rows),patch.object(hooks.Windows,'remove_autorun',side_effect=remove):
            backend.hooks([old],[data])
        self.assertEqual(calls,['protected','ordinary'])
        self.assertTrue(any('protected' in note for note in backend.notes))


@unittest.skipUnless(os.name=='nt','Windows system operation')
class SystemActions(unittest.TestCase):
    def test_real_powershell_keeps_permission_and_binding_failures_distinct(self):
        original=windows.powershell
        for outcome,error in (('denied',PermissionError),('changed',RuntimeError),('ok',None)):
            fixture=r'''
function Get-CimInstance {param($Filter,$ClassName)
 if($env:HELENE_FAKE_OUTCOME -eq 'denied'){throw [UnauthorizedAccessException]::new('Denied fixture')}
 if($env:HELENE_FAKE_OUTCOME -eq 'changed'){throw 'Changed fixture binding'}
 return $null
}
function Get-ScheduledTask {return @()}
'''
            def synthetic_only(script,env):return original(fixture+script,{**env,'HELENE_FAKE_OUTCOME':outcome})
            with patch.object(windows,'powershell',side_effect=synthetic_only),patch.object(windows,'run_privileged',side_effect=AssertionError('UAC forbidden')):
                if error:
                    with self.assertRaises(error):windows.system_action(Path('C:/qa/old'),'custody')
                else:windows.system_action(Path('C:/qa/old'),'custody')

    def test_every_install_action_refuses_elevation_even_with_old_compatibility_flag(self):
        import json
        for action in ('stop_service','restore_service','custody','registration','cleanup'):
            for code,error in ((-2147024891,PermissionError),(-2146233087,RuntimeError)):
                with self.subTest(action=action,code=code),patch.object(windows,'powershell',return_value=json.dumps({'ok':False,'code':code})),\
                        patch.object(windows,'run_privileged',side_effect=AssertionError('UAC forbidden')):
                    with self.assertRaises(error):windows.system_action(Path('C:/qa/old'),action,elevate=True)

    def test_changed_binding_still_fails_and_only_permission_denial_is_retained(self):
        for error in (RuntimeError('binding changed'),PermissionError(5,'denied')):
            retained=[]
            with patch.object(windows,'system_registrations',return_value={'task'}),patch.object(windows,'service',return_value=None),\
                    patch.object(windows,'system_action',side_effect=error):
                if isinstance(error,PermissionError):
                    windows.unhook(Path('C:/qa/old'),retained);self.assertTrue(retained)
                else:
                    with self.assertRaisesRegex(RuntimeError,'binding changed'):windows.unhook(Path('C:/qa/old'),retained)


if __name__=='__main__':unittest.main()
