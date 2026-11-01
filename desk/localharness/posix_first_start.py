"""Portable POSIX first launch: profile data outlives every program folder.

Only the explicitly selected profile is used. No legacy folder search, pruning,
service installation or migration scheduler runs on ordinary first launch.
"""
from dataclasses import dataclass
from pathlib import Path
import datetime as dt
import os
import time
from . import first_start as common


@dataclass(frozen=True)
class Places:
    data: Path

    @classmethod
    def current(cls, root=None):
        from .portable_profile import default_data
        if root is not None:
            from .package_notice import read
            root=Path(root).resolve()
            saved=read(common.receipt(root))
            if saved.get('format')==common.FORMAT and saved.get('data'):
                return cls(Path(saved['data']).expanduser().resolve())
            value=read(root/'helene.json')
            if value.get('setup_complete') is True and value.get('tree'):
                path=Path(value['tree']).expanduser()
                path=(path if path.is_absolute() else root/path).resolve()
                if path!=root and not path.is_relative_to(root):return cls(path)
        return cls(default_data().resolve())


ready = common.ready
agent_exists = common.agent_exists
ensure_config = common.ensure_config


def _guard(root, places):
    from .config_transaction import transaction
    root, data = common.plain(root), common.plain(places.data)
    if root == data or root.is_relative_to(data) or data.is_relative_to(root):
        raise ValueError('Keep the program folder outside the selected agent data')
    if common.programme_marker(data):
        raise ValueError('The selected profile contains program code; connect a data-only folder')
    data.mkdir(parents=True, exist_ok=True)
    return transaction(data/'memory/.control/first-start')


def handover(root, *, places=None):
    """Retire only the authenticated previous owner of this exact profile."""
    from . import session_host
    from .package_notice import read
    root=Path(root).resolve();places=places or Places.current()
    path=places.data/'memory/.control/desktop-session.json'
    value=read(path)
    if not value or Path(value.get('program',str(root))).resolve()==root:return False
    if value.get('format')!=session_host.FORMAT or value.get('tree')!=str(places.data.resolve()):
        raise ValueError('Previous session does not belong to the selected profile')
    state=session_host.process_state(value)
    if state=='unknown':raise RuntimeError('Previous session identity is unknown; no replacement started')
    previous=Path(value['program']).resolve()
    if state=='alive':
        from .migration_windows import request_python_exit
        import psutil
        for observer in value.get('observers',[]):
            try:
                process=psutil.Process(observer['pid'])
                if process.create_time()!=observer.get('birth'):continue
                request_python_exit(previous,process.pid,observer_only=True)
            except psutil.NoSuchProcess:pass
        # No service scan or PID-based broad termination. An uncertain reply is
        # resolved by exact birth state; Exit is never replayed against a new PID.
        try:session_host._rpc(value,'exit',{'owner_exit':False},timeout=45)
        except OSError:pass
        deadline=time.monotonic()+20
        while session_host.process_state(value) not in ('absent','changed'):
            if session_host.process_state(value)=='unknown' or time.monotonic()>deadline:
                raise RuntimeError('Previous profile owner shutdown is not confirmed')
            time.sleep(.05)
    from .migration_posix import owner_lease_available
    if not owner_lease_available(places.data/'.desktop-owner.lock'):
        raise RuntimeError('Previous profile workers have not released their lease')
    config=previous/'helene.json'
    if ready(previous,places) and config.is_file():
        saved=common.load(config)
        selected=Path(saved.get('tree','data'))
        selected=(selected if selected.is_absolute() else previous/selected).resolve()
        if selected!=places.data.resolve():raise ValueError('Previous data pointer differs')
        saved=common._bind(saved,previous,root,places.data)
        common._write(places.data/'memory/.state/helene-profile.json',
            {k:v for k,v in saved.items() if k not in ('tree','code','python','app','runner','port','installed')},private=True)
    return True


def connect(root, path, *, places=None, progress=None):
    places=places or Places.current(root)
    if agent_exists(places.data):raise ValueError('The current profile already contains an agent')
    selected=Places(common.plain(Path(path).expanduser()))
    if not selected.data.is_dir() or not agent_exists(selected.data):raise ValueError('Select an existing agent data folder')
    prepare(root,places=selected,progress=progress)
    return selected


def prepare(root, *, places=None, progress=None):
    places=places or Places.current();root=common.plain(root)
    report=progress or (lambda value:None)
    with _guard(root,places):
        required=('runtime/bin/python3','app/python_app.py','app/deskapp.py',
                  'app/first_start_window.py','app/resources/first-start.html','tree/agent.py','tree/llm.py','python-app.json')
        if any(not (root/name).is_file() for name in required):raise ValueError('Unpack the complete application into a new folder')
        changed=handover(root,places=places)
        if ready(root,places):
            ensure_config(root,places=places,refresh_profile=changed)
            return {'state':'completed','data':str(places.data),'agent':agent_exists(places.data)}
        report({'stage':'preparing','label':'Подключаем данные профиля'})
        # New distributions carry a blank template. An admitted configured
        # folder is never silently rebound to another profile.
        config=root/'helene.json'
        if config.is_file():
            saved=common.load(config)
            raw=Path(saved.get('tree','data'))
            selected=(raw if raw.is_absolute() else root/raw).resolve()
            if saved.get('setup_complete') is not False and selected!=places.data.resolve():
                raise ValueError('This program already selects another profile; reconnect explicitly')
        ensure_config(root,places=places,refresh_profile=True)
        common._write(common.receipt(root),{'format':common.FORMAT,'state':'completed',
            'completed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'data':str(places.data.resolve())},private=True)
        report({'stage':'completed','label':'Готово'})
        return {'state':'completed','data':str(places.data),'agent':agent_exists(places.data)}


def create_agent(root, values, *, places=None, progress=None):
    places=places or Places.current()
    with _guard(root,places):
        return common._create_agent(root,values,places=places,progress=progress)
