"""Install bundled native components without replacing Python, UI or agent data.

components.json describes exact files in each component's binary/source/toolchain
closure. Installation is local and offline. Rebuildability means actual source
and compiler bytes in the bundle, not a URL promised for later.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

NAMES = {"relay"}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as reader:
        while chunk := reader.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def manifest(program: Path):
    value = json.loads((program / "components" / "components.json").read_text(encoding="utf-8"))
    if value.get("format") != "helene-components.v1" or set(value.get("components", {})) != NAMES:
        raise ValueError("invalid Hélène component bundle")
    return value["components"]


def verify(program: Path, name: str):
    if name not in NAMES:
        raise ValueError("unknown component")
    entry = manifest(program)[name]
    if isinstance(entry.get("unavailable"),str) and entry["unavailable"]:
        raise ValueError(entry["unavailable"])
    files = entry.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("empty component inventory")
    base = program / "components" / name
    for group in ("binary", "source"):
        if not any(p.startswith(group + "/") for p in files):
            raise ValueError(f"component lacks {group}: {name}")
    if entry.get("shared_toolchain") and entry.get("toolchain_optional") is not True:
        shared = program / "components" / "toolchain"
        inventory = json.loads((shared / "files.json").read_text(encoding="utf-8"))
        if not inventory or not (shared / 'rebuild_native.py').is_file():
            raise ValueError('offline toolchain inventory unavailable')
        # The compiler is an accompanying rebuild kit, not an executable input
        # to copying this component. Its digest inventory remains available to
        # the rebuild consumer without hashing gigabytes on every Install click.
    elif not entry.get("shared_toolchain") and not any(p.startswith("toolchain/") for p in files):
        raise ValueError("component lacks toolchain: " + name)
    for relative, expected in files.items():
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
            raise ValueError("invalid component path")
        source = base / path
        if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(base.resolve()):
            raise ValueError("component file unavailable: " + relative)
        if digest(source) != expected:
            raise ValueError("component changed: " + relative)
    executable = str(entry["executable"])
    if executable not in files or not executable.startswith("binary/"):
        raise ValueError("component executable missing from inventory")
    install_name = str(entry["install_name"])
    if Path(install_name).name != install_name or ":" in install_name or "\\" in install_name:
        raise ValueError("invalid component destination")
    return entry


def status(program: Path, owner: Path):
    rows = []
    for name in sorted(NAMES):
        try:
            # A passive Settings/startup read does not hash the compiler and
            # its thousands of dependencies. Installation verifies the closure.
            entry = manifest(program)[name]
            if isinstance(entry.get("unavailable"),str) and entry["unavailable"]:
                rows.append({"id":name,"ready":False,"installed":False,"unavailable":True,"error":entry["unavailable"]})
                continue
            install_name = str(entry["install_name"])
            if Path(install_name).name != install_name or ":" in install_name or "\\" in install_name:
                raise ValueError("invalid component destination")
            target = owner / install_name
            expected = entry["files"][entry["executable"]]
            installed = target.is_file() and digest(target) == expected
            rows.append({"id": name, "ready": True, "installed": installed,
                         "path": str(target), "source": str(program / "components" / name / "source"),
                         "toolchain_optional":entry.get("toolchain_optional") is True,
                         "toolchain_present":(program/'components/toolchain/files.json').is_file(),
                         "toolchain": str(program / "components" / ("toolchain" if entry.get("shared_toolchain") else name + "/toolchain"))})
        except (OSError, ValueError, KeyError) as exc:
            rows.append({"id": name, "ready": False, "installed": False, "error": str(exc)})
    return rows


def install(program: Path, owner: Path, name: str):
    entry = verify(program, name)
    install_name = str(entry["install_name"])
    if Path(install_name).name != install_name or ":" in install_name or "\\" in install_name:
        raise ValueError("invalid component destination")
    source = program / "components" / name / entry["executable"]
    target = owner / install_name
    # No service start/replacement here. The app stops its host before changing a
    # live organ; OS file-in-use refusal retains the old installed bytes.
    handle, raw = tempfile.mkstemp(prefix=".component-", dir=owner)
    os.close(handle)
    staged = Path(raw)
    try:
        shutil.copy2(source, staged)
        if digest(staged) != entry["files"][entry["executable"]]:
            raise ValueError("component copy changed")
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)
    return {"ok": True, "component": name, "path": str(target), "restart_required": True}
