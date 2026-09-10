# -*- coding: utf-8 -*-
"""Все стенды продукта одной командой: питон, окно, Rust.

    python tests/run_all.py            # питон и окно (быстро, ~минута)
    python tests/run_all.py --rust     # плюс cargo test в shell, svc, setup
    python tests/run_all.py --list     # что бы запустилось, ничего не запуская

⚠ ЗАЧЕМ ЭТОТ ФАЙЛ. Наборов было ДВА, и они звались разными командами:
`python tests/t_*.py` и `node app/test/*.mjs`. Второй легко забыть — и его
забывали: `actions.test.mjs` простоял КРАСНЫМ от переделки ленты шагов до
09.09, проверяя слова, которых компонент давно не говорил. Обычный прогон его
не звал, потому что «обычный прогон» знал только про питон.

Правило теперь одно: прогон один. Что добавили новый стенд — видно по составу
(`--list`), а не по памяти того, кто выпускает; сборка выпуска зовёт этот же
файл сама (`build_dist.py`, шаг «стенды»).

Стенды Rust отдельным ключом намеренно: `cargo test` в трёх крейтах — это
минуты и требует тулчейна, а питоновский набор гоняется на каждой правке.
Выпуск зовёт с `--rust`.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESK = HERE.parent

#: Крейты Rust: у каждого свой набор, и все три едут в поставку.
CRATES = ("shell", "svc", "setup")


def python_stands() -> list[Path]:
    return sorted(HERE.glob("t_*.py"))


def window_stands() -> list[Path]:
    return sorted((DESK / "app" / "test").glob("*.mjs"))


def run(cmd: list[str], cwd: Path, name: str, quiet: bool) -> tuple[bool, str]:
    started = time.time()
    done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    took = time.time() - started
    ok = done.returncode == 0
    mark = "  ok" if ok else "ПАДАЕТ"
    print(f"{mark}  {name}  ({took:.1f} с)", flush=True)
    if not ok and not quiet:
        tail = ((done.stdout or "") + (done.stderr or "")).strip().splitlines()
        for line in tail[-25:]:
            print("        " + line)
    return ok, name


def main() -> int:
    ap = argparse.ArgumentParser(description="все стенды продукта одной командой")
    ap.add_argument("--rust", action="store_true", help="плюс cargo test в shell, svc, setup")
    ap.add_argument("--list", action="store_true", help="показать состав и выйти")
    ap.add_argument("--quiet", action="store_true", help="не печатать хвосты падений")
    args = ap.parse_args()

    py = python_stands()
    win = window_stands()
    if args.list:
        print(f"питон ({len(py)}):")
        for one in py:
            print("   ", one.name)
        print(f"окно ({len(win)}):")
        for one in win:
            print("   ", one.name)
        print(f"rust ({len(CRATES)}):", ", ".join(CRATES))
        return 0

    print(f"стенды: питон {len(py)}, окно {len(win)}"
          + (f", rust {len(CRATES)}" if args.rust else " (rust — ключом --rust)"))
    failed: list[str] = []

    for one in py:
        ok, name = run([sys.executable, str(one)], DESK, f"питон · {one.name}", args.quiet)
        if not ok:
            failed.append(name)

    node = shutil.which("node")
    if not node:
        # Молчать нельзя: «стенды прошли» без стендов окна — это ровно тот
        # случай, ради которого файл написан.
        print("ПАДАЕТ  окно · node не найден — стенды окна НЕ ПРОГНАНЫ")
        failed.append("окно · node не найден")
    elif win:
        ok, name = run([node, "--test", *[str(w) for w in win]], DESK / "app",
                       f"окно · {len(win)} файлов", args.quiet)
        if not ok:
            failed.append(name)

    if args.rust:
        cargo = shutil.which("cargo")
        if not cargo:
            print("ПАДАЕТ  rust · cargo не найден — стенды Rust НЕ ПРОГНАНЫ")
            failed.append("rust · cargo не найден")
        else:
            for crate in CRATES:
                ok, name = run([cargo, "test", "--offline"], DESK / crate,
                               f"rust · {crate}", args.quiet)
                if not ok:
                    failed.append(name)

    print()
    if failed:
        print(f"КРАСНЫХ: {len(failed)}")
        for name in failed:
            print("   ", name)
        return 1
    print("все зелёные")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
