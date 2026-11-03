"""Hermetic regression for empty roots, unrelated hooks and changed identities.

No test in this module may enumerate or mutate real Windows registrations.
"""
import json
import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import windows_hooks as hooks


class HookScope(unittest.TestCase):
    def setUp(self):
        self.current=r'C:\qa\Incoming'
        self.old=r'C:\qa\Old Helene'
        self.data=r'C:\Users\fixture\Helene\data'
        self.argv={}
    def service(self,name,*argv):
        command=subprocess.list2cmdline(argv);self.argv[command]=list(argv)
        return {'name':name,'command':command}
    def selected(self,scope,services=(),tasks=(),autoruns=()):
        return hooks.plan(scope,services,tasks,autoruns,split=self.argv.__getitem__)
    def test_empty_scope_never_even_reads_real_machine_inventory(self):
        backend=MagicMock()
        result=hooks.retire(hooks.Scope.make(self.current,[],[]),backend)
        self.assertEqual(result,hooks.Plan((),(),()))
        self.assertEqual(backend.mock_calls,[])
    def test_unrelated_task_with_null_optional_com_fields_does_not_abort_startup(self):
        rows=[{'name':'unrelated task','folder':'\\','actions':[{'kind':0,
            'executable':r'C:\Windows\System32\unrelated.exe','arguments':None,'cwd':None}]}]
        with patch.object(hooks,'task_call',return_value=rows):snapshot=hooks.Windows().tasks()
        self.assertEqual(snapshot[0]['actions'][0]['arguments'],'')
        self.assertEqual(snapshot[0]['actions'][0]['cwd'],'')
        self.assertEqual(snapshot[0]['actions'][0]['command'],'"C:\\Windows\\System32\\unrelated.exe"')
    def test_invalid_and_base_paths_refuse_before_inventory_or_effects(self):
        for value in ('',' ',None,'C:\\',r'C:\Windows',r'C:\Program Files',r'C:\Users\fixture\AppData\Local','relative',r'C:\qa\..\Windows'):
            with self.subTest(value=value),self.assertRaises((ValueError,TypeError)):
                hooks.Scope.make(self.current,[value],[])
        for roots in ('',r'C:\qa\old',None):
            with self.assertRaises(ValueError):hooks.Scope.make(self.current,roots,[])
    def test_orphan_data_scope_selects_only_its_known_body_bridge(self):
        unrelated=[self.service('AmneziaVPN-service',r'C:\Program Files\AmneziaVPN\AmneziaVPN-service.exe'),
                   self.service('AnyDesk',r'C:\Program Files (x86)\AnyDesk\AnyDesk.exe','--service'),
                   self.service('Graphics helper',r'C:\Windows\System32\helper.exe'),
                   self.service('Helene body',r'C:\other\external.exe','--root',self.data)]
        bridge=self.service('legacy bridge',r'C:\shared\helene-bridge.exe','--root',self.data)
        sibling=self.service('another bridge',r'C:\shared\helene-bridge.exe','--root',self.data+'-other')
        selected=self.selected(hooks.Scope.make(self.current,[],[self.data]),[*unrelated,bridge,sibling])
        self.assertEqual(selected.services,(bridge,))
        backend=MagicMock();hooks.apply(selected,backend)
        self.assertEqual(backend.mock_calls,[unittest.mock.call.remove_service(bridge)])
    def test_programme_path_is_literal_case_insensitive_and_has_boundaries(self):
        root=r'C:\qa\Helene [old]+(1)'
        owned=self.service('old',root.upper()+r'\runtime\python.exe','-B',root+r'\app\session_host.py')
        sibling=self.service('sibling',root+'-other'+r'\helene.exe')
        mention=self.service('unrelated',r'C:\Windows\System32\external.exe','--note',root)
        selected=self.selected(hooks.Scope.make(self.current,[root]),[owned,sibling,mention])
        self.assertEqual(selected.services,(owned,))
    def test_external_python_must_execute_an_old_entry_instead_of_mentioning_it(self):
        entry=self.service('session',r'C:\Python\python.exe','-B','-s',self.old+r'\app\session_host.py')
        note=self.service('diagnostic',r'C:\Python\python.exe','-c','print('+repr(self.old)+')')
        unrelated=self.service('other',r'C:\Python\python.exe',r'C:\other\agent.py','--note',self.old)
        selected=self.selected(hooks.Scope.make(self.current,[self.old]),[entry,note,unrelated])
        self.assertEqual(selected.services,(entry,))
    def test_existing_new_owner_is_never_selected_by_common_data_binding(self):
        new=self.service('Helene session',r'C:\Python\python.exe',self.current+r'\app\session_host.py','--root',self.data)
        new_body=self.service('body',self.current+r'\body\helene-body.exe','--root',self.data)
        for roots in ([self.current],[r'C:\qa'],[self.current+r'\runtime']):
            with self.assertRaises(ValueError):hooks.Scope.make(self.current,roots,[self.data])
        selected=self.selected(hooks.Scope.make(self.current,[self.old],[self.data]),[new,new_body])
        self.assertEqual(selected.services,())
    def test_mixed_scheduled_task_refuses_the_whole_plan_before_any_mutation(self):
        own=self.service('task',self.old+r'\helene.exe')
        other=self.service('other',r'C:\other\tool.exe')
        task={'name':'shared','folder':'\\','actions':[{'executable':self.old+r'\helene.exe','command':own['command']},
            {'executable':r'C:\other\tool.exe','command':other['command']}]}
        with self.assertRaisesRegex(RuntimeError,'shares old and unrelated'):
            self.selected(hooks.Scope.make(self.current,[self.old]),[own],[task])
    def test_task_and_run_commands_cannot_gain_authority_from_their_names(self):
        own=self.service('autorun',self.old+r'\helene.exe')
        unrelated=self.service('Helene supervisor',r'C:\other\app.exe','--note',self.old)
        tasks=[{'name':'Helene','folder':'\\','actions':[{'executable':r'C:\other\app.exe','command':unrelated['command']}]},
               {'name':'old','folder':'\\custom\\','actions':[{'executable':self.old+r'\helene.exe','command':own['command']}]}]
        selected=self.selected(hooks.Scope.make(self.current,[self.old]),tasks=tasks,autoruns=[unrelated,own])
        self.assertEqual(selected.tasks,(tasks[1],));self.assertEqual(selected.autoruns,(own,))
    def test_changed_registry_command_is_retained(self):
        fake=MagicMock();key=fake.OpenKey.return_value.__enter__.return_value
        fake.KEY_QUERY_VALUE=1;fake.KEY_SET_VALUE=2;fake.HKEY_CURRENT_USER=101
        fake.QueryValueEx.return_value=('new unrelated command',1)
        receipt={'hive':'HKEY_CURRENT_USER','view':0,'key':r'Software\Microsoft\Windows\CurrentVersion\Run','name':'old','command':'old command','kind':1}
        with patch.dict(sys.modules,winreg=fake),self.assertRaisesRegex(RuntimeError,'identity changed'):
            hooks.Windows().remove_autorun(receipt)
        fake.DeleteValue.assert_not_called()
    def test_task_access_denied_is_distinct_from_identity_or_runtime_failure(self):
        from localharness import migration_windows
        for code,error in ((-2147024891,PermissionError),(-2146233087,RuntimeError)):
            with patch.object(migration_windows,'powershell',return_value=json.dumps({'ok':False,'code':code})),self.assertRaises(error):
                hooks.task_call('read-only fixture')
    def test_first_start_never_retries_logical_failure_as_administrator(self):
        from localharness import windows_first_start as native
        import tempfile
        if os.name!='nt':
            # Only the Windows path ABI is supplied on POSIX. Keep actual
            # private filesystem generations and custody checks below; no
            # real registration reader or privileged action is admitted.
            from types import SimpleNamespace
            self.enterContext(patch.object(hooks.Scope,'make',side_effect=lambda incoming,roots,data:
                SimpleNamespace(incoming=str(incoming),roots=tuple(map(str,roots)),data=tuple(map(str,data)))))
        with tempfile.TemporaryDirectory(prefix='hook-permission-private-') as temporary:
            base=Path(temporary);current=base/'incoming';old=base/'old';data=base/'data'
            for path in (current,old,data):path.mkdir()
            backend=native.Backend(current);backend.roots=[old];backend.keep=[data];backend.protected=[data]
            with patch.object(hooks,'retire',side_effect=RuntimeError('binding changed')),patch.object(backend,'privileged') as elevated:
                with self.assertRaisesRegex(RuntimeError,'binding changed'):backend.hooks([old],[data])
                elevated.assert_not_called()
            with patch.object(hooks,'retire',side_effect=PermissionError(5,'denied')),patch.object(backend,'privileged') as elevated:
                backend.hooks([old],[data]);elevated.assert_called_once()
                self.assertEqual(elevated.call_args.args,('hooks',))
                captured=elevated.call_args.kwargs['_captured']
                self.assertEqual(captured['scope']['roots'],[str(old)])
                self.assertEqual(native._verify_custody(captured['scope'],captured),captured)
    def test_privileged_worker_rejects_empty_root_before_cwd_resolution(self):
        from localharness import windows_first_start as native,first_start
        request={'action':'hooks','incoming':self.current,'roots':[''],'protected':[self.data],'source':''}
        with patch.object(first_start,'plain') as resolve,self.assertRaises(ValueError):native.privileged_action(request)
        resolve.assert_not_called()


@unittest.skipUnless(os.name=='nt','Windows ctypes ABI; all DLL calls are fake')
class ExactServiceMutation(unittest.TestCase):
    def setUp(self):
        self.command='"C:\\qa\\Old Helene\\helene.exe" --service'
        self.api=MagicMock();self.api.OpenSCManagerW.return_value=101;self.api.OpenServiceW.return_value=102
        self.keep=c.create_unicode_buffer(self.command)
        self.order=[];self.states=[4,1]
        def configuration(handle,buffer,size,needed):
            # QUERY_SERVICE_CONFIGW: three DWORDs, then aligned LPWSTR.
            offset=(3*c.sizeof(w.DWORD)+c.sizeof(w.LPVOID)-1)//c.sizeof(w.LPVOID)*c.sizeof(w.LPVOID)
            w.LPVOID.from_buffer(buffer,offset).value=c.addressof(self.keep)
            self.order.append('query-binding');return True
        def status(handle,out):
            c.cast(out,c.POINTER(w.DWORD))[1]=self.states.pop(0)
            self.order.append('query-status');return True
        self.api.QueryServiceConfigW.side_effect=configuration
        self.api.QueryServiceStatus.side_effect=status
        self.api.ChangeServiceConfigW.side_effect=lambda *args:self.order.append('disable') or True
        self.api.ControlService.side_effect=lambda *args:self.order.append('stop') or True
        self.api.DeleteService.side_effect=lambda *args:self.order.append('delete') or True
        self.patch=patch.object(hooks.c,'WinDLL',return_value=self.api)
        self.patch.start();self.addCleanup(self.patch.stop)
    def test_exact_binding_is_checked_before_disable_stop_and_delete(self):
        hooks.Windows().remove_service({'name':'owned fixture','command':self.command})
        self.assertEqual(self.order,['query-binding','disable','query-status','stop','query-status','delete'])
        self.api.CloseServiceHandle.assert_any_call(102);self.api.CloseServiceHandle.assert_any_call(101)
    def test_changed_binding_cannot_disable_or_delete_the_service(self):
        self.keep=c.create_unicode_buffer(r'C:\unrelated\new.exe')
        with self.assertRaisesRegex(RuntimeError,'binding changed'):
            hooks.Windows().remove_service({'name':'owned fixture','command':self.command})
        self.api.ChangeServiceConfigW.assert_not_called();self.api.DeleteService.assert_not_called()
    def test_access_denied_never_degrades_to_a_broader_selector(self):
        def denied(*args):c.set_last_error(5);return False
        self.api.QueryServiceConfigW.side_effect=denied
        with self.assertRaises(PermissionError):hooks.Windows().remove_service({'name':'owned fixture','command':self.command})
        self.api.ChangeServiceConfigW.assert_not_called();self.api.DeleteService.assert_not_called()


@unittest.skipUnless(os.name=='nt','Windows parser only; no registrations accessed')
class CommandParser(unittest.TestCase):
    def test_actual_windows_parser_roundtrips_quoted_spaces_and_metacharacters(self):
        for argv in ([r'C:\qa\Old [1]+ Helene\helene.exe','--root',r'C:\Users\fixture\Helene\data'],
                     [r'C:\Python\python.exe','-B',r'C:\qa\old\app\session_host.py','--note','quote " and backslash\\']):
            self.assertEqual(hooks.split_command(subprocess.list2cmdline(argv)),argv)


@unittest.skipUnless(os.name=='nt','Windows PowerShell with fake COM objects only')
class TaskMutation(unittest.TestCase):
    def test_real_powershell_snapshot_normalizes_null_fields_from_fake_com(self):
        from localharness import migration_windows
        original=migration_windows.powershell
        fixture=r'''
$script:fixtureTask=[pscustomobject]@{Name='ordinary task';Definition=[pscustomobject]@{Actions=@([pscustomobject]@{Type=0;Path='C:\Windows\System32\unrelated.exe';Arguments=$null;WorkingDirectory=$null})}}
$fixtureFolder=[pscustomobject]@{Path='\'}
$fixtureFolder|Add-Member ScriptMethod GetTasks {param($flags) return $script:fixtureTask}
$fixtureFolder|Add-Member ScriptMethod GetFolders {param($flags) return @()}
$fixtureScheduler=[pscustomobject]@{}
$fixtureScheduler|Add-Member ScriptMethod Connect {}
$fixtureScheduler|Add-Member ScriptMethod GetFolder {param($path) if($path -cne '\'){throw 'Wrong fixture folder'};return $fixtureFolder}
function New-Object {param([string]$TypeName,[string]$ComObject)
 if($ComObject -ceq 'Schedule.Service'){return $fixtureScheduler}
 if($TypeName -ceq 'System.Collections.Generic.List[object]'){return ,([System.Collections.Generic.List[object]]::new())}
 throw 'Unexpected fixture constructor'
}
'''
        def fake_com_only(script,env):return original(fixture+script,env)
        with patch.object(migration_windows,'powershell',side_effect=fake_com_only):
            rows=hooks.Windows().tasks()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['actions'][0]['arguments'],'')
        self.assertEqual(rows[0]['actions'][0]['cwd'],'')
        self.assertEqual(rows[0]['actions'][0]['command'],'"C:\\Windows\\System32\\unrelated.exe"')
    def test_literal_task_identity_and_changed_actions_in_actual_powershell(self):
        from localharness.migration_windows import powershell
        fixture=r'''
$ErrorActionPreference='Stop'
$fixtureReceipt=[pscustomobject]@{name='old[*] literal';folder='\qa\';actions=@([pscustomobject]@{kind=0;executable='C:\qa\old\helene.exe';arguments='--service';cwd='C:\qa\old'})}
$env:HELENE_EXACT_TASK=ConvertTo-Json -InputObject $fixtureReceipt -Depth 5 -Compress
$task=[pscustomobject]@{Name=$fixtureReceipt.name;Enabled=$true;Definition=[pscustomobject]@{Actions=@([pscustomobject]@{Type=0;Path=$fixtureReceipt.actions[0].executable;Arguments=$fixtureReceipt.actions[0].arguments;WorkingDirectory=$fixtureReceipt.actions[0].cwd})}}
if($env:HELENE_FAKE_NULL_OPTIONALS -eq 'yes'){
 $fixtureReceipt.actions[0].arguments='';$fixtureReceipt.actions[0].cwd=''
 $env:HELENE_EXACT_TASK=ConvertTo-Json -InputObject $fixtureReceipt -Depth 5 -Compress
 $task.Definition.Actions[0].Arguments=$null;$task.Definition.Actions[0].WorkingDirectory=$null
}
if($env:HELENE_FAKE_TASK_CHANGED -eq 'yes'){$task.Definition.Actions[0].Path='C:\unrelated\new.exe'}
$script:deleted=$false
$folder=[pscustomobject]@{}
$folder|Add-Member ScriptMethod GetTask {param($name) if($name -cne $fixtureReceipt.name){throw 'Wrong literal name'};return $task}
$folder|Add-Member ScriptMethod DeleteTask {param($name,$flags) if($name -cne $fixtureReceipt.name -or $flags -ne 0){throw 'Wrong delete target'};$script:deleted=$true}
$fake=[pscustomobject]@{}
$fake|Add-Member ScriptMethod Connect {}
$fake|Add-Member ScriptMethod GetFolder {param($path) if($path -cne $fixtureReceipt.folder){throw 'Wrong folder'};return $folder}
function New-Object {param([string]$ComObject) if($ComObject -cne 'Schedule.Service'){throw 'Unexpected COM request'};return $fake}
$failed=$false;$message=''
try{& ([scriptblock]::Create($env:HELENE_FAKE_TASK_SCRIPT))|Out-Null}catch{$failed=$true;$message=$_.Exception.Message}
@{deleted=$script:deleted;failed=$failed;message=$message}|ConvertTo-Json -Compress
'''
        for changed in (False,True):
            for null in (False,True):
                result=json.loads(powershell(fixture,{**os.environ,'HELENE_FAKE_TASK_CHANGED':'yes' if changed else 'no','HELENE_FAKE_NULL_OPTIONALS':'yes' if null else 'no','HELENE_FAKE_TASK_SCRIPT':hooks.TASK_REMOVE}))
                self.assertEqual({key:result[key] for key in ('deleted','failed')},{'deleted':not changed,'failed':changed},result['message'])


if __name__=='__main__':unittest.main()
