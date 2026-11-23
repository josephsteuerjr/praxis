"""Capture a Git tree and restore its exact commit from an extracted release."""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import zipfile
import zlib

from privacy import check as check_release_member


def git(root, *args, data=None):
    return subprocess.run(["git", "-C", str(root), *args], input=data,
                          stdout=subprocess.PIPE, check=True).stdout


def path_name(name):
    p = PurePosixPath(name)
    if (not p.parts or p.is_absolute() or p.as_posix() != name or "\\" in name
            or ":" in name or any(part.endswith((" ", ".")) or part.lower() in (".git", "..", "git~1")
                                    or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]} for part in p.parts)
            or any(ord(c) < 32 for c in name)):
        raise ValueError("Unsafe release member: " + repr(name))
    return name


def rows(root):
    result = {}
    for record in git(root, "ls-tree", "-rz", "HEAD").split(b"\0"):
        if not record:
            continue
        header, raw_name = record.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        name = path_name(raw_name.decode())
        if kind != "blob" or mode not in ("100755", "100644"):
            raise ValueError("Release requires regular tracked files: " + name)
        result[name] = [mode, oid]
    return result


def blob_id(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def archive(root, destination, version):
    if git(root, "status", "--porcelain").strip():
        raise ValueError("Commit the release sources before packaging")
    members = rows(root)
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    raw_commit = git(root, "cat-file", "commit", commit)
    origin = {"version": version, "commit": commit, "git_commit": base64.b64encode(raw_commit).decode(),
              "tree": git(root, "rev-parse", "HEAD^{tree}").decode().strip(), "files": members}
    prefix = f"Helene-{version}-source/"
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        staged = Path(tmp) / "source.zip"
        with zipfile.ZipFile(staged, "x", zipfile.ZIP_DEFLATED) as z, subprocess.Popen(
                ["git", "-C", str(root), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE) as batch:
            for name, (mode, oid) in members.items():
                batch.stdin.write((oid + "\n").encode())
                batch.stdin.flush()
                header = batch.stdout.readline().split()
                if header[:2] != [oid.encode(), b"blob"]:
                    raise ValueError("Missing Git blob: " + name)
                data = batch.stdout.read(int(header[2]))
                if batch.stdout.read(1) != b"\n" or blob_id(data) != oid:
                    raise ValueError("Git blob mismatch: " + name)
                check_release_member(name, data)
                info = zipfile.ZipInfo(prefix + name)
                info.create_system = 3
                info.external_attr = int(mode, 8) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, data)
            batch.stdin.close()
            if batch.wait():
                raise RuntimeError("git cat-file failed")
            info = zipfile.ZipInfo(prefix + "RELEASE-ORIGIN.json")
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, json.dumps(origin, sort_keys=True))
        with zipfile.ZipFile(staged) as z:
            if z.testzip() or len(z.namelist()) != len(members) + 1:
                raise ValueError("Invalid source archive")
        try:
            os.link(staged, destination)
        except FileExistsError:
            with staged.open("rb") as left, destination.open("rb") as right:
                if hashlib.file_digest(left, "sha256").digest() != hashlib.file_digest(right, "sha256").digest():
                    raise ValueError("Concurrent source archive belongs to a different commit")
    return commit


def restore(extracted, destination, expected):
    extracted, destination = extracted.resolve(), destination.resolve()
    if destination.exists() or destination.is_relative_to(extracted):
        raise ValueError("Choose a new work directory outside the extracted archive")
    origin = json.loads((extracted / "RELEASE-ORIGIN.json").read_bytes())
    if origin["commit"] != expected:
        raise ValueError("Source archive differs from the expected release commit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        work = Path(tmp) / "work"
        work.mkdir()
        git(work, "init", "-q", "-b", "release-work")
        git(work, "config", "core.autocrlf", "false")
        index = bytearray()
        for name, (mode, oid) in origin["files"].items():
            path_name(name)
            src = extracted / name
            if (mode not in ("100644", "100755") or not src.resolve().is_relative_to(extracted)
                    or src.is_symlink() or not src.is_file()):
                raise ValueError("Unexpected source entry: " + name)
            data = src.read_bytes()
            if blob_id(data) != oid:
                raise ValueError("Extract a fresh copy before editing: " + name)
            # Write the exact image of this blob, independent of checkout EOL filters.
            loose = work / ".git/objects" / oid[:2] / oid[2:]
            if not loose.exists():
                loose.parent.mkdir(exist_ok=True)
                loose.write_bytes(zlib.compress(b"blob " + str(len(data)).encode() + b"\0" + data))
            index.extend(f"{mode} {oid}\t{name}\0".encode())
            dst = work / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            dst.chmod(int(mode, 8) & 0o777)
        git(work, "update-index", "-z", "--index-info", data=bytes(index))
        tree = git(work, "write-tree").decode().strip()
        raw = base64.b64decode(origin["git_commit"], validate=True)
        oid = git(work, "hash-object", "-t", "commit", "-w", "--stdin", data=raw).decode().strip()
        if tree != origin["tree"] or oid != expected or git(work, "rev-parse", oid + "^{tree}").decode().strip() != tree:
            raise ValueError("Source inventory does not reconstruct the exact commit")
        (work / ".git/shallow").write_text(oid + "\n", encoding="ascii")
        git(work, "update-ref", "HEAD", oid)
        work.rename(destination)
    return expected
