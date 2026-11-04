"""Provision an optional server-only XTTS component; leave it unselected/stopped."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import urllib.request
import uuid

REVISION = "6b5c4844e7dcd49d8c160fcd3c9a6e87de213fbe"
FILES = {
    "config.json": "7187e9fb84193c2780769227ba07eeb1b74b474a52cd5f9d537964a566a8dc90",
    "model.pth": "6817f33ceb69480a4a665a91b61461f0aca6dd1397c0ba46ebffbe13c6f5fe80",
    "vocab.json": "df6f3454c184d2d01c5461ac82087da2eacff6aaafc9fdf65b8264550d5cb779",
    "speakers_xtts.pth": "14aec53dbf98ca82d05783785eab4dd2490fe808e391c6c9cda313373afc8801",
    "LICENSE.txt": "190f6d7c19b8984f91b97712b94ce92d2b2e640fc677dacab966e955ece9d043",
}


def provision_model(root: Path):
    from xtts_protocol import file_digest
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, expected in FILES.items():
        destination = root / name
        if destination.exists():
            if destination.is_symlink() or file_digest(destination) != expected:
                raise ValueError("existing model differs: " + name)
            continue
        partial = root / ("." + name + "." + uuid.uuid4().hex + ".part")
        try:
            checksum = hashlib.sha256()
            url = "https://huggingface.co/coqui/XTTS-v2/resolve/" + REVISION + "/" + name
            with urllib.request.urlopen(url, timeout=60) as response, partial.open("xb") as output:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    checksum.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if checksum.hexdigest() != expected:
                raise ValueError("download integrity: " + name)
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)
    from xtts_control import write_config
    write_config(root / "manifest.json", {"repository": "coqui/XTTS-v2", "revision": REVISION,
                 "files": FILES}, actor="xtts-installer")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path("/opt/praxis-xtts"))
    parser.add_argument("--live-root", type=Path, default=Path("/opt/praxis"))
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("run explicitly as root on the Linux server")
    if subprocess.run(["systemctl", "is-active", "--quiet", "praxis-xtts.service"]).returncode == 0:
        parser.error("stop the XTTS capability before updating its component")
    source = Path(__file__).resolve().parent
    prefix = args.prefix.resolve()
    prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime = prefix / "runtime"
    python = runtime / "bin/python"
    if not python.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(runtime)], check=True)
    pip = [str(python), "-B", "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check"]
    subprocess.run(pip + ["torch==2.8.0+cpu", "torchaudio==2.8.0+cpu",
                         "--index-url", "https://download.pytorch.org/whl/cpu"], check=True)
    lock = source / "requirements-xtts.lock"
    subprocess.run(pip + ["-r", str(lock if lock.is_file() else source / "requirements-xtts.txt")], check=True)
    subprocess.run([str(python), "-B", "-m", "pip", "check"], check=True)
    (prefix / "requirements.lock").write_bytes(subprocess.check_output([str(python), "-B", "-m", "pip", "freeze"]))
    provision_model(prefix / "model")
    from xtts_control import write_config
    if not (prefix / "profile.json").exists():
        write_config(prefix / "profile.json", {"speaker": "", "reference": "", "language": "ru"}, actor="xtts-installer")
    home = args.live_root.resolve() / "memory/.state/xtts"
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (home / "selection.json").exists():
        write_config(home / "selection.json", {"schema": "praxis.xtts.selection.v1", "enabled": False}, actor="xtts-installer")
    token = args.live_root.resolve() / "memory/.secrets/xtts-client.token"
    token.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not token.exists():
        with token.open("x") as stream:
            stream.write(secrets.token_hex(32))
        token.chmod(0o600)
    code = prefix / "code"
    code.mkdir(exist_ok=True, mode=0o700)
    for name in ("xtts_protocol.py", "xtts_engine.py", "xtts_service.py", "xtts_profiles.py", "xtts_voice_rpc.py", "xtts_stream.py"):
        temporary = code / ("." + name + ".part")
        shutil.copyfile(source / name, temporary)
        temporary.replace(code / name)
    unit = (source / "xtts.service").read_text()
    unit = unit.replace("/opt/praxis/", str(args.live_root.resolve()) + "/").replace("/opt/praxis-xtts", str(prefix))
    Path("/etc/systemd/system/praxis-xtts.service").write_text(unit)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    selection = json.loads((home / "selection.json").read_text())
    print(json.dumps({"installed": True, "selected": selection.get("enabled") is True, "service_started": False,
                      "next": "xtts_control.py configure, then enable"}))


if __name__ == "__main__":
    main()
