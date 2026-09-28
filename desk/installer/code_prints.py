# -*- coding: utf-8 -*-
"""Отпечатки кода выпуска — база переноса правок агента для установок до 1.2.5.

С 1.2.5 установщик ПК кладёт у установки чистую копию её версии (`pristine/<версия>.zip`)
и по ней видит, что в `tree/` и `app/` правил агент. У установок, поставленных раньше,
такой копии нет, и сравнить им было бы не с чем. Для них поставка несёт отпечатки прежних
выпусков — `server/updater/shipped-code.json`, `{"<версия>/<платформа>": {"tree/…": sha256}}`:
по ним видно, какие файлы агент правил и какие из них выпуск не трогал (такие переносятся
как есть); слить правку, которую тронул и выпуск, без содержимого базы нельзя — она
уезжает агенту материалом.

Снимается с развёрнутой поставки выпуска (папка сборки или установка, которую точно не
правили):

    python installer/code_prints.py <папка поставки> [--version 1.2.4] [--flavor windows]

Версия и платформа по умолчанию — из паспорта `helene-build.json` этой папки.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "server" / "updater"))

import codecarry  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", help="развёрнутая поставка выпуска (есть tree/ и app/)")
    parser.add_argument("--version", default="")
    parser.add_argument("--flavor", default="")
    parser.add_argument("--out", default=str(codecarry.SHIPPED))
    args = parser.parse_args(argv)
    root = Path(args.root)
    build = json.loads((root / "helene-build.json").read_text("utf-8-sig"))
    version = args.version or str(build.get("version") or "")
    flavor = args.flavor or codecarry.flavor_of(root)
    if not version or not flavor:
        raise SystemExit("версия или платформа не известны — задай --version и --flavor")
    prints = codecarry.code_prints(root)
    if not prints:
        raise SystemExit(f"в {root} нет кода (tree/, app/)")
    out = Path(args.out)
    table = json.loads(out.read_text("utf-8")) if out.is_file() else {}
    table[f"{version}/{flavor}"] = dict(sorted(prints.items()))
    out.write_text(json.dumps(dict(sorted(table.items())), ensure_ascii=False, indent=0) + "\n",
                   "utf-8", newline="\n")
    print(f"{version}/{flavor}: {len(prints)} файлов -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
