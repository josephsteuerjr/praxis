"""Bounded command failures in profile data; never persist request arguments."""
from pathlib import Path
import datetime as dt
import re,threading,traceback
from .essential_backup import load
from .package_notice import read
from .first_start import _write

_lock=threading.RLock()


def location(data):return Path(data)/'memory/.state/operation-errors.json'


def record(root,operation,error,*,values=None,data=None):
    root=Path(root);config=load(root/'helene.json')
    if data is None:
        chosen=Path(config.get('tree') or 'data')
        data=chosen if chosen.is_absolute() else root/chosen
    from .python_support import redactor
    request=dict(values or {})
    # Login OTPs and PINs are request secrets; a configuration's 'code' is a
    # source path and must not be mistaken for a credential.
    for key in tuple(request):
        if re.fullmatch('code|pin|otp',key,re.I):request[key+'_secret']=request.pop(key)
    mask=redactor({'configuration':config,'request':request})
    operation=operation if isinstance(operation,str) and re.fullmatch('[a-z_]{1,64}',operation) else 'unknown'
    row={'at':dt.datetime.now(dt.timezone.utc).isoformat(),'program':str(root),
         'operation':operation,'error_type':type(error).__name__,'error':mask(str(error))[:4096],
         'frames':[{'file':frame.filename,'line':frame.lineno,'function':frame.name}
                   for frame in traceback.extract_tb(error.__traceback__)[-12:]]}
    for key in ('errno','winerror'):
        value=getattr(error,key,None)
        if isinstance(value,int):row[key]=value
    target=location(data)
    with _lock:
        previous=read(target);rows=previous.get('errors',[]) if isinstance(previous,dict) else []
        _write(target,{'format':'helene-operation-errors.v1','errors':[*rows[-31:],row]},private=True)
    return target
