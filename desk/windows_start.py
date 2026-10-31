"""The clickable Windows binary's single Python first-start entry."""
import argparse
import os
from pathlib import Path
import runpy
import sys


def ordinary(root,arguments):
    entry=root/'app/python_app.py'
    sys.argv=[str(entry),'--root',str(root),*arguments]
    runpy.run_path(str(entry),run_name='__main__')


def main(argv=None,*,places=None):
    values=list(sys.argv[1:] if argv is None else argv)
    parser=argparse.ArgumentParser(add_help=False);parser.add_argument('--root',type=Path,required=True)
    known,arguments=parser.parse_known_args(values);root=known.root.resolve()
    from localharness import first_start
    selected=places or first_start.Places.current()
    completed=first_start.ready(root,selected)
    if completed:os.environ['HELENE_USER_DATA']=str(selected.data)
    # Existing authenticated update/startup consumers retain their own custody.
    # Emergency Stop must never trigger installation work.
    if any(flag in arguments for flag in ('--panic','--migration','--session-job')):
        return ordinary(root,arguments)
    if completed:
        changed=first_start.handover(root,places=selected)
        first_start.ensure_config(root,places=selected,refresh_profile=changed)
        if first_start.agent_exists(selected.data):
            return ordinary(root,arguments)
    from first_start_window import main as window
    return window(root,places=selected)


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        from localharness.bootstrap_ready import signal
        signal(failed=True)
        if os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,str(error),'Hélène',0x10)
        raise SystemExit(1)
