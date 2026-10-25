"""Add the legacy Update-button entry to an already assembled Helene ZIP.

Run after compiling setup/bin/migration for the same release. The standalone
setup and ZIP entry are byte-identical; checksum sidecars are regenerated.
"""
from pathlib import Path
import argparse
import hashlib
import shutil
import zipfile


def repack(archive, setup):
    backup=archive.with_suffix('.before-migration-entry.zip')
    if not backup.exists():shutil.copy2(archive,backup)
    temp=archive.with_suffix('.building.zip')
    desk=Path(__file__).resolve().parents[1]
    replacements={'Helene/helene-setup.exe':setup,
                  'Helene/app/localharness/migration_windows.py':desk/'localharness/migration_windows.py'}
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as dest:
        for entry in source.infolist():
            if entry.filename in replacements:continue
            with source.open(entry) as a,dest.open(entry,'w',force_zip64=True) as b:
                shutil.copyfileobj(a,b,1024*1024)
        for name,path in replacements.items():dest.write(path,name)
    with zipfile.ZipFile(temp) as result:
        if result.read('Helene/helene-setup.exe')!=setup.read_bytes():raise ValueError('Setup capture differs')
    temp.replace(archive)
    for path in (archive,setup):
        with path.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
        path.with_name(path.name+'.sha256').write_text(sha+'  '+path.name+'\n',encoding='ascii')
        print(path.name,path.stat().st_size,sha,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path);parser.add_argument('setup',type=Path)
    args=parser.parse_args();repack(args.archive,args.setup)
