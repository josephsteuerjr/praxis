"""Verify one *source-only* Hélène vNext composition, never release readiness.

The existing edition, desk, body and relay manifests own their respective byte sets.
This reader checks their joins against an exact Git HEAD, and writes one reusable
observation. It neither builds nor installs anything. A missing relay kit remains
missing, not an implicit public-source substitute. Verify an earlier receipt with
--verify: a changed input is a refusal, not a silently refreshed receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import build_mac  # noqa: E402 — body_source_digest is the existing body source identity
import relay_rebuild_kit  # noqa: E402
import vnext_candidate  # noqa: E402 — exact Git/source selection owner


class CompositionError(ValueError):
    """One source relationship cannot be established."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _document(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise CompositionError(f"missing or linked manifest: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CompositionError(f"unreadable manifest: {path.name}") from exc
    if not isinstance(value, dict):
        raise CompositionError(f"manifest is not an object: {path.name}")
    return value


def _files(root: Path, *, exclude: str) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise CompositionError(f"missing or linked component: {root.name}")
    found = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise CompositionError(f"linked component path: {path.relative_to(root)}")
        rel = path.relative_to(root).as_posix()
        if path.is_file() and rel != exclude:
            found[rel] = _sha(path)
    return found


def _git_blob(root: Path, head: str, rel: str) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(["git", "-C", str(root), "show", f"{head}:{rel}"],
                            capture_output=True, timeout=30, env=env)
    if result.returncode:
        raise CompositionError(f"Git source unavailable: {rel}")
    return result.stdout


def inspect(root: Path, head: str, edition: Path, desk: Path,
            body_receipt: Path, body_dir: Path, relay_kit: Path | None = None) -> dict:
    """Observe existing artifacts; no success field can stand for installed proof."""
    root, edition, desk, body_receipt, body_dir = (
        Path(p).resolve() for p in (root, edition, desk, body_receipt, body_dir))
    source = _document(edition / ".vnext-source.json")
    if (source.get("kind") != "vnext_source_only" or source.get("base_head") != head
            or source.get("allowed_dirty") != [] or not isinstance(source.get("files"), dict)):
        raise CompositionError("edition is not a clean candidate of this HEAD")
    # Reuse the composer rather than defining a second overlay policy. It checks
    # real source bytes against HEAD, even for assume-unchanged/skip-worktree.
    selected = vnext_candidate._sources(root, head, set())
    if set(source["files"]) != set(selected):
        raise CompositionError("edition source selection differs from pinned HEAD")
    observed = _files(edition, exclude=".vnext-source.json")
    if set(observed) != set(selected):
        raise CompositionError("edition file inventory differs from pinned HEAD")
    rows = {}
    for rel, (origin, selected_path) in selected.items():
        row = source["files"].get(rel)
        if (not isinstance(row, dict) or row.get("source") != origin or
                row.get("sha256") != observed[rel] or
                row.get("bytes") != (edition / rel).stat().st_size or
                _sha(selected_path) != observed[rel]):
            raise CompositionError(f"edition source or bytes moved: {rel}")
        rows[rel] = row
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    if source.get("digest") != hashlib.sha256(payload).hexdigest():
        raise CompositionError("edition aggregate digest mismatch")
    desktop = _document(desk / "desk.json")
    if (desktop.get("product") != "desk" or desktop.get("flavor") != "windows"
            or not isinstance(desktop.get("files"), dict)):
        raise CompositionError("not a Windows desk source package")
    desk_files = _files(desk, exclude="desk.json")
    if desk_files != desktop["files"]:
        raise CompositionError("desk inventory differs from manifest")
    # deskpkg.build hashes its Path-sorted insertion order; Windows Path sorting
    # is case-folded, so sorting the relative strings here would invent a new
    # aggregate for the same verified files. Preserve the manifest's order.
    desk_digest = hashlib.sha256("\n".join(
        f"{rel} {digest}" for rel, digest in desktop["files"].items()).encode()).hexdigest()
    if desktop.get("digest") != desk_digest:
        raise CompositionError("desk aggregate digest mismatch")
    # A desk manifest proves packaged bytes, not which repository supplied them.
    # These selected executors/owner are the product join to the pinned HEAD.
    selected_cli = ("body.py", "cu_read_cli.py", "cu_fs_cli.py", "desktop_supervisor.py",
                    "desktop_process.py")
    for name in selected_cli:
        rel = f"localharness/{name}"
        origin = f"desk/{rel}"
        if (desk_files.get(rel) is None or not (root / origin).is_file() or
                desk_files[rel] != _sha(root / origin) or
                desk_files[rel] != hashlib.sha256(_git_blob(root, head, origin)).hexdigest()):
            raise CompositionError(f"desk executor not from pinned HEAD: {rel}")
    built = _document(body_receipt)
    body_source = edition / "body"
    digest, count = build_mac.body_source_digest(body_source)
    if (count == 0 or built.get("source_digest") != digest or built.get("files") != count
            or Path(built.get("source", "")).resolve() != body_source):
        raise CompositionError("body build receipt does not name this edition source")
    binaries = {}
    for name in ("praxis-body", "praxis-bridge"):
        exe = body_dir / f"{name}.exe"
        expected = (built.get("exe_sha256") or {}).get(name)
        if (not isinstance(expected, str) or len(expected) != 64 or exe.is_symlink()
                or not exe.is_file() or _sha(exe) != expected):
            raise CompositionError(f"body binary missing or differs: {name}")
        binaries[name] = expected
    relay = {"state": "missing", "qualification": "not_evaluated"}
    if relay_kit is not None:
        try:
            kit = relay_rebuild_kit.verify(Path(relay_kit))
        except relay_rebuild_kit.KitError as exc:
            raise CompositionError(f"relay kit refused: {exc}") from exc
        relay = {"state": "present", "qualification": kit["qualification"],
                 "manifest_sha256": _sha(Path(relay_kit) / relay_rebuild_kit.MANIFEST),
                 "binary_sha256": kit["binary_sha256"]}
    return {"kind": "vnext_product_source_observation_v1", "head": head,
            "edition": {"manifest_sha256": _sha(edition / ".vnext-source.json"),
                        "digest": source["digest"], "files": len(observed)},
            "desk": {"manifest_sha256": _sha(desk / "desk.json"),
                     "digest": desk_digest, "files": len(desk_files),
                     "head_binding": "selected_python_executors_only",
                     "skipped": desktop.get("skipped", [])},
            "body": {"receipt_sha256": _sha(body_receipt), "source_digest": digest,
                     "binaries": binaries, "toolchain": "unverified"},
            "relay": relay,
            "limitations": ["source_only_not_installed", "no_release_authority",
                            "desk_other_inputs_origin_unverified",
                            "body_toolchain_unverified", "relay_protocol_unverified"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--edition", required=True)
    parser.add_argument("--desk", required=True)
    parser.add_argument("--body-receipt", required=True)
    parser.add_argument("--body-dir", required=True)
    parser.add_argument("--relay-kit")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--out")
    choice.add_argument("--verify")
    args = parser.parse_args()
    try:
        if args.out:
            output = Path(args.out).resolve()
            # Writing the observation inside any input immediately stales the
            # inventory it just read. Root contains the edition's source paths.
            inputs = [Path(args.root).resolve(), Path(args.edition).resolve(),
                      Path(args.desk).resolve(), Path(args.body_dir).resolve()]
            if args.relay_kit:
                inputs.append(Path(args.relay_kit).resolve())
            if any(output == base or output.is_relative_to(base) for base in inputs):
                raise CompositionError("source observation output must be outside every input")
        current = inspect(Path(args.root), args.head, Path(args.edition), Path(args.desk),
                          Path(args.body_receipt), Path(args.body_dir),
                          Path(args.relay_kit) if args.relay_kit else None)
        if args.verify:
            if current != _document(Path(args.verify)):
                raise CompositionError("source observation changed; previous receipt not verified")
        else:
            path = Path(args.out)
            with path.open("x", encoding="utf-8", newline="\n") as output:
                json.dump(current, output, ensure_ascii=False, sort_keys=True, indent=2)
                output.write("\n")
        print(json.dumps(current, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print("vNext source observation REFUSED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
