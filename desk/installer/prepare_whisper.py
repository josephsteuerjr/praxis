"""Make the optional Windows Whisper library ZIP from a qualified embedded runtime.

No pip or model download. Read installed wheel RECORDs, include their dependency
closure and licences, and keep the engine's Python ABI in the bundle passport.
The owner's recognition model remains in data/models/whisper and is selected or
fetched separately in the voice card. The base app needs none of these binaries.
"""
from __future__ import annotations
import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import sys
import zipfile
from packaging.requirements import Requirement

HEAVY = ('faster_whisper','ctranslate2','onnxruntime','av','tokenizers')


def normalize(name):return re.sub(r'[-_.]+','-',name).lower()


def build(runtime:Path,output:Path):
    runtime=runtime.resolve();sites=[runtime,runtime/'Lib/site-packages']
    dists={normalize(d.metadata['Name']):d for d in metadata.distributions(path=[str(s) for s in sites])}
    pending=['faster-whisper'];selected={}
    while pending:
        name=normalize(pending.pop())
        if name in selected:continue
        if name not in dists:raise ValueError('Runtime is missing Whisper dependency: '+name)
        dist=dists[name];selected[name]=dist
        for raw in dist.requires or ():
            req=Requirement(raw)
            if req.marker is None or req.marker.evaluate({'extra':''}):pending.append(req.name)
    files={}
    for dist in selected.values():
        base=Path(dist.locate_file('')).resolve()
        for relative in dist.files or ():
            path=Path(dist.locate_file(relative)).resolve()
            if not path.is_relative_to(base) or not path.is_file() or path.suffix=='.pyc' or '__pycache__' in path.parts:continue
            name=path.relative_to(base).as_posix()
            if name.endswith('/direct_url.json'):continue
            if name in files and files[name]!=path:raise ValueError('Whisper dependency path collision: '+name)
            files[name]=path
    passport={'format':'helene-whisper-engine.v1','python':f'{sys.version_info.major}.{sys.version_info.minor}',
              'dists':{n:d.version for n,d in selected.items()},'models':'download separately from the voice card'}
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
        for name,path in sorted(files.items()):archive.write(path,'Helene/voice/site-packages/'+name)
        archive.writestr('Helene/voice/voice-pack.json',json.dumps(passport,indent=2))
        archive.writestr('Helene/voice/installed.json',json.dumps(passport,indent=2))
    return {'files':len(files),'packages':passport['dists'],'bytes':output.stat().st_size,'python':passport['python']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(build(args.runtime,args.output),indent=2))
