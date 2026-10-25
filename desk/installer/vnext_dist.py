"""Bind a pinned vNext source composition to the existing distribution payload.

This is a read-side join, not a second packer or a release approval. The
composer owns source selection; build_dist owns staging, scans, ZIP and setup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

import vnext_candidate
import relay_src


class SourceMismatch(ValueError):
    """The selected edition or copied distribution no longer matches its source."""


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _inventory(root: Path) -> dict[str, str]:
    if not root.is_dir() or root.is_symlink():
        raise SourceMismatch("edition or payload directory unavailable or linked")
    found = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise SourceMismatch("linked path in edition or payload: " + str(path.relative_to(root)))
        if path.is_file():
            found[path.relative_to(root).as_posix()] = _sha(path)
    return found


def _generated_python_cache(rel: str) -> bool:
    return "__pycache__" in rel.split("/") or rel.endswith((".pyc", ".pyo"))


def _digest(files: dict[str, str]) -> str:
    return hashlib.sha256("\n".join(f"{name} {value}" for name, value in sorted(files.items()))
                           .encode("utf-8")).hexdigest()


DESK_BUILD_KIND = "vnext_desk_build_v1"
DESK_EXES = (("shell", "helene.exe"), ("svc", "helene-svc.exe"),
             ("setup", "helene-setup.exe"))
_DESK_SKIP_DIRS = {"node_modules", "target", "build", "__pycache__", ".git",
                   ".claude", ".venv", "venv", ".pytest_cache", "logs", "data"}
_DESK_SKIP_FILES = {"secret-strings.txt", ".env", ".env.local"}


def _linked(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0)
                                         & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def desk_inputs(desk: Path) -> tuple[str, int]:
    """Byte inventory of the desk source and generated fronts, not build outputs.

    This deliberately includes adjacent shared Rust, UI, server and installer
    source: a same-version edit cannot reuse an older exe/front receipt.
    """
    desk = Path(desk).resolve()
    found = {}
    for base, dirs, files in os.walk(desk, followlinks=False):
        parent = Path(base)
        for name in dirs:
            if name not in _DESK_SKIP_DIRS and _linked(parent / name):
                raise SourceMismatch("linked desk build input directory: " + name)
        dirs[:] = sorted(name for name in dirs if name not in _DESK_SKIP_DIRS)
        for name in sorted(files):
            if name in _DESK_SKIP_FILES or name.startswith(".env") or name.endswith((".pyc", ".pyo")):
                continue
            path = parent / name
            if _linked(path) or not path.is_file():
                raise SourceMismatch("linked/nonregular desk build input: " + str(path.relative_to(desk)))
            found[path.relative_to(desk).as_posix()] = _sha(path)
    if not found:
        raise SourceMismatch("empty desk build inputs")
    return _digest(found), len(found)


def desk_executables(desk: Path) -> dict[str, str]:
    products = {}
    for part, exe in DESK_EXES:
        path = Path(desk) / part / "target" / "release" / exe
        if not path.is_file() or _linked(path):
            raise SourceMismatch("desk binary missing: " + exe)
        products[exe] = _sha(path)
    return products


def stamp_desk_build(desk: Path, receipt: Path) -> dict:
    """Only successful frontend and three-crate build orchestration calls this."""
    before = desk_inputs(desk)
    exes = desk_executables(desk)
    if desk_inputs(desk) != before or desk_executables(desk) != exes:
        raise SourceMismatch("desk source/binaries moved during build receipt")
    note = {"kind": DESK_BUILD_KIND, "source_sha256": before[0],
            "source_files": before[1], "exes": exes}
    Path(receipt).parent.mkdir(parents=True, exist_ok=True)
    Path(receipt).write_text(json.dumps(note, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return note


def verify_desk_build(desk: Path, receipt: Path) -> dict:
    try:
        note = json.loads(Path(receipt).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SourceMismatch("desk build receipt unavailable") from exc
    digest, count = desk_inputs(desk)
    if (not isinstance(note, dict) or note.get("kind") != DESK_BUILD_KIND
            or note.get("source_sha256") != digest or note.get("source_files") != count
            or note.get("exes") != desk_executables(desk)):
        raise SourceMismatch("desk build source or executables differ from receipt")
    return {field: note[field] for field in ("kind", "source_sha256", "source_files", "exes")}


def verify_edition(repo: Path, head: str, edition: Path) -> dict:
    """Re-read all selected bytes and their Git blobs, not just a manifest name."""
    repo, edition = Path(repo).resolve(), Path(edition).resolve()
    if edition == repo or edition.is_relative_to(repo):
        raise SourceMismatch("edition must be outside its source checkout")
    note_path = edition / ".vnext-source.json"
    try:
        note = json.loads(note_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SourceMismatch("vNext source manifest unavailable") from exc
    if (not isinstance(note, dict) or note.get("kind") != "vnext_source_only"
            or note.get("base_head") != head or note.get("allowed_dirty") != []
            or not isinstance(note.get("files"), dict)):
        raise SourceMismatch("edition is not a clean composition of pinned HEAD")
    try:
        selected = vnext_candidate._sources(repo, head, set())
    except (OSError, ValueError) as exc:
        raise SourceMismatch("pinned source checkout moved: " + str(exc)) from exc
    # The product test gate imports the edition and may leave bytecode. Those
    # files are not source and copy_tree never ships them; any other extra is
    # still a source-selection mismatch.
    actual = {name: sha for name, sha in _inventory(edition).items()
              if not _generated_python_cache(name)}
    actual.pop(".vnext-source.json", None)
    if set(actual) != set(selected) or set(note["files"]) != set(selected):
        raise SourceMismatch("edition inventory differs from selected Git sources")
    rows = {}
    for rel, (origin, source) in selected.items():
        row = note["files"].get(rel)
        if (not isinstance(row, dict) or row.get("source") != origin
                or row.get("sha256") != actual[rel] or row.get("bytes") != (edition / rel).stat().st_size
                or _sha(source) != actual[rel]):
            raise SourceMismatch("edition source bytes moved: " + rel)
        rows[rel] = row
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if note.get("digest") != hashlib.sha256(encoded).hexdigest():
        raise SourceMismatch("edition aggregate digest mismatch")
    return {"head": head, "manifest_sha256": _sha(note_path),
            "source_digest": note["digest"], "source_files": len(rows), "files": actual}


def verify_copied(edition: dict, staged_tree: Path, excluded) -> dict:
    """Compare the actual staged tree BEFORE build_dist adds LICENSE/NOTICE.

    A file absent by the packer's explicit policy is not an unexplained omission.
    Source and final-payload identities are reported separately.
    """
    expected = {name: sha for name, sha in edition["files"].items()
                if name != ".vnext-source.json" and not excluded(name)}
    actual = _inventory(Path(staged_tree))
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        moved = sorted(name for name in set(expected) & set(actual) if actual[name] != expected[name])
        raise SourceMismatch(f"staged vNext tree differs: missing={missing[:5]}, extra={extra[:5]}, moved={moved[:5]}")
    return {"shipped_source_files": len(actual), "staged_source_digest": _digest(actual),
            "excluded_source_files": len(edition["files"]) - len(actual)}


def final_payload(staged_tree: Path) -> dict:
    """Identity after the packer has added its license and notice copies."""
    files = _inventory(Path(staged_tree))
    return {"files": len(files), "sha256": _digest(files)}


RELAY_PATH = "praxis/relay"


def _relay_blobs(repo: Path, head: str) -> dict[str, bytes]:
    if vnext_candidate._git(repo, "rev-parse", "HEAD").decode("ascii").strip() != head:
        raise SourceMismatch("relay source HEAD moved")
    found = {}
    records = vnext_candidate._git(repo, "ls-tree", "-r", "-z", head, "--", RELAY_PATH)
    for record in records.split(b"\0"):
        if not record:
            continue
        header, name = record.split(b"\t", 1)
        mode, kind, _oid = header.split()
        if mode not in (b"100644", b"100755") or kind != b"blob":
            raise SourceMismatch("relay contains nonregular tracked input")
        rel = name.decode("utf-8").removeprefix(RELAY_PATH + "/")
        found[rel] = vnext_candidate._git(repo, "show", f"{head}:{name.decode('utf-8')}")
    if not {"Cargo.toml", "Cargo.lock"}.issubset(found):
        raise SourceMismatch("selected tracked relay has no Cargo manifest/lock")
    return found


def verify_relay(repo: Path, head: str, staged: Path, *, kind: str = "tracked") -> dict:
    """A newly selected tracked adaptation, not the historical deployed relay."""
    repo, staged = Path(repo).resolve(), Path(staged).resolve()
    if staged == repo or staged.is_relative_to(repo):
        raise SourceMismatch("staged relay must be outside the source checkout")
    if kind == "embedded":
        import embedded_relay
        return embedded_relay.verify(repo, head, staged)
    if kind != "tracked":
        raise SourceMismatch("unknown relay source kind")
    expected = _relay_blobs(repo, head)
    source = repo / RELAY_PATH
    for rel, content in expected.items():
        if (source / rel).is_symlink() or (source / rel).read_bytes() != content:
            raise SourceMismatch("tracked relay source moved: " + rel)
        if (staged / rel).is_symlink() or (staged / rel).read_bytes() != content:
            raise SourceMismatch("staged relay source moved: " + rel)
    try:
        original = relay_src.build_inputs(source)
        copied = relay_src.build_inputs(staged)
    except (OSError, ValueError) as exc:
        raise SourceMismatch("relay build inputs unavailable: " + str(exc)) from exc
    if original != copied:
        raise SourceMismatch("relay build inputs differ from pinned tracked adaptation")
    note = relay_src.stamp(staged)
    legacy, files = relay_src.digest(staged)
    if (note.get("kind") != "vnext_tracked_relay" or note.get("head") != head
            or note.get("digest") != legacy or note.get("files") != len(files)
            or note.get("input_sha256") != copied[0] or note.get("input_files") != copied[1]):
        raise SourceMismatch("selected relay source receipt does not bind current bytes")
    return {"kind": "new_tracked_adaptation", "head": head,
            "source": RELAY_PATH, "input_digest_kind": relay_src.INPUT_DIGEST_KIND,
            "input_sha256": copied[0], "input_files": copied[1]}


def stage_relay(repo: Path, head: str, destination: Path) -> dict:
    """Make a disposable, exact-HEAD relay build tree, without changing Git or live."""
    repo, destination = Path(repo).resolve(), Path(destination).resolve()
    if destination.exists() or destination == repo or destination.is_relative_to(repo):
        raise SourceMismatch("relay destination occupied or inside source checkout")
    blobs = _relay_blobs(repo, head)
    source = repo / RELAY_PATH
    # Check the complete source inventory as well as HEAD-listed files: ignored
    # eligible inputs must not enter a cargo/Docker build without Git identity.
    actual = relay_src.build_inputs(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".vnext-relay-", dir=destination.parent))
    try:
        for rel, content in blobs.items():
            path = source / rel
            if path.is_symlink() or path.read_bytes() != content:
                raise SourceMismatch("tracked relay source moved: " + rel)
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        if relay_src.build_inputs(work) != actual:
            raise SourceMismatch("untracked or ignored relay build inputs present")
        legacy, files = relay_src.digest(work)
        (work / relay_src.STAMP).write_text(json.dumps({
            "kind": "vnext_tracked_relay", "head": head, "origin": RELAY_PATH,
            "digest": legacy, "files": len(files), "input_sha256": actual[0],
            "input_files": actual[1]}, sort_keys=True) + "\n", encoding="utf-8")
        if destination.exists():
            raise SourceMismatch("relay destination appeared during staging")
        os.rename(work, destination)
        return verify_relay(repo, head, destination)
    finally:
        if work.exists():
            shutil.rmtree(work)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare/check exact sources for the existing Windows dist builder")
    parser.add_argument("action", choices=("stage", "check"))
    parser.add_argument("--root", type=Path, default=vnext_candidate.ROOT)
    parser.add_argument("--head", required=True)
    parser.add_argument("--out", type=Path, required=True, help="private NEW directory outside the source checkout")
    parser.add_argument("--relay-kind", choices=("tracked", "embedded"), default="tracked")
    parser.add_argument("--embedded-source", type=Path, help="exact embedded hotfix source directory, staging only")
    args = parser.parse_args()
    edition, relay = args.out / "edition", args.out / "relay"
    try:
        if args.action == "stage":
            if args.out.exists():
                raise SourceMismatch("source staging destination already exists")
            args.out.mkdir(parents=True)
            vnext_candidate.stage(edition, root=args.root, expected_head=args.head)
            if args.relay_kind == "embedded":
                if not args.embedded_source:
                    raise SourceMismatch("embedded relay staging requires --embedded-source")
                import embedded_relay
                embedded_relay.stage(args.root, args.head, args.embedded_source, relay)
            else:
                if args.embedded_source:
                    raise SourceMismatch("--embedded-source requires --relay-kind embedded")
                stage_relay(args.root, args.head, relay)
        current = {"edition": {k: v for k, v in verify_edition(args.root, args.head, edition).items()
                               if k != "files"},
                   "relay": verify_relay(args.root, args.head, relay, kind=args.relay_kind)}
        print(json.dumps(current, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print("vNext distribution sources REFUSED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
