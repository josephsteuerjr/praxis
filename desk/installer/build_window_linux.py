"""Stage the Electron window and shared Rust host for the Linux package."""
from __future__ import annotations
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

DESK = Path(__file__).resolve().parent.parent

def electron_version() -> str:
    package = json.loads((DESK / "electron/package.json").read_text(encoding="utf-8"))
    version = package["devDependencies"]["electron"]
    if not __import__("re").fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("Electron must be pinned to an exact release")
    return version

def safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    for member in members:
        path = PurePosixPath(member.filename)
        kind = (member.external_attr >> 16) & 0o170000
        if path.is_absolute() or ".." in path.parts or "\\" in member.filename or kind == stat.S_IFLNK:
            raise SystemExit("unsafe Electron archive member: " + member.filename)
    return members

def stage_window(out: Path, cache: Path, version: str, skip_rust: bool = False) -> dict:
    import build_dist as bd
    import build_mac as bm
    electron = electron_version()
    name = f"electron-v{electron}-linux-x64.zip"
    base = f"https://github.com/electron/electron/releases/download/v{electron}"
    sums = cache / f"electron-{electron}-SHASUMS256.txt"
    archive = cache / name
    if not sums.is_file(): bm.fetch(f"{base}/SHASUMS256.txt", sums)
    entries = dict((line.split(None, 1)[1].strip().lstrip("*"), line.split(None, 1)[0])
                   for line in sums.read_text(encoding="utf-8").splitlines() if len(line.split(None, 1)) == 2)
    digest = entries.get(name, "")
    if not __import__("re").fullmatch(r"[0-9a-f]{64}", digest):
        raise SystemExit("Electron publisher checksum is missing: " + name)
    if not archive.is_file(): bm.fetch(f"{base}/{name}", archive)
    if bd.sha256(archive) != digest:
        raise SystemExit("Electron checksum mismatch: " + str(archive))
    destination = out / "electron"
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(destination, members=safe_members(z))
    executable = destination / "electron"
    if not executable.is_file(): raise SystemExit("Electron archive has no executable")
    executable.rename(destination / "helene-window")
    for item in ("helene-window", "chrome_crashpad_handler", "chrome-sandbox"):
        path = destination / item
        if path.is_file(): path.chmod(0o4755 if item == "chrome-sandbox" else 0o755)
    # Install scripts are deliberately skipped: Electron is the checksum-verified zip above.
    bm.run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=DESK / "electron", timeout=900)
    bm.run(["node", "build.mjs"], cwd=DESK / "electron", timeout=180)
    app = destination / "resources/app"
    app.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DESK / "electron/out", app / "out", dirs_exist_ok=True)
    (app / "package.json").write_text(json.dumps({"name": "helene", "productName": "Hélène", "version": version,
        "main": "out/main.js"}, ensure_ascii=False) + "\n", encoding="utf-8")
    shutil.copytree(DESK / "shell/icons", destination / "resources/icons", dirs_exist_ok=True)
    host_target = cache / "host-target"
    if not skip_rust:
        bm.run(["cargo", "build", "--locked", "--release", "--no-default-features", "--features", "host",
                "--bin", "helene-host", "--target-dir", host_target], cwd=DESK / "shell", timeout=5400)
    host = host_target / "release/helene-host"
    if not host.is_file(): raise SystemExit("missing headless Rust host: " + str(host))
    shutil.copy2(host, out / "helene-host")
    (out / "helene-host").chmod(0o755)
    (out / "helene").write_text('#!/bin/sh\nset -eu\nhere=$(dirname -- "$(readlink -f -- "$0")")\nexec "$here/electron/helene-window" "$@"\n', encoding="utf-8")
    (out / "helene").chmod(0o755)
    return {"backend": "electron", "electron": electron, "archive_sha256": digest,
            "shared_host": True, "stationary_touchpad_hold": "unavailable",
            "hardware_accepted": False}
