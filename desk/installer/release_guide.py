"""Version-specific entry documents shared by desktop refresh and server builds."""
from pathlib import Path
import hashlib
import json
import re
import subprocess


def documents(source, version, target, start):
    source = Path(source).resolve()
    if not re.fullmatch(r"[\w.-]+", version):
        raise ValueError("Invalid version for a source archive")
    origin = source / "RELEASE-ORIGIN.json"
    if origin.is_file():
        metadata = json.loads(origin.read_bytes())
        commit = metadata["commit"]
        if metadata["version"] != version or not re.fullmatch("[a-f0-9]{40}", commit):
            raise ValueError("Source archive identity differs from requested version")
    else:
        commit = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "--verify", "HEAD"], text=True
        ).strip()
    map_path = "app/resources/HELENE-MAP.md"
    map_bytes = (source / "desk/resources/HELENE-MAP.md").read_bytes()
    archive = f"Helene-{version}-source.zip"
    metadata = dict(version=version, source_commit=commit, target=target,
                    source_archive=archive, map=map_path,
                    map_sha256=hashlib.sha256(map_bytes).hexdigest())
    section = ("\n\n## Карта кода и выпуск\n\n"
               f"Hélène {version} · `{commit}`\n\n"
               f"Откройте [карту кода]({map_path}). В разделе Assets этого же выпуска "
               f"скачайте **{archive}**. После распаковки откройте **START-HERE.md** "
               "рядом с папкой release. Происхождение карты записано в "
               "[RELEASE-ORIGIN.json](RELEASE-ORIGIN.json).\n")
    files = {"START-HERE.md": (start.rstrip() + section + "\n[Команды сборки](release/README.md)\n").encode(),
             "RELEASE-ORIGIN.json": (json.dumps(metadata, indent=2) + "\n").encode()}
    for name in ("kit.py", "build.py", "source_archive.py", "privacy.py", "README.md", "inputs.json"):
        files["release/" + name] = (source / "release" / name).read_bytes()
    return files


def write_stage(source, stage, version, platform, target):
    start = desktop_start(source, platform, target)
    for name, data in documents(source, version, target, start).items():
        path = Path(stage) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def desktop_start(source, platform, target):
    if platform == "windows":
        from windows_archive_entry import instructions
        # Preserve the final launcher's own clean-upgrade and first-run instructions.
        text = instructions()
        template = "PYTHON-START-HERE.md"
        human = (Path(source) / "desk/installer" / template).read_text(encoding="utf8")
        return text.rstrip() + "\n\n## Агент может менять себя\n" + human.split("## Агент может менять себя\n", 1)[1]
    if platform == "macos":
        template = ("PYTHON-MACOS-INTEL-START-HERE.md" if target == "macos-x86_64"
                    else "PYTHON-MACOS-START-HERE.md")
    else:
        template = "PYTHON-LINUX-START-HERE.md"
    return (Path(source) / "desk/installer" / template).read_text(encoding="utf8")
