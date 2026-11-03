"""One private preparation failure in profile state, outside programme code."""
from pathlib import Path
import datetime as dt,traceback


def location(data):
    return Path(data)/'memory/.state/first-start-error.json'


def record(root,data,error):
    from .first_start import plain,_write
    from .essential_backup import load
    root=plain(root);target=plain(location(data))
    metadata=load(root/'python-app.json')
    value={'format':'helene-first-start-error.v1','at':dt.datetime.now(dt.timezone.utc).isoformat(),
           'program':str(root),'version':metadata.get('version'),
           'error_type':type(error).__name__,'error':str(error),
           'traceback':''.join(traceback.format_exception(type(error),error,error.__traceback__))}
    _write(target,value,private=True)
    return target


def owner_location(data):return Path(data)/'memory/.state/owner-control-last.json'


def owner_result(root,data,operation,*,error=None,proof=None,removed=()):
    from .first_start import plain,_write
    value={'format':'helene-owner-control-result.v1','at':dt.datetime.now(dt.timezone.utc).isoformat(),
           'program':str(root),'operation':operation,'state':'error' if error else 'ready',
           'removed_stop_sources':list(removed),'proof':proof or {}}
    if error:
        value.update(error_type=type(error).__name__,error=str(error),
            traceback=''.join(traceback.format_exception(type(error),error,error.__traceback__)))
    _write(plain(owner_location(data)),value,private=True)
