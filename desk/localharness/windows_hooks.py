"""Plan legacy Windows hooks in Python; mutate only exact, rechecked objects.

Reading the machine inventory never grants removal authority. An empty or invalid
programme path cannot become a match. No destructive command contains a path
regular expression or a wildcard selector.
"""
from __future__ import annotations
from dataclasses import dataclass
import ctypes as c
from ctypes import wintypes as w
import json
import ntpath
import os
from pathlib import PureWindowsPath
import time


ORGANS={'helene-body.exe','helene-bridge.exe','praxis-body.exe','praxis-bridge.exe','praxis-relay.exe'}
INTERPRETERS={'python.exe','pythonw.exe','python3.exe','wscript.exe','cscript.exe'}
MODULES={'localharness.session_host','localharness.runner','python_app','deskapp','windows_start'}
DATA_FLAGS={'--root','--data','--tree','--data-dir','--config','--profile','--spool','--db','--database'}
LABELS={brand+separator+part for brand in ('helene','praxis') for separator in ('',' ','_','-')
        for part in ('body','bridge','session','supervisor','relay')}


def path(value):
    if not isinstance(value,str) or not value.strip() or '\0' in value:
        raise ValueError('Empty or invalid Windows hook path')
    value=os.path.expandvars(value)
    if '%' in value or any(ord(char)<32 for char in value):raise ValueError('Unresolved Windows hook path')
    if value.startswith('\\\\?\\UNC\\'):value='\\\\'+value[8:]
    elif value.startswith('\\\\?\\'):value=value[4:]
    parsed=PureWindowsPath(value)
    if not parsed.is_absolute() or '..' in parsed.parts:raise ValueError('Windows hook path must be absolute')
    return ntpath.normcase(ntpath.normpath(value))


def within(value,root):
    try:return ntpath.commonpath((value,root))==root
    except ValueError:return False


def paths(values):
    if isinstance(values,(str,bytes)) or values is None:raise ValueError('Hook roots must be a list of paths')
    result=[]
    for value in values:
        item=path(os.fspath(value))
        if len(PureWindowsPath(item).parts)<3:raise ValueError('Windows base directory is not a hook scope')
        if PureWindowsPath(item).name in {'windows','users','program files','program files (x86)','programdata','appdata','local','roaming','locallow','programs','downloads'}:
            raise ValueError('Windows base directory is not a hook scope')
        result.append(item)
    return tuple(dict.fromkeys(result))


@dataclass(frozen=True)
class Scope:
    incoming: str|None
    roots: tuple[str,...]
    data: tuple[str,...]

    @classmethod
    def make(cls,incoming,roots,data=()):
        current=path(str(incoming))
        old=paths(roots);protected=paths(data)
        if any(within(current,item) or within(item,current) for item in old):
            raise ValueError('Incoming programme is protected from hook removal')
        return cls(current,old,protected)

    def binds(self,argv,label=''):
        if not argv or not all(isinstance(value,str) for value in argv):return False
        def absolute(value):
            try:return path(value)
            except ValueError:return None
        executable=absolute(argv[0])
        if not executable or (self.incoming and within(executable,self.incoming)):return False
        arguments=[absolute(value.split('=',1)[1] if value.startswith('--') and '=' in value else value) for value in argv[1:]]
        if self.incoming and any(value and within(value,self.incoming) for value in arguments):return False
        if any(within(executable,root) for root in self.roots):return True
        component=ntpath.basename(executable)
        module=None
        if component in INTERPRETERS:
            # A diagnostic string or a mentioned directory is not an entry script.
            index=1
            while index<len(argv):
                value=argv[index];index+=1
                if value in ('-c','/e'):break
                if value=='-m':
                    module=argv[index] if index<len(argv) else None
                    break
                if value in ('-X','-W','--check-hash-based-pycs'):index+=1;continue
                if value=='--':
                    if index>=len(argv):break
                    value=argv[index]
                elif value.startswith(('-','/')):continue
                script=absolute(value)
                if script and ntpath.splitext(script)[1].lower() in ('.py','.pyw','.vbs'):
                    if any(within(script,root) for root in self.roots):return True
                break
        if component not in ORGANS and not (component in INTERPRETERS and module in MODULES):return False
        # External bridge/body binaries may belong to an orphaned data owner.
        bound=[]
        for index,value in enumerate(argv[1:],1):
            if value in DATA_FLAGS and index+1<len(argv):bound.append(absolute(argv[index+1]))
            elif '=' in value and value.split('=',1)[0] in DATA_FLAGS:bound.append(absolute(value.split('=',1)[1]))
        return any(value and any(within(value,root) for root in (*self.roots,*self.data)) for value in bound)


def split_command(command):
    """Use Windows' actual argument parser, with no shell evaluation."""
    if not isinstance(command,str) or not command.strip() or '\0' in command:return []
    command=os.path.expandvars(command)
    api=c.WinDLL('shell32',use_last_error=True);core=c.WinDLL('kernel32',use_last_error=True)
    api.CommandLineToArgvW.argtypes=[w.LPCWSTR,c.POINTER(c.c_int)]
    api.CommandLineToArgvW.restype=c.POINTER(w.LPWSTR)
    core.LocalFree.argtypes=[w.LPVOID]
    count=c.c_int();items=api.CommandLineToArgvW(command,c.byref(count))
    if not items:raise c.WinError(c.get_last_error())
    try:return [items[index] for index in range(count.value)]
    finally:core.LocalFree(items)


@dataclass(frozen=True)
class Plan:
    services: tuple
    tasks: tuple
    autoruns: tuple


def plan(scope,services,tasks,autoruns,*,split=split_command):
    def bound(command,label=''):return scope.binds(split(command),label)
    selected_services=tuple(row for row in services if bound(row['command'],row['name']))
    selected_tasks=[]
    for row in tasks:
        ownership=[bool(action.get('executable')) and bound(action['command'],row['name']) for action in row['actions']]
        if any(ownership):
            if not all(ownership):raise RuntimeError('A scheduled task shares old and unrelated actions; retained')
            selected_tasks.append(row)
    return Plan(selected_services,tuple(selected_tasks),tuple(row for row in autoruns if bound(row['command'],row['name'])))


def apply(selected,backend):
    """Every backend operation rechecks the captured identity before mutation."""
    for row in selected.services:backend.remove_service(row)
    for row in selected.tasks:backend.remove_task(row)
    for row in selected.autoruns:backend.remove_autorun(row)


TASK_SNAPSHOT=r'''
$scheduler=New-Object -ComObject Schedule.Service;$scheduler.Connect()
$rows=New-Object System.Collections.Generic.List[object]
function Visit($folder){
 foreach($task in $folder.GetTasks(1)){
  $actions=@(foreach($action in $task.Definition.Actions){
   if($action.Type -eq 0){[pscustomobject]@{executable=[string]$action.Path;arguments=[string]$action.Arguments;cwd=[string]$action.WorkingDirectory;kind=0}}
   else{[pscustomobject]@{executable='';arguments='';cwd='';kind=[int]$action.Type}}})
  $rows.Add([pscustomobject]@{name=$task.Name;folder=$folder.Path;actions=$actions})
 }
 foreach($child in $folder.GetFolders(0)){Visit $child}
}
Visit ($scheduler.GetFolder('\'))
@($rows.ToArray())
'''

TASK_REMOVE=r'''
$request=ConvertFrom-Json -InputObject $env:HELENE_EXACT_TASK
if(!$request.name -or !$request.folder -or @($request.actions).Count -eq 0){throw 'Invalid exact task receipt'}
$scheduler=New-Object -ComObject Schedule.Service;$scheduler.Connect()
$folder=$scheduler.GetFolder([string]$request.folder)
$task=$folder.GetTask([string]$request.name)
if($task.Name -cne $request.name){throw 'Scheduled task identity changed'}
$actions=@($task.Definition.Actions)
if($actions.Count -ne @($request.actions).Count){throw 'Scheduled task actions changed'}
for($i=0;$i -lt $actions.Count;$i++){
 $actual=$actions[$i];$expected=$request.actions[$i]
 if($actual.Type -ne 0 -or $expected.kind -ne 0 -or [string]$actual.Path -cne [string]$expected.executable -or
    [string]$actual.Arguments -cne [string]$expected.arguments -or [string]$actual.WorkingDirectory -cne [string]$expected.cwd){throw 'Scheduled task actions changed'}
}
$task.Enabled=$false
$folder.DeleteTask([string]$request.name,0)
$true
'''


def task_call(script,request=None):
    from .migration_windows import powershell
    env=dict(os.environ)
    if request is not None:env['HELENE_EXACT_TASK']=json.dumps(request)
    wrapped="$ErrorActionPreference='Stop';try{$value=@(& {"+script+"});ConvertTo-Json -InputObject @{ok=$true;value=$value} -Depth 12 -Compress}catch{$failure=$_.Exception;while($failure.InnerException){$failure=$failure.InnerException};ConvertTo-Json -InputObject @{ok=$false;code=$failure.HResult} -Compress}"
    result=json.loads(powershell(wrapped,env))
    if not result.get('ok'):
        code=int(result.get('code',0))&0xFFFFFFFF
        if code&0xFFFF==5:raise PermissionError(5,'Windows task access denied')
        raise RuntimeError('Windows task operation failed (code '+str(code)+')')
    return result['value']


def task_rows(result):
    """COM permits null Arguments and WorkingDirectory for ordinary tasks."""
    if not isinstance(result,list):raise ValueError('Invalid scheduled task snapshot')
    for row in result:
        for action in row['actions']:
            for field in ('executable','arguments','cwd'):
                value=action.get(field)
                if value is None:value=''
                if not isinstance(value,str):raise ValueError('Invalid scheduled task text field: '+field)
                action[field]=value
            executable,arguments=action['executable'],action['arguments']
            action['command']=('"'+executable+'"'+(' '+arguments if arguments else '')) if executable else ''
    return result


class Windows:
    def services(self):
        import psutil
        result=[]
        for service in psutil.win_service_iter():
            try:info={'name':service.name(),'command':service.binpath()}
            except psutil.NoSuchProcess:continue
            except psutil.AccessDenied as error:raise PermissionError(5,'Windows service inventory access denied') from error
            result.append(info)
        return result

    def tasks(self):
        return task_rows(task_call(TASK_SNAPSHOT))

    def autoruns(self):
        import winreg
        result=[];key_path=r'Software\Microsoft\Windows\CurrentVersion\Run'
        for hive_name in ('HKEY_CURRENT_USER','HKEY_LOCAL_MACHINE'):
            for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(getattr(winreg,hive_name),key_path,0,winreg.KEY_READ|view) as key:
                        for index in range(winreg.QueryInfoKey(key)[1]):
                            name,value,kind=winreg.EnumValue(key,index)
                            if kind in (winreg.REG_SZ,winreg.REG_EXPAND_SZ):
                                result.append({'hive':hive_name,'view':view,'key':key_path,'name':name,'command':value,'kind':kind})
                except FileNotFoundError:continue
        return result

    def remove_autorun(self,row):
        import winreg
        if row['hive'] not in ('HKEY_CURRENT_USER','HKEY_LOCAL_MACHINE') or row['key']!=r'Software\Microsoft\Windows\CurrentVersion\Run':
            raise ValueError('Invalid autorun receipt')
        with winreg.OpenKey(getattr(winreg,row['hive']),row['key'],0,winreg.KEY_QUERY_VALUE|winreg.KEY_SET_VALUE|row['view']) as key:
            try:actual=winreg.QueryValueEx(key,row['name'])
            except FileNotFoundError:return
            if actual!=(row['command'],row['kind']):raise RuntimeError('Autorun identity changed')
            winreg.DeleteValue(key,row['name'])

    def remove_task(self,row):task_call(TASK_REMOVE,row)

    def remove_service(self,row):
        """Use one SCM service handle, and refuse a changed executable binding."""
        if not row['name'] or any(char in row['name'] for char in '\0/\\'):raise ValueError('Invalid service receipt')
        api=c.WinDLL('advapi32',use_last_error=True)
        def bind(name,args,result):
            method=getattr(api,name);method.argtypes=args;method.restype=result;return method
        open_manager=bind('OpenSCManagerW',[w.LPCWSTR,w.LPCWSTR,w.DWORD],w.HANDLE)
        open_service=bind('OpenServiceW',[w.HANDLE,w.LPCWSTR,w.DWORD],w.HANDLE)
        query=bind('QueryServiceConfigW',[w.HANDLE,w.LPVOID,w.DWORD,c.POINTER(w.DWORD)],w.BOOL)
        change=bind('ChangeServiceConfigW',[w.HANDLE,*([w.DWORD]*3),w.LPCWSTR,w.LPCWSTR,c.POINTER(w.DWORD),w.LPCWSTR,w.LPCWSTR,w.LPCWSTR,w.LPCWSTR],w.BOOL)
        close=bind('CloseServiceHandle',[w.HANDLE],w.BOOL)
        delete=bind('DeleteService',[w.HANDLE],w.BOOL)
        class Config(c.Structure):
            _fields_=[('kind',w.DWORD),('start',w.DWORD),('error',w.DWORD),('image',w.LPWSTR),('group',w.LPWSTR),('tag',w.DWORD),('dependencies',w.LPWSTR),('account',w.LPWSTR),('display',w.LPWSTR)]
        class Status(c.Structure):_fields_=[(name,w.DWORD) for name in ('kind','state','accepted','exit','specific','checkpoint','hint')]
        control=bind('ControlService',[w.HANDLE,w.DWORD,c.POINTER(Status)],w.BOOL)
        query_status=bind('QueryServiceStatus',[w.HANDLE,c.POINTER(Status)],w.BOOL)
        def checked(ok):
            if not ok:raise c.WinError(c.get_last_error())
        manager=open_manager(None,None,1)
        if not manager:raise c.WinError(c.get_last_error())
        try:
            service=open_service(manager,row['name'],0x10000|1|2|4|0x20)
            if not service:
                if c.get_last_error()==1060:return
                raise c.WinError(c.get_last_error())
            try:
                buffer=c.create_string_buffer(8192);needed=w.DWORD()
                checked(query(service,buffer,len(buffer),c.byref(needed)))
                if Config.from_buffer(buffer).image!=row['command']:raise RuntimeError('Service executable binding changed')
                checked(change(service,0xFFFFFFFF,4,0xFFFFFFFF,None,None,None,None,None,None,None))
                status=Status();checked(query_status(service,c.byref(status)))
                if status.state!=1:
                    if not control(service,1,c.byref(status)) and c.get_last_error()!=1062:raise c.WinError(c.get_last_error())
                    deadline=time.monotonic()+20
                    while True:
                        checked(query_status(service,c.byref(status)))
                        if status.state==1:break
                        if time.monotonic()>=deadline:raise TimeoutError('Identified old service did not stop')
                        time.sleep(.1)
                checked(delete(service))
            finally:close(service)
        finally:close(manager)


def retire(scope,backend=None):
    if not scope.roots and not scope.data:return Plan((),(),())
    backend=backend or Windows()
    selected=plan(scope,backend.services(),backend.tasks(),backend.autoruns())
    apply(selected,backend)
    return selected
