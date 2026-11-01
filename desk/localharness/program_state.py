"""Private, program-bound mechanical state; no new sibling litter."""
from pathlib import Path
import os,re


def _identity(value):
    if not isinstance(value,str) or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}',value):
        raise ValueError('Invalid operation identity')
    return value


def _plain(path):
    path=Path(os.path.abspath(path))
    for item in (path,*path.parents):
        if item.is_symlink() or (hasattr(item,'is_junction') and item.is_junction()):
            raise ValueError('Linked programme-state path')
    return path


def hidden(path):
    path=Path(path)
    if os.name=='nt' and path.exists():
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetFileAttributesW.argtypes=[ctypes.c_wchar_p];kernel.GetFileAttributesW.restype=ctypes.c_ulong
        kernel.SetFileAttributesW.argtypes=[ctypes.c_wchar_p,ctypes.c_ulong];kernel.SetFileAttributesW.restype=ctypes.c_int
        flags=kernel.GetFileAttributesW(str(path))
        if flags==0xffffffff or not kernel.SetFileAttributesW(str(path),flags|2):raise ctypes.WinError(ctypes.get_last_error())
    return path


def tidy_program(program):
    """Hide only declared distribution/support entries, never unknown user files."""
    if os.name!='nt':return []
    from .package_notice import read
    program=_plain(program)
    inventory=read(program/'python-package-files.json').get('files') or {}
    names=set()
    for name in inventory:
        parts=Path(name).parts
        if not parts or Path(name).is_absolute() or '..' in parts:raise ValueError('Invalid package visibility inventory')
        names.add(parts[0])
    names.update(('.helene','python-package-files.json','python-migration.json','data-relocation.json',
                  'helene.json','portable-program.json','agent-default.json','window-preferences.json',
                  'owner-stop.json','helene-bootstrap.json','code','code-backup','.source-updates',
                  'code-transition.json','code-transition-plan.json','old-program-retirement.json',
                  'source-materials.json','source-materials-retired.json','.webview','.migration-webview'))
    changed=[]
    for name in sorted(names-{'helene.exe','Helene.exe','data','memory','agents','profile','backups'}):
        path=program/name
        if path.exists():
            _plain(path);hidden(path);changed.append(name)
    return changed


def window_control(program,pid,*,role='window',create=False):
    if role not in ('window','migrator') or not isinstance(pid,int) or pid<=0:raise ValueError('Invalid window control identity')
    name=f'.python-{role}-{pid}.json'
    canonical=directory(program,'windows',create=create)/name
    legacy=Path(program).resolve()/name
    return legacy if not create and not canonical.exists() and legacy.is_file() else canonical


def window_controls(program):
    base=directory(program,'windows',create=False)
    return [*base.glob('.python-window-*.json'),*Path(program).glob('.python-window-*.json')]


def root(program,*,create=True):
    program=_plain(program);base=program/'.helene'
    if base.is_symlink() or (hasattr(base,'is_junction') and base.is_junction()):raise ValueError('Linked private programme state')
    if create:base.mkdir(parents=True,exist_ok=True);hidden(base)
    return base


def directory(program,name,*,create=True):
    if not isinstance(name,str) or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}',name):
        raise ValueError('Invalid programme-state category')
    path=root(program,create=create)/name
    if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):raise ValueError('Linked programme-state directory')
    if create:path.mkdir(parents=True,exist_ok=True)
    return path


def progress(program,identity,*,create=True):
    identity=_identity(identity)
    return directory(program,'progress',create=create)/(identity+'.json')


def progress_belongs(program,path):
    program,path=_plain(program),_plain(path)
    if path.parent==directory(program,'progress',create=False) and path.suffix=='.json':
        _identity(path.stem);return True
    if path.parent==program.parent and path.name.startswith('.helene-update-') and path.name.endswith('.progress.json'):
        _identity(path.name[len('.helene-update-'):-len('.progress.json')]);return True
    return False


def staging(program,identity):
    identity=_identity(identity)
    if os.name=='nt':
        from .first_start import ready,branded
        if ready(program):
            parent=Path(program).resolve().parent
            if Path(program).name.casefold()=='helene' and branded(parent.name):parent=parent.parent
            return parent/('Helene-update-'+identity)
    return directory(program,'updates')/('.helene-update-'+identity)


def staging_belongs(program,path):
    program,path=_plain(program),_plain(path)
    if path.name.startswith('Helene-update-'):
        identity=_identity(path.name[len('Helene-update-'):])
        return os.name=='nt' and path==staging(program,identity)
    if not path.name.startswith('.helene-update-'):return False
    _identity(path.name[len('.helene-update-'):])
    return path.parent in (directory(program,'updates',create=False),program.parent)


def recovery(program,identity):
    identity=_identity(identity)
    return directory(program,'recovery')/('.helene-recovery-'+identity)


def recovery_belongs(program,path,identity):
    path,program=_plain(path),_plain(program)
    return path.name=='.helene-recovery-'+_identity(identity) and path.parent in (
        directory(program,'recovery',create=False),program.parent)


def migration(program,identity,*,create=False):
    identity=_identity(identity)
    return directory(program,'migrations',create=create)/('.helene-migration-'+identity)


def migration_belongs(program,path):
    path,program=_plain(path),_plain(program)
    if not path.name.startswith('.helene-migration-'):return False
    _identity(path.name[len('.helene-migration-'):])
    return path.parent in (directory(program,'migrations',create=False),program.parent)
