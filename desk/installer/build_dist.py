# -*- coding: utf-8 -*-
"""Сборка портативного дистрибутива Hélène.

Раскладка поставки:
  Helene/
    helene.exe           — оболочка (окно+трей), поднимает всё остальное
    helene-setup.exe     — установщик: сцены первого запуска, копия в LocalAppData
    helene-svc.exe       — служба Windows (необязательная)
    helene-relay.exe     — реле подписки ChatGPT
    helene-bridge.exe    — мост тела руки `computer` (praxis-bridge из tree/body)
    helene-body.exe      — тело: окна, экран, клавиатура и мышь, файлы, процессы
                          (praxis-body из tree/body); поднимает их харнесс
    helene.json          — конфиг поставки (ключ пуст, настраивает установщик)
    helene-build.json    — паспорт сборки: версия, время, git, состав рантайма
    ПЕРВЫЙ-ЗАПУСК.md    — что делать сразу после распаковки
    ОБНОВЛЕНИЕ.md       — как обновиться и что перед этим скопировать
    runtime/            — embedded CPython + зависимости (самодостаточный)
    app/                — канал (deskapp+deskd+static), руннер (localharness),
                          resources/ (каноническая конституция)
    tree/               — код дерева агента (порт как есть, без тестов/секретов)
    licenses/           — тексты лицензий того, что влинковано в наши exe
    data/               — рождается при первом запуске (память, кадр, душа)

Запуск: python build_dist.py [--out DIR] [--skip-runtime] [--allow-partial]
Сеть нужна один раз: embeddable CPython с python.org + pip с pypi + busybox
+ MinGit с GitHub (git для личного репозитория агента).

Правило этого файла: сборка либо выпускает ПОЛНУЮ поставку, либо падает с
понятной строкой. Раньше половина шагов писала «⚠ … не найден» и шла дальше с
кодом выхода 0 — так уезжал релиз без установщика, без телефона и со старым
exe из папки прошлой сборки. Отладочные полусборки теперь только под
--allow-partial, и он пишет в паспорт сборки, что поставка неполная.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# Русские сообщения ниже — не украшение, их читает владелец. При перенаправлении
# вывода (`> build.log`, конвейер, CI) Python берёт локаль консоли — на машине
# владельца cp1251 — и сборка падала на ПЕРВОМ print из-за «→». Поток чиним, а
# не текст.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

PY_VERSION = "3.14.5"      # та же линия, на которой гоняется порт (гейт зелёный)
EMBED_URL = (f"https://www.python.org/ftp/python/{PY_VERSION}/"
             f"python-{PY_VERSION}-embed-amd64.zip")
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
# busybox-w32 (официальная сборка Рона Йорстона): один exe, POSIX-шелл.
# Едет в runtime/bash.exe — прямо рядом с python.exe, не в подпапке: папки
# runtime/shims в поставке нет вовсе (см. копирование шима ниже и
# localharness/fence.py). Инструмент shell агента зовёт `bash -lc`, и на
# машине без Git/WSL команда иначе честно отказывала бы. Решение владельца 31.08.
BUSYBOX_URL = "https://frippery.org/files/busybox/busybox64.exe"
# MinGit (git-for-windows, вариант busybox: только git, без bash и perl) —
# личный репозиторий агента в дереве данных и руки, что ходят в git. Едет в
# runtime/git; в PATH его вставляет руннер (localharness/boot.arm_git) и ограда
# (fence.Container.run), в систему git ничего не пишет. До 06.09 git в поставке
# не было вовсе, и обещание кадра «правка автокоммитится» было ложью (см.
# runner._git_state). Слово владельца 06.09: «как в Уроборосе, из коробки».
MINGIT_VERSION = "2.55.0.5"
MINGIT_URL = ("https://github.com/git-for-windows/git/releases/download/"
              f"v2.55.0.windows.5/MinGit-{MINGIT_VERSION}-busybox-64-bit.zip")

# Контрольные суммы скачиваемого. Раньше не сверялось НИЧЕГО, и кэш признавался
# годным по одному лишь факту существования файла: оборванная закачка ехала в
# поставку, а busybox — это `runtime/bash.exe`, тот самый bash, которым агент
# исполняет руку shell.
#
# Честно о границе этой защиты: суммы сняты 03.09.2026 с файлов, на которых
# поставка 0.2.1 собрана и работает, а не с подписи издателя. Это защита от
# ПОДМЕНЫ И ПОРЧИ ПОСЛЕ этой даты, а не доказательство, что тогда всё было
# чисто. Меняешь PY_VERSION или адрес — снимаешь сумму заново и пишешь дату.
SHA256 = {
    EMBED_URL:   "ba6bd811c4eedb19195cf275770ef127e893d63701e24152606e2cb76f6d876a",
    GET_PIP_URL: "fb24e693bab954209a063d90953621412ccad4a500905a726286e038f508ddf6",
    BUSYBOX_URL: "07bb1e5b095b00d68a695481f9240879f33c5724b40aa2308f999d54ed78f075",
    # снята 06.09.2026 с файла, скачанного с GitHub Releases git-for-windows
    MINGIT_URL:  "a0287b54d3ead0c5abd0d15094f5b4c909867aae6bf8b9f2aee7109d24fa0081",
}

DESK = Path(__file__).resolve().parent.parent
ROOT = DESK.parent
sys.path.insert(0, str(DESK))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout  # noqa: E402 — где на диске лежат соседи раскладки
import deskpkg  # noqa: E402 — объявление пакета desk живёт в репозитории, а не в сборке
import relay_src  # noqa: E402 — происхождение исходника реле
import core_src  # noqa: E402 — ядро, слой издания и расхождение между ними

# Зависимости ДЕРЕВА: то, что импортирует ход (agent/llm/webtool).
# Aiogram/paramiko/stt — другие тела, в продукт не едут; cryptography не нужна
# (telegram_confirmation агентом не импортируется — проверено грепом 31.08).
TREE_DEPS = [
    "anthropic", "openai", "httpx", "python-dotenv", "pillow",
    "pypdf", "trafilatura", "charset-normalizer",
]

# ГОЛОС. `faster-whisper` тянет за собой ctranslate2, onnxruntime, av и numpy —
# рантайм тяжелеет примерно на 170 МБ, и это осознанная цена: на сервере
# расшифровка есть с 0.3.x, а на Windows голосовое до 0.5.2 превращалось в
# молчание. Модель в поставку не входит и войти не может (480 МБ у маленькой,
# 1,6 ГБ у рабочей) — её качает владелец из окна, `localharness/voice.py`.
# ГОЛОС НАРУЖУ (11.09). `piper-tts` — 34 МБ колёсиком, и почти всё, что ему
# нужно (onnxruntime, numpy), уже привезено ради расшифровки. Голос в поставку
# не входит: 61 МБ на каждый, качает владелец из окна тем же помощником.
# ⚠ Не Edge и не Silero: Edge — это голос Микрософта ПО СЕТИ, то есть текст
# ответа агента уходил бы наружу на каждую фразу, а Silero тянет torch (~2 ГБ).
# faster-whisper 1.2.1 передаёт metadata_errors в av.open; PyAV 19 убрал
# этот аргумент. Под CPython 3.14 нет колёс av<15 из requirements ядра:
# совместимые колёса 15–18 поддерживают прежний API.
VOICE_DEPS = ["faster-whisper", "piper-tts", "av<19"]

# Зависимости ПОСТАВКИ = дерево + пакет desk (канал просит aiohttp, раннер —
# telethon). Свой список desk объявляет сам (deskpkg.DEPS_*), и сервер ставит
# ровно его: раньше про зависимости канала знала только эта строка, а на
# сервере они держались тем, что кто-то однажды поставил их в образ руками.
DEPS = TREE_DEPS + VOICE_DEPS + deskpkg.requirements(deskpkg.WINDOWS)

# Дымовой тест рантайма: ровно то, что продукт импортирует на своём пути.
SMOKE_IMPORTS = ("anthropic", "openai", "httpx", "dotenv", "PIL", "piper",
                 "aiohttp", "pypdf", "trafilatura", "telethon", "faster_whisper")

APP_DIST = DESK / "app" / "dist"     # UI окна — сборка Vite (npm --prefix app run build)
MOBILE_DIST = DESK / "mobile" / "dist"   # PWA телефона (npm --prefix mobile run build)

# Дерево агента, зеркало реле и цель сборки тела лежат РЯДОМ с раскладкой, а не
# в ней. Где именно — знает `layout.py`, и знает один он: до 10.09 этот путь был
# жёсткой константой в четырёх местах сразу.
LIVE_DEFAULT = layout.neighbour("live")
BODY_TARGET = layout.body_target()


def live_root(cli: str | None) -> Path:
    return layout.tree(cli)


# --- отбор файлов дерева ------------------------------------------------------
#
# Две разные семантики раньше жили в одном списке и нигде не были описаны:
# со слэшем — startswith от корня (то есть "body/target" ловил и
# "body/targetting.py"), без слэша — сравнение с КАЖДОЙ частью пути (то есть
# будущий пакет core/memory/ исчез бы из поставки беззвучно). Разведены.

# Пути от корня дерева: данные и чужие сборки, которых в продукте быть не должно.
TREE_EXCLUDE_PATHS = [
    "body/target", "hands/target",
    "memory", "workspace", "soul", "private", "runs", "shadow_traffic", "1500",
    "_archive",
    # 28.09 (слово Егора: «никаких упоминаний Праксис»): документы корня дерева
    # описывают другую установку — чужое имя, чужой дом, чужие полномочия. Агент
    # их читал (forge.py берёт README/AGENTS задачи; навыки отсылали сюда) —
    # в поставке их нет. Роль модуля — в самом модуле, карта — КАК-УСТРОЕН-HELENE.md.
    "ARCHITECTURE.md", "CODEMAP.md", "CONTRACTS.md", "CONTRIBUTORS.md", "HOME.md",
    "README.md", "VISION.md",
]

# Имена и маски — на любой глубине: мусор сборки, тесты, её рабочие заметки.
TREE_EXCLUDE_NAMES = [
    "__pycache__", ".git", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "test_*.py", "conftest.py", "hands_diff.txt",
    "docker-compose*", "Dockerfile", "*.bak",
    "workspace_*.md", "*.pre-*", "moderation_shadow_corpus.json",
    "STATUS.md", "SYNC-HEAD.txt", "ПОРТ-СТАТУС-*.md",
    # 25.09 (ревью V1-6): инструкция агентам с постурой сервера владельца — не продукт.
    "AGENTS.md",
]

# Формы секретов. Отдельным списком и с ГРОМКОЙ строкой в логе: чёрный список
# имён — второй эшелон (первый — сканер содержимого, см. scan_for_secrets), и
# то, что он сработал, владелец должен видеть, а не узнавать из тишины.
TREE_EXCLUDE_SECRETS = [
    ".env*", "*.env", "*.session", "*.session-journal",
    "*.pem", "*.key", "*.pfx", "*.p12", "*.jks", "*.keystore",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.ppk",
    "credentials*.json", "client_secret*.json", "service-account*.json",
    "auth.json", ".netrc", "_netrc", ".npmrc", ".pypirc", ".pgpass",
    ".git-credentials", "llm.json",
]


def _match_name(parts: list[str], patterns: list[str]) -> bool:
    # fnmatchcase, а не fnmatch: fnmatch зовёт os.path.normcase и на Windows
    # регистр не различает, а на Linux различает — сборка на разных ОС давала
    # разный состав поставки (".ENV" уезжал бы с Linux). Сверяем и как есть,
    # и в нижнем регистре — одинаково на всех системах.
    for pattern in patterns:
        low = pattern.lower()
        for part in parts:
            if fnmatch.fnmatchcase(part, pattern) or fnmatch.fnmatchcase(part.lower(), low):
                return True
    return False


def _excluded(rel: str) -> str:
    """'' если файл едет; иначе причина ('путь' / 'имя' / 'секрет')."""
    norm = rel.replace("\\", "/")
    parts = norm.split("/")
    for prefix in TREE_EXCLUDE_PATHS:
        if norm == prefix or norm.startswith(prefix + "/"):
            return "путь"
    if _match_name(parts, TREE_EXCLUDE_SECRETS):
        return "секрет"
    if _match_name(parts, TREE_EXCLUDE_NAMES):
        return "имя"
    return ""


def copy_tree(src: Path, dst: Path) -> tuple[int, list[str]]:
    count = 0
    secrets: list[str] = []
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(src))
        why = _excluded(rel)
        if why:
            if why == "секрет":
                secrets.append(rel.replace("\\", "/"))
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    return count, secrets


# Статика окна и её отпечаток объявлены в пакете desk — одной реализацией на
# поставку, вариант Praxis и сервер. Имена оставлены здесь: их читает и вариант
# Praxis, и установщик (сверка «менял ли выпуск интерфейс»).
STATIC_MANIFEST = deskpkg.STATIC_MANIFEST
static_manifest = deskpkg.static_manifest
copy_static = deskpkg.copy_static


def copy_text_lf(src: Path, dst: Path) -> None:
    """Копия текстового файла с одной нормой переводов строк — LF."""
    # Правило этого файла — «либо полная поставка, либо понятная строка».
    # Документы поставки были единственным местом, где отсутствие входа давало
    # голый FileNotFoundError из середины сборки.
    if not src.is_file():
        raise SystemExit(f"нет файла поставки: {src}")
    text = src.read_text(encoding="utf-8").replace("\r\n", "\n")
    dst.write_text(text, encoding="utf-8", newline="\n")


def stage_payload(dest: Path, live: Path, allow_partial: bool) -> dict:
    """app/ + tree/ + requirements — общий груз поставки.

    Одна функция на всех потребителей, чтобы состав не расходился молча.
    Раньше её тело было ещё раз переписано внутри main() — и уже разошлось
    (requirements.txt писала только эта функция, а звали только main).

    ``app/`` — это ПАКЕТ desk целиком (``deskpkg.build``), тот же, что уезжает
    на сервер: канал, читалки, окно, телефон, раннер и ресурсы одним списком с
    манифестом и версией. Перечислять пути здесь больше нечего — до 10.09 их
    перечисляли дважды, и мини-апп, например, ехал только на сервер, потому что
    про него знал только тот список.
    """
    print("desk:")
    pkg = deskpkg.build(dest / "app", deskpkg.WINDOWS, allow_partial=allow_partial,
                        log=print)
    for part in pkg["parts"]:
        print(f"  {part['name']:<14} {part['files']:>4}")
    static_digest = pkg["static"]
    phone = any(part["name"] == "mobile" for part in pkg["parts"])

    print("tree:")
    copied, secrets = copy_tree(live, dest / "tree")
    print(f"  файлов дерева: {copied}")
    for rel in secrets:
        print(f"  ⨯ не поехало (форма секрета): {rel}")
    # Порог, а не ноль: пустое дерево раньше проходило как «файлов дерева: 0»
    # обычной строкой, и архив без tree/ уезжал с кодом выхода 0.
    if copied < 100:
        raise SystemExit(
            f"дерево агента почти пустое: {copied} файлов из {live}\n"
            "поставка без tree/ нерабочая — проверь путь (--tree PATH или HELENE_TREE_SRC)")
    (dest / "requirements.txt").write_text("\n".join(DEPS) + "\n",
                                           encoding="utf-8", newline="\n")
    return {"tree_files": copied, "phone": phone, "secrets_skipped": secrets,
            "static_digest": static_digest, "desk": pkg}


# --- сеть ---------------------------------------------------------------------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_sum(url: str, path: Path) -> None:
    want = SHA256.get(url)
    if not want:
        return
    got = sha256(path)
    if got != want:
        raise SystemExit(
            f"НЕ СОШЛАСЬ КОНТРОЛЬНАЯ СУММА: {path.name}\n"
            f"  адрес:  {url}\n"
            f"  ждали:  {want}\n"
            f"  скачали:{got}\n"
            "Файл подменён, повреждён или издатель выложил новую сборку.\n"
            "Разберись, ПОТОМ обнови SHA256 в build_dist.py — не наоборот.")


def fetch(url: str, dst: Path) -> None:
    if dst.exists() and dst.stat().st_size > 0:
        # Кэш ТОЖЕ сверяется: раньше он признавался годным по dst.exists(), и
        # усечённый или подменённый файл жил в нём вечно.
        _check_sum(url, dst)
        print(f"  есть: {dst.name}")
        return
    print(f"  качаю {url}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Скачиваем во временный файл и переименовываем: оборванная закачка раньше
    # оставляла усечённый файл, который следующий прогон принимал за кэш и
    # распаковывал/выполнял.
    part = dst.with_suffix(dst.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            part.write_bytes(resp.read())
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        part.unlink(missing_ok=True)
        raise SystemExit(
            f"не скачалось: {url}\n"
            f"причина: {e}\n"
            "нет сети или адрес недоступен — сборка остановлена до работы") from None
    try:
        _check_sum(url, part)
    except SystemExit:
        part.unlink(missing_ok=True)
        raise
    os.replace(part, dst)


# --- рантайм ------------------------------------------------------------------

def stage_git(out: Path, cache: Path) -> int:
    """MinGit в runtime/git — из СВЕРЕННОГО кэша, всегда заново.

    Папка runtime/ переживает пересборку (ROOT_KEEP), поэтому старый git
    сносится явно: иначе однажды испорченная или устаревшая раскладка жила бы
    в поставке вечно. Дым — `git --version` из своей папки без PATH: MinGit
    обязан работать переносимо, а не через реестр или установленный Git.
    """
    zip_path = cache / Path(MINGIT_URL).name
    fetch(MINGIT_URL, zip_path)
    dest = out / "runtime" / "git"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    exe = dest / "cmd" / "git.exe"
    if not exe.is_file():
        raise SystemExit(f"в {zip_path.name} нет cmd/git.exe — раскладка MinGit "
                         "изменилась, сборка остановлена")
    # Вывод ловим сами: _run_timed печатает в консоль, а не возвращает, и первый
    # прогон 06.09 упал на пустой строке при живом git (версия ушла в лог).
    try:
        probe = subprocess.run([str(exe), "--version"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        raise SystemExit("runtime/git не ответил на --version за две минуты") from None
    said = (probe.stdout or "").strip()
    if probe.returncode != 0 or "git version" not in said:
        raise SystemExit(f"runtime/git не отвечает на --version: {said!r} "
                         f"{(probe.stderr or '').strip()!r}")
    n = sum(1 for p in dest.rglob("*") if p.is_file())
    print(f"  runtime/git ({said}) положен, файлов: {n}")
    return n


def build_runtime(out: Path, cache: Path) -> None:
    """Embedded CPython, который умеет наши зависимости.

    Embeddable-сборка python.org по умолчанию БЕЗ site-packages и без pip:
    в `python314._pth` включаем `import site`, ставим pip через get-pip и
    ставим зависимости внутрь. Результат самодостаточен: ни PATH, ни реестра,
    ни установленного питона на машине не требуется.
    """
    runtime = out / "runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    embed_zip = cache / f"python-{PY_VERSION}-embed-amd64.zip"
    get_pip = cache / "get-pip.py"
    fetch(EMBED_URL, embed_zip)
    fetch(GET_PIP_URL, get_pip)
    runtime.mkdir(parents=True)
    with zipfile.ZipFile(embed_zip) as zf:
        zf.extractall(runtime)
    try:
        pth = next(runtime.glob("python3*._pth"))
    except StopIteration:
        raise SystemExit(
            f"в embeddable-сборке нет python3*._pth ({embed_zip.name}) — "
            "python.org изменил раскладку, сборка остановлена") from None
    text = pth.read_text(encoding="utf-8").replace("#import site", "import site")
    # Проверяем РЕЗУЛЬТАТ замены, а не факт вызова: напиши python.org
    # «# import site» с пробелом — замена молча не сработала бы, сборка прошла
    # зелёной, а рантайм получился бы без site-packages и упал у пользователя.
    if not re.search(r"^\s*import site\s*$", text, re.M):
        raise SystemExit(
            f"{pth.name}: не удалось включить `import site` — без него рантайм "
            "не увидит site-packages. Содержимое:\n" + text)
    pth.write_text(text, encoding="utf-8", newline="\n")
    py = runtime / "python.exe"
    print("  ставлю pip…")
    # timeout на каждом шаге: подвисший индекс PyPI раньше вешал сборку навсегда
    # и без строки в выводе (у fetch таймаут был, у pip — нет).
    run = lambda args, check: _run_timed(args, check=check, timeout=1800)
    run([str(py), str(get_pip), "--no-warn-script-location", "-q"], True)
    print("  ставлю зависимости…")
    # Часть пакетов (pyaes у Telethon) идёт исходниками без колеса под 3.14:
    # им нужен setuptools на время сборки; после — снимаем, пользователю он не нужен.
    run([str(py), "-m", "pip", "install", "-q", "--no-warn-script-location",
         "setuptools", "wheel"], True)
    run([str(py), "-m", "pip", "install", "-q",
         "--no-warn-script-location", *DEPS], True)
    run([str(py), "-m", "pip", "uninstall", "-y", "-q", "setuptools", "wheel"], False)
    # Кэш pip в поставке не нужен. Сам pip остаётся сознательно: он единственный
    # способ починить рантайм на машине пользователя, не пересобирая поставку.
    run([str(py), "-m", "pip", "cache", "purge", "-q"], False)


def _run_timed(args: list[str], *, check: bool, timeout: int):
    try:
        return subprocess.run(args, check=check, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SystemExit(
            f"шаг сборки рантайма не ответил за {timeout // 60} мин: {args[0]} …\n"
            "скорее всего недоступен индекс PyPI — сборка остановлена") from None


def smoke_runtime(out: Path, imports: list[str] | tuple[str, ...] = SMOKE_IMPORTS) -> str:
    """Рантайм обязан импортировать то, ради чего он собран.

    Раньше это не проверялось никогда: добавили зависимость, собрали с
    --skip-runtime — и падение случалось на машине владельца, при первом импорте.
    С 1.2.1 голос проверяется отдельно (`smoke_voice`): рантайм — без него.
    """
    py = out / "runtime" / "python.exe"
    if not py.is_file():
        raise SystemExit(
            f"нет рантайма: {py}\n"
            "с --skip-runtime рантайм должен уже лежать в папке сборки; "
            "для выпуска собирай без флага")
    code = "import " + ", ".join(imports)
    r = subprocess.run([str(py), "-c", code], capture_output=True, text=True,
                       timeout=300, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("рантайм не импортирует свои зависимости:\n" + (r.stderr or "").strip())
    r = subprocess.run([str(py), "-m", "pip", "freeze"], capture_output=True, text=True,
                       timeout=300, encoding="utf-8", errors="replace")
    return (r.stdout or "").strip()


# --- 1.2.1: голосовой движок — отдельным набором ------------------------------
#
# Егор 27.09: «почему установщик так много весит?» — больше половины (94 из 180 МБ
# сжатыми) был голос: ctranslate2, onnxruntime, FFmpeg из av, numpy, piper. Модель слуха
# и голос синтеза и так качаются из окна (0,5–1,6 ГБ), поэтому движок едет туда же:
# отдельным архивом выпуска, докачивается вместе с моделью (`localharness/voice.py`),
# ложится в `<установка>/voice/site-packages`, а `.pth` в рантайме добавляет эту папку
# в путь каждого процесса. Граница — по метаданным пакетов, не списком руками:
# «только голосу» = замыкание VOICE_DEPS минус замыкание всего остального. Наш код и
# дерево агента этих пакетов не импортируют (проверено грепом 27.09).
#
# ⚠ Имя архива — НЕ .zip: окна 1.1.x/1.2.0 берут из выпуска первый «helene-*.zip»
# (shell::pick_update_zip), и голосовой набор перехватил бы у них кнопку «Обновить».

VOICE_IMPORTS = ("faster_whisper", "piper")
# Чем ещё голос должен импортироваться из набора (дымовой тест): тяжёлые двоичные части.
VOICE_SMOKE_EXTRA = ("ctranslate2", "onnxruntime", "av", "numpy")
VOICE_PTH = "helene-voice.pth"
# Путь из runtime/Lib/site-packages к <установка>/voice/site-packages: `site` читает
# .pth при старте любого процесса рантайма и добавляет строку, только если папка есть.
VOICE_PTH_LINE = "../../../voice/site-packages"
VOICE_PACK_MANIFEST = "voice-pack.json"


def _norm_dist(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _req_name(spec: str) -> str:
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
    return _norm_dist(m.group(1)) if m else ""


def _site_dists(site: Path) -> dict[str, dict]:
    """Пакеты папки site-packages: имя → версия, зависимости (без extra) и файлы RECORD
    (только внутри папки: `../../Scripts/*` остаются рантайму)."""
    import csv
    out: dict[str, dict] = {}
    if not site.is_dir():
        return out
    for info in sorted(site.glob("*.dist-info")):
        meta_path = info / "METADATA"
        if not meta_path.is_file():
            continue
        meta = meta_path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^Name:\s*(.+)$", meta, re.M)
        if not m:
            continue
        ver = re.search(r"^Version:\s*(.+)$", meta, re.M)
        reqs = []
        for line in re.findall(r"^Requires-Dist:\s*(.+)$", meta, re.M):
            spec, _, marker = line.partition(";")
            if "extra" not in marker and _req_name(spec):
                reqs.append(_req_name(spec))
        files: list[str] = []
        rec = info / "RECORD"
        if rec.is_file():
            with rec.open(encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.reader(fh):
                    path = (row[0] if row else "").strip().replace("\\", "/")
                    if path and not path.startswith(("..", "/")):
                        files.append(path)
        out[_norm_dist(m.group(1).strip())] = {
            "version": ver.group(1).strip() if ver else "", "requires": reqs, "files": files}
    return out


def _dist_closure(roots: list[str], dists: dict[str, dict]) -> set[str]:
    seen: set[str] = set()
    stack = [r for r in roots if r]
    while stack:
        name = stack.pop()
        if name in seen or name not in dists:
            continue
        seen.add(name)
        stack.extend(dists[name]["requires"])
    return seen


def _move_file(src: Path, dst: Path) -> bool:
    if not src.is_file() or dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    return True


def _prune_empty(root: Path) -> None:
    for dirpath, _dirs, _files in sorted(os.walk(root), key=lambda t: -len(t[0])):
        p = Path(dirpath)
        if p != root:
            with contextlib.suppress(OSError):
                p.rmdir()


def split_voice(site: Path, stage: Path) -> dict:
    """Развести пакеты: только голосовые — в `stage`, остальные — в рантайм. Идемпотентно:
    повторная сборка с --skip-runtime застаёт уже разведённое и ничего не двигает.
    -> {"dists": {имя: версия}, "tops": [...], "moved": n}."""
    here, there = _site_dists(site), _site_dists(stage)
    both = {**there, **here}
    base_roots = [_req_name(d) for d in TREE_DEPS + deskpkg.requirements(deskpkg.WINDOWS)] + ["pip"]
    base = _dist_closure(base_roots, both)
    voice_roots = [_req_name(d) for d in VOICE_DEPS]
    missing = [d for d in voice_roots if d not in both]
    if missing:
        raise SystemExit(f"голосовых пакетов нет ни в рантайме, ни в наборе: {missing} — "
                         "рантайм собран без голоса; пересобери без --skip-runtime")
    voice = _dist_closure(voice_roots, both) - base
    owners: dict[str, set[str]] = {}
    for name, d in both.items():
        for f in d["files"]:
            owners.setdefault(f.split("/", 1)[0], set()).add(name)
    moved = 0
    stage.mkdir(parents=True, exist_ok=True)
    for name, d in both.items():
        src, dst = (site, stage) if name in voice else (stage, site)
        for f in d["files"]:
            moved += _move_file(src / f, dst / f)
    # Остатки внутри папок, которыми владеет только голос (скомпилированный __pycache__,
    # файлы, появившиеся после установки), — туда же, целиком.
    tops = sorted(t for t, who in owners.items()
                  if who <= voice and t != "__pycache__" and not t.endswith(".dist-info"))
    for top in tops:
        left = site / top
        if left.is_dir():
            for p in sorted(left.rglob("*")):
                if p.is_file():
                    moved += _move_file(p, stage / p.relative_to(site))
            _prune_empty(left)
            with contextlib.suppress(OSError):
                left.rmdir()
    _prune_empty(site)
    (site / VOICE_PTH).write_text(VOICE_PTH_LINE + "\n", encoding="utf-8", newline="\n")
    return {"dists": {n: both[n]["version"] for n in sorted(voice)}, "tops": tops, "moved": moved}


def smoke_voice(out: Path, stage: Path) -> None:
    """Голос импортируется и декодирует WAV; модель и сеть не нужны."""
    py = out / "runtime" / "python.exe"
    code = f"import sys; sys.path.insert(0, {str(stage)!r}); import " + ", ".join(
        VOICE_IMPORTS + VOICE_SMOKE_EXTRA) + "\n" + """
import io, wave
from faster_whisper.audio import decode_audio
source = io.BytesIO()
with wave.open(source, "wb") as wav:
    wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
    wav.writeframes(bytes(32000))
source.seek(0)
audio = decode_audio(source)
assert audio.shape == (16000,) and audio.dtype == numpy.float32
print("voice decode: 16000 samples, float32")
"""
    r = subprocess.run([str(py), "-c", code], capture_output=True, text=True,
                       timeout=300, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("голосовой набор не импортируется или не декодирует аудио:\n" + (r.stderr or "").strip())
    print("  " + r.stdout.strip())


def pack_voice(stage: Path, dest: Path, meta: dict, *, level: int = 19) -> dict:
    """Набор голоса одним архивом: tar (опись `voice-pack.json` первой, дальше
    `site-packages/…`), сжатый zstd. Рядом — `.sha256`. -> запись для паспорта."""
    import io
    import tarfile
    from compression import zstd
    files = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file())
    unpacked = sum((stage / f).stat().st_size for f in files)
    manifest = {**meta, "files": len(files), "unpacked_bytes": unpacked}
    opts = {
        zstd.CompressionParameter.compression_level: level,
        zstd.CompressionParameter.enable_long_distance_matching: 1,
        zstd.CompressionParameter.window_log: 27,
        zstd.CompressionParameter.nb_workers: max(1, (os.cpu_count() or 2) - 2),
    }
    tmp = dest.with_name(dest.name + ".tmp")
    with open(tmp, "wb") as raw:
        with zstd.ZstdFile(raw, "w", options=opts) as zf:
            with tarfile.open(fileobj=zf, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                data = (json.dumps(manifest, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
                info = tarfile.TarInfo(VOICE_PACK_MANIFEST)
                info.size, info.mode, info.mtime = len(data), 0o644, int(_dt.datetime.now().timestamp())
                tar.addfile(info, io.BytesIO(data))
                for rel in files:
                    info = tar.gettarinfo(str(stage / rel), arcname="site-packages/" + rel)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mode = 0o644
                    with open(stage / rel, "rb") as fh:
                        tar.addfile(info, fh)
    os.replace(tmp, dest)
    digest = sha256(dest)
    dest.with_name(dest.name + ".sha256").write_text(
        f"{digest} *{dest.name}\n", encoding="utf-8", newline="\n")
    return {"name": dest.name, "sha256": digest, "bytes": dest.stat().st_size,
            "unpacked_bytes": unpacked, "files": len(files),
            "python": meta.get("python", ""), "dists": meta.get("dists", {})}


# --- секрет-гард --------------------------------------------------------------

def _credential_floor(live: Path):
    """Кред-пол берём из самого дерева агента — один пол на продукт и на сборку.

    Раньше «секрет-гардов» было два, и оба не могли сработать никогда: они
    проверяли существование tree/.env и tree/.env.example ПОСЛЕ отбора, который
    эти же имена уже вырезал. Содержимое едущих файлов не смотрел никто.
    """
    src = live / "core" / "secrets.py"
    if not src.is_file():
        raise SystemExit(f"нет кред-пола {src} — сканировать поставку нечем, сборка остановлена")
    spec = importlib.util.spec_from_file_location("_helene_secrets", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # модуль на голой стандартной библиотеке (re)
    return mod.credential_floor


def _deny_strings() -> list[str]:
    """Локальный список строк, которых в поставке быть не должно.

    installer/secret-strings.txt в .gitignore: сюда владелец кладёт свои
    буквальные значения (пароль VPS, адрес сервера), и они проверяются, не
    попадая при этом в публичный репозиторий.

    Отсутствие файла — не отказ (у большинства машин его и не будет), но и не
    молчание: сборка печатает строкой, что эта половина гарда не подключена.
    Раньше функция возвращала пустой список беззвучно, а гард рядом печатал
    «чисто» — половина кричала SystemExit-ом, половина молчала.
    """
    p = DESK / "installer" / "secret-strings.txt"
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


# Файл целиком читаем, без капа: кред-пол дерева режет чтение на 512 КБ (там это
# защита памяти живого агента), а у сборки этой причины нет — зато есть tree/agent.py
# на 1,04 МБ, у которого при капе не смотрелась ровно половина. Верхняя граница
# оставлена одна, от чужого дампа в дереве.
SCAN_MAX_BYTES = 64 * 1024 * 1024

# Форма присвоения — ТОЛЬКО для сборки. В самом дереве её нет сознательно
# (live/core/secrets.py:31): живой агент постоянно обсуждает конфиги, и
# «PASSWORD=…» дал бы ложняки по его нормальной работе. У сборки этой причины
# нет, а находка, ради которой гард писали, была именно такой формы —
# SERVER_PASS от VPS в рабочей папке репозитория.
_ASSIGN_NAME = r"[A-Za-z0-9_\-]*(?:password|passwd|pass|secret|token|apikey|api_key|key)"
_ASSIGN_RE = re.compile(
    r"(?i)" + _ASSIGN_NAME + r"[A-Za-z0-9_\-]*[\"']?\s*[=:]\s*[\"']?([A-Za-z0-9_\-+/=.:~]{8,})")
# Значение должно выглядеть ЛИТЕРАЛОМ, а не кодом: `key = _sends_place_key(row)`
# и `secret = hmac.new(...)` — это 237 срабатываний на живом дереве, то есть
# гард, который никто не включит. Отбираем по трём признакам: не путь атрибута,
# не КОНСТАНТА_ЗАГЛАВНЫМИ, и не меньше трёх цифр внутри.
_ASSIGN_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_ASSIGN_CONST = re.compile(r"[A-Z][A-Z0-9_]*")
_ASSIGN_PLACEHOLDER = {"redacted", "changeme", "placeholder", "undefined", "example"}


def _assign_secret(text: str) -> str:
    """'' или метка, если в тексте есть присвоение секрета буквальным значением.

    Честная граница: словесный пароль с одной цифрой («hunter2pass») эта форма
    не увидит — три цифры отсекают код, но отсекают и его. Для таких значений
    есть installer/secret-strings.txt.
    """
    for m in _ASSIGN_RE.finditer(text):
        v = m.group(1)
        if sum(c.isdigit() for c in v) < 3:
            continue
        if _ASSIGN_DOTTED.fullmatch(v) or _ASSIGN_CONST.fullmatch(v):
            continue
        if v.lower() in _ASSIGN_PLACEHOLDER:
            continue
        # 05.10: пример из документации — значение, ОБОРВАННОЕ явным
        # многоточием сразу после захвата («object_key": "runs/sha256/…» в
        # docs/run_retention.md): класс захвата не-ASCII не ест, поэтому
        # смотрим текст после. Живой секрет с «…» на конце не пишут.
        if text[m.end():m.end() + 3].startswith(("…", "...")):
            continue
        return "присвоение секрета (" + m.group(0).split("=")[0].split(":")[0].strip()[:40] + "=…)"
    return ""


def _as_text(raw: bytes) -> str | None:
    if not raw:
        return None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    if b"\x00" not in raw:
        return raw.decode("utf-8", "replace")
    half = min(len(raw), 4096) // 2
    even = raw[0:4096:2].count(0)
    odd = raw[1:4096:2].count(0)
    if half and (even >= half * 0.8 or odd >= half * 0.8):
        return raw.decode("utf-16-le" if odd >= even else "utf-16-be", "replace")
    return None   # бинарь — не сканируем


def _file_text(p: Path) -> str | None:
    try:
        if p.stat().st_size > SCAN_MAX_BYTES:
            return None
        return _as_text(p.read_bytes())
    except OSError:
        return None


# Рантайм приходит с PyPI, и кред-пол находит в нём собственный ИСХОДНИК
# библиотеки: rsa (зависимость telethon) держит строку заголовка PEM в своём
# коде. Это не секрет, а формат. Перечислено поимённо, чтобы новое срабатывание
# в рантайме роняло сборку, а не тонуло в списке исключений по маске.
RUNTIME_KNOWN_FALSE = {
    ("Lib/site-packages/rsa/key.py", "private key"),
    ("Lib/site-packages/rsa/pem.py", "private key"),
}
# Рантайм сканируем по текстовым расширениям: 4778 файлов, ~20 секунд. Раньше он
# был исключён дважды — и из очистки корня (ROOT_KEEP), и из скана, то есть
# попавшее туда однажды ехало в каждый следующий релиз невидимо для обоих.
RUNTIME_SCAN_EXT = (".py", ".json", ".txt", ".cfg", ".ini", ".toml")
# Path(".env").suffix — пустая строка, поэтому имена без расширения проверяем
# отдельно: чужой .env, забытый в рантайме, и есть тот случай, ради которого это.
RUNTIME_SCAN_NAMES = (".env", ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials")


def scan_for_secrets(out: Path, live: Path, scan_runtime: bool) -> int:
    """Сканирует СОДЕРЖИМОЕ поставки: всё, что приехало с диска владельца, плюс
    рантайм по текстовым расширениям, когда он в этом прогоне пересобирался."""
    floor = _credential_floor(live)
    deny = _deny_strings()
    if deny:
        print(f"  страховка secret-strings.txt: {len(deny)} строк")
    else:
        print("  страховка НЕ ПОДКЛЮЧЕНА: installer/secret-strings.txt нет — "
              "буквальные значения (пароль сервера, адреса) не проверяются")
    targets: list[Path] = []
    # data/ здесь потому, что она обязана быть пустой: очистка корня её сносит,
    # сборка создаёт заново. Если однажды в ней что-то окажется — это увидят.
    # 25.09 (ревью V1-8): `server/` — серверные конфиги и compose; правка «для себя» уезжала
    # бы незамеченной.
    for rel in ("tree", "app", "licenses", "data", "server"):
        d = out / rel
        if d.is_dir():
            targets += [p for p in d.rglob("*") if p.is_file()]
    targets += [p for p in out.iterdir() if p.is_file()]
    hits: list[str] = []
    for p in targets:
        text = _file_text(p)
        if text is None:
            continue
        label = floor(text) or _assign_secret(text)
        if label:
            hits.append(f"{p.relative_to(out)} — {label}")
            continue
        for needle in deny:
            if needle in text:
                hits.append(f"{p.relative_to(out)} — строка из secret-strings.txt")
                break
    scanned = len(targets)
    rt = out / "runtime"
    if scan_runtime and rt.is_dir():
        rfiles = [p for p in rt.rglob("*")
                  if p.is_file() and (p.suffix.lower() in RUNTIME_SCAN_EXT
                                      or p.name.lower() in RUNTIME_SCAN_NAMES)]
        for p in rfiles:
            text = _file_text(p)
            if text is None:
                continue
            rel = p.relative_to(rt).as_posix()
            # Форму присвоения по чужому коду НЕ гоняем: в 4778 файлах чужих
            # пакетов она даст ложняки, а падать на них будет сборка выпуска.
            label = floor(text)
            if label and (rel, label) not in RUNTIME_KNOWN_FALSE:
                hits.append(f"runtime/{rel} — {label}")
                continue
            for needle in deny:
                if needle in text:
                    hits.append(f"runtime/{rel} — строка из secret-strings.txt")
                    break
        scanned += len(rfiles)
        print(f"  рантайм: просмотрено {len(rfiles)} текстовых файлов")
    elif rt.is_dir():
        print("  рантайм НЕ сканирован (--skip-runtime): он остался от прошлого прогона")
    if hits:
        raise SystemExit("СЕКРЕТ В ПОСТАВКЕ — сборка остановлена:\n  " + "\n  ".join(hits))
    return scanned


# --- лицензии влинкованного -----------------------------------------------------

LICENSE_FILE_GLOBS = ("LICENSE*", "LICENCE*", "COPYING*", "NOTICE*", "UNLICENSE*")


def _cargo_registry_src() -> Path | None:
    home = Path(os.environ.get("CARGO_HOME") or (Path.home() / ".cargo"))
    src = home / "registry" / "src"
    if not src.is_dir():
        return None
    dirs = [d for d in src.iterdir() if d.is_dir()]
    return dirs[0] if len(dirs) == 1 else (max(dirs, key=lambda d: len(list(d.iterdir()))) if dirs else None)


def _lock_crates(lock: Path) -> list[tuple[str, str]]:
    if not lock.is_file():
        return []
    out, name, ver, registry = [], "", "", False
    for line in lock.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s == "[[package]]":
            if name and ver and registry:
                out.append((name, ver))
            name, ver, registry = "", "", False
        elif s.startswith("name = "):
            name = s.split("=", 1)[1].strip().strip('"')
        elif s.startswith("version = "):
            ver = s.split("=", 1)[1].strip().strip('"')
        elif s.startswith("source = ") and "registry+" in s:
            registry = True
    if name and ver and registry:
        out.append((name, ver))
    return out


def collect_rust_licenses(out: Path, allow_partial: bool, live: Path | None = None,
                          *, parts: tuple[str, ...] = ("shell", "setup", "svc"),
                          include_body: bool = True,
                          exes: str = ("helene.exe, helene-setup.exe, helene-svc.exe, "
                                       "helene-bridge.exe и helene-body.exe")) -> int:
    """Тексты лицензий крейтов, статически влинкованных в наши exe.

    MIT требует включать уведомление об авторстве «in all copies or substantial
    portions». Раньше ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md обещал, что тексты «лежат в
    исходниках каждого проекта» — в поставке их не было ни одного. Собираем из
    локального реестра cargo, одинаковые тексты кладём один раз.

    С 0.3.1 сюда входит и `Cargo.lock` тела (tree/body): мост и тело едут в
    поставке как helene-bridge.exe и helene-body.exe. Вариант Praxis везёт одну
    оболочку — `parts=("shell",)`, `include_body=False`.
    """
    dest = out / "licenses" / "rust"
    crates: set[tuple[str, str]] = set()
    for part in parts:
        crates.update(_lock_crates(DESK / part / "Cargo.lock"))
    if not crates:
        raise SystemExit(f"не прочитались Cargo.lock ({'/'.join(parts)}) — "
                         "лицензии влинкованного собрать не из чего")
    body_lock = (live or LIVE_DEFAULT) / "body" / "Cargo.lock"
    body_crates = _lock_crates(body_lock) if include_body and body_lock.is_file() else []
    if include_body and not body_crates:
        msg = f"не прочитался Cargo.lock тела ({body_lock}) — лицензии моста и тела не собраны"
        if not allow_partial:
            raise SystemExit(msg)
        print("  ⚠ " + msg)
    crates.update(body_crates)
    registry = _cargo_registry_src()
    if registry is None:
        msg = ("нет локального реестра cargo (~/.cargo/registry/src) — "
               "тексты лицензий крейтов собрать не из чего.\n"
               "собери Rust-части на этой машине или запусти с --allow-partial")
        if not allow_partial:
            raise SystemExit(msg)
        print("  ⚠ " + msg.splitlines()[0])
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    texts = dest / "texts"
    texts.mkdir(exist_ok=True)
    index: list[str] = []
    missing: list[str] = []
    for name, ver in sorted(crates):
        crate_dir = registry / f"{name}-{ver}"
        files = []
        if crate_dir.is_dir():
            for glob in LICENSE_FILE_GLOBS:
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
    head = [
        f"# Лицензии Rust-крейтов, влинкованных в {exes}",
        "",
        f"Собрано автоматически при сборке поставки из Cargo.lock ({len(crates)} крейтов).",
        "Одинаковые тексты лежат в `texts/` по одному разу; ссылки ниже ведут на них.",
        "",
    ]
    if missing:
        head += [
            "Без файла лицензии в исходниках крейта (лицензия объявлена полем "
            "`license` в его Cargo.toml): " + ", ".join(missing) + ".",
            "",
        ]
    (dest / "README.md").write_text("\n".join(head + sorted(index)) + "\n",
                                    encoding="utf-8", newline="\n")
    return len(index)


# --- тексты поставки ----------------------------------------------------------

FIRST_RUN = """# Hélène · первый запуск

Программа не подписана сертификатом. При первом запуске Windows SmartScreen
может сказать «Система защитила ваш компьютер»: нажми «Подробнее», затем
«Выполнить в любом случае». Это одноразово.

1. Запусти `helene-setup.exe` — это установщик.
2. Пройди сцены: имя агента и своё, **конституция** (текст, по которому агент
   будет жить, — его можно править прямо там), откуда приходит модель, служба,
   установка. Конституцию стоит прочитать: это единственное решение установки,
   которое потом меняет только сам агент.
3. Программа встанет в свою папку; на последней сцене нажми «Открыть».
   Агент представится первым — первое слово за ним.

Дальше всё меняется в самой программе: значок шестерёнки внизу слева — экран
«Настройки» (модель и ключ, Telegram, телефон, служба, автозапуск, адрес
обновлений). Файл `helene.json` руками править не нужно: если он перестанет
читаться, программа скажет об этом отдельным окном, а агент не поднимется,
пока файл не починят.

Вкладка «Система» объясняет, из чего агент состоит и что происходит с
сообщением. Закрыть окно — не значит остановить агента: он продолжит жить в
значке у часов.

Все данные агента живут в `data/` внутри установленной папки. Её адрес
показан в «Настройки → О программе». Просто скопировать папку на другую
машину нельзя: службу, ярлыки и запись об установке Windows держит по
абсолютным путям — на новой машине поставь Hélène установщиком, а `data/`
скопируй поверх: в ней память, дневник и конституция.

Когда выйдет новая версия — рядом лежит `ОБНОВЛЕНИЕ.md`: там по шагам, что
остановить и что скопировать в сторону перед установкой поверх.
"""

# Шаблон конфига поставки. Он живёт ровно до установщика, который пишет свой,
# но живёт: если helene-setup.exe рядом не поднялся, окно читает ИМЕННО этот
# файл. Поэтому здесь нет ни одного «дефолта на всякий случай»: модель пуста
# (снят gpt-5.2 — его не знает реле), а ключи телефона, песочницы и обновлений
# присутствуют — карта устройства обещает владельцу, что они тут есть.
HELENE_JSON = """{
  "mode": "local",
  "agent_mode": "sandbox",
  "python": "runtime/python.exe",
  "app": "app/deskapp.py",
  "runner": "app/localharness/runner.py",
  "tree": "data",
  "code": "tree",
  "port": 8094,
  "agent": {
    "name": ""
  },
  "owner": {
    "name": "",
    "room": "Hélène"
  },
  "model": {
    "framework": "openai",
    "base_url": "https://api.openai.com/v1",
    "key": "",
    "model": "",
    "max_tokens": 8192
  },
  "telegram": {
    "bot_token": "",
    "owner_id": 0
  },
  "phone": {
    "enabled": false
  },
  "sandbox": {
    "enabled": true,
    "network": true
  },
  "service": {
    "session0": false
  },
  "computer": {
    "enabled": false,
    "port": 9480,
    "scopes": ["computer.read", "computer.files", "computer.process", "computer.apps"]
  },
  "update": {
    "url": "https://api.github.com/repos/josephsteuerjr/praxis/releases/latest"
  },
  "read_dotenv": false
}
"""


# --- версии -------------------------------------------------------------------

# Версия продукта объявлена в пакете desk: её спрашивает и эта сборка, и
# выкладка поставки на сервер, и один разбор Cargo.toml на обоих — то же правило,
# что и для состава.
product_version = deskpkg.product_version


def _git(path: Path, *args: str) -> str | None:
    try:
        # quotepath=false: иначе кириллические имена приезжают восьмеричными
        # escape-последовательностями, и сообщение об ошибке нечитаемо ровно там,
        # где его читают в спешке.
        r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(path), *args],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _git_head(path: Path) -> tuple[str, bool, int]:
    """Хэш HEAD, признак грязного дерева и число несохранённых записей.

    Раньше спрашивали только `rev-parse HEAD`. Собрать поставку из
    несохранённых правок — самый частый режим работы, и паспорт объявлял её
    сборкой коммита, в котором нет ни одной из них. Поле, которому верят,
    врало; RELEASE.md при этом предъявляет его владельцу как то, что
    «остаётся посмотреть глазами».
    """
    head = _git(path, "rev-parse", "HEAD")
    if head is None:
        return "", False, 0
    status = _git(path, "status", "--porcelain")
    if status is None:
        return head.strip(), False, 0
    lines = [s for s in status.splitlines() if s.strip()]
    return head.strip(), bool(lines), len(lines)


# Исходники, которых нет в git: поставка соберётся, а `git commit -a` даст дерево,
# которое НЕ СОБИРАЕТСЯ. Класс повторился дважды подряд и оба раза дошёл до конца
# работ незамеченным: первый — четыре файла (common/firewall_rule.rs, app/src/relay.ts,
# app/test/, setup/ui/src/scenes/legacy.ts), второй — те же четыре плюс ВСЯ душа
# агента (resources/soul/, 15 файлов), без которой установщик собирает продукт без
# единого навыка и молчит об этом. Поэтому проверка стоит здесь: сборка — последнее
# место, где это ловится до раздачи.
#
# Смотрим только на то, из чего собирается продукт: код, тексты в поставку, тесты.
# Черновики в корне и мусор редактора сборку не роняют.
_TRACK_SUFFIXES = (".rs", ".py", ".ts", ".tsx", ".js", ".mjs", ".css", ".html",
                   ".json", ".toml", ".ps1", ".md", ".ico", ".png")
_TRACK_ROOTS = ("app/", "setup/", "shell/", "svc/", "mobile/", "common/",
                "localharness/", "deskd/", "resources/", "installer/", "tests/",
                "ui-kit/", "docs/", "server/")


def _untracked_sources(path: Path) -> list[str]:
    """Пути, которые едут в продукт, но git о них не знает."""
    raw = _git(path, "status", "--porcelain", "--untracked-files=all")
    if raw is None:
        return []
    out: list[str] = []
    for line in raw.splitlines():
        if not line.startswith("?? "):
            continue
        rel = line[3:].strip().strip('"')
        if not rel.startswith(_TRACK_ROOTS):
            continue
        if any(part in ("__pycache__", "node_modules", "dist", "target", "build")
               for part in rel.split("/")):
            continue
        if rel.endswith("/") or Path(rel).suffix in _TRACK_SUFFIXES:
            out.append(rel)
    return sorted(out)


def _guard_untracked(path: Path) -> None:
    lost = _untracked_sources(path)
    if not lost:
        return
    print("  ⚠⚠ ИСХОДНИКИ НЕ В GIT — сборка остановлена:")
    for rel in lost:
        print(f"       {rel}")
    raise SystemExit(
        "добавь их в git (git add ...) или назови мусором в .gitignore: "
        "иначе `git commit -a` даст дерево, которое не собирается")


def _git_field(path: Path, label: str) -> tuple[str, bool]:
    _guard_untracked(path)
    head, dirty, n = _git_head(path)
    if not head:
        return "", False
    if dirty:
        print(f"  ⚠ {label}: {n} несохранённых записей git — паспорт пометит сборку как -dirty")
    return head + ("-dirty" if dirty else ""), dirty


# Версия внутри exe. Тауриевские helene.exe и helene-setup.exe несут ресурс
# VERSIONINFO (FileVersion в UTF-16); helene-svc.exe собирается голым cargo и
# версии внутри не несёт вовсе — про него так и говорится строкой, а не
# делается вид, что сверили.
def _exe_string(path: Path, key_name: str) -> str:
    """Значение строки VERSIONINFO (`FileVersion`, `ProductName`) как его несёт exe.

    ⚠ Ключ и значение в ресурсе разделены нулями-заполнителями, а за значением
    сразу лежит СЛЕДУЮЩАЯ строка таблицы. Прежний разбор нули просто выбрасывал,
    и `ProductName` приезжал склеенным с соседом («Praxis» + «0.4.8» → «Praxis0»,
    09.09) — поэтому режем по нулям и берём первый непустой кусок.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    key = key_name.encode("utf-16-le")
    i = raw.find(key)
    if i < 0:
        return ""
    seg = raw[i + len(key): i + len(key) + 160].decode("utf-16-le", "replace")
    for piece in seg.split("\x00"):
        piece = piece.strip()
        if piece:
            return piece
    return ""


def _exe_version(path: Path) -> str:
    m = re.match(r"\d+(?:\.\d+){1,3}", _exe_string(path, "FileVersion"))
    return m.group(0) if m else ""


# Имя продукта ВНУТРИ exe (Tauri пишет productName в VERSIONINFO.ProductName).
# Оболочка Hélène и оболочка Praxis — один крейт с разным TAURI_CONFIG; по одному
# лишь имени файла их не различить, и 08.09 копия с identity Hélène ушла в папку
# Praxis: single-instance фокусировал окно Миры вместо своего.
def _exe_product_name(path: Path) -> str:
    seg = _exe_string(path, "ProductName")
    m = re.match(r"[A-Za-z][A-Za-z0-9 ._-]{0,40}", seg)
    return m.group(0).strip() if m else ""


# --- главное ------------------------------------------------------------------

# Что кладёт в КОРЕНЬ поставки сама сборка. Всё остальное в корне — след
# прошлого прогона (helene.log от запуска окна из этой папки, exe прошлой
# версии, install.log) и в архив ехать не должно. Раньше вместо этого списка
# был кортеж из одного элемента ("install.log",), и он уже дважды разошёлся с
# тем, что знает установщик (setup/src/install.rs).
ROOT_KEEP = {"runtime"}   # дорого пересобирать; чистится отдельно, флагом


# --- вариант Praxis: окно к своему серверу ---------------------------------------

# Та же оболочка, собранная с другим productName/identifier (shell/build-praxis.ps1),
# в СВОЙ каталог сборки: target/ остаётся за helene.exe, target-praxis/ — за Praxis.
SHELL_PRAXIS_EXE = DESK / "shell" / "target-praxis" / "release" / "helene.exe"
PRAXIS_PRODUCT = "Praxis"
PRAXIS_IDENTIFIER = "app.praxis.desk"
PRAXIS_BUILD_HINT = "собери вариант: pwsh -File shell/build-praxis.ps1"
# 1.2: мастер Praxis — тот же helene-setup с фичей `praxis` (setup/build-praxis.ps1), в
# поставке — praxis-setup.exe; к нему пришивается хвост, как у Hélène.
SETUP_PRAXIS_EXE = DESK / "setup" / "target-praxis" / "release" / "helene-setup.exe"
SETUP_PRAXIS_HINT = "собери мастер варианта: pwsh -File setup/build-praxis.ps1"

# Конфиг варианта Praxis: режим remote, адрес и ключ впишет человек
# (installer/PRAXIS.md). `setup_complete` стоит, чтобы оболочка не искала
# helene-setup.exe: установщика у этого варианта нет по замыслу. Автопроверка
# обновлений выключена — канал общий с Hélène, окно ищет в релизе свой
# Praxis-<версия>.zip, а руками обновляться проще через распаковку поверх.
PRAXIS_JSON = """{
  "mode": "remote",
  "base": "",
  "key": "",
  "agent": {
    "name": "Praxis"
  },
  "owner": {
    "name": ""
  },
  "notifications": {
    "text": true
  },
  "update": {
    "auto": false,
    "url": "https://api.github.com/repos/josephsteuerjr/praxis/releases/latest"
  },
  "setup_complete": true
}
"""


def core_provenance(live: Path) -> dict:
    """Какого ядра эта поставка — и насколько объявленное издание сходится с делом.

    Раскладка 10.09 объявила: ядро в `praxis/`, издание Элен СЛОЕМ в
    `helene/core`. Пока это объявление никто не проверял, оно значило ровно
    столько же, сколько до 09.09 значило происхождение реле, — то есть ничего.

    Проверено 10.09 прибором `core_src.py --check`: выложенное ядро — её экспорт
    от 13.08, а поставка везёт рабочую копию, которая отслеживает её прод. Итог:
    47 файлов расходятся, не будучи объявленными (её же починки после 13.08), и
    7 объявлены зря — числа `EDITION.md` мерились против её ЖИВОГО дерева, а не
    против выложенного ядра. Значит наложение «ядро + слой» наше дерево сегодня
    не воспроизводит, и собирать из него — это выпустить ядро месячной давности.

    Поэтому здесь не отказ, а ЗАПИСЬ: поставка едет из рабочей копии (как и
    ехала), а паспорт честно говорит, чем в этот момент были выложенные ядро и
    слой и как далеко от них уехало то, что действительно поехало. Отказ стоит
    там, где ему место, — на сборке ИЗ ядра (`--from-core`).
    """
    try:
        note = core_src.passport(core_src.CORE_DEFAULT, core_src.LAYER_DEFAULT)
    except OSError:
        print("  ⚠ ядро и слой не прочитались — паспорт о них промолчит")
        return {}
    core, layer = note["core"], note["layer"]
    # 25.09 (ревью V1-5): в паспорте поставки лежали абсолютные пути машины сборки с именем
    # пользователя (во всех выпусках 0.8.0–0.8.8). Пути — от корня репозитория.
    repo = DESK.parent.resolve()
    for part in (core, layer):
        raw = str(part.get("path") or "")
        if raw:
            try:
                part["path"] = Path(raw).resolve().relative_to(repo).as_posix()
            except ValueError:
                part["path"] = Path(raw).name
    print(f"  ядро {core['path']}: {core['files']} файлов, отпечаток {core['digest'][:12]}"
          f"{' @ ' + core['head'] if core['head'] else ''}"
          f"{' (грязное)' if core['dirty'] else ''}")
    print(f"  слой {layer['files']} файлов, отпечаток {layer['digest'][:12]}")

    drift = core_src.compare(core_src.CORE_DEFAULT, core_src.LAYER_DEFAULT, live)
    note["drift"] = {"undeclared": len(drift["undeclared"]), "stale": len(drift["stale"]),
                     "only_ours": len(drift["only_ours"]),
                     "declared_ok": len(drift["declared_ok"]),
                     "drifted": len(drift["drifted"]),
                     "names_undeclared": drift["undeclared"], "names_stale": drift["stale"],
                     "names_drifted": drift["drifted"]}
    if drift["undeclared"] or drift["stale"] or drift["drifted"]:
        print(f"  ⚠ слой и дело разъехались: не объявлено {len(drift['undeclared'])}, "
              f"объявлено зря {len(drift['stale'])}, "
              f"слой отстал от дерева {len(drift['drifted'])} — "
              "python installer/core_src.py --check")
        print("    (поставка едет из рабочей копии — это записано в паспорте)")
    else:
        print("  слой сходится с фактической разницей")
    return note


def assemble_from_core(dest: Path, core: Path, layer: Path) -> int:
    """Собрать дерево как «ядро + слой»: сперва ядро, поверх — файлы издания.

    ⚠ Отказывается собирать, пока слой не описывает издание целиком. Иначе это
    не сборка из ядра, а сборка из ЧУЖОГО дерева под именем нашего: файл, который
    расходится и не объявлен, приедет в поставку в редакции ядра — молча и без
    следа. Ровно тот класс, из-за которого 09.09 в поставку уехало чужое реле.
    """
    drift = core_src.compare(core, layer, live_root(None))
    # ⚠ Объявить файл и положить в слой вчерашнюю его редакцию — по
    # последствиям то же, что не объявить вовсе: поставка соберётся не из
    # этого дерева. Проверялось только объявление, содержимое — нет (11.09).
    # `only_ours` тоже отказ (ревью 25.09, A11 F5): необъявленный новый модуль издания молча
    # выпал бы из сборки «ядро + слой» — sitecustomize.py спасало только то, что он объявлен.
    if drift["undeclared"] or drift["stale"] or drift["drifted"] or drift.get("only_ours"):
        raise SystemExit(
            f"из ядра собрать нельзя: слой не описывает издание.\n"
            f"  не объявлено, но расходится: {len(drift['undeclared'])}\n"
            f"  объявлено зря (совпадает):   {len(drift['stale'])}\n"
            f"  объявлено, но слой отстал:   {len(drift['drifted'])}"
            f" ({', '.join(drift['drifted'][:6])})\n"
            f"  только у нас, не объявлено:  {len(drift.get('only_ours') or [])}"
            f" ({', '.join((drift.get('only_ours') or [])[:6])})\n"
            "Подробно — python installer/core_src.py --check\n"
            "Расходиться могут обе стороны, и отказ не знает какая: либо выложенное "
            "ядро отстало от живого (свежий экспорт зеркала — её сторона), либо от "
            "него отстала рабочая копия. Смотреть в список: если там в основном её "
            "стенды и её файлы — отстали мы.")
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for src_root in (core, layer):
        for rel, path in core_src.files(src_root).items():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            n += 1
    return n


BODY_BUILT = BODY_TARGET.parent / "BODY-BUILT.json"


def body_provenance(body_exe: Path, bridge_exe: Path, allow_partial: bool, live: Path) -> dict:
    """Из чего собраны `helene-body.exe`/`helene-bridge.exe` в этой поставке (ревью 25.09, A9 F4).

    Тот же класс дефекта, что у реле 09.09: бинари тела берутся ГОТОВЫМИ из `_body_target`,
    и правка `body/crates/*.rs` без пересборки уезжала бы вчерашним exe при `complete: true`.
    Сборка тела (`installer/body_src.py --build`) пишет рядом `BODY-BUILT.json`: отпечаток
    исходника (`build_mac.body_source_digest(live/body)`) и суммы exe. Здесь — сверка.
    """
    import build_mac  # noqa: PLC0415 — сосед по installer/, тот же отпечаток, что у Mac
    # ⚠ Здесь стояло `LIVE / "body"` — имени LIVE в модуле нет (сборка 0.8.8 упала на
    # первом же прогоне). Исходник тела — ТО дерево, из которого собирается поставка
    # (`--tree`), тот же корень, что и у секрет-гарда.
    src = live / "body"
    digest, files = build_mac.body_source_digest(src) if (src / "Cargo.toml").is_file() else ("", 0)
    info: dict = {"source": "tree/body", "digest": digest or None, "files": files,
                  "exe_sha256": {"helene-body": sha256(body_exe) if body_exe.is_file() else None,
                                 "helene-bridge": sha256(bridge_exe) if bridge_exe.is_file() else None}}
    print(f"  исходник тела: {files} файлов, отпечаток {digest[:12] or '?'}")
    try:
        made = json.loads(BODY_BUILT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        made = None
    if not isinstance(made, dict):
        line = (f"чем собрано тело — неизвестно ({BODY_BUILT} нет): "
                "installer/body_src.py --build")
        if not allow_partial:
            raise SystemExit(line)
        print(f"  ⚠ {line}")
        info["stale"] = True
        return info
    same = (made.get("source_digest") == digest
            and made.get("exe_sha256", {}).get("praxis-body") == info["exe_sha256"]["helene-body"]
            and made.get("exe_sha256", {}).get("praxis-bridge") == info["exe_sha256"]["helene-bridge"])
    if not same:
        line = ("тело собрано не из этого исходника — пересобрать: installer/body_src.py --build "
                f"(отпечаток бинаря {str(made.get('source_digest', ''))[:12]}, исходника {digest[:12]})")
        if not allow_partial:
            raise SystemExit(line)
        print(f"  ⚠ {line}")
        info["stale"] = True
    else:
        info["built_utc"] = made.get("built_utc")
        print(f"  тело собрано из него же ({made.get('built_utc')})")
    return info


def relay_provenance(exe: Path, allow_partial: bool) -> dict:
    """Из чего собран `helene-relay.exe` в этой поставке.

    Реле — единственный бинарь поставки, исходник которого лежит ВНЕ обоих
    репозиториев: `_relay_prod_src/` — копия живого `/opt/relay/Code`. Пока о
    ней не спрашивал никто, поставка 09.09 уехала с реле от 03.09 при починке
    ссылок от 09.09, и узнали об этом случайно.

    Сборка отвечает на два вопроса сама: чем этот исходник является (отпечаток
    и записка о происхождении) и тем ли исходником собран бинарь. Второе — по
    отпечатку, записанному СБОРКОЙ реле (`installer/relay_src.py --build`), а
    не по времени файлов: обновление зеркала переписывает mtime, ничего не
    меняя по существу, и такая проверка кричала бы всегда.
    """
    total, files = relay_src.digest(relay_src.MIRROR)
    note = relay_src.stamp(relay_src.MIRROR)
    made = relay_src.built(relay_src.MIRROR)
    info = {"digest": total, "files": len(files), "source": note or None,
            "built": made or None}
    print(f"  исходник реле: {len(files)} файлов, отпечаток {total[:12]}")
    if note:
        print(f"  снят с {note.get('from', '?')} @ {str(note.get('commit', ''))[:7]}"
              f" ({note.get('pulled_utc', '?')})")
    else:
        print(f"  ⚠ происхождение исходника реле НЕ ЗАПИСАНО ({relay_src.STAMP} нет): "
              "installer/relay_src.py --pull --host <адрес>")

    if not made:
        print(f"  ⚠ чем собран helene-relay.exe — неизвестно ({relay_src.BUILT} нет): "
              "installer/relay_src.py --build")
        return info
    same_exe = (exe.is_file()
                and hashlib.sha256(exe.read_bytes()).hexdigest() == made.get("exe_sha256"))
    if made.get("digest") != total or not same_exe:
        line = ("helene-relay.exe собран не из этого исходника — пересобрать: "
                f"installer/relay_src.py --build (отпечаток бинаря "
                f"{str(made.get('digest', ''))[:12]}, исходника {total[:12]})")
        if not allow_partial:
            raise SystemExit(line)
        print(f"  ⚠ {line}")
        info["stale"] = True
    else:
        print(f"  собран из него же ({made.get('built_utc')})")
    return info


def relay_linux_provenance(binary: Path, allow_partial: bool) -> dict:
    """Из чего собран Linux-бинарь реле, который едет в поставку для сервера.

    Тот же вопрос и тот же ответ, что у `helene-relay.exe`, только цель другая.
    Отдельная проверка нужна потому, что бинари собираются РАЗНЫМИ командами
    (`--build` и `--build-linux`): пересобрать один и забыть другой — обычное
    дело, а разъехавшийся Linux-бинарь виден только на сервере и только тем,
    что агент молчит.
    """
    total, _files = relay_src.digest(relay_src.MIRROR)
    made = relay_src.built_linux(relay_src.MIRROR)
    if not made:
        line = (f"чем собран Linux-бинарь реле — неизвестно ({relay_src.LINUX_BUILT} нет): "
                "installer/relay_src.py --build-linux")
        if not allow_partial:
            raise SystemExit(line)
        print(f"  ⚠ {line}")
        return {}
    same = (binary.is_file()
            and hashlib.sha256(binary.read_bytes()).hexdigest() == made.get("exe_sha256"))
    if made.get("digest") != total or not same:
        line = ("Linux-бинарь реле собран не из этого исходника — пересобрать: "
                f"installer/relay_src.py --build-linux (отпечаток бинаря "
                f"{str(made.get('digest', ''))[:12]}, исходника {total[:12]})")
        if not allow_partial:
            raise SystemExit(line)
        print(f"  ⚠ {line}")
        return dict(made, stale=True)
    print(f"  Linux-бинарь реле собран из него же ({made.get('built_utc')})")
    return made


def build_praxis_app(args) -> None:
    """Поставка варианта Praxis: окно в режиме remote без ядра, рантайма и тела.

    Состав повторяет то, что 09.09 было собрано руками в Programs\\Praxis
    (ПЕРЕДАЧА-09.09 §3): exe варианта, значок, статика окна, helene.json-заготовка,
    документ подключения, лицензии. Ничего из дерева агента сюда не едет — и
    поэтому секрет-гард здесь сканирует только то, что положено, кред-полом из
    дерева, если оно есть рядом (без дерева гард честно объявляет себя пропущенным).
    """
    out = Path(args.out).resolve() / PRAXIS_PRODUCT
    version, declared = product_version()
    print(f"{PRAXIS_PRODUCT} (окно к своему серверу) {version}")
    print(f"дистрибутив -> {out}")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    print("оболочка:")
    if not SHELL_PRAXIS_EXE.is_file():
        raise SystemExit(f"нет {SHELL_PRAXIS_EXE}\n{PRAXIS_BUILD_HINT}")
    inside = _exe_version(SHELL_PRAXIS_EXE)
    want = declared.get("shell/Cargo.toml", "")
    if inside and want and inside != want:
        raise SystemExit(f"praxis.exe: внутри {inside}, а shell/Cargo.toml объявляет {want} — "
                         f"exe не пересобран после подъёма версии ({PRAXIS_BUILD_HINT})")
    product_inside = _exe_product_name(SHELL_PRAXIS_EXE)
    if product_inside != PRAXIS_PRODUCT:
        raise SystemExit(f"в {SHELL_PRAXIS_EXE} productName «{product_inside or '?'}», а нужен "
                         f"«{PRAXIS_PRODUCT}»: это оболочка Hélène, не вариант ({PRAXIS_BUILD_HINT})")
    shutil.copy2(SHELL_PRAXIS_EXE, out / "praxis.exe")
    print(f"  praxis.exe: положен (productName={product_inside}, версия {inside or '?'})")
    # Мастер варианта (1.2): им ставится и снимается Praxis — вместо страниц NSIS.
    if not SETUP_PRAXIS_EXE.is_file():
        raise SystemExit(f"нет {SETUP_PRAXIS_EXE}\n{SETUP_PRAXIS_HINT}")
    setup_inside = _exe_version(SETUP_PRAXIS_EXE)
    setup_want = declared.get("setup/Cargo.toml", "")
    if setup_inside and setup_want and setup_inside != setup_want:
        raise SystemExit(f"мастер Praxis: внутри {setup_inside}, а setup/Cargo.toml объявляет {setup_want} — "
                         f"не пересобран после подъёма версии ({SETUP_PRAXIS_HINT})")
    setup_product = _exe_product_name(SETUP_PRAXIS_EXE)
    if setup_product != "Praxis Setup":
        raise SystemExit(f"в {SETUP_PRAXIS_EXE} productName «{setup_product or '?'}», а нужен «Praxis Setup»: "
                         f"это мастер Hélène, не вариант ({SETUP_PRAXIS_HINT})")
    shutil.copy2(SETUP_PRAXIS_EXE, out / "praxis-setup.exe")
    print(f"  praxis-setup.exe: положен (мастер варианта, версия {setup_inside or '?'})")
    icon = DESK / "shell" / "icons-praxis" / "icon.ico"
    if not icon.is_file():
        raise SystemExit(f"нет значка варианта: {icon}")
    # Одним именем: praxis.ico — ровно то, что ищет оболочка для уведомлений и
    # ярлыка (<productName>.ico). Второй копии (praxis-pult.ico) не стало 18.09.2026
    # вместе со словом «пульт»: её не искал никто, она осталась от ручной
    # поставки 09.09.
    shutil.copy2(icon, out / "praxis.ico")

    print("окно:")
    # Настольный Praxis везёт УДАЛЁННОЕ издание окна: агент у него на сервере, и карточек
    # местного агента в этой сборке нет вовсе (разделение приложений 10.09).
    static_digest = copy_static(out / "app" / "static", deskpkg.REMOTE)
    print(f"  app/static: окно Praxis, {static_digest[:12]}")

    print("документы:")
    (out / "helene.json").write_text(PRAXIS_JSON, encoding="utf-8", newline="\n")
    copy_text_lf(DESK / "installer" / "PRAXIS.md", out / "PRAXIS.md")
    copy_text_lf(DESK / "installer" / "THIRD-PARTY.md", out / "ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md")
    copy_text_lf(DESK / "installer" / "ЛИЦЕНЗИЯ.md", out / "ЛИЦЕНЗИЯ.md")
    copy_text_lf(DESK / "installer" / "NOTICE", out / "NOTICE")
    n_lic = collect_rust_licenses(out, args.allow_partial, parts=("shell",),
                                  include_body=False, exes="praxis.exe")
    print(f"  лицензии крейтов: {n_lic}")

    print("паспорт сборки:")
    desk_head, desk_dirty = _git_field(DESK, "desk")
    manifest = {
        "product": PRAXIS_PRODUCT,
        "variant": "praxis",
        "identifier": PRAXIS_IDENTIFIER,
        "version": version,
        "built_utc": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "complete": True,
        "git": {"desk": desk_head, "desk_dirty": desk_dirty, "dirty": desk_dirty},
        "declared_versions": declared,
        "static": static_digest,
    }
    (out / "helene-build.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    print("секрет-гард:")
    live = live_root(args.tree)
    if live.is_dir():
        scanned = scan_for_secrets(out, live, scan_runtime=False)
        print(f"  просканировано файлов: {scanned} — чисто")
    elif args.allow_partial:
        print(f"  ⚠ пропущен: нет дерева агента ({live}) — кред-пол взять неоткуда")
    else:
        raise SystemExit(f"секрет-гарду нужен кред-пол из дерева агента, а его нет: {live}\n"
                         "укажи --tree PATH или HELENE_TREE_SRC (для отладки: --allow-partial)")

    write_payload_manifest(out, version, PRAXIS_PRODUCT)
    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"итого: {total / 1e6:.1f} МБ до сжатия")
    archive = out.parent / f"{PRAXIS_PRODUCT}-{version}"
    print("zip…")
    shutil.make_archive(str(archive), "zip", out.parent, PRAXIS_PRODUCT)
    zip_path = out.parent / f"{PRAXIS_PRODUCT}-{version}.zip"
    digest = sha256(zip_path)
    zip_path.with_suffix(".zip.sha256").write_text(
        f"{digest} *{zip_path.name}\n", encoding="utf-8", newline="\n")
    print(f"готово: {zip_path} ({zip_path.stat().st_size / 1e6:.1f} МБ)")
    print(f"sha256: {digest}")
    if not args.skip_setup_exe:
        build_setup_exe(out, version, product=PRAXIS_PRODUCT)


def run_stands(skip: bool, tree: Path | None = None) -> None:
    """Прогнать ВСЕ стенды перед сборкой: питон, окно, Rust.

    ⚠ Почему это делает сборка, а не человек по списку. Наборов три, и звались
    они тремя разными командами; набор окна забыли — и `actions.test.mjs`
    простоял красным от переделки ленты шагов до 09.09. Список в `RELEASE.md`
    помнить человеку, а сборке помнить нечего: она зовёт один файл
    (`tests/run_all.py`), а тот сам знает состав.

    Отказ здесь — отказ собирать. Полусборка с красным стендом называется
    выпуском ровно до первого запуска у владельца.
    """
    if skip:
        print("стенды: ПРОПУЩЕНЫ (--skip-tests) — это отладка, не выпуск")
        return
    runner = DESK / "tests" / "run_all.py"
    if not runner.is_file():
        raise SystemExit(f"нет прогона стендов: {runner}")
    print("стенды: питон, окно и Rust…" + (f" (дерево — {tree})" if tree else ""))
    # ⚠ 29.09: стенды звались без HELENE_TREE_SRC и проверяли дерево ПО УМОЛЧАНИЮ — соседний
    # `live`, стоявший на ветке 19.09, — а в поставку уезжало `--tree` (port-2409). Выпуски
    # 1.2.x проверялись не на том дереве, что уходило людям. Теперь — ровно на нём.
    env = dict(os.environ, HELENE_TREE_SRC=str(tree)) if tree else None
    done = subprocess.run([sys.executable, str(runner), "--rust"], cwd=str(DESK), env=env)
    if done.returncode != 0:
        raise SystemExit("стенды красные — сборка остановлена. "
                         "Чинить, а не собирать: подробности выше.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DESK / "installer" / "build"))
    parser.add_argument("--skip-runtime", action="store_true",
                        help="не пересобирать runtime (он уже в out) — только для отладки")
    parser.add_argument("--allow-partial", action="store_true",
                        help="разрешить неполную поставку (отладка); попадёт в паспорт сборки")
    parser.add_argument("--tree", default="",
                        help="путь к дереву агента (по умолчанию ../live или HELENE_TREE_SRC)")
    parser.add_argument("--variant", choices=("helene", "praxis"), default="helene",
                        help="helene — полная поставка Hélène (по умолчанию); praxis — издание "
                             "к серверу: то же окно в режиме remote, без ядра, рантайма и тела")
    parser.add_argument("--skip-setup-exe", action="store_true",
                        help="не собирать <Продукт>-<версия>-setup.exe (отладка)")
    parser.add_argument("--skip-tests", action="store_true",
                        help="не гонять стенды перед сборкой (отладка); в выпуске — никогда")
    parser.add_argument("--from-core", action="store_true",
                        help="собрать дерево как «ядро (../praxis) + слой (../helene/core)», "
                             "а не из рабочей копии. Откажется, пока слой не описывает "
                             "издание целиком — см. installer/core_src.py --check")
    args = parser.parse_args()
    if args.variant == "praxis":
        build_praxis_app(args)
        return
    out = Path(args.out).resolve() / "Helene"   # имя папки — латиницей
    cache = Path(args.out).resolve() / "cache"
    live = live_root(args.tree)
    version, declared = product_version()

    if not live.is_dir():
        raise SystemExit(
            f"нет дерева агента: {live}\n"
            "оно лежит в СОСЕДНЕМ репозитории. Укажи путь: --tree PATH или HELENE_TREE_SRC")

    if args.from_core:
        # Дерево собирается из объявленной раскладки, а не берётся с диска.
        # Проверка перед копированием, а не после: собрать и потом сказать
        # «кстати, оно не то» — это и есть тихий выпуск чужого дерева.
        staged_tree = Path(args.out).resolve() / "core-tree"
        shutil.rmtree(staged_tree, ignore_errors=True)
        n = assemble_from_core(staged_tree, core_src.CORE_DEFAULT, core_src.LAYER_DEFAULT)
        print(f"дерево собрано из ядра и слоя: {n} файлов -> {staged_tree}")
        live = staged_tree

    run_stands(args.skip_tests, live)

    print(f"Hélène {version}")
    print(f"дистрибутив -> {out}")
    print(f"дерево агента: {live}")
    if args.allow_partial:
        print("⚠ --allow-partial: это ОТЛАДОЧНАЯ полусборка, не выпуск")

    # Корень поставки чистится ЦЕЛИКОМ, кроме рантайма. Раньше сносились только
    # app/tree/data, и в корне доживали exe прошлого выпуска: сборка печатала
    # «⚠ helene.exe не найден», а в архив уезжал старый бинарь.
    if out.exists():
        for item in out.iterdir():
            if item.name in ROOT_KEEP:
                continue
            (shutil.rmtree(item) if item.is_dir() else item.unlink())
    out.mkdir(parents=True, exist_ok=True)

    # Внешние адреса трогаем ДО пятиминутной сборки рантайма: раньше busybox
    # качался последним, и недоступность frippery.org роняла сборку голым
    # трейсбеком после всей работы, а повторный прогон начинал рантайм заново.
    # fetch зовём ВСЕГДА, а не «если файла нет»: он сам решает про кэш и сам
    # сверяет сумму. С проверкой «if not exists» подменённый кэш обходил гард
    # целиком — сумма проверялась только у того, что скачано в этом прогоне.
    print("shell-шим:")
    busybox = cache / "busybox64.exe"
    fetch(BUSYBOX_URL, busybox)
    # MinGit — тоже до рантайма и по той же причине: сеть падает до долгой работы.
    fetch(MINGIT_URL, cache / Path(MINGIT_URL).name)

    # 1.2.1: голосовой движок живёт рядом с рантаймом, отдельным набором (split_voice).
    voice_stage = Path(args.out).resolve() / "voice-stage" / "site-packages"
    if args.skip_runtime:
        print("runtime: пропущен (--skip-runtime)")
    else:
        print("runtime:")
        # Набор — производное рантайма: пересобрали рантайм — прежний набор устарел.
        shutil.rmtree(voice_stage.parent, ignore_errors=True)
        build_runtime(out, cache)
    print("  голосовой набор — отдельно от рантайма…")
    voice_split = split_voice(out / "runtime" / "Lib" / "site-packages", voice_stage)
    print(f"  голосу — {len(voice_split['dists'])} пакетов, передвинуто файлов: {voice_split['moved']}")
    print("  дымовой тест рантайма (без голоса)…")
    freeze = smoke_runtime(out, [m for m in SMOKE_IMPORTS if m not in VOICE_IMPORTS])
    print(f"  импорты живы, пакетов: {len(freeze.splitlines())}")
    smoke_voice(out, voice_stage)
    print("  голос импортируется из набора")

    # busybox лежит ПРЯМО РЯДОМ с python.exe, не в подпапке и не в PATH:
    # CreateProcess ищет команду в каталоге приложения и System32 РАНЬШЕ PATH,
    # и `System32\bash.exe` (заглушка WSL) перехватывала имя при любом PATH.
    # Каталог приложения — наш runtime, и этот же порядок работает на нас.
    # Кладём безусловно из СВЕРЕННОГО кэша: очистка корня не трогает runtime/,
    # и «положить, только если файла нет» означало бы, что однажды испорченный
    # bash.exe переживёт любое число пересборок.
    bash_shim = out / "runtime" / "bash.exe"
    shutil.copy2(busybox, bash_shim)
    print("  runtime/bash.exe (busybox-w32) положен")
    print("git:")
    stage_git(out, cache)

    print("app:")
    staged = stage_payload(out, live, args.allow_partial)

    print("бинарники:")
    # Четвёртое поле — чью декларацию версии обязан подтвердить сам exe.
    # None у реле: это чужой крейт со своей версией.
    binaries = [
        (DESK / "shell" / "target" / "release" / "helene.exe", "helene.exe",
         "собери shell: cargo build --release --features custom-protocol",
         "shell/Cargo.toml"),
        (DESK / "svc" / "target" / "release" / "helene-svc.exe", "helene-svc.exe",
         "собери svc: cargo build --release",
         "svc/Cargo.toml"),
        (DESK / "setup" / "target" / "release" / "helene-setup.exe", "helene-setup.exe",
         "собери setup: npm --prefix setup/ui run build + cargo build --release --features custom-protocol",
         "setup/Cargo.toml"),
        # ⚠ Путь к зеркалу берётся у раскладки, а не собирается из ROOT: после
        # переезда `desk/` внутрь репозитория `ROOT / "_relay_prod_src"` стал
        # указывать в пустоту, и первая же сборка после переезда объявила
        # поставку неполной — при живом, только что собранном exe рядом.
        # Ровно от этого класса и заведён `layout.py`; здесь строка его
        # пережила.
        (relay_src.MIRROR / "target" / "release" / "codex-proxy-server.exe", "helene-relay.exe",
         "собери реле: cargo build --release в _relay_prod_src",
         None),
        # То же реле под Linux — для сервера: `server/serverboot.py` поднимает
        # его третьим ребёнком, и без него агент с подпиской ChatGPT, увезённый
        # на сервер архивом переноса, нем (ключи приезжают, поднять нечем).
        (relay_src.linux_binary(), "helene-relay",
         "собери реле под Linux (нужен докер): installer/relay_src.py --build-linux",
         None),
        # Тело руки `computer` — крейты дерева (tree/body), версия у них своя
        # (workspace 0.1.0), поэтому декларация — None, как у реле. Собираются
        # ВНЕ дерева (`--target-dir`), чтобы `live/` оставалось чистым.
        (BODY_TARGET / "praxis-bridge.exe", "helene-bridge.exe",
         "собери тело: в live/body — cargo build --release -p praxis-body -p praxis-bridge "
         f"--target-dir {BODY_TARGET.parent}",
         None),
        (BODY_TARGET / "praxis-body.exe", "helene-body.exe",
         "собери тело: в live/body — cargo build --release -p praxis-body -p praxis-bridge "
         f"--target-dir {BODY_TARGET.parent}",
         None),
    ]
    missing = []
    stale = []
    relay: dict = {}
    body: dict | None = None
    relay_linux: dict = {}
    for src, name, how, decl in binaries:
        if not src.is_file():
            missing.append(f"{name} — {how}")
            continue
        # Версию ВНУТРИ exe раньше не читал никто: подняли три Cargo.toml, не
        # пересобрали — и архив уезжал под новым номером со старыми бинарями,
        # с "complete": true и без единого предупреждения. У покупателя окно
        # сравнивало бы свою (старую) версию с тегом релиза и звало обновляться
        # НАВСЕГДА. Падаем так же, как падает разъезд трёх Cargo.toml.
        if decl:
            inside = _exe_version(src)
            want = declared.get(decl, "")
            if inside and want and inside != want:
                stale.append(f"{name}: внутри {inside}, а {decl} объявляет {want} — "
                             f"exe не пересобран после подъёма версии ({how})")
                continue
            if not inside:
                print(f"  {name}: версии внутри нет — сверить нечем")
        # Оболочка обязана быть СВОЕЙ: тот же крейт собирается и как Praxis
        # (shell/build-praxis.ps1, свой target-praxis/), и exe с чужим
        # productName в этой папке — смесь, а не поставка.
        if name == "helene.exe":
            product_inside = _exe_product_name(src)
            if product_inside and product_inside != "Helene":
                stale.append(f"{name}: внутри productName «{product_inside}», а это поставка "
                             f"Hélène — собран не тот вариант оболочки ({how})")
                continue
        shutil.copy2(src, out / name)
        print(f"  {name}: положен")
        if name == "helene-relay.exe":
            relay = relay_provenance(src, args.allow_partial)
        if name == "helene-body.exe":
            body = body_provenance(src, out / "helene-bridge.exe", args.allow_partial, live)
        if name == "helene-relay":
            relay_linux = relay_linux_provenance(src, args.allow_partial)
    if stale:
        raise SystemExit(
            "версия внутри exe не совпадает с объявленной, поставка была бы смесью:\n  "
            + "\n  ".join(stale))
    if missing:
        text = "поставка неполная:\n  " + "\n  ".join(missing)
        if not args.allow_partial:
            raise SystemExit(text + "\n(для отладочной полусборки: --allow-partial)")
        print("  ⚠ " + text)
    if (out / "helene.exe").is_file():
        shutil.copy2(DESK / "shell" / "icons" / "icon.ico", out / "helene.ico")
    if (out / "helene-svc.exe").is_file():
        for script in sorted((DESK / "installer" / "service").glob("*.ps1")):
            shutil.copy2(script, out / script.name)

    print("документы:")
    (out / "ПЕРВЫЙ-ЗАПУСК.md").write_text(FIRST_RUN, encoding="utf-8", newline="\n")
    # Одна норма переводов строк на всё, что человек открывает в Проводнике:
    # копии из репозитория приезжали с CRLF, а написанное сборщиком — с LF, и в
    # одной папке лежали оба вида.
    copy_text_lf(DESK / "installer" / "THIRD-PARTY.md", out / "ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md")
    copy_text_lf(DESK / "installer" / "ЛИЦЕНЗИЯ.md", out / "ЛИЦЕНЗИЯ.md")
    copy_text_lf(DESK / "resources" / "HELENE-MAP.md", out / "КАК-УСТРОЕН-HELENE.md")
    # Инструкции ПО ОБНОВЛЕНИЮ в поставке не было вовсе: единственная жила в
    # installer/RELEASE.md, который сюда не едет. Кнопка «Скачать» в окне
    # открывала архив на 80 МБ и дальше человек оставался один.
    copy_text_lf(DESK / "resources" / "ОБНОВЛЕНИЕ.md", out / "ОБНОВЛЕНИЕ.md")
    # 25.09 (K): правила расширений владельца — рядом с программой; карточка «Расширения»
    # окна и записка агенту ссылаются на него.
    copy_text_lf(DESK / "resources" / "РАСШИРЕНИЯ.md", out / "РАСШИРЕНИЯ.md")
    # Сервер: та же поставка разворачивается в Docker на Linux (server/README-СЕРВЕР.md):
    # Dockerfile, compose, надзор serverboot.py, шаблон конфига. Windows-части
    # (exe, runtime/) туда не копируются самим Dockerfile.
    server_out = out / "server"
    if server_out.exists():
        shutil.rmtree(server_out)
    server_out.mkdir()
    # Рекурсивно: у server/ появились ПОДПАПКИ (рецепт контейнера канала), а
    # плоский обход отдавал каталог в shutil.copy2 и валил сборку голым
    # PermissionError на предпоследнем шаге — после всей долгой работы.
    def _copy_server(src: Path, dst: Path) -> int:
        count = 0
        for item in sorted(src.iterdir()):
            if item.name.startswith(".") or item.name == "__pycache__":
                continue
            if item.is_dir():
                (dst / item.name).mkdir(parents=True, exist_ok=True)
                count += _copy_server(item, dst / item.name)
            elif item.suffix in (".md", ".py", ".yml", ".yaml", ".txt", ".json") or item.name == "Dockerfile":
                copy_text_lf(item, dst / item.name)
                count += 1
            else:
                shutil.copy2(item, dst / item.name)
                count += 1
        return count

    print(f"  server/: {_copy_server(DESK / 'server', server_out)} файлов")
    # Apache-2.0 §4(a): получатель кода обязан получить копию лицензии, §4(d) —
    # NOTICE. Дерево агента объявлено под Apache-2.0 в обоих документах, а
    # рядом с ним не было ни LICENSE, ни NOTICE.
    apache = (DESK / "installer" / "ЛИЦЕНЗИЯ.md").read_text(encoding="utf-8")
    # ⚠ Здесь переменная звалась `body` и затирала происхождение тела из body_provenance —
    # в паспорте 0.8.8 поле `body` оказалось текстом Apache (ревью V3 F2).
    license_text = apache.split("\n---\n", 1)[1].strip() if "\n---\n" in apache else apache
    (out / "tree" / "LICENSE").write_text(license_text + "\n", encoding="utf-8", newline="\n")
    copy_text_lf(DESK / "installer" / "NOTICE", out / "tree" / "NOTICE")
    copy_text_lf(DESK / "installer" / "NOTICE", out / "NOTICE")
    n_lic = collect_rust_licenses(out, args.allow_partial, live)
    print(f"  лицензии крейтов: {n_lic}")

    (out / "helene.json").write_text(HELENE_JSON, encoding="utf-8", newline="\n")
    (out / "data").mkdir(exist_ok=True)

    # Голосовой набор — до паспорта: его имя и сумма едут в паспорт, по ним окно на
    # машине владельца качает и сверяет движок (localharness/voice.py).
    print("голосовой набор…")
    voice_pack = pack_voice(voice_stage, out.parent / f"Helene-voice-{version}-windows.tar.zst",
                            {"product": "Hélène", "version": version, "python": PY_VERSION,
                             "dists": voice_split["dists"]})
    print(f"  {voice_pack['name']}: {voice_pack['bytes'] / 1e6:.1f} МБ "
          f"({voice_pack['unpacked_bytes'] / 1e6:.0f} МБ распакованным), sha256 {voice_pack['sha256']}")

    print("паспорт сборки:")
    desk_head, desk_dirty = _git_field(DESK, "desk")
    tree_head, tree_dirty = _git_field(live, "дерево агента")
    core = core_provenance(live)
    manifest = {
        "product": "Hélène",
        "version": version,
        "built_utc": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "complete": not (missing or not staged["phone"]),
        "partial_reason": missing + ([] if staged["phone"] else ["телефон (mobile/dist)"]),
        # Хэш с суффиксом -dirty и отдельный флаг: по хэшу без суффикса сборку
        # нельзя было отличить от сборки самого коммита.
        "git": {"desk": desk_head, "desk_dirty": desk_dirty,
                "tree": tree_head, "tree_dirty": tree_dirty,
                "dirty": desk_dirty or tree_dirty},
        "declared_versions": declared,
        "python": PY_VERSION,
        "tree_files": staged["tree_files"],
        # Отпечаток статики окна (тот же, что в app/static/.helene-static.json):
        # по нему установщик решает, менял ли выпуск интерфейс.
        "static": staged["static_digest"],
        # Пакет desk в этой поставке — тем же манифестом, что уезжает на
        # сервер: по отпечатку видно, одна ли там и здесь версия канала.
        # Реле: единственный бинарь, исходник которого лежит вне обоих
        # репозиториев. Отпечаток исходника и записка о происхождении — здесь.
        "relay": (dict(relay, linux=relay_linux or None) if relay
                  else ({"linux": relay_linux} if relay_linux else None)),
        # Ядро и слой издания — ДВА отпечатка, а не один по собранному дереву:
        # собранное дерево не отвечает на вопрос «какого ядра эта поставка», а
        # он и есть главный. Плюс расхождение объявленного слоя с делом, чтобы
        # его нельзя было не заметить (см. core_provenance).
        "core": core or None,
        # Тело: из каких крейтов и тем ли исходником собрано (см. body_provenance).
        "body": body or None,
        "desk": {"version": staged["desk"]["version"],
                 "flavor": staged["desk"]["flavor"],
                 "digest": staged["desk"]["digest"],
                 "files": len(staged["desk"]["files"]),
                 "skipped": [s["name"] for s in staged["desk"]["skipped"]]},
        # Точный состав скачанного и установленного. Пинов по хэшам у pip нет
        # (открытый остаток, см. RELEASE.md), но по этим двум спискам сборку
        # можно опознать и повторить: раньше выложенный архив нельзя было
        # сопоставить ни с чем.
        "downloads": {Path(url).name: SHA256.get(url, "") for url in SHA256},
        "packages": freeze.splitlines(),
        # 1.2.1: движок голоса — отдельный актив выпуска рядом с zip; окно качает его
        # вместе с моделью и сверяет по этой сумме (не по чужому слову сети).
        "voice_pack": voice_pack,
    }
    (out / "helene-build.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    # 1.2.5: отпечатки кода ЭТОЙ поставки — в её же shipped-code.json (исходный в
    # репозитории не трогается). Своя чистая копия (pristine/) есть у установки на ПК, но
    # архиву переноса из контейнера сервера (он не видит .updater/) и установке, у которой
    # pristine/ пропал, базу для правок агента даст только отпечаток.
    sys.path.insert(0, str(DESK / "server" / "updater"))
    import codecarry  # noqa: PLC0415
    prints_path = out / "server" / "updater" / "shipped-code.json"
    prints = json.loads(prints_path.read_text("utf-8")) if prints_path.is_file() else {}
    prints[f"{manifest['version']}/{staged['desk']['flavor']}"] = codecarry.code_prints(out)
    prints_path.write_text(json.dumps(prints, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"  отпечатки кода {manifest['version']}/{staged['desk']['flavor']}: "
          f"{len(prints[manifest['version'] + '/' + staged['desk']['flavor']])} файлов")

    # Гард стоит ПОСЛЕ паспорта и до архива: раньше он был раньше, и последний
    # файл поставки — тот самый, который собирается из git, pip freeze и
    # деклараций среды сборки, — не проверял никто.
    print("секрет-гард:")
    scanned = scan_for_secrets(out, live, scan_runtime=not args.skip_runtime)
    print(f"  просканировано файлов: {scanned} — чисто")

    # Опись поставки (1.2) — в корень: едет и в zip (кнопка «Обновить»), и в хвост
    # установщика; по ней мастер ведёт ход и отличает поставку от владельческого.
    # Голос в описи — для установщика: при обновлении с поставки, где движок жил в
    # рантайме (≤ 1.2.0), он переносит эти пакеты в voice/, а не выбрасывает.
    write_payload_manifest(out, version, "Helene", voice={
        "dists": sorted(voice_split["dists"]), "tops": voice_split["tops"], "python": PY_VERSION})
    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"итого: {total / 1e6:.1f} МБ до сжатия")
    # Имя архива с версией: две скачанные поставки в «Загрузках» раньше были
    # неразличимы. update_check ищет в релизе любой .zip — имя ему безразлично.
    archive = out.parent / f"Helene-{version}"
    print("zip…")
    shutil.make_archive(str(archive), "zip", out.parent, "Helene")
    zip_path = out.parent / f"Helene-{version}.zip"
    size = zip_path.stat().st_size
    # Контрольная сумма архива — рядом с ним и в RELEASE.md к релизу: релиз не
    # подписан, и это единственное, чем скачавший может проверить, что получил
    # ровно тот файл.
    digest = sha256(zip_path)
    zip_path.with_suffix(".zip.sha256").write_text(
        f"{digest} *{zip_path.name}\n", encoding="utf-8", newline="\n")
    print(f"готово: {zip_path} ({size / 1e6:.1f} МБ)")
    print(f"sha256: {digest}")
    if not args.skip_setup_exe:
        build_setup_exe(out, version)


# --- 1.2: поставка в хвосте мастера ---------------------------------------------
#
# Слово Егора 27.09: «без серых страниц NSIS — одно лицо от первого клика до
# открытого окна». Установщик для людей — один файл `Helene-<v>-setup.exe`: это сам
# мастер (`helene-setup.exe`), за ним архив поставки (tar, сжатый zstd) и 32 байта
# хвоста. Мастер читает собственный файл и раскладывает поставку рядом с целевой
# папкой (`<папка>.new`), без %TEMP%. Формат хвоста — `setup/src/payload.rs`, один
# и тот же здесь и там: `HLNPAYLD`, смещение архива (u64 LE), длина (u64 LE), первые
# 8 байт sha256 архива.
#
# Почему не тот же zip: 261 МБ против 172 МБ (zstd 19 с дальними совпадениями —
# рантайм питона полон повторов), а раскладка разжимает 650 МБ за пару секунд.
# Архив для кнопки «Обновить» в окне остаётся zip — его читает питон и Проводник.

TAIL_MAGIC = b"HLNPAYLD"
PAYLOAD_MANIFEST = ".helene-payload.json"
# Что мастер читает до раскладки — вперёд архива.
PAYLOAD_HEAD = ("helene-build.json", "app/static/.helene-static.json")
# Набор входа в ChatGPT до установки: реле и ядро питона рантайма без пакетов
# (вход поднимает на питоне сервер обратного вызова — `RELAY_PYTHON`).
KIT_TOP = ("helene-relay.exe",)
KIT_RUNTIME_SUFFIXES = (".exe", ".dll", ".pyd", ".zip", "._pth", ".cat")
# Имена верхнего уровня, которые после установки принадлежат владельцу, а не поставке.
OWNER_TOP = ("helene.json", "data")


def _payload_files(out: Path) -> list[str]:
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())


def write_payload_manifest(out: Path, version: str, product: str, *, voice: dict | None = None) -> dict:
    """Опись поставки `.helene-payload.json` — в корень поставки (едет и в zip, и в хвост).

    По ней мастер знает ход раскладки (файлов и байт), набор входа в ChatGPT и имена
    верхнего уровня: при следующем обновлении то, что было поставкой и выпуском убрано,
    не переносится как «владельческое»."""
    files = [f for f in _payload_files(out) if f != PAYLOAD_MANIFEST]
    kit = [f for f in files
           if f in KIT_TOP
           or (f.startswith("runtime/") and f.count("/") == 1 and f.endswith(KIT_RUNTIME_SUFFIXES))]
    top = sorted({f.split("/", 1)[0] for f in files} - set(OWNER_TOP))
    total = sum((out / f).stat().st_size for f in files)
    manifest = {
        "product": product,
        "version": version,
        "files": len(files),
        "bytes": total,
        "kit": kit,
        "top": top,
        "code_sha256": {f: __import__('hashlib').sha256((out / f).read_bytes()).hexdigest()
                        for f in files if f.startswith(('tree/', 'app/'))
                        and '/__pycache__/' not in f and not f.endswith('.pyc')},
    }
    if voice:
        manifest["voice"] = voice
    (out / PAYLOAD_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8", newline="\n")
    return manifest


def _payload_order(out: Path, manifest: dict) -> list[str]:
    files = _payload_files(out)
    head = [PAYLOAD_MANIFEST] + [f for f in PAYLOAD_HEAD if f in files]
    kit = [f for f in manifest.get("kit", []) if f in files and f not in head]
    rest = [f for f in files if f not in head and f not in kit]
    return head + kit + rest


def pack_payload(out: Path, dest: Path, manifest: dict, *, level: int = 19) -> tuple[int, str]:
    """tar поставки в порядке «опись → паспорт → набор входа → остальное», сжатый zstd.
    -> (длина, sha256 hex)."""
    import tarfile
    from compression import zstd
    order = _payload_order(out, manifest)
    opts = {
        zstd.CompressionParameter.compression_level: level,
        zstd.CompressionParameter.enable_long_distance_matching: 1,
        zstd.CompressionParameter.window_log: 27,
        zstd.CompressionParameter.nb_workers: max(1, (os.cpu_count() or 2) - 2),
    }
    with open(dest, "wb") as raw:
        with zstd.ZstdFile(raw, "w", options=opts) as zf:
            # PAX: длинные и кириллические имена («ПЕРВЫЙ-ЗАПУСК.md») без усечения.
            with tarfile.open(fileobj=zf, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                for rel in order:
                    info = tar.gettarinfo(str(out / rel), arcname=rel)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mode = 0o755 if rel.endswith(".exe") else 0o644
                    with open(out / rel, "rb") as fh:
                        tar.addfile(info, fh)
    return dest.stat().st_size, sha256(dest)


def stitch_setup_exe(wizard: Path, packed: Path, digest_hex: str, target: Path) -> Path:
    """exe мастера + архив + 32 байта хвоста — одним файлом."""
    offset = wizard.stat().st_size
    length = packed.stat().st_size
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "wb") as dst:
        with open(wizard, "rb") as src:
            shutil.copyfileobj(src, dst, 1 << 20)
        with open(packed, "rb") as src:
            shutil.copyfileobj(src, dst, 1 << 20)
        dst.write(TAIL_MAGIC + offset.to_bytes(8, "little") + length.to_bytes(8, "little")
                  + bytes.fromhex(digest_hex)[:8])
    os.replace(tmp, target)
    return target


def check_tail(exe: Path) -> dict:
    """Прочитать хвост готового установщика и сверить сумму — тем же правилом, что мастер."""
    size = exe.stat().st_size
    with open(exe, "rb") as fh:
        fh.seek(size - 32)
        raw = fh.read(32)
        if raw[:8] != TAIL_MAGIC:
            raise SystemExit(f"{exe.name}: хвоста HLNPAYLD нет")
        offset = int.from_bytes(raw[8:16], "little")
        length = int.from_bytes(raw[16:24], "little")
        if offset + length + 32 != size:
            raise SystemExit(f"{exe.name}: хвост врёт о границах архива")
        fh.seek(offset)
        h = hashlib.sha256()
        left = length
        while left:
            chunk = fh.read(min(left, 1 << 20))
            if not chunk:
                break
            h.update(chunk)
            left -= len(chunk)
    if h.digest()[:8] != raw[24:32]:
        raise SystemExit(f"{exe.name}: сумма архива в хвосте не сходится")
    return {"offset": offset, "length": length}


def build_setup_exe(out: Path, version: str, *, product: str = "Helene") -> Path:
    """Один установочный файл `<Продукт>-<версия>-setup.exe`.

    Мастер с поставкой в хвосте (1.2) — без NSIS, без распаковки в %TEMP%, с отменой и
    откатом (`setup/src/tx.rs`). Hélène — `helene-setup.exe`, Praxis — тот же мастер с
    фичей `praxis` (`praxis-setup.exe`, `setup/build-praxis.ps1`).
    """
    wizard = out / ("helene-setup.exe" if product == "Helene" else "praxis-setup.exe")
    if not wizard.is_file():
        raise SystemExit(f"нет {wizard}: мастер должен лежать в поставке до пришивания хвоста")
    manifest = json.loads((out / PAYLOAD_MANIFEST).read_text(encoding="utf-8"))
    target = out.parent / f"{product}-{version}-setup.exe"
    packed = out.parent / f".{product}-{version}.tar.zst"
    print("setup exe (мастер + поставка в хвосте)…")
    t0 = _dt.datetime.now()
    length, digest = pack_payload(out, packed, manifest)
    print(f"  архив: {length / 1e6:.1f} МБ ({manifest['files']} файлов, {manifest['bytes'] / 1e6:.0f} МБ до сжатия) "
          f"за {(_dt.datetime.now() - t0).total_seconds():.0f} с")
    stitch_setup_exe(wizard, packed, digest, target)
    packed.unlink(missing_ok=True)
    check_tail(target)
    full = sha256(target)
    target.with_name(target.name + ".sha256").write_text(f"{full} *{target.name}\n", encoding="utf-8", newline="\n")
    print(f"готово: {target} ({target.stat().st_size / 1e6:.1f} МБ)")
    print(f"sha256: {full}")
    return target


if __name__ == "__main__":
    main()
