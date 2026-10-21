"""Compose the Python app edition from Git blobs plus explicitly selected edits.

Unchanged sources use their pinned Git blob bytes (not a Windows checkout's
CRLF projection). Changed overlay paths must be named; unrelated relay dirt is
not implicitly part of the package. This is a local build, not publication.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import core_src


def compose(root: Path, output: Path, edits: list[str]):
    def git(*args):
        return subprocess.check_output(['git',*args],cwd=root)
    selected={}
    for row in git('ls-tree','-r','-z','HEAD','--','praxis','helene/core').split(b'\0'):
        if not row:continue
        meta,raw=row.split(b'\t',1);name=raw.decode('utf-8')
        prefix='helene/core/' if name.startswith('helene/core/') else 'praxis/'
        relative=name[len(prefix):]
        if core_src._skip(relative) or core_src.mirror_only(relative):continue
        if prefix=='helene/core/' or relative not in selected:
            selected[relative]=(name,meta.split()[-1].decode('ascii'))
    for name in edits:
        if not name.startswith('helene/core/') or not (root/name).is_file():
            raise ValueError('Only explicit Hélène overlay inputs are accepted: '+name)
        relative=name.removeprefix('helene/core/')
        if not core_src._skip(relative) and not core_src.mirror_only(relative):
            selected[relative]=(name,None)
    output.mkdir(parents=True,exist_ok=False)
    files={}
    for relative,(name,oid) in sorted(selected.items()):
        data=(root/name).read_bytes() if name in edits else git('cat-file','blob',oid)
        destination=output/relative;destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(data)
        files[relative]={'source':name,'sha256':hashlib.sha256(data).hexdigest()}
    return {'head':git('rev-parse','HEAD').decode().strip(),'files':files,'edits':edits}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--edits',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True)
    a=p.parse_args();root=Path(__file__).resolve().parents[2]
    value=compose(root,a.output,json.loads(a.edits.read_text(encoding='utf-8')))
    a.manifest.write_text(json.dumps(value,indent=2),encoding='utf-8')
    print('EDITION_COMPOSED',len(value['files']))
if __name__=='__main__':main()
