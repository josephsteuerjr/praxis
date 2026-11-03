"""Private Windows Python layout with ordinary -S/-I and bound SDK bytes.

Embedded ._pth overrides command-line site flags and PYTHONPATH. Verification
uses exact copies of its Python/SDK closure, without the program's ._pth or
source paths. Root platform sidecars (including portable BusyBox) stay adjacent
to Python: Windows searches System32 before PATH for a bare child executable.
The Git distribution, models and owner state are never copied.
"""
from pathlib import Path
import hashlib,json,os,re,stat,sys


def describe(runtime,python):
    if sys.platform!='win32':return None
    root=Path(runtime['root']).resolve();python=Path(python).resolve()
    files=runtime['files']
    pths=[name for name in files if '/' not in name and name.endswith('._pth')]
    if not pths:return None
    if (len(pths)!=1 or not re.fullmatch(r'python\d+\._pth',pths[0])
            or python!=root/'python.exe'):
        raise ValueError('Unsupported embedded verification Python layout')
    stem=pths[0][:-5]
    selected={name:row for name,row in files.items()
        if '/' in name and name.split('/',1)[0] not in {'git','bin','Scripts'} or '/' not in name and
        Path(name).suffix.lower() in {'.exe','.dll','.pyd','.zip','.py'}}
    if not {'python.exe',stem+'.dll',stem+'.zip'}<=selected.keys():
        raise ValueError('Incomplete embedded verification Python closure')
    if any(name.endswith(('.pth','._pth')) for name in selected):
        raise ValueError('Executable site configuration is not a declared verification input')
    return {'format':'helene-private-python.v1','root':str(root),'files':selected,
            'source_pth':pths[0],'helper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def _registry():
    # With an explicit PYTHONHOME, CPython ignores the core default value, but
    # extension subkeys still precede site-packages. Refuse those ambient paths
    # before any unisolated child starts; never change the owner's registry.
    import winreg
    name='SOFTWARE\\Python\\PythonCore\\'+sys.winver+'\\PythonPath'
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        try:key=winreg.OpenKeyEx(hive,name)
        except FileNotFoundError:continue
        with key:
            for index in range(winreg.QueryInfoKey(key)[0]):
                child=winreg.EnumKey(key,index)
                with winreg.OpenKeyEx(key,child) as entry:
                    try:value=winreg.QueryValueEx(entry,None)[0]
                    except FileNotFoundError:continue
                    if value:raise ValueError('Ambient Python registry extension paths prevent isolated verification')


def provision(layout,target,stopped=lambda:False,progress=None):
    if layout is None:return None
    _registry()
    root=Path(layout['root']).resolve();target=Path(target)
    if target.exists() or target.is_symlink():raise ValueError('Private Python target already exists')
    target.mkdir(parents=True)
    for name,row in sorted(layout['files'].items()):
        source=root/name;out=target/name
        if stopped():raise InterruptedError('Owner stopped private Python preparation')
        info=source.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('Linked/special Python verification input: '+name)
        out.parent.mkdir(parents=True,exist_ok=True)
        digest=hashlib.sha256();count=0
        with source.open('rb') as src,out.open('xb') as dst:
            while chunk:=src.read(1024*1024):
                if stopped():raise InterruptedError('Owner stopped private Python preparation')
                digest.update(chunk);dst.write(chunk);count+=len(chunk)
        if digest.hexdigest()!=row['sha256']:raise ValueError('Python verification input changed: '+name)
        if progress:progress(count,1)
    if stopped():raise InterruptedError('Owner stopped private Python preparation')
    _prevent_bytecode(layout,target,stopped)
    if stopped():raise InterruptedError('Owner stopped private Python cache preparation')
    return str(target/'python.exe')


def _prevent_bytecode(layout,target,stopped=lambda:False):
    """Disable automatic cache writes only in fresh, private SDK cache folders.

    Python treats an unwritable __pycache__ as optional. Keep ordinary imports,
    SDK source bytes and strict post-run inventory unchanged, even for children
    that ignore inherited Python environment options. Delete rights stay intact.
    """
    if os.name!='nt':return
    import ctypes as c
    from ctypes import wintypes as w
    class Trustee(c.Structure):
        _fields_=[('multiple',w.LPVOID),('operation',w.DWORD),('form',w.DWORD),('kind',w.DWORD),('sid',w.LPVOID)]
    class Access(c.Structure):
        _fields_=[('rights',w.DWORD),('mode',w.DWORD),('inheritance',w.DWORD),('trustee',Trustee)]
    kernel=c.WinDLL('kernel32',use_last_error=True);api=c.WinDLL('advapi32',use_last_error=True)
    kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE];kernel.CreateFileW.restype=w.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes=[w.HANDLE,c.c_int,w.LPVOID,w.DWORD];kernel.GetFileInformationByHandleEx.restype=w.BOOL
    kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle.restype=w.BOOL
    kernel.LocalFree.argtypes=[w.HANDLE];kernel.LocalFree.restype=w.HANDLE
    api.ConvertStringSidToSidW.argtypes=[w.LPCWSTR,c.POINTER(w.LPVOID)];api.ConvertStringSidToSidW.restype=w.BOOL
    api.GetSecurityInfo.argtypes=[w.HANDLE,c.c_int,w.DWORD,w.LPVOID,w.LPVOID,c.POINTER(w.LPVOID),w.LPVOID,c.POINTER(w.LPVOID)];api.GetSecurityInfo.restype=w.DWORD
    api.SetEntriesInAclW.argtypes=[w.ULONG,c.POINTER(Access),w.LPVOID,c.POINTER(w.LPVOID)];api.SetEntriesInAclW.restype=w.DWORD
    api.SetSecurityInfo.argtypes=[w.HANDLE,c.c_int,w.DWORD,w.LPVOID,w.LPVOID,w.LPVOID,w.LPVOID];api.SetSecurityInfo.restype=w.DWORD
    sid=w.LPVOID()
    if not api.ConvertStringSidToSidW('S-1-1-0',c.byref(sid)):raise c.WinError(c.get_last_error())
    try:
        parents=set()
        for name in layout['files']:
            if not name.endswith('.py'):continue
            relative=Path(name)
            if relative.is_absolute() or '..' in relative.parts or ':' in name or '\\' in name:
                raise ValueError('Invalid private SDK source path')
            parents.add(str(relative.parent))
        parents=sorted(parents)
        for parent in parents:
            if stopped():raise InterruptedError('Owner stopped private Python cache preparation')
            cache=Path(target)/parent/'__pycache__'
            # The SDK itself is a new copy; existing cache entries are not inputs.
            cache.mkdir(exist_ok=False)
            handle=kernel.CreateFileW(str(cache),0x20000|0x40000,3,None,3,0x02000000|0x00200000,None)
            if handle==c.c_void_p(-1).value:raise c.WinError(c.get_last_error())
            descriptor=w.LPVOID();old_acl=w.LPVOID();new_acl=w.LPVOID()
            try:
                attributes=(w.DWORD*2)()
                if not kernel.GetFileInformationByHandleEx(handle,9,c.byref(attributes),c.sizeof(attributes)):raise c.WinError(c.get_last_error())
                if attributes[0]&0x400 or not attributes[0]&0x10:raise ValueError('Linked or nondirectory SDK cache')
                code=api.GetSecurityInfo(handle,1,4,None,None,c.byref(old_acl),None,c.byref(descriptor))
                if code:raise c.WinError(code)
                if not old_acl:raise ValueError('SDK cache has no ordinary inherited ACL')
                # Exact rights only: FILE_ADD_FILE/FILE_ADD_SUBDIRECTORY. No
                # generic write mask (which would also deny read synchronization).
                entry=Access(0x6,3,0,Trustee(None,0,0,5,sid))
                code=api.SetEntriesInAclW(1,c.byref(entry),old_acl,c.byref(new_acl))
                if code:raise c.WinError(code)
                code=api.SetSecurityInfo(handle,1,4,None,None,new_acl,None)
                if code:raise c.WinError(code)
            finally:
                if new_acl:kernel.LocalFree(new_acl)
                if descriptor:kernel.LocalFree(descriptor)
                kernel.CloseHandle(handle)
    finally:kernel.LocalFree(sid)


def verify(layout,target,stopped=lambda:False):
    if layout is None:return
    target=Path(target);found=set()
    for directory,dirs,names in os.walk(target):
        for name in [*dirs,*names]:
            p=Path(directory)/name;info=p.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise ValueError('Linked private Python input')
        for name in names:
            path=Path(directory)/name;relative=path.relative_to(target).as_posix();found.add(relative)
            expected=layout['files'].get(relative)
            if expected is None:raise ValueError('Undeclared private Python input: '+relative)
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                while chunk:=stream.read(1024*1024):
                    if stopped():raise InterruptedError('Owner stopped private Python verification')
                    digest.update(chunk)
            if digest.hexdigest()!=expected['sha256']:raise ValueError('Tests changed private Python input: '+relative)
    if found!=set(layout['files']):raise ValueError('Tests removed private Python inputs')
