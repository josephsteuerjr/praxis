"""Linux per-command subreaper. A shell's detached descendants remain ours.

This is a separate process per command: unrelated concurrent commands cannot
adopt one another's children. Only an ECHILD observation permits settlement.
An interrupted helper without that receipt leaves parent custody unknown.
"""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main(path):
    directory=Path(path)
    request=json.loads((directory/'request.json').read_text())
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(36,1,0,0,0):raise OSError(ctypes.get_errno(),'PR_SET_CHILD_SUBREAPER')
    stopping=False
    def stop(signum,frame):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    (directory/'ready').write_text(request['nonce'])
    while request.get('deferred') and not stopping:
        if os.getppid()!=request['parent_pid']:
            stopping=True;break
        gate=directory/'admitted'
        if gate.exists() and gate.read_text()==request['nonce']:break
        time.sleep(.01)
    command=request['command']
    try:
        if stopping:raise OSError('custodian ended before command admission')
        proc=subprocess.Popen(command,shell=request['shell'],executable=request.get('executable'))
        leader=proc.pid;code=None
    except OSError as exc:
        print(f'command launch failed: {exc}',file=sys.stderr)
        leader=None;code=127
    while True:
        empty=False
        while True:
            try:pid,status=os.waitpid(-1,os.WNOHANG)
            except ChildProcessError:
                empty=True;break
            if not pid:break
            if pid==leader:code=os.waitstatus_to_exitcode(status)
        if empty:break
        stop_file=directory/'stop'
        if stop_file.exists() and stop_file.read_text()==request['nonce']:stopping=True
        if stopping:
            # pidfds close the census/signal PID-reuse race. Killing immediate
            # children adopts their descendants; repeat until ECHILD, including
            # double-fork, setsid and children ignoring TERM.
            children=Path(f'/proc/self/task/{os.getpid()}/children').read_text().split()
            for raw in children:
                try:
                    fd=os.pidfd_open(int(raw))
                    try:
                        stat=Path(f'/proc/{raw}/stat').read_text().rsplit(')',1)[1].split()
                        if int(stat[1])==os.getpid():signal.pidfd_send_signal(fd,signal.SIGKILL)
                    finally:os.close(fd)
                except ProcessLookupError:pass
                except FileNotFoundError:pass
        time.sleep(.02)
    value={'nonce':request['nonce'],'settled':True,'exit_code':code if code is not None else 127,
           'stopped':stopping,'subreaper_pid':os.getpid()}
    temporary=directory/'settled.tmp'
    temporary.write_text(json.dumps(value))
    temporary.replace(directory/'settled.json')
    return 0


if __name__=='__main__':raise SystemExit(main(sys.argv[1]))
