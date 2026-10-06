# -*- coding: utf-8 -*-
"""Сборка Hélène для семейства Debian: Debian, Ubuntu, Astra (порт 28.09).

Идёт НА Linux, в контейнере `installer/linux/Dockerfile.build` (Debian 10: порог glibc
2.28 — см. шапку того файла). Чистые части — имена, пороги glibc по колёсам и бинарям,
тексты пакета, control-файл — импортируются и на Windows; их держит стенд
`tests/t_build_linux.py`.

Что выходит:

  helene_<версия>_amd64.deb         Debian, Ubuntu, Astra
  helene-<версия>-1.x86_64.rpm      Fedora/RHEL, РЕД ОС, ROSA, ALT (ALT — apt-rpm, проверить
                                    отдельно; слово Егора 28.09: «и rpm сделать»)
  *.sha256

Оба пакета — из ОДНОГО конфига nfpm (`nfpm.yaml` рядом): программа в /opt/helene, шаблон
службы helene@.service (Debian — /lib/systemd/system, rpm — /usr/lib/systemd/system),
политика polkit /usr/share/polkit-1/actions/app.helene.policy, /usr/bin/helene-home. Разное у
семейств — только имена зависимостей и путь юнита.

Раскладка /opt/helene: окно Electron и общий Rust-хост, затем движок и данные:

  helene  electron/          окно Electron
  helene-host               общий Rust-хост оболочки
  helene-relay  helene-svc  helene-bridge  helene-body     Rust, glibc ≤ 2.28
  runtime/                  CPython 3.14.7 (python-build-standalone) + пакеты, голос включая
  app/                      пакет desk вида linux (deskpkg.build)
  tree/                     код агента — байт в байт из Windows-архива выпуска (или --tree)
  server/ licenses/         как у Windows и Mac
  helene.json               ШАБЛОН конфига: `helene-svc home` копирует его в дом владельца
  helene-build.json         паспорт сборки
  ПЕРВЫЙ-ЗАПУСК.md …        документы поставки

Дом владельца — `~/.local/share/helene` (`helene-svc home`): свой `helene.json`, `data/` и
ССЫЛКИ на части /opt/helene. Весь код, что ищет реле, питон и тело рядом с конфигом,
работает без переделок; обновление пакета подменяет программу под ссылками.

git в поставку не кладётся: у Debian-семейства он есть пакетом, и .deb его требует
(`Depends: git`), как и `bubblewrap` — ограду тула shell.

Запуск (в контейнере сборщика):
    python3 installer/build_linux.py [--out DIR] [--from-release TAG | --tree PATH]
                                     [--skip-runtime] [--skip-rust] [--skip-tests]
                                     [--allow-partial]

Правило то же, что у build_dist/build_mac: сборка либо выпускает ПОЛНЫЙ пакет, либо падает
понятной строкой. Окно Electron обязательно: complete:true означает полный состав,
а аппаратная приёмка указывается отдельно в поле window паспорта.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

DESK = Path(__file__).resolve().parent.parent
ROOT = DESK.parent
sys.path.insert(0, str(DESK))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import deskpkg  # noqa: E402
import build_dist as bd  # noqa: E402 — отбор дерева, гард, лицензии, зависимости
import build_mac as bm  # noqa: E402 — дерево из выпуска, реле, тело, их лицензии
from build_window_linux import stage_window

PLATFORM = "linux"
ARCH = "amd64"                      # имя архитектуры Debian (dpkg --print-architecture)
PRODUCT = "Hélène"
FOLDER = "Helene"
PACKAGE = "helene"                  # имя пакета Debian: строчные, без диакритики
PROGRAM_ROOT = "/opt/helene"        # то же, что LINUX_SVC_PROGRAM_ROOT в common/linux_service.rs
MAINTAINER = "Hélène <https://github.com/josephsteuerjr/praxis/issues>"
RPM_RELEASE = "1"

#: Порог glibc поставки: Debian 10 / Astra 1.7 (догадка про Astra — проверяется в ВМ).
GLIBC_FLOOR = (2, 28)

# --- что скачивается ------------------------------------------------------------------

PY_VERSION = bm.PY_VERSION          # 3.14.7 — та же линия, что у Mac
PBS_TAG = bm.PBS_TAG
PBS_NAME = f"cpython-{PY_VERSION}+{PBS_TAG}-x86_64-unknown-linux-gnu-install_only.tar.gz"
PBS_BASE = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}"
PBS_URL = f"{PBS_BASE}/{PBS_NAME}"
#: Сумма архива питона. Пусто — сверяется с файлом `SHA256SUMS` того же выпуска издателя
#: (он скачивается рядом), и найденная сумма печатается: её вписывают сюда при первой
#: сборке, и дальше выпуск сверяется с записанным, а не только с издателем.
PBS_SHA256 = "0ab3305457051cd3e7c031857e005f1bda17c218a1990567dacaaac6dd1d14f0"

#: Что в корне поставки — от Rust. Каждый обязан быть ELF и не требовать glibc новее порога.
ROOT_BINARIES = ("helene-relay", "helene-svc", "helene-bridge", "helene-body", "helene-host")
SVC_CRATE = DESK / "svc"
SVC_BIN = "helene-svc"

#: Обязательный состав, включая окно и общий хост.
REQUIRED_ROOT = (
    "helene-relay", "helene-svc", "helene-bridge", "helene-body",
    "helene", "helene-host", "electron/helene-window", "electron/resources/app/package.json",
    "electron/resources/app/out/main.js", "electron/resources/app/out/preload.js",
    "electron/LICENSE", "electron/LICENSES.chromium.html",
    "runtime/bin/python3",
    "app/deskapp.py", "app/desk.json", "app/static/index.html", "app/mobile/index.html",
    "app/localharness/runner.py", "app/localharness/body.py", "app/resources/SOUL.md",
    "tree", "server", "licenses/rust/README.md", "licenses/body/README.md",
    "licenses/relay/README.md",
    "helene.json", "helene-build.json", "requirements.txt",
    "ПЕРВЫЙ-ЗАПУСК.md", "ОБНОВЛЕНИЕ.md", "КАК-УСТРОЕН-HELENE.md", "ЛИЦЕНЗИЯ.md",
    "РАСШИРЕНИЯ.md", "ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md", "NOTICE",
)

#: Зависимости пакета Debian. glibc — порогом; git — агенту (личный репозиторий, снимки
#: правок), bubblewrap — ограда тула shell (`fence_posix`), procps — `ps` для тела и стендов.
#: Рекомендации: шина доступности (дерево окна), polkit (пароль администратора окном),
#: xdg-utils (открыть ссылку входа в подписку).
DEPENDS = ("libc6 (>= 2.28)", "git", "bubblewrap", "procps", "ca-certificates",
           "libstdc++6", "libgtk-3-0 | libgtk-3-0t64", "libnss3", "libgbm1", "libasound2 | libasound2t64",
           "libx11-6", "libxcomposite1", "libxdamage1", "libxext6", "libxfixes3",
           "libxrandr2", "libxcb1", "libxkbcommon0", "libatk1.0-0 | libatk1.0-0t64", "libatk-bridge2.0-0 | libatk-bridge2.0-0t64",
           "libcups2 | libcups2t64", "libdrm2", "libpango-1.0-0", "libcairo2")
RECOMMENDS = ("at-spi2-core", "pkexec | policykit-1", "xdg-utils")
#: Те же зависимости именами rpm-семейства (Fedora/RHEL/РЕД ОС/ROSA): glibc 2.28 — это RHEL 8,
#: ровно наш порог; `procps-ng` — имя procps у Red Hat. Мягкие зависимости rpm (Recommends)
#: понимает с rpm 4.12 — у RHEL 8 и новее он есть.
RPM_DEPENDS = ("glibc >= 2.28", "git", "bubblewrap", "procps-ng", "ca-certificates",
               "libstdc++", "gtk3", "nss", "mesa-libgbm", "alsa-lib", "libX11", "libXcomposite",
               "libXdamage", "libXext", "libXfixes", "libXrandr", "libxcb", "libxkbcommon",
               "atk", "at-spi2-atk", "cups-libs", "libdrm", "pango", "libcairo")
RPM_RECOMMENDS = ("at-spi2-core", "polkit", "xdg-utils")
#: Куда кладётся юнит: у Debian 10 /lib ещё не слит с /usr/lib, у rpm-семейства путь пакетов —
#: /usr/lib/systemd/system.
UNIT_PATHS = {"deb": "/lib/systemd/system/helene@.service",
              "rpm": "/usr/lib/systemd/system/helene@.service"}


# --- чистые функции (идут и на Windows, их держит tests/t_build_linux.py) ------------------

def asset_names(version: str) -> dict[str, str]:
    """Имена пакетов — по обычаю каждого семейства: `name_ver_arch.deb`, `name-ver-rel.arch.rpm`."""
    deb = f"{PACKAGE}_{version}_{ARCH}.deb"
    rpm = f"{PACKAGE}-{version}-{RPM_RELEASE}.x86_64.rpm"
    return {"deb": deb, "deb_sha256": deb + ".sha256", "rpm": rpm, "rpm_sha256": rpm + ".sha256",
            "windows_zip": f"{FOLDER}-{version}.zip"}


def glibc_of_manylinux(tag: str) -> tuple[int, int] | None:
    """Какую glibc требует тег колеса: `manylinux_2_28_x86_64` → (2, 28);
    `manylinux2014` → (2, 17), `manylinux2010` → (2, 12), `manylinux1` → (2, 5).
    musllinux и прочее — None (не наша libc)."""
    tag = tag.strip()
    m = re.search(r"manylinux_(\d+)_(\d+)_", tag)
    if m:
        return int(m.group(1)), int(m.group(2))
    for legacy, floor in (("manylinux2014", (2, 17)), ("manylinux2010", (2, 12)),
                          ("manylinux1", (2, 5))):
        if legacy in tag:
            return floor
    return None


def glibc_floor_of_tags(tags) -> tuple[int, int] | None:
    """Наибольшее требование glibc среди тегов колёс (каждое колесо несёт несколько
    тегов: берём наименьший у колеса — его хватает, — и наибольший по всем колёсам)."""
    worst = None
    for wheel_tags in tags:
        floors = [f for f in (glibc_of_manylinux(t) for t in wheel_tags) if f]
        if not floors:
            continue
        need = min(floors)
        worst = need if worst is None else max(worst, need)
    return worst


_GLIBC_SYMBOL = re.compile(rb"GLIBC_(\d+)\.(\d+)(?:\.(\d+))?")


def glibc_floor_of_elf(data: bytes) -> tuple[int, int] | None:
    """Наибольшая версия символов GLIBC_x.y, на которую ссылается ELF — порог glibc, с
    которым он запустится. Читается из байтов (имена версий лежат в `.dynstr`), без
    objdump: стенд гоняет это и на Windows. Статический ELF без glibc — None."""
    if not data.startswith(b"\x7fELF"):
        return None
    found = [(int(m.group(1)), int(m.group(2))) for m in _GLIBC_SYMBOL.finditer(data)]
    return max(found) if found else None


def version_text(v: tuple[int, int] | None) -> str:
    return "—" if v is None else f"{v[0]}.{v[1]}"


def deb_version(version: str) -> str:
    """Версия продукта → версия пакета Debian (у нас совпадают: 1.2.3 → 1.2.3)."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"версия {version!r} не годится в пакет Debian")
    return version


#: Политика polkit: действие, от имени которого `pkexec` спрашивает пароль, когда владелец
#: ставит или снимает службу. Путь программы — ровно тот, что зовёт `helene-svc service`.
POLKIT_POLICY = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <vendor>Hélène</vendor>
  <vendor_url>https://github.com/josephsteuerjr/praxis</vendor_url>
  <action id="app.helene.service">
    <description>Управление службой или команда администратора Hélène</description>
    <message>Hélène просит пароль администратора для операции службы или одной команды агента. Подтверждение относится только к этой операции.</message>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">{PROGRAM_ROOT}/helene-svc</annotate>
  </action>
</policyconfig>
"""

#: Сценарии dpkg. 04.10, слово владельца («apt install не ставит службу — установочного
#: демона просто нет»): postinst САМ ставит службу владельцу этого компьютера. Кого
#: поднимать: кто ставил (SUDO_USER / PKEXEC_UID — терминал), кто за активным графическим
#: сеансом seat0 (App Center ставит от root, переменных нет), наконец — единственный
#: пользователь с уже заведённым домом агента. На fresh-машине без ни одного
#: экземпляра — включаем; если экземпляры уже есть (обновление или владелец сознательно
#: выключил) — НЕ трогаем: включить выключенное без слова владельца значило бы
#: предавать его выбор. Не определили владельца — служба не включена, её поставит
#: карточка окна (pkexec) тем же путём. При удалении пакета гасятся все экземпляры,
#: чтобы systemd не держал юнит на снятой программе; обновление их НЕ гасит: программа
#: меняется под работающей службой, и systemd перезапускает её сам (`try-restart` ниже).

#: Корневой резидент нулевой сессии (04.10). Ставится пакетом ВЫКЛЮЧЕННЫМ:
#: дверь открывает только третья ступень лестницы (service.session0=true),
#: и включают её тем же подтверждением polkit, что и надзорную службу.
BROKER_UNIT = """[Unit]
Description=Hélène — корневой брокер нулевой сессии
Documentation=file:///opt/helene/КАК-УСТРОЕН-HELENE.md
After=systemd-sysctl.service
ConditionPathExists=/opt/helene/helene-svc

[Service]
Type=simple
ExecStart=/opt/helene/helene-svc broker
Restart=always
RestartSec=10
TimeoutStopSec=20
# Журнал — в journald (stderr) и в /var/lib/helene/broker.log самой службой.

[Install]
WantedBy=multi-user.target
"""

POSTINST = """#!/bin/sh
set -e
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload >/dev/null 2>&1 || true
    # Обновление сохраняет ранее данное согласие на нулевую сессию. Старый
    # пакет мог сохранить флаг, но оставить корневой брокер выключенным.
    if /opt/helene/runtime/bin/python3 -I -c '
import json, pwd
from pathlib import Path
for user in pwd.getpwall():
    if not 1000 <= user.pw_uid < 60000:
        continue
    try:
        cfg = json.loads((Path(user.pw_dir) / ".local/share/helene/helene.json").read_text())
        service = cfg.get("service", {})
        if service.get("session0") is True and service.get("broker") is not False:
            raise SystemExit(0)
    except (OSError, ValueError, AttributeError):
        continue
raise SystemExit(1)
'; then
        systemctl enable --now helene-broker.service >/dev/null 2>&1 || true
    fi
    systemctl try-restart helene-broker.service >/dev/null 2>&1 || true
    # Обновление: работающие службы владельцев — на новую программу.
    for unit in $(systemctl list-units --plain --no-legend 'helene@*.service' 2>/dev/null | awk '{print $1}'); do
        systemctl try-restart "$unit" >/dev/null 2>&1 || true
    done
    # Установочный демон (04.10, вечер): ставит службу, если её некому держать
    # и владелец ЯВНО не отказывался. Граница честности — не «здесь жили» (дом
    # есть у каждого обновляющегося, и те никогда не получали службу), а отметка
    # отказа: снятие службы программой пишет в конфиг installed.service=false —
    # вот это свято. Выключенное голым systemctl без программы отметки не
    # оставляет: такой случай демон сочтёт «не отказывался» и включит — цена
    # границы, о ней сказано в контракте тестом.
    have=$(ls /etc/systemd/system/multi-user.target.wants/helene@*.service 2>/dev/null | grep -c . || true)
    running=$(systemctl list-units --all --plain --no-legend 'helene@*.service' 2>/dev/null | grep -c . || true)
    if [ "$have" = "0" ] && [ "$running" = "0" ]; then
        owner=""
        # 1) кто ставил: терминал с sudo или pkexec
        if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
            owner="$SUDO_USER"
        elif [ -n "$PKEXEC_UID" ]; then
            owner=$(getent passwd "$PKEXEC_UID" 2>/dev/null | cut -d: -f1)
        fi
        # 2) кто за графическим сеансом (App Center ставит от root без переменных)
        if [ -z "$owner" ] && command -v loginctl >/dev/null 2>&1; then
            # Колонки list-sessions: SESSION UID USER SEAT TTY (STATE в ней нет).
            # Только настоящие люди: uid>=1000 отсекает служебный сеанс приветствия
            # gdm (uid~120), который тоже сидит на seat0 и ломал «ровно один сеанс».
            seats=$(loginctl list-sessions --no-legend 2>/dev/null | awk '$4=="seat0" && $2>=1000 && $2<60000 {print $3}' | sort -u)
            count=$(printf '%s
' "$seats" | grep -c . || true)
            if [ "$count" = "1" ]; then owner="$seats"; fi
        fi
        if [ -n "$owner" ] && [ "$owner" != "root" ]; then
            case "$owner" in
                *[!A-Za-z0-9_.-]*|"") owner="" ;;
            esac
        fi
        if [ -n "$owner" ]; then
            # Явный отказ владельца (служба снята программой) — не включаем.
            cfg="/home/$owner/.local/share/helene/helene.json"
            if [ -f "$cfg" ] && grep -q '"service"[[:space:]]*:[[:space:]]*false' "$cfg"; then
                :
            else
                systemctl enable --now "helene@$owner.service" >/dev/null 2>&1 || true
            fi
        fi
    fi
fi
exit 0
"""

# Аргумент сценария у семейств разный: dpkg зовёт prerm с `remove` (и `upgrade` при
# обновлении), rpm — %preun с числом оставшихся копий пакета (`0` — снятие, `1` — обновление).
PRERM = """#!/bin/sh
set -e
case "$1" in remove|0) removing=1 ;; *) removing=0 ;; esac
if [ "$removing" = 1 ] && [ -d /run/systemd/system ]; then
    for unit in $(systemctl list-units --all --plain --no-legend 'helene@*.service' 2>/dev/null | awk '{print $1}'); do
        systemctl disable --now "$unit" >/dev/null 2>&1 || true
    done
    systemctl disable --now helene-broker.service >/dev/null 2>&1 || true
fi
exit 0
"""

POSTRM = """#!/bin/sh
set -e
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload >/dev/null 2>&1 || true
fi
# Данные владельцев (~/.local/share/helene) пакет не трогает никогда — даже при purge:
# это память агента, и снимает её только владелец руками.
exit 0
"""

#: `/usr/bin/helene-home` — одна команда для владельца: завести дом и сказать, где он.
HELENE_HOME_SCRIPT = f"""#!/bin/sh
# Дом агента Hélène для ЭТОГО пользователя: ~/.local/share/helene (конфиг, данные и
# ссылки на программу в {PROGRAM_ROOT}). Безопасно звать сколько угодно раз.
exec {PROGRAM_ROOT}/helene-svc home "$@"
"""


def helene_json_linux() -> str:
    """Шаблон конфига — тот же, что у Windows, с питоном `runtime/bin/python3` (в доме
    владельца `runtime` — ссылка на программу)."""
    cfg = json.loads(bd.HELENE_JSON)
    cfg["python"] = "runtime/bin/python3"
    return json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"


FIRST_RUN_LINUX = f"""# Hélène · первый запуск на Linux (Debian, Ubuntu, Astra)

Программа ставится пакетом:

    sudo apt install ./Helene_<версия>_amd64.deb

Код агента ложится в `{PROGRAM_ROOT}` (владелец — root, только чтение), данные — у
КАЖДОГО пользователя свои, в `~/.local/share/helene`: конфиг `helene.json`, память,
дневник, конституция, вход в ChatGPT. Пакет их не трогает никогда — ни при обновлении,
ни при удалении.

## Дом агента

    helene-home

заводит `~/.local/share/helene`: копирует шаблон `helene.json`, создаёт `data/` и ставит
ссылки на части программы. Звать можно сколько угодно раз — чужого не сносит, конфликт
называет словами.

## Окно

Открой Hélène из меню приложений или командой `helene`. При первом запуске появятся
настройки модели. Окно Electron использует общий Rust-хост: тот же движок, настройки,
резервные копии и управление агентами. Закрытие окна прячет его к часам; «Выйти» в меню
значка завершает его дочерние процессы. Установленная служба работает независимо.

Неподвижное удержание края пальцами на тачпаде в Linux Electron пока недоступно
(и X11, и Wayland). Обычная прокрутка и возврат края используют общую физику;
аппаратная проверка Linux-тачпада ещё не выполнена. Обновление — новым .deb/.rpm
через пакетный менеджер, а не Windows/Mac установщиком из окна.

## Работать без входа в систему (служба)

    helene-svc service install      # поставить: пароль администратора спросит система
    helene-svc service state        # running | stopped | absent | unknown
    helene-svc service remove       # снять

Служба — `helene@<твоё имя>.service`: systemd поднимает движок, канал и реле от ТВОЕГО
имени (не root), без входа в систему, и поднимает снова, если упали. Журнал —
`journalctl -u helene@$USER` и `~/.local/share/helene/data/service.log`.

Чего служба не даёт (свойство режима, а не поломка): окон и экрана у неё нет — процесс
вне сеанса не видит ни X-сервер, ни шину доступности. Тул `computer` оживает, когда
открыто окно Helene: тело поднимает оно.

## Управление компьютером

Тул `computer` — окна, экран, клавиатура и мышь, файлы и процессы. Окна, ввод и снимки
идут через X-сервер (EWMH, XTest), дерево окна — через доступность (AT-SPI, пакет
`at-spi2-core`). ⚠ В сеансе **Wayland** (по умолчанию в Debian 12 и Ubuntu 22.04+) тело
видит и водит только X-программы: так устроен Wayland. Для полного управления выбери при
входе сеанс X11 («GNOME на Xorg» / «Ubuntu on Xorg»). Дерево окна и действия над
элементами работают в обоих. На Astra (рабочий стол Fly) сеанс — X11.

## Ограда тула shell

bubblewrap: команды агента видят рантайм и код, пишут только в его дом. ⚠ На Ubuntu
24.04+ AppArmor ограничивает пространства имён для непривилегированных программ; если
ограда не поднимается — это видно на экране «Система» (догадка: нужен профиль AppArmor
для bwrap — проверяется в ВМ).

## Что проверить руками

Живьём на Debian/Ubuntu/Astra эта сборка ещё не прогонялась — только стенды в
контейнерах. Порядок проверки — `desk-notes/ПЕРЕДАЧА-28.09-DEBIAN.md`.

Куда писать — issues https://github.com/josephsteuerjr/praxis/issues, с версией из
`{PROGRAM_ROOT}/helene-build.json`.
"""

THIRD_PARTY_LINUX = """# Лицензии третьих сторон (сборка для Linux)

Hélène собрана из открытых компонентов. Полные тексты — внутри поставки:

- крейты службы и общего хоста (`helene-svc`, `helene-host`) — `licenses/rust/`;
- Electron — `electron/LICENSE`, Chromium — `electron/LICENSES.chromium.html`;
- крейты реле подписки ChatGPT (`helene-relay`, MIT) — `licenses/relay/`;
- крейты моста и тела тула `computer` (`helene-bridge`, `helene-body`) — `licenses/body/`
  (среди них x11rb, zbus и arboard — MIT или Apache-2.0);
- пакеты Python — `runtime/lib/python3.14/site-packages/<пакет>.dist-info/`;
- CPython — `runtime/lib/python3.14/LICENSE.txt` (PSF; сборка python-build-standalone).

OpenSSL влинкован в `helene-relay` статически — лицензия OpenSSL/SSLeay (OpenSSL 1.1.1
из Debian 10), текст — `licenses/relay/OPENSSL.txt`.

Код агента (`tree/`) — Apache-2.0: `tree/LICENSE`, `tree/NOTICE`. git и bubblewrap в
поставку не входят — это пакеты системы.
"""


def missing_in_root(root: Path) -> list[str]:
    return [rel for rel in REQUIRED_ROOT if not (Path(root) / rel).exists()]


def sha256_line(digest: str, name: str) -> str:
    return f"{digest}  {name}\n"


# --- рантайм --------------------------------------------------------------------------

def publisher_sum(cache: Path) -> str:
    """Сумма архива питона из `SHA256SUMS` выпуска издателя."""
    sums = cache / f"pbs-{PBS_TAG}-SHA256SUMS"
    if not sums.is_file():
        bd.fetch(f"{PBS_BASE}/SHA256SUMS", sums)
    for line in sums.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == PBS_NAME:
            return parts[0].lower()
    raise SystemExit(f"в SHA256SUMS выпуска {PBS_TAG} нет {PBS_NAME}")


def fetch_python(cache: Path) -> Path:
    tgz = cache / PBS_NAME
    bd.fetch(PBS_URL, tgz)
    want = PBS_SHA256 or publisher_sum(cache)
    got = bd.sha256(tgz)
    if got != want:
        tgz.unlink(missing_ok=True)
        raise SystemExit(f"НЕ СОШЛАСЬ СУММА {PBS_NAME}: ждали {want}, скачали {got}")
    if not PBS_SHA256:
        print(f"  ⚠ сумма питона сверена с издателем: {got} — впиши в PBS_SHA256")
    return tgz


def deps() -> list[str]:
    return list(bd.TREE_DEPS) + list(bd.VOICE_DEPS) + deskpkg.requirements(deskpkg.LINUX)


def runtime_python(out: Path) -> Path:
    return out / "runtime" / "bin" / "python3"


def stage_runtime(out: Path, cache: Path) -> None:
    runtime = out / "runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    tgz = fetch_python(cache)
    with tarfile.open(tgz) as archive:
        archive.extractall(out, filter="tar")
    (out / "python").rename(runtime)
    py = runtime_python(out)
    said = bm.capture([py, "--version"], timeout=120).strip()
    if PY_VERSION not in said:
        raise SystemExit(f"рантайм назвался {said!r}, а ждали {PY_VERSION}")
    print(f"  runtime/: {said}")
    req = out / "requirements.txt"
    req.write_text("\n".join(deps()) + "\n", encoding="utf-8", newline="\n")
    pip = [py, "-m", "pip", "install", "-q", "--no-warn-script-location"]
    bm.run([*pip, "setuptools", "wheel"], timeout=1800)
    only = ["--only-binary=:all:"] + [f"--no-binary={name}" for name in bm.SDIST_OK]
    bm.run([*pip, *only, "-r", req], timeout=3600)
    bm.run([py, "-m", "pip", "uninstall", "-y", "-q", "setuptools", "wheel"], check=False, timeout=600)
    # Кэш pip — снаружи поставки (PIP_CACHE_DIR на томе сборщика, см. build-in-docker.sh): в
    # пакет он не едет, а пересборка не качает сотни мегабайт колёс голоса заново.


def runtime_wheel_tags(out: Path) -> list[list[str]]:
    site = out / "runtime" / "lib" / f"python{PY_VERSION.rsplit('.', 1)[0]}" / "site-packages"
    tags = []
    for wheel in sorted(site.glob("*.dist-info/WHEEL")):
        tags.append([line[4:].strip() for line in wheel.read_text(encoding="utf-8", errors="replace").splitlines()
                     if line.startswith("Tag:")])
    return tags


def glibc_census(out: Path) -> dict:
    """Порог glibc всего, что в поставке: колёса по тегам, ELF по символам. Больше порога —
    отказ: пообещать Debian 10 и не запуститься на нём — хуже, чем не обещать."""
    wheels = glibc_floor_of_tags(runtime_wheel_tags(out))
    worst_elf, offenders = None, []
    for path in sorted(out.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            with path.open("rb") as f:
                if f.read(4) != b"\x7fELF":
                    continue
            floor = glibc_floor_of_elf(path.read_bytes())
        except OSError:
            continue
        if floor is None:
            continue
        worst_elf = floor if worst_elf is None else max(worst_elf, floor)
        if floor > GLIBC_FLOOR:
            offenders.append(f"{path.relative_to(out)} (glibc {version_text(floor)})")
    if wheels and wheels > GLIBC_FLOOR:
        offenders.append(f"колёса рантайма (manylinux glibc {version_text(wheels)})")
    if offenders:
        raise SystemExit(f"поставка требует glibc новее {version_text(GLIBC_FLOOR)}:\n  "
                         + "\n  ".join(offenders[:30]))
    print(f"  порог glibc: колёса {version_text(wheels)}, ELF {version_text(worst_elf)}; "
          f"обещано {version_text(GLIBC_FLOOR)}")
    return {"floor": version_text(GLIBC_FLOOR), "wheels": version_text(wheels),
            "elf": version_text(worst_elf)}


# --- Rust -------------------------------------------------------------------------------

def build_svc(skip: bool) -> Path:
    exe = SVC_CRATE / "target" / "release" / SVC_BIN
    if not skip:
        bm.run(["cargo", "build", "--release"], cwd=SVC_CRATE, timeout=5400)
    if not exe.is_file():
        raise SystemExit(f"нет {exe}")
    return exe


def not_linked_to_libssl(exe: Path) -> None:
    """Реле обязано нести OpenSSL в себе: libssl1.1 Debian 10 нет на Debian 12/Ubuntu 22.04+."""
    said = bm.capture(["ldd", exe], timeout=60, check=False)
    if "libssl" in said or "libcrypto" in said:
        raise SystemExit(f"{exe.name} слинкован с системной OpenSSL — на новых системах не запустится:\n{said}")


# --- пакеты (nfpm: .deb и .rpm из одного конфига) ---------------------------------------

def program_contents(out: Path, program_root: str = PROGRAM_ROOT) -> list[dict]:
    """Содержимое /opt/helene для nfpm — перечислением, а не «деревом»: каждый файл со своими
    правами, симлинк рантайма (`bin/python3 → python3.14`) — симлинком. Чистая функция
    над файловой системой (её держит стенд на временной папке)."""
    out = Path(out)
    items: list[dict] = []
    for path in sorted(out.rglob("*")):
        rel = path.relative_to(out).as_posix()
        dst = f"{program_root}/{rel}"
        if path.is_symlink():
            items.append({"src": os.readlink(path), "dst": dst, "type": "symlink"})
        elif path.is_file():
            mode = path.stat().st_mode & 0o7777
            items.append({"src": str(path), "dst": dst,
                          "file_info": {"mode": mode}})
        elif path.is_dir() and not any(path.iterdir()):
            items.append({"dst": dst, "type": "dir", "file_info": {"mode": 0o755}})
    return items


def nfpm_config(version: str, contents: list[dict], files: dict[str, str]) -> dict:
    """Конфиг nfpm: общая часть, различия семейств — в `overrides`. `files` — пути
    подготовленных файлов (юнит, политика, helene-home, сценарии). Чистая функция."""
    extra = [
        {"src": files["unit"], "dst": UNIT_PATHS["deb"], "packager": "deb",
         "file_info": {"mode": 0o644}},
        {"src": files["unit"], "dst": UNIT_PATHS["rpm"], "packager": "rpm",
         "file_info": {"mode": 0o644}},
        *([] if "broker_unit" not in files else [
            {"src": files["broker_unit"], "dst": "/lib/systemd/system/helene-broker.service",
             "file_info": {"mode": 0o644}}]),
        {"src": files["policy"], "dst": "/usr/share/polkit-1/actions/app.helene.policy",
         "file_info": {"mode": 0o644}},
        {"src": files["home"], "dst": "/usr/bin/helene-home", "file_info": {"mode": 0o755}},
        # `helene-svc service install|state|remove` — команды владельца из документа первого
        # запуска: без ссылки в PATH их пришлось бы звать полным путём (поймала проверка
        # установки в чистом Debian 28.09).
        {"src": f"{PROGRAM_ROOT}/helene-svc", "dst": "/usr/bin/helene-svc", "type": "symlink"},
        {"src": f"{PROGRAM_ROOT}/helene", "dst": "/usr/bin/helene", "type": "symlink"},
    ]
    if "desktop" in files:
        extra.extend([
            {"src": files["desktop"], "dst": "/usr/share/applications/helene.desktop", "file_info": {"mode": 0o644}},
            {"src": files["icon"], "dst": "/usr/share/icons/hicolor/128x128/apps/helene.png", "file_info": {"mode": 0o644}},
        ])
    return {
        "name": PACKAGE,
        "arch": ARCH,
        "platform": "linux",
        "version": deb_version(version),
        "release": RPM_RELEASE,
        "section": "utils",
        "priority": "optional",
        "maintainer": MAINTAINER,
        "vendor": PRODUCT,
        "homepage": "https://github.com/josephsteuerjr/praxis",
        "license": "Apache-2.0",
        "description": ("Hélène — личный агент на твоём компьютере\n"
                        "Агент с памятью, конституцией и руками: Telegram, телефон, файлы и, по "
                        "отдельной опции, окна, экран, клавиатура и мышь (X11 и AT-SPI). Код — в "
                        f"{PROGRAM_ROOT}, данные владельца — в ~/.local/share/helene; служба без "
                        "входа в систему — шаблон systemd helene@<имя>.service."),
        "contents": contents + extra,
        "scripts": {"postinstall": files["postinst"], "preremove": files["prerm"],
                    "postremove": files["postrm"]},
        "overrides": {
            "deb": {"depends": list(DEPENDS), "recommends": list(RECOMMENDS)},
            "rpm": {"depends": list(RPM_DEPENDS), "recommends": list(RPM_RECOMMENDS)},
        },
        "deb": {"compression": "gzip"},
        "rpm": {"compression": "gzip", "group": "Applications/System",
                "summary": "Hélène — личный агент на твоём компьютере"},
    }


def build_packages(out: Path, version: str, dest: Path, unit_text: str) -> dict[str, Path]:
    """Собрать .deb и .rpm из папки поставки `out` (она становится /opt/helene)."""
    work = dest / "pkg-work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    files = {"unit": work / "helene@.service",
             "broker_unit": work / "helene-broker.service",
             "policy": work / "app.helene.policy",
             "home": work / "helene-home", "postinst": work / "postinst",
             "prerm": work / "prerm", "postrm": work / "postrm"}
    for key, text in (("unit", unit_text), ("broker_unit", BROKER_UNIT),
                      ("policy", POLKIT_POLICY), ("home", HELENE_HOME_SCRIPT),
                      ("postinst", POSTINST), ("prerm", PRERM), ("postrm", POSTRM)):
        files[key].write_text(text, encoding="utf-8", newline="\n")
    files["desktop"] = work / "helene.desktop"
    files["desktop"].write_text("[Desktop Entry]\nType=Application\nName=Hélène\nComment=Личный агент\nExec=/usr/bin/helene\nIcon=helene\nTerminal=false\nCategories=Utility;\nStartupWMClass=Helene\n", encoding="utf-8")
    files["icon"] = DESK / "shell/icons/128x128.png"
    config = nfpm_config(version, program_contents(out), {k: str(v) for k, v in files.items()})
    conf_path = work / "nfpm.yaml"          # JSON — это валидный YAML
    conf_path.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
    names = asset_names(version)
    made: dict[str, Path] = {}
    for packager in ("deb", "rpm"):
        target = dest / names[packager]
        target.unlink(missing_ok=True)
        bm.run(["nfpm", "package", "--config", conf_path, "--packager", packager, "--target", target],
               timeout=3600)
        if not target.is_file():
            raise SystemExit(f"nfpm не собрал {target.name}")
        if packager == "rpm":
            import rpm_archive_size
            print("RPM archive-size:", rpm_archive_size.repair(target))
        made[packager] = target
    # Проверка чужими руками: dpkg и rpm читают то, что собрал nfpm.
    bm.run(["dpkg-deb", "--info", made["deb"]], timeout=120)
    listing = bm.capture(["dpkg-deb", "-c", made["deb"]], timeout=600)
    rpm_listing = bm.capture(["rpm", "-qlp", made["rpm"]], timeout=600)
    bm.run(["rpm", "-qip", "--requires", made["rpm"]], timeout=120)
    bm.run(["rpm", "-Kv", "--nosignature", made["rpm"]], timeout=120)
    # Drain stdout without buffering the >1GiB archive in memory; require exit0.
    with subprocess.Popen(["rpm2cpio", str(made["rpm"])], stdout=subprocess.DEVNULL) as check:
        if check.wait(timeout=600) != 0:
            raise SystemExit("rpm2cpio не принял архив RPM")
    for need in (f".{PROGRAM_ROOT}/helene-svc", f".{PROGRAM_ROOT}/runtime/bin/python3 ->",
                 f".{UNIT_PATHS['deb']}", "./usr/share/polkit-1/actions/app.helene.policy"):
        if need not in listing:
            raise SystemExit(f"в .deb нет {need!r} — пакет собран не тем составом")
    for need in (f"{PROGRAM_ROOT}/helene-svc", UNIT_PATHS["rpm"], "/usr/bin/helene-home"):
        if need not in rpm_listing:
            raise SystemExit(f"в .rpm нет {need!r} — пакет собран не тем составом")
    shutil.rmtree(work, ignore_errors=True)
    return made


# --- главное ----------------------------------------------------------------------------

def arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="сборка Hélène для Debian, Ubuntu, Astra (.deb)")
    parser.add_argument("--out", default=str(DESK / "installer" / "build-linux"))
    parser.add_argument("--from-release", default="", metavar="TAG")
    parser.add_argument("--tag", default="", metavar="TAG")
    parser.add_argument("--tree", default="", metavar="PATH")
    parser.add_argument("--skip-runtime", action="store_true")
    parser.add_argument("--skip-rust", action="store_true")
    parser.add_argument("--skip-fronts", action="store_true",
                        help="не собирать фронты (они уже собраны) — отладка")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-smokes", action="store_true", help="не исполнять импортный смоук рантайма")
    parser.add_argument("--allow-partial", action="store_true")
    return parser


def main() -> None:
    args = arg_parser().parse_args()
    if args.from_release and args.tree:
        raise SystemExit("--from-release и --tree вместе не бывают")
    if not sys.platform.startswith("linux"):
        raise SystemExit("эта сборка идёт только на Linux (контейнер installer/linux/Dockerfile.build)")
    version, declared = deskpkg.product_version()
    tree_tag, tag = bm.resolve_tags(args.from_release, args.tag, args.tree)
    if tag and bm.version_from_tag(tag) != version:
        raise SystemExit(f"ветка объявляет версию {version}, а сборку просят назвать {tag}")
    out = Path(args.out).resolve() / FOLDER
    cache = Path(args.out).resolve() / "cache"
    names = asset_names(version)
    host_glibc = os.confstr("CS_GNU_LIBC_VERSION") if hasattr(os, "confstr") else "?"
    print(f"{PRODUCT} {version} для {PLATFORM}/{ARCH}; glibc сборщика: {host_glibc}")
    print(f"сборка -> {out}")
    if out.exists():
        for item in out.iterdir():
            if item.name == "runtime" and args.skip_runtime:
                continue
            (shutil.rmtree(item) if item.is_dir() and not item.is_symlink() else item.unlink())
    out.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []

    print("фронты:")
    if args.skip_fronts:
        print("  не собираются (--skip-fronts)")
    else:
        bm.build_fronts()

    print("окно Electron и общий Rust-хост:")
    window = stage_window(out, cache, version, args.skip_rust)

    print("служба:")
    shutil.copy2(build_svc(args.skip_rust), out / SVC_BIN)
    (out / SVC_BIN).chmod(0o755)
    unit_text = bm.capture([out / SVC_BIN, "unit"], timeout=60)
    if "ExecStart=/opt/helene/helene-svc daemon --owner %i" not in unit_text:
        raise SystemExit("helene-svc unit напечатал не тот юнит:\n" + unit_text)

    print("реле:")
    # OpenSSL внутрь бинаря: libssl.a из libssl-dev сборщика (см. Dockerfile.build).
    # ⚠ Одного OPENSSL_STATIC мало (первая сборка 28.09): openssl-sys идёт через pkg-config,
    # тот не называет системный каталог с libssl.a, и сборка МОЛЧА откатывается на libssl.so.1.1
    # — такое реле не запустится на Debian 12 / Ubuntu 22.04+. С явными каталогами pkg-config
    # не зовётся вовсе. Сторож ниже (`not_linked_to_libssl`) и поймал откат.
    os.environ["OPENSSL_STATIC"] = "1"
    os.environ.setdefault("OPENSSL_LIB_DIR", "/usr/lib/x86_64-linux-gnu")
    os.environ.setdefault("OPENSSL_INCLUDE_DIR", "/usr/include")
    relay_exe, relay = bm.build_relay(cache, args.skip_rust)
    shutil.copy2(relay_exe, out / "helene-relay")
    (out / "helene-relay").chmod(0o755)
    not_linked_to_libssl(out / "helene-relay")

    print("дерево агента:")
    if args.tree:
        staged_tree = bm.stage_from_tree(out, Path(args.tree).resolve())
        source_release = None
    else:
        staged_tree = bm.stage_from_release(out, cache, tree_tag)
        source_release = staged_tree
    live = out / "tree"

    print("тело:")
    body_exes, body_info = bm.build_body(
        cache, args.skip_rust, bm.body_src_for(live, strict=bool(args.from_release) and not args.allow_partial),
        tree_head=str(staged_tree.get("tree_head") or ""))
    for name, exe in body_exes.items():
        shutil.copy2(exe, out / name)
        (out / name).chmod(0o755)
        print(f"  {name}: {(out / name).stat().st_size / 1e6:.1f} МБ")

    if args.skip_runtime:
        print("runtime: пропущен (--skip-runtime)")
    else:
        print("runtime:")
        stage_runtime(out, cache)
    freeze = bd.runtime_inventory(out) if args.skip_smokes else bm.smoke_runtime(out)
    print(f"  состав пакетов: {len(freeze.splitlines())}; смоук: {'пропущен по запросу' if args.skip_smokes else 'пройден'}")

    print("desk:")
    pkg = deskpkg.build(out / "app", deskpkg.LINUX, allow_partial=args.allow_partial, log=print)
    (out / "requirements.txt").write_text("\n".join(deps()) + "\n", encoding="utf-8", newline="\n")

    if args.skip_tests:
        print("стенды: ПРОПУЩЕНЫ (--skip-tests) — это отладка, не выпуск")
    else:
        bm.run_stands(out, False)

    print("документы:")
    (out / "ПЕРВЫЙ-ЗАПУСК.md").write_text(FIRST_RUN_LINUX, encoding="utf-8", newline="\n")
    for src, dst in (("resources/ОБНОВЛЕНИЕ.md", "ОБНОВЛЕНИЕ.md"), ("resources/РАСШИРЕНИЯ.md", "РАСШИРЕНИЯ.md"),
                     ("resources/HELENE-MAP.md", "КАК-УСТРОЕН-HELENE.md"),
                     ("installer/ЛИЦЕНЗИЯ.md", "ЛИЦЕНЗИЯ.md"), ("installer/NOTICE", "NOTICE")):
        bd.copy_text_lf(DESK / src, out / dst)
    (out / "ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md").write_text(THIRD_PARTY_LINUX, encoding="utf-8", newline="\n")
    n = bd.collect_rust_licenses(out, args.allow_partial, live, parts=("svc", "shell"), include_body=False,
                                 exes="helene-svc, helene-host")
    print(f"  лицензии крейтов службы: {n}")
    relay_src_dir = cache / "praxis-relay"
    print(f"  лицензии крейтов реле: {bm.collect_relay_licenses(out, relay_src_dir, args.allow_partial)}")
    openssl = Path("/usr/share/doc/libssl-dev/copyright")
    if openssl.is_file():
        bd.copy_text_lf(openssl, out / "licenses" / "relay" / "OPENSSL.txt")
    print(f"  лицензии крейтов тела: {bm.collect_body_licenses(out, bm.body_src_for(live), args.allow_partial, body_info)}")
    (out / "helene.json").write_text(helene_json_linux(), encoding="utf-8", newline="\n")

    print("порог glibc:")
    glibc = glibc_census(out)

    print("паспорт сборки:")
    desk_head, desk_dirty = bd._git_field(DESK, "desk")
    lost = [rel for rel in missing_in_root(out) if rel != "helene-build.json"]
    partial = missing + [f"нет в сборке: {rel}" for rel in lost] + \
        [f"пакет desk без {s['name']}" for s in pkg["skipped"]]
    blocking = partial
    if blocking and not args.allow_partial:
        raise SystemExit("сборка неполная:\n  " + "\n  ".join(blocking))
    manifest = {
        "product": PRODUCT, "platform": PLATFORM, "arch": ARCH, "version": version,
        "built_utc": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "complete": not partial, "partial_reason": partial,
        "validation": {"runtime_smoke": "skipped_by_request" if args.skip_smokes else "passed"},
        "git": {"desk": desk_head, "desk_dirty": desk_dirty,
                "tree": staged_tree.get("tree_head", ""), "tree_dirty": staged_tree.get("tree_dirty", False),
                "dirty": desk_dirty or bool(staged_tree.get("tree_dirty"))},
        "source_release": ({k: source_release[k] for k in ("tag", "asset", "sha256", "tree_files", "passport", "core")}
                           if source_release else None),
        "declared_versions": declared,
        "python": PY_VERSION, "python_build": f"python-build-standalone {PBS_TAG}",
        "glibc": glibc, "builder_glibc": host_glibc,
        "tree_files": staged_tree["tree_files"],
        "static": pkg["static"],
        "relay": relay, "body": body_info, "window": window,
        "desk": {"version": pkg["version"], "flavor": pkg["flavor"], "digest": pkg["digest"],
                 "files": len(pkg["files"]), "skipped": [s["name"] for s in pkg["skipped"]]},
        "program_root": PROGRAM_ROOT,
        "packages": freeze.splitlines(),
    }
    (out / "helene-build.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8", newline="\n")

    print("секрет-гард:")
    lib = f"lib/python{PY_VERSION.rsplit('.', 1)[0]}/site-packages/"
    bd.RUNTIME_KNOWN_FALSE = set(bd.RUNTIME_KNOWN_FALSE) | {
        (lib + rel.split("site-packages/", 1)[1], label)
        for rel, label in bd.RUNTIME_KNOWN_FALSE if "site-packages/" in rel
    }
    print(f"  просканировано файлов: {bd.scan_for_secrets(out, live, scan_runtime=not args.skip_runtime)} — чисто")

    print("пакеты (nfpm):")
    made = build_packages(out, version, out.parent, unit_text)
    for packager, path in made.items():
        digest = bd.sha256(path)
        (out.parent / names[f"{packager}_sha256"]).write_text(
            sha256_line(digest, path.name), encoding="utf-8", newline="\n")
        print(f"готово: {path} ({path.stat().st_size / 1e6:.1f} МБ), sha256 {digest}")
    if partial:
        print("⚠ ПОЛУСБОРКА: " + "; ".join(partial))


if __name__ == "__main__":
    main()
