"""Prepare, build, verify and stage Hélène releases. Requires Python 3.11+ and Git."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import urlsplit
import zipfile

from build import build, digest, FRONTS
from source_archive import archive, git, path_name, restore

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "release/inputs.json"


def version(root):
    value = json.loads((root / "desk/app/package.json").read_bytes())["version"]
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", value):
        raise ValueError("The source must declare a three-part release version")
    return value


def prepare(root, new):
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", new):
        raise ValueError("Use a three-part release version")
    old = version(root)
    if old == new:
        raise ValueError("This version is already present. Check the diff and commit it.")
    changes = {}
    def replace(file, pattern, count):
        target = root / file
        raw = target.read_bytes()
        before = pattern.replace("{v}", old).encode()
        after = pattern.replace("{v}", new).encode()
        if raw.count(before) != count:
            raise ValueError("Review version carrier: " + file)
        changes[target] = raw.replace(before, after)
    for project in FRONTS:
        replace(f"desk/{project}/package.json", '"version": "{v}"', 1)
        replace(f"desk/{project}/package-lock.json", '"version": "{v}"', 2)
    for crate in ("setup", "shell", "svc"):
        replace(f"desk/{crate}/Cargo.toml", 'version = "{v}"', 1)
        replace(f"desk/{crate}/Cargo.lock", 'version = "{v}"', 1)
    for crate in ("setup", "shell"):
        replace(f"desk/{crate}/tauri.conf.json", '"version": "{v}"', 1)
    replace("desk/installer/install.sh", 'HELENE_TAG_DEFAULT="v{v}"', 1)
    replace(".github/workflows/macos.yml", "v{v}", 2)
    for path, data in changes.items():
        path.write_bytes(data)
    print(f"Prepared {old} -> {new}. Review and commit {len(changes)} version carriers.")


def obtain(item, cache):
    cache.mkdir(parents=True, exist_ok=True)
    name = path_name(item["asset"])
    if "/" in name or not re.fullmatch("[a-f0-9]{64}", item["sha256"]):
        raise ValueError("Invalid input manifest")
    path = cache / name
    if not path.exists():
        url = item["url"]
        if not url.startswith("https://"):
            raise ValueError("Input downloads require HTTPS")
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as tmp:
            temp = Path(tmp.name)
        try:
            with urllib.request.urlopen(url, timeout=90) as remote, temp.open("wb") as out:
                shutil.copyfileobj(remote, out)
            if digest(temp) != item["sha256"] or temp.stat().st_size != item["size"]:
                raise ValueError("Downloaded input differs: " + name)
            try:
                os.link(temp, path)
            except FileExistsError:
                pass
        finally:
            temp.unlink(missing_ok=True)
    if path.stat().st_size != item["size"] or digest(path) != item["sha256"]:
        raise ValueError("Replace the mismatched cache file: " + name)
    return path


def output_source(root, out):
    v = version(root)
    source = out / f"Helene-{v}-source.zip"
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    if not source.exists():
        archive(root, source, v)
    else:
        with tempfile.TemporaryDirectory(dir=out) as tmp:
            check = Path(tmp) / source.name
            archive(root, check, v)
            if digest(source) != digest(check):
                raise ValueError("Output belongs to a different commit. Select a new output directory.")
    return source, commit


def stage_build(root, out, target, cache, lock):
    v = version(root)
    source_zip, commit = output_source(root, out)
    receipt = out / (target + ".json")
    if receipt.exists():
        row = json.loads(receipt.read_bytes())
        item = out / path_name(row["asset"])
        if (row["source_commit"] == commit and row["input_sha256"] == lock["targets"][target]["sha256"]
                and item.is_file() and digest(item) == row["sha256"]):
            print("Already verified:", item)
            return
        raise ValueError("Output receipt differs. Select a new output directory.")
    with tempfile.TemporaryDirectory(prefix="helene-build-", dir=out) as tmp:
        work = Path(tmp)
        with zipfile.ZipFile(source_zip) as z:
            for info in z.infolist():
                path_name(info.filename)
                file = Path(z.extract(info, work))
                file.chmod((info.external_attr >> 16) & 0o777 or 0o644)
        source = work / f"Helene-{v}-source"
        # The packaging libraries read code only from this freshly exported tree.
        sys.path[:0] = [str(source / "desk"), str(source / "desk/installer")]
        staged = work / "assets"
        staged.mkdir()
        row = build(source, work, staged, v, target, lock, lambda item: obtain(item, cache))
        data = staged / row["asset"]
        result = out / data.name
        if result.exists() and digest(result) != row["sha256"]:
            raise ValueError("Existing asset differs. Select a new output directory.")
        if not result.exists():
            os.link(data, result)
        tmp_receipt = work / "receipt.json"
        tmp_receipt.write_text(json.dumps(row, indent=2), encoding="utf8")
        os.link(tmp_receipt, receipt)
        print(result, row["sha256"])


def publish(root, out, targets, dest, repo, channel):
    v = version(root)
    source, commit = output_source(root, out)
    files = [source]
    for target in targets:
        row = json.loads((out / (target + ".json")).read_bytes())
        file = out / path_name(row["asset"])
        if (row["target"] != target or file.parent != out or row["source_commit"] != commit
                or digest(file) != row["sha256"] or file.stat().st_size != row["size"]):
            raise ValueError("Invalid build receipt: " + target)
        files.append(file)
    targets_unique = {file.name for file in files}
    if len(targets_unique) != len(files):
        raise ValueError("Select each target once")
    sums = "".join(digest(f) + "  " + f.name + "\n" for f in files)
    name = "v" + v
    parsed = urlsplit(channel)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"))):
        raise ValueError("Use a public HTTPS channel or local HTTP loopback URL")
    feed = {"tag_name": name, "draft": False, "prerelease": False, "body": "Hélène " + v,
            "assets": [{"name": f.name, "size": f.stat().st_size, "digest": "sha256:" + digest(f),
                        "browser_download_url": channel.rstrip("/") + "/" + name + "/" + f.name} for f in files]}
    dest.mkdir(parents=True, exist_ok=True)
    final = dest / name
    if not final.exists():
        with tempfile.TemporaryDirectory(dir=dest) as tmp:
            staged = Path(tmp) / name
            staged.mkdir()
            for f in files:
                shutil.copyfile(f, staged / f.name)
            (staged / "SHA256SUMS").write_text(sums, encoding="ascii", newline="\n")
            (staged / "release.json").write_text(json.dumps(feed, indent=2), encoding="utf8")
            staged.rename(final)
    if (set(p.name for p in final.iterdir()) != targets_unique | {"SHA256SUMS", "release.json"}
            or (final / "SHA256SUMS").read_text() != sums or json.loads((final / "release.json").read_bytes()) != feed
            or any(digest(final / f.name) != digest(f) for f in files)):
        raise ValueError("Staged release differs. Select a new destination.")
    if repo:
        # An existing tag must already point to the reviewed candidate.
        tag_commit = subprocess.check_output(["gh", "api", f"repos/{repo}/commits/{name}", "--jq", ".sha"], text=True).strip()
        if tag_commit != commit:
            raise ValueError("Create the reviewed tag at the exact source commit first")
        view = ["gh", "release", "view", name, "--repo", repo, "--json", "isDraft,assets"]
        current = subprocess.run(view, capture_output=True)
        if current.returncode:
            subprocess.run(["gh", "release", "create", name, "--repo", repo, "--verify-tag", "--draft",
                            "--title", "Hélène " + v, "--notes", "Hélène " + v], check=True)
        def remaining():
            remote = json.loads(subprocess.check_output(view))
            if not remote["isDraft"]:
                raise ValueError("Only a draft can receive assets from this command")
            local = {f.name: final / f.name for f in files}
            local["SHA256SUMS"] = final / "SHA256SUMS"
            for asset in remote["assets"]:
                path = local.pop(asset["name"], None)
                if path is None or asset.get("digest") != "sha256:" + digest(path):
                    raise ValueError("Review unexpected or mismatched draft asset: " + asset["name"])
            return list(local.values())
        missing = remaining()
        if missing:
            subprocess.run(["gh", "release", "upload", name, "--repo", repo, *map(str, missing)], check=True)
        if remaining():
            raise ValueError("Draft incomplete. Rerun the same publish command.")
    print(final)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    restore_cmd = sub.add_parser("restore")
    restore_cmd.add_argument("--to", type=Path, required=True)
    restore_cmd.add_argument("--commit", required=True)
    sub.add_parser("prepare").add_argument("version")
    inputs = json.loads(LOCK.read_bytes())
    for command in ("source", "build", "publish"):
        cmd = sub.add_parser(command)
        cmd.add_argument("--out", type=Path, required=True)
        if command == "build":
            cmd.add_argument("--target", required=True, choices=inputs["targets"])
            cmd.add_argument("--cache", type=Path, required=True)
        if command == "publish":
            cmd.add_argument("--target", action="append", choices=inputs["targets"], required=True)
            cmd.add_argument("--to", type=Path, required=True)
            cmd.add_argument("--channel-url", default="http://127.0.0.1:8765")
            cmd.add_argument("--github-draft", metavar="OWNER/REPO")
    args = p.parse_args()
    if args.cmd == "restore":
        print(restore(ROOT, args.to, args.commit))
    elif args.cmd == "prepare":
        prepare(ROOT, args.version)
    else:
        out = args.out.resolve()
        if out.is_relative_to(ROOT):
            raise ValueError("Use an output directory outside the source tree")
        out.mkdir(parents=True, exist_ok=True)
        if args.cmd == "source":
            print(output_source(ROOT, out))
        elif args.cmd == "build":
            stage_build(ROOT, out, args.target, args.cache.resolve(), inputs)
        else:
            publish(ROOT, out, args.target, args.to.resolve(), args.github_draft, args.channel_url)


if __name__ == "__main__":
    main()
