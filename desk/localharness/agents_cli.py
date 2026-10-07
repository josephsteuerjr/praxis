# -*- coding: utf-8 -*-
"""Список агентов установки — из командной строки. Для оболочки и для рук.

    python agents_cli.py list --base <папка установки>
    python agents_cli.py add  --base <папка установки> --name "Мира"
        [--soul-kind canonical|inherit|text] [--soul-file <путь>] [--soul-from <донор>]
    python agents_cli.py set-enabled --base <папка> --id mira --enabled false
    python agents_cli.py remove --base <папка> --id mira [--attic <папка>]

Зачем отдельная программа, если правила и так в `agents.py`. Затем, что читать
их должны двое: питон (раннер, канал) и Rust (оболочка, служба). Читать —
дёшево, и Rust читает сам (`common/agents.rs`, сверка общими случаями). А вот
ПИСАТЬ — заводить папку, выбирать id и свободный порт, решать, что наследуется
от корневого, — должен кто-то один. Две реализации «завести агента» разъехались
бы на первом же имени с кириллицей, и владелец получил бы папку с чужим именем
и порт, занятый дважды.

Контракт вызова (Rust-волна 1.4.0): флаговая форма, `PYTHONUTF8=1`, ответ —
одной строкой JSON в stdout, жалобы — в stderr и кодом возврата; Rust судит по
коду возврата, stdout может быть пуст.

Душа при рождении (`--soul-kind`): canonical — конституция поставки (умолчание,
файла сида нет); text — текст из `--soul-file` ляжет сидом `soul-seed.md` рядом
с конфигом (контракт UI-волны B1: текст всегда файлом, не через argv); inherit —
копия души донора `--soul-from` (умолчание — корневой агент). Раннер прочтёт
сид до первого старта и родит дом из него; файл останется как запись о рождении.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agents  # noqa: E402


def _read_soul_file(raw: str) -> str:
    """Текст сида из файла — BOM-толерантно, как конфиг (Блокнот пишет UTF-8 BOM)."""
    try:
        blob = Path(raw).read_bytes()
    except OSError as exc:
        raise ValueError(f"файл души не читается: {raw} ({exc})")
    if blob[:3] == b"\xef\xbb\xbf":
        blob = blob[3:]
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"файл души не в UTF-8: {raw} ({exc})")
    if not text.strip():
        raise ValueError(f"файл души пуст: {raw}")
    return text


def _soul_args(args) -> dict | None:
    """Собрать `soul` для create() из флагов. Отказ — внятной ошибкой, не тишиной."""
    kind = (args.soul_kind or "").strip().lower() or "canonical"
    if kind not in ("canonical", "inherit", "text", "doctor"):
        raise ValueError(f"--soul-kind: canonical | inherit | text | doctor, а не {kind!r}")
    if kind == "canonical":
        if args.soul_file or args.soul_from:
            raise ValueError("--soul-kind canonical не берёт ни --soul-file, ни --soul-from")
        return None
    if kind == "doctor":
        if args.soul_file or args.soul_from:
            raise ValueError("--soul-kind doctor не берёт ни --soul-file, ни --soul-from: "
                             "канон доктора читает сама программа из поставки")
        return {"kind": "doctor"}
    if kind == "text":
        if args.soul_from:
            raise ValueError("--soul-from — для inherit; для text душа берётся из --soul-file")
        if not args.soul_file:
            raise ValueError("--soul-kind text требует --soul-file (текст не едет через argv)")
        return {"kind": "text", "text": _read_soul_file(args.soul_file)}
    # inherit: донора называет --soul-from (умолчание — корневой); текста нет
    if args.soul_file:
        raise ValueError("--soul-file — для text; inherit берёт душу донора --soul-from")
    return {"kind": "inherit"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="агенты этой установки")
    parser.add_argument("what", choices=("list", "add", "set-enabled", "remove"))
    parser.add_argument("--base", default="", help="папка установки (где helene.json)")
    parser.add_argument("--name", default="", help="имя нового агента словами владельца")
    parser.add_argument("--soul-kind", default="", help="canonical | inherit | text (умолчание canonical)")
    parser.add_argument("--soul-file", default="", help="для text: путь к файлу с текстом души")
    parser.add_argument("--soul-from", default="", help="для inherit: id агента-донора (умолчание main)")
    parser.add_argument("--id", default="", help="id агента для set-enabled/remove")
    parser.add_argument("--enabled", default="", help="для set-enabled: true | false")
    parser.add_argument("--attic", default="",
                        help="для remove: папка чердака (куда переносится агент)")
    args = parser.parse_args(argv)

    base = Path(args.base).resolve() if args.base else Path.cwd()
    if args.what == "list":
        got = {"agents": [a.as_dict() for a in agents.roster(base)]}
        print(json.dumps(got, ensure_ascii=False))
        return 0

    if args.what == "set-enabled":
        word = (args.enabled or "").strip().lower()
        if word not in ("true", "false"):
            print("--enabled: true | false", file=sys.stderr)
            return 2
        try:
            fresh = agents.set_enabled(base, args.id, word == "true")
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps({"ok": True, "agent": fresh.as_dict()}, ensure_ascii=False))
        return 0

    if args.what == "remove":
        try:
            dest = agents.remove_agent(base, args.id, Path(args.attic) if args.attic else None)
        except (ValueError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps({"ok": True, "attic": str(dest)}, ensure_ascii=False))
        return 0

    # add — завести агента
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
        soul = _soul_args(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        made = agents.create(base, name, soul=soul,
                             donor_id=(args.soul_from or "").strip().lower() or None)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"папка агента не завелась: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(made.as_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
