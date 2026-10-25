"""Prepare a hash-identical Windows alias selected by pre-Python updaters.

The legacy Windows consumer takes the first Helene-*.zip except macOS. GitHub
currently returns Linux platform names before the normal Windows name. An alias
that sorts before those names permits migration without changing an installed
1.7.0 binary. Publication must verify the actual API order with that consumer.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import zipfile


def prepare(archive):
    archive=Path(archive).resolve()
    match=re.fullmatch(r'Helene-(\d+\.\d+\.\d+)\.zip',archive.name)
    if not match:raise ValueError('Expected the normal Windows release ZIP')
    with zipfile.ZipFile(archive) as z:
        meta=json.loads(z.read('Helene/python-app.json'))
        if meta.get('target','windows-x86_64')!='windows-x86_64':raise ValueError('Only Windows gets this legacy alias')
        for name in ('Helene/helene-setup.exe','Helene/runtime/python.exe','Helene/app/python_migrate.py'):
            if name not in z.namelist():raise ValueError('Incomplete migration package: '+name)
        if z.testzip() is not None:raise ValueError('Damaged Windows ZIP')
    alias=archive.with_name('Helene-'+match[1]+'-0-windows.zip')
    if alias.exists():raise FileExistsError(alias)
    try:os.link(archive,alias)
    except OSError:shutil.copy2(archive,alias)
    with archive.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    with alias.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=digest:raise IOError('Compatibility alias differs')
    alias.with_suffix('.zip.sha256').write_text(digest+'  '+alias.name+'\n',encoding='ascii')
    return {'path':str(alias),'sha256':digest,'size':alias.stat().st_size,'normal_archive':archive.name,
            'purpose':'Legacy Windows updater; same verified archive and helper, not a second product'}
