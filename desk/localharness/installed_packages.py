"""One-time Linux package to portable transition. No portable-folder discovery.

The package database owns discovery and retirement. Copying precedes startup,
startup precedes removal, and a failed removal is retried without copying again.
Only the two historical product package names and their known program roots
are admitted. Elevation invokes the OS package manager, never writable Python.
"""
from contextlib import ExitStack
from pathlib import Path
import hashlib
import json
import os
import pwd
import shutil
import subprocess
import time
import uuid

PACKAGES={'helene':Path('/opt/helene'),'helene-python':Path('/usr/lib/helene-python/seed')}


def command(argv, *, check=True):
    result=subprocess.run(argv,capture_output=True,text=True,timeout=180,
                          env=dict(os.environ,LC_ALL='C'))
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout or 'Command failed').strip()[-3000:])
    return result


def query(name,manager):
    if name not in PACKAGES or manager not in ('deb','rpm'):raise ValueError('Unknown installed package')
    if manager=='deb':
        result=command(['/usr/bin/dpkg-query','-W','-f=${Status}\t${Version}',name],check=False)
        if result.returncode or not result.stdout.startswith('install ok installed\t'):return None
        version=result.stdout.split('\t',1)[1].strip()
        files=command(['/usr/bin/dpkg-query','-L',name]).stdout.splitlines()
    else:
        result=command(['/usr/bin/rpm','-q','--qf','%{VERSION}-%{RELEASE}.%{ARCH}',name],check=False)
        if result.returncode:return None
        version=result.stdout.strip()
        files=command(['/usr/bin/rpm','-ql',name]).stdout.splitlines()
    root=PACKAGES[name]
    members=sorted(x for x in files if x.startswith(str(root)+'/'))
    if not members or '/usr/bin/helene' not in files:
        raise ValueError('Package does not own the historical Hélène program: '+name)
    return {'name':name,'manager':manager,'version':version,'program':str(root),
            'inventory':hashlib.sha256('\n'.join(sorted(files)).encode()).hexdigest()}


def discover():
    manager='deb' if Path('/usr/bin/dpkg-query').is_file() else 'rpm' if Path('/usr/bin/rpm').is_file() else None
    if not manager:return []
    home=Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')
    rows=[]
    for name in PACKAGES:
        package=query(name,manager)
        if not package:continue
        profile=home/('helene-python/program' if name=='helene-python' else 'helene')
        # This is the documented package-managed user home, not a directory scan.
        if (profile/'helene.json').is_file():
            rows.append(dict(package,profile=str(profile.resolve())))
        elif not profile.exists():
            rows.append(dict(package,profile=None))
        else:
            raise RuntimeError('Installed profile exists without readable configuration: '+str(profile))
    return rows


def still_installed(package):
    current=query(package['name'],package['manager'])
    if current is None:return False
    if any(current[k]!=package[k] for k in ('version','program','inventory')):
        raise RuntimeError('Installed package changed during migration; inspect it before removal')
    return True


def privileged(argv):
    if os.geteuid()!=0:
        if not Path('/usr/bin/pkexec').is_file():
            raise RuntimeError('The system has no graphical administrator prompt (pkexec). Install polkit to finish package removal.')
        argv=['/usr/bin/pkexec',*argv]
    return command(argv)


def stop_previous(package):
    old=Path(package['profile'])
    # Ask the old Python window through its authenticated local exit API.
    from .migration_windows import request_python_exit
    for record in old.glob('.python-window-*.json'):
        value=json.loads(record.read_text(encoding='utf-8'))
        pid=int(value['pid'])
        try:os.kill(pid,0)
        except ProcessLookupError:continue
        request_python_exit(old,pid)
        import psutil
        deadline=time.monotonic()+60
        while True:
            try:
                if psutil.Process(pid).status()==psutil.STATUS_ZOMBIE:break
            except psutil.NoSuchProcess:break
            if time.monotonic()>=deadline:raise TimeoutError('Previous Hélène has not finished closing; profile retained')
            time.sleep(.1)
    # Native 1.x can own a per-user systemd service. Never start a broker.
    if Path('/run/systemd/system').is_dir():
        user=pwd.getpwuid(os.getuid()).pw_name
        unit='helene@'+user+'.service'
        result=command(['/usr/bin/systemctl','show',unit,'--property=ExecStart','--property=ActiveState'],check=False)
        if 'ActiveState=active' in result.stdout:
            if '/opt/helene/helene-svc' not in result.stdout:raise RuntimeError('Unexpected legacy service binding')
            privileged(['/usr/bin/systemctl','stop',unit])
    # Leases below prove the profile is actually idle. No process-name killing.


def prepare(package,new,progress=None):
    from . import app_migration
    from .desktop_supervisor import _tree_hold
    from python_migrate import installation_lock
    new=Path(new).resolve()
    if not still_installed(package):raise RuntimeError('Selected installed package disappeared')
    from .python_platform import require_runtime
    require_runtime(new)
    if package.get('profile') is None:
        with installation_lock(new):
            config=json.loads((new/'helene.json').read_text(encoding='utf-8'))
            if config.get('setup_complete') is not False:raise ValueError('A fresh portable folder is required')
            data=new/'data';data.mkdir(exist_ok=True)
            note={'format':'helene-python-migration.v1','id':uuid.uuid4().hex,'state':'prepared',
                  'old_root':package['program'],'new_root':str(new),'no_prior_profile':True,
                  'data_paths':[str(data)],'old_data_paths':[],
                  'source_paths':[{'old':str(Path(package['program'])/'tree'),'selected':str(new/'tree')}],
                  'installed_package':package}
            identity=new/'installed-package.json';part=identity.with_suffix('.tmp')
            part.write_text(json.dumps(package,indent=2),encoding='utf-8');part.replace(identity)
            app_migration.write_note(new,note)
            return note
    old=Path(package['profile']).resolve()
    stop_previous(package)
    with installation_lock(new),ExitStack() as locks:
        found=app_migration.discover(old)
        for tree in sorted({Path(r['tree']) for r in found['agents']}):locks.enter_context(_tree_hold(tree))
        pending=new/'python-migration.json'
        reuse=None
        if pending.is_file():
            reuse=json.loads(pending.read_text(encoding='utf-8'))
            if reuse.get('state')!='preserving' or reuse.get('old_root')!=str(old):
                raise RuntimeError('A different migration already owns this folder')
            app_migration.retain_attempt_configs(new,reuse)
            template=None
        else:
            template=(new/'helene.json').read_bytes()
            if json.loads(template).get('setup_complete') is not False:
                raise ValueError('Migration requires a fresh portable folder')
        # Write the package identity independently: even a crash during the
        # profile copy must reopen the same transaction, not the offline wizard.
        identity=new/'installed-package.json'
        part=identity.with_suffix('.tmp');part.write_text(json.dumps(package,indent=2),encoding='utf-8');part.replace(identity)
        note=app_migration.prepare(old,new,new/'profile',old_service_stopped=True,
             independent=True,preserve_voice=True,fresh_template=template,reuse=reuse,progress=progress)
        note['installed_package']=package
        app_migration.write_note(new,note)
        return note


def retire(new):
    from . import app_migration
    new=Path(new).resolve()
    note=json.loads((new/'python-migration.json').read_text(encoding='utf-8'))
    package=note.get('installed_package')
    if not package or note.get('package_removed'):return note
    if note.get('state') not in ('new_app_started','retirement_incomplete') or not note.get('startup_proof'):
        raise RuntimeError('The new application has not confirmed its window and profile')
    # All selected data/code must be independent of removable package bytes.
    protected=[Path(p).resolve() for p in note['data_paths']]
    protected += [Path(p['selected']).resolve() for p in note['source_paths']]
    custody=new if note.get('no_prior_profile') and package.get('profile') is None else new/'profile'
    if any(not p.is_relative_to(custody) or not p.is_dir() for p in protected):
        raise RuntimeError('Preserved profile is incomplete or outside the portable application')
    try:
        if still_installed(package):
            # A shared system package must not strand another user's profile.
            for user in pwd.getpwall():
                if user.pw_uid<1000 or user.pw_uid==os.getuid() or user.pw_uid>=60000:continue
                other=Path(user.pw_dir)/'.local/share'/('helene-python/program' if package['name']=='helene-python' else 'helene')
                try:
                    (other/'helene.json').stat()
                    present=True
                except FileNotFoundError:present=False
                except PermissionError:
                    raise RuntimeError('Cannot check the protected profile of '+user.pw_name+'. The new application is ready; the shared package is retained.')
                if present:
                    raise RuntimeError('Another user still uses this package: '+user.pw_name+'. Migrate that profile before removing the shared package.')
            args=(['/usr/bin/dpkg','--purge','--',package['name']] if package['manager']=='deb'
                  else ['/usr/bin/rpm','--erase',package['name']])
            privileged(args)
            if query(package['name'],package['manager']) is not None:raise RuntimeError('Package manager still reports the old package installed')
        note.update(state='completed',package_removed=True)
        note.pop('retirement_error',None)
    except Exception as exc:
        note.update(state='retirement_incomplete',retirement_error=str(exc))
    app_migration.write_note(new,note)
    return note
