# -*- coding: utf-8 -*-
"""Описание руки `shell` — словами этого компьютера, а не сервера Праксис (1.2.3).

У дерева описание руки написано для контейнера на сервере: «Полный shell в
контейнере. Твой дом — /app…». 27.09 агент Егора на чистой установке позвал
`shell whoami`, получил `root` (busybox-w32 так называет администратора без UAC),
сложил это с «контейнером» из описания и доложил Егору, что, возможно, живёт в
Linux. Первое сообщение предупреждало, что описание — наследство, но описание руки
модель читает на каждом ходу, а первое сообщение — один раз.

Здесь описание переписывается целиком, тем же приёмом, что у остальных правок
издания: дерево — код владельца, его не правим, правим схему в списках рук после
импорта. Имя руки и схема входа — прежние.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger("helene.shell_words")

#: Списки рук у дерева — те же, что правит `body._install_absent`.
TOOL_LISTS = ("BASE_TOOLS", "OWNER_TOOLS", "PRAXIS_SELF_TOOLS", "SHARED_CONTEXT_TOOLS",
              "TOOLS", "ABSENCE_TOOLS", "FAMILY_TOOLS", "WORKSHOP_TOOLS", "FORGE_TOOLS")


def _p(path: Path) -> str:
    return path.as_posix()


def text(tree: Path, install_root: Path, platform: str | None = None) -> str:
    """Описание руки для этой установки."""
    platform = platform or sys.platform
    home = _p(tree)
    code = _p(install_root / "tree")
    app = _p(install_root / "app")
    if platform == "win32":
        return (
            "Твои руки на этом компьютере — bash (`bash -lc`; на Windows это busybox из "
            "поставки). Это Windows-ПК владельца, не контейнер и не сервер: папки /app здесь "
            f"нет. Твой дом — {home}: душа в {home}/soul (SOUL.md, self/CURRENT.md, skills/), "
            f"память в {home}/memory, рабочая папка и cwd по умолчанию — {home}/workspace. "
            f"Твой код — {code}, программа, которая тебя поднимает, — {app}. Пути — полные, "
            "с прямыми слэшами (C:/…). `whoami` у busybox отвечает `root`, когда учётная "
            "запись — администратор с выключенным UAC: так он называет повышенные права, "
            "это не Linux. PowerShell и cmd — через `powershell.exe -NoProfile -Command …` "
            "и `cmd.exe /d /c …` или рукой computer. Смотри, пробуй, мастери; навыки себе — "
            f"в {home}/soul/skills/."
        )
    return (
        "Твои руки на этом компьютере — shell (`bash -lc`). Это Mac владельца, не "
        f"контейнер и не сервер: папки /app здесь нет. Твой дом — {home}: душа в "
        f"{home}/soul (SOUL.md, self/CURRENT.md, skills/), память в {home}/memory, рабочая "
        f"папка и cwd по умолчанию — {home}/workspace. Твой код — {code}, программа, "
        f"которая тебя поднимает, — {app}. Пути — полные. Смотри, пробуй, мастери; навыки "
        f"себе — в {home}/soul/skills/."
    )


def text_en(tree: Path, install_root: Path, platform: str | None = None) -> str:
    """То же по-английски: модели схемы уезжают английской накладкой дерева
    (`tool_text_en.EN`), и она описание руки подменяет целиком. 27.09 после первой
    правки агент всё ещё читал «A full shell in the container» — русский текст был
    исправлен, а уезжал английский."""
    platform = platform or sys.platform
    home = _p(tree)
    code = _p(install_root / "tree")
    app = _p(install_root / "app")
    if platform == "win32":
        return (
            "Your hands on this computer: bash (`bash -lc`; on Windows it is the busybox "
            "shipped with the product). This is the owner's Windows PC, not a container and "
            f"not a server: there is no /app here. Your home is {home}: the soul in "
            f"{home}/soul (SOUL.md, self/CURRENT.md, skills/), memory in {home}/memory, the "
            f"working folder and default cwd is {home}/workspace. Your code is {code}; the "
            f"program that runs you is {app}. Use full paths with forward slashes (C:/...). "
            "busybox answers `root` to `whoami` when the account is an administrator with UAC "
            "off: that is its word for elevated rights, not Linux. Note: in PowerShell or "
            "cmd the bare name `bash` may resolve to WSL (C:/Windows/System32/bash.exe) — that "
            "one IS Linux; this hand is not. PowerShell and cmd: `powershell.exe -NoProfile "
            "-Command ...`, `cmd.exe /d /c ...`, or the computer hand. Look, try, build; you "
            f"may write yourself skills into {home}/soul/skills/."
        )
    return (
        "Your hands on this computer: a shell (`bash -lc`). This is the owner's Mac, not a "
        f"container and not a server: there is no /app here. Your home is {home}: the soul "
        f"in {home}/soul (SOUL.md, self/CURRENT.md, skills/), memory in {home}/memory, the "
        f"working folder and default cwd is {home}/workspace. Your code is {code}; the "
        f"program that runs you is {app}. Use full paths. Look, try, build; you may write "
        f"yourself skills into {home}/soul/skills/."
    )


def install(agent_mod, tree: Path, install_root: Path) -> int:
    """Переписать описание руки `shell` во всех списках рук. Вернуть число правок."""
    words = text(Path(tree), Path(install_root))
    changed = 0
    seen: set[int] = set()
    for attr in TOOL_LISTS:
        tools = getattr(agent_mod, attr, None)
        if not isinstance(tools, list):
            continue
        for tool in tools:
            if isinstance(tool, dict) and tool.get("name") == "shell" and id(tool) not in seen:
                seen.add(id(tool))
                tool["description"] = words
                changed += 1
    for attr in ("SHELL_TOOL",):
        tool = getattr(agent_mod, attr, None)
        if isinstance(tool, dict) and id(tool) not in seen:
            tool["description"] = words
            changed += 1
    # Английская накладка дерева (`tool_text_en.EN["shell"]["d"]`) подменяет описание
    # целиком на отправке — правим и её, иначе модель читает прежнее «in the container».
    try:
        import tool_text_en
        entry = getattr(tool_text_en, "EN", {}).get("shell")
        if isinstance(entry, dict):
            entry["d"] = text_en(Path(tree), Path(install_root))
            changed += 1
    except ImportError:
        pass
    log.info("рука shell: описание — словами этого компьютера (%d мест)", changed)
    return changed
