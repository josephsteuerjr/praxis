# -*- coding: utf-8 -*-
"""Сборка Hélène для macOS (Apple Silicon).

Идёт НА macOS — раннер GitHub `macos-15` (`.github/workflows/macos.yml`). Чистые
части — разбор тега, имена активов, Info.plist, шаблон конфига, паспорт, отбор
записей архива, распознавание Mach-O, минимум macOS по тегам колёс — импортируются
и на Windows; их держит стенд `tests/t_build_mac.py`.

Раскладка архива `Helene-<версия>-macos-arm64.zip` (в корне — папка Helene/, как у
Windows-архива `Helene-<версия>.zip`):

  Helene/
    Helene.app/               оболочка (крейт shell → бинарь `helene`), app.helene.desk
    Helene Setup.app/         мастер (крейт setup → бинарь `helene-setup`), app.helene.setup
    helene-relay              реле подписки ChatGPT (praxis-relay @ f8ef18f = 0.8.2)
    helene-svc                служба без входа в систему: `daemon` — супервизор канала, движка и
                              реле под launchd, `plist` — описание демона app.helene.svc (крейт svc)
    helene-bridge             мост тела тула `computer` (praxis-bridge из praxis/body — исходник
                              в ЭТОМ репозитории, зеркало прода Праксис + darwin-ветки)
    helene-body               тело: экран, окна, клавиатура и мышь (CoreGraphics), дерево окна
                              (Accessibility), файлы, процессы; поднимает их движок рядом с собой,
                              снаружи ограды, когда включено «Управление компьютером»
    runtime/                  CPython 3.14.7 (python-build-standalone) + все пакеты, голос включая
    runtime/git/              git 2.55.0, собранный из исходника с RUNTIME_PREFIX (как MinGit на Windows)
    app/                      пакет desk вида macos (deskpkg.build) — собирается ЗАНОВО из этой ветки
    tree/                     код агента — байт в байт из Windows-архива выпуска
    data/                     пусто; рождается при первом запуске
    server/ licenses/         как у Windows; плюс licenses/body/ — крейты моста и тела
    helene.json               шаблон конфига поставки (python → runtime/bin/python3)
    helene-build.json         паспорт сборки; по нему оболочка и мастер находят корень установки
    install.sh                установка, обновление и снятие — тот же файл, что curl-однострочник
    ПЕРВЫЙ-ЗАПУСК.md ОБНОВЛЕНИЕ.md КАК-УСТРОЕН-HELENE.md ЛИЦЕНЗИЯ.md
    ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md NOTICE requirements.txt

Чего в этой сборке нет по решению владельца: Intel-маков, подписи Developer ID
и нотаризации, dmg. Об этом говорят документы поставки — не экран. Тело, брокер
прав и служба есть с 0.8.0: тело — два бинаря выше, брокер — сама оболочка через
системный диалог пароля (osascript), служба — `helene-svc` и демон launchd
`app.helene.svc` в /Library/LaunchDaemons (ставится под администратором).

Запуск (на macOS):
    python3 installer/build_mac.py [--out DIR] [--from-release TAG | --tree PATH]
                                   [--skip-runtime] [--skip-rust] [--skip-body]
                                   [--skip-tests] [--allow-partial]

Правило то же, что у `build_dist.py`: сборка либо выпускает ПОЛНЫЙ архив, либо
падает с понятной строкой. Windows-сборка не трогается: общее импортируется из
`build_dist`, а не переписывается.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# Тот же поток, что у Windows-сборки: русские строки под перенаправлением
# вывода иначе роняют сборку на первом print.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

DESK = Path(__file__).resolve().parent.parent
ROOT = DESK.parent
sys.path.insert(0, str(DESK))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import deskpkg  # noqa: E402 — состав пакета desk объявлен в репозитории
import relay_src  # noqa: E402 — отпечаток исходника реле, тот же приём, что у Windows
import build_dist as bd  # noqa: E402 — общие куски: отбор дерева, сеть, гард, лицензии, шаблоны

PLATFORM = "macos"
ARCH = "arm64"
PRODUCT = "Hélène"
FOLDER = "Helene"                       # папка в архиве и корень установки — латиницей, как у Windows
INSTALL_HOME = "~/Applications/Helene"  # куда ставит мастер; тот же смысл, что Programs\\Helene

# --- что скачивается ------------------------------------------------------------
#
# Python — python-build-standalone (astral-sh): самодостаточный CPython, pip внутри,
# ничего в системе не трогает. Та же линия 3.14, что у Windows-сборки (там 3.14.5
# из embeddable с python.org — у него нет сборки под macOS).
PY_VERSION = "3.14.7"
PBS_TAG = "20260901"
PBS_NAME = f"cpython-{PY_VERSION}+{PBS_TAG}-aarch64-apple-darwin-install_only.tar.gz"
PBS_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
           f"{PBS_TAG}/{PBS_NAME}")
# git — из исходника, с RUNTIME_PREFIX: бинарь ищет свои libexec/ и templates/ от
# собственного положения, а не от вшитого при сборке пути. Так же переносим, как
# MinGit на Windows (build_dist.stage_git), и та же линия 2.55.
GIT_VERSION = "2.55.0"
GIT_NAME = f"git-{GIT_VERSION}.tar.xz"
GIT_URL = f"https://mirrors.edge.kernel.org/pub/software/scm/git/{GIT_NAME}"

# Контрольные суммы. Сняты 19.09.2026 локально (`curl -L … | sha256sum`) и
# СВЕРЕНЫ с суммами издателей: python-build-standalone — файл `SHA256SUMS` того
# же выпуска 20260901; git — `sha256sums.asc` на kernel.org. Меняешь версию —
# снимаешь заново и пишешь дату, как в build_dist.SHA256.
SHA256 = {
    PBS_URL: "30daa970c7d223530120f1693cd3c6fa4c0c0d31ef158710b0dd77f286a5b23e",
    GIT_URL: "457fdb04dc8728e007d4688695e6912e6f680727920f2a40bf11eacc17505357",
}

# Реле: публичный репозиторий, коммит 0.8.1. На Windows реле собирается из
# зеркала живого исходника (`_relay_prod_src`, там ещё трей под cfg(windows));
# на Mac собирается публичный коммит как есть.
RELAY_REPO = "https://github.com/josephsteuerjr/praxis-relay"
RELAY_COMMIT = "f8ef18fccd70d800938b5fcfdcb378073e1c7b60"
RELAY_BIN = "codex-proxy-server"

# Тело тула `computer`: мост и тело — крейты `praxis/body` В ЭТОМ репозитории
# (зеркало прода Праксис, `CORE-SOURCE.json`, плюс darwin-ветки порта 19.09),
# клонировать нечего. Windows-сборка берёт те же крейты из `live/body` соседа
# (`build_dist.py`, BODY_TARGET); здесь исходник — репозиторий, потому что
# darwin-ветки живут в нём, пока она не взяла патч в прод. Собираются ВНЕ
# исходника (`--target-dir` в кэше сборки), чтобы `praxis/` оставался чистым.
# Версия крейтов — своя (workspace 0.1.0), в объявления продукта не входит, как
# у реле; в паспорт едет коммит зеркала и отпечаток исходника.
BODY_SRC = ROOT / "praxis" / "body"
BODY_CRATES = ("praxis-body", "praxis-bridge")
#: Бинарь крейта → имя в корне поставки (без `.exe`: имена по платформе решает
#: движок, `localharness/body.py`).
BODY_BINARIES = {"praxis-bridge": "helene-bridge", "praxis-body": "helene-body"}
#: Что в исходнике тела считается исходником: то, из чего собирается бинарь.
#: `target*/`, README и скрипты деплоя — не в счёт (как PATTERNS у реле).
BODY_SRC_PATTERNS = ("Cargo.toml", "Cargo.lock", "crates/*/Cargo.toml", "crates/*/src/**/*.rs")
#: Откуда зеркало `praxis/`: коммит прода и дата снимка (`installer/core_src.py`).
CORE_SOURCE = ROOT / "CORE-SOURCE.json"

# Свободные бинари в корне поставки (не в бандлах): подписываются ad-hoc
# каждый (`sign_targets`), и каждый обязателен (`REQUIRED_ROOT`).
ROOT_BINARIES = ("helene-relay", "helene-svc", "helene-bridge", "helene-body")

#: Служба: крейт `desk/svc`, обычный cargo-бинарь (не бандл — окна у него нет и
#: быть не должно, его запускает launchd). На Windows тот же крейт собирает
#: `helene-svc.exe`; здесь имя без суффикса.
SVC_CRATE = DESK / "svc"
SVC_BIN = "helene-svc"

# Откуда берётся дерево агента: из Windows-архива того же выпуска. Дерево там —
# проверенный прод; собирать его на Mac заново значило бы выпустить под одним
# тегом два разных дерева.
RELEASE_REPO = "josephsteuerjr/praxis"
RELEASE_TAG_DEFAULT = "v0.8.2"

# Минимум macOS. Задуман 12.0, но колёса голоса под cp314/arm64 (numpy,
# onnxruntime, av — проверено `pip download` 19.09.2026) собраны с тегом
# macosx_14_0: на 12 и 13 dyld их не загрузит, а голос по решению владельца
# обязателен. Объявлять меньше — значит обещать то, что не заработает.
# Сборка сверяет это число с тегами реально установленных колёс (см.
# `runtime_macos_floor`) и падает, если они требуют больше.
MACOS_MIN = "14.0"

# Пакеты, у которых на PyPI нет колеса и не будет: чистый Python исходником.
# `pyaes` (зависимость telethon) — единственный такой во всём составе. Всё
# остальное ставится строго колёсами (`--only-binary=:all:`): собирать
# расширения на раннере — значит зависеть от его SDK, а не от PyPI.
SDIST_OK = ("pyaes",)

# Что кладёт мастер в бандлы. Оболочка Hélène и мастер — два разных .app с
# разными идентификаторами: single-instance и уведомления macOS различают
# программы именно по CFBundleIdentifier.
BUNDLES = {
    "shell": {
        "app": "Helene.app", "exe": "helene", "name": "Hélène",
        "identifier": "app.helene.desk", "crate": "shell",
        "icon": DESK / "shell" / "icons" / "icon.png",
    },
    "setup": {
        "app": "Helene Setup.app", "exe": "helene-setup", "name": "Hélène Setup",
        "identifier": "app.helene.setup", "crate": "setup",
        "icon": DESK / "setup" / "icons" / "icon.png",
    },
}

# Набор iconset для `iconutil`: имя → сторона в пикселях. Исходник значка —
# 256×256, поэтому 512 и 1024 получаются растяжением: размыто лучше, чем дыра
# в Finder при крупном виде. Значок 1024 в репозитории сделал бы это честнее.
ICONSET = (
    ("icon_16x16", 16), ("icon_16x16@2x", 32), ("icon_32x32", 32), ("icon_32x32@2x", 64),
    ("icon_128x128", 128), ("icon_128x128@2x", 256), ("icon_256x256", 256),
    ("icon_256x256@2x", 512), ("icon_512x512", 512), ("icon_512x512@2x", 1024),
)

# Переменные make для git. NO_PERL — не потому, что perl'а на Mac нет (он есть),
# а чтобы не тащить perl-скрипты git (send-email, svn), которым нужны модули,
# которых у человека нет; tcl/gettext/python/gitweb агенту не нужны. TLS и
# SHA-1 — системные (CommonCrypto), без hardlink'ов в libexec (в zip они всё
# равно не переживут), без дублей `git-add` и прочих dashed-форм.
#
# Только системные библиотеки. На Darwin 24+ git сам берёт libiconv из Homebrew
# (config.mak.uname: USE_HOMEBREW_LIBICONV), а curl ищет через `curl-config` из
# PATH — на раннере это Homebrew, и у человека без Homebrew бинарь падал бы
# «Library not loaded». NO_HOMEBREW снимает первое (git тогда сам включает свой
# обход для системного iconv — ICONV_RESTART_RESET), CURL_LDFLAGS и
# CURL_CONFIG=/usr/bin/true — второе; CURL_CFLAGS с путём SDK добавляет
# stage_git_bundle. Что вышло на деле, проверяет `otool -L` (foreign_dylibs).
GIT_MAKE_VARS = (
    "prefix=/", "RUNTIME_PREFIX=YesPlease", "NO_GETTEXT=1", "NO_TCLTK=1", "NO_PERL=1",
    "NO_PYTHON=1", "NO_GITWEB=1", "NO_EXPAT=1", "NO_OPENSSL=1", "APPLE_COMMON_CRYPTO=1",
    "NO_INSTALL_HARDLINKS=YesPlease", "SKIP_DASHED_BUILT_INS=YesPlease",
    "NO_HOMEBREW=1", "CURL_LDFLAGS=-lcurl", "CURL_CONFIG=/usr/bin/true",
)

# Что бинарь вправе грузить: только системное. /opt/homebrew, /usr/local, @rpath —
# зависимость от машины раннера, у человека этого нет.
SYSTEM_DYLIB_PREFIXES = ("/usr/lib/", "/System/")

# Пакеты голоса и их тяжёлые зависимости — их версии сборка называет вслух:
# на красном круге CI иначе не видно, что именно встало в рантайм.
VOICE_PACKAGES = frozenset({"faster-whisper", "piper-tts", "ctranslate2", "onnxruntime",
                            "av", "numpy", "tokenizers", "huggingface-hub"})

# Записи Windows-архива, которые едут в Mac-сборку как есть. `app/` НЕ берём:
# пакет desk собирается заново из этой ветки — движок на Mac другой.
# `data/` в архиве пуста, её сборка создаёт сама. Лицензии влинкованного
# собираются заново — у Mac другие бинари. Документы пишутся заново — у Mac
# другой текст.
FROM_RELEASE_DIRS = ("tree/", "server/")
# Что Windows-сборка дописывает в tree/ ПОСЛЕ отбора (Apache §4): в счёте
# `tree_files` её паспорта этих двух нет.
TREE_ADDED = ("tree/LICENSE", "tree/NOTICE")

# Без чего архив — не архив. Проверяется по собранной папке перед zip: раньше
# половина Windows-сборок узнавала о неполноте у владельца.
REQUIRED_ROOT = (
    "Helene.app/Contents/MacOS/helene", "Helene.app/Contents/Info.plist",
    "Helene.app/Contents/Resources/icon.icns",
    "Helene Setup.app/Contents/MacOS/helene-setup", "Helene Setup.app/Contents/Info.plist",
    "helene-relay", "helene-svc", "helene-bridge", "helene-body",
    "runtime/bin/python3", "runtime/git/bin/git", "runtime/git/libexec/git-core",
    "runtime/git/share/git-core/templates", "runtime/git/COPYING",
    "app/deskapp.py", "app/desk.json", "app/static/index.html", "app/mobile/index.html",
    "app/localharness/runner.py", "app/localharness/body.py", "app/resources/SOUL.md",
    "tree", "data", "server", "licenses/rust/README.md", "licenses/body/README.md",
    "helene.json", "helene-build.json", "install.sh", "requirements.txt",
    "ПЕРВЫЙ-ЗАПУСК.md", "ОБНОВЛЕНИЕ.md", "КАК-УСТРОЕН-HELENE.md", "ЛИЦЕНЗИЯ.md",
    "ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md", "NOTICE",
)


# --- чистые функции (идут и на Windows, их держит tests/t_build_mac.py) --------------

def resolve_tags(from_release: str, tag: str, tree_path: str) -> tuple[str, str]:
    """(откуда дерево, как называется сборка).

    Дерево агента едет из Windows-архива выпуска `from_release` (по умолчанию —
    `RELEASE_TAG_DEFAULT`, если дерево не с диска). Имя сборки (`--tag`) — тег,
    которым подписываются архив и `install.sh`; по умолчанию тот же. Разные они
    бывают ТОЛЬКО в проверочных прогонах CI до выкладки: версия уже поднята, а
    архива нового выпуска ещё нет — дерево берётся из последнего существующего.
    Выкладывать такую сборку нельзя: это стережёт шаг выкладки в workflow, а в
    паспорте видно `source_release.tag` ≠ `version`."""
    tree_tag = (from_release or "").strip() or ("" if tree_path else RELEASE_TAG_DEFAULT)
    named = (tag or "").strip() or tree_tag
    return tree_tag, named


def version_from_tag(tag: str) -> str:
    """'v0.7.1' → '0.7.1'. Непонятный тег — отказ, а не нули (как parse_version в оболочке)."""
    m = re.fullmatch(r"[vV]?(\d+\.\d+\.\d+)", (tag or "").strip())
    if not m:
        raise SystemExit(f"тег выпуска не похож на версию: {tag!r} (ждём вида v0.7.1)")
    return m.group(1)


def asset_names(version: str) -> dict[str, str]:
    """Имена активов выпуска. Windows-архив — `Helene-<v>.zip`, наш — с суффиксом
    платформы: оболочка выбирает вложение по префиксу `Helene-`, и суффикс — то,
    по чему Mac и Windows отличат свой архив от чужого."""
    stem = f"{FOLDER}-{version}-{PLATFORM}-{ARCH}"
    return {
        "zip": stem + ".zip",
        "sha256": stem + ".zip.sha256",
        "install": "install.sh",
        "windows_zip": f"{FOLDER}-{version}.zip",
    }


def info_plist(kind: str, version: str) -> dict:
    """Info.plist бандла: имя по-французски, идентификатор — контракт четырёх."""
    b = BUNDLES[kind]
    plist = {
        "CFBundleName": b["name"],
        "CFBundleDisplayName": b["name"],
        "CFBundleIdentifier": b["identifier"],
        "CFBundleExecutable": b["exe"],
        "CFBundleIconFile": "icon",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "CFBundleInfoDictionaryVersion": "6.0",
        "LSMinimumSystemVersion": MACOS_MIN,
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHighResolutionCapable": True,
        # Окно и мастер ходят на 127.0.0.1 (канал, реле, локальные модели):
        # без этого ключа ATS режет незашифрованную петлю.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    }
    if kind == "shell":
        # Голосовые из окна — getUserMedia в WKWebView; без строки назначения
        # TCC не спрашивает, а убивает процесс при первом обращении к микрофону.
        plist["NSMicrophoneUsageDescription"] = "Hélène записывает голосовые сообщения для агента"
        # Окно живёт спрятанным в строку меню, а сторожа детей и проверка
        # обновлений в оболочке спят на thread::sleep — App Nap их замедлил бы.
        plist["LSAppNapIsDisabled"] = True
    return plist


def helene_json_mac() -> str:
    """Шаблон конфига поставки — тот же, что у Windows, с одним отличием:
    интерпретатор лежит в `runtime/bin/python3`. Правится поле, а не копия текста."""
    cfg = json.loads(bd.HELENE_JSON)
    cfg["python"] = "runtime/bin/python3"
    return json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"


def stamp_install_sh(text: str, tag: str, macos_min: str = MACOS_MIN) -> str:
    """Вписать в install.sh тег выпуска и минимум macOS. Строки обязаны быть:
    скрипт с чужой версией в шапке — это установка не той сборки."""
    out, n_tag = re.subn(r'^(HELENE_TAG_DEFAULT=)"[^"\n]*"', rf'\1"{tag}"', text,
                         count=1, flags=re.M)
    out, n_min = re.subn(r'^(HELENE_MACOS_MIN=)"[^"\n]*"', rf'\1"{macos_min.split(".")[0]}"',
                         out, count=1, flags=re.M)
    if n_tag != 1 or n_min != 1:
        raise SystemExit("в install.sh нет строк HELENE_TAG_DEFAULT=\"…\" и HELENE_MACOS_MIN=\"…\" — "
                         "сборке некуда вписать тег и минимум macOS")
    return out


def zip_members_for(names, top: str = FOLDER + "/",
                    dirs: tuple[str, ...] = FROM_RELEASE_DIRS) -> dict[str, str]:
    """Какие записи Windows-архива едут в сборку → относительный путь назначения.

    Только файлы под перечисленными папками, только внутрь: запись с `..` или
    абсолютным путём — отказ, а не «пропустить молча».
    """
    take: dict[str, str] = {}
    for name in names:
        if not name.startswith(top) or name.endswith("/"):
            continue
        rel = name[len(top):]
        if not rel.startswith(dirs):
            continue
        parts = rel.split("/")
        if rel.startswith("/") or any(p in ("", ".", "..") for p in parts):
            raise SystemExit(f"подозрительная запись в архиве выпуска: {name!r}")
        take[name] = rel
    return take


def core_summary(passport: dict) -> dict | None:
    """Ядро и слой из паспорта Windows-архива — без локальных путей владельца."""
    core = passport.get("core") if isinstance(passport, dict) else None
    if not isinstance(core, dict):
        return None
    out: dict = {}
    for key in ("core", "layer"):
        block = core.get(key)
        if isinstance(block, dict):
            out[key] = {k: block[k] for k in ("files", "digest", "head", "dirty") if k in block}
    drift = core.get("drift")
    if isinstance(drift, dict):
        out["drift"] = {k: v for k, v in drift.items() if not str(k).startswith("names_")}
    return out or None


def macos_floor_of_tags(tags) -> str:
    """Наибольший минимум macOS среди тегов колёс: 'cp314-cp314-macosx_14_0_arm64' → '14.0'.
    Пусто — ни одного macOS-тега (только py3-none-any)."""
    floor = (0, 0)
    for tag in tags:
        for m in re.finditer(r"macosx_(\d+)_(\d+)_", str(tag)):
            floor = max(floor, (int(m.group(1)), int(m.group(2))))
    return f"{floor[0]}.{floor[1]}" if floor != (0, 0) else ""


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text)) or (0,)


def foreign_dylibs(otool_text: str) -> list[str]:
    """Из вывода `otool -L` — библиотеки не из системы. Пусто — бинарь переносим.

    Первая строка вывода — имя самого файла (без отступа), дальше по строке на
    библиотеку с отступом и версиями в скобках."""
    out: list[str] = []
    for line in otool_text.splitlines():
        if not line.startswith(("\t", " ")):
            continue
        lib = line.strip().split(" (", 1)[0].strip()
        if lib and not lib.startswith(SYSTEM_DYLIB_PREFIXES):
            out.append(lib)
    return out


def vtool_minos(text: str) -> str:
    """Из `vtool -show-build` — minos: минимум macOS, под который слинкован бинарь."""
    m = re.search(r"^\s*minos\s+(\d+(?:\.\d+)*)\s*$", text, re.M)
    return m.group(1) if m else ""


# Mach-O: 64-битный, 32-битный и «толстый» (universal) заголовок, оба порядка
# байт. Толстый заголовок совпадает с магией class-файлов Java — отсекаем по
# числу архитектур: больше 32 их не бывает.
_MACH_O_THIN = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce")
_MACH_O_FAT = (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")


def is_mach_o(path: Path) -> bool:
    try:
        with Path(path).open("rb") as f:
            head = f.read(8)
    except OSError:
        return False
    if len(head) < 8:
        return False
    if head[:4] in _MACH_O_THIN:
        return True
    if head[:4] in _MACH_O_FAT:
        return int.from_bytes(head[4:8], "big") <= 32
    return False


def sign_targets(root: Path) -> list[Path]:
    """Что подписывать ad-hoc: оба бандла, свободные бинари корня (реле, мост,
    тело) и всё Mach-O в runtime/ (сам python, libpython, расширения из колёс,
    git и его libexec). Неподписанный arm64-бинарь macOS убивает при запуске, а
    подписи из чужих колёс бывают и валидными, и никакими — поэтому список, а
    решение по каждому — `codesign --verify` (см. codesign_all).

    ⚠ Подпись ad-hoc — это ещё и то, почему после КАЖДОГО обновления слетают
    разрешения TCC у тела («Запись экрана», «Универсальный доступ»): система
    помнит программу по подписи, а у ad-hoc она новая на каждую сборку.
    Документы поставки об этом говорят (ПЕРВЫЙ-ЗАПУСК.md, ОБНОВЛЕНИЕ.md)."""
    root = Path(root)
    found: list[Path] = []
    for b in BUNDLES.values():
        app = root / b["app"]
        if app.is_dir():
            found.append(app)
    for name in ROOT_BINARIES:
        exe = root / name
        if exe.is_file():
            found.append(exe)
    runtime = root / "runtime"
    if runtime.is_dir():
        for p in sorted(runtime.rglob("*")):
            if p.is_file() and not p.is_symlink() and is_mach_o(p):
                found.append(p)
    return found


def missing_in_root(root: Path) -> list[str]:
    """Чего нет в собранной папке из обязательного состава."""
    root = Path(root)
    return [rel for rel in REQUIRED_ROOT if not (root / rel).exists()]


def sha256_line(digest: str, name: str) -> str:
    """Формат `.sha256` — как у Windows-архива: `<digest> *<имя>`."""
    return f"{digest} *{name}\n"


def body_source_files(src: Path) -> list[Path]:
    """Файлы исходника тела по BODY_SRC_PATTERNS — отсортированно, без следов сборки."""
    src = Path(src)
    seen: set[Path] = set()
    for pat in BODY_SRC_PATTERNS:
        seen.update(p for p in src.glob(pat) if p.is_file())
    return sorted(seen)


def body_source_digest(src: Path) -> tuple[str, int]:
    """Отпечаток исходника тела -> (sha256, число файлов). Содержимое к LF — как
    `relay_src.digest`: зеркало на Windows и оригинал на проде иначе отличались
    бы каждым файлом."""
    files = body_source_files(src)
    lines = []
    for path in files:
        rel = path.relative_to(src).as_posix()
        raw = path.read_bytes().replace(b"\r\n", b"\n")
        lines.append(rel + " " + hashlib.sha256(raw).hexdigest())
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(), len(files)


def core_source(path: Path = CORE_SOURCE) -> dict:
    """`CORE-SOURCE.json`: откуда зеркало `praxis/` (коммит прода, дата, число файлов).
    Нет файла — пусто, и паспорт это скажет полем `commit: ""`, а не упадёт:
    тело собрано из того, что лежит в репозитории, и это факт сборки."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def body_summary(*, mirror: dict, crates: tuple[str, ...], digest: str, files: int,
                 exe_sha256: dict, target_dir: str) -> dict:
    """Поле `body` паспорта: коммит зеркала прода (`CORE-SOURCE.json`), крейты,
    отпечаток исходника, суммы бинарей. Darwin-ветки живут в этом репозитории
    поверх зеркала — их коммит есть в `git.desk` того же паспорта."""
    return {
        "source": "praxis/body",
        "commit": str(mirror.get("head") or ""),
        "mirror_taken_at": str(mirror.get("taken_at") or ""),
        "mirror_dirty": bool(mirror.get("dirty")),
        "crates": list(crates),
        "binaries": dict(BODY_BINARIES),
        "files": files,
        "digest": digest,
        "exe_sha256": dict(exe_sha256),
        "target_dir": target_dir,
        "built_utc": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def build_passport(*, version: str, declared: dict, desk_head: str, desk_dirty: bool,
                   tree_head: str, tree_dirty: bool, source_release: dict | None,
                   staged: dict, relay: dict | None, freeze: str, downloads: dict,
                   complete: bool, partial_reason: list[str], signed: int,
                   git_bundle: dict | None, macos_floor: str, body: dict | None = None) -> dict:
    """Паспорт той же формы, что у Windows (`build_dist.main`), плюс платформа."""
    return {
        "product": PRODUCT,
        "platform": PLATFORM,
        "arch": ARCH,
        "version": version,
        "built_utc": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "complete": complete,
        "partial_reason": partial_reason,
        "git": {"desk": desk_head, "desk_dirty": desk_dirty,
                "tree": tree_head, "tree_dirty": tree_dirty,
                "dirty": desk_dirty or tree_dirty},
        # Откуда дерево: тег, актив и его сумма, паспорт того архива (коротко).
        "source_release": source_release,
        "declared_versions": declared,
        "python": PY_VERSION,
        "python_build": f"python-build-standalone {PBS_TAG}",
        "git_bundle": git_bundle,
        # Что объявлено бандлам и что требуют колёса на деле — оба числа рядом.
        "macos_min": MACOS_MIN,
        "macos_floor_wheels": macos_floor,
        "tree_files": staged["tree_files"],
        "static": staged["static_digest"],
        "relay": relay,
        # Тело: коммит зеркала прода, крейты, отпечаток исходника, суммы бинарей.
        # None — сборка без тела (--skip-body), и тогда complete=false.
        "body": body,
        "core": (source_release or {}).get("core") if source_release else None,
        "desk": {"version": staged["desk"]["version"],
                 "flavor": staged["desk"]["flavor"],
                 "digest": staged["desk"]["digest"],
                 "files": len(staged["desk"]["files"]),
                 "skipped": [s["name"] for s in staged["desk"]["skipped"]]},
        "bundles": {b["app"]: b["identifier"] for b in BUNDLES.values()},
        "signed_adhoc": signed,
        "downloads": downloads,
        "packages": freeze.splitlines(),
    }


# --- тексты поставки --------------------------------------------------------------

FIRST_RUN_MAC = """# Hélène · первый запуск на macOS

Программа поставлена скриптом `install.sh` (или им же — из архива, скачанного
руками) в папку `~/Applications/Helene`. Открыть её: Finder → «Программы» в
твоей домашней папке → `Helene.app`, или из терминала —
`open ~/Applications/Helene/Helene.app`. Ярлык в Dock перетаскивается оттуда же.

Ставить и обновлять — от того пользователя, который вошёл на экран Mac, в его
Терминале, без `sudo` и без `su`. Мастер — окно: открывается он в сессии того,
кто за экраном, и от его имени, — под `sudo` это был бы не root, а после `su` в
другого пользователя мастер стартует от первого и не может прочитать папку
второго (живой случай: «you don't have permission to view it»). В этих трёх
случаях — `sudo`, `su`, нет графической сессии (ssh) — скрипт отказывает словами,
ничего не скачав. Прав администратора установка не требует; пароль администратора
программа спросит сама, когда он понадобится: для службы и действий с правами.

Подписи Developer ID у программы нет. Файлы, скачанные `curl`, карантина не
получают — поэтому установка идёт однострочником. Если архив скачан браузером,
не открывай из него бандлы руками: карантин висит на всём внутри, и Gatekeeper
убьёт не только мастер, но и `helene-relay` с `python3`, которых мастер зовёт.
Отдай архив скрипту — он снимает карантин при распаковке:

    curl -fsSL https://github.com/josephsteuerjr/praxis/releases/latest/download/install.sh | sh -s -- --from ~/Downloads/Helene-<версия>-macos-arm64.zip

(или `sh Helene/install.sh --from <архив>` из уже распакованной папки). Если
`Helene Setup.app` всё же открыт руками и macOS говорит «не удаётся проверить
разработчика»: на macOS 15 (Sequoia) и новее обхода правой кнопкой нет —
Настройки → «Конфиденциальность и безопасность» → внизу «Открыть всё равно»,
и так для каждого бинаря, который macOS остановит следом; на macOS 14 —
правая кнопка по бандлу → «Открыть». Первым открывается мастер
`Helene Setup.app`, а не `Helene.app`: программу в `~/Applications/Helene`
кладёт он.

1. При первой установке открывается мастер `Helene Setup.app`: имя агента и
   своё, **конституция** (текст, по которому агент будет жить, — его можно
   править прямо там), откуда приходит модель, режим, установка. Конституцию
   стоит прочитать: это единственное решение установки, которое потом меняет
   только сам агент.
2. Мастер копирует программу в `~/Applications/Helene`; на последней сцене
   нажми «Открыть». Агент представится первым — первое слово за ним.
3. Дальше всё меняется в самой программе: значок шестерёнки внизу слева —
   экран «Настройки» (модель и ключ, Telegram, телефон, автозапуск, адрес
   обновлений). `helene.json` руками править не нужно.

Все данные агента живут в `~/Applications/Helene/data`: память, дневник,
конституция, вход в ChatGPT. Перенос на другую машину — «Экспорт агента» в
Настройках, или скопировать `data/` и `helene.json` поверх свежей установки при
закрытой программе.

## Управление компьютером

Тул `computer` — окна, экран, клавиатура и мышь, файлы и процессы этой машины —
на Mac есть с 0.8.0. Включается в мастере (сцена «Управление компьютером») или
потом в «Настройках», карточка с тем же именем; умолчание — выключено: агент
водит твоей мышью по-настоящему, и слабая модель может не понять, что делает.
Тело (`helene-body`) и мост (`helene-bridge`) лежат в корне папки программы;
поднимает их движок рядом с собой, снаружи ограды, и они умирают вместе с ним.
Координаты — пункты экрана, как у клика; снимок экрана по умолчанию уменьшен
до них.

Системе нужны два разрешения. Без них тело не врёт, а отказывает словами
(снимок без «Записи экрана» — не обои, а отказ с подсказкой, куда идти):

- **«Запись экрана и системного звука»** — снимки экрана и заголовки чужих окон;
- **«Универсальный доступ»** — клавиатура, мышь и чтение дерева окна.

Где: Системные настройки → «Конфиденциальность и безопасность» → нужный
раздел → включить `Helene`. macOS спросит сама при первом обращении тела (в
окне на карточке есть и кнопки «Открыть настройки», ведущие прямо в раздел).
«Универсальный доступ» действует сразу; «Запись экрана» macOS применяет только
к заново запущенному процессу — после галочки перезапусти программу.

⚠ **После каждого обновления Hélène оба разрешения слетают.** Подписи
Developer ID у программы нет, подпись ad-hoc новая на каждую сборку, а система
помнит программу по подписи: старая строка в списке остаётся, но не действует.
Лечится руками, в тех же двух разделах: убрать `Helene` из списка кнопкой «−»
и добавить снова кнопкой «+» (бандл — `~/Applications/Helene/Helene.app`).
Пока это не сделано, тул отвечает «нет разрешения …» и называет путь.

## Работать без входа в систему (служба)

По умолчанию агент живёт, пока открыта программа: окно можно закрыть, значок
остаётся в строке меню, но выйдешь из учётной записи — агент закроется вместе с
сеансом. Служба это меняет: движок и реле поднимает `launchd`, и Telegram с
телефоном отвечают, когда окна нет вовсе.

Включается в мастере (переключатель «Работать без входа в систему») или потом в
«Настройках», карточка «Режим» → кнопка «Поставить службу». Система один раз
спросит пароль администратора: описание демона (`app.helene.svc`) кладётся в
`/Library/LaunchDaemons`, владельцем root. Больше под этими правами не делается
ничего — сам агент идёт от ТВОЕГО имени (`UserName` в описании), не от root.

Чего служба не даёт, и это свойство режима, а не поломка:

- **окон и экрана у неё нет.** Процесс вне твоего сеанса не видит рабочего
  стола, и разрешений TCC система ему не выдаст. Тул `computer` оживает, когда
  ты откроешь окно Helene: тело (`helene-body`) поднимает именно оно, а движок
  службы держит для него мост. Окно закрыто — тела нет, и `Настройки` про это
  говорят словами;
- **при включённом FileVault после перезагрузки не идёт ничего**, пока ты не
  войдёшь в систему первый раз: до этого диск заперт, и launchd нечего читать.

Журнал службы — `~/Applications/Helene/data/service.log`. Снять: та же карточка
«Режим» → «Снять службу» (снова пароль), или руками:

    sudo launchctl bootout system/app.helene.svc
    sudo rm /Library/LaunchDaemons/app.helene.svc.plist

Снятие программы мастером снимает и службу.

## Права администратора

Брокер прав на Mac — сама программа, без отдельной службы: когда агент просит
действие с правами администратора, окно показывает команду и «зачем», ты
подтверждаешь, и macOS спрашивает пароль своим диалогом (`osascript … with
administrator privileges`). Пароль видит только система, программе он не
достаётся, в журналы не попадает. Отказ в диалоге — отказ агенту словами.
Правил брандмауэра брокер на Mac не ставит: macOS сама спросит, разрешить ли
входящие соединения, когда включишь «Телефон».

## Что проверить руками

Живьём на Маке человеком это ещё не прогонялось — только на раннере GitHub
(там тело поднялось и подключилось, а разрешения — какие есть у раннера).
Если ты первый, пройди по порядку и напиши, что не так:

1. Включить «Управление компьютером» → два системных диалога (или кнопки
   «Открыть настройки» на карточке) → обе галочки стоят.
2. Ход «сделай снимок экрана» — приходит картинка с окнами, а не обои и не отказ.
3. Ход «прочитай окно Finder» — дерево с кнопками, списками и текстом.
4. Ход «нажми кнопку … в этом окне» — нажалась.
5. Брокер: «попроси права и создай папку в /usr/local/…» → диалог пароля →
   квитанция в чате с тем, что вышло.
6. После обновления программы: разрешения слетели — убрать и добавить `Helene`
   заново, повторить п. 2.
7. Служба, по шагам — это самое непроверенное место сборки:
   1) «Настройки» → «Режим» → «Поставить службу» → система спрашивает пароль
      администратора → строка «Служба работает»;
   2) закрыть окно совсем (значок в строке меню → «Выход»);
   3) написать агенту в Telegram — он обязан ответить с закрытым окном;
   4) открыть окно снова: оно работает КЛИЕНТОМ службы — своего движка не
      поднимает (в `helene.log` строка «подключаюсь без своих детей»), а тул
      `computer` оживает, потому что тело поднимает само окно;
   5) выйти из учётной записи и войти обратно — агент жив;
   6) «Снять службу» → ещё один пароль → «Службы нет», и после этого окно снова
      поднимает движок само.
8. Обновление СО СЛУЖБОЙ: обновить (однострочником в Терминале или «Проверить
   обновления») — система обязана спросить пароль администратора ДВА раза:
   мастер сначала снимает демон, потом ставит его заново. После обновления
   служба работает, агент отвечает в Telegram с закрытым окном, `data/` и
   настройки на месте. Если диалог не появился — служба осталась снятой:
   поставить её обратно из «Режима» и написать об этом в issues, это как раз
   то, чего мы не видели живьём.

Куда писать — issues репозитория https://github.com/josephsteuerjr/praxis/issues,
с версией из `helene-build.json`.

Чего в сборке для macOS нет (это не поломка, а состав):

- Intel-маков — только Apple Silicon (M1 и новее), macOS 14 и новее;
- подписи Developer ID, нотаризации и dmg — отсюда `install.sh` вместо образа
  и слетающие после обновления разрешения;
- правил брандмауэра — macOS сам спросит, разрешить ли программе входящие
  соединения, когда включишь «Телефон»;
- нулевой сессии: она есть только на Windows. Служба здесь идёт от твоего
  имени, а права администратора агент просит отдельно — диалогом пароля.

Ограда тула `shell` здесь — seatbelt (`sandbox-exec`) macOS: команды агента
видят рантайм и код, пишут только в его дом. Права администратора не нужны;
тело и брокер — снаружи ограды, это записано в «Системе» и в самой опции.

Когда выйдет новая версия — «Настройки» → «Проверить обновления», или тот же
однострочник установки: он увидит, что программа уже стоит, остановит её и
обновит поверх, не трогая `data/` и `helene.json`. Подробно — `ОБНОВЛЕНИЕ.md`.
"""

THIRD_PARTY_MAC = """# Лицензии третьих сторон (сборка для macOS)

Hélène собрана из открытых компонентов. Ниже — что именно едет в этой поставке
и на каких условиях. Полные тексты лежат внутри самой поставки:

- Rust-крейты, статически влинкованные в `Helene.app` и `Helene Setup.app` — в
  `licenses/rust/` (список и ссылки на тексты — `licenses/rust/README.md`;
  собирается при сборке из `Cargo.lock` обоих крейтов);
- крейты реле подписки ChatGPT (`helene-relay`) — в `licenses/relay/`;
- крейты моста и тела тула `computer` (`helene-bridge`, `helene-body`) — в
  `licenses/body/`;
- пакеты Python — в `runtime/lib/python3.14/site-packages/<пакет>.dist-info/`;
- CPython — `runtime/lib/python3.14/LICENSE.txt`.

## Программа и мастер (Rust)

Основное, что видно в исходниках; полный список зависимостей вместе с их
транзитивными — в `licenses/rust/README.md`.

- Tauri 2 и его плагины (single-instance, window-state) — MIT или Apache-2.0
- serde, serde_json — MIT или Apache-2.0
- ureq — MIT или Apache-2.0

## Интерфейс (TypeScript)

- motion — MIT
- qrcode — MIT
- Vite и TypeScript используются только при сборке и в поставку не входят

## Шрифты

- Source Serif 4 — SIL Open Font License 1.1
- Golos Text — SIL Open Font License 1.1
- PT Mono — SIL Open Font License 1.1
- Shantell Sans — SIL Open Font License 1.1

## Встроенный Python и пакеты

- CPython — Python Software Foundation License (`runtime/lib/python3.14/LICENSE.txt`).
  Сборка — python-build-standalone (Astral, лицензия сборочных скриптов — MIT
  или Apache-2.0; сами файлы интерпретатора — PSF)
- aiohttp, anthropic, openai, httpx, python-dotenv, pillow, pypdf, trafilatura,
  charset-normalizer, telethon, faster-whisper (ctranslate2, onnxruntime, av,
  numpy), piper-tts и их зависимости — по их `dist-info/`
- pip остаётся в `runtime/` сознательно: без него рантайм нельзя починить на
  машине пользователя, не пересобирая всю поставку

## Git (`runtime/git`)

`runtime/git` — Git __GIT_VERSION__, собранный из официального исходника
(kernel.org) без изменений, **GPL-2.0**. Отдельная программа: Hélène и агент её
вызывают (личный репозиторий агента в папке данных, снимки его правок), но не
линкуют, и на лицензию Hélène это не влияет. Текст лицензии — `runtime/git/COPYING`.

**Письменное предложение по GPL-2.0 §3(b).** Владелец Hélène обязуется в
течение трёх лет с момента получения вами этой поставки передать любому
обратившемуся полную машиночитаемую копию исходного кода этой сборки Git по
цене не выше стоимости передачи. Запрос — через issues репозитория
https://github.com/josephsteuerjr/praxis/issues с указанием версии поставки
(`helene-build.json`). Тот же исходник опубликован авторами:
https://mirrors.edge.kernel.org/pub/software/scm/git/ (`git-__GIT_VERSION__.tar.xz`,
сумма записана в паспорте сборки).

## Реле подписки ChatGPT

- `helene-relay` — MIT, исходники: https://github.com/josephsteuerjr/praxis-relay
  (коммит записан в паспорте сборки); тексты — `licenses/relay/`

## Тело тула `computer` (`helene-body`, `helene-bridge`)

Оба собраны из крейтов `praxis/body` репозитория Hélène (зеркало кода Праксис
плюс ветки для macOS): `praxis-body`, `praxis-bridge`, `praxis-body-protocol`;
коммит зеркала и отпечаток исходника записаны в паспорте сборки (`body`), сами
исходники — в репозитории https://github.com/josephsteuerjr/praxis (`praxis/body`).
Их зависимости (axum, tokio, rusqlite с bundled SQLite — Public Domain,
core-foundation и core-graphics — MIT или Apache-2.0, и остальные) перечислены
в `licenses/body/README.md`, тексты — рядом.

Условия самого кода тела — те же, что у дерева: Apache-2.0 (см. «Код агента»
ниже). ⚠ В `praxis/body/Cargo.toml` поле `license` этого workspace всё ещё
объявляет `PolyForm-Noncommercial-1.0.0` — это старая запись, оставшаяся с
тех пор, когда дерево ещё не было открыто под Apache-2.0; решение автора о
лицензии дерева (27.08.2026) её перекрывает, но поле в манифесте стоит
поправить в самом дереве (это правка хребта Праксис, здесь её не делают).

## Стороннее внутри дерева агента

- `tree/panel_static/3d-force-graph.min.js` — 3d-force-graph версии 1.80.0,
  https://github.com/vasturiano/3d-force-graph (MIT). В сборку упакованы
  three.js (MIT) и модули d3 (ISC). Сам минифицированный файл несёт только
  строку версии, без текстов лицензий: их условия — в перечисленных
  репозиториях. В продукте этот файл не используется: его читают серверные
  панели дерева агента, которых в Hélène нет

## Код агента

Дерево агента (`tree/`) — Apache-2.0. Полный текст лицензии — `tree/LICENSE`,
уведомление об авторстве — `tree/NOTICE` (и `NOTICE` в корне поставки).

Чего в этой поставке нет из Windows-состава: BusyBox (`runtime/bash.exe` — на
Mac свой `/bin/sh`) и MinGit (здесь git из исходника, выше). Служба есть —
`helene-svc`, тот же крейт, что `helene-svc.exe` на Windows, только вместо SCM
и трубы брокера у него демон launchd: брокер прав на Mac — сама оболочка через
системный диалог пароля, отдельного бинаря у него нет.
""".replace("__GIT_VERSION__", GIT_VERSION)


# --- команды ----------------------------------------------------------------------

def run(args, *, cwd: Path | None = None, timeout: int = 3600, env: dict | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    """Внешняя команда с дедлайном; вывод — в консоль. Падение — понятной строкой."""
    cmd = [str(a) for a in args]
    print("  $ " + " ".join(cmd if len(cmd) < 12 else cmd[:11] + ["…"]), flush=True)
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"команда не ответила за {timeout // 60} мин: {cmd[0]} …") from None
    except FileNotFoundError:
        raise SystemExit(f"нет команды {cmd[0]!r} — на этой машине сборка не пойдёт") from None
    if check and r.returncode != 0:
        raise SystemExit(f"команда завершилась с кодом {r.returncode}: {' '.join(cmd[:6])} …")
    return r


def capture(args, *, cwd: Path | None = None, timeout: int = 600, env: dict | None = None,
            check: bool = True) -> str:
    cmd = [str(a) for a in args]
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, timeout=timeout, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise SystemExit(f"команда не ответила за {timeout // 60} мин: {cmd[0]} …") from None
    except FileNotFoundError:
        raise SystemExit(f"нет команды {cmd[0]!r} — на этой машине сборка не пойдёт") from None
    if check and r.returncode != 0:
        raise SystemExit(f"команда завершилась с кодом {r.returncode}: {' '.join(cmd[:6])} …\n"
                         + (r.stderr or "").strip()[-800:])
    return r.stdout or ""


def _check_sum(url: str, path: Path) -> None:
    want = SHA256.get(url)
    if not want:
        return
    got = bd.sha256(path)
    if got != want:
        path.unlink(missing_ok=True)   # испорченный кэш не должен пережить прогон
        raise SystemExit(
            f"НЕ СОШЛАСЬ КОНТРОЛЬНАЯ СУММА: {path.name}\n"
            f"  адрес:  {url}\n"
            f"  ждали:  {want}\n"
            f"  скачали:{got}\n"
            "Файл подменён, повреждён или издатель выложил новую сборку.\n"
            "Разберись, ПОТОМ обнови SHA256 в build_mac.py — не наоборот.")


def fetch(url: str, dst: Path) -> None:
    """Скачать в кэш через `build_dist.fetch` (кэш, .part, таймаут) и сверить с
    НАШЕЙ суммой: суммы этого файла Windows-сборка не знает, и её проверка для
    наших адресов — пустая."""
    bd.fetch(url, dst)
    _check_sum(url, dst)


# --- дерево агента ------------------------------------------------------------------

def release_asset_digest(tag: str, name: str) -> str:
    """sha256 актива по данным GitHub (`assets[].digest`); '' — GitHub не сказал."""
    try:
        text = capture(["gh", "api", f"repos/{RELEASE_REPO}/releases/tags/{tag}",
                        "--jq", f'.assets[] | select(.name == "{name}") | .digest // ""'],
                       timeout=120, check=False)
    except SystemExit:
        return ""
    digest = text.strip().split("\n")[0].strip()
    return digest[len("sha256:"):] if digest.startswith("sha256:") else ""


def stage_from_release(out: Path, cache: Path, tag: str) -> dict:
    """Дерево (и server/) из Windows-архива выпуска — байт в байт то, что в проде."""
    version = version_from_tag(tag)
    names = asset_names(version)
    zip_path = cache / names["windows_zip"]
    if not zip_path.is_file():
        print(f"  качаю {names['windows_zip']} из выпуска {tag}…")
        cache.mkdir(parents=True, exist_ok=True)
        run(["gh", "release", "download", tag, "--repo", RELEASE_REPO,
             "--pattern", names["windows_zip"], "-D", cache, "--clobber"], timeout=1800)
    if not zip_path.is_file():
        raise SystemExit(f"в выпуске {tag} нет актива {names['windows_zip']}")
    digest = bd.sha256(zip_path)
    want = release_asset_digest(tag, names["windows_zip"])
    if want and want != digest:
        zip_path.unlink(missing_ok=True)
        raise SystemExit(f"{names['windows_zip']} в кэше не сходится с суммой актива на GitHub "
                         f"(ждали {want[:12]}…, лежит {digest[:12]}…) — кэш снесён, повтори")
    print(f"  {names['windows_zip']}: {zip_path.stat().st_size / 1e6:.1f} МБ, sha256 {digest[:12]}"
          + ("" if want else " (GitHub суммы актива не дал — сверить не с чем)"))

    with zipfile.ZipFile(zip_path) as zf:
        members = zf.namelist()
        try:
            passport = json.loads(zf.read(f"{FOLDER}/helene-build.json"))
        except KeyError:
            raise SystemExit(f"в {names['windows_zip']} нет {FOLDER}/helene-build.json — "
                             "это не архив поставки Hélène") from None
        if str(passport.get("version")) != version:
            raise SystemExit(f"паспорт архива говорит версия {passport.get('version')!r}, "
                             f"а тег — {tag}: не тот архив")
        plan = zip_members_for(members)
        for rel in ("tree", "server"):
            shutil.rmtree(out / rel, ignore_errors=True)
        counts = {"tree": 0, "server": 0}
        for member, rel in plan.items():
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            counts[rel.split("/")[0]] += 1
    (out / "data").mkdir(exist_ok=True)
    # Паспорт Windows считает файлы ОТБОРА (copy_tree); LICENSE и NOTICE в tree/
    # сборка дописывает после счёта. Считаем так же, чтобы числа сходились.
    tree_files = counts["tree"] - sum(1 for rel in TREE_ADDED if (out / rel).is_file())
    print(f"  tree/: {counts['tree']} файлов (отбор: {tree_files}), server/: {counts['server']}")
    if tree_files != int(passport.get("tree_files") or -1):
        raise SystemExit(f"в архиве {tree_files} файлов дерева, а его паспорт обещает "
                         f"{passport.get('tree_files')} — архив разошёлся сам с собой")
    if not (out / "tree" / "core" / "secrets.py").is_file():
        raise SystemExit("в дереве из архива нет core/secrets.py — секрет-гарду нечем сканировать")
    git = passport.get("git") or {}
    return {
        "tag": tag,
        "asset": names["windows_zip"],
        "sha256": digest,
        "tree_files": tree_files,
        "tree_head": str(git.get("tree") or ""),
        "tree_dirty": bool(git.get("tree_dirty")),
        "passport": {k: passport.get(k) for k in ("built_utc", "git", "python", "static", "desk")},
        "core": core_summary(passport),
    }


def stage_server_from_repo(src: Path, dst: Path) -> int:
    """server/ из этой ветки — тот же отбор, что у `build_dist.main` (там функция
    вложенная и снаружи недоступна): тексты с LF, остальное как есть."""
    count = 0
    dst.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.iterdir()):
        if item.name.startswith(".") or item.name == "__pycache__":
            continue
        if item.is_dir():
            count += stage_server_from_repo(item, dst / item.name)
        elif item.suffix in (".md", ".py", ".yml", ".yaml", ".txt", ".json") or item.name == "Dockerfile":
            bd.copy_text_lf(item, dst / item.name)
            count += 1
        else:
            shutil.copy2(item, dst / item.name)
            count += 1
    return count


def stage_from_tree(out: Path, live: Path) -> dict:
    """Дерево из рабочей копии — тем же отбором, что Windows-сборка (`copy_tree`)."""
    if not live.is_dir():
        raise SystemExit(f"нет дерева агента: {live}")
    shutil.rmtree(out / "tree", ignore_errors=True)
    copied, secrets = bd.copy_tree(live, out / "tree")
    print(f"  файлов дерева: {copied}")
    for rel in secrets:
        print(f"  ⨯ не поехало (форма секрета): {rel}")
    if copied < 100:
        raise SystemExit(f"дерево агента почти пустое: {copied} файлов из {live}")
    # Apache-2.0 §4(a) и §4(d): копия лицензии и NOTICE рядом с кодом — как у Windows.
    apache = (DESK / "installer" / "ЛИЦЕНЗИЯ.md").read_text(encoding="utf-8")
    body = apache.split("\n---\n", 1)[1].strip() if "\n---\n" in apache else apache
    (out / "tree" / "LICENSE").write_text(body + "\n", encoding="utf-8", newline="\n")
    bd.copy_text_lf(DESK / "installer" / "NOTICE", out / "tree" / "NOTICE")
    shutil.rmtree(out / "server", ignore_errors=True)
    n = stage_server_from_repo(DESK / "server", out / "server")
    print(f"  server/: {n} файлов")
    (out / "data").mkdir(exist_ok=True)
    tree_head, tree_dirty = bd._git_field(live, "дерево агента")
    return {"tree_files": copied, "tree_head": tree_head, "tree_dirty": tree_dirty}


# --- рантайм ------------------------------------------------------------------------

def deps() -> list[str]:
    """Зависимости поставки: дерево + голос + пакет desk вида macos. Вид объявляет
    C в `deskpkg.MACOS`; пока его нет — отказ словами, а не AttributeError."""
    flavor = getattr(deskpkg, "MACOS", None)
    if flavor is None:
        raise SystemExit("deskpkg не знает вида «macos» (deskpkg.MACOS) — состав пакета desk "
                         "для Mac ещё не объявлен, собирать нечего")
    return list(bd.TREE_DEPS) + list(bd.VOICE_DEPS) + deskpkg.requirements(flavor)


def runtime_python(out: Path) -> Path:
    return out / "runtime" / "bin" / "python3"


def stage_runtime(out: Path, cache: Path) -> None:
    """CPython из python-build-standalone + зависимости колёсами."""
    runtime = out / "runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    tgz = cache / PBS_NAME
    fetch(PBS_URL, tgz)
    work = cache / "pbs-unpack"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    # Системный tar, а не tarfile: архив несёт симлинки (bin/python3 → python3.14)
    # и права на исполнение, и bsdtar кладёт их как есть.
    run(["tar", "-xzf", tgz, "-C", work], timeout=600)
    src = work / "python"
    if not (src / "bin" / "python3.14").is_file():
        raise SystemExit(f"в {PBS_NAME} нет python/bin/python3.14 — astral изменил раскладку")
    out.mkdir(parents=True, exist_ok=True)
    os.rename(src, runtime)
    shutil.rmtree(work, ignore_errors=True)
    py = runtime_python(out)
    said = capture([py, "--version"], timeout=120).strip()
    if PY_VERSION not in said:
        raise SystemExit(f"рантайм назвался {said!r}, а ждали {PY_VERSION}")
    print(f"  runtime/: {said}")
    req = out / "requirements.txt"
    req.write_text("\n".join(deps()) + "\n", encoding="utf-8", newline="\n")
    print("  ставлю зависимости…")
    pip = [py, "-m", "pip", "install", "-q", "--no-warn-script-location"]
    # setuptools/wheel — на время: исходник pyaes без них не собирается; после —
    # снимаем, пользователю они не нужны (тот же приём, что у Windows-сборки).
    run([*pip, "setuptools", "wheel"], timeout=1800)
    only = ["--only-binary=:all:"] + [f"--no-binary={name}" for name in SDIST_OK]
    run([*pip, *only, "-r", req], timeout=3600)
    run([py, "-m", "pip", "uninstall", "-y", "-q", "setuptools", "wheel"], check=False, timeout=600)
    run([py, "-m", "pip", "cache", "purge", "-q"], check=False, timeout=600)


def smoke_runtime(out: Path) -> str:
    """Рантайм обязан импортировать то, ради чего собран (тот же список, что у
    Windows — `build_dist.SMOKE_IMPORTS`). -> pip freeze для паспорта."""
    py = runtime_python(out)
    if not py.is_file():
        raise SystemExit(f"нет рантайма: {py}\n"
                         "с --skip-runtime рантайм должен уже лежать в папке сборки")
    code = "import " + ", ".join(bd.SMOKE_IMPORTS)
    r = subprocess.run([str(py), "-c", code], capture_output=True, text=True, timeout=300,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("рантайм не импортирует свои зависимости:\n" + (r.stderr or "").strip())
    return capture([py, "-m", "pip", "freeze"], timeout=300).strip()


def runtime_wheel_tags(out: Path):
    """Теги всех поставленных колёс — из `*.dist-info/WHEEL`."""
    site = out / "runtime" / "lib" / f"python{PY_VERSION.rsplit('.', 1)[0]}" / "site-packages"
    for wheel in sorted(site.glob("*.dist-info/WHEEL")):
        for line in wheel.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Tag:"):
                yield line[4:].strip()


def runtime_macos_floor(out: Path) -> str:
    """Минимум macOS, который на деле требуют колёса; больше объявленного — отказ."""
    floor = macos_floor_of_tags(runtime_wheel_tags(out))
    if floor and version_tuple(floor) > version_tuple(MACOS_MIN):
        raise SystemExit(f"колёса рантайма требуют macOS {floor}, а бандл объявляет "
                         f"LSMinimumSystemVersion {MACOS_MIN}: подними MACOS_MIN или подбери "
                         "версии пакетов — обещать то, что не загрузится, нельзя")
    print(f"  минимум macOS по колёсам: {floor or 'не задан (только чистый Python)'}; "
          f"объявлено {MACOS_MIN}")
    return floor


def stage_git_bundle(out: Path, cache: Path) -> dict:
    """git из исходника → runtime/git (bin/, libexec/git-core/, share/git-core/templates)."""
    txz = cache / GIT_NAME
    fetch(GIT_URL, txz)
    src_root = cache / "git-src"
    shutil.rmtree(src_root, ignore_errors=True)
    src_root.mkdir(parents=True)
    run(["tar", "-xJf", txz, "-C", src_root], timeout=600)
    src = src_root / f"git-{GIT_VERSION}"
    if not (src / "Makefile").is_file():
        raise SystemExit(f"в {GIT_NAME} нет git-{GIT_VERSION}/Makefile — раскладка исходника изменилась")
    ncpu = capture(["sysctl", "-n", "hw.ncpu"], timeout=60, check=False).strip() or "4"
    # Заголовки curl — из SDK, а не из того, что `curl-config` найдёт в PATH:
    # линкуемся с системной libcurl, значит и объявления должны быть её.
    sdk = capture(["xcrun", "--show-sdk-path"], timeout=120).strip()
    if not sdk or not Path(sdk).is_dir():
        raise SystemExit("xcrun --show-sdk-path не назвал SDK — нужны Xcode Command Line Tools")
    make_vars = [*GIT_MAKE_VARS, f"CURL_CFLAGS=-I{sdk}/usr/include"]
    # Целевой минимум — MACOS_MIN, а не версия хоста: без переменной clang
    # линкует под macOS раннера (15), и на 14 это «Symbol not found».
    env = {**os.environ, "MACOSX_DEPLOYMENT_TARGET": MACOS_MIN}
    print(f"  собираю git {GIT_VERSION} ({ncpu} потоков, minos {MACOS_MIN}, SDK {sdk})…")
    run(["make", f"-j{ncpu}", *make_vars], cwd=src, timeout=3600, env=env)
    dest = out / "runtime" / "git"
    shutil.rmtree(dest, ignore_errors=True)
    run(["make", "install", f"DESTDIR={dest}", *make_vars], cwd=src, timeout=1800, env=env)
    git = dest / "bin" / "git"
    if not git.is_file():
        raise SystemExit(f"после make install нет {git}")
    # GPL-2.0 требует текст лицензии рядом с программой; `make install` его не
    # кладёт, а ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md обещает runtime/git/COPYING.
    if not (src / "COPYING").is_file():
        raise SystemExit(f"в исходнике git нет COPYING ({src}) — лицензию положить не из чего")
    shutil.copy2(src / "COPYING", dest / "COPYING")
    said = capture([git, "--version"], timeout=120).strip()
    if "git version" not in said:
        raise SystemExit(f"runtime/git не отвечает на --version: {said!r}")
    # Переносимость по библиотекам: только /usr/lib и /System. Чужая строка в
    # otool — подцепился Homebrew (curl или iconv), у человека это не загрузится.
    for rel in ("bin/git", "libexec/git-core/git-remote-http"):
        p = dest / rel
        if not p.is_file():
            raise SystemExit(f"после make install нет runtime/git/{rel}")
        foreign = foreign_dylibs(capture(["otool", "-L", p], timeout=120))
        if foreign:
            raise SystemExit(f"runtime/git/{rel} слинкован с чужими библиотеками: "
                             f"{', '.join(foreign)} — это Homebrew раннера, у человека его нет")
    minos = vtool_minos(capture(["vtool", "-show-build", git], timeout=120))
    if not minos:
        raise SystemExit("vtool -show-build не назвал minos у runtime/git/bin/git")
    if version_tuple(minos) > version_tuple(MACOS_MIN):
        raise SystemExit(f"runtime/git слинкован под macOS {minos}, а обещано {MACOS_MIN}: "
                         "MACOSX_DEPLOYMENT_TARGET не сработал")
    # Переносимость: exec-path обязан лежать ВНУТРИ runtime/git (RUNTIME_PREFIX),
    # и `git init` обязан находить шаблоны — без PATH и без чужого git.
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(cache), "PATH": "/usr/bin:/bin"}
    exec_path = capture([git, "--exec-path"], timeout=120, env=env).strip()
    if not Path(exec_path).resolve().is_relative_to(dest.resolve()):
        raise SystemExit(f"runtime/git смотрит за свой exec-path наружу: {exec_path} — "
                         "RUNTIME_PREFIX не сработал")
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        capture([git, "init", "-q", tmp], timeout=120, env=env)
        if not (Path(tmp) / ".git" / "HEAD").is_file():
            raise SystemExit("runtime/git init не создал репозиторий")
    files = [p for p in dest.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files if not p.is_symlink())
    shutil.rmtree(src_root, ignore_errors=True)
    print(f"  runtime/git ({said}) положен: {len(files)} файлов, {size / 1e6:.1f} МБ, minos {minos}")
    return {"version": GIT_VERSION, "files": len(files), "bytes": size, "exec_path": "libexec/git-core",
            "minos": minos, "license": "COPYING"}


# --- фронты, Rust, реле -------------------------------------------------------------

# Все пять фронтов, а не три, что едут в Mac-архив (окно, телефон, мастер).
# Окно Пульта и мини-апп в поставку не входят, но стенд пакета desk
# (`tests/t_deskpkg.py`) собирает пакет КАЖДОГО вида, и вид `server` без
# `pult/dist` и `miniapp/dist` красный — это минута сборки, а не полусборка.
FRONTS = ("app", "mobile", "setup/ui", "pult", "miniapp")


def build_fronts() -> None:
    for rel in FRONTS:
        print(f"  {rel}:")
        run(["npm", "ci", "--no-audit", "--no-fund"], cwd=DESK / rel, timeout=1800)
        run(["npm", "run", "build"], cwd=DESK / rel, timeout=1800)


def rust_binary(kind: str) -> Path:
    b = BUNDLES[kind]
    return DESK / b["crate"] / "target" / "release" / b["exe"]


def build_rust() -> None:
    for kind, b in BUNDLES.items():
        print(f"  {b['crate']}:")
        run(["cargo", "build", "--release", "--features", "custom-protocol"],
            cwd=DESK / b["crate"], timeout=5400)
        if not rust_binary(kind).is_file():
            raise SystemExit(f"после cargo build нет {rust_binary(kind)}")


def build_svc() -> Path:
    """Собрать службу (крейт `desk/svc`) и вернуть путь к бинарю.

    Отдельно от `build_rust`: там Tauri-бандлы с фичей `custom-protocol`, а
    здесь голый cargo. Своего `--target-dir` не задаём — крейт собирается в
    `desk/svc/target`, как и на Windows, и кэш раннера (Swatinem/rust-cache)
    его подхватывает.
    """
    print(f"  {SVC_CRATE.name}:")
    run(["cargo", "build", "--release"], cwd=SVC_CRATE, timeout=5400)
    exe = SVC_CRATE / "target" / "release" / SVC_BIN
    if not exe.is_file():
        raise SystemExit(f"после cargo build нет {exe}")
    return exe


def relay_source(cache: Path) -> Path:
    """Клон реле на нужном коммите — в кэше, чтобы повторный прогон не качал заново."""
    src = cache / "praxis-relay"
    if not (src / ".git").is_dir():
        shutil.rmtree(src, ignore_errors=True)
        run(["git", "clone", "--quiet", RELAY_REPO, src], timeout=1800)
    head = capture(["git", "-C", src, "rev-parse", "HEAD"], timeout=120).strip()
    if head != RELAY_COMMIT:
        run(["git", "-C", src, "fetch", "--quiet", "origin", RELAY_COMMIT], timeout=1800)
        run(["git", "-C", src, "checkout", "--quiet", "--detach", RELAY_COMMIT], timeout=300)
        head = capture(["git", "-C", src, "rev-parse", "HEAD"], timeout=120).strip()
    if head != RELAY_COMMIT:
        raise SystemExit(f"реле стоит на {head[:12]}, а нужен {RELAY_COMMIT[:12]}")
    return src


def build_relay(cache: Path, skip_rust: bool) -> tuple[Path, dict]:
    src = relay_source(cache)
    exe = src / "target" / "release" / RELAY_BIN
    if not skip_rust:
        run(["cargo", "build", "--release"], cwd=src, timeout=5400)
    if not exe.is_file():
        raise SystemExit(f"нет собранного реле: {exe}")
    total, files = relay_src.digest(src)
    version = deskpkg._cargo_version(src / "Cargo.toml")
    print(f"  реле {version} @ {RELAY_COMMIT[:7]}: {len(files)} файлов исходника, отпечаток {total[:12]}")
    return exe, {
        "repo": RELAY_REPO, "commit": RELAY_COMMIT, "version": version,
        "digest": total, "files": len(files),
        "exe_sha256": bd.sha256(exe),
        "built_utc": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# --- тело -----------------------------------------------------------------------------

def body_target_dir(cache: Path) -> Path:
    """Куда cargo кладёт бинари тела: в кэш сборки, не в `praxis/body/target`
    (исходник — зеркало прода, следов сборки в нём быть не должно). Workflow
    гоняет `cargo test` тела с тем же `--target-dir`, чтобы зависимости
    компилировались один раз и кэшировались вместе с кэшем сборки."""
    return Path(cache) / "body-target"


def build_body(cache: Path, skip_rust: bool) -> tuple[dict[str, Path], dict]:
    """Мост и тело из `praxis/body` этого репозитория -> {имя в поставке: путь к
    бинарю}, запись для паспорта. Клонировать нечего: исходник лежит рядом."""
    src = BODY_SRC
    if not (src / "Cargo.toml").is_file():
        raise SystemExit(f"нет исходника тела: {src / 'Cargo.toml'} — зеркало praxis/ без body/")
    for crate in BODY_CRATES:
        if not (src / "crates" / crate / "Cargo.toml").is_file():
            raise SystemExit(f"в {src} нет крейта {crate} — тело собирать не из чего")
    target = body_target_dir(cache)
    if not skip_rust:
        packages = [arg for crate in BODY_CRATES for arg in ("-p", crate)]
        run(["cargo", "build", "--release", *packages, "--target-dir", target], cwd=src, timeout=5400)
    exes: dict[str, Path] = {}
    for crate, name in BODY_BINARIES.items():
        exe = target / "release" / crate
        if not exe.is_file():
            raise SystemExit(f"нет собранного бинаря тела: {exe}"
                             + (" (с --skip-rust он должен уже лежать в кэше)" if skip_rust else ""))
        # Бинарь обязан хотя бы запуститься на этой машине: clap отвечает на
        # --help кодом 0, а «Killed: 9» или чужая dylib видны уже здесь.
        capture([exe, "--help"], timeout=120)
        exes[name] = exe
    digest, files = body_source_digest(src)
    mirror = core_source()
    info = body_summary(mirror=mirror, crates=BODY_CRATES, digest=digest, files=files,
                        exe_sha256={name: bd.sha256(exe) for name, exe in exes.items()},
                        target_dir=str(target))
    print(f"  тело из praxis/body @ зеркало {info['commit'][:7] or '(CORE-SOURCE.json нет)'}: "
          f"{files} файлов исходника, отпечаток {digest[:12]}")
    return exes, info


# --- бандлы и подпись ---------------------------------------------------------------

def make_icns(png: Path, dest: Path, work: Path) -> None:
    if not png.is_file():
        raise SystemExit(f"нет значка: {png}")
    iconset = work / (dest.stem + ".iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)
    for name, side in ICONSET:
        capture(["sips", "-z", str(side), str(side), png, "--out", iconset / f"{name}.png"],
                timeout=120)
    capture(["iconutil", "-c", "icns", iconset, "-o", dest], timeout=120)
    shutil.rmtree(iconset, ignore_errors=True)
    if not dest.is_file():
        raise SystemExit(f"iconutil не сделал {dest}")


def make_bundle(out: Path, kind: str, version: str, exe_src: Path, work: Path) -> Path:
    b = BUNDLES[kind]
    app = out / b["app"]
    shutil.rmtree(app, ignore_errors=True)
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    exe = contents / "MacOS" / b["exe"]
    shutil.copy2(exe_src, exe)
    exe.chmod(0o755)
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump(info_plist(kind, version), f, sort_keys=False)
    (contents / "PkgInfo").write_bytes(b"APPL????")
    make_icns(b["icon"], contents / "Resources" / "icon.icns", work)
    print(f"  {b['app']}: {b['identifier']}, {exe.stat().st_size / 1e6:.1f} МБ")
    return app


def codesign_all(out: Path) -> int:
    """Ad-hoc подпись всего, что не проходит `codesign --verify`. Валидные подписи
    (из колёс, из python-build-standalone) не переписываются."""
    signed = 0
    for target in sign_targets(out):
        ok = subprocess.run(["codesign", "--verify", "--strict", str(target)],
                            capture_output=True, text=True, timeout=300).returncode == 0
        if ok:
            continue
        args = ["codesign", "--force", "--sign", "-"]
        if target.suffix == ".app":
            args.append("--deep")
        r = subprocess.run([*args, str(target)], capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise SystemExit(f"codesign отказал на {target.relative_to(out)}:\n{(r.stderr or '').strip()}")
        signed += 1
    for b in BUNDLES.values():
        r = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(out / b["app"])],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise SystemExit(f"{b['app']} после подписи не проходит проверку: {(r.stderr or '').strip()}")
    print(f"  подписано ad-hoc: {signed}")
    return signed


# --- лицензии реле и тела ---------------------------------------------------------

def license_texts(dest: Path, crates, registry: Path) -> tuple[list[str], list[str]]:
    """Тексты лицензий крейтов из локального реестра cargo -> (строки указателя,
    крейты без файла лицензии). Тот же приём, что `build_dist.collect_rust_licenses`:
    одинаковые тексты кладутся в `dest/texts/` по одному разу, по sha256."""
    texts = Path(dest) / "texts"
    texts.mkdir(parents=True, exist_ok=True)
    index: list[str] = []
    missing: list[str] = []
    for name, ver in sorted(set(crates)):
        crate_dir = Path(registry) / f"{name}-{ver}"
        files = []
        if crate_dir.is_dir():
            for glob in bd.LICENSE_FILE_GLOBS:
                files += [f for f in crate_dir.glob(glob) if f.is_file()]
        if not files:
            missing.append(f"{name} {ver}")
            continue
        refs = []
        for f in sorted(set(files)):
            raw = f.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()[:16]
            target = texts / f"{digest}.txt"
            if not target.exists():
                target.write_bytes(raw)
            refs.append(f"[{f.name}](texts/{digest}.txt)")
        index.append(f"- **{name} {ver}** — " + ", ".join(refs))
    return index, missing


def _missing_note(missing: list[str]) -> list[str]:
    if not missing:
        return []
    return ["Без файла лицензии в исходниках крейта (лицензия объявлена полем "
            "`license` в его Cargo.toml): " + ", ".join(missing) + ".", ""]


def collect_relay_licenses(out: Path, src: Path, allow_partial: bool) -> int:
    """Тексты лицензий крейтов реле — тем же приёмом, что `collect_rust_licenses`,
    только по чужому Cargo.lock; плюс LICENSE и NOTICE самого реле."""
    dest = out / "licenses" / "relay"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("LICENSE", "NOTICE"):
        if (src / name).is_file():
            bd.copy_text_lf(src / name, dest / name)
    crates = bd._lock_crates(src / "Cargo.lock")
    if not crates:
        raise SystemExit(f"не прочитался Cargo.lock реле ({src}) — лицензии его крейтов собрать не из чего")
    registry = bd._cargo_registry_src()
    if registry is None:
        if not allow_partial:
            raise SystemExit("нет локального реестра cargo — тексты лицензий крейтов реле собрать не из чего")
        print("  ⚠ нет реестра cargo: лицензии крейтов реле не собраны")
        return 0
    index, missing = license_texts(dest, crates, registry)
    head = [
        "# Лицензии Rust-крейтов, влинкованных в helene-relay",
        "",
        f"Реле — praxis-relay ({RELAY_REPO}, коммит {RELAY_COMMIT[:7]}), MIT: `LICENSE` и `NOTICE` рядом.",
        f"Собрано автоматически при сборке из его Cargo.lock ({len(set(crates))} крейтов).",
        "Одинаковые тексты лежат в `texts/` по одному разу; ссылки ниже ведут на них.",
        "",
    ] + _missing_note(missing)
    (dest / "README.md").write_text("\n".join(head + sorted(index)) + "\n",
                                    encoding="utf-8", newline="\n")
    return len(index)


def body_license_head(n_crates: int, mirror_head: str, missing: list[str]) -> list[str]:
    """Шапка `licenses/body/README.md`: чьи крейты, откуда, на каких условиях.
    Слова о лицензии тела — те же, что в ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md у Windows:
    код тела — Apache-2.0 по решению автора, поле `license` в манифесте
    устарело; молчать об этом нельзя, писать PolyForm как факт — тоже."""
    return [
        "# Лицензии Rust-крейтов, влинкованных в helene-bridge и helene-body",
        "",
        "Мост и тело тула `computer` собраны из крейтов `praxis/body` репозитория Hélène "
        f"(зеркало кода Праксис, коммит прода {mirror_head[:7] or '?'}, плюс ветки для macOS): "
        "`praxis-bridge`, `praxis-body`, `praxis-body-protocol`.",
        "Условия самого кода тела — те же, что у дерева агента: Apache-2.0 (`tree/LICENSE`, "
        "`NOTICE` в корне поставки); поле `license = \"PolyForm-Noncommercial-1.0.0\"` в "
        "`praxis/body/Cargo.toml` — старая запись до открытия дерева под Apache-2.0, решение "
        "автора (27.08.2026) её перекрывает. Подробнее — `ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md`.",
        "",
        f"Зависимости собраны автоматически при сборке из его Cargo.lock ({n_crates} крейтов).",
        "Одинаковые тексты лежат в `texts/` по одному разу; ссылки ниже ведут на них.",
        "",
    ] + _missing_note(missing)


def mirror_head(info: dict | None) -> str:
    """Коммит зеркала прода — из записи паспорта тела (`commit`), из
    `CORE-SOURCE.json` (`head`) или пусто; шапка лицензий тогда пишет «?»."""
    info = info or {}
    return str(info.get("commit") or info.get("head") or "")


def collect_body_licenses(out: Path, src: Path, allow_partial: bool,
                          body: dict | None = None) -> int:
    """Тексты лицензий крейтов моста и тела — по `praxis/body/Cargo.lock`, в
    `licenses/body/` (у Windows они слиты в `licenses/rust/` — здесь отдельно,
    как у реле: у тела свой исходник и своя судьба). Собственные крейты
    workspace в `Cargo.lock` без `source` — их лицензию называет шапка.
    `body` — запись паспорта от `build_body` (коммит зеркала берётся из неё)."""
    dest = out / "licenses" / "body"
    dest.mkdir(parents=True, exist_ok=True)
    crates = bd._lock_crates(src / "Cargo.lock")
    if not crates:
        raise SystemExit(f"не прочитался Cargo.lock тела ({src}) — лицензии моста и тела собрать не из чего")
    registry = bd._cargo_registry_src()
    if registry is None:
        if not allow_partial:
            raise SystemExit("нет локального реестра cargo — тексты лицензий крейтов тела собрать не из чего")
        print("  ⚠ нет реестра cargo: лицензии крейтов тела не собраны")
        return 0
    index, missing = license_texts(dest, crates, registry)
    head = body_license_head(len(set(crates)), mirror_head(body) or mirror_head(core_source()), missing)
    (dest / "README.md").write_text("\n".join(head + sorted(index)) + "\n",
                                    encoding="utf-8", newline="\n")
    return len(index)


# --- стенды -------------------------------------------------------------------------

def run_stands(out: Path, skip: bool) -> None:
    """Стенды питона и окна — рантаймом ЭТОЙ сборки: это и проверка продукта, и
    дымовой тест рантайма разом. Стенды Rust гоняет workflow отдельно (`cargo
    test` в shell и setup): `run_all.py --rust` знает и про svc, а его на Mac нет."""
    if skip:
        print("стенды: ПРОПУЩЕНЫ (--skip-tests) — это отладка, не выпуск")
        return
    runner = DESK / "tests" / "run_all.py"
    if not runner.is_file():
        raise SystemExit(f"нет прогона стендов: {runner}")
    # Дерево агента стенды ищут через `layout.tree()`: HELENE_TREE_SRC, иначе
    # сосед `live/` рядом с репозиторием. На раннере соседа нет — дерево уже
    # лежит в сборке, его и называем; остальная среда — как есть.
    tree = out / "tree"
    if not (tree / "agent.py").is_file():
        raise SystemExit(f"стендам нужно дерево агента, а в {tree} его нет — сперва шаг «дерево агента»")
    # PYTHONPATH — мост для стенда, который к `layout.tree()` не ходит, а
    # вставляет в sys.path соседа `../live` буквально (`tests/t_seed_git.py`):
    # без него `self_model` из дерева не найти. Имена верхнего уровня дерева
    # (123) со стандартной библиотекой и модулями desk не пересекаются
    # (проверено 19.09), а пути desk стенды ставят в sys.path первыми.
    inherited = os.environ.get("PYTHONPATH", "")
    env = {**os.environ, "HELENE_TREE_SRC": str(tree), "PYTHONUTF8": "1",
           "PYTHONPATH": str(tree) + (os.pathsep + inherited if inherited else "")}
    print(f"стенды: питон и окно — рантаймом сборки, дерево {tree}…")
    done = subprocess.run([str(runtime_python(out)), str(runner)], cwd=str(DESK), env=env)
    if done.returncode != 0:
        raise SystemExit("стенды красные — сборка остановлена. Чинить, а не собирать.")


# --- главное ------------------------------------------------------------------------

#: Что в корне поставки — от тела: с `--skip-body` этих записей не ждём, а
#: пишем в паспорт `complete: false` с причиной.
BODY_ROOT_ENTRIES = tuple(BODY_BINARIES.values()) + ("licenses/body/README.md",)


def arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="сборка Hélène для macOS (Apple Silicon)")
    parser.add_argument("--out", default=str(DESK / "installer" / "build-mac"))
    parser.add_argument("--from-release", default="", metavar="TAG",
                        help=f"взять дерево агента из Windows-архива этого выпуска "
                             f"(по умолчанию {RELEASE_TAG_DEFAULT}, если не задан --tree)")
    parser.add_argument("--tag", default="", metavar="TAG",
                        help="тег, которым называются архив и install.sh (по умолчанию = --from-release); "
                             "другой — только для проверочных прогонов CI до выкладки")
    parser.add_argument("--tree", default="", metavar="PATH",
                        help="взять дерево агента из рабочей копии (отладка; отбор как у build_dist)")
    parser.add_argument("--skip-runtime", action="store_true",
                        help="не пересобирать runtime/ (python, пакеты, git) — только для отладки")
    parser.add_argument("--skip-rust", action="store_true",
                        help="не звать cargo: взять бинари из target/release (и тело из кэша) как есть")
    parser.add_argument("--skip-body", action="store_true",
                        help="собрать без тела (helene-body, helene-bridge): отладочная полусборка, "
                             "паспорт получит complete=false; в выпуске — никогда")
    parser.add_argument("--skip-tests", action="store_true",
                        help="не гонять стенды (отладка); в выпуске — никогда")
    parser.add_argument("--allow-partial", action="store_true",
                        help="разрешить неполную сборку (отладка); попадёт в паспорт")
    return parser


def main() -> None:
    args = arg_parser().parse_args()
    if args.from_release and args.tree:
        raise SystemExit("--from-release и --tree вместе не бывают: дерево либо из выпуска, либо с диска")
    if sys.platform != "darwin":
        raise SystemExit("эта сборка идёт только на macOS (sips, iconutil, codesign, ditto); "
                         "на других системах из неё импортируются только чистые функции")

    version, declared = deskpkg.product_version()
    tree_tag, tag = resolve_tags(args.from_release, args.tag, args.tree)
    if tag and version_from_tag(tag) != version:
        raise SystemExit(f"ветка объявляет версию {version}, а сборку просят назвать {tag}: "
                         "имя сборки Mac обязано совпадать с версией ветки")
    if tree_tag and tree_tag != tag:
        print(f"⚠ дерево агента — из выпуска {tree_tag}, а сборка называется {tag}: это ПРОВЕРОЧНЫЙ "
              "прогон до выкладки; в выпуск такой архив не кладётся (паспорт: source_release.tag)")
    out = Path(args.out).resolve() / FOLDER
    cache = Path(args.out).resolve() / "cache"
    names = asset_names(version)
    print(f"{PRODUCT} {version} для {PLATFORM}/{ARCH}")
    print(f"сборка -> {out}")
    if args.allow_partial:
        print("⚠ --allow-partial: это ОТЛАДОЧНАЯ полусборка, не выпуск")

    # Корень чистится целиком, кроме рантайма (дорого) — как ROOT_KEEP у Windows.
    if out.exists():
        for item in out.iterdir():
            if item.name == "runtime" and args.skip_runtime:
                continue
            (shutil.rmtree(item) if item.is_dir() else item.unlink())
    out.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    # Сеть — до долгой работы: недоступный адрес должен ронять сборку сразу.
    # Это секунды (35 МБ или кэш), а ловит отсутствие сети раньше всего.
    print("скачиваемое:")
    fetch(PBS_URL, cache / PBS_NAME)
    fetch(GIT_URL, cache / GIT_NAME)

    # Порядок шагов — по вероятности падения, а не по раскладке архива: сперва
    # то, что ломается чаще и проверяется дешевле (фронты, компиляция под mac),
    # потом долгие рантайм и git. Иначе ошибка компиляции показывалась бы к
    # двадцатой минуте прогона CI, после восьми минут pip и make.
    print("фронты:")
    build_fronts()

    print("rust:")
    if args.skip_rust:
        print("  cargo не зовётся (--skip-rust) — бинари из target/release")
    else:
        build_rust()
    missing: list[str] = []
    print("реле:")
    relay: dict | None = None
    try:
        relay_exe, relay = build_relay(cache, args.skip_rust)
        shutil.copy2(relay_exe, out / "helene-relay")
        (out / "helene-relay").chmod(0o755)
        print("  helene-relay: положен")
    except SystemExit as e:
        if not args.allow_partial:
            raise
        missing.append(f"helene-relay — {e}")
        print(f"  ⚠ {e}")
    relay_src_dir = cache / "praxis-relay"

    # Служба: тот же крейт, что на Windows, режим `daemon` под launchd.
    print("служба:")
    try:
        svc_exe = (SVC_CRATE / "target" / "release" / SVC_BIN) if args.skip_rust else build_svc()
        if not svc_exe.is_file():
            raise SystemExit(f"нет {svc_exe} (--skip-rust без прежней сборки)")
        shutil.copy2(svc_exe, out / SVC_BIN)
        (out / SVC_BIN).chmod(0o755)
        # Версия крейта уже сверена с остальными девятью объявлениями
        # (`deskpkg.product_version`, ключ svc/Cargo.toml) — здесь только размер.
        print(f"  {SVC_BIN}: положен ({(out / SVC_BIN).stat().st_size / 1e6:.1f} МБ)")
    except SystemExit as e:
        if not args.allow_partial:
            raise
        missing.append(f"{SVC_BIN} — {e}")
        print(f"  ⚠ {e}")

    print("тело:")
    body_info: dict | None = None
    if args.skip_body:
        print("  ⚠ --skip-body: тела в сборке не будет — это ОТЛАДОЧНАЯ полусборка, не выпуск")
    else:
        try:
            body_exes, body_info = build_body(cache, args.skip_rust)
            for name, exe in body_exes.items():
                shutil.copy2(exe, out / name)
                (out / name).chmod(0o755)
                print(f"  {name}: положен ({(out / name).stat().st_size / 1e6:.1f} МБ)")
        except SystemExit as e:
            if not args.allow_partial:
                raise
            missing.append(f"тело (helene-body, helene-bridge) — {e}")
            print(f"  ⚠ {e}")

    if args.skip_runtime:
        print("runtime: пропущен (--skip-runtime)")
        git_bundle = None
    else:
        print("runtime:")
        stage_runtime(out, cache)
        print("git:")
        git_bundle = stage_git_bundle(out, cache)
    print("  дымовой тест рантайма…")
    freeze = smoke_runtime(out)
    print(f"  импорты живы, пакетов: {len(freeze.splitlines())}")
    voice = [line for line in freeze.splitlines()
             if line.split("==", 1)[0].strip().lower().replace("_", "-") in VOICE_PACKAGES]
    print("  голос: " + (", ".join(voice) if voice else "НИЧЕГО ИЗ VOICE_PACKAGES НЕ ВСТАЛО"))
    macos_floor = runtime_macos_floor(out)
    if not (out / "runtime" / "git" / "bin" / "git").is_file():
        raise SystemExit("нет runtime/git/bin/git — с --skip-runtime git должен уже лежать в сборке")

    print("дерево агента:")
    if args.tree:
        live = Path(args.tree).resolve()
        staged_tree = stage_from_tree(out, live)
        source_release = None
    else:
        staged_tree = stage_from_release(out, cache, tree_tag)
        source_release = staged_tree
    live = out / "tree"

    print("desk:")
    flavor = getattr(deskpkg, "MACOS", None)
    if flavor is None:
        raise SystemExit("deskpkg не знает вида «macos» (deskpkg.MACOS) — пакет desk для Mac не объявлен")
    pkg = deskpkg.build(out / "app", flavor, allow_partial=args.allow_partial, log=print)
    for part in pkg["parts"]:
        print(f"  {part['name']:<14} {part['files']:>4}")
    (out / "requirements.txt").write_text("\n".join(deps()) + "\n", encoding="utf-8", newline="\n")
    staged = {"tree_files": staged_tree["tree_files"], "static_digest": pkg["static"], "desk": pkg}

    # Стенды — после дерева и пакета: они гоняются рантаймом сборки, и это
    # разом проверка продукта и дымовой тест рантайма.
    run_stands(out, args.skip_tests)

    print("бандлы:")
    work = cache / "bundle-work"
    for kind in BUNDLES:
        exe = rust_binary(kind)
        if not exe.is_file():
            missing.append(f"{BUNDLES[kind]['app']} — нет {exe} (cargo build --release --features custom-protocol)")
            continue
        make_bundle(out, kind, version, exe, work)

    print("документы:")
    (out / "ПЕРВЫЙ-ЗАПУСК.md").write_text(FIRST_RUN_MAC, encoding="utf-8", newline="\n")
    bd.copy_text_lf(DESK / "resources" / "ОБНОВЛЕНИЕ.md", out / "ОБНОВЛЕНИЕ.md")
    bd.copy_text_lf(DESK / "resources" / "HELENE-MAP.md", out / "КАК-УСТРОЕН-HELENE.md")
    bd.copy_text_lf(DESK / "installer" / "ЛИЦЕНЗИЯ.md", out / "ЛИЦЕНЗИЯ.md")
    bd.copy_text_lf(DESK / "installer" / "NOTICE", out / "NOTICE")
    (out / "ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md").write_text(THIRD_PARTY_MAC, encoding="utf-8", newline="\n")
    sh_src = DESK / "installer" / "install.sh"
    if not sh_src.is_file():
        raise SystemExit(f"нет {sh_src}")
    install_sh = stamp_install_sh(sh_src.read_text(encoding="utf-8").replace("\r\n", "\n"), tag or f"v{version}")
    (out / "install.sh").write_text(install_sh, encoding="utf-8", newline="\n")
    (out / "install.sh").chmod(0o755)
    n_lic = bd.collect_rust_licenses(out, args.allow_partial, live, parts=("shell", "setup"),
                                     include_body=False, exes="Helene.app и Helene Setup.app")
    print(f"  лицензии крейтов: {n_lic}")
    if (relay_src_dir / "Cargo.lock").is_file():
        print(f"  лицензии крейтов реле: {collect_relay_licenses(out, relay_src_dir, args.allow_partial)}")
    if body_info is not None:
        print(f"  лицензии крейтов тела: {collect_body_licenses(out, BODY_SRC, args.allow_partial, body_info)}")
    (out / "helene.json").write_text(helene_json_mac(), encoding="utf-8", newline="\n")
    (out / "data").mkdir(exist_ok=True)

    print("подпись ad-hoc:")
    signed = codesign_all(out)

    print("паспорт сборки:")
    desk_head, desk_dirty = bd._git_field(DESK, "desk")
    # Паспорт пишется этим же шагом — его отсутствие ДО записи не нехватка (седьмой
    # круг CI дошёл сюда с полным составом и упал ровно на этой строке).
    # С --skip-body записей тела в корне не ждём: их отсутствие — осознанная
    # полусборка, она едет в паспорт своей строкой ниже, а не как «нет в сборке».
    skipped_body = ["тела нет: --skip-body (отладочная полусборка, не выпуск)"] if args.skip_body else []
    lost = [rel for rel in missing_in_root(out)
            if rel != "helene-build.json" and not (args.skip_body and rel in BODY_ROOT_ENTRIES)]
    for rel in lost:
        print(f"  ⚠ в сборке нет: {rel}")
    partial = missing + [f"нет в сборке: {rel}" for rel in lost] + \
        ([] if not pkg["skipped"] else [f"пакет desk без {s['name']}" for s in pkg["skipped"]])
    if partial and not args.allow_partial:
        raise SystemExit("сборка неполная:\n  " + "\n  ".join(partial) + "\n(для отладочной полусборки: --allow-partial)")
    partial += skipped_body
    manifest = build_passport(
        version=version, declared=declared, desk_head=desk_head, desk_dirty=desk_dirty,
        tree_head=staged_tree["tree_head"], tree_dirty=staged_tree["tree_dirty"],
        source_release=({k: source_release[k] for k in ("tag", "asset", "sha256", "tree_files", "passport", "core")}
                        if source_release else None),
        staged=staged, relay=relay, freeze=freeze,
        downloads={PBS_NAME: SHA256[PBS_URL], GIT_NAME: SHA256[GIT_URL]},
        complete=not partial, partial_reason=partial, signed=signed,
        git_bundle=git_bundle, macos_floor=macos_floor, body=body_info)
    (out / "helene-build.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    still = [rel for rel in missing_in_root(out) if not (args.skip_body and rel in BODY_ROOT_ENTRIES)]
    if still:
        raise SystemExit("после записи паспорта в корне всё ещё не хватает: " + ", ".join(still))

    # Гард ПОСЛЕ паспорта и до архива — как у Windows. Раскладка рантайма на Mac
    # другая (`lib/python3.14/site-packages`, а не `Lib/site-packages`), поэтому
    # известные ложняки Windows-списка переводятся на неё: иначе `rsa/key.py`
    # (зависимость telethon) ронял бы каждую сборку тем же «private key».
    print("секрет-гард:")
    lib = f"lib/python{PY_VERSION.rsplit('.', 1)[0]}/site-packages/"
    bd.RUNTIME_KNOWN_FALSE = set(bd.RUNTIME_KNOWN_FALSE) | {
        (lib + rel.split("site-packages/", 1)[1], label)
        for rel, label in bd.RUNTIME_KNOWN_FALSE if "site-packages/" in rel
    }
    scanned = bd.scan_for_secrets(out, live, scan_runtime=not args.skip_runtime)
    print(f"  просканировано файлов: {scanned} — чисто")

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file() and not f.is_symlink())
    print(f"итого: {total / 1e6:.1f} МБ до сжатия")
    zip_path = out.parent / names["zip"]
    zip_path.unlink(missing_ok=True)
    print("zip (ditto)…")
    # ditto, а не zipfile: симлинки рантайма (bin/python3 → python3.14), права на
    # исполнение и бандлы .app должны приехать как есть; --keepParent даёт в корне
    # архива папку Helene/, как у Windows.
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", out, zip_path], cwd=out.parent, timeout=1800)
    digest = bd.sha256(zip_path)
    (out.parent / names["sha256"]).write_text(sha256_line(digest, names["zip"]), encoding="utf-8", newline="\n")
    shutil.copy2(out / "install.sh", out.parent / "install.sh")
    print(f"готово: {zip_path} ({zip_path.stat().st_size / 1e6:.1f} МБ)")
    print(f"sha256: {digest}")
    print(f"рядом: {names['sha256']}, install.sh")


if __name__ == "__main__":
    main()
