#!/usr/bin/env python3
"""Stage an *optional* Windows Rust relay rebuild kit, without touching an installed relay.

A kit is a source/build identity and an offline-Cargo experiment, NOT a claim
that the host blocked build-script network calls, that Microsoft components may
be redistributed, or that a clean Windows machine can build it. Those separate
qualifications are deliberately recorded as unverified. The caller supplies
an exact private mirror and explicit vendor/Rust/native closures; the public
praxis/relay or frozen helene/core/relay is never a fallback.

    python relay_rebuild_kit.py stage --mirror ... --vendor ... --rust ... \
        --native ... --out ...
    python relay_rebuild_kit.py verify --kit ...
    python relay_rebuild_kit.py candidate --kit ... --source ... --out ...

The `candidate` operation accepts an edited copy of source/ and writes a NEW
binary/receipt outside the original kit. It never overwrites the kit or live
program. An updater, owner Stop and rollback remain separate owners.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import uuid

TARGET = "x86_64-pc-windows-msvc"
BINARY = "codex-proxy-server.exe"
MANIFEST = "bundle-manifest.json"
_COMPONENTS = ("source", "vendor", "rust", "native")
_RECEIPTS = {"RELAY-SOURCE.json", "RELAY-BUILD.json", "RELAY-BUILD-LINUX.json"}
_SOURCE_FORBIDDEN = {"auth.json", ".env", ".env.local", ".env.production", "credentials.json"}
_NATIVE_LAYOUT = "layout.json"
_PRIVATE_KEY_MARKER = b"-----BEGIN " + b"PRIVATE KEY-----"


class KitError(ValueError):
    """A source/closure/build claim was not proven; no kit can be qualified."""


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _linked(path: Path) -> bool:
    info = path.lstat()
    return (stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0)
                                               & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)))


def inventory(root: Path, *, source: bool = False, private_mirror: bool = False) -> dict[str, str]:
    """Full byte-exact inventory; only Git, build output and private receipts are omitted.

    Unknown files are not silently ignored. A build script using a file outside
    this tree or another workspace cannot be certified by this manifest; actual
    isolated rebuilding must test that boundary separately.
    """
    if not root.is_dir() or _linked(root):
        raise KitError("closure_missing_or_linked:" + root.name)
    found: dict[str, str] = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        parent = Path(base)
        rel_parent = parent.relative_to(root)
        for directory in sorted(dirs):
            path = parent / directory
            if _linked(path):
                raise KitError("linked_directory:" + str(path.relative_to(root)))
        if source and rel_parent == Path("."):
            if not private_mirror and any(d in (".git", "target") for d in dirs):
                raise KitError("private_or_build_directory_in_kit")
            dirs[:] = [d for d in dirs if d not in (".git", "target")]
            if "vendor" in dirs:
                raise KitError("source_vendor_must_be_separate")
        else:
            dirs.sort()
        for name in sorted(files):
            path = parent / name
            rel = path.relative_to(root).as_posix()
            if _linked(path) or not path.is_file():
                raise KitError("linked_or_nonregular_file:" + rel)
            if source and rel_parent == Path(".") and (name in _RECEIPTS or name.lower() == ".git"):
                if not private_mirror:
                    raise KitError("private_receipt_in_kit:" + name)
                continue  # Private host/commit notes and linked-worktree pointers stay local.
            if source:
                if name.lower() in _SOURCE_FORBIDDEN or name.lower().endswith((".pem", ".p12", ".pfx", ".key")):
                    raise KitError("private_source_filename:" + rel)
                if _PRIVATE_KEY_MARKER in path.read_bytes():
                    raise KitError("private_key_in_source:" + rel)
            found[rel] = _sha(path)
    if not found:
        raise KitError("empty_closure:" + root.name)
    if source and not {"Cargo.toml", "Cargo.lock"}.issubset(found):
        raise KitError("source_missing_cargo_manifest_or_lock")
    if source and not any(name.startswith("src/") and name.endswith(".rs") for name in found):
        raise KitError("source_missing_rust_entry")
    return found


def _digest(files: dict[str, str]) -> str:
    return hashlib.sha256("\n".join(f"{name} {sha}" for name, sha in sorted(files.items())).encode()).hexdigest()


def _read_object(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise KitError("receipt_unreadable:" + path.name) from exc
    if not isinstance(obj, dict):
        raise KitError("receipt_not_object:" + path.name)
    return obj


def _absolute(path: Path) -> Path:
    """Make subprocess inputs cwd-independent WITHOUT following source symlinks."""
    return Path(os.path.abspath(path))


def _legacy_digest(source: Path) -> tuple[str, int]:
    """Mirror's historical LF-normalized receipt; NOT the new build-input digest."""
    files = {}
    for pat in ("src/**/*", "Cargo.toml", "Cargo.lock"):
        for path in source.glob(pat):
            if path.is_file():
                rel = path.relative_to(source).as_posix()
                files[rel] = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return _digest(files), len(files)


def _copy_inventory(src: Path, dest: Path, files: dict[str, str]) -> None:
    for rel, expected in files.items():
        path = src / rel
        if not path.is_file() or _linked(path) or _sha(path) != expected:
            raise KitError("source_changed_during_copy:" + rel)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        if _sha(target) != expected:
            raise KitError("copied_bytes_differ:" + rel)


def _native_layout(root: Path) -> dict:
    layout = _read_object(root / _NATIVE_LAYOUT)
    if layout.get("target") != TARGET:
        raise KitError("native_target_mismatch")
    def relative(rel: str) -> bool:
        # On Windows, `\Windows\System32` has no drive but inherits C: when
        # joined to C:\kit\native. is_absolute() alone would admit that escape.
        return not (Path(rel).anchor or PureWindowsPath(rel).anchor or
                    ".." in Path(rel).parts or ":" in rel)
    for key in ("path", "lib", "include"):
        items = layout.get(key)
        if not isinstance(items, list) or not items or not all(isinstance(s, str) for s in items):
            raise KitError("native_layout_missing:" + key)
        for rel in items:
            if not relative(rel) or not (root / rel).is_dir():
                raise KitError("native_layout_outside_or_missing:" + key)
    for key in ("linker", "cc"):
        rel = layout.get(key)
        if not isinstance(rel, str) or not rel or not relative(rel) or not (root / rel).is_file() or _linked(root / rel):
            raise KitError("native_executable_outside_or_missing:" + key)
    return {key: layout[key] for key in ("target", "path", "lib", "include", "linker", "cc")}


def _source_receipt(mirror: Path, source_files: dict[str, str]) -> dict:
    legacy, count = _legacy_digest(mirror)
    source = _read_object(mirror / "RELAY-SOURCE.json")
    built = _read_object(mirror / "RELAY-BUILD.json")
    if source.get("digest") != legacy or source.get("files") != count or source.get("dirty") is True:
        raise KitError("mirror_provenance_mismatch")
    exe = mirror / "target" / "release" / BINARY
    if built.get("digest") != legacy or not exe.is_file() or _sha(exe) != built.get("exe_sha256"):
        raise KitError("legacy_build_or_binary_mismatch")
    # Only public hashes/counts leave the mirror. `from`, account, host and notes do not.
    return {"legacy_source_digest": legacy, "legacy_file_count": count,
            "source_inputs_sha256": _digest(source_files), "previous_binary_sha256": built["exe_sha256"]}


def _vendor_lock(source: Path, vendor: Path, files: dict[str, str]) -> None:
    try:
        lock = tomllib.loads((source / "Cargo.lock").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise KitError("cargo_lock_unreadable") from exc
    if any(row.get("source", "").startswith("registry+") for row in lock.get("package", [])):
        if not any(name.endswith("/.cargo-checksum.json") for name in files):
            raise KitError("vendor_missing_checksums")
    # Cargo itself proves the *exact* locked dependency resolution at build time.
    # A checksum-file presence check alone is not vendoring proof.


def _env(bundle: Path, build_root: Path, layout: dict) -> dict[str, str]:
    rust = bundle / "rust" / "bin"
    native = bundle / "native"
    env = {name: os.environ[name] for name in ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT") if name in os.environ}
    home = build_root / "home"
    home.mkdir(parents=True, exist_ok=True)
    temp = build_root / "temp"
    temp.mkdir()
    env.update({"HOME": str(home), "USERPROFILE": str(home), "CARGO_HOME": str(build_root / "cargo-home"),
                "RUSTUP_HOME": str(build_root / "rustup-home"), "CARGO_INCREMENTAL": "0",
                "CARGO_NET_OFFLINE": "true", "RUSTC": str(rust / "rustc.exe"),
                "CARGO_TARGET_DIR": str(build_root / "target"),
                "CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER": str(native / layout["linker"]),
                "CC_x86_64_pc_windows_msvc": str(native / layout["cc"]),
                "CXX_x86_64_pc_windows_msvc": str(native / layout["cc"]),
                "TEMP": str(temp), "TMP": str(temp),
                "PATH": os.pathsep.join([str(rust)] + [str(native / rel) for rel in layout["path"]] +
                                         [str(Path(env.get("SystemRoot", "C:/Windows")) / "System32")]),
                "LIB": os.pathsep.join(str(native / rel) for rel in layout["lib"]),
                "INCLUDE": os.pathsep.join(str(native / rel) for rel in layout["include"])})
    return env


def _build(bundle: Path, source: Path, build_root: Path, *, timeout: int = 1800) -> tuple[Path, dict]:
    bundle, source, build_root = map(_absolute, (bundle, source, build_root))
    layout = _native_layout(bundle / "native")
    cargo = bundle / "rust" / "bin" / "cargo.exe"
    rustc = bundle / "rust" / "bin" / "rustc.exe"
    if not cargo.is_file() or not rustc.is_file():
        raise KitError("rust_toolchain_missing_executables")
    before = inventory(source, source=True)
    build_root.mkdir(parents=True, exist_ok=False)
    env = _env(bundle, build_root, layout)
    vendor = (bundle / "vendor").resolve()
    command = [str(cargo), "--config", 'source.crates-io.replace-with="kit-vendor"',
               "--config", 'source.kit-vendor.directory=' + json.dumps(str(vendor)),
               "build", "--release", "--locked", "--offline", "--target", TARGET]
    try:
        proc = subprocess.run(command, cwd=source, env=env, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise KitError("candidate_build_timed_out") from exc
    if proc.returncode != 0:
        # Do not copy arbitrary cargo stdout/stderr: build scripts can print tokens.
        raise KitError("candidate_build_failed:exit=" + str(proc.returncode))
    if inventory(source, source=True) != before:
        raise KitError("source_changed_during_build")
    exe = build_root / "target" / TARGET / "release" / BINARY
    if not exe.is_file() or _linked(exe):
        raise KitError("candidate_binary_missing")
    return exe, {"source_inputs_sha256": _digest(before), "binary_sha256": _sha(exe),
                 "cargo_mode": "locked_offline", "target": TARGET,
                 "network_isolation": "unverified", "native_redistribution": "unreviewed"}


def verify(bundle: Path) -> dict:
    if not bundle.is_dir() or _linked(bundle):
        raise KitError("kit_missing_or_linked")
    if not (bundle / MANIFEST).is_file() or _linked(bundle / MANIFEST):
        raise KitError("kit_manifest_missing_or_linked")
    note = _read_object(bundle / MANIFEST)
    if note.get("format") != "relay-rebuild-kit.v1" or note.get("target") != TARGET:
        raise KitError("kit_manifest_version_or_target")
    if (note.get("qualification") != "source_bound_offline_unverified"
            or not isinstance(note.get("build"), dict)
            or note["build"].get("cargo_mode") != "locked_offline"
            or note["build"].get("network_isolation") != "unverified"
            or note["build"].get("native_redistribution") != "unreviewed"):
        raise KitError("kit_qualification_mismatch")
    expected = note.get("components")
    if not isinstance(expected, dict) or set(expected) != set(_COMPONENTS):
        raise KitError("kit_manifest_components")
    for name in _COMPONENTS:
        row = expected[name]
        if not isinstance(row, dict) or not isinstance(row.get("files"), dict):
            raise KitError("kit_manifest_component_shape:" + name)
        files = inventory(bundle / name, source=name == "source")
        if files != row["files"] or _digest(files) != row.get("sha256"):
            raise KitError("kit_component_mismatch:" + name)
    if (not (bundle / "rebuild.py").is_file() or not (bundle / "helene-relay.exe").is_file()
            or _linked(bundle / "rebuild.py") or _linked(bundle / "helene-relay.exe")):
        raise KitError("kit_executable_or_recipe_missing")
    if _sha(bundle / "rebuild.py") != note.get("rebuild_sha256") or _sha(bundle / "helene-relay.exe") != note.get("binary_sha256"):
        raise KitError("kit_executable_or_recipe_mismatch")
    if sorted(p.name for p in bundle.iterdir()) != sorted([*_COMPONENTS, "rebuild.py", "helene-relay.exe", MANIFEST]):
        raise KitError("kit_unlisted_top_level_item")
    if note.get("build", {}).get("source_inputs_sha256") != expected["source"]["sha256"] or note.get("build", {}).get("binary_sha256") != note["binary_sha256"]:
        raise KitError("kit_build_binding_mismatch")
    if note.get("origin", {}).get("source_inputs_sha256") != expected["source"]["sha256"]:
        raise KitError("kit_origin_source_mismatch")
    old, count = _legacy_digest(bundle / "source")
    if (note.get("origin", {}).get("legacy_source_digest"), note.get("origin", {}).get("legacy_file_count")) != (old, count):
        raise KitError("kit_legacy_source_mismatch")
    _native_layout(bundle / "native")
    _vendor_lock(bundle / "source", bundle / "vendor", expected["vendor"]["files"])
    return note


def stage(mirror: Path, vendor: Path, rust: Path, native: Path, out: Path) -> dict:
    mirror, vendor, rust, native, out = map(_absolute, (mirror, vendor, rust, native, out))
    if out.exists():
        raise KitError("kit_output_exists_preserved")
    if any(out.resolve().is_relative_to(root.resolve()) for root in (mirror, vendor, rust, native)):
        raise KitError("kit_output_inside_input")
    source_files = inventory(mirror, source=True, private_mirror=True)
    origin = _source_receipt(mirror, source_files)
    inputs = {"source": (mirror, source_files)}
    for name, root in (("vendor", vendor), ("rust", rust), ("native", native)):
        inputs[name] = (root, inventory(root))
    _vendor_lock(mirror, vendor, inputs["vendor"][1])
    _native_layout(native)
    temp = out.with_name(out.name + ".preparing-" + uuid.uuid4().hex[:8])
    temp.mkdir(parents=True, exist_ok=False)
    for name, (root, files) in inputs.items():
        _copy_inventory(root, temp / name, files)
        if inventory(temp / name, source=name == "source") != files:
            raise KitError("staged_closure_mismatch:" + name)
        if inventory(root, source=name == "source", private_mirror=name == "source") != files:
            raise KitError("input_changed_during_stage:" + name)
    source_copy = temp / "source"
    source_copy_files = inventory(source_copy, source=True)
    exe, built = _build(temp, source_copy, temp / ".candidate")
    if inventory(source_copy, source=True) != source_copy_files:
        raise KitError("source_changed_after_build")
    shutil.copyfile(exe, temp / "helene-relay.exe")
    shutil.copyfile(__file__, temp / "rebuild.py")
    build_dir = temp / ".candidate"
    # Work output is not part of the immutable kit. It remains recoverable in
    # the sibling preparation directory only on failure, not in the package.
    shutil.rmtree(build_dir)
    components = {name: {"files": files, "sha256": _digest(files)} for name, (_, files) in inputs.items()}
    note = {"format": "relay-rebuild-kit.v1", "target": TARGET, "components": components,
            "origin": origin, "build": built, "binary_sha256": _sha(temp / "helene-relay.exe"),
            "rebuild_sha256": _sha(temp / "rebuild.py"),
            "qualification": "source_bound_offline_unverified"}
    (temp / MANIFEST).write_text(json.dumps(note, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    verify(temp)
    temp.rename(out)  # Refuse an occupied destination; never replace a prior kit.
    return note


def candidate(bundle: Path, edited_source: Path, out: Path) -> dict:
    bundle, edited_source, out = map(_absolute, (bundle, edited_source, out))
    note = verify(bundle)
    if out.exists():
        raise KitError("candidate_output_exists_preserved")
    # The output may be reached through a symlink or Windows junction in one
    # of its existing parents. Lexical containment would mutate an input while
    # claiming the original kit/source was preserved.
    physical_out = out.resolve()
    if (physical_out.is_relative_to(bundle.resolve()) or
            physical_out.is_relative_to(edited_source.resolve())):
        raise KitError("candidate_output_inside_input")
    files = inventory(edited_source, source=True)
    out.mkdir(parents=True, exist_ok=False)
    source = out / "source"
    _copy_inventory(edited_source, source, files)
    exe, build = _build(bundle, source, out / ".build")
    verify(bundle)  # Even a build script must not mutate the original inputs.
    shutil.copyfile(exe, out / "helene-relay.exe")
    row = {"format": "relay-candidate.v1", "original_kit_sha256": _sha(bundle / MANIFEST),
           "edited_source_sha256": _digest(files), "previous_source_sha256": note["components"]["source"]["sha256"],
           "candidate_binary_sha256": _sha(out / "helene-relay.exe"), "build": build,
           "switch": "not_performed", "rollback": "original_kit_preserved"}
    (out / "candidate-receipt.json").write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    stage_args = commands.add_parser("stage")
    for arg in ("mirror", "vendor", "rust", "native", "out"):
        stage_args.add_argument("--" + arg, required=True)
    verify_args = commands.add_parser("verify")
    verify_args.add_argument("--kit", required=True)
    candidate_args = commands.add_parser("candidate")
    for arg in ("kit", "source", "out"):
        candidate_args.add_argument("--" + arg, required=True)
    args = parser.parse_args()
    try:
        if args.action == "stage":
            row = stage(*(Path(getattr(args, name)) for name in ("mirror", "vendor", "rust", "native", "out")))
        elif args.action == "verify":
            row = verify(Path(args.kit))
        else:
            row = candidate(Path(args.kit), Path(args.source), Path(args.out))
    except KitError as exc:
        print("relay rebuild kit REFUSED: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"format": row["format"], "binary_sha256": row.get("binary_sha256", row.get("candidate_binary_sha256")),
                      "qualification": row.get("qualification", "candidate_not_installed")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
