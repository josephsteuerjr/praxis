"""Selected Windows filesystem executor for Hélène's existing CU body protocol.

This is not a controller: one private stdin request, one stdout receipt, no
credential and no native fallback. The body owns request admission and replay.
A filesystem operation is not necessarily atomic with its optimistic hash check:
other writers do not take this process's lock. Failures after a replacement may
leave the effect unknown and must be read back, not automatically retried.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat as statmod
import sys
import time
import uuid

MAX_REQUEST = 64 * 1024 * 1024
MAX_READ = 8 * 1024 * 1024
MAX_LIST = 4096
MAX_SCAN = 100000
MAX_CREATED_REPORTED = 64
READONLY = 0x1
REPARSE = 0x400
MOVEFILE_REPLACE_EXISTING = 0x1
MOVEFILE_WRITE_THROUGH = 0x8
SELECTED = frozenset(("fs.stat", "fs.list", "fs.read", "fs.hash",
                      "fs.write_atomic", "fs.replace", "fs.mkdir"))


class Refusal(Exception):
    """Known refusal with the named preparatory effect (if any)."""
    def __init__(self, message: str, effect: str = "none"):
        super().__init__(message)
        self.effect = effect


def _path(args: dict) -> Path:
    value = args.get("path")
    if not isinstance(value, str):
        raise Refusal("path must be a string")
    return Path(value)


def _kind(info) -> str:
    attrs = getattr(info, "st_file_attributes", 0)
    if attrs & REPARSE or statmod.S_ISLNK(info.st_mode):
        return "reparse"
    if statmod.S_ISDIR(info.st_mode):
        return "directory"
    if statmod.S_ISREG(info.st_mode):
        return "file"
    return "other"


def _hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _existing_hash(path: Path) -> str | None:
    # Same optimistic precondition as the native implementation: existence is
    # checked before staging, not atomically locked against unrelated writers.
    return _hash(path)[0] if path.exists() else None


def _expected(actual: str | None, expected) -> None:
    if expected is None:
        return
    if not isinstance(expected, str):
        raise Refusal("expected_sha256 must be a string")
    if actual != expected:
        raise Refusal(f"hash conflict: expected {expected}, actual {actual or '<missing>'}")


def _backup(path: Path) -> Path:
    saved = path.parent / f".{path.name}.praxis-backup-{uuid.uuid4()}"
    shutil.copyfile(path, saved)
    return saved


def _replace(source: Path, destination: Path) -> None:
    if os.name != "nt":
        # The body selects this CLI only on Windows. This branch allows pure
        # contract tests on POSIX without asserting POSIX execution support.
        os.replace(source, destination)
        return
    move = ctypes.windll.kernel32.MoveFileExW
    move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    move.restype = ctypes.c_int
    if not move(str(source), str(destination), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH):
        raise OSError(ctypes.get_last_error(), "commit atomic write")


def _atomic_write(path: Path, content: bytes, phase: dict) -> None:
    parent = path.parent
    # A partly created parent chain is possible even if mkdir raises.
    phase["effect"] = "precommit"
    parent.mkdir(parents=True, exist_ok=True)
    # Parent directories may have been created even when the destination is
    # preserved on a later, controlled precommit failure.
    stage = parent / f".{path.name}.praxis-part-{uuid.uuid4()}"
    try:
        with stage.open("xb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        _replace(stage, path)
        phase["effect"] = "unknown"  # readback/hash may fail after commit
    finally:
        if stage.exists():
            stage.unlink()


def _read(args: dict) -> dict:
    path = _path(args)
    offset = args.get("offset", 0)
    limit = args.get("limit", 1024 * 1024)
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 0:
        raise Refusal("offset and limit must be nonnegative integers")
    limit = max(1, min(MAX_READ, limit))
    with path.open("rb") as source:
        size = os.fstat(source.fileno()).st_size
        if offset > size:
            raise Refusal(f"offset {offset} is beyond file size {size}")
        source.seek(offset)
        data = source.read(min(limit, size - offset))
    text = data.decode("utf-8", "replace")
    return {"ok": True, "path": str(path), "offset": offset, "size": size,
            "next_offset": offset + len(data), "eof": offset + len(data) == size,
            "text": text, "lossy": text.encode("utf-8") != data}


def _list(args: dict) -> dict:
    path = _path(args)
    offset = args.get("offset", 0)
    limit = args.get("limit", 1000)
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 0:
        raise Refusal("offset and limit must be nonnegative integers")
    limit = max(1, min(MAX_LIST, limit))
    entries = []
    with os.scandir(path) as listing:
        for entry in listing:
            if len(entries) >= MAX_SCAN:
                raise Refusal(f"directory contains more than {MAX_SCAN} entries; use rg or a narrower path")
            # Rust DirEntry.metadata does not follow a link; a dangling link
            # must not make the whole listing fail or borrow its target's kind.
            info = entry.stat(follow_symlinks=False)
            kind = ("directory" if statmod.S_ISDIR(info.st_mode) else
                    "file" if statmod.S_ISREG(info.st_mode) else "other")
            entries.append({"name": entry.name, "path": str(Path(entry.path)),
                            "kind": kind, "size": info.st_size})
    entries.sort(key=lambda row: row["name"])
    total = len(entries)
    start = min(offset, total)
    end = min(start + limit, total)
    return {"ok": True, "path": str(path), "offset": start, "limit": limit,
            "total": total, "next_offset": end, "eof": end == total,
            "entries": entries[start:end]}


def _mkdir(args: dict, phase: dict) -> dict:
    path = _path(args)
    parents = args.get("parents", True)
    if type(parents) is not bool:
        raise Refusal("parents must be a boolean")
    extra = sorted(key for key in args if key not in ("path", "parents"))
    started = time.monotonic()
    limits = {"max_created_reported": MAX_CREATED_REPORTED}
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if (_kind(info) == "directory" or
                ((path.is_symlink() or path.is_junction()) and path.is_dir())):
            return {"ok": True, "path": str(path), "existed": True, "parents": parents,
                    "created": [], "created_count": 0, "created_truncated": False,
                    "complete": True, "failed_count": 0, "failed": [],
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "summary": f"{path} already exists as a directory; nothing was created",
                    "ignored_args": extra, "limits": limits}
        raise Refusal(f"{path} already exists and it is a {_kind(info)}, not a directory")
    missing = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    missing.reverse()
    if not parents and len(missing) > 1:
        raise Refusal(f"parent directory {missing[-2]} does not exist: this call creates one level. "
                      f"Repeat with parents=true to create the whole chain ({len(missing) - 1} missing level(s)).")
    created = []
    failed = []
    for current in missing:
        try:
            current.mkdir()
            created.append(current)
            phase["effect"] = "precommit"
        except OSError as error:
            failed.append({"path": str(current), "error": str(error)})
            break
    complete = not failed
    count = len(created)
    summary = (f"created {count} directory(ies), deepest is {path}" if complete else
               f"PARTIAL: created {count} directory(ies) and then failed before reaching {path}")
    return {"ok": complete, "path": str(path), "existed": False, "parents": parents,
            "created": [str(p) for p in created[:MAX_CREATED_REPORTED]], "created_count": count,
            "created_truncated": count > MAX_CREATED_REPORTED, "complete": complete,
            "failed_count": len(failed), "failed": failed,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "summary": summary, "ignored_args": extra, "limits": limits}


def dispatch(capability: str, args: dict, *, phase: dict | None = None) -> dict:
    """The phase is explicit so a controlled exception can disclose earlier setup."""
    if capability not in SELECTED:
        raise Refusal("unsupported Python CU filesystem capability")
    if not isinstance(args, dict):
        raise Refusal("CU filesystem arguments must be an object")
    phase = phase if phase is not None else {"effect": "none"}
    path = _path(args)
    if capability == "fs.stat":
        info = path.lstat()
        return {"ok": True, "path": str(path), "kind": _kind(info), "size": info.st_size,
                "readonly": bool(getattr(info, "st_file_attributes", 0) & READONLY)
                            if os.name == "nt" else not bool(info.st_mode & statmod.S_IWUSR),
                "modified_unix_ms": info.st_mtime_ns // 1_000_000}
    if capability == "fs.list":
        return _list(args)
    if capability == "fs.read":
        return _read(args)
    if capability == "fs.hash":
        digest, size = _hash(path)
        return {"ok": True, "path": str(path), "sha256": digest, "size": size}
    if capability == "fs.mkdir":
        return _mkdir(args, phase)
    expected = args.get("expected_sha256")
    before = _existing_hash(path)
    _expected(before, expected)
    if capability == "fs.replace":
        old, new = args.get("old"), args.get("new")
        if not isinstance(old, str) or not isinstance(new, str):
            raise Refusal("old and new must be strings")
        # Read and decode STRICTLY, like Rust's read_to_string. Prevalidate the
        # entire replacement before creating backup, stage or parent directories.
        source = path.read_bytes().decode("utf-8", errors="strict")
        matches = source.count(old) if old else len(source) + 1
        if matches != 1:
            raise Refusal(f"exact replace requires one match, found {matches}")
        content = source.replace(old, new, 1).encode("utf-8")
    else:
        value = args.get("content")
        if not isinstance(value, str):
            raise Refusal("content must be a string")
        content = value.encode("utf-8")
    backup_requested = args.get("backup", False)
    if type(backup_requested) is not bool:
        raise Refusal("backup must be a boolean")
    backup = None
    if backup_requested:
        if path.exists() or capability == "fs.replace":
            phase["effect"] = "precommit"  # copy can itself leave an incomplete backup
            backup = _backup(path)
    _atomic_write(path, content, phase)
    after, size = _hash(path)
    return {"ok": True, "path": str(path), "before_sha256": before,
            "after_sha256": after, "size": size,
            "backup": str(backup) if backup else None}


def main() -> None:
    phase = {"effect": "none"}
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise Refusal("CU filesystem request too large")
        request = json.loads(raw)
        result = dispatch(request["capability"], request["args"], phase=phase)
        response = {"ok": True, "result": result}
    except (Refusal, OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        response = {"ok": False, "error": str(error),
                    "effect": phase["effect"] if not isinstance(error, Refusal) else
                    (phase["effect"] if phase["effect"] != "none" else error.effect)}
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False,
                        separators=(",", ":")).encode("utf-8"))


if __name__ == "__main__":
    main()
