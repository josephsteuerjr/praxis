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
    return str(target/'python.exe')


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
