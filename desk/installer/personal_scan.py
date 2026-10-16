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
import json
import re
import subprocess
import sys

# ⚠ Вывод — в UTF-8: в голой консоли владельца (cp1251) печать находки роняет прибор
# `UnicodeEncodeError`, и «упал» становится неотличим от «нашёл». Тот же класс поймала
# приёмка 11.09 на `secret_scan`, и лечится он в обоих одинаково.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

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
    # 16.10: три точных, несуществующих room-id в test_room_identity_private_1510;
    # одна используется также в герметичной среде/корпусе модерации. НЕ имя
    # ключа и не диапазон: любой новый адрес комнаты остаётся находкой.
    "-100999999999999999": "вымышленная тестовая группа вне рабочего диапазона",
    "-100999999999999998": "вымышленная чужая группа в отрицательном тесте",
    "-100000000000000000": "некорректный id из отрицательного теста",
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
        # Только отслеживаемые имена, но WORKTREE-байты: до staging это проверяет
        # готовящийся дифф. Для выпуска вызывающий обязан заморозить чистый HEAD;
        # грязное дерево/частичный index не является доказательством готового снимка.
        listing = subprocess.run(["git", "-C", str(REPO), "-c", "core.quotepath=false",
                                  "ls-files", "-z", "--cached"], capture_output=True, check=True)
        for rel in listing.stdout.decode("utf-8", "replace").split("\0"):
            if rel:
                # Missing/unreadable tracked bytes cannot mean "no personal
                # values".  Refuse the entire check, not just this path.
                out[rel] = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        return out
    # One historical tree needs one object-reader, not a process per file.  The
    # old loop took >120 s on this tree and could never finish the release gate.
    # Request object IDs, not "rev:path": even a legal filename containing a
    # newline cannot change the batch protocol.  No archive export-ignore rule
    # is allowed to hide tracked bytes from this privacy scan.
    listing = subprocess.run(["git", "-C", str(REPO), "ls-tree", "-r", "-z", rev],
                             capture_output=True, check=True)
    blobs: list[tuple[str, bytes]] = []
    for row in listing.stdout.split(b"\0"):
        if not row:
            continue
        header, separator, raw_path = row.partition(b"\t")
        if not separator:
            raise ValueError("malformed Git tree entry")
        mode, kind, oid = header.split()
        if kind != b"blob":
            # Gitlinks are not file contents; git show previously returned no
            # usable text for them.  Trees are represented by their leaves.
            if kind == b"commit":
                continue
            raise ValueError("unexpected Git tree entry")
        blobs.append((raw_path.decode("utf-8", "replace"), oid))
    if not blobs:
        return out
    batch = subprocess.run(["git", "-C", str(REPO), "cat-file", "--batch"],
                           input=b"".join(oid + b"\n" for _, oid in blobs),
                           capture_output=True, check=True).stdout
    position = 0
    for rel, oid in blobs:
        line_end = batch.find(b"\n", position)
        if line_end < 0:
            raise ValueError("incomplete Git object header")
        received, kind, size = batch[position:line_end].split()
        if received != oid or kind != b"blob" or not size.isdigit():
            raise ValueError("Git object identity changed during privacy scan")
        start, end = line_end + 1, line_end + 1 + int(size)
        if end >= len(batch) or batch[end:end + 1] != b"\n":
            raise ValueError("incomplete Git object body")
        out[rel] = batch[start:end].decode("utf-8", "replace")
        position = end + 1
    if position != len(batch):
        raise ValueError("unclaimed Git object bytes")
    return out


def without_inventory_digests(rel: str, text: str) -> str:
    """A digit run inside a typed source digest is not a Telegram identity.

    Exemption is limited to this product's explicit payload-inventory fields.
    Owner/contact fields, untyped JSON and prose remain fully scanned.
    """
    schemas = {'HELENE-SOURCE.json': ('tree_inventory_scope', 'published_payload', 'frozen_tree_files',
               ('frozen_tree','accepted_tree_baseline','source_public_parent')),
               'desk/tests/fixtures/owner-supplied-skills-1.5.9.json': ('schema', 'helene.owner-supplied-skills.v1', 'files',
               ('accepted_public_source', 'prior_public_source'))}
    if rel not in schemas: return text
    # Keep duplicate keys: ordinary json.loads followed by dumps would erase
    # earlier owner/contact fields. Exempt only the typed value at its own path.
    class ObjectPairs(list): pass
    try: value = json.loads(text, object_pairs_hook=ObjectPairs)
    except ValueError: return text
    marker, expected, inventory_key, refs = schemas[rel]
    if not isinstance(value, ObjectPairs) or dict(value).get(marker) != expected: return text
    def encode(item, location=()):
        size = 40 if len(location) == 1 and location[0] in refs else 64 if len(location) == 2 and location[0] == inventory_key else None
        if size and isinstance(item, str) and re.fullmatch(r'[a-f0-9]{' + str(size) + '}', item):
            return json.dumps('<source-digest>')
        if isinstance(item, ObjectPairs):
            return '{' + ','.join(json.dumps(k, ensure_ascii=False) + ':' + encode(v, location+(k,)) for k,v in item) + '}'
        if isinstance(item, list): return '[' + ','.join(encode(v, location+('[]',)) for v in item) + ']'
        return json.dumps(item, ensure_ascii=False)
    return encode(value)


def found(pool: dict[str, str]) -> dict[str, dict[str, set[str]]]:
    hits: dict[str, dict[str, set[str]]] = {}
    for kind, rx in PATTERNS.items():
        for rel, text in pool.items():
            if kind == "telegram id (9–10 цифр)": text = without_inventory_digests(rel, text)
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
        # ⚠ ОТКАЗ, А НЕ ПЕЧАТЬ. Прибор находил и выходил нулём: для человека это строка
        # «разобрать каждое», для любой цепочки — «прошло». Приёмка 11.09 прогнала его на
        # живом зеркале: 173 значения и код возврата 0. Вторая из двух обязательных
        # сверок публикации отвечала на слово «нашёл» противоположным кодом, чем первая
        # (`secret_scan` поднимает SystemExit) — два прибора, названные одним списком в
        # RELEASE.md, вели себя по-разному. Программного вызывающего у него сегодня нет,
        # и это единственная причина, по которой дыра до сих пор спала.
        raise SystemExit(
            f"НОВОГО ЛИЧНОГО В ЗЕРКАЛЕ: {total_new} значений — разобрать каждое"
        )
    print("нового личного этим синком не появилось")


if __name__ == "__main__":
    main()
