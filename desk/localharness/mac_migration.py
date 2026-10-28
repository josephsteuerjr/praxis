"""Confirmed Mac profile handoff. No signals, uninstaller, broker or source deletion.

Legacy Rust owners did not share Python's flock. Observe actual same-user
executable/argv/cwd paths AND hold data-tree flocks through final capture.
A legacy owner can still be manually reopened; a final census detects this,
not an invented claim of exclusion against non-cooperating legacy programs.
"""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time

from . import app_migration
from .essential_backup import load


def installation(path):
    path=Path(path).expanduser().resolve(strict=True)
    if path.suffix=='.app':path=path.parent
    if not (path/'helene.json').is_file():raise ValueError('Choose the old Helene folder or its Helene.app, containing helene.json')
    return path


def references(value, roots):
    if not isinstance(value,str) or not Path(value).is_absolute():return False
    # argv entries are already tokenized by the OS. Handle --root=/path too.
    path=Path(value).resolve()
    return any(path==root or path.is_relative_to(root) for root in roots)


def quiescent(roots):
    """Read OS identities, not PID files or product-name substring matches."""
    if sys.platform!='darwin':raise RuntimeError('Mac migration process admission requires native macOS')
    import psutil
    roots=tuple(Path(p).resolve() for p in roots)
    uid=os.getuid()
    for pid in psutil.pids():
        if pid==os.getpid():continue
        try:
            process=psutil.Process(pid)
            ids=process.uids()
            # setuid/elevated processes belong to another effective principal;
            # macOS denies their cwd even with our real UID (e.g. runner sudo).
            # Registered old system jobs are checked through launchd separately.
            if ids.real!=uid or ids.effective!=uid:continue
            values=[process.exe(),process.cwd(),*process.cmdline()]
            values += [v.split('=',1)[1] for v in values if v.startswith('--') and '=' in v]
            if any(references(v,roots) for v in values):
                raise RuntimeError(f'Old installation still in use by process {pid}; Quit it and disable its login/service start, then retry')
        except psutil.NoSuchProcess:continue
        except psutil.AccessDenied as exc:
            facts={}
            for field in ('name','status','ppid','exe','cwd'):
                try:facts[field]=getattr(process,field)()
                except (psutil.Error,OSError) as error:facts[field]=type(error).__name__
            if facts.get('status')==psutil.STATUS_ZOMBIE:continue
            raise RuntimeError(f'Cannot inspect process {pid}; old installation closure is unverified: {facts}') from exc


def integrations_quiet(roots):
    """Refuse loaded launchd jobs bound to selected paths; never unload them."""
    import plistlib
    for directory,domain in ((Path.home()/'Library/LaunchAgents',f'gui/{os.getuid()}'),
                             (Path('/Library/LaunchDaemons'),'system')):
        for path in directory.glob('*.plist'):
            try:value=plistlib.loads(path.read_bytes())
            except (OSError,ValueError,plistlib.InvalidFileException):continue
            args=[value.get('Program',''),*value.get('ProgramArguments',[]),value.get('WorkingDirectory','')]
            if not any(references(v,roots) for v in args):continue
            label=value.get('Label')
            if not isinstance(label,str):raise ValueError('Selected launchd integration has no readable label')
            result=subprocess.run(['launchctl','print',domain+'/'+label],capture_output=True,timeout=10)
            if result.returncode==0:
                raise RuntimeError('Disable the old login/service integration before migration: '+str(path))
            # launchctl's not-found answer is distinct from permission/transport failure.
            error=(result.stderr+result.stdout).decode('utf-8',errors='replace')
            if 'Could not find service' not in error and 'Could not find domain' not in error:
                raise RuntimeError('Cannot establish launchd integration state: '+str(path))


@contextmanager
def lease(tree):
    """Real OS lease: existing bytes unchanged; create a missing empty lease file."""
    import fcntl
    tree=Path(tree).resolve(strict=True)
    fd=os.open(tree/'.desktop-owner.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('Data lease is not a regular file')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise RuntimeError('Data is still owned; quit the previous Hélène and retry') from exc
        yield
    finally:os.close(fd)


def aliases_for(old, former):
    if not former:return {}
    prior=Path(former).expanduser()
    if not prior.is_absolute():raise ValueError('Former installation path must be absolute')
    return {str(prior):str(old)}


def inspect(old, new, data, former=''):
    if not str(data).strip() or not Path(data).expanduser().is_absolute():raise ValueError('Choose an absolute new data-home path')
    old=installation(old);new=Path(new).resolve();data=Path(data).expanduser().resolve()
    if old==new or old.is_relative_to(new) or new.is_relative_to(old):raise ValueError('Old and new applications must be separate folders')
    if data==old or data==new or data.is_relative_to(old) or data.is_relative_to(new):raise ValueError('Choose a data folder outside both application folders')
    aliases=aliases_for(old,former)
    found=app_migration.discover(old,path_aliases=aliases)
    preserved=app_migration.preservation_roots(found,old,copy_external=True)
    roots={old,*preserved}
    for row in found['agents']:
        for p in (Path(row['tree']),row['code']):
            if not p.is_dir():raise ValueError('Configured data/code is unavailable: '+str(p)+'. For a copied Mac folder, enter its former location.')
            if new==p or new.is_relative_to(p) or data==p or data.is_relative_to(p):raise ValueError('Configured source overlaps the new application or data home')
            roots.add(p)
    for source in roots:
        if source==new or new.is_relative_to(source) or source==data or data.is_relative_to(source):
            raise ValueError('Preserved Git/code root overlaps the new application or data home')
        for path in source.rglob('*') if source in preserved else ():
            if path.is_symlink() and (Path(os.readlink(path)).is_absolute() or not path.resolve().is_relative_to(source)):
                raise ValueError('Absolute/external source link needs manual relocation before migration: '+str(path))
    return old,new,data,aliases,found,roots


class Session:
    def __init__(self,new):
        self.new=Path(new).resolve();self.busy=threading.Lock();self.progress={};self.child=None;self.start_time=None

    def status(self):
        note=load(self.new/'python-migration.json') if (self.new/'python-migration.json').is_file() else {}
        status=load(self.new/'mac-migration-status.json') if (self.new/'mac-migration-status.json').is_file() else {}
        if self.child is not None and note.get('state')!='new_app_started' and status.get('state')=='launch_requested':
            code=self.child.poll()
            if code is not None:
                self.record('start_failed',error=f'New application exited ({code}) before startup confirmation. Check migration-start.log; the prepared profile and old installation are retained.')
                status=load(self.new/'mac-migration-status.json')
            elif self.start_time is not None and time.monotonic()-self.start_time>150:
                self.record('launch_unconfirmed',error='Startup was not confirmed within 150 seconds. Inspect the new window and migration-start.log; close it before retrying. No process has been killed automatically.')
                status=load(self.new/'mac-migration-status.json')
        return {'state':note.get('state','not_prepared'),'migration_id':note.get('id'),
                'startup_proof':bool(note.get('startup_proof')),'progress':self.progress,
                'handoff':status,'old_retained':True,'old_root':note.get('old_root',''),
                'data_home':note.get('data_home',''),'former':next(iter(note.get('path_aliases') or {}),''),
                'default_data':str(Path.home()/'Library/Application Support/Helene-Python/Migrated')}

    def prepare(self,old,data,*,closed=False,former=''):
        if closed is not True:raise ValueError('Explicitly confirm that the old application is Quit, not just its window closed')
        if not self.busy.acquire(blocking=False):raise RuntimeError('Migration is already in progress')
        saved_template=None;destination_owned=False;stack=ExitStack()
        try:
            if sys.platform!='darwin':raise RuntimeError('This migration must run on macOS')
            old,new,data,aliases,found,roots=inspect(old,self.new,data,former)
            from .python_platform import require_runtime
            require_runtime(new)
            stack.enter_context(lease(new));destination_owned=True
            quiescent({new})
            prior=load(new/'python-migration.json') if (new/'python-migration.json').is_file() else None
            if prior:
                if (prior.get('old_root')!=str(old) or prior.get('new_root')!=str(new) or
                    prior.get('data_home')!=str(data) or prior.get('path_aliases')!=aliases):
                    raise ValueError('Existing migration belongs to different selected paths; keep it and use its original choices')
                if prior.get('state')=='new_app_started':return self.status()
                if prior.get('state')=='prepared':return self.status()
                if prior.get('state')!='preserving':raise ValueError('Inspect the retained migration before retrying')
            else:
                # Keep the fresh template outside configs(new) BEFORE moving any
                # config. snapshot() precedes the migration journal and can be
                # interrupted by process/window exit, bypassing our exception handler.
                retained=new/'.mac-migration-template.json'
                template_path=new/'helene.json' if (new/'helene.json').is_file() else retained
                template=load(template_path)
                if template.get('setup_complete') is not False or (new/'data').exists() or (new/'agents').exists():
                    raise ValueError('Use a fresh unpacked Python application, not an existing agent home')
                saved_template=template_path.read_bytes()
            integrations_quiet(roots);quiescent(roots)
            trees={Path(row['tree']) for row in found['agents']}
            if prior:
                trees.update(Path(p) for p in prior.get('data_paths',[]) if Path(p).exists())
            for tree in sorted(trees):stack.enter_context(lease(tree))
            quiescent(roots)
            if saved_template is not None:
                temporary=retained.with_suffix('.tmp')
                temporary.write_bytes(saved_template);temporary.replace(retained)
            # Configs from failed copies are recoverable, not silently replaced.
            app_migration.retain_attempt_configs(new,prior or {'id':'fresh-template','state':'template'})
            self.progress={'stage':'preserving'}
            note=app_migration.prepare(old,new,data,old_service_stopped=True,
                progress=self.progress.update,reuse=prior,preserve_voice=False,
                path_aliases=aliases,copy_external=True)
            try:quiescent(roots)
            except Exception:
                note['state']='preserving';app_migration.write_note(new,note)
                raise
            self.progress={'stage':'prepared'}
            self.record('prepared',old_root=str(old),data_home=str(data))
            return self.status()
        except Exception as exc:
            if destination_owned and saved_template is not None and not (self.new/'helene.json').exists():
                restored=self.new/'.helene-template-restore.tmp'
                restored.write_bytes(saved_template)
                restored.replace(self.new/'helene.json')
            if destination_owned:self.record('failed',error=str(exc))
            raise
        finally:
            stack.close()
            self.busy.release()

    def record(self,state,**details):
        value={'format':'helene-mac-migration-status.v1','state':state,**details}
        path=self.new/'mac-migration-status.json';temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)

    def start(self):
        if sys.platform!='darwin':raise RuntimeError('Start the migrated application on macOS')
        if not self.busy.acquire(blocking=False):raise RuntimeError('Migration is busy')
        try:
            note=load(self.new/'python-migration.json')
            if note.get('state')=='new_app_started':return self.status()
            if note.get('state')!='prepared':raise ValueError('Prepare the profile successfully before starting')
            if self.child is not None and self.child.poll() is None:raise RuntimeError('New application is already starting; inspect its window or wait for its startup error')
            roots={Path(note['old_root']),*(Path(p) for p in note['old_data_paths']),*(Path(p) for p in note.get('preserved_roots',{}))}
            integrations_quiet(roots);quiescent(roots)
            quiescent({self.new})
            from .python_platform import desktop_argv
            argv=desktop_argv(self.new,['--root',str(self.new),'--migration',note['id']])
            with (self.new/'migration-start.log').open('ab') as log:
                self.child=subprocess.Popen(argv,cwd=self.new,stdout=log,stderr=log,start_new_session=True)
            self.start_time=time.monotonic()
            self.record('launch_requested',pid=self.child.pid,log=str(self.new/'migration-start.log'))
            return self.status()
        except Exception as exc:
            self.record('start_failed',error=str(exc));raise
        finally:self.busy.release()
