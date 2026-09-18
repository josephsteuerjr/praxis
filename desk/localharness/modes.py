# -*- coding: utf-8 -*-
"""Ограда рук и служба — ДВА НЕЗАВИСИМЫХ ВОПРОСА, а не один список из трёх.

⚠⚠ ЭТО ГЛАВНОЕ ИСПРАВЛЕНИЕ ЭТОГО ФАЙЛА. Первая редакция знала три режима
(`sandbox | interactive | service`) одним списком, и из этого следовал P0:
у владельца, поставившего службу И песочницу, миграция выводила `service`, а
`service` означал «ограды нет» (`want_sandbox = name == "sandbox"`). Ограда
снималась МОЛЧА, при том что владелец её выбирал. Склеены были два разных
измерения; здесь они разведены.

**1. Режим — про ограду рук.** Два значения, больше нет:

  * **sandbox** — агент заперт в своей папке: файловые руки и `shell` дальше
    дома не идут, `shell` живёт в AppContainer. Наружу — только смонтированное
    (`sandbox.mounts`, разбор в `fence.py`).
    ⚠ Про окна здесь стояло «Окна при этом ДОСТУПНЫ: рука окон (UIA) в контейнер
    не попадает». Первая половина верна — ограда до руки окон не достаёт, — а
    вывод неверен: самой руки в поставке НЕТ. `computer` из дерева ходит в тело
    Праксис по HTTP (body_client → praxis-bridge), а у Hélène не едет ни мост, ни
    тело. Проверено по коду 04.09 (задача C3), разбор — в шапке `fence.py`.
  * **interactive** — файловая система и процессы под ПОЛЬЗОВАТЕЛЕМ. Не «вся
    машина»: у ограниченной учётки агент тоже ограничен. Права администратора
    запрашиваются по необходимости, окном UAC.

**2. Служба — опция ПОВЕРХ любого режима.** Не режим и не третий пункт списка:
ставится один раз под администратором и даёт защиту папки установки (ACL),
жизнь без окна и брокера прав. Ограду она не снимает и не включает — режим
выбирается отдельно и действует в точности так же. Стоит служба или нет —
спрашиваем у самой Windows (`service_installed`), а не у конфига.

Внутри опции — две РАЗНЫЕ галочки, которые до этой правки были одним ключом:

  * `service.session0` — **доступ агента к правам системы**. Харнесс
    поднимается ПОД службой (нулевая сессия: права системы, рабочего стола не
    видно) и брокер выполняет для агента команды правами СИСТЕМЫ. Умолчание —
    выключено, с оговоркой (`SESSION0_WARNING`).
  * `service.firewall` — **нужна ли службе привилегия на правило брандмауэра**.
    Одно узкое действие продукта по кнопке владельца («Телефон», QR): открыть
    порт трубы. Прав системы агенту оно не даёт. Умолчание — ВКЛЮЧЕНО, потому
    что с выключенным кнопка QR под службой была мертва: `shell` в режиме
    службы шёл в брокера, а брокер отказывал, пока не включена нулевая сессия —
    то есть узкое действие продукта требовало отдать агенту всю машину.

⚠⚠ ПОЧЕМУ КЛЮЧ РЕЖИМА НЕ НАЗЫВАЕТСЯ `mode`. В `helene.json` ключ `mode` УЖЕ
ЗАНЯТ и означает совсем другое — где живёт харнесс: `"local"` или `"remote"`.
Его читают два места на Rust, и оба на чужом значении молча выключают продукт:

    shell/src/main.rs:2134   match cfg.get("mode")… { "local" => подъём детей, … }
    svc/src/main.rs:520      if plan.mode != "local" { «служба не нужна» }

То есть `"mode": "sandbox"` — это окно, которое не поднимает ни трубу, ни
руннер, и служба, которая отказывается стартовать. Поэтому режим живёт в
СВОЁМ ключе `agent_mode`, а `mode` остаётся тем, чем был. Если кто-то всё же
запишет режим в `mode` (документ задачи предлагал именно это имя), мы читаем
его оттуда, ГРОМКО пишем в журнал и при первой же записи чиним файл: режим
уезжает в `agent_mode`, а `mode` возвращается в `"local"`.

Ручки, в которые раскладывается режим:

    agent_mode          sandbox | interactive   (значение "service" — старое,
                        читается ради совместимости и чинится при записи)
    sandbox.enabled     true только в режиме sandbox
    sandbox.network     сеть контейнера — остаётся выбором владельца
    service.session0    доступ агента к правам системы; умолчание false
    service.firewall    служба ставит правило брандмауэра; умолчание true
    service.broker      выключатель брокера целиком (читает svc, не мы)
    installed.service   след установщика: ставилась ли служба (подсказка, когда
                        SCM не ответил)

Расхождение ручки с режимом — не тихая правка: побеждает режим, а строка о
расхождении уходит и в журнал, и в анатомию, и в /api/state.

Модуль намеренно без зависимостей, кроме стандартной библиотеки: его импортирует
и руннер (`localharness/`), и читатели трубы (`deskd/readers.py`).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("helene.modes")

#: Ограда рук — и только она. Порядок: от самого запертого к самому свободному,
#: он же порядок карточек на экранах. Службы здесь нет и быть не должно: она
#: измерение поверх любого из этих двух (см. докстринг модуля).
MODES: tuple[str, ...] = ("sandbox", "interactive")

#: Что писала в `agent_mode` первая волна, пока служба считалась режимом.
#: Такой конфиг читается — иначе выбор владельца потерялся бы молча, — но
#: означает он «служба, а ограду вывести отдельно», а не «ограды нет».
LEGACY_SERVICE = "service"

#: Всё, что мы согласны прочитать из ключа режима.
READABLE: tuple[str, ...] = MODES + (LEGACY_SERVICE,)

#: Чем становится конфиг без единого следа режима. Не «песочница»: миграция
#: (`infer`) смотрит на то, что в файле УЖЕ есть, и до умолчания доходит только
#: пустой конфиг.
DEFAULT_MODE = "interactive"

#: Ключ режима в helene.json. См. докстринг: `mode` занят под local|remote.
KEY = "agent_mode"

#: Имя службы в SCM (svc/src/main.rs::SERVICE_NAME). Латиницей.
SERVICE_NAME = "Helene"

#: Умолчание галочки «служба ставит правило брандмауэра». True: это узкое
#: действие продукта по кнопке владельца, и с False кнопка «Телефон» под
#: службой мертва.
FIREWALL_DEFAULT = True

TITLES = {
    "sandbox": "Песочница",
    "interactive": "Интерактивный",
}

#: Короткая честная строка режима — одна на окно, телефон и анатомию.
#: Пишется на «ты», без пафоса; в песочнице прямо сказано и про запертые файлы,
#: и про окна, потому что половина правды здесь читается как обман.
#:
#: ⚠ 10.09 сюда дописан Forge. «Файлы и команды дальше дома не идут» было
#: неправдой сразу в двух местах: `run`/`run_tests`/`pip_install` шли мимо
#: ограды (починено — `fence._shim_workshop`), а `coding_*` работает в worktree
#: ЗАДАЧИ, который лежит где угодно на диске, и это не чинится вовсе. Полный
#: список — `fence.hands_report`, экран «Система»; здесь только та часть, ради
#: которой владелец не пойдёт никуда смотреть.
TEXTS = {
    "sandbox": ("Агент заперт в своей папке: файлы и команды дальше дома не "
                "идут, наружу — только те папки, что ты смонтировал. Окна и "
                "рабочий стол — отдельная опция «Управление компьютером»: "
                "тело для них живёт снаружи ограды. Снаружи и Forge: он "
                "работает в worktree задачи, а тот лежит там, куда ты его "
                "завёл. Поимённо, какая рука накрыта, а какая нет, — на экране "
                "«Система»."),
    "interactive": ("Агент работает с твоими правами: файлы и процессы — те же, "
                    "что доступны тебе самому, не больше. Файловые руки видят "
                    "то же, что и shell, монтировать ничего не нужно. Если "
                    "учётка ограничена, агент ограничен так же. Права "
                    "администратора он просит отдельно, окном Windows."),
}

# --------------------------------------------------------------------------- #
#  Служба: тексты опции, а не режима
# --------------------------------------------------------------------------- #

SERVICE_TITLE = "Поставить службу Windows"

#: ⚠ Здесь было ДВЕ неправды, обе поправлены 04.09 по коду службы:
#:   1. «агент живёт, пока включён компьютер» — нет: без нулевой сессии харнесс
#:      поднимается В СЕССИИ ВЛАДЕЛЬЦА (`svc::supervise_session` ждёт активную
#:      сессию и запускает задачу в ней), то есть живёт, пока ты в системе;
#:   2. «папка установки закрыта на запись всем, кроме системы» — нет:
#:      администраторам запись выдаётся НАМЕРЕННО, иначе не встанет обновление
#:      (`svc/src/main.rs::harden_args`: `*S-1-5-32-544:F` рядом с `*S-1-5-18:F`,
#:      пользователям и прошедшим проверку — только `RX`).
SERVICE_TEXT = ("Ставится один раз, под администратором. Код агента поднимается "
                "сам, как только ты вошёл в систему, — окно для этого держать "
                "не нужно; из системы вышел — код агента закрылся вместе с "
                "сессией. Папку установки служба закрывает: обычным учётным "
                "записям — читать и запускать, запись остаётся у системы и у "
                "администраторов (без неё не встанет обновление). Ограду это "
                "не меняет — режим ты выбираешь отдельно, и он работает так же.")

SESSION0_TITLE = "Разрешить агенту нулевую сессию"

SESSION0_TEXT = ("Код агента поднимается под самой службой: права системы, "
                 "рабочего стола не видно, живёт и когда ты вышел из системы. "
                 "Тем же разрешением брокер выполняет для агента команды "
                 "правами системы. По умолчанию выключено.")

#: Оговорка к галочке нулевой сессии. Владелец должен прочитать её ДО, а не
#: узнать после. ⚠ Имя константы разбирает сборка установщика
#: (setup/ui/vite.config.ts) — переименование ломает её сборку.
SESSION0_WARNING = ("Нулевая сессия разрешена: агент получает права системы и "
                    "при этом не видит рабочего стола. Слабая модель может не "
                    "понять, что делает.")

FIREWALL_TITLE = "Правило брандмауэра ставит служба"

FIREWALL_TEXT = ("Кнопка «Телефон» открывает порт через службу, без окна "
                 "Windows про права. Это одно узкое действие продукта по твоей "
                 "кнопке — прав системы агенту оно не даёт и с нулевой сессией "
                 "не связано. Выключишь — окно будет спрашивать права само.")

# --------------------------------------------------------------------------- #
#  Управление компьютером: опция поверх любого режима, не режим
# --------------------------------------------------------------------------- #

#: Ключ блока в helene.json: `computer.enabled`, `computer.scopes`, `computer.port`.
#: Читает его `localharness/body.py`; окно пишет через `keepBlock`.
COMPUTER_KEY = "computer"

COMPUTER_TITLE = "Управление компьютером"

#: ⚠ Имя константы разбирает сборка установщика (setup/ui/vite.config.ts).
COMPUTER_TEXT = ("Рука `computer`: окна, экран, клавиатура и мышь, файлы и "
                 "процессы на этой машине. Работает через отдельное тело "
                 "(helene-body.exe), которое код агента поднимает рядом с собой в "
                 "твоей сессии — снаружи ограды, поэтому в песочнице оно тоже "
                 "работает. Что именно разрешено, решают четыре права ниже; "
                 "от режима опция не зависит.")

#: Оговорка — та же, что у нулевой сессии: владелец читает её ДО включения.
COMPUTER_WARNING = ("Опция для энтузиастов, не для слабых моделей: агент водит "
                    "твоей мышью и клавиатурой по-настоящему и видит экран. "
                    "Слабая модель может не понять, что делает.")

#: Умолчание опции — выключено: включают осознанно, прочитав оговорку.
COMPUTER_DEFAULT = False

#: Порт моста по умолчанию (body.DEFAULT_PORT). Не 9473: там тело Праксис.
COMPUTER_PORT_DEFAULT = 9480

#: Четыре права дерева (`computer_access.SCOPES`) — в порядке показа. Ключи —
#: те же строки, что проверяет рука: `_COMPUTER_ACTION_SCOPES` в tree/agent.py.
COMPUTER_SCOPES: tuple[str, ...] = ("computer.read", "computer.files",
                                    "computer.process", "computer.apps")

COMPUTER_SCOPE_TITLES = {
    "computer.read": "Смотреть",
    "computer.files": "Файлы",
    "computer.process": "Процессы",
    "computer.apps": "Окна и ввод",
}

COMPUTER_SCOPE_TEXTS = {
    "computer.read": ("Состояние тела, инвентарь машины, список папок и "
                      "свойства файлов. Ничего не меняет."),
    "computer.files": ("Читать, писать и пересылать файлы по абсолютному пути — "
                       "любые, что доступны твоей учётке, мимо ограды."),
    "computer.process": ("Запускать команды PowerShell в твоей сессии и следить "
                         "за ними. Тоже мимо ограды."),
    "computer.apps": ("Список окон, активация, клавиатура и мышь, снимки "
                      "экрана, чтение окна как текста, буфер обмена."),
}


def computer_state(cfg: dict) -> dict:
    """Что записано владельцем: включено ли, какие права, какой порт.

    Нет ключа `scopes` — все четыре: включил опцию — получил руку целиком,
    сузить можно галочками. Живое «тело подключено» здесь НЕ решается: это
    знает только раннер (`body.py`, снимок `memory/.state/body.json`).
    """
    raw = cfg.get(COMPUTER_KEY)
    block = dict(raw) if isinstance(raw, dict) else {}
    scopes_raw = block.get("scopes")
    if scopes_raw is None:
        scopes = list(COMPUTER_SCOPES)
    elif isinstance(scopes_raw, (list, tuple)):
        chosen = {str(x) for x in scopes_raw}
        scopes = [s for s in COMPUTER_SCOPES if s in chosen]
    else:
        scopes = []
    try:
        port = int(block.get("port") or COMPUTER_PORT_DEFAULT)
    except (TypeError, ValueError):
        port = COMPUTER_PORT_DEFAULT
    return {
        "enabled": bool(block.get("enabled", COMPUTER_DEFAULT)),
        "scopes": scopes,
        "port": port if 1024 <= port <= 65535 else COMPUTER_PORT_DEFAULT,
        "explicit": isinstance(raw, dict) and "enabled" in raw,
    }


def computer_option() -> dict:
    """Опция управления компьютером с четырьмя правами — для экранов."""
    return {
        "name": "computer",
        "title": COMPUTER_TITLE,
        "text": COMPUTER_TEXT,
        "warning": COMPUTER_WARNING,
        "default": COMPUTER_DEFAULT,
        "needs_admin": False,
        "scopes": [
            {"key": key, "title": COMPUTER_SCOPE_TITLES[key],
             "text": COMPUTER_SCOPE_TEXTS[key]}
            for key in COMPUTER_SCOPES
        ],
    }


# --------------------------------------------------------------------------- #
#  Чтение: что записано в файле
# --------------------------------------------------------------------------- #

def _clean(value) -> str:
    return str(value or "").strip().lower()


def _block(cfg: dict, name: str) -> dict:
    block = cfg.get(name)
    return dict(block) if isinstance(block, dict) else {}


def _stated_raw(cfg: dict) -> tuple[str, str]:
    """Что ЛЕЖИТ в ключе режима, включая старое `"service"`, и где найдено.

    Три формы, потому что конфиг правят руками и его писали разные авторы:
      * `"agent_mode": "sandbox"` — канон;
      * `"agent_mode": {"name": "sandbox", "session0": true}` — тоже читаем;
      * `"mode": "sandbox"` — ошибка (см. докстринг модуля), читаем и чиним.
    """
    raw = cfg.get(KEY)
    if isinstance(raw, dict):
        name = _clean(raw.get("name") or raw.get("mode"))
        if name in READABLE:
            return name, f"{KEY}.name"
    elif _clean(raw) in READABLE:
        return _clean(raw), KEY
    # `mode` — это местожительство харнесса (local|remote). Режим здесь означает
    # сломанный продукт: окно не поднимет детей, служба не стартует.
    hijacked = _clean(cfg.get("mode"))
    if hijacked in READABLE:
        return hijacked, "mode"
    return "", ""


def stated(cfg: dict) -> tuple[str, str]:
    """ОГРАДА, записанная в конфиге, и где она найдена. ("", "") — не записана.

    Старое `"service"` оградой не является: это была склейка двух измерений.
    Такой конфиг здесь читается как «ограда не записана» — и выводится
    миграцией из `sandbox.enabled`, а не превращается в «ограды нет».
    Сам факт службы достаётся отдельно: `legacy_service` и `service_installed`.
    """
    name, where = _stated_raw(cfg)
    return ("", "") if name == LEGACY_SERVICE else (name, where)


def legacy_service(cfg: dict) -> tuple[bool, str]:
    """Лежит ли в ключе режима старое `"service"`, и в каком именно ключе."""
    name, where = _stated_raw(cfg)
    return (True, where) if name == LEGACY_SERVICE else (False, "")


def session0(cfg: dict) -> bool:
    """Доступ агента к правам системы, как его ЗАПИСАЛИ. Умолчание — false.

    Канон — `service.session0`: ровно оттуда её читает служба
    (`svc::load_plan`). Форму `agent_mode.session0` принимаем, чтобы правка
    руками по документу задачи не пропадала молча, но при записи она переезжает
    в канон.
    """
    svc = _block(cfg, "service")
    if "session0" in svc:
        return bool(svc.get("session0"))
    raw = cfg.get(KEY)
    if isinstance(raw, dict) and "session0" in raw:
        return bool(raw.get("session0"))
    return False


def firewall(cfg: dict) -> bool:
    """Ставит ли правило брандмауэра сама служба. Умолчание — ДА.

    Отдельный от `session0` ключ намеренно: до этой правки обе двери держал
    один `service.session0`, и кнопка «Телефон» под службой была мертва, пока
    владелец не отдаст агенту права системы. Это два несвязанных вопроса.
    """
    svc = _block(cfg, "service")
    if "firewall" in svc:
        return bool(svc.get("firewall"))
    return FIREWALL_DEFAULT


# --------------------------------------------------------------------------- #
#  Есть ли служба на самом деле
# --------------------------------------------------------------------------- #

_SCM_CACHE: dict = {"at": 0.0, "value": None}
_SCM_TTL = 20.0


def service_installed(cfg: dict | None = None, *, probe: bool = True) -> bool | None:
    """Стоит ли служба. None — «спросить не у кого» (не Windows, отказ SCM).

    Спрашиваем у самой Windows, а не у конфига: `installed.service` — это след
    установщика, и он врёт после ручного снятия службы. Конфиг остаётся
    подсказкой на случай, если SCM не ответил.

    Через ctypes, а не `sc.exe`: подпроцесс на старте руннера и на каждом
    опросе состояния — это и задержка, и лишнее окно консоли. Прав здесь не
    нужно: SC_MANAGER_CONNECT + SERVICE_QUERY_STATUS даются любому вошедшему.
    """
    hint = None
    if isinstance(cfg, dict):
        installed = _block(cfg, "installed")
        if "service" in installed:
            hint = bool(installed.get("service"))
    if not probe or os.name != "nt":
        return hint
    now = time.time()
    if _SCM_CACHE["value"] is not None and now - _SCM_CACHE["at"] < _SCM_TTL:
        return bool(_SCM_CACHE["value"])
    found = _scm_has_service(SERVICE_NAME)
    if found is None:
        return hint
    _SCM_CACHE.update({"at": now, "value": found})
    return found


def _scm_has_service(name: str) -> bool | None:
    try:
        import ctypes
        import ctypes.wintypes as wt
    except Exception:
        return None
    try:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.OpenSCManagerW.restype = wt.HANDLE
        advapi.OpenSCManagerW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.DWORD]
        advapi.OpenServiceW.restype = wt.HANDLE
        advapi.OpenServiceW.argtypes = [wt.HANDLE, wt.LPCWSTR, wt.DWORD]
        advapi.CloseServiceHandle.argtypes = [wt.HANDLE]
        scm = advapi.OpenSCManagerW(None, None, 0x0001)      # SC_MANAGER_CONNECT
        if not scm:
            return None
        try:
            svc = advapi.OpenServiceW(scm, name, 0x0004)     # SERVICE_QUERY_STATUS
            if svc:
                advapi.CloseServiceHandle(svc)
                return True
            # 1060 = ERROR_SERVICE_DOES_NOT_EXIST — это ОТВЕТ «нет», а не отказ.
            return False if ctypes.get_last_error() == 1060 else None
        finally:
            advapi.CloseServiceHandle(scm)
    except Exception:
        log.debug("SCM не опросился", exc_info=True)
        return None


# --------------------------------------------------------------------------- #
#  Миграция: старый конфиг без режима
# --------------------------------------------------------------------------- #

def infer(cfg: dict) -> tuple[str, str]:
    """ОГРАДА старого конфига и ОДНА строка о том, из чего она выведена.

    ⚠⚠ ЗДЕСЬ БЫЛ P0. Раньше первой строкой стояло «служба стоит → service», и
    установка со службой И включённой оградой после миграции оставалась БЕЗ
    ограды: `service` означал `want_sandbox = False`. Служба ограду не снимает
    и никогда не снимала — она вообще про другое (см. докстринг модуля).
    Поэтому службы здесь нет ни в одном виде и параметра `installed` у функции
    больше нет: ограда выводится ИСКЛЮЧИТЕЛЬНО из `sandbox.enabled`.

    Отсутствующий блок `sandbox` — это НЕ «иначе»: ограда в `fence.install`
    включена по умолчанию, значит такой конфиг сегодня живёт песочницей, и
    миграция обязана описать то, что есть, а не то, что удобнее.
    """
    sandbox = cfg.get("sandbox")
    if isinstance(sandbox, dict) and "enabled" in sandbox:
        if bool(sandbox.get("enabled")):
            return "sandbox", "sandbox.enabled = true"
        return "interactive", "sandbox.enabled = false"
    if sandbox is None:
        return "sandbox", "блока sandbox нет, а ограда по умолчанию включена"
    return DEFAULT_MODE, "в конфиге нет ни одного следа ограды"


# --------------------------------------------------------------------------- #
#  Разбор и раскладка
# --------------------------------------------------------------------------- #

def resolve(cfg: dict, *, installed: bool | None = None) -> dict:
    """Полная картина по конфигу: какая ограда И что со службой. Не меняет ничего.

    Возвращает:
      name          — режим-ограда (всегда один из MODES);
      title, text   — как о нём говорить владельцу;
      sandbox       — какой должна быть ограда в этом режиме;
      explicit      — режим был записан явно (иначе он выведен миграцией);
      source        — откуда взят ("ключ `agent_mode`", "миграция: …");
      needs_write   — файл стоит починить: режима нет, он лежит не в том ключе,
                      в нём старое "service" или `sandbox.enabled` разошлась;

      service_installed — стоит ли служба (None — не спросили);
      service_title, service_text — тексты опции службы;
      session0      — эффективный доступ агента к правам системы (без службы
                      всегда False);
      session0_set  — как он записан в файле;
      firewall      — эффективная привилегия службы на правило брандмауэра;
      firewall_set  — как она записана в файле;
      legacy_service — в ключе режима лежит старое "service";
      notes         — расхождения ручек с режимом, человеческими словами.
    """
    if installed is None:
        installed = service_installed(cfg)
    raw_name, where = _stated_raw(cfg)
    legacy = raw_name == LEGACY_SERVICE
    name = "" if legacy else raw_name
    explicit = bool(name)
    why = ""
    if explicit:
        source = ("ключ `mode`, не на своём месте" if where == "mode"
                  else f"ключ `{where}`")
    else:
        name, why = infer(cfg)
        source = f"миграция: {why}"

    want_sandbox = name == "sandbox"
    stored_session0 = session0(cfg)
    stored_firewall = firewall(cfg)
    # Без службы обе галочки — слова в файле: некому их исполнить. `None`
    # (SCM не ответил) считаем «может стоять»: промолчать про права системы
    # хуже, чем сказать лишнее.
    effective_session0 = stored_session0 and installed is not False
    effective_firewall = stored_firewall and installed is not False

    # Совпадает ли ручка с режимом ПРЯМО В ФАЙЛЕ. Мало выставить её в памяти:
    # экран настроек и владелец с блокнотом читают файл, и вечно расходящаяся
    # ручка над выбранным режимом — это и есть вторая правда.
    sandbox_block = cfg.get("sandbox")
    sandbox_agrees = (isinstance(sandbox_block, dict) and "enabled" in sandbox_block
                      and bool(sandbox_block.get("enabled")) == want_sandbox)

    notes: list[str] = []
    if where == "mode":
        notes.append(
            "режим записан в ключ `mode`, который занят под местожительство "
            "кода агента (local|remote): в таком файле окно не поднимает код агента, "
            f"а служба не стартует. Чиню — режим уезжает в `{KEY}`.")
    if legacy:
        notes.append(
            "в конфиге записан режим «служба» — так писала первая редакция, "
            "пока служба считалась режимом. Служба режимом не является: она "
            f"опция поверх ограды, и ограду не снимает. Ограду вывожу отдельно "
            f"({why}), саму службу спрашиваю у Windows. Чиню при записи.")
    if isinstance(sandbox_block, dict) and "enabled" in sandbox_block \
            and not sandbox_agrees:
        notes.append(
            f"sandbox.enabled = {str(bool(sandbox_block.get('enabled'))).lower()}, "
            f"а режим «{TITLES[name]}» требует "
            f"{str(want_sandbox).lower()} — побеждает режим")
    if stored_session0 and installed is False:
        notes.append("service.session0 = true, но служба не установлена — "
                     "нулевую сессию некому дать, галочка не действует. "
                     "Служба ставится в Настройках, карточка «Режим»")
    # Про выключенную привилегию говорим, только когда служба ЕСТЬ: без службы
    # правило и так ставит окно, и строка об этом была бы не расхождением, а
    # шумом на каждом старте у каждого, кто службу не ставил.
    if not stored_firewall and installed is not False:
        notes.append("service.firewall = false — правило брандмауэра служба не "
                     "ставит: кнопка «Телефон» спросит права окном Windows")

    return {
        "name": name,
        "title": TITLES[name],
        "text": TEXTS[name],
        "sandbox": want_sandbox,
        "explicit": explicit,
        "source": source,
        "needs_write": (not explicit) or where == "mode" or not sandbox_agrees,
        "service_installed": installed,
        "service_title": SERVICE_TITLE,
        "service_text": SERVICE_TEXT,
        "session0": effective_session0,
        "session0_set": stored_session0,
        "session0_warning": SESSION0_WARNING if effective_session0 else "",
        "firewall": effective_firewall,
        "firewall_set": stored_firewall,
        "legacy_service": legacy,
        "notes": notes,
    }


def apply(cfg: dict, *, installed: bool | None = None) -> dict:
    """Разложить режим по ручкам ПРЯМО В cfg и сказать, что вышло.

    Вторую правду не заводим: `fence.install` продолжает читать
    `sandbox.enabled`, служба — `service.session0` и `service.firewall`. Режим
    просто выставляет их ПЕРЕД тем, как их прочтут, и называет вслух каждое
    расхождение.

    Службу здесь не трогаем ни в каком виде: ни `installed.service` (это след
    установщика, соврать в нём хуже, чем промолчать), ни SCM. Старое
    `agent_mode: "service"` при записи заменяется выведенной оградой — и это
    ровно то место, где раньше терялась песочница.
    """
    picture = resolve(cfg, installed=installed)
    cfg[KEY] = picture["name"]
    if _clean(cfg.get("mode")) in READABLE:
        # Освобождаем занятый ключ: без этого окно не поднимет ни трубу, ни
        # руннер (см. докстринг модуля). "local" — то, что здесь и было.
        cfg["mode"] = "local"
    sandbox = cfg.get("sandbox")
    if not isinstance(sandbox, dict):
        sandbox = {}
    sandbox["enabled"] = picture["sandbox"]
    sandbox.setdefault("network", True)
    cfg["sandbox"] = sandbox
    service = cfg.get("service")
    if not isinstance(service, dict):
        service = {}
    # session0 пишем всегда: форма `agent_mode.session0` должна переехать в
    # канон, иначе служба продолжит читать своё. firewall — только умолчанием:
    # выбор владельца здесь уже записан, а отсутствие ключа означает «да».
    service["session0"] = picture["session0_set"]
    service.setdefault("firewall", picture["firewall_set"])
    cfg["service"] = service
    return picture


def journal(picture: dict, *, where: str = "") -> None:
    """Записать режим, службу и все расхождения в лог. Молчать здесь нельзя."""
    tail = f" ({where})" if where else ""
    log.info("режим: %s%s — источник: %s", picture["title"], tail,
             picture["source"])
    installed = picture.get("service_installed")
    log.info("служба: %s · нулевая сессия: %s · правило брандмауэра: %s",
             {True: "установлена", False: "не установлена"}.get(installed, "не спросили"),
             "разрешена" if picture.get("session0") else "запрещена",
             "ставит служба" if picture.get("firewall") else "ставит окно")
    if picture.get("session0"):
        log.warning("режим: %s", SESSION0_WARNING)
    for note in picture["notes"]:
        log.warning("режим: %s", note)


# --------------------------------------------------------------------------- #
#  Запись: явный режим в файле
# --------------------------------------------------------------------------- #

def read_config_text(path: Path) -> str:
    """helene.json так, как его сохранил РЕДАКТОР ВЛАДЕЛЬЦА.

    Копия правила из `boot.read_config_text` / `shell::decode_config` /
    `svc::decode_config`: файл зовут править руками, а Блокнот и PowerShell 5.1
    пишут UTF-8 с меткой или UTF-16. Здесь она своя, чтобы модуль оставался
    без зависимостей и его могли импортировать читатели трубы.
    """
    raw = Path(path).read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if b"\x00" in raw[:4096]:
        return raw.decode("utf-16-le" if raw[1:2] == b"\x00" else "utf-16-be")
    return raw.decode("utf-8")


def ensure_written(path: Path, *, installed: bool | None = None) -> dict:
    """Записать режим в helene.json явно, если его там ещё нет.

    Это и есть «записать при первом сохранении»: миграция выводит режим из того,
    что в файле есть, а дальше он там ЛЕЖИТ — и владелец видит свой выбор
    словом, а не догадывается о нём по `sandbox.enabled`.

    Файл перечитывается прямо перед записью и меняется точечно (режим, ограда,
    галочки): владелец мог в эту же секунду сохранить настройки из окна, и
    выливать поверх него разобранный полминуты назад конфиг нельзя. Запись
    атомарная — окно и служба читают этот файл на ходу.

    Возвращает ту же картину, что `resolve`, плюс `written` (bool) и `error`.
    """
    path = Path(path)
    try:
        cfg = json.loads(read_config_text(path))
        if not isinstance(cfg, dict):
            raise ValueError("верхний уровень должен быть объектом {…}")
    except (OSError, ValueError) as exc:
        picture = resolve({}, installed=installed)
        picture.update({"written": False, "error": f"{path} не прочитан: {exc}"})
        log.warning("режим: %s", picture["error"])
        return picture
    picture = resolve(cfg, installed=installed)
    if not picture["needs_write"]:
        picture["written"] = False
        return picture
    apply(cfg, installed=installed)
    try:
        tmp = path.with_name(".tmp-" + path.name)
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as exc:
        picture.update({"written": False, "error": f"{path} не записан: {exc}"})
        log.warning("режим: не записался в %s: %s — работаю по выведенному "
                    "режиму «%s»", path, exc, picture["title"])
        return picture
    picture["written"] = True
    log.info("режим: записан в %s явно — %s (%s)", path.name, picture["name"],
             picture["source"])
    return picture


def describe(picture: dict) -> dict:
    """Срез картины для окна и телефона: только то, что показывают человеку.

    Два независимых ответа рядом: `name`/`sandbox` — какая ограда,
    `service_installed`/`session0`/`firewall` — что со службой. Склеивать их
    обратно на экране нельзя: из этой склейки и вырос P0.
    """
    return {
        "name": picture.get("name") or DEFAULT_MODE,
        "title": picture.get("title") or TITLES[DEFAULT_MODE],
        "text": picture.get("text") or TEXTS[DEFAULT_MODE],
        "sandbox": bool(picture.get("sandbox")),
        "explicit": bool(picture.get("explicit")),
        "source": picture.get("source") or "",
        "service_installed": picture.get("service_installed"),
        "service_title": picture.get("service_title") or SERVICE_TITLE,
        "service_text": picture.get("service_text") or SERVICE_TEXT,
        "session0": bool(picture.get("session0")),
        "session0_set": bool(picture.get("session0_set")),
        "session0_warning": picture.get("session0_warning") or "",
        "firewall": bool(picture.get("firewall")),
        "firewall_set": bool(picture.get("firewall_set")),
        "legacy_service": bool(picture.get("legacy_service")),
        "notes": list(picture.get("notes") or ()),
    }


def catalogue() -> list[dict]:
    """Два режима-ограды списком — для экранов установщика и настроек.

    Службы здесь НЕТ: она не третья карточка выбора, а отдельная галочка поверх
    любой из двух (`service_option`). Один источник текстов на установщик,
    настройки, анатомию и конституцию: расхождение описаний здесь означало бы,
    что владелец выбирает одно, а получает другое.
    """
    return [{
        "name": name,
        "title": TITLES[name],
        "text": TEXTS[name],
        # Ни одна ограда прав администратора не требует: и per-user установка, и
        # профиль AppContainer, и icacls на свои папки обходятся без него.
        "needs_admin": False,
        "sandbox": name == "sandbox",
    } for name in MODES]


def service_option() -> dict:
    """Опция службы с двумя её галочками — для тех же экранов.

    Отдельная функция, а не третий пункт `catalogue()`: служба ставится поверх
    ЛЮБОГО режима и ни одну ограду не снимает.
    """
    return {
        "name": "service",
        "title": SERVICE_TITLE,
        "text": SERVICE_TEXT,
        "needs_admin": True,
        "toggles": [
            {
                "key": "service.session0",
                "title": SESSION0_TITLE,
                "text": SESSION0_TEXT,
                "warning": SESSION0_WARNING,
                "default": False,
            },
            {
                "key": "service.firewall",
                "title": FIREWALL_TITLE,
                "text": FIREWALL_TEXT,
                "warning": "",
                "default": FIREWALL_DEFAULT,
            },
        ],
    }
