"""Assemble a platform from a clean source snapshot and pinned public inputs."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

from source_archive import path_name

FRONTS = ("app", "mobile", "miniapp", "remote", "setup/ui")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def extract_rows(z, prefix, rows, dest):
    for name, row in rows.items():
        target = dest / path_name(name)
        item = z.getinfo(prefix + name)
        if item.file_size != row["size"]:
            raise ValueError("Unexpected input size: " + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with z.open(item) as src, target.open("xb") as out:
            shutil.copyfileobj(src, out)
        if digest(target) != row["sha256"]:
            raise ValueError("Package input digest differs: " + name)
        mode = row["mode"]
        target.chmod((int(mode, 8) if isinstance(mode, str) else mode) & 0o777)


def fronts(source, donor, work):
    import portable_sources
    tools = work / "tools"
    with zipfile.ZipFile(donor) as z:
        manifest = json.loads(z.read("Helene/verification/manifest.json"))
        native = manifest["bundled_executors"][sys.platform]
        extract_rows(z, "Helene/verification-tools/", native["files"], tools)
    node = tools / native["tools"]["node"]
    npm = tools / "npm/bin/npm-cli.js"
    user_config = work / "npm-user.conf"
    global_config = work / "npm-global.conf"
    user_config.touch()
    global_config.touch()
    env = {k: v for k, v in os.environ.items() if not k.lower().startswith("npm_config_")}
    env.update(npm_config_cache=str(tools / native["npm_cache"]), npm_config_offline="true",
               npm_config_userconfig=str(user_config), npm_config_globalconfig=str(global_config),
               PATH=str(node.parent) + os.pathsep + os.environ["PATH"])
    before = portable_sources.frontend_inputs(source)
    for name in FRONTS:
        directory = source / "desk" / name
        subprocess.run([node, npm, "ci", "--offline", "--include=optional", "--no-audit", "--no-fund"],
                       cwd=directory, env=env, check=True)
    shared = source / "desk/node_modules"
    modules = source / "desk/app/node_modules"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(modules), str(shared))
    else:
        shared.symlink_to(modules, target_is_directory=True)
    commands = []
    for name in FRONTS:
        subprocess.run([node, npm, "run", "build"], cwd=source / "desk" / name, env=env, check=True)
        commands.append({"cwd": "desk/" + name, "argv": ["npm", "run", "build"], "returncode": 0})
    receipt = work / "frontends.json"
    portable_sources.write_receipt(source, receipt, commands[:3], before=before)
    hashes = {}
    for name in ("app/dist", "mobile/dist", "miniapp/dist", "remote/dist", "setup/dist"):
        root = source / "desk" / name
        if not (root / "index.html").is_file():
            raise ValueError("Missing frontend output: " + name)
        for file in sorted(root.rglob("*")):
            if file.is_file():
                hashes[file.relative_to(source).as_posix()] = digest(file)
    return receipt, hashes


def verification(source, donor, work, version):
    from localharness import source_verification_bundle as suite
    with zipfile.ZipFile(donor) as z:
        previous = json.loads(z.read("Helene/verification/manifest.json"))
        wheels = {}
        for native, dep in previous.get("dependencies", {}).items():
            root = work / "wheels" / native
            extract_rows(z, f"Helene/verification/dependencies/{native}/", dep["files"], root)
            (root / "wheels.json").write_text(json.dumps({"packages": dep["packages"]}))
            wheels[native] = root
        evidence = None
        if "retained_evidence" in previous:
            evidence = work / "retained"
            extract_rows(z, "Helene/verification/evidence-inputs/", previous["retained_evidence"]["files"], evidence)
    out = work / "verification"
    suite.build(source, out, version, previous["executor_locks"], wheelhouses=wheels,
                allowed_skips=previous["allowed_test_skips"], native=previous["native_lanes"],
                rebuild=previous["build_lanes"], scripts=previous["script_contracts"], retained_inputs=evidence)
    path = out / "manifest.json"
    data = json.loads(path.read_bytes())
    data["bundled_executors"] = previous["bundled_executors"]
    for key, value in previous.items():
        if key.startswith("source_declared_") and isinstance(value, dict):
            origin = value.get("source")
            if origin and data["files"].get(origin, {}).get("sha256") != value.get("source_sha256"):
                raise ValueError("Review changed source-bound test declarations: " + origin)
            for name, expected in value.get("source_files", {}).items():
                if data["files"].get(name, {}).get("sha256") != expected:
                    raise ValueError("Review changed test declaration: " + name)
            data[key] = value
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf8")
    return out, digest(path)


def build(source, work, output, version, target, inputs, fetch):
    import portable_refresh
    from build_server_bundle import build as server_build
    item = inputs["targets"][target]
    donor = fetch(item)
    is_server = target == "server-linux-arm64"
    pin = json.loads((source / "desk/localharness/relay-release.json").read_bytes())
    with zipfile.ZipFile(donor) as z:
        meta = json.loads(z.read("Helene/python-app.json"))
        relay = json.loads(z.read("Helene/relay-product/BUILD-PASSPORT.json"))
        if meta.get("version") != inputs["version"] or relay["version"] != pin["version"]:
            raise ValueError("Input version or bundled relay differs from the pinned release")
        expected = {"debian-x86_64":"linux-x86_64", "fedora-x86_64":"linux-x86_64",
                    "server-linux-arm64":"linux-arm64"}.get(target, target)
        if meta.get("target") != expected or bool(meta.get("server")) != is_server:
            raise ValueError("Wrong input platform for " + target)
        if (meta.get("distribution") == "fedora") != target.startswith("fedora-"):
            raise ValueError("Wrong desktop distribution for " + target)
        if is_server:
            commit, filename, wanted = relay["source_commit"], "praxis-relay-headless", relay["sha256"]
        else:
            if relay["native_platform"] != expected:
                raise ValueError("Relay architecture differs from target")
            commit, filename, wanted = relay["base_commit"], relay["binary"]["name"], relay["binary"]["sha256"]
        if commit != pin["source_commit"] or "/" in path_name(filename):
            raise ValueError("Qualify an input with the selected relay first")
        with z.open("Helene/relay-product/" + filename) as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != wanted:
                raise ValueError("Native relay bytes differ from the build passport")
    system = {"win32":"windows", "darwin":"macos", "linux":"linux"}.get(sys.platform)
    arch = {"amd64":"x86_64", "aarch64":"arm64"}.get(platform.machine().lower(), platform.machine().lower())
    host = f"{system}-{arch}"
    if not is_server and host != item["host"]:
        raise ValueError(f"Run {target} on {item['host']} (current host {host})")
    if target.startswith("fedora-") and platform.freedesktop_os_release().get("ID") != "fedora":
        raise ValueError("Use a Fedora builder for the Fedora desktop")
    tool_target = target if not is_server else {"linux-x86_64":"debian-x86_64"}.get(host, host)
    if tool_target not in inputs["targets"]:
        raise ValueError("Cross-assemble the ARM server on a supported desktop builder")
    tool_input = donor if tool_target == target else fetch(inputs["targets"][tool_target])
    receipt, output_hashes = fronts(source, tool_input, work)
    if is_server:
        relay = work / "relay"
        relay.mkdir()
        with zipfile.ZipFile(donor) as z:
            for info in z.infolist():
                if info.filename.startswith("Helene/relay-product/") and not info.is_dir():
                    name = path_name(info.filename.removeprefix("Helene/relay-product/"))
                    if "/" in name:
                        raise ValueError("Unexpected server relay path")
                    (relay / name).write_bytes(z.read(info))
        built = server_build(source, work / "server", version=version, arch="arm64", relay=relay)
    else:
        bundle, bundle_hash = verification(source, donor, work, version)
        kind = target.split("-")[0]
        bootstrap = None
        if kind == "windows":
            from windows_archive_entry import bootstrap_matches, launcher
            with zipfile.ZipFile(donor) as archive:
                previous = json.loads(archive.read("Helene/helene-bootstrap.json"))
            if not bootstrap_matches(previous, source/"desk") or previous.get("icon_sha256") != digest(source/"desk/shell/icons/icon.ico"):
                manifest = source/"desk/python-launcher/Cargo.toml"
                subprocess.run(["cargo", "fetch", "--locked", "--manifest-path", str(manifest)], check=True)
                native = work/"windows-bootstrap"
                native.mkdir()
                bootstrap = launcher(native)
        plan, changes, removed = portable_refresh.plan(
            donor, source, version, kind, expected_sha256=item["sha256"], frontend_receipt=receipt,
            verification_bundle=bundle, verification_sha256=bundle_hash, windows_bootstrap=bootstrap)
        built = work / item["asset"].replace(inputs["version"], version, 1)
        portable_refresh.build(donor, built, plan, changes, removed, expected_plan=plan["sha256"])
    with zipfile.ZipFile(built) as z:
        origin = json.loads(z.read("Helene/RELEASE-ORIGIN.json"))
        guide = z.read("Helene/START-HERE.md").decode()
        if (origin["version"] != version or origin["source_commit"] != json.loads((source/"RELEASE-ORIGIN.json").read_bytes())["commit"]
                or origin["source_archive"] not in guide or origin["map"] not in guide
                or hashlib.sha256(z.read("Helene/" + origin["map"])).hexdigest() != origin["map_sha256"]):
            raise ValueError("Final archive entry does not match its sources")
    shutil.copyfile(built, output / built.name)
    return {"asset": built.name, "sha256": digest(built), "size": built.stat().st_size,
            "target": target, "source_commit": origin["source_commit"], "built_on": host,
            "frontends": output_hashes, "input_sha256": item["sha256"]}
