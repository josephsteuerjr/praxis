"""Short-lived admission for checking an unsaved HTTPS bridge: probe route only."""
import contextlib
import json
import re
import secrets
import time
from pathlib import Path
from .private_files import write_private

HEADER='X-Helene-Bridge-Probe'


@contextlib.contextmanager
def issue(tree,host):
    token=secrets.token_hex(32)
    path=Path(tree)/'memory/.state/server-probes'/(token+'.json')
    write_private(path,json.dumps({'host':host.lower(),'expires':time.time()+45}).encode())
    try:yield {HEADER:token}
    finally:path.unlink(missing_ok=True)


def permitted(tree,request,host):
    if request.method!='GET' or request.path!='/api/phone/probe' or request.headers.get('Origin'):return False
    token=request.headers.get(HEADER,'')
    if not re.fullmatch('[a-f0-9]{64}',token):return False
    try:
        row=json.loads((Path(tree)/'memory/.state/server-probes'/(token+'.json')).read_text(encoding='utf-8'))
        return row.get('host')==host.lower() and time.time()<float(row.get('expires') or 0)<=time.time()+46
    except (OSError,ValueError,TypeError):return False
