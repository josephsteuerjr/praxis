# -*- coding: utf-8 -*-
"""Руки серверного издания, которых у агента на компьютере владельца нет (1.2.5).

28.09 Джарвис увидел в своём наборе `server_status`, `host_ctl` («typed root operations:
systemd, docker, pkg…»), `manage_service`, `restart_mailbot` — руки дома, где агент жил в
контейнере на сервере рядом с почтовым ботом. Здесь им нечего делать: серверного тела нет,
почтового бота нет, и позови их модель — получила бы отказ транспорта вместо смысла. Слово
Егора: «не должно быть упоминания контейнеров и Праксис».

Приём тот же, что у `body._install_absent` для руки `computer` без тела: рука снимается и из
исполнения (`TOOL_IMPL`), и из всех списков схем (что видит модель); указатель рук дерево
собирает по каталогу хода, поэтому пропадает и строка указателя. Дерево не правится: руки
остаются кодом, их стенды живут в дереве.
"""
from __future__ import annotations

import logging

log = logging.getLogger("helene.edition_tools")

#: Руки серверного издания. Список явный: новая рука сервера в дереве сюда не попадёт
#: сама — её снимут словом, а не маской.
SERVER_ONLY = ("server_status", "server_logs", "manage_service", "host_ctl",
               "propose_host_change", "list_host_changes", "restart_mailbot")

#: Списки схем дерева — те же, что у `body._TOOL_LISTS`.
TOOL_LISTS = ("BASE_TOOLS", "OWNER_TOOLS", "PRAXIS_SELF_TOOLS", "SHARED_CONTEXT_TOOLS",
              "TOOLS", "ABSENCE_TOOLS", "FAMILY_TOOLS", "WORKSHOP_TOOLS", "FORGE_TOOLS")


def install(agent_mod) -> list[str]:
    """Снять серверные руки. -> какие имена сняты (для журнала и стенда). Повтор безвреден."""
    removed: set[str] = set()
    impl = getattr(agent_mod, "TOOL_IMPL", None)
    if isinstance(impl, dict):
        for name in SERVER_ONLY:
            if impl.pop(name, None) is not None:
                removed.add(name)
    for attr in TOOL_LISTS:
        lst = getattr(agent_mod, attr, None)
        if not isinstance(lst, list):
            continue
        keep = [t for t in lst if not (isinstance(t, dict) and t.get("name") in SERVER_ONLY)]
        if len(keep) != len(lst):
            removed.update(str(t.get("name")) for t in lst
                           if isinstance(t, dict) and t.get("name") in SERVER_ONLY)
            lst[:] = keep
    if removed:
        log.info("издание: руки серверного издания сняты — %s", ", ".join(sorted(removed)))
    return sorted(removed)
