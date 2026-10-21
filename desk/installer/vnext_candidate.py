"""Stage a source-only vNext edition without re-labeling the frozen 1.7 payload.

The caller pins a Git HEAD and explicitly names every dirty agent-source path it
intends to include. Edition files win over the Praxis mirror, as in the frozen
composer. This is a *source* candidate, not an installer/release passport: it
neither edits HELENE-SOURCE.json nor establishes a production publisher.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import core_src

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = ("praxis", "helene/core")


def _git(root: Path, *args: str) -> bytes:
    # An ambient GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE can make `git -C` prove
    # another checkout. This tool's baseline is the directory it actually copies.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            timeout=60, env=env)
    if result.returncode:
        raise ValueError("Git source identity unavailable (" + " ".join(args[:2]) + ")")
    return result.stdout


def _relative_paths(raw: bytes) -> set[str]:
    return {item.decode("utf-8").replace("\\", "/") for item in raw.split(b"\0") if item}


def _sources(root: Path, expected_head: str, allowed_dirty: set[str]) -> dict[str, tuple[str, Path]]:
    head = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
    if re.fullmatch(r"[0-9a-f]{40}", expected_head) is None or head != expected_head:
        raise ValueError("source HEAD moved; pinned HEAD does not match")
    changed = _relative_paths(_git(root, "diff", "--name-only", "-z", "HEAD", "--", *SOURCE_ROOTS))
    untracked = _relative_paths(_git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", *SOURCE_ROOTS))
    changed |= untracked
    if changed != allowed_dirty:
        raise ValueError("source path drift: unexpected=" + repr(sorted(changed - allowed_dirty))
                         + "; declared-but-unchanged=" + repr(sorted(allowed_dirty - changed)))
    tracked = _relative_paths(_git(root, "ls-files", "--cached", "-z", "--", *SOURCE_ROOTS))
    object_format = _git(root, "rev-parse", "--show-object-format=storage").decode("ascii").strip()
    if object_format not in ("sha1", "sha256"):
        raise ValueError("unsupported Git object format")
    head_blobs = {}
    for record in _git(root, "ls-tree", "-r", "-z", "HEAD", "--", *SOURCE_ROOTS).split(b"\0"):
        if not record:
            continue
        header, name = record.split(b"\t", 1)
        _mode, kind, oid = header.split()
        if kind == b"blob":
            head_blobs[name.decode("utf-8").replace("\\", "/")] = oid.decode("ascii")
    selected: dict[str, tuple[str, Path]] = {}
    for base in SOURCE_ROOTS:
        source_dir = root / base
        if source_dir.is_symlink() or not source_dir.is_dir():
            raise ValueError("source directory unavailable: " + base)
        for item in source_dir.rglob("*"):
            rel = item.relative_to(source_dir).as_posix()
            if item.is_symlink() and not core_src._skip(rel) and not core_src.mirror_only(rel):
                raise ValueError("symlink in source: " + base + "/" + rel)
        for rel, path in core_src.files(source_dir).items():
            source_name = base + "/" + rel
            if source_name not in tracked and source_name not in untracked:
                # In particular, a .gitignored but otherwise eligible file is
                # NOT covered by the HEAD pin or the declared dirty list.
                raise ValueError("source has no Git identity: " + source_name)
            if not path.resolve().is_relative_to(source_dir.resolve()):
                raise ValueError("source escapes root: " + base + "/" + rel)
            if source_name not in allowed_dirty:
                expected_oid = head_blobs.get(source_name)
                if expected_oid is None:
                    raise ValueError("source missing from pinned HEAD: " + source_name)
                content = path.read_bytes()
                h = hashlib.new(object_format)
                h.update(b"blob " + str(len(content)).encode("ascii") + b"\0" + content)
                if h.hexdigest() != expected_oid:
                    # Git diff trusts assume-unchanged/skip-worktree and stat
                    # shortcuts. Compare real bytes to HEAD independently.
                    raise ValueError("undeclared source bytes differ from HEAD: " + source_name)
            selected[rel] = (source_name, path)
    for source_name in tracked:
        base, _, rel = source_name.partition("/")
        # The two roots are multi-component; use prefix selection below.
        if source_name.startswith("helene/core/"):
            base, rel = "helene/core", source_name.removeprefix("helene/core/")
        if (not core_src._skip(rel) and not core_src.mirror_only(rel)
                and not (root / source_name).is_file()):
            raise ValueError("tracked source absent: " + source_name)
    if not selected:
        raise ValueError("empty source candidate")
    return selected


def stage(destination: Path, *, root: Path = ROOT, expected_head: str,
          allowed_dirty: tuple[str, ...] = ()) -> dict:
    """Publish a new complete tree; refuse a destination already present at entry.

    The base SHA is an *external* Git identity, not a digest minted from the bytes
    currently being staged. During authoring, every changed source path is explicit;
    after commit, call again against that HEAD with no allowed_dirty paths.

    This source fixture requires byte-for-byte HEAD identity on unchanged files;
    a clean checkout with CRLF/smudge conversion may refuse safely. The final
    rename is no-replace for an existing directory on Windows; POSIX can replace
    an EMPTY directory created by an independent writer during the rename race.
    Use a private candidate parent, not a shared publication directory.
    """
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("candidate destination already exists")
    if destination == root or root in destination.parents:
        raise ValueError("candidate must be outside the source repository")
    allowed = set(allowed_dirty)
    if any(not x.startswith(("praxis/", "helene/core/")) or ".." in Path(x).parts for x in allowed):
        raise ValueError("dirty source path outside selected roots")
    selected = _sources(root, expected_head, allowed)
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".vnext-candidate-", dir=destination.parent))
    try:
        entries = {}
        for rel, (origin, source) in sorted(selected.items()):
            content = source.read_bytes()
            # Catch a changed source between selection and read; never call an open
            # file that drifted beneath us a stable source revision.
            if source.read_bytes() != content:
                raise ValueError("source moved during capture: " + origin)
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError("copy changed: " + rel)
            entries[rel] = {"source": origin, "sha256": digest, "bytes": len(content)}
        # Reject a writer that changed a file or HEAD during the copy. Git's
        # content diff catches only tracked changes; per-file SHA catches all.
        if _sources(root, expected_head, allowed) != selected:
            raise ValueError("source selection changed during capture")
        for rel, (origin, source) in selected.items():
            if hashlib.sha256(source.read_bytes()).hexdigest() != entries[rel]["sha256"]:
                raise ValueError("source changed during capture: " + origin)
        payload = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        manifest = {"kind": "vnext_source_only", "base_head": expected_head,
                    "allowed_dirty": sorted(allowed), "files": entries,
                    "digest": hashlib.sha256(payload).hexdigest()}
        (work / ".vnext-source.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                                         indent=2) + "\n", encoding="utf-8")
        if destination.exists():
            raise ValueError("candidate destination appeared during capture")
        os.rename(work, destination)
        return manifest
    finally:
        if work.exists():
            shutil.rmtree(work)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--head", required=True, help="exact 40-hex Git HEAD expected by caller")
    p.add_argument("--dirty", action="append", default=[], help="one explicitly included dirty source path")
    a = p.parse_args()
    print(json.dumps(stage(a.out, expected_head=a.head, allowed_dirty=tuple(a.dirty)),
                     ensure_ascii=False))
