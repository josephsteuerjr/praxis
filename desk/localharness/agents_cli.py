# -*- coding: utf-8 -*-
"""Список агентов установки — из командной строки. Для оболочки и для рук.

    python agents_cli.py list --base <папка установки>
    python agents_cli.py add  --base <папка установки> --name "Мира"

Зачем отдельная программа, если правила и так в `agents.py`. Затем, что читать
их должны двое: питон (раннер, канал) и Rust (оболочка, служба). Читать —
дёшево, и Rust читает сам (`common/agents.rs`, сверка общими случаями). А вот
ПИСАТЬ — заводить папку, выбирать id и свободный порт, решать, что наследуется
от корневого, — должен кто-то один. Две реализации «завести агента» разъехались
бы на первом же имени с кириллицей, и владелец получил бы папку с чужим именем
и порт, занятый дважды.

Ответ — одной строкой JSON в stdout; жалобы — в stderr и код возврата.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agents  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="агенты этой установки")
    parser.add_argument("what", choices=("list", "add"))
    parser.add_argument("--base", default="", help="папка установки (где helene.json)")
    parser.add_argument("--name", default="", help="имя нового агента словами владельца")
    args = parser.parse_args(argv)

    base = Path(args.base).resolve() if args.base else Path.cwd()
    if args.what == "list":
        got = {"agents": [a.as_dict() for a in agents.roster(base)]}
        print(json.dumps(got, ensure_ascii=False))
        return 0

    name = (args.name or "").strip()
    if not name:
        print("у агента должно быть имя — им он подписывает свои слова", file=sys.stderr)
        return 2
    if not (base / agents.CONFIG_NAME).is_file():
        # Заводить соседа рядом с несуществующей установкой нельзя: пути в его
        # конфиге ведут «на два уровня вверх», и вести им будет некуда.
        print(f"рядом нет {agents.CONFIG_NAME} — это не папка установки: {base}", file=sys.stderr)
        return 2
    try:
        made = agents.create(base, name)
    except OSError as exc:
        print(f"папка агента не завелась: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(made.as_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
