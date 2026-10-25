"""Prepare a separate XTTS voice bundle from an already provisioned runtime.

Use --download-model for a personal/non-commercial XTTS-v2 evaluation only.
The model's CPML terms are carried verbatim, never replaced by the code licence.
The main Hélène interpreter is not modified. A runtime must be relocatable (an
embedded Python distribution, not a venv pointing back to a developer machine).
"""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

MODEL = "https://huggingface.co/coqui/XTTS-v2/resolve/v2.0.2/"
FILES = ("config.json", "model.pth", "vocab.json", "speakers_xtts.pth", "LICENSE.txt")


def download_model(destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = destination / name
        if target.is_file():
            continue
        part = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(MODEL + name, timeout=300) as source, part.open("wb") as sink:
            shutil.copyfileobj(source, sink, 1024 * 1024)
        part.replace(target)
        print(name, target.stat().st_size, flush=True)


def bundle(runtime, model, output):
    if (runtime / "pyvenv.cfg").exists():
        raise ValueError("Use relocatable embedded Python, not a machine-bound venv")
    for name in FILES:
        if not (model / name).is_file():
            raise FileNotFoundError(model / name)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(runtime, output / "runtime",ignore=shutil.ignore_patterns('__pycache__','*.pyc','.git','direct_url.json'))
    shutil.copytree(model, output / "model")
    interpreter=runtime/('python.exe' if (runtime/'python.exe').is_file() else 'bin/python3')
    # Read the model's own speaker embeddings in its compatible, separate torch
    # runtime. The UI needs names only; it must not load torch on a Settings GET.
    catalogue=subprocess.check_output([str(interpreter),'-c',
        'import torch,json,sys;from pathlib import Path;p=Path(sys.argv[1]);'
        'v=torch.load(p/"speakers_xtts.pth",map_location="cpu",weights_only=True);'
        'print(json.dumps({"speakers":sorted(v),"languages":json.loads((p/"config.json").read_text())["languages"],"torch_version":str(torch.__version__),"cuda_build":torch.version.cuda}))',
        str(model)],timeout=90)
    catalogue=json.loads(catalogue)
    (output/'model/voices.json').write_text(json.dumps(catalogue,indent=2,ensure_ascii=False),encoding='utf-8')
    files = {}
    for path in sorted(output.rglob("*")):
        if path.is_file():
            h = hashlib.sha256()
            with path.open("rb") as source:
                while data := source.read(1024 * 1024): h.update(data)
            files[path.relative_to(output).as_posix()] = h.hexdigest()
    (output / "xtts-bundle.json").write_text(json.dumps({"format":"helene-xtts.v1",
        "model_source":MODEL,"model_terms":"model/LICENSE.txt","runtime":{"torch_version":catalogue['torch_version'],"cuda_build":catalogue['cuda_build']},"files":files}, indent=2),encoding="utf-8")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',type=Path,required=True)
    p.add_argument('--download-model',action='store_true')
    p.add_argument('--runtime',type=Path)
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.download_model:download_model(a.model)
    if a.output:
        if not a.runtime:p.error('--runtime is required with --output')
        bundle(a.runtime,a.model,a.output)

if __name__=='__main__':main()
