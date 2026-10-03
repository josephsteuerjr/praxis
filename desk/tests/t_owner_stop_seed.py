# -*- coding: utf-8 -*-
"""Стенд стоп-крана владельца: путь флага и его чтение.

03.10, слово владельца («остановить/запустить — можно и нужно»): на Linux движок
всегда идёт от имени владельца, и флаг стопа лежит в его XDG-state — ВНЕ дерева
данных, чтобы снос дерева агента не снимал стоп. Путь обязаны собирать одинаково
двое: окно/служба на Rust (`common/owner_stop.rs`) и движок на Python (здесь).
Разъехались — Rust пишет в одну точку, Python читает из другой, и «Остановить
движок» перестаёт останавливать.

Проверяется (Linux): путь из HOME; без HOME флага нет (честное «не поддержано»);
stopped() видит появление и снятие флага. На Windows — только структурная сверка
Rust-пути (живой флаг Windows защищён службой и проверяется своими стендами).

Запуск:  python tests/t_owner_stop_seed.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localharness import owner_stop  # noqa: E402


def main() -> int:
    fails = 0

    def check(name: str, ok: bool) -> None:
        nonlocal fails
        print(("ok  " if ok else "FAIL") + " · " + name)
        if not ok:
            fails += 1

    if os.name != "nt":
        with tempfile.TemporaryDirectory() as home:
            os.environ["HOME"] = home
            seed = owner_stop.seed_file()
            check("путь флага — XDG-state вне дерева данных",
                  seed == Path(home) / ".local" / "state" / "helene" / "stop.json")
            check("флага нет — не остановлен", owner_stop.stopped() is False)

            seed.parent.mkdir(parents=True, exist_ok=True)
            seed.write_text('{"at": 1, "via": "window", "by": "owner"}', encoding="utf-8")
            check("флаг появился — остановлен", owner_stop.stopped() is True)

            seed.unlink()
            check("флаг снят — не остановлен", owner_stop.stopped() is False)

            del os.environ["HOME"]
            check("без HOME флага нет (платформа честно не поддержана)",
                  owner_stop.seed_file() is None)
    else:
        check("на Windows флаг живёт в ProgramData (свои стенды службы)",
              owner_stop.seed_file() is not None and "Helene" in str(owner_stop.seed_file()))

    rust = Path(__file__).resolve().parents[1] / "common" / "owner_stop.rs"
    text = rust.read_text(encoding="utf-8")
    check("Rust собирает тот же путь (структурная сверка)",
          all(part in text for part in ('join(".local")', 'join("state")', 'join("helene")', "var_os(\"HOME\")")))

    print("КРАСНЫХ:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
