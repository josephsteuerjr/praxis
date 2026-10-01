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


def log(note: str) -> None:
    print(f"[build_win] {note}", flush=True)


def run(name: str, cmd: list[str], cwd: Path, log_name: str) -> subprocess.Popen:
    out = open(BUILD / log_name, "ab")
    return subprocess.Popen(cmd, cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT)


def main() -> int:
    ap = argparse.ArgumentParser(description="сборка Windows-установщика одной командой")
    ap.add_argument("--tree", required=True, help="дерево агента, которое едет в поставку (port-2409)")
    ap.add_argument("--dry", action="store_true", help="показать план и выйти")
    ap.add_argument("--skip-fronts", action="store_true")
    ap.add_argument("--skip-crates", action="store_true")
    ap.add_argument("--skip-body", action="store_true")
    ap.add_argument("--skip-dist", action="store_true", help="собрать всё, но не паковать (отладка крейтов)")
    args = ap.parse_args()
    tree = Path(args.tree).resolve()
    if not (tree / "agent.py").is_file():
        raise SystemExit(f"не похоже на дерево агента: {tree} (нет agent.py)")

    plan = [
        ("fronts", "пять npm-сборок (app, mobile, miniapp, remote, setup/ui)" if not args.skip_fronts else "пропуск"),
        ("crates", "shell/svc/setup ПАРАЛЛЕЛЬНО (cargo release)" if not args.skip_crates else "пропуск"),
        ("body", f"тело из поставляемого дерева: {tree}" if not args.skip_body else "пропуск"),
        ("dist", f"build_dist --tree {tree} --skip-runtime --skip-tests" if not args.skip_dist else "пропуск"),
    ]
    for step, note in plan:
        log(f"{step:8} {note}")
    if args.dry:
        return 0

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
            procs.append((name, run(name, [cargo_exe, *cmd[1:]], DESK / name, f"crate-{name}.log")))
        failed = [n for n, p in procs if p.wait() != 0]
        if failed:
            raise SystemExit(f"крейты красные: {', '.join(failed)} — логи build/crate-<имя>.log")
        log(f"crates   готовы параллельно за {time.time() - t0:.0f} с")

    if not args.skip_body:
        t0 = time.time()
        subprocess.run([sys.executable, str(DESK / "installer" / "body_src.py"),
                        "--build", "--live", str(tree)], check=True)
        log(f"body     готов за {time.time() - t0:.0f} с")

    if not args.skip_dist:
        t0 = time.time()
        subprocess.run([sys.executable, str(DESK / "installer" / "build_dist.py"),
                        "--tree", str(tree), "--skip-runtime", "--skip-tests"], check=True)
        log(f"dist     готов за {time.time() - t0:.0f} с")

    log(f"итого {time.time() - started:.0f} с")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
