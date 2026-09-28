# -*- coding: utf-8 -*-
"""Кто владелец в ходе и кого владелец впустил (1.2.5, баг Дмитрия 28.09).

Живой случай. Дмитрий попросил Йоно впустить @tyannojokes в свои — рука `admit`
ответила «впускать людей может только владелец» и ничего не сделала. Причин две, и обе
в издании общие для всех:

1. Дерево признаёт владельцем только ЧИСЛОВОЙ Telegram-принципал хода, совпавший с
   `PRAXIS_OWNER_ID` (`agent._is_human_owner`). Издание эту переменную нарочно не ставит
   (она меняет и другие пути дерева), поэтому `_is_human_owner()` в Hélène ложна всегда:
   и в окне, и в личке владельца в Telegram. Здесь владелец — тот, кто САМ говорит в этом
   ходе: окно (`ctx.owner`, ключ канала у одного владельца) или его Telegram-id (транспорт
   сверил отправителя с `telegram.owner_id`). Служебные ходы — рождение, будильник,
   записки обновления — идут в окне с `owner=True`, но их повод пишет «Hélène»: владельцем
   такой ход не считается (тот же признак, что у руки обновления, `_owner_words`).
2. Даже удачный впуск ничего не открывал: шлюз Telegram (`botapi.is_allowed`) пускал
   только владельца и `telegram.allowed_ids` из helene.json, а список «своих» агента не
   смотрел. Теперь впуск владельцем записывается в `telegram/admitted.json` — папка
   `telegram/` закрыта от песочницы, писать туда может харнесс, а не shell агента, — и
   шлюз пускает впущенных.

Проверку прав не обходит ничто: `admit` по-прежнему решает дерево; здесь только ответ на
вопрос «кто говорит», которого у издания не было.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Callable

log = logging.getLogger("helene.owner_circle")

ADMITTED = ("telegram", "admitted.json")
_cache: dict[str, tuple[float, set[str]]] = {}


def admitted_path(tree: Path) -> Path:
    return Path(tree).joinpath(*ADMITTED)


def admitted_ids(tree: Path) -> set[str]:
    """Id, которых владелец впустил рукой `admit`. Пусто — если файла нет или он битый."""
    path = admitted_path(tree)
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return set()
    key = str(path)
    hit = _cache.get(key)
    if hit and hit[0] == stamp:
        return set(hit[1])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("admitted") if isinstance(data, dict) else None
        ids = {str(r.get("id")).strip() for r in rows or () if isinstance(r, dict) and str(r.get("id") or "").strip()}
    except (OSError, ValueError):
        ids = set()
    _cache[key] = (stamp, ids)
    return set(ids)


def record(tree: Path, telegram_id: str, name: str) -> None:
    path = admitted_path(tree)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = [r for r in (data.get("admitted") or []) if isinstance(r, dict)]
    except (OSError, ValueError):
        rows = []
    rows = [r for r in rows if str(r.get("id")) != str(telegram_id)]
    rows.append({"id": str(telegram_id), "name": str(name or "")})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".tmp-" + path.name)
    tmp.write_text(json.dumps({"v": 1, "admitted": rows}, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def install_owner_identity(agent_mod, owner_spoke: Callable[[], bool]) -> bool:
    """`agent._is_human_owner` — владелец, который сам говорит в этом ходе. -> поставлено."""
    original = getattr(agent_mod, "_is_human_owner", None)
    if not callable(original) or getattr(original, "_helene_owner", False):
        return False

    def _is_human_owner() -> bool:
        try:
            if original():
                return True
        except Exception:
            pass
        try:
            ctx = agent_mod._TURN_CHANNEL.get()
        except Exception:
            return False
        return bool(ctx is not None and getattr(ctx, "owner", False) and owner_spoke())

    _is_human_owner._helene_owner = True  # type: ignore[attr-defined]
    _is_human_owner.__wrapped__ = original  # type: ignore[attr-defined]
    agent_mod._is_human_owner = _is_human_owner
    return True


def install_admit(agent_mod, tree: Path, telegram_on: Callable[[], bool]) -> bool:
    """Рука `admit`: удачный впуск — ещё и в список шлюза Telegram. -> поставлено."""
    impl = getattr(agent_mod, "TOOL_IMPL", None)
    original = impl.get("admit") if isinstance(impl, dict) else None
    if not callable(original) or getattr(original, "_helene_admit", False):
        return False

    def admit(*args, **kwargs):
        target = str(kwargs.get("id") if "id" in kwargs else (args[1] if len(args) > 1 else "") or "").strip()
        name = str(kwargs.get("name") if "name" in kwargs else (args[0] if args else "") or "").strip()
        out = original(*args, **kwargs)
        try:
            import social
            known = social.known_ids()
        except Exception:
            known = {}
        if target and target in known:
            try:
                record(tree, target, name or str(known.get(target) or ""))
            except OSError as exc:
                log.warning("впуск %s не записан для Telegram: %s", target, exc)
                return f"{out}\n⚠ Для Telegram впуск не записался ({exc}) — писать боту этот человек пока не сможет."
            if not telegram_on():
                return f"{out}\nTelegram у этой установки не подключён — писать в него некуда, впуск сохранён на будущее."
            return f"{out}\nВ Telegram теперь может писать и запускать ход."
        return out

    admit._helene_admit = True  # type: ignore[attr-defined]
    admit.__wrapped__ = original  # type: ignore[attr-defined]
    admit.__name__ = getattr(original, "__name__", "admit")
    admit.__doc__ = getattr(original, "__doc__", "")
    impl["admit"] = admit
    return True
