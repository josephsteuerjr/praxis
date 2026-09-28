# -*- coding: utf-8 -*-
"""Мозг, закреплённый владельцем (1.2.4).

28.09, стенд Дмитрия К (через Arête): его агент Йоно трижды сам переключал голос на
gpt-6-sol, записывая в «зачем» «Owner request», хотя Дмитрий четыре раза говорил не
переключать; вернуть рабочий мозг мог только он, правкой llm.json. Слово Егора:
«закрепи модель тогда уж».

Закрепление — рычаг ВЛАДЕЛЬЦА (`helene.json` → `model.pinned`, галочка в Настройках →
Модель): пока оно стоит, смена модели рукой агента (`switch_brain` switch / profile)
отказывает словами и оставляет строку в её журнале — с её же «зачем», чтобы владелец
видел попытку и её причину. Ступень рассуждения и выбор слота подписки остаются её —
закреплена модель, а не мозг целиком. Дерево не правим: оборачиваем `brain.switch` и
`brain.apply_profile` после импорта, как `body.install` оборачивает руку `computer`.
Галочку снимает только владелец; читается она на каждом вызове — снял, и рука снова
работает, без перезапуска.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("helene.brain_pin")

WORDS = ("Модель закреплена владельцем: её выбирает он — Настройки → Модель, галочка "
         "«Закрепить модель». Сменить её сам я не могу; если нужна другая — скажу ему "
         "словами, зачем, и он решит.")


def pinned(config_path: Path | None) -> bool:
    """Стоит ли галочка владельца. Не прочиталось — не закреплено (как было до 1.2.4)."""
    if config_path is None:
        return False
    try:
        cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    model = cfg.get("model") if isinstance(cfg, dict) else None
    return bool(isinstance(model, dict) and model.get("pinned"))


def _refuse(brain, what: str, why: str) -> dict:
    note = f"смена мозга ({what}) отклонена: модель закреплена владельцем · зачем: {(why or '—')[:200]}"
    try:
        brain._journal(note)
    except Exception:
        log.debug("строка о закреплении не легла в журнал", exc_info=True)
    log.info("мозг: %s", note)
    return {"ok": False, "error": WORDS, "pinned": True}


def install(config_path: Path | None) -> bool:
    """Обернуть смену модели агентом. -> True, если обёртка стоит."""
    try:
        import brain
    except ImportError:
        log.warning("мозг: модуль brain не загрузился — закрепление модели не действует")
        return False
    switch = getattr(brain, "switch", None)
    profile = getattr(brain, "apply_profile", None)
    if not callable(switch) or getattr(switch, "_helene_pin", False):
        return bool(getattr(switch, "_helene_pin", False))

    def pinned_switch(role: str, model: str, *, why: str = "", by: str = "praxis") -> dict:
        if pinned(config_path):
            return _refuse(brain, f"{role} → {model}", why)
        return switch(role, model, why=why, by=by)

    pinned_switch._helene_pin = True  # type: ignore[attr-defined]
    pinned_switch.__doc__ = switch.__doc__
    brain.switch = pinned_switch
    if callable(profile):
        def pinned_profile(name: str, *, why: str = "", by: str = "praxis") -> dict:
            if pinned(config_path):
                return _refuse(brain, f"профиль {name}", why)
            return profile(name, why=why, by=by)

        pinned_profile._helene_pin = True  # type: ignore[attr-defined]
        pinned_profile.__doc__ = profile.__doc__
        brain.apply_profile = pinned_profile
    log.info("мозг: закрепление модели владельцем подключено (сейчас %s)",
             "закреплена" if pinned(config_path) else "не закреплена")
    return True
