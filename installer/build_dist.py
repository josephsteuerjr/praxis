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

# Зависимости ПОСТАВКИ — только то, что импортируется на пути продукта:
# ход (agent/llm/webtool) + труба (deskapp). Telethon/aiogram/paramiko/stt —
# другие тела, в продукт не едут; cryptography не нужна (telegram_confirmation
# агентом не импортируется — проверено грепом 31.08).
DEPS = [
    "anthropic", "openai", "httpx", "python-dotenv", "pillow",
    "aiohttp", "pypdf", "trafilatura", "charset-normalizer",
    "telethon==1.44.0",   # Telegram своим аккаунтом (MTProto)
]

# Дымовой тест рантайма: ровно то, что продукт импортирует на своём пути.
SMOKE_IMPORTS = ("anthropic", "openai", "httpx", "dotenv", "PIL",
                 "aiohttp", "pypdf", "trafilatura", "telethon")

DESK = Path(__file__).resolve().parent.parent
ROOT = DESK.parent
APP_DIST = DESK / "app" / "dist"     # UI окна — сборка Vite (npm --prefix app run build)
MOBILE_DIST = DESK / "mobile" / "dist"   # PWA телефона (npm --prefix mobile run build)

# Дерево агента живёт в СОСЕДНЕМ репозитории. Раньше путь был единственной
# константой: нет папки — rglob по несуществующему каталогу молча отдаёт
# пустой список, и сборка выпускала архив без tree/ и без единого слова.
LIVE_DEFAULT = ROOT / "live"
# Куда собираются мост и тело (крейты tree/body): рядом с репозиториями, не в
# `live/` — дерево Праксис при сборке остаётся чистым.
BODY_TARGET = ROOT / "_body_target" / "release"


def live_root(cli: str | None) -> Path:
    src = cli or os.environ.get("HELENE_TREE_SRC") or ""
    return Path(src).resolve() if src.strip() else LIVE_DEFAULT


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
]

# Имена и маски — на любой глубине: мусор сборки, тесты, её рабочие заметки.
TREE_EXCLUDE_NAMES = [
    "__pycache__", ".git", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "test_*.py", "conftest.py", "hands_diff.txt",
    "docker-compose*", "Dockerfile", "*.bak",
    "workspace_*.md", "*.pre-*", "moderation_shadow_corpus.json",
    "STATUS.md", "SYNC-HEAD.txt", "ПОРТ-СТАТУС-*.md",
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


STATIC_MANIFEST = ".helene-static.json"


def static_manifest(root: Path) -> dict:
    """Манифест статики окна: sha256 каждого файла и один общий отпечаток.

    По нему установщик решает, менял ли ВЫПУСК интерфейс (задача A §2, слово
    владельца 07.09: «если я не трогал статику — оставлять пользовательскую»).
    Сравниваются манифесты двух поставок — новой и той, что ставилась раньше, —
    а не файлы пользователя: правил ли он их, нас не касается.
    """
    files: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel == STATIC_MANIFEST:
            continue
        files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(
        "\n".join(f"{rel} {sha}" for rel, sha in files.items()).encode("utf-8")).hexdigest()
    return {"v": 1, "digest": digest, "files": files}


def copy_static(dst: Path) -> str:
    """UI окна — только сборка Vite; без неё сборка честно падает. -> отпечаток."""
    if not (APP_DIST / "index.html").is_file():
        raise SystemExit("нет app/dist — собери UI: npm --prefix app run build")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(APP_DIST, dst)
    manifest = static_manifest(dst)
    (dst / STATIC_MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8", newline="\n")
    return manifest["digest"]


def copy_mobile(dst: Path, allow_partial: bool) -> bool:
    """PWA телефона. Раньше её отсутствие было предупреждением, и поставка
    уезжала без страницы телефона — а README обещает её, и узнавал владелец об
    этом по 404 от /m/. Теперь это отказ, как и у окна."""
    if not (MOBILE_DIST / "index.html").is_file():
        if not allow_partial:
            raise SystemExit(
                "нет mobile/dist — собери телефон: npm --prefix mobile run build\n"
                "(для отладочной полусборки: --allow-partial)")
        print("  ⚠ mobile/dist нет — ТЕЛЕФОНА В ЭТОЙ ПОСТАВКЕ НЕТ (--allow-partial)")
        return False
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(MOBILE_DIST, dst)
    return True


def copy_resources(dst: Path) -> None:
    """Ресурсы продукта: каноническая конституция и всё, что читает boot.py.

    Рекурсивно: раньше брался только верхний уровень, и подпапка в resources/
    не поехала бы и не пожаловалась.

    __pycache__ и .pyc не едут: это байт-код с машины сборщика, поставке он не
    нужен, а секрет-гард бинарь не читает по построению — то есть через эту
    папку в архив уезжало то, что никем не проверено."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(DESK / "resources", dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


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
    """
    (dest / "app" / "deskd").mkdir(parents=True, exist_ok=True)
    shutil.copy2(DESK / "deskapp.py", dest / "app" / "deskapp.py")
    # Все модули пакета, а не перечисление имён: `rooms.py` (07.09) не уехал бы
    # в поставку, и канал падал бы на импорте — поставка без комнат нерабочая.
    for f in sorted((DESK / "deskd").glob("*.py")):
        shutil.copy2(f, dest / "app" / "deskd" / f.name)
    static_digest = copy_static(dest / "app" / "static")
    copy_resources(dest / "app" / "resources")
    phone = copy_mobile(dest / "app" / "mobile", allow_partial)
    # rglob, а не glob: подпакет в localharness раньше молча не уехал бы.
    for f in sorted((DESK / "localharness").rglob("*.py")):
        target = dest / "app" / "localharness" / f.relative_to(DESK / "localharness")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)

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
            "static_digest": static_digest}


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


def smoke_runtime(out: Path) -> str:
    """Рантайм обязан импортировать то, ради чего он собран.

    Раньше это не проверялось никогда: добавили зависимость, собрали с
    --skip-runtime — и падение случалось на машине владельца, при первом импорте.
    """
    py = out / "runtime" / "python.exe"
    if not py.is_file():
        raise SystemExit(
            f"нет рантайма: {py}\n"
            "с --skip-runtime рантайм должен уже лежать в папке сборки; "
            "для выпуска собирай без флага")
    code = "import " + ", ".join(SMOKE_IMPORTS)
    r = subprocess.run([str(py), "-c", code], capture_output=True, text=True,
                       timeout=300, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("рантайм не импортирует свои зависимости:\n" + (r.stderr or "").strip())
    r = subprocess.run([str(py), "-m", "pip", "freeze"], capture_output=True, text=True,
                       timeout=300, encoding="utf-8", errors="replace")
    return (r.stdout or "").strip()


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
    for rel in ("tree", "app", "licenses", "data"):
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


def collect_rust_licenses(out: Path, allow_partial: bool, live: Path | None = None) -> int:
    """Тексты лицензий крейтов, статически влинкованных в наши exe.

    MIT требует включать уведомление об авторстве «in all copies or substantial
    portions». Раньше ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md обещал, что тексты «лежат в
    исходниках каждого проекта» — в поставке их не было ни одного. Собираем из
    локального реестра cargo, одинаковые тексты кладём один раз.

    С 0.3.1 сюда входит и `Cargo.lock` тела (tree/body): мост и тело едут в
    поставке как helene-bridge.exe и helene-body.exe.
    """
    dest = out / "licenses" / "rust"
    crates: set[tuple[str, str]] = set()
    for part in ("shell", "setup", "svc"):
        crates.update(_lock_crates(DESK / part / "Cargo.lock"))
    if not crates:
        raise SystemExit("не прочитались Cargo.lock (shell/setup/svc) — "
                         "лицензии влинкованного собрать не из чего")
    body_lock = (live or LIVE_DEFAULT) / "body" / "Cargo.lock"
    body_crates = _lock_crates(body_lock) if body_lock.is_file() else []
    if not body_crates:
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
        "# Лицензии Rust-крейтов, влинкованных в helene.exe, helene-setup.exe, "
        "helene-svc.exe, helene-bridge.exe и helene-body.exe",
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
    "url": "https://api.github.com/repos/josephsteuerjr/helene/releases/latest"
  },
  "read_dotenv": false
}
"""


# --- версии -------------------------------------------------------------------

def _cargo_version(path: Path) -> str:
    if not path.is_file():
        return ""
    in_pkg = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("["):
            in_pkg = s == "[package]"
        elif in_pkg and s.startswith("version"):
            return s.split("=", 1)[1].strip().strip('"')
    return ""


def _json_version(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("version", ""))
    except (ValueError, OSError):
        return ""


def product_version() -> tuple[str, dict]:
    """Версия продукта = версия трёх Cargo.toml, и они обязаны совпадать.

    Раньше сверять было нечем: RELEASE.md просил поднять три числа, а
    разъехаться они могли молча, и в поставке версии не было вообще.
    """
    declared = {
        "shell/Cargo.toml": _cargo_version(DESK / "shell" / "Cargo.toml"),
        "setup/Cargo.toml": _cargo_version(DESK / "setup" / "Cargo.toml"),
        "svc/Cargo.toml": _cargo_version(DESK / "svc" / "Cargo.toml"),
        "shell/tauri.conf.json": _json_version(DESK / "shell" / "tauri.conf.json"),
        "setup/tauri.conf.json": _json_version(DESK / "setup" / "tauri.conf.json"),
        "app/package.json": _json_version(DESK / "app" / "package.json"),
        "mobile/package.json": _json_version(DESK / "mobile" / "package.json"),
        "setup/ui/package.json": _json_version(DESK / "setup" / "ui" / "package.json"),
    }
    core = {k: v for k, v in declared.items() if k.endswith("Cargo.toml")}
    if not all(core.values()):
        raise SystemExit("не прочиталась версия из Cargo.toml: " +
                         ", ".join(k for k, v in core.items() if not v))
    if len(set(core.values())) != 1:
        raise SystemExit("версии разъехались, поставка была бы смесью:\n  " +
                         "\n  ".join(f"{k} = {v}" for k, v in core.items()))
    return next(iter(core.values())), declared


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
def _exe_version(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    key = "FileVersion".encode("utf-16-le")
    i = raw.find(key)
    if i < 0:
        return ""
    seg = raw[i + len(key): i + len(key) + 160].decode("utf-16-le", "replace").replace("\x00", "")
    m = re.match(r"\d+(?:\.\d+){1,3}", seg)
    return m.group(0) if m else ""


# --- главное ------------------------------------------------------------------

# Что кладёт в КОРЕНЬ поставки сама сборка. Всё остальное в корне — след
# прошлого прогона (helene.log от запуска окна из этой папки, exe прошлой
# версии, install.log) и в архив ехать не должно. Раньше вместо этого списка
# был кортеж из одного элемента ("install.log",), и он уже дважды разошёлся с
# тем, что знает установщик (setup/src/install.rs).
ROOT_KEEP = {"runtime"}   # дорого пересобирать; чистится отдельно, флагом


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DESK / "installer" / "build"))
    parser.add_argument("--skip-runtime", action="store_true",
                        help="не пересобирать runtime (он уже в out) — только для отладки")
    parser.add_argument("--allow-partial", action="store_true",
                        help="разрешить неполную поставку (отладка); попадёт в паспорт сборки")
    parser.add_argument("--tree", default="",
                        help="путь к дереву агента (по умолчанию ../live или HELENE_TREE_SRC)")
    args = parser.parse_args()
    out = Path(args.out).resolve() / "Helene"   # имя папки — латиницей
    cache = Path(args.out).resolve() / "cache"
    live = live_root(args.tree)
    version, declared = product_version()

    if not live.is_dir():
        raise SystemExit(
            f"нет дерева агента: {live}\n"
            "оно лежит в СОСЕДНЕМ репозитории. Укажи путь: --tree PATH или HELENE_TREE_SRC")

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

    if args.skip_runtime:
        print("runtime: пропущен (--skip-runtime)")
    else:
        print("runtime:")
        build_runtime(out, cache)
    print("  дымовой тест рантайма…")
    freeze = smoke_runtime(out)
    print(f"  импорты живы, пакетов: {len(freeze.splitlines())}")

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
        (ROOT / "_relay_prod_src" / "target" / "release" / "codex-proxy-server.exe", "helene-relay.exe",
         "собери реле: cargo build --release в _relay_prod_src",
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
        shutil.copy2(src, out / name)
        print(f"  {name}: положен")
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
    # Сервер: та же поставка разворачивается в Docker на Linux (server/README-СЕРВЕР.md):
    # Dockerfile, compose, надзор serverboot.py, шаблон конфига. Windows-части
    # (exe, runtime/) туда не копируются самим Dockerfile.
    server_out = out / "server"
    if server_out.exists():
        shutil.rmtree(server_out)
    server_out.mkdir()
    for item in sorted((DESK / "server").iterdir()):
        if item.name.startswith(".") or item.name == "__pycache__":
            continue
        if item.suffix in (".md", ".py", ".yml", ".yaml", ".txt", ".json") or item.name == "Dockerfile":
            copy_text_lf(item, server_out / item.name)
        else:
            shutil.copy2(item, server_out / item.name)
    print(f"  server/: {len(list(server_out.iterdir()))} файлов")
    # Apache-2.0 §4(a): получатель кода обязан получить копию лицензии, §4(d) —
    # NOTICE. Дерево агента объявлено под Apache-2.0 в обоих документах, а
    # рядом с ним не было ни LICENSE, ни NOTICE.
    apache = (DESK / "installer" / "ЛИЦЕНЗИЯ.md").read_text(encoding="utf-8")
    body = apache.split("\n---\n", 1)[1].strip() if "\n---\n" in apache else apache
    (out / "tree" / "LICENSE").write_text(body + "\n", encoding="utf-8", newline="\n")
    copy_text_lf(DESK / "installer" / "NOTICE", out / "tree" / "NOTICE")
    copy_text_lf(DESK / "installer" / "NOTICE", out / "NOTICE")
    n_lic = collect_rust_licenses(out, args.allow_partial, live)
    print(f"  лицензии крейтов: {n_lic}")

    (out / "helene.json").write_text(HELENE_JSON, encoding="utf-8", newline="\n")
    (out / "data").mkdir(exist_ok=True)

    print("паспорт сборки:")
    desk_head, desk_dirty = _git_field(DESK, "desk")
    tree_head, tree_dirty = _git_field(live, "дерево агента")
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
        # Точный состав скачанного и установленного. Пинов по хэшам у pip нет
        # (открытый остаток, см. RELEASE.md), но по этим двум спискам сборку
        # можно опознать и повторить: раньше выложенный архив нельзя было
        # сопоставить ни с чем.
        "downloads": {Path(url).name: SHA256.get(url, "") for url in SHA256},
        "packages": freeze.splitlines(),
    }
    (out / "helene-build.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    # Гард стоит ПОСЛЕ паспорта и до архива: раньше он был раньше, и последний
    # файл поставки — тот самый, который собирается из git, pip freeze и
    # деклараций среды сборки, — не проверял никто.
    print("секрет-гард:")
    scanned = scan_for_secrets(out, live, scan_runtime=not args.skip_runtime)
    print(f"  просканировано файлов: {scanned} — чисто")

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


if __name__ == "__main__":
    main()
