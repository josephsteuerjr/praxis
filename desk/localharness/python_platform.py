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


def asset_name(version, selected):
    if selected not in TARGETS:
        raise ValueError('Unknown Python application target')
    suffix = '' if selected == 'windows-x86_64' else '-'+selected
    return 'Helene-'+version+suffix+'.zip'


def require_runtime(program):
    if target(program) != runtime_target():
        raise ValueError('Incoming application is for another OS/architecture')
