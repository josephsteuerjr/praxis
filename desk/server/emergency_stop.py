"""Persistent stop for the transferred Docker installation, outside agent code.

Both the agent and authenticated channel may request STOP. Only the host owner
can remove the latch. Container restart or replacement keeps the data volume.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import time


def latch(tree):
    return Path(tree)/'.owner-stop/stop.json'


def prepare(tree):
    folder=latch(tree).parent
    folder.mkdir(exist_ok=True)
    info=folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise RuntimeError('Owner-stop directory is not owned by the supervisor')
    folder.chmod(0o755)


def stopped(tree):
    try: latch(tree).stat(); return True
    except FileNotFoundError: return False
    except OSError: return True


def consume(tree):
    request=Path(tree)/'memory/.control/panic.json'
    try:
        fd=os.open(request,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    except FileNotFoundError:
        return stopped(tree)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode): raise ValueError('panic request is not a file')
        with os.fdopen(fd,'rb') as source:
            fd=None
            raw=source.read(1025)
        try: row=json.loads(raw) if len(raw)<=1024 else {}
        except (ValueError,UnicodeError): row={}
        if not isinstance(row,dict): row={}
        via=str(row.get('via') or 'agent')
        via=via if via in {'agent','telegram','phone','window','cli'} else 'agent'
        try:
            with latch(tree).open('x',encoding='utf-8') as target:
                json.dump({'at':time.time(),'via':via,'by':'agent' if via=='agent' else 'owner'},target)
                target.flush();os.fsync(target.fileno())
        except FileExistsError:
            pass
        request.unlink(missing_ok=True)
    finally:
        if fd is not None:os.close(fd)
    return stopped(tree)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('status','resume'))
    parser.add_argument('--tree',required=True);args=parser.parse_args()
    if args.action=='resume':
        if os.geteuid()!=0:raise SystemExit('Resume requires the server owner: run with sudo')
        # Explicit owner action also clears a still pending stop request.
        (Path(args.tree)/'memory/.control/panic.json').unlink(missing_ok=True)
        latch(args.tree).unlink(missing_ok=True)
    print(json.dumps({'stopped':stopped(args.tree)}))


if __name__=='__main__':main()
