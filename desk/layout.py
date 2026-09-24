# -*- coding: utf-8 -*-
"""Где что лежит на диске — одно объявление на сборку, приборы и стенды.

Раскладка сменилась 10.09: `desk/` переехал ВНУТРЬ репозитория `praxis`, рядом с
ядром и слоем издания::

    praxis-repo/            <- репозиторий (github.com/josephsteuerjr/praxis)
        praxis/             ядро агента
        helene/core/        слой: чем издание Элен отличается от ядра
        desk/               приложение Элен  (мы здесь)
        remote/             приложение Praxis: то же окно к серверу
    live/                   рабочая копия дерева агента   ┐
    _relay_prod_src/        зеркало исходника реле         ├ РЯДОМ, не внутри
    _body_target/           куда собираются тело и мост    ┘

Три нижних соседа в репозиторий не входят и не должны: дерево — рабочая копия
её прода, зеркало реле — копия чужого исходника с сервера, цель сборки — мусор
компилятора. Но найти их надо, и до 10.09 путь до них был жёстко `ROOT / имя`.

⚠ Почему это отдельный файл. После переезда `ROOT` стал корнем репозитория, и
жёсткая константа стала указывать в пустоту — молча: `rglob` по несуществующему
каталогу отдаёт пустой список, и сборка выпускала бы архив без `tree/` без
единого слова. Чинить это пришлось бы в четырёх местах сразу (сборка, прибор
ядра, зеркало реле, стенды) — то есть завести четыре расходящиеся правды о
том, где лежит одна и та же папка. Правда здесь одна.
"""
from __future__ import annotations

import os
from pathlib import Path

DESK = Path(__file__).resolve().parent
ROOT = DESK.parent                      # корень раскладки: praxis/, helene/, desk/, remote/

CORE = ROOT / "praxis"                  # ядро агента
LAYER = ROOT / "helene" / "core"        # слой издания Элен
REMOTE = ROOT / "remote"                # приложение Praxis: то же окно к серверу


def neighbour(name: str) -> Path:
    """Соседняя папка: внутри раскладки или уровнем выше, что найдётся первым.

    Оба места настоящие. До переезда соседи лежали рядом с `desk/`; после —
    рядом с репозиторием. Пока обе раскладки живут на дисках у людей, знать
    надо обе, а спорить о них — в одном месте.

    Нет нигде — возвращаем ожидаемое, чтобы отказ назвал путь, а не молчал.
    """
    for cand in (ROOT / name, ROOT.parent / name):
        if cand.exists():
            return cand
    return ROOT / name


def tree(cli: str | None = None) -> Path:
    """Рабочая копия дерева агента: аргумент, потом среда, потом сосед."""
    said = (cli or os.environ.get("HELENE_TREE_SRC") or "").strip()
    return Path(said).resolve() if said else neighbour("live")


def body_target() -> Path:
    """Куда собираются `helene-body.exe` и `helene-bridge.exe`."""
    said = (os.environ.get("HELENE_BODY_DIR") or "").strip()
    return Path(said).resolve() if said else neighbour("_body_target") / "release"


def relay_mirror() -> Path:
    """Зеркало живого исходника реле (копия `/opt/relay/Code`)."""
    return neighbour("_relay_prod_src")
