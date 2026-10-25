# -*- coding: utf-8 -*-
"""Сборка Windows-установщика одной командой — в правильном порядке и без холостых прогонов.

    python installer/build_win.py --tree ../../port-2409            # всё
    python installer/build_win.py --tree ../../port-2409 --dry      # только план
    python installer/build_win.py --tree ../../port-2409 --skip-fronts --skip-dist

Порядок выучен на 1.2.9 (01.10), где три прогона ушли на ошибки порядка:
фронты → крейты (ПАРАЛЛЕЛЬНО — они независимы, это ~9 мин → ~5,5) → тело из
ПОСТАВЛЯЕМОГО дерева (body_src --live, иначе отпечаток бинаря не сойдётся с
деревом) → build_dist. Стенды здесь не гоняются: полный гейт — Docker
(см. gate*.sh), у сборки --skip-tests оправдан гейтом на тех же SHA.

Почему python, а не .ps1: subprocess без MSYS-переделки путей (docker /bin/sh
из Git Bash ломался именно об это), одинаковый запуск из любой оболочки,
логи в build/build-<шаг>.log.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
BUILD = DESK / "installer" / "build"

#: Три независимых крейта: собираются параллельно. Версия в Cargo.toml И
#: Cargo.lock должна совпадать — иначе cargo с --locked (гейт) и build_dist
#: (сверка версий в exe) откажут по-разному и вразнобой.
#: npm на Windows — npm.cmd: голое «npm» subprocess не найдёт, ищем which-ом.
NPM = shutil.which("npm") or "npm"


def cargo() -> str:
    return shutil.which("cargo") or "cargo"


CRATES = (
    ("shell", ["cargo", "build", "--release", "--features", "custom-protocol"]),
    ("svc", ["cargo", "build", "--release"]),
    ("setup", ["cargo", "build", "--release", "--features", "custom-protocol"]),
)
WINDOWS_TARGET = "x86_64-pc-windows-msvc"
CRATE_PRODUCTS = {"shell": "helene.exe", "svc": "helene-svc.exe", "setup": "helene-setup.exe"}


def log(note: str) -> None:
    print(f"[build_win] {note}", flush=True)


def run(name: str, cmd: list[str], cwd: Path, log_name: str) -> subprocess.Popen:
    out = open(BUILD / log_name, "ab")
    return subprocess.Popen(cmd, cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT)


def _sha(path: Path) -> bytes:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").digest()


def main() -> int:
    ap = argparse.ArgumentParser(description="сборка Windows-установщика одной командой")
    ap.add_argument("--tree", required=True, help="дерево агента, которое едет в поставку (port-2409)")
    ap.add_argument("--dry", action="store_true", help="показать план и выйти")
    ap.add_argument("--vnext-head", default="", help="exact Git HEAD of the composed vNext --tree")
    ap.add_argument("--relay-root", default="", help="staged tracked relay source (new variant)")
    ap.add_argument("--relay-kind", choices=("tracked", "embedded"), default="tracked")
    ap.add_argument("--build-relay", action="store_true",
                    help="build Windows relay only; return Linux binary+receipt from a server build first")
    ap.add_argument("--full-dist", action="store_true", help="let build_dist run full runtime/tests gates; default is an incomplete debug package")
    ap.add_argument("--external-tests", default="", help="exact-source external Linux + safe Windows test receipt bundle")
    ap.add_argument("--skip-fronts", action="store_true")
    ap.add_argument("--skip-crates", action="store_true")
    ap.add_argument("--skip-body", action="store_true")
    ap.add_argument("--skip-dist", action="store_true", help="собрать всё, но не паковать (отладка крейтов)")
    ap.add_argument("--skip-smokes", action="store_true", help="пропустить смоуки рантайма/голоса в упаковщике")
    args = ap.parse_args()
    tree = Path(args.tree).resolve()
    if not (tree / "agent.py").is_file():
        raise SystemExit(f"не похоже на дерево агента: {tree} (нет agent.py)")
    if bool(args.vnext_head) != bool(args.relay_root):
        raise SystemExit("vNext Windows distribution needs both --vnext-head and --relay-root")
    if args.build_relay and not args.relay_root:
        raise SystemExit("--build-relay needs a selected --relay-root")
    if args.relay_kind == "embedded" and not args.relay_root:
        raise SystemExit("embedded relay requires a pinned --relay-root")
    if args.full_dist and (args.skip_fronts or args.skip_crates or args.skip_body or args.skip_dist or args.skip_smokes):
        raise SystemExit("--full-dist cannot accompany an omitted build step or skipped smoke")
    if args.external_tests and not (args.full_dist and args.vnext_head):
        raise SystemExit("--external-tests requires --full-dist and pinned vNext sources")

    plan = [
        ("fronts", "пять npm-сборок (app, mobile, miniapp, remote, setup/ui)" if not args.skip_fronts else "пропуск"),
        ("crates", "shell/svc/setup ПАРАЛЛЕЛЬНО (cargo release)" if not args.skip_crates else "пропуск"),
        ("body", f"тело из поставляемого дерева: {tree}" if not args.skip_body else "пропуск"),
        ("dist", f"build_dist --tree {tree} " + ("полный gate" if args.full_dist else "DEBUG: runtime/tests пропущены")
         if not args.skip_dist else "пропуск"),
    ]
    if args.relay_root:
        plan.insert(2, ("relay", "собрать Windows-реле, Linux бинарь+квитанцию проверить с сервера"
                         if args.build_relay else "проверить уже собранные два target-бинаря и их квитанции"))
    for step, note in plan:
        log(f"{step:8} {note}")
    if args.dry:
        return 0

    if args.vnext_head:
        # A direct build_dist call repeats these checks. This early check avoids
        # spending build minutes on an already stale source selection.
        import vnext_dist  # noqa: PLC0415
        import build_dist  # noqa: PLC0415
        import relay_src  # noqa: PLC0415
        relay_root = Path(args.relay_root).resolve()
        vnext_dist.verify_edition(DESK.parent, args.vnext_head, tree)
        vnext_dist.verify_relay(DESK.parent, args.vnext_head, relay_root, kind=args.relay_kind)
        if args.build_relay:
            relay_src.build(relay_root)
        # Linux binary and receipt come from a server build, never local Docker.
        build_dist.relay_provenance(relay_root / "target/release/codex-proxy-server.exe", False, relay_root)
        build_dist.relay_linux_provenance(relay_src.linux_binary(relay_root), False, relay_root)

    started = time.time()
    BUILD.mkdir(parents=True, exist_ok=True)

    if not args.skip_fronts:
        t0 = time.time()
        for sub in ("app", "mobile", "miniapp", "remote"):
            subprocess.run([NPM, "run", "build"], cwd=str(DESK / sub), check=True)
        subprocess.run([NPM, "run", "build"], cwd=str(DESK / "setup" / "ui"), check=True)
        log(f"fronts   готовы за {time.time() - t0:.0f} с")

    if not args.skip_crates:
        t0 = time.time()
        # setup/ui нужен ДО сборки setup-крейта (таури вшивает фронт).
        procs: list[tuple[str, subprocess.Popen]] = []
        cargo_exe = cargo()
        for name, cmd in CRATES:
            procs.append((name, run(name, [cargo_exe, *cmd[1:], "--target", WINDOWS_TARGET, "--target-dir",
                                          str(DESK / name / "target")],
                                    DESK / name, f"crate-{name}.log")))
        failed = [n for n, p in procs if p.wait() != 0]
        if failed:
            raise SystemExit(f"крейты красные: {', '.join(failed)} — логи build/crate-<имя>.log")
        # `--target` takes precedence over CARGO_BUILD_TARGET and .cargo/config.
        # The packer uses target/release, so copy ONLY the products of this
        # explicit target, not an older same-version binary left at that path.
        for name, exe in CRATE_PRODUCTS.items():
            produced = DESK / name / "target" / WINDOWS_TARGET / "release" / exe
            if not produced.is_file():
                raise SystemExit(f"крейту {name} некуда ставить квитанцию: нет {produced}")
            selected = DESK / name / "target" / "release" / exe
            selected.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(produced, selected)
            if _sha(produced) != _sha(selected):
                raise SystemExit(f"crate output changed during copy: {exe}")
        log(f"crates   готовы параллельно за {time.time() - t0:.0f} с")

    desk_receipt = BUILD / "DESK-BUILT.json"
    if args.vnext_head and not args.skip_fronts and not args.skip_crates:
        # Emit only after the frontend assets and all three native crates built.
        # A direct build_dist caller must read this same source/output receipt.
        vnext_dist.stamp_desk_build(DESK, desk_receipt)

    if not args.skip_body:
        t0 = time.time()
        subprocess.run([sys.executable, str(DESK / "installer" / "body_src.py"),
                        "--build", "--live", str(tree)], check=True)
        log(f"body     готов за {time.time() - t0:.0f} с")

    if not args.skip_dist:
        t0 = time.time()
        subprocess.run([sys.executable, str(DESK / "installer" / "build_dist.py"),
                        "--tree", str(tree),
                        *([] if args.full_dist else ["--skip-runtime", "--skip-tests"]),
                        *(["--skip-tests", "--external-tests", str(Path(args.external_tests).resolve())]
                          if args.external_tests else []),
                        *(["--vnext-head", args.vnext_head, "--relay-root", str(relay_root),
                           "--desk-build-receipt", str(desk_receipt), "--relay-kind", args.relay_kind]
                          if args.vnext_head else []),
                        *(["--skip-smokes"] if args.skip_smokes else [])], check=True)
        if args.full_dist:
            import json  # noqa: PLC0415
            note = json.loads((BUILD / "Helene" / "helene-build.json").read_text(encoding="utf-8"))
            if not note.get("complete") or not (BUILD / f"Helene-{note['version']}-setup.exe").is_file():
                raise SystemExit("full distribution build did not produce a complete setup.exe")
        log(f"dist     готов за {time.time() - t0:.0f} с")

    log(f"итого {time.time() - started:.0f} с")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
