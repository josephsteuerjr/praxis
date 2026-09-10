# -*- coding: utf-8 -*-
"""Сверка зеркала со ЗНАЧЕНИЯМИ живых секретов, а не с паттернами.

    python installer/secret_scan.py <дерево> <json с живыми секретами>

Первая из двух сверок перед публикацией; вторая — `personal_scan.py`, и пропускать её
нельзя: она ловит другой класс.

Почему по значениям. Регексы по generic-паттернам дают только синтетику из фикстур
(`sk-proj-Ab12…`, `123456:TEST-BOT-TOKEN`) — Егор предупреждал об этом заранее. Настоящий
ключ выглядит как обычная строка, и найти его можно только зная, ЧТО искать. Поэтому
берутся живые `.env`, `.deploy.env`, `memory/llm.json` с прода и каждое их значение ищется
в дереве, которое собираемся публиковать.

Значения сюда приезжают из временного файла и здесь же остаются: наружу печатается только
ИМЯ ручки и число попаданий — сам секрет в вывод не попадает никогда.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

TREE = pathlib.Path(sys.argv[1])
SECRETS = pathlib.Path(sys.argv[2])

#: Значения, которые совпадают с зеркалом и секретами НЕ являются. Список по ЗНАЧЕНИЮ, а
#: не по имени ручки: подставь кто-нибудь в `PRAXIS_TZ` настоящий ключ — и он поймается.
#: Каждое проверено глазами 10.09; тот же состав записан от сверки 27.08.
NOT_SECRET = {
    "809306689": "PRAXIS_OWNER_ID — его собственный id, публикует сознательно",
    "dm-809306689": "имя потока из его же id",
    "ru-RU-SvetlanaNeural": "имя голоса синтеза",
    "Europe/Samara": "часовой пояс",
    "addressed": "режим вовлечения в комнате",
    "https://api.z.ai/api/anthropic": "публичный адрес провайдера",
    "http://host.docker.internal:5012": "адрес внутри докера, наружу не смотрит",
    "gpt-5.6-luna": "имя модели",
    "gpt-5.6-terra": "имя модели",
}

#: Слишком короткое или слишком общее значение ищется по всему дереву как шум:
#: «1», «true», «/app» и подобное. Порог — не «доверие», а способ не утонуть.
MIN_LEN = 8


def values_of(text: str, source: str) -> dict[str, str]:
    """Пары ручка → значение из .env-подобного файла или JSON."""
    out: dict[str, str] = {}
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = {}

        def walk(node, path: str) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{path}.{key}" if path else key)
            elif isinstance(node, str):
                out[f"{source}:{path}"] = node

        walk(data, "")
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        out[f"{source}:{key.strip()}"] = value
    return out


def main() -> None:
    secrets = json.loads(SECRETS.read_text(encoding="utf-8"))
    wanted: dict[str, str] = {}
    for source, text in secrets.items():
        if text:
            wanted.update(values_of(text, source))

    checked = {
        name: value
        for name, value in wanted.items()
        if len(value) >= MIN_LEN and value not in NOT_SECRET and not value.startswith(("/", "."))
    }
    print(f"живых значений снято: {len(wanted)}, из них проверяется: {len(checked)}")
    print(f"(короткие, пути и его собственные — мимо: {len(wanted) - len(checked)})")

    blob: list[tuple[str, str]] = []
    for path in TREE.rglob("*"):
        if not path.is_file() or ".git/" in path.as_posix():
            continue
        try:
            blob.append((path.relative_to(TREE).as_posix(),
                         path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    print(f"файлов в дереве проверено: {len(blob)}")

    hits = 0
    for name, value in sorted(checked.items()):
        where = [rel for rel, text in blob if value in text]
        if where:
            hits += 1
            print(f"  ⚠ НАЙДЕНО значение ручки {name} в: {', '.join(where[:6])}")
    print()
    if hits:
        raise SystemExit(f"СЕКРЕТЫ В ЗЕРКАЛЕ: {hits} — публиковать НЕЛЬЗЯ")
    print("ноль попаданий: ни одно живое значение в зеркало не уехало")


if __name__ == "__main__":
    main()
