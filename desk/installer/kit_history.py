# -*- coding: utf-8 -*-
"""Прежние тексты стартового комплекта — для обновления нетронутых файлов души (1.2.5).

Стартовый комплект (`resources/soul/**`, конституция, запись дня ноль) кладётся в дом агента
только если файла ещё нет: навык, переписанный агентом, обновление не трогает. Но 28.09
тексты поставки переписаны (без рода, без чужих имён и путей — слово Егора), и у агентов,
рождённых раньше, лежали бы прежние. Правило: файл, который совпадает с КАКОЙ-ЛИБО прежней
поставкой слово в слово (после подстановки имён), не правил никто — его обновление меняет;
правленый — не трогает.

Этот сценарий собирает все прежние редакции из истории git в
`resources/.shipped-history.json` (точка в имени — `_seed_kit` такие файлы не кладёт).
Запуск из корня репозитория:  python desk/installer/kit_history.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
REPO = DESK.parent
RES = DESK / "resources"
OUT = RES / ".shipped-history.json"


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


def kit_files() -> list[str]:
    """Пути комплекта относительно resources/ — как их знает `boot.refresh_kit`."""
    names = ["SOUL.md", "self-day-zero.md"]
    names += sorted(str(p.relative_to(RES)).replace("\\", "/")
                    for p in (RES / "soul").rglob("*.md"))
    return names


def main() -> None:
    history: dict[str, list[str]] = {}
    for rel in kit_files():
        path = f"desk/resources/{rel}"
        seen: list[str] = []
        current = (RES / rel).read_text(encoding="utf-8") if (RES / rel).is_file() else None
        for commit in _git("log", "--format=%H", "--", path).split():
            try:
                text = _git("show", f"{commit}:{path}")
            except subprocess.CalledProcessError:
                continue
            if text not in seen and text != current:
                seen.append(text)
        if seen:
            history[rel] = seen
    OUT.write_text(json.dumps({"v": 1, "files": history}, ensure_ascii=False, indent=0) + "\n",
                   encoding="utf-8", newline="\n")
    total = sum(len(v) for v in history.values())
    print(f"{OUT.name}: {len(history)} файлов, прежних редакций {total}, "
          f"{OUT.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
