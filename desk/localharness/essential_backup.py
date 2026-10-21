"""Small, explicit backup of irreplaceable agent state, not a program image.

The same inventory is used by manual, periodic and migration backups. Existing
archives are never pruned here. Rebuildable code, models, environments and caches
stay outside; a backup is not a replacement for copying a complete data tree
when that tree itself must move during migration.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import zipfile
from pathlib import Path

ESSENTIAL = ("soul", "memory", "dossiers", "graph", "state", "workspace/inbox")
SKIP = {"__pycache__", "node_modules", ".git", ".venv", ".cache", "cache",
        "backups", "shadow", "phone-access", "tool-results", "tmp"}


def configs(root: Path):
    yield root / "helene.json"
    directory = root / "agents"
    if directory.is_dir():
        yield from sorted(directory.glob("*/helene.json"))


def load(path: Path) -> dict:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            value = json.loads(raw.decode(encoding))
            if not isinstance(value, dict):
                raise ValueError(f"config is not an object: {path}")
            return value
        except UnicodeError:
            continue
    raise ValueError(f"cannot decode config: {path}")


def tree_path(config: Path, value: dict) -> Path:
    tree = Path(value.get("tree") or "data")
    return (tree if tree.is_absolute() else config.parent / tree).resolve()


def inventory(root: Path):
    for config in configs(root):
        if not config.is_file():
            continue
        relative = config.relative_to(root).as_posix()
        yield config, relative
        tree = tree_path(config, load(config))
        prefix = "data" if config.parent == root else "agents/" + config.parent.name + "/data"
        for name in ESSENTIAL:
            source = tree / name
            if not source.exists() or source.is_symlink():
                continue
            if source.is_file():
                yield source, f"{prefix}/{name}"
                continue
            for directory, dirs, files in os.walk(source, followlinks=False):
                base = Path(directory)
                dirs[:] = sorted(d for d in dirs if d.lower() not in SKIP and not (base / d).is_symlink())
                for filename in sorted(files):
                    path = base / filename
                    if path.is_symlink() or filename.endswith((".pyc", ".log", ".lock")) or ".log." in filename:
                        continue
                    yield path, prefix + "/" + path.relative_to(tree).as_posix()
        # Telegram sessions and private environment are state, not runtime code.
        for pattern in ("*.session", "*.session-journal", ".env", "telegram/*.session", "telegram/*.session-journal"):
            for path in sorted(tree.glob(pattern)):
                if path.is_file() and not path.is_symlink():
                    yield path, prefix + "/" + path.relative_to(tree).as_posix()


def destination(root: Path) -> Path:
    setting = (load(root / "helene.json").get("backup") or {}).get("dir") or "backups"
    path = Path(setting)
    return (path if path.is_absolute() else root / path).resolve()


def listing(root: Path):
    folder = destination(root)
    items = [{"name": p.name, "bytes": p.stat().st_size, "when": p.stat().st_mtime}
             for p in folder.glob("*.zip") if p.is_file()]
    return {"dir": str(folder), "items": sorted(items, key=lambda r: r["when"], reverse=True)}


def due(root: Path) -> bool:
    days = float((load(root / "helene.json").get("backup") or {}).get("every_days", 7))
    if days <= 0:
        return False
    import time
    newest = max((r["when"] for r in listing(root)["items"]), default=0)
    return time.time() - newest >= days * 86400


def snapshot(root: Path, destination: Path, reason="manual", stopped=lambda: False):
    root = root.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    label = "".join(c if c.isalnum() or c in "-_" else "-" for c in reason)
    target = destination / f"{stamp}-{label}.zip"
    staged = target.with_suffix(".zip.tmp")
    count = size = 0
    try:
        with zipfile.ZipFile(staged, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for source, name in inventory(root):
                if stopped():
                    raise InterruptedError("backup stopped")
                with source.open("rb") as reader, archive.open(name, "w", force_zip64=True) as writer:
                    while chunk := reader.read(64 * 1024):
                        if stopped():
                            raise InterruptedError("backup stopped")
                        writer.write(chunk)
                        size += len(chunk)
                count += 1
        staged.replace(target)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return {"ok": True, "path": str(target), "files": count, "bytes": size,
            "scope": "essential", "includes_runtime": False}
