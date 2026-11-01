"""Scoped, forceful Windows retirement for the one-time portable first launch."""
from __future__ import annotations
from contextlib import contextmanager
import copy
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time


def user_sid():
    api=c.WinDLL('advapi32',use_last_error=True);core=kernel();token=w.HANDLE()
    api.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)]
    api.GetTokenInformation.argtypes=[w.HANDLE,c.c_int,w.LPVOID,w.DWORD,c.POINTER(w.DWORD)]
    api.ConvertSidToStringSidW.argtypes=[w.LPVOID,c.POINTER(w.LPWSTR)]
    core.GetCurrentProcess.restype=w.HANDLE;core.LocalFree.argtypes=[w.HLOCAL]
    if not api.OpenProcessToken(core.GetCurrentProcess(),8,c.byref(token)):raise c.WinError(c.get_last_error())
    try:
        size=w.DWORD();api.GetTokenInformation(token,1,None,0,c.byref(size))
        buffer=c.create_string_buffer(size.value)
        if not api.GetTokenInformation(token,1,buffer,size,c.byref(size)):raise c.WinError(c.get_last_error())
        sid=c.cast(buffer,c.POINTER(w.LPVOID))[0];text=w.LPWSTR()
        if not api.ConvertSidToStringSidW(sid,c.byref(text)):raise c.WinError(c.get_last_error())
        try:return text.value
        finally:core.LocalFree(text)
    finally:core.CloseHandle(token)


def kernel():
    api=c.WinDLL('kernel32',use_last_error=True)
    signatures={
        'OpenProcess':([w.DWORD,w.BOOL,w.DWORD],w.HANDLE),
        'CloseHandle':([w.HANDLE],w.BOOL),
        'GetProcessTimes':([w.HANDLE,*([c.POINTER(w.FILETIME)]*4)],w.BOOL),
        'TerminateProcess':([w.HANDLE,w.UINT],w.BOOL),
        'WaitForSingleObject':([w.HANDLE,w.DWORD],w.DWORD),
        'CreateMutexW':([w.LPVOID,w.BOOL,w.LPCWSTR],w.HANDLE),
        'ReleaseMutex':([w.HANDLE],w.BOOL),
        'IsProcessCritical':([w.HANDLE,c.POINTER(w.BOOL)],w.BOOL),
    }
    for name,(arguments,result) in signatures.items():
        method=getattr(api,name);method.argtypes=arguments;method.restype=result
    return api


def terminate(pid,birth):
    """Terminate the observed kernel process object, never a recycled PID."""
    if pid in (0,4,os.getpid()):raise ValueError('Protected first-start process')
    api=kernel();handle=api.OpenProcess(0x1000|0x100000|1,False,pid)
    if not handle:
        if c.get_last_error()==87:return
        raise c.WinError(c.get_last_error())
    try:
        critical=w.BOOL()
        if not api.IsProcessCritical(handle,c.byref(critical)):raise c.WinError(c.get_last_error())
        if critical.value:raise RuntimeError('Windows critical process holds an old programme file')
        stamps=[w.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(handle,*(c.byref(item) for item in stamps)):raise c.WinError(c.get_last_error())
        observed=(stamps[0].dwHighDateTime<<32)|stamps[0].dwLowDateTime
        expected=int((birth+11644473600)*10_000_000) if isinstance(birth,float) else int(birth)
        if abs(observed-expected)>32:raise RuntimeError('Old process generation changed')
        if api.WaitForSingleObject(handle,0)==0:return
        if not api.TerminateProcess(handle,1):
            error=c.get_last_error()
            # A captured child may exit while its guardian is being killed.
            # Windows then reports ACCESS_DENIED even for this valid handle.
            # Only the same kernel object's confirmed exit admits success.
            if api.WaitForSingleObject(handle,1000)==0:return
            raise c.WinError(error)
        if api.WaitForSingleObject(handle,10000)!=0:raise RuntimeError('Windows did not release the old process')
    finally:api.CloseHandle(handle)


def _inside(value,roots):
    if not value:return False
    try:
        path=Path(value)
        if not path.is_absolute():return False
        path=Path(os.path.normpath(value))
        raw=str(path)
        if raw.upper().startswith('\\\\?\\UNC\\'):path=Path('\\\\'+raw[8:])
        elif raw.startswith('\\\\?\\'):path=Path(raw[4:])
    except (OSError,ValueError):return False
    return any(path==root or path.is_relative_to(root) for root in roots)


def owned(roots,protected=()):
    """Guardians first, then their captured descendants (including bridge/body)."""
    import psutil
    from .windows_hooks import paths,Scope,INTERPRETERS,ORGANS
    roots=paths(roots);protected=paths(protected)
    roots=tuple(Path(path).resolve() for path in roots);protected=tuple(Path(path).resolve() for path in protected);rows={};selected=set()
    scope=Scope(None,paths(roots),paths(protected))
    if hasattr(psutil.process_iter,'cache_clear'):psutil.process_iter.cache_clear()
    for process in psutil.process_iter(['pid','ppid','exe','create_time'],ad_value=None):
        info=process.info;pid=info['pid']
        if pid==os.getpid():continue
        rows[pid]=info
        # All entry scripts and --root bindings count, not only runner.py.
        # This includes a system-Python session_host that used to respawn organs.
        component=Path(info['exe']).name.casefold() if info['exe'] else ''
        candidates=INTERPRETERS|ORGANS
        if component in candidates:
            try:info['cmdline']=process.cmdline()
            except (psutil.NoSuchProcess,psutil.AccessDenied):info['cmdline']=[]
        else:info['cmdline']=[]
        # Running-process executable identity comes from the kernel query,
        # never a relative or deliberately spoofed argv[0].
        argv=[info['exe'],*info['cmdline'][1:]] if info['exe'] else []
        if scope.binds(argv):selected.add(pid)
    for data in protected:
        descriptor=Path(data)/'memory/.control/desktop-session.json'
        if not descriptor.is_file():continue
        from .package_notice import read
        value=read(descriptor);pid=value.get('pid');row=rows.get(pid)
        if row and row.get('create_time')==value.get('birth') and Path(value.get('tree','')).resolve()==Path(data).resolve():
            selected.add(pid)
    while True:
        added={pid for pid,row in rows.items() if row['ppid'] in selected
               and row.get('create_time') is not None and rows[row['ppid']].get('create_time') is not None
               and row['create_time']>=rows[row['ppid']]['create_time']}-selected
        if not added:break
        selected.update(added)
    def depth(pid):
        seen=set();parent=rows[pid]['ppid']
        while parent in selected and parent not in seen:
            seen.add(parent);parent=rows[parent]['ppid']
        return len(seen)
    result=[rows[pid] for pid in sorted(selected,key=depth)]
    if any(row.get('create_time') is None for row in result):raise RuntimeError('Old process identity unavailable')
    return result


def restart_holders(files):
    """Windows Restart Manager identifies actual file holders by PID + FILETIME."""
    if not files:return []
    class Unique(c.Structure):_fields_=[('pid',w.DWORD),('started',w.FILETIME)]
    class Info(c.Structure):
        _fields_=[('process',Unique),('name',w.WCHAR*256),('service',w.WCHAR*64),
                  ('kind',c.c_int),('status',w.ULONG),('session',w.DWORD),('restartable',w.BOOL)]
    api=c.WinDLL('rstrtmgr');session=w.DWORD();key=c.create_unicode_buffer(33)
    api.RmStartSession.argtypes=[c.POINTER(w.DWORD),w.DWORD,w.LPWSTR]
    api.RmRegisterResources.argtypes=[w.DWORD,w.UINT,c.POINTER(w.LPCWSTR),w.UINT,w.LPVOID,w.UINT,w.LPVOID]
    api.RmGetList.argtypes=[w.DWORD,c.POINTER(w.UINT),c.POINTER(w.UINT),c.POINTER(Info),c.POINTER(w.DWORD)]
    api.RmEndSession.argtypes=[w.DWORD]
    code=api.RmStartSession(c.byref(session),0,key)
    if code:raise OSError(code,'Restart Manager session failed')
    try:
        for offset in range(0,len(files),256):
            group=files[offset:offset+256];names=(w.LPCWSTR*len(group))(*(str(path) for path in group))
            code=api.RmRegisterResources(session.value,len(group),names,0,None,0,None)
            if code:raise OSError(code,'Restart Manager could not inspect old programme files')
        needed=w.UINT();count=w.UINT();reason=w.DWORD()
        code=api.RmGetList(session.value,c.byref(needed),c.byref(count),None,c.byref(reason))
        if code not in (0,234):raise OSError(code,'Restart Manager holder query failed')
        while needed.value:
            count=w.UINT(needed.value);items=(Info*count.value)()
            code=api.RmGetList(session.value,c.byref(needed),c.byref(count),items,c.byref(reason))
            if code==234:continue
            if code:raise OSError(code,'Restart Manager holder identity failed')
            return [{'pid':item.process.pid,'birth':(item.process.started.dwHighDateTime<<32)|item.process.started.dwLowDateTime,
                     'kind':item.kind,'service':item.service} for item in items[:count.value]]
        return []
    finally:api.RmEndSession(session.value)


_CUSTODY='helene-first-start-os-custody.v1'


def _entry(path):
    from .first_start import plain
    path=Path(path)
    if path.name in ('','.','..'):return plain(path)
    return plain(path.parent)/path.name


def _custody_scope(request):
    from .first_start import plain
    from .windows_hooks import Scope
    incoming=request['incoming'];roots=request['roots'];protected=request['protected']
    identities=request.get('identities',protected)
    Scope.make(incoming,roots,identities);Scope.make(incoming,[],protected)
    entry=request.get('source_entry',False)
    content=request.get('source_content',True)
    if type(entry) is not bool or type(content) is not bool:raise ValueError('Invalid source custody mode')
    result={'incoming':str(plain(incoming)),
        **{key:[str(plain(path)) for path in values] for key,values in (
            ('roots',roots),('protected',protected),('identities',identities),
            ('excluded',request.get('excluded',[])),
            ('process_excluded',request.get('process_excluded',[incoming])))},
        'source':str((_entry if entry else plain)(request['source'])) if request.get('source') else '',
        'source_entry':entry,'source_content':content}
    return result


def _custody_paths(scope):
    programmes={scope['incoming'],*scope['roots'],*scope['excluded'],*scope['process_excluded']}
    paths={*programmes,*scope['protected'],*scope['identities']}
    if scope['source']:paths.add(scope['source'])
    return paths,programmes


def _generation(path,*,programme=False,entry=False,content=True):
    """A bounded filesystem generation, never a fresh ownership discovery."""
    from .first_start import plain,receipt,linked
    path=_entry(path) if entry else plain(path)
    def identity(value):
        if (type(value.st_dev) is not int or value.st_dev<0
                or type(value.st_ino) is not int or value.st_ino<=0):
            raise ValueError('Filesystem generation is unconfirmed')
        kind='link' if stat.S_ISLNK(value.st_mode) or (entry and getattr(path,'is_junction',lambda:False)()) else 'directory' if stat.S_ISDIR(value.st_mode) else 'file' if stat.S_ISREG(value.st_mode) else ''
        if not kind:raise ValueError('Unsupported filesystem generation')
        return {'device':value.st_dev,'inode':value.st_ino,'kind':kind}
    def hashed(source):
        source=plain(source);before=source.stat()
        if not stat.S_ISREG(before.st_mode):raise ValueError('Custody file is not regular')
        with source.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        after=source.stat()
        if (identity(before)!=identity(after) or before.st_size!=after.st_size
                or before.st_mtime_ns!=after.st_mtime_ns):
            raise ValueError('Custody file changed while measuring it')
        return digest
    try:
        try:before=path.lstat() if entry else path.stat()
        except FileNotFoundError:return {'kind':'absent'}
        result=identity(before)
        if result['kind']=='link':result['target']=os.readlink(path)
        elif result['kind']=='file':
            if content:result['sha256']=hashed(path)
            else:result.update(size=before.st_size,modified_ns=before.st_mtime_ns)
        elif programme:
            # Archive wrappers can contain several admitted programme roots.
            # Bind their small receipts, never copy/hash runtime trees here.
            result['receipts']={};todo=[(path,0)]
            skip={'app','runtime','tree','code','.git','verification','data','memory','components','node_modules','.helene'}
            while todo:
                directory,depth=todo.pop();stamp=directory.stat()
                try:
                    digest=hashed(receipt(directory))
                except FileNotFoundError:pass
                else:
                    result['receipts'][receipt(directory).relative_to(path).as_posix()]={
                        'directory':identity(stamp),'sha256':digest}
                if identity(directory.stat())!=identity(stamp):raise ValueError('Receipt directory generation changed')
                if depth<3:
                    todo.extend((child,depth+1) for child in directory.iterdir()
                        if child.name.casefold() not in skip and not linked(child) and child.is_dir())
        if identity(path.lstat() if entry else path.stat())!=identity(before):raise ValueError('Filesystem generation changed')
        return result
    except OSError as exc:
        raise ValueError('Filesystem custody is unconfirmed: '+str(path)) from exc


def _capture_custody(request):
    scope=_custody_scope(request);paths,programmes=_custody_paths(scope)
    return {'format':_CUSTODY,'scope':scope,
        'generations':{path:_generation(path,programme=path in programmes,
            entry=scope['source_entry'] and path==scope['source'],
            content=scope['source_content'] if path==scope['source'] else True) for path in sorted(paths)}}


def _verify_custody(request,captured=None):
    captured=captured if captured is not None else request.get('custody')
    scope=_custody_scope(request);paths,programmes=_custody_paths(scope)
    if (not isinstance(captured,dict) or captured.get('format')!=_CUSTODY
            or captured.get('scope')!=scope or not isinstance(captured.get('generations'),dict)
            or set(captured['generations'])!=paths):
        raise ValueError('Scoped OS custody is missing or differs')
    for path in sorted(paths):
        if _generation(path,programme=path in programmes,
                entry=scope['source_entry'] and path==scope['source'],
                content=scope['source_content'] if path==scope['source'] else True)!=captured['generations'][path]:
            raise ValueError('Scoped OS target generation changed: '+path)
    return captured


class Backend:
    def __init__(self,root):
        self.root=Path(root).resolve();self.roots=[];self.protected=[];self.keep=[];self.excluded=[];self.process_excluded=[self.root];self._hooks_cleared=None;self._registrations=[];self._pruning=False;self._elevated=False

    def removable(self,path):
        path=Path(path).resolve()
        if any(path==p or path.is_relative_to(p) or p.is_relative_to(path) for p in map(lambda value:Path(value).resolve(),self.excluded)):
            raise ValueError('Нельзя удалить программу, защищённую квитанцией')

    def stoppable(self,path):
        path=Path(path).resolve()
        if any(path==p or path.is_relative_to(p) or p.is_relative_to(path) for p in map(lambda value:Path(value).resolve(),self.process_excluded)):
            raise ValueError('Cannot stop the incoming programme')

    def registered(self,places):
        import winreg
        from .first_start import branded
        bases=(*places.appdata,*places.program_files);found=[]
        prefix=r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
        for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive,prefix,0,winreg.KEY_READ|view) as parent:
                        names=[winreg.EnumKey(parent,i) for i in range(winreg.QueryInfoKey(parent)[0])]
                    for name in names:
                        try:
                            with winreg.OpenKey(hive,prefix+'\\'+name,0,winreg.KEY_READ|view) as key:
                                title=winreg.QueryValueEx(key,'DisplayName')[0];location=winreg.QueryValueEx(key,'InstallLocation')[0]
                            old=Path(location).resolve()
                            if (branded(str(title)) and old!=self.root and _inside(old,bases)
                                    and all(old!=Path(base).resolve() for base in bases)):
                                found.append(old);self._registrations.append((old,hive,view,prefix+'\\'+name))
                        except (FileNotFoundError,PermissionError):continue
                except FileNotFoundError:continue
        return list(dict.fromkeys(found))

    def custody(self,path=None,*,roots=None,identities=None,entry=False,content=True):
        admitted=getattr(self,'_admitted_custody',None)
        if admitted is not None:_verify_custody(admitted['scope'],admitted)
        request={'incoming':str(self.root),'roots':[str(p) for p in (getattr(self,'roots',[]) if roots is None else roots)],
            'protected':[str(p) for p in getattr(self,'keep',[])],
            'identities':[str(p) for p in (getattr(self,'protected',[]) if identities is None else identities)],
            'excluded':[str(p) for p in getattr(self,'excluded',[])],
            'process_excluded':[str(p) for p in getattr(self,'process_excluded',[self.root])],
            'source':str(path) if path is not None else '', 'source_entry':entry,'source_content':content}
        captured=_capture_custody(request)
        if admitted is not None:
            # Derived prune children can gain only a bounded child operand;
            # every original scope generation remains the admitted one.
            previous=admitted['scope'];current=captured['scope']
            if any(current[key]!=previous[key] for key in previous if key not in ('source','source_entry')):
                raise ValueError('Elevated scope changed from its original custody')
            if current['source'] and current['source']!=previous['source']:
                base=Path(previous['source']) if previous['source'] else None
                if base is None or not Path(current['source']).is_relative_to(base):
                    raise ValueError('Elevated child leaves its admitted source')
            _verify_custody(previous,admitted)
            for raw,generation in admitted['generations'].items():
                if raw in captured['generations']:captured['generations'][raw]=copy.deepcopy(generation)
        return captured

    def _confirmed_removed(self,path):
        """Advance only an entry whose deletion this worker just completed."""
        admitted=getattr(self,'_admitted_custody',None)
        if admitted is None:return
        path=_entry(path)
        if os.path.lexists(path):raise ValueError('Deleted entry was replaced')
        for raw,generation in admitted['generations'].items():
            root=Path(raw)
            if root==path or root.is_relative_to(path):
                admitted['generations'][raw]={'kind':'absent'}
            elif generation.get('kind')=='directory' and 'receipts' in generation:
                generation['receipts']={name:row for name,row in generation['receipts'].items()
                    if root/name!=path and not (root/name).is_relative_to(path)}

    @contextmanager
    def _bound(self,captured):
        previous=getattr(self,'_admitted_custody',None)
        if previous is None:self._admitted_custody=captured
        try:yield
        finally:self._admitted_custody=previous

    def privileged(self,action,path=None,*,_captured=None,**values):
        from . import migration_windows as windows
        captured=_captured if _captured is not None else self.custody(path,entry=action=='remove')
        request={'action':action,**captured['scope'],**values,'custody':captured}
        expected_source=str(_entry(path) if captured['scope']['source_entry'] else Path(path).resolve()) if path is not None else ''
        if request['source']!=expected_source:
            raise ValueError('Scoped OS source differs from captured custody')
        _verify_custody(request)
        python=self.root/'runtime/python.exe';helper=self.root/'app/localharness/windows_first_start.py'
        command=[str(python),'-B','-s',str(helper),'--request',json.dumps(request)]
        if not python.is_file() or not helper.is_file():raise RuntimeError('В архиве отсутствует помощник удаления прежней программы')
        if windows.admin():
            result=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000,timeout=180)
            if result.returncode:raise RuntimeError('Windows не завершила удаление прежних файлов')
        else:
            env=dict(os.environ,HELENE_FIRST_PYTHON=str(python),HELENE_FIRST_ARGUMENTS=subprocess.list2cmdline(command[1:]))
            windows.run_privileged(r"$ErrorActionPreference='Stop';$p=Start-Process -FilePath $env:HELENE_FIRST_PYTHON -ArgumentList $env:HELENE_FIRST_ARGUMENTS -WindowStyle Hidden -Wait -PassThru;if($p.ExitCode -ne 0){throw 'Scoped first-start operation failed'}",env)

    def data_access(self,data):
        # PF profiles can contain SYSTEM-created private files. Test access
        # after shutdown; grant only the original interactive owner's SID.
        captured=self.custody(data)
        try:
            import tempfile
            descriptor,name=tempfile.mkstemp(prefix='.helene-access-',dir=data);os.close(descriptor);os.unlink(name)
            for here,dirs,names in os.walk(data,followlinks=False):
                for name in names:
                    with (Path(here)/name).open('rb') as stream:stream.read(1)
        except PermissionError:self.privileged('data-access',data,_captured=captured,sid=user_sid())

    @contextmanager
    def exclusive(self,data):
        api=kernel();identity=hashlib.sha256(str(Path(data).resolve()).casefold().encode()).hexdigest()
        handle=api.CreateMutexW(None,False,'Local\\HeleneFirstStart-'+identity)
        if not handle:raise c.WinError(c.get_last_error())
        acquired=False
        try:
            observed=api.WaitForSingleObject(handle,0)
            if observed not in (0,0x80):raise RuntimeError('Подготовка Hélène уже идёт в другом окне')
            acquired=True;yield
        finally:
            if acquired:api.ReleaseMutex(handle)
            api.CloseHandle(handle)

    def hooks(self,roots,protected=()):
        from . import windows_hooks
        from .first_start import plain
        for path in [*roots,*protected]:self.stoppable(path)
        scope=windows_hooks.Scope.make(self.root,roots,protected)
        for item in (*scope.roots,*scope.data):plain(item)
        captured=self.custody(roots=roots,identities=protected)
        native=windows_hooks.Windows()
        class Guarded:
            def __getattr__(self,name):
                method=getattr(native,name)
                if name in ('remove_service','remove_task','remove_autorun'):
                    def guarded(row):
                        _verify_custody(captured['scope'],captured)
                        return method(row)
                    return guarded
                return method
        try:return windows_hooks.retire(scope,Guarded())
        except PermissionError:
            if self._elevated:raise
            self.privileged('hooks',_captured=captured)

    def stop(self,roots,protected=()):
        self.roots=list(roots);self.protected=list(protected)
        if getattr(self,'_admitted_custody',None) is None:self.keep=list(protected)
        captured=self.custody()
        binding=(tuple(sorted(set(map(str,roots)))),tuple(sorted(set(map(str,protected)))))
        if self._hooks_cleared!=binding:self.hooks(roots,protected);self._hooks_cleared=binding
        quiet=0
        for attempt in range(12):
            _verify_custody(captured['scope'],captured)
            processes=owned(roots,protected)
            if not processes:
                quiet+=1
                if quiet>=3:return
            else:
                quiet=0
                for row in processes:
                    _verify_custody(captured['scope'],captured)
                    try:terminate(row['pid'],row['create_time'])
                    except PermissionError:
                        if self._elevated:raise
                        self.privileged('terminate',_captured=captured,pid=row['pid'],birth=row['create_time'])
            time.sleep(.2)
        raise RuntimeError('Прежний супервизор продолжает перезапускать body/bridge')

    def assert_quiet(self,roots,protected=()):
        if owned(roots,[*self.protected,*protected]):raise RuntimeError('Прежняя Hélène ещё удерживает данные')

    def committed_data(self,target):self.keep=[Path(target)]

    def release(self,path,*,_captured=None):
        """Force actual old-file holders closed; never infer liveness from age."""
        from .first_start import plain
        path=plain(path);self.removable(path)
        if not path.exists():return
        captured=_captured if _captured is not None else self.custody(path,content=False)
        _verify_custody(captured['scope'],captured)
        files=[path] if path.is_file() else [p for p in path.rglob('*') if p.is_file() and not p.is_symlink()]
        released=0
        excluded={row['pid'] for row in owned(self.process_excluded)} if self.process_excluded else set()
        for row in restart_holders(files):
            _verify_custody(captured['scope'],captured)
            if row['pid']==os.getpid():continue
            if row['pid'] in excluded:raise RuntimeError('Защищённая программа удерживает прежние файлы')
            # Critical Windows components are never part of Helene. A temporary
            # scanner overlap is retried by remove(); it cannot earn success.
            if row['kind'] in (3,1000):raise RuntimeError('Системная служба удерживает прежние файлы: '+row['service'])
            try:terminate(row['pid'],row['birth'])
            except PermissionError:
                if self._elevated:raise
                self.privileged('terminate',path,_captured=captured,pid=row['pid'],birth=row['birth'])
            released+=1
        return released

    def retire(self,old):
        self.removable(old)
        import winreg
        from .migration_windows import system_action
        for bound,hive,view,name in self._registrations:
            if bound!=old:continue
            try:
                with winreg.OpenKey(hive,name,0,winreg.KEY_READ|view) as key:
                    current=Path(winreg.QueryValueEx(key,'InstallLocation')[0]).resolve()
                if current!=old:raise RuntimeError('Previous installation registration changed')
                winreg.DeleteKeyEx(hive,name,view,0)
            except FileNotFoundError:pass
            except PermissionError:system_action(old,'cleanup')

    def prune(self,old,protected):
        self.removable(old)
        from .first_start import _prune_program
        captured=self.custody(old)
        with self._bound(captured):
            self._pruning=True
            try:
                try:_prune_program(old,protected,self)
                except PermissionError:self.privileged('prune',old,_captured=captured,keep=[str(p) for p in protected])
            finally:self._pruning=False

    def remove(self,path):
        path=Path(path)
        self.removable(path)
        if path==self.root or self.root.is_relative_to(path):raise ValueError('Cannot remove the incoming programme')
        captured=self.custody(path,entry=True)
        with self._bound(captured):return self._remove_captured(path,captured)

    def _remove_captured(self,path,captured):
        def writable(function,name,_error):
            os.chmod(name,stat.S_IWRITE|stat.S_IREAD);function(name)
        failure=None
        for attempt in range(3):
            _verify_custody(captured['scope'],captured)
            try:
                if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):path.unlink()
                elif path.is_dir():shutil.rmtree(path,onerror=writable)
                elif path.exists():path.unlink()
                self._confirmed_removed(path)
                return
            except OSError as exc:
                failure=exc;_verify_custody(captured['scope'],captured)
                self.stop(self.roots,self.protected);released=self.release(path,_captured=captured)
                if isinstance(exc,PermissionError) and not released:
                    if self._pruning or self._elevated:raise
                    self.privileged('remove',path,_captured=captured);self._confirmed_removed(path);return
                time.sleep(.1)
        raise RuntimeError('Windows не освободила прежнюю папку: '+str(path)) from failure

    def shortcuts(self,roots,current):
        from .migration_windows import shortcut_folders,powershell,link_belongs,unpin_shortcut,install_shortcuts,run_privileged
        # Resolve every shortcut in one COM session, not one PowerShell per
        # link per former installation (which used to take minutes).
        folders=[str(p) for p in shortcut_folders() if Path(p).is_dir()]
        env=dict(os.environ,HELENE_FIRST_ROOTS=json.dumps(folders))
        script=r'''$ErrorActionPreference='Stop'
$w=New-Object -ComObject WScript.Shell
$result=@(foreach($folder in @($env:HELENE_FIRST_ROOTS|ConvertFrom-Json)){
 Get-ChildItem -LiteralPath $folder -Filter *.lnk -File -Recurse -ErrorAction SilentlyContinue|ForEach-Object {
 $s=$w.CreateShortcut($_.FullName);[pscustomobject]@{path=$_.FullName;target=$s.TargetPath;arguments=$s.Arguments;cwd=$s.WorkingDirectory}}})
ConvertTo-Json -InputObject @($result) -Compress -Depth 3
'''
        rows=json.loads(powershell(script,env)) if folders else []
        for row in rows:
            if not any(link_belongs(row,root) for root in roots):continue
            path=Path(row['path']);unpin_shortcut(path)
            try:path.unlink()
            except PermissionError:
                run_privileged("$ErrorActionPreference='Stop';Remove-Item -LiteralPath $env:HELENE_LINK -Force",dict(os.environ,HELENE_LINK=str(path)))
        if current is not None:install_shortcuts(current,desktop_only=True)


def privileged_action(request):
    """Narrow UAC worker. No model, old code import, config, journal or GUI."""
    from .first_start import plain,_prune_program
    from .windows_hooks import Scope
    # Validate raw values before plain() can turn an empty string into cwd.
    Scope.make(request['incoming'],request['roots'],request.get('identities',request['protected']))
    Scope.make(request['incoming'],[],request['protected'])
    incoming=plain(request['incoming']);backend=Backend(incoming)
    roots=[plain(p) for p in request['roots']];protected=[plain(p) for p in request['protected']]
    if any(incoming==root or incoming.is_relative_to(root) for root in roots):raise ValueError('Incoming programme is protected')
    identities=[plain(p) for p in request.get('identities',request['protected'])]
    backend.roots=roots;backend.protected=identities;backend.keep=protected;backend._elevated=True
    backend.excluded=[plain(p) for p in request.get('excluded',[])]
    backend.process_excluded=[plain(p) for p in request.get('process_excluded',[incoming])]
    action=request['action'];entry=action=='remove' and request.get('source_entry') is True
    source=(_entry if entry else plain)(request['source']) if request.get('source') else None
    contained=(any(source==root or source.is_relative_to(root) for root in [*roots,*protected])
               if entry and source is not None else _inside(source,[*roots,*protected]) if source is not None else True)
    if not contained:raise ValueError('Privileged path leaves declared scope')
    _verify_custody(request)
    backend._admitted_custody=copy.deepcopy(request['custody'])
    if request['custody']['scope']['source_content'] is not True and action!='terminate':
        raise ValueError('Metadata-only custody permits typed owner termination only')
    if action=='hooks':backend.hooks(roots,identities)
    elif action=='data-access':
        if source not in protected:raise ValueError('Data grant target differs')
        sid=request['sid']
        import re
        if not re.fullmatch(r'S-1-[0-9-]+',sid):raise ValueError('Invalid owner SID')
        _verify_custody(request)
        subprocess.run(['icacls',str(source),'/grant:r','*'+sid+':(OI)(CI)F','/T','/C','/Q'],check=True,
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
    elif action=='prune':
        if source not in roots:raise ValueError('Programme root differs')
        keep=[plain(p) for p in request['keep']]
        if any(p not in protected for p in keep):raise ValueError('Preserved data scope differs')
        _verify_custody(request)
        _prune_program(source,keep,backend)
    elif action=='remove':
        if any(source==p or source.is_relative_to(p) or p.is_relative_to(source) for p in protected):
            raise ValueError('Cannot remove preserved data')
        _verify_custody(request)
        backend.remove(source)
    elif action=='terminate':
        pid=request['pid'];birth=request['birth']
        if backend.process_excluded and any(row['pid']==pid for row in owned(backend.process_excluded)):
            raise ValueError('Cannot terminate a receipt-protected programme')
        admitted=any(row['pid']==pid and row['create_time']==birth for row in owned(roots,identities))
        if not admitted and source is not None:
            files=[source] if source.is_file() else [p for p in source.rglob('*') if p.is_file()]
            admitted=any(row['pid']==pid and row['birth']==birth and row['kind'] not in (3,1000) for row in restart_holders(files))
        if not admitted:raise ValueError('Privileged process is not an identified old-file owner')
        _verify_custody(request)
        terminate(pid,birth)
    else:raise ValueError('Unknown first-start OS operation')


if __name__=='__main__':
    import argparse,sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]));__package__='localharness'
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True)
    args=parser.parse_args()
    try:privileged_action(json.loads(args.request))
    except Exception:raise SystemExit(1)
