"""Install the UI in the existing desk server static/assets directory."""
import argparse
import os
from pathlib import Path
import tempfile
import zipfile


def publish(archive, static):
    if not (static / "index.html").is_file() or not (static / "assets").is_dir():
        raise ValueError("Expected the active desk static directory")
    target = static / "assets/praxis-window"
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        if z.testzip() or not {"index.html", "config.js", "LICENSE", "NOTICE"}.issubset(z.namelist()):
            raise ValueError("Incomplete window bundle")
        for name in sorted(z.namelist(), key=lambda n: n == "index.html"):
            path = (target / name).resolve()
            if not path.is_relative_to(target.resolve()) or path == target.resolve():
                raise ValueError("Unexpected bundle path")
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
                tmp.write(z.read(name))
            os.chmod(tmp.name, 0o644)
            os.replace(tmp.name, path)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("archive", type=Path)
    p.add_argument("static", type=Path)
    args = p.parse_args()
    publish(args.archive, args.static)
