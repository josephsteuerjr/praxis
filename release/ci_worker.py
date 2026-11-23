"""Restore a candidate, build on this runner and exercise the packaged Python."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import zipfile


def main():
    target, destination = sys.argv[1], Path(sys.argv[2]).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve().parent
    candidate = json.loads((src/"candidate.json").read_bytes())
    with (src/"source.zip").open("rb") as stream:
        if hashlib.file_digest(stream,"sha256").hexdigest() != candidate["sha256"]:
            raise ValueError("Input source archive hash mismatch")
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        with zipfile.ZipFile(src/"source.zip") as z:
            z.extractall(work/"input")
        original, = (work/"input").iterdir()
        run = lambda *cmd: subprocess.run(list(map(str,cmd)),check=True)
        run(sys.executable,original/"release/kit.py","restore","--commit",candidate["commit"],"--to",work/"source")
        run(sys.executable,work/"source/release/kit.py","build","--target",target,"--cache",work/"cache","--out",destination)
        record = json.loads((destination/(target+".json")).read_bytes())
        if record["source_commit"] != candidate["commit"]:
            raise ValueError("Build came from another source")
        with zipfile.ZipFile(destination/record["asset"]) as z:
            meta = json.loads(z.read("Helene/RELEASE-ORIGIN.json"))
            guide = z.read("Helene/START-HERE.md").decode()
            assert meta["map"] in guide and meta["source_archive"] in guide
            assert hashlib.sha256(z.read("Helene/"+meta["map"])).hexdigest()==meta["map_sha256"]
            if not target.startswith("server-"):
                runtime = ("runtime/python.exe" if os.name=="nt" else "runtime/bin/python3")
                for member in z.infolist():
                    name=member.filename.removeprefix("Helene/")
                    if name==runtime or name.startswith("runtime/") and not name.startswith("runtime/bin/") and "site-packages/" not in name:
                        file=Path(z.extract(member,work/"smoke"))
                        if not file.is_dir() and member.external_attr>>16&0o111:
                            file.chmod(0o755)
                probe=Path(z.extract("Helene/tree/room_identity.py",work/"smoke"))
                run(work/"smoke/Helene"/runtime,"-I","-c",
                    "import runpy,sys; m=runpy.run_path(sys.argv[1]); assert m['root_chat_id']({}) is None; print(sys.version)",probe)
        (destination/(target+"-native.json")).write_text(json.dumps({"commit":candidate["commit"],
            "target":target,"host":platform.platform(),"packaged_python":not target.startswith("server-")},indent=2))


if __name__=="__main__":
    main()
