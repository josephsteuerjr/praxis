"""Python application target identity shared by packaging and update readers."""
from pathlib import Path
import json
import platform
import sys

TARGETS = {'windows-x86_64', 'linux-x86_64', 'macos-arm64'}


def desktop_identity():
    """UI names are distinct from Python's internal sys.platform spelling."""
    system = {'win32':'windows', 'darwin':'macos'}.get(sys.platform, sys.platform)
    if system.startswith('linux'):system='linux'
    machine = platform.machine().lower()
    return {'platform':system, 'arch':{'amd64':'x86_64','aarch64':'arm64'}.get(machine,machine)}


def runtime_target():
    machine = platform.machine().lower()
    if sys.platform == 'win32' and machine in ('amd64','x86_64'):
        return 'windows-x86_64'
    if sys.platform.startswith('linux') and machine in ('amd64','x86_64'):
        return 'linux-x86_64'
    if sys.platform == 'darwin' and machine in ('arm64','aarch64'):
        return 'macos-arm64'
    raise RuntimeError('Unsupported application platform: '+sys.platform+'/'+machine)


def target(program):
    value = json.loads((Path(program)/'python-app.json').read_text(encoding='utf-8'))
    selected = value.get('target', 'windows-x86_64')  # original Windows v1 record
    if selected not in TARGETS:
        raise ValueError('Unknown Python application target')
    return selected


def distribution(program):
    program=Path(program)
    value=json.loads((program/'python-app.json').read_text(encoding='utf-8'))
    if target(program)!='linux-x86_64':return None
    flavor=value.get('distribution') or ('fedora' if (program/'FEDORA-BUILD.json').is_file() else 'debian')
    if flavor not in ('debian','fedora'):raise ValueError('Unknown Linux application distribution')
    return flavor


def asset_name(version, selected, flavor=None):
    if selected not in TARGETS:
        raise ValueError('Unknown Python application target')
    if flavor not in (None,'debian','fedora'):raise ValueError('Unknown Linux application distribution')
    suffix = '' if selected == 'windows-x86_64' else '-fedora-x86_64' if selected=='linux-x86_64' and flavor=='fedora' else '-'+selected
    return 'Helene-'+version+suffix+'.zip'


def require_runtime(program, current=None):
    if target(program) != runtime_target():
        raise ValueError('Incoming application is for another OS/architecture')
    if current is not None and target(program)=='linux-x86_64' and distribution(program)!=distribution(current):
        raise ValueError('Incoming application is for another Linux distribution')


def desktop_argv(program, arguments=()):
    """GUI re-entry keeps Cocoa bundle identity; helpers keep raw Python."""
    program=Path(program).resolve()
    arguments=list(map(str,arguments))
    bundle=program/'Helene.app/Contents/MacOS/Helene'
    if sys.platform=='darwin' and '--browser' not in arguments and bundle.is_file():
        return [str(bundle),*arguments]
    python=program/('runtime/python.exe' if sys.platform=='win32' else 'runtime/bin/python3')
    return [str(python),str(program/'app/python_app.py'),*arguments]
