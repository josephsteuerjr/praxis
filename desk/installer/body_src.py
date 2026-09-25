"""Сборка тела руки `computer` для Windows с записью происхождения (25.09, A9 F4).

    python installer/body_src.py --build      # cargo build --release -p praxis-body -p praxis-bridge
    python installer/body_src.py --check      # тем ли исходником собраны exe в _body_target

Пишет `_body_target/BODY-BUILT.json`: отпечаток исходника `live/body` (тот же, что у
Mac-сборки — `build_mac.body_source_digest`), суммы exe, время. `build_dist.py` сверяет
его с живым исходником и отказывает, если тело собрано не из него (как реле).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import layout  # noqa: E402
import build_mac  # noqa: E402

TARGET = layout.body_target()            # …/_body_target/release
BUILT = TARGET.parent / "BODY-BUILT.json"
EXES = ("praxis-body", "praxis-bridge")


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def stamp(src: Path) -> dict:
    digest, files = build_mac.body_source_digest(src)
    return {"source": str(src), "source_digest": digest, "files": files,
            "exe_sha256": {name: _sha(TARGET / f"{name}.exe") for name in EXES},
            "built_utc": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--live", default="", help="дерево агента (умолчание — layout.tree())")
    args = ap.parse_args()
    src = layout.tree(args.live or None).resolve() / "body"
    if not (src / "Cargo.toml").is_file():
        raise SystemExit(f"нет исходника тела: {src}")
    if args.build:
        cmd = ["cargo", "build", "--release", "-p", "praxis-body", "-p", "praxis-bridge",
               "--target-dir", str(TARGET.parent)]
        print(" ".join(cmd))
        subprocess.run(cmd, cwd=src, check=True)
        made = stamp(src)
        BUILT.parent.mkdir(parents=True, exist_ok=True)
        BUILT.write_text(json.dumps(made, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"записано {BUILT}: отпечаток исходника {made['source_digest'][:12]}")
        return 0
    digest, files = build_mac.body_source_digest(src)
    try:
        made = json.loads(BUILT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"нет {BUILT} — тело собрано неизвестно чем: --build")
        return 2
    ok = made.get("source_digest") == digest and all(
        made.get("exe_sha256", {}).get(n) == _sha(TARGET / f"{n}.exe") for n in EXES)
    print(f"исходник {digest[:12]} ({files} файлов); собрано из {str(made.get('source_digest', ''))[:12]} "
          f"({made.get('built_utc')}) — {'совпадает' if ok else 'НЕ СОВПАДАЕТ'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
