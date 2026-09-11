# -*- coding: utf-8 -*-
"""Сверка зеркала со ЗНАЧЕНИЯМИ живых секретов, а не с паттернами.

    python installer/secret_scan.py <дерево> <json с живыми секретами>
    python installer/secret_scan.py <дерево> installer/secret-strings.txt

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
import subprocess
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
    # Добавлено 11.09 сверкой перед выпуском 0.5.4: `memory/llm.json` держит и
    # ключи, и НАЗВАНИЯ — роль голоса, роль оценщика, семейство провайдера.
    # Их сёстры (luna, terra) в списке были, эти три — нет, и сверка объявляла
    # «публиковать НЕЛЬЗЯ» на именах моделей. Проверено глазами: в этом файле
    # ключей нет вовсе, они живут в `.env`.
    "gpt-5.6-sol": "имя модели (роль голоса)",
    "glm-5.3-flash": "имя модели (роль оценщика)",
    "anthropic": "название семейства провайдера, не ключ",
}

#: Слишком короткое или слишком общее значение ищется по всему дереву как шум:
#: «1», «true», «/app» и подобное. Порог — не «доверие», а способ не утонуть.
MIN_LEN = 8


#: Крупные файлы читать текстом бессмысленно и дорого: секрет живёт в исходниках
#: и конфигах, а не в архиве на четверть гигабайта. Пропуск НАЗЫВАЕТСЯ числом —
#: «не смотрел» и «чисто» обязаны отличаться.
MAX_FILE_BYTES = 8 * 1024 * 1024


def listing(tree: pathlib.Path) -> tuple[list[tuple[str, pathlib.Path]], str]:
    """Что именно проверять — и сказать вслух, что именно.

    ⚠ Если это репозиторий, берём ИНДЕКС git, а не папку: публикуется индекс, а
    в рабочей копии рядом живут `installer/build/` (собранная поставка),
    `node_modules/`, `target/` — сотни тысяч файлов, которых в зеркале нет и не
    будет. Обход папки на них и падал.

    ⚠ `-c core.quotepath=false`: без него git отдаёт русские имена в
    восьмеричных экранах, и половина файлов просто не находится на диске.
    """
    listed = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "-z"],
        cwd=str(tree), capture_output=True)
    if listed.returncode == 0 and listed.stdout.strip():
        rows = [r for r in listed.stdout.decode("utf-8", "replace").split("\0") if r]
        out = [(rel, tree / rel) for rel in rows]
        return [(rel, path) for rel, path in out if path.is_file()], "индекс git"
    out = []
    for path in tree.rglob("*"):
        posix = path.as_posix()
        if "/.git/" in posix or posix.endswith("/.git"):
            continue
        if not path.is_file():
            continue
        out.append((path.relative_to(tree).as_posix(), path))
    return out, "обход папки (это не репозиторий)"


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
    wanted: dict[str, str] = {}
    raw = SECRETS.read_text(encoding="utf-8")
    if SECRETS.suffix.lower() == ".txt":
        # ⚠ Второй законный вход: `installer/secret-strings.txt` — тот самый
        # список буквальных значений владельца, который читает и сборка. Пока
        # сверка умела только JSON со СНЯТЫМИ живыми `.env`, эти два списка
        # жили порознь: один проверялся в поставке, другой — в зеркале, и
        # дописать значение надо было в оба. Теперь достаточно одного файла.
        for n, line in enumerate(raw.splitlines(), 1):
            value = line.strip()
            if value and not value.startswith("#"):
                wanted[f"{SECRETS.name}:{n}"] = value
    else:
        for source, text in json.loads(raw).items():
            if text:
                wanted.update(values_of(text, source))

    checked = {
        name: value
        for name, value in wanted.items()
        if len(value) >= MIN_LEN and value not in NOT_SECRET and not value.startswith(("/", "."))
    }
    print(f"живых значений снято: {len(wanted)}, из них проверяется: {len(checked)}")
    print(f"(короткие, пути и его собственные — мимо: {len(wanted) - len(checked)})")

    files, how = listing(TREE)
    print(f"что проверяем: {how}, файлов {len(files)}")

    # ⚠ Файлы читаются ПО ОДНОМУ и не копятся. Первая редакция складывала весь
    # текст дерева в список пар и падала с MemoryError на рабочей копии: рядом с
    # исходниками лежит `installer/build/` с собранной поставкой на 260 МБ, и
    # обход уходил в неё. Память кончалась ровно перед выводом — то есть сверка,
    # написанная ради «публиковать нельзя», не говорила НИЧЕГО.
    found: dict[str, list[str]] = {}
    looked = skipped_big = skipped_bad = 0
    for rel, path in files:
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                skipped_big += 1
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped_bad += 1
            continue
        looked += 1
        for name, value in checked.items():
            if value in text:
                found.setdefault(name, []).append(rel)
    print(f"прочитано текстом: {looked}"
          + (f", пропущено крупных (> {MAX_FILE_BYTES // (1024 * 1024)} МБ): {skipped_big}"
             if skipped_big else "")
          + (f", не прочиталось: {skipped_bad}" if skipped_bad else ""))

    hits = 0
    for name in sorted(found):
        hits += 1
        where = found[name]
        print(f"  ⚠ НАЙДЕНО значение ручки {name} в: {', '.join(where[:6])}")
    print()
    if hits:
        raise SystemExit(f"СЕКРЕТЫ В ЗЕРКАЛЕ: {hits} — публиковать НЕЛЬЗЯ")
    print("ноль попаданий: ни одно живое значение в зеркало не уехало")


if __name__ == "__main__":
    main()
