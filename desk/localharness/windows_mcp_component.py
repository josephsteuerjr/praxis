"""Optional Windows MCP: immutable upstream wheels, private Python, retained pins."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import uuid
import zipfile
from . import windows_mcp_words as words

RELATIVE = 'components/windows-mcp'
PIN = json.loads(Path(__file__).with_name('windows-mcp-release.json').read_text(encoding='utf-8'))


def artifacts(pin=PIN):
    if pin.get('format') != 'helene-windows-mcp.v1' or pin.get('target') != 'windows-x86_64':
        raise ValueError('Unsupported Windows MCP release')
    rows = [pin['runtime'], *pin['wheels']]
    names = set()
    for row in rows:
        name = row.get('name', '')
        url = urllib.parse.urlsplit(row.get('url', ''))
        if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*\.(zip|whl)', name)
                or name in names or not re.fullmatch('[a-f0-9]{64}', row.get('sha256', ''))
                or type(row.get('size')) is not int or not 0 < row['size'] < 500_000_000
                or url.scheme != 'https' or url.hostname not in ('www.python.org', 'files.pythonhosted.org')
                or url.username or url.password or url.fragment
                or Path(urllib.parse.unquote(url.path)).name != name):
            raise ValueError('Invalid Windows MCP artifact')
        names.add(name)
    return rows


def require_platform(program=None):
    from .python_platform import runtime_target
    if runtime_target() != PIN['target']:
        if program is None:
            raise RuntimeError(words.configured({},'unsupported_platform'))
        raise words.Error(program,'unsupported_platform')


def directory(program):
    from .component_store import path
    return path(program, RELATIVE)


def interpreter(home):
    path = str(Path(home) / 'python.exe')
    # Some upstream extension DLL loaders still use MAX_PATH. Use the same
    # file's native short spelling where Windows supplies it (no junction).
    if os.name == 'nt' and len(path) > 160:
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        fn = ctypes.windll.kernel32.GetShortPathNameW
        fn.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        fn.restype = ctypes.c_uint32
        count = fn(path, buffer, len(buffer))
        if 0 < count < len(buffer):
            return buffer.value
    return path


def status(program):
    from .python_platform import runtime_target
    from .component_install_job import status as job_status
    supported = runtime_target() == PIN['target']
    error = ''
    try:
        home = directory(program)
        receipt = json.loads((home / 'installed.json').read_text(encoding='utf-8')) if (home / 'installed.json').is_file() else {}
        installed = receipt.get('product') == 'windows-mcp' and (home / 'python.exe').is_file()
    except (OSError, ValueError, KeyError) as exc:
        installed = False
        error = str(exc)
    return dict(id='windows-mcp', ready=supported, installed=installed,
                version=receipt.get('version', '') if installed else PIN['version'],
                download_bytes=sum(row['size'] for row in artifacts()),
                project=PIN['project'], license='MIT',
                error=error or ('' if supported else words.text(program,'unsupported_platform')),
                progress=job_status(program, 'windows-mcp'), restart_required=False)


def fetch(row, progress=None):
    from .component_store import cache
    from .python_updates import _download
    target = cache() / (row['sha256'][:12] + '-' + row['name'])
    if target.is_file():
        with target.open('rb') as source:
            valid = target.stat().st_size == row['size'] and hashlib.file_digest(source, 'sha256').hexdigest() == row['sha256']
        if valid:
            return target
        target.rename(target.with_name(target.name + '.invalid-' + uuid.uuid4().hex))
    result = _download(row['url'], target, row['sha256'], progress)
    if result['bytes'] != row['size']:
        raise ValueError('Windows MCP artifact size differs from the pinned release')
    return target


def unpack(archive, home, *, wheel=False):
    """Install the wheel layout without executing package installers or scripts."""
    total = 0
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            name = member.filename
            parts = PurePosixPath(name).parts
            if (not parts or '\\' in name or ':' in name or name.startswith('/') or '..' in parts
                    or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('Unsafe Windows MCP archive member')
            if member.is_dir():
                continue
            total += member.file_size
            if member.file_size < 0 or total > 1_000_000_000:
                raise ValueError('Windows MCP archive is too large')
            root = home / 'Lib/site-packages' if wheel else home
            if wheel and parts[0].endswith('.data'):
                if len(parts) < 3:
                    raise ValueError('Invalid wheel data layout')
                roots = {'purelib': home / 'Lib/site-packages', 'platlib': home / 'Lib/site-packages',
                         'scripts': home / 'Scripts', 'data': home, 'headers': home / 'Include'}
                root = roots.get(parts[1])
                if root is None:
                    raise ValueError('Unknown wheel data scheme')
                parts = parts[2:]
            target = root.joinpath(*parts)
            if target.exists():
                raise ValueError('Duplicate Windows MCP payload file: ' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as incoming, target.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            if target.stat().st_size != member.file_size:
                raise ValueError('Truncated Windows MCP payload')


def install(program, progress=None):
    require_platform(program)
    from .component_store import root, import_directory, pin
    from .config_transaction import transaction
    rows = artifacts()
    def report(**value):
        if progress:
            progress(value)
    with transaction(Path(program) / '.helene/windows-mcp-install'):
        archives = []
        for index, row in enumerate(rows):
            report(stage='downloading', asset=row['name'], files_done=index, total_files=len(rows))
            archives.append(fetch(row, progress))
        parent = root() / 'staging'
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='windows-mcp-', dir=parent) as temporary:
            home = Path(temporary)
            for index, archive in enumerate(archives):
                report(stage='extracting', asset=rows[index]['name'], files_done=index, total_files=len(rows))
                unpack(archive, home, wheel=index > 0)
            # site enables wheel .pth hooks, including pywin32's DLL paths.
            (home / 'python314._pth').write_text('python314.zip\n.\nLib/site-packages\nimport site\n', encoding='utf-8')
            notices = []
            for row in PIN['wheels']:
                files = row['license_files']
                # fastmcp-slim and fastmcp are the same upstream release; the
                # full distribution supplies the shared Apache license text.
                if not files and row['package'] == 'fastmcp-slim':
                    files = next(r['license_files'] for r in PIN['wheels'] if r['package'] == 'fastmcp' and r['version'] == row['version'])
                if not files or any(not (home / 'Lib/site-packages' / f).is_file() for f in files):
                    raise ValueError('Third-party license missing: ' + row['package'])
                notices.append(dict(package=row['package'], version=row['version'], license=row['license'],
                                    files=['Lib/site-packages/' + f for f in files]))
            if not (home / 'LICENSE.txt').is_file():
                raise ValueError('Python runtime license missing')
            notices.append(dict(package='Python', version=PIN['python'], files=['LICENSE.txt']))
            (home / 'THIRD-PARTY-NOTICES.json').write_text(json.dumps(notices, ensure_ascii=False, indent=2), encoding='utf-8')
            report(stage='checking')
            from .program_state import directory as state_directory
            from .update_process import permanent_environment
            environment=permanent_environment()
            environment.update(HELENE_WINDOWS_MCP_STATE=str(state_directory(program,'windows-mcp')),
                ANONYMIZED_TELEMETRY='false',POSTHOG_API_KEY='',WINDOWS_MCP_WATCHDOG='false')
            checked = subprocess.run([interpreter(home), '-B',
                str(Path(__file__).with_name('windows_mcp_entry.py')), '--check'],env=environment,
                cwd=home, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if checked.returncode:
                raise RuntimeError('Windows MCP import check failed: ' + checked.stdout.decode('utf-8', errors='replace')[-3000:])
            receipt = dict(product='windows-mcp', version=PIN['version'], target=PIN['target'],
                           python=PIN['python'], license=PIN['license'], artifacts=rows)
            (home / 'installed.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
            reference, result = import_directory(home, program=program)
            pin(program, RELATIVE, reference)
    return dict(installed=True, version=PIN['version'], **result)
