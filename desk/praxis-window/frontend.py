"""Build the window's page from her phone UI kit and this folder's `ui/`.

The kit is taken from Git: the published commit whose `remote/praxis-desk/desk/ui-kit`
is, byte for byte, the kit her deployed phone was built from (checked against the
mirror on the server on 2026-11-23). Nothing is patched on the way: her modules are
used as they are, and the window is only this folder's own layout on top of them.

    python frontend.py <checkout> ui.zip [--modules <node_modules>] [--check-only]

`checkout` is any checkout of the repository holding that commit (its object
database is what is read; the working tree is not touched). Dependencies are the
kit's own on that commit. `<checkout>/desk/remote/node_modules` is used when it
stands beside the same lock file (or `--modules` says so); otherwise `npm ci`
installs the kit's lock in the temporary tree.
"""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

RELEASE = "b950005"                       # public main; its remote/praxis-desk is her mirror
KIT = "remote/praxis-desk/desk/ui-kit"
DEPS = ["desk/remote/package.json", "desk/remote/package-lock.json"]   # the kit's dependencies
CONFIG = 'window.DESK_CONFIG={base:"",key:"",local_agent:false,product:"Праксис",agent:"Праксис"};\n'
HERE = Path(__file__).resolve().parent
UI = HERE / "ui"


def link(target, existing):
    if os.name == "nt":
        subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(target), str(existing)], check=True, capture_output=True)
    else:
        os.symlink(existing, target, target_is_directory=True)


def unlink(target):
    if os.name == "nt":
        # a junction is removed with rmdir, never with a recursive delete of what it points to
        os.rmdir(target)
    else:
        os.unlink(target)


def tool(root, name):
    return root / "node_modules" / ".bin" / (name + (".cmd" if os.name == "nt" else ""))


def build(source, output, modules=None, check_only=False, log=print):
    tree = tempfile.TemporaryDirectory(prefix="praxis-window-")
    try:
        root = Path(tree.name)
        raw = subprocess.check_output(["git", "-C", str(source), "archive", RELEASE, KIT])
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            archive.extractall(root, filter="data")
        kit = root / "remote" / "praxis-desk" / "desk" / "ui-kit"
        (root / "desk").mkdir()
        shutil.move(kit, root / "desk" / "ui-kit")
        window = root / "desk" / "window"
        shutil.copytree(UI, window, ignore=shutil.ignore_patterns("node_modules", "dist"))
        (window / "config.js").write_text(CONFIG, encoding="utf-8")
        # Зависимости — те же, что у её набора на RELEASE: их список и замок тоже берутся из Git.
        # Готовые подходят, когда стоят рядом с таким же замком; иначе ставятся во временном дереве.
        raw = subprocess.check_output(["git", "-C", str(source), "archive", RELEASE, *DEPS])
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            archive.extractall(root, filter="data")
        deps = root / "desk" / "remote"
        norm = lambda data: data.replace(b"\r\n", b"\n")
        lock = norm((deps / "package-lock.json").read_bytes())
        installed = Path(modules) if modules else source / "desk" / "remote" / "node_modules"
        own = installed.parent / "package-lock.json"
        if (installed / "vite").is_dir() and (modules or (own.is_file() and norm(own.read_bytes()) == lock)):
            link(window / "node_modules", installed)
        else:
            npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
            subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=deps, check=True)
            link(window / "node_modules", deps / "node_modules")
        linked = True
        try:
            for name in ("tsc", "vite"):
                subprocess.run([tool(window, name), "--noEmit"] if name == "tsc" else [tool(window, name), "build"],
                               cwd=window, check=True)
            if check_only:
                return
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in sorted((window / "dist").rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(window / "dist").as_posix())
                archive.writestr("config.js", CONFIG)
                for name in ("LICENSE", "NOTICE"):
                    path = source / name
                    if path.is_file():
                        archive.write(path, name)
            log(f"собрано: {output}")
        finally:
            if linked:
                unlink(window / "node_modules")
    finally:
        tree.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--modules", type=Path)
    parser.add_argument("--check-only", action="store_true", help="type-check and build without writing the archive")
    args = parser.parse_args()
    if not args.check_only and args.output is None:
        parser.error("output archive is required")
    build(args.source.resolve(), args.output.resolve() if args.output else None,
          args.modules.resolve() if args.modules else None, args.check_only)
