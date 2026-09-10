# -*- coding: utf-8 -*-
"""Что НОВОГО личного появилось в зеркале этим синком.

    python installer/personal_scan.py <репозиторий> <ревизия ДО синка>

Гонять ПОСЛЕ каждого синка зеркала, рядом с `secret_scan.py`. Две сверки разные, и
вторая не следует из первой: 10.09 сверка по значениям секретов прошла честно, а id
человека уехал в публичный репозиторий — он не в `.env`, он в фикстуре теста.

Вторая половина рецепта, которую я пропустил и о которой память предупреждала прямым
текстом: «Отдельно искать НОВОЕ личное: `@handle`, id 9–10 цифр, `-100…`, e-mail, пары
"Имя Фамилия", IPv4 — и сравнивать с уже опубликованным, иначе тонешь в шуме. Егор решил:
своё (имя, `@tatarskiy_e4pochmak`, id) оставляем, чужое убираем».

Сверка по секретам (`secret_scan.py`) этого класса не ловит: id человека не лежит в
`.env`, он лежит в фикстуре теста и выглядит как обычное число.

Ключевое слово — НОВОЕ. Сравниваем с тем, что было опубликовано ДО синка: всё, что уже
годами лежало в зеркале, — не находка этого дня, а старая история; тонуть в ней значит не
увидеть настоящее.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(sys.argv[1])
BEFORE = sys.argv[2]                      # ревизия зеркала ДО синка

#: Его собственное — остаётся по его решению.
HIS_OWN = {"809306689", "tatarskiy_e4pochmak"}

PATTERNS = {
    "telegram id (9–10 цифр)": re.compile(r"(?<![\d.\-])(\d{9,10})(?![\d.])"),
    "чат -100…": re.compile(r"-100\d{8,}"),
    "@handle": re.compile(r"(?<![\w/])@([a-zA-Z][\w]{4,31})"),
    "e-mail": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"),
    "IPv4": re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])"),
}

#: Технический шум, который под «id 9–10 цифр» и IPv4 попадает по форме, а личным не
#: является. Список закрытый и с причиной у каждого.
NOISE = {
    "1234567890": "явная синтетика фикстур",
    "0.0.0.0": "адрес прослушивания",
    "127.0.0.1": "петля",
    "255.255.255.255": "маска",
    "1.2.3.4": "документационный",
    "192.168.1.1": "частная сеть, документационный",
}


def texts(rev: str | None) -> dict[str, str]:
    """Файлы ядра на ревизии (или на диске, если rev не задан)."""
    out: dict[str, str] = {}
    if rev is None:
        # ⚠ Только то, что git ОТСЛЕЖИВАЕТ. Обход диска читал `node_modules` и собранный
        # рантайм — 5235 «находок», ни одна из которых не публикуется. Публикуется ровно
        # содержимое индекса, и смотреть надо в него.
        listing = subprocess.run(["git", "-C", str(REPO), "-c", "core.quotepath=false",
                                  "ls-files", "--cached"], capture_output=True)
        for rel in listing.stdout.decode("utf-8", "replace").splitlines():
            try:
                out[rel] = (REPO / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        return out
    listing = subprocess.run(["git", "-C", str(REPO), "-c", "core.quotepath=false",
                              "ls-tree", "-r", "--name-only", rev], capture_output=True)
    for rel in listing.stdout.decode("utf-8", "replace").splitlines():
        blob = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{rel}"],
                              capture_output=True)
        out[rel] = blob.stdout.decode("utf-8", "replace")
    return out


def found(pool: dict[str, str]) -> dict[str, dict[str, set[str]]]:
    hits: dict[str, dict[str, set[str]]] = {}
    for kind, rx in PATTERNS.items():
        for rel, text in pool.items():
            for match in rx.finditer(text):
                value = match.group(1) if match.groups() else match.group(0)
                if value in HIS_OWN or value in NOISE:
                    continue
                hits.setdefault(kind, {}).setdefault(value, set()).add(rel)
    return hits


def main() -> None:
    now, before = found(texts(None)), found(texts(BEFORE))
    total_new = 0
    for kind in PATTERNS:
        fresh = {v: w for v, w in now.get(kind, {}).items() if v not in before.get(kind, {})}
        if not fresh:
            continue
        total_new += len(fresh)
        print(f"\n{kind} — НОВОГО: {len(fresh)}")
        for value, where in sorted(fresh.items()):
            print(f"    {value:<20} в {', '.join(sorted(where))[:110]}")
    print()
    if total_new:
        print(f"НОВОГО ЛИЧНОГО В ЗЕРКАЛЕ: {total_new} значений — разобрать каждое")
    else:
        print("нового личного этим синком не появилось")


if __name__ == "__main__":
    main()
