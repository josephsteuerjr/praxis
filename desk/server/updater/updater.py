# -*- coding: utf-8 -*-
"""Исполнитель обновления Hélène на сервере — сосед СНАРУЖИ контейнера агента.

Повод — жалоба Дмитрия К (26.09): его агент весь день готовил обновление и упирался
в «нет доступа». Агент живёт внутри контейнера, который надо заменить; изнутри у него
нет докера, сокета и путей хоста, и каждый шаг шёл кругом через человека.

Отмычку внутрь агента (сокет докера в его контейнер) мы не даём: это был бы root на
хосте у всего, что умеет писать в его дерево. Вместо неё — этот исполнитель: свой
контейнер рядом, со своим сокетом, и файловый протокол в дереве агента — тот же
приём, что у надзора (`app/deskd/control.py`, раздел «обновление на сервере»; у меня —
своя копия, `protocol.py`, почему — в ней):

    memory/.control/update-plan.json          агент (рука update_request) или окно кладёт план
    memory/.control/update-plan.receipt.json  я отвечаю: сверил / жду «да» / иду / испытание / итог
    memory/.control/update-plan.confirm.json  «да» или «нет» человека
    memory/.control/update-plan.verdict.json  слово на испытании: «принимаю» или «сломано»
    memory/.control/updater.json              я о себе каждые ~5 с (окно и рука видят, что я есть)
    memory/.control/update-history.jsonl      итоги прошлых обновлений

Код агента (`tree/`, `app/`) с 27.09 лежит на диске сервера и смонтирован в его
контейнер: агент правит себя, и правка переживает пересборку. Поэтому обновление —
это не «заменить код», а «перенести правки агента на новую версию».

Что я делаю по «да» — в таком порядке, чтобы живой агент не трогался до последнего:
  1. качаю архив выпуска, сверяю sha256 и паспорт (версия, полнота), распаковываю рядом;
     держу под рукой чистый исходник ТЕКУЩЕЙ версии — базу, против которой видно, что
     в коде правил агент (из своего кэша или скачав её выпуск);
  2. откладываю прежний образ тегом отката и собираю новый ИЗ РАСПАКОВАННОГО — агент
     работает;
  3. репетирую расширения владельца под новым образом (как мастер на Windows);
  4. откладываю прежнюю поставку в копию и кладу новую; `data/` и `helene.json` не трогаю,
     а своё владельца в папках поставки (модели в `server/models`, `server/.env`, его
     override) переношу в новые папки;
  5. жду конца хода агента, останавливаю его контейнер, копирую `data/` (если план
     просит полную копию), ПЕРЕНОШУ ПРАВКИ АГЕНТА: его код против чистой прежней версии
     — это его правки; каждую кладу на новую версию: файл, который выпуск не трогал, —
     как есть; тронутый — трёхсторонним слиянием (git merge-file); что не легло — в новой
     версии стоит её вариант, а его — ему в `data/workspace/update-<версия>/` со всеми
     тремя сторонами и объяснением;
  6. поднимаю новую версию с теми же адресом, портом, именами хостов и моделями, что были
     у прежнего контейнера (владелец задавал их в командной строке — беру из контейнера);
  7. механические проверки по закрытому списку; провал любой — откат;
  8. ИСПЫТАНИЕ: агент сам проверяет себя — думает ли, помнит ли, живы ли руки,
     расширения и его перенесённые правки — и отвечает «принимаю» или «сломано».
     «Сломано» — откат; молчание до срока — НЕ откат: новая версия остаётся, владелец узнаёт из записки (04.10, слово владельца: откат не принудительный). Мозга нет — испытание пропускается словами.

Откат возвращает код и образ, но НЕ память и не `helene.json`: «откат кода не откатывает
память — это её жизнь, а не версия продукта» (Егор, 25.09). Копии `data/` и `helene.json`
лежат рядом и идут в дело, только если прежняя версия на данных новой не поднимается.
Правки, которые агент успел сделать в коде новой версии, откат отдаёт ему в workspace.

Прерванное (перезапуск посреди любого шага) я довожу сам: состояние пишется ДО каждого
шага, который трудно отменить, и восстановление смотрит, что сделано на самом деле.

Чего я не делаю по построению:
  * без «да» — ничего; «да» привязано к одноразовому ключу моей расписки: подтверждается
    ровно показанная версия и сумма. Честно о границе: «да» — файл в дереве агента, и
    агент своим shell может положить его сам (рука `update_request` передаёт «да» только
    в ходе, начатом словами владельца, но shell — не рука). Поэтому я и умею ровно одно:
    официальный выпуск новее текущего, с копией, переносом правок и откатом;
  * ставлю только официальный выпуск и только версию НОВЕЕ текущей. Адрес выпусков —
    из МОЕЙ среды, не из helene.json: тот правит агент;
  * не исполняю ничего из того, что агент может переписать: `app/` и `tree/` теперь на
    его диске, поэтому протокол у меня свой, а его код я только читаю как данные;
  * трогаю только один контейнер — названный мне (`HELENE_CONTAINER`) — и только его
    compose-проект, по меткам самого контейнера; никаких prune, down и --remove-orphans;
  * своё состояние держу в `<установка>/.updater/`, куда контейнеру агента хода нет;
    всё, что под `data/`, `tree/` и `app/`, читаю и пишу без следования ссылкам.

Запуск — из compose-файла рядом (`server/updater/docker-compose.yml`):
    python3 /opt/helene/server/updater/updater.py
Среда: HELENE_DIR (папка установки — ТЕМ ЖЕ путём, что на хосте), HELENE_CONTAINER,
HELENE_UPDATE_RELEASES, HELENE_UPDATE_ASSET, HELENE_UPDATE_KEEP, HELENE_UPDATE_INSECURE.
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protocol  # noqa: E402 — своя копия протокола, рядом с этим файлом

SCHEMA = "helene.updater.v1"
DEFAULT_RELEASES = "https://api.github.com/repos/josephsteuerjr/praxis/releases"
PRODUCT = "Hélène"

#: Внутри контейнера агента (Dockerfile: WORKDIR /opt/helene, compose: ../data).
IN_CONTAINER_ROOT = "/opt/helene"
IN_CONTAINER_DATA = "/opt/helene/data"
IN_CONTAINER_CONFIG = "/opt/helene/helene.json"
#: Порт канала ВНУТРИ контейнера фиксирован в compose ("127.0.0.1:${HELENE_PORT}:8094").
CONTAINER_PORT = "8094/tcp"

#: Код агента: эти папки лежат на диске сервера и смонтированы в его контейнер.
CODE_DIRS = ("tree", "app")
IN_CONTAINER_CODE = {"tree": "/opt/helene/tree", "app": "/opt/helene/app"}
# Обход кода, сравнение с чистой версией и перенос правок агента — в
# `codecarry.py` рядом: тем же переносом пользуется установщик на ПК.
from codecarry import (  # noqa: E402,F401
    CODE_IGNORE_DIRS, CODE_IGNORE_NAMES, CODE_IGNORE_SUFFIXES, MAX_CODE_FILE, MAX_DIFF,
    MAX_DIFF_FILE, MAX_MATERIALS, NOFOLLOW, NONBLOCK, BINARY, FWALK, members, read_member,
    link_target, executable, read_plain, snapshot, differs, code_edits, is_binary, merge_text,
    unified, _skip_why, _blocked, carry_code, _write_code, materials_readme)

#: Что остаётся на месте при подмене: данные агента, его конфиг и моя папка. Остальное —
#: поставка, её меняем целиком (как «распаковать поверх» в README-СЕРВЕР).
KEEP_IN_PLACE = frozenset({"data", "helene.json", ".updater"})
#: Чего из архива серверу не нужно: `runtime/` — встроенный Python для Windows (~590 МБ).
#: Его не распаковываю и не трогаю — на сервере Python свой, в образе.
SERVER_SKIP = frozenset({"runtime"})

#: Без чего распакованная поставка — не поставка сервера. Исполнитель — тоже: после
#: успеха я перезапускаюсь кодом новой поставки, и выпуск без него оставил бы сервер
#: без исполнителя (docker поднимал бы пустое место по кругу).
REQUIRED = ("helene-build.json", "requirements.txt", "app/deskapp.py", "app/desk.json",
            "app/localharness/runner.py", "app/deskd/control.py", "tree",
            "server/Dockerfile", "server/docker-compose.yml", "server/serverboot.py",
            "server/updater/updater.py", "server/updater/protocol.py")

#: Переменные compose-файла агента -> откуда взять их значение в ЖИВОМ контейнере.
#: Владелец задаёт их в командной строке (`HELENE_HOSTS=… docker compose up`), и нигде
#: больше они не записаны: подними я новый контейнер без них — канал перестал бы
#: пускать окно по имени хоста, а голос потерял бы модель.
CARRY_ENV = {
    "HELENE_PUBLIC_URL": "HELENE_PUBLIC_URL",
    "HELENE_HOSTS": "HELENE_HOSTS",
    "HELENE_STT_THREADS": "PRAXIS_STT_CPU_THREADS",
    "HELENE_STT_KEEP_LOADED": "PRAXIS_STT_KEEP_LOADED",
}

MAX_SHARED = 64 * 1024            # файл обмена больше этого — не наш файл
MAX_ARCHIVE = 2 * 1024 ** 3       # архив выпуска больше 2 ГБ — не наш архив
MAX_CONFIG = 4 * 1024 ** 2        # helene.json больше этого — в копию не беру (его пишет агент)
PROBE_LIMIT = 1024 ** 2           # ответ пробы больше мегабайта — мусор, а не ответ
SPACE_MARGIN = 512 * 1024 ** 2
BEAT_EVERY = 5.0
LATEST_EVERY = 6 * 3600
IDLE_WAIT = 180                   # сколько ждать конца хода агента перед остановкой
TRIAL_EXTEND = 600                # насколько продлить испытание агенту, занятому ходом



class UpdateError(RuntimeError):
    """Отказ шага с причиной словами — она уходит в расписку как есть."""


def said(exc: BaseException) -> str:
    """Причина словами: своя ошибка — как есть, чужая — с именем типа («KeyError: 'x'»)."""
    return str(exc) if isinstance(exc, UpdateError) else f"{type(exc).__name__}: {exc}"


def utc(epoch: float | None = None) -> str:
    moment = dt.datetime.fromtimestamp(epoch, dt.UTC) if epoch else dt.datetime.now(dt.UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def mb(size: int | float) -> str:
    return f"{size / 1024 ** 2:.0f} МБ" if size < 1024 ** 3 else f"{size / 1024 ** 3:.1f} ГБ"


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def clean(text: str) -> str:
    """Строка для журнала: без управляющих символов терминала, продолжения — с отступом.

    В журнал попадают и слова агента (вердикт, заметки пробы, имена его файлов), а
    `docker logs helene-updater` владелец читает в своём терминале: ESC-последовательность
    оттуда перекрасила бы или стёрла ему экран, а перевод строки нарисовал бы поддельную
    строку «[updater …] готов».
    """
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group(0)):02x}", str(text)).replace("\n", "\n    ")


def log(text: str) -> None:
    print(f"[updater {utc()}] {clean(text)}", flush=True)


# --------------------------------------------------------------------------- #
#  Папка обмена в дереве агента — без следования ссылкам
# --------------------------------------------------------------------------- #

class Shared:
    """Чтение и запись под `data/` так, что подложенная агентом ссылка не уводит наружу.

    `data/` целиком во власти агента: `memory` или `.control` могут оказаться ссылкой
    на `server/` установки, а файл плана — ссылкой на FIFO. Поэтому каждый шаг пути
    открывается с O_NOFOLLOW относительно предыдущего, файлы читаются только обычные и
    небольшие, а запись идёт через новый файл со случайным именем и `rename` —
    он заменяет саму ссылку, а не пишет туда, куда она ведёт.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir_fd(self, parts: tuple[str, ...], create: bool) -> int:
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts:
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                try:
                    nxt = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(part, 0o755, dir_fd=fd)
                    nxt = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = nxt
            return fd
        except BaseException:
            os.close(fd)
            raise

    def exists(self, *parts: str) -> bool:
        try:
            os.close(self._dir_fd(tuple(parts), create=False))
            return True
        except OSError:
            return False

    def read(self, *path: str) -> dict:
        *parts, name = path
        try:
            dfd = self._dir_fd(tuple(parts), create=False)
        except OSError:
            return {}
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        except OSError:
            return {}
        finally:
            os.close(dfd)
        with os.fdopen(fd, "rb") as fh:
            info = os.fstat(fh.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SHARED:
                return {}
            blob = fh.read(MAX_SHARED + 1)
        try:
            data = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, *path_and_data) -> None:
        *path, data = path_and_data
        *parts, name = path
        dfd = self._dir_fd(tuple(parts), create=True)
        try:
            tmp = f".tmp-{name}-{secrets.token_hex(6)}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644,
                         dir_fd=dfd)
            with os.fdopen(fd, "wb") as fh:
                fh.write((json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
            os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        finally:
            os.close(dfd)

    def put(self, parts: tuple[str, ...], blob: bytes) -> None:
        """Новый файл по вложенному пути (папки заводятся без следования ссылкам).

        Файл обязан быть новым (O_EXCL): кладу только в свежую папку, и занятое место —
        признак того, что туда что-то подложили.
        """
        *dirs, name = parts
        dfd = self._dir_fd(tuple(dirs), create=True)
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644,
                         dir_fd=dfd)
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
        finally:
            os.close(dfd)

    def append(self, *path_and_row) -> None:
        """Строка в конец журнала. FIFO на его месте — ENXIO, а не вечное ожидание
        читателя (без O_NONBLOCK любой итог вешал бы исполнителя навсегда)."""
        *path, row = path_and_row
        *parts, name = path
        dfd = self._dir_fd(tuple(parts), create=True)
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o644, dir_fd=dfd)
            with os.fdopen(fd, "ab") as fh:
                if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                    return
                fh.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(dfd)

    def remove(self, *path: str) -> None:
        *parts, name = path
        try:
            dfd = self._dir_fd(tuple(parts), create=False)
        except OSError:
            return
        try:
            os.unlink(name, dir_fd=dfd)
        except OSError:
            pass
        finally:
            os.close(dfd)


CTL = ("memory", ".control")


# --------------------------------------------------------------------------- #
#  Докер и сеть — за узкими дверями, чтобы стенды подменяли их целиком
# --------------------------------------------------------------------------- #

class Docker:
    """Клиент `docker` (с плагином compose) из образа исполнителя, по сокету хоста.

    Вывод читается с пределом: stdout — с начала (inspect отдаёт JSON), stderr — хвост
    (ошибка сборки — в конце). Вывод пробы (`docker exec`) пишет агент — его python в
    его контейнере, — и без предела гигабайт мусора раздул бы память исполнителя.
    Больше предела — команда прерывается: такой ответ всё равно не разобрать.
    """

    OUT_LIMIT = 8 * 1024 ** 2
    ERR_TAIL = 256 * 1024

    def run(self, args: list[str], *, env: dict | None = None, timeout: float = 600,
            limit: int = OUT_LIMIT) -> tuple[int, str, str]:
        full = dict(os.environ)
        full.update({k: str(v) for k, v in (env or {}).items()})
        try:
            proc = subprocess.Popen(["docker", *args], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full)
        except OSError as exc:
            return 127, "", f"docker не запускается: {exc}"
        out, err, over = bytearray(), bytearray(), threading.Event()

        def pump_out() -> None:
            for chunk in iter(lambda: proc.stdout.read(65536), b""):
                if len(out) + len(chunk) > limit:
                    over.set()
                    proc.kill()
                    return
                out.extend(chunk)

        def pump_err() -> None:
            for chunk in iter(lambda: proc.stderr.read(65536), b""):
                err.extend(chunk)
                del err[:-self.ERR_TAIL]

        pumps = [threading.Thread(target=pump_out, daemon=True),
                 threading.Thread(target=pump_err, daemon=True)]
        for pump in pumps:
            pump.start()
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            code = 124
            err.extend(f"\ndocker {' '.join(args[:3])}: не уложился в {timeout:.0f} с".encode())
        for pump in pumps:
            pump.join(5)
        if over.is_set():
            code = 125
            err.extend(f"\ndocker {' '.join(args[:3])}: вывод больше {mb(limit)} — прерван".encode())
        return code, bytes(out).decode("utf-8", "replace"), bytes(err).decode("utf-8", "replace")


class Net:
    """GitHub Releases по HTTPS. Небезопасный http — только с HELENE_UPDATE_INSECURE=1."""

    def __init__(self, insecure: bool = False):
        self.insecure = insecure

    def _check(self, url: str) -> None:
        if url.startswith("https://") or (self.insecure and url.startswith("http://")):
            return
        raise UpdateError(f"адрес не https: {url[:120]}")

    def _open(self, url: str, timeout: float):
        self._check(url)
        request = urllib.request.Request(url, headers={
            "User-Agent": "helene-updater", "Accept": "application/vnd.github+json"})
        answer = urllib.request.urlopen(request, timeout=timeout)
        self._check(answer.geturl())      # переадресация тоже обязана быть https
        return answer

    def json(self, url: str) -> dict:
        try:
            with self._open(url, 30) as answer:
                data = json.loads(answer.read(4 * 1024 * 1024).decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise UpdateError(f"{url} ответил {exc.code}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise UpdateError(f"{url} не ответил: {exc}") from exc
        if not isinstance(data, dict):
            raise UpdateError(f"{url} ответил не объектом")
        return data

    def text(self, url: str, limit: int = 4096) -> str:
        try:
            with self._open(url, 30) as answer:
                return answer.read(limit).decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            raise UpdateError(f"{url} не ответил: {exc}") from exc

    def download(self, url: str, dest: Path, limit: int, progress=None) -> str:
        """Скачать в `dest`, считая sha256 на лету. -> sha256."""
        digest = hashlib.sha256()
        got = 0
        try:
            with self._open(url, 60) as answer, open(dest, "wb") as out:
                while True:
                    chunk = answer.read(1024 * 1024)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > limit:
                        raise UpdateError(f"архив больше {mb(limit)} — не наш архив")
                    digest.update(chunk)
                    out.write(chunk)
                    if progress:
                        progress(got)
        except (urllib.error.URLError, OSError) as exc:
            raise UpdateError(f"архив не скачался: {exc}") from exc
        return digest.hexdigest()


# --------------------------------------------------------------------------- #
#  Чистые разборы — их и проверяют стенды
# --------------------------------------------------------------------------- #

_HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")


def sha_in_text(text: str, name: str = "") -> str:
    """sha256 из `.sha256`-файла или заметок релиза: строка с именем архива, иначе первая."""
    lines = str(text or "").splitlines()
    if name:
        for line in lines:
            if name.lower() in line.lower():
                found = _HEX64.search(line)
                if found:
                    return found.group(0).lower()
    found = _HEX64.search(str(text or ""))
    return found.group(0).lower() if found else ""


def pick_release(rel: dict, asset_prefix: str) -> dict:
    """Ответ GitHub Releases -> то, что нужно исполнителю. Без суммы — отказ."""
    tag = str(rel.get("tag_name") or "")
    version = tag.strip().lstrip("vV")
    if protocol.version_tuple(version) is None:
        raise UpdateError(f"выпуск назван «{tag}» — это не номер версии")
    if rel.get("draft") or rel.get("prerelease"):
        raise UpdateError(f"{tag} — черновик или предварительный выпуск, такие я не ставлю")
    name = f"{asset_prefix}-{version}.zip"
    assets = [a for a in rel.get("assets") or [] if isinstance(a, dict)]
    asset = next((a for a in assets if str(a.get("name") or "") == name), None)
    if asset is None:
        raise UpdateError(f"в выпуске {tag} нет архива {name}")
    url = str(asset.get("browser_download_url") or "")
    digest = str(asset.get("digest") or "").strip().lower()
    sha = digest[7:] if digest.startswith("sha256:") else ""
    side = next((str(a.get("browser_download_url") or "") for a in assets
                 if str(a.get("name") or "") == name + ".sha256"), "")
    body = str(rel.get("body") or "")
    notes = next((line.strip() for line in body.splitlines()
                  if line.strip() and not line.strip().startswith("#")), "")[:200]
    return {"tag": tag, "version": version, "asset": name, "url": url,
            "size": int(asset.get("size") or 0), "sha256": sha, "sha_url": side,
            "sha_from_notes": sha_in_text(body, name), "notes": notes,
            "html_url": str(rel.get("html_url") or ""),
            "published_at": str(rel.get("published_at") or "")}


def describe(row: dict) -> dict:
    """Ответ `docker inspect` о контейнере -> то, что важно для подмены."""
    cfg = row.get("Config") or {}
    labels = cfg.get("Labels") or {}
    host = row.get("HostConfig") or {}
    state = row.get("State") or {}
    env = {}
    for item in cfg.get("Env") or []:
        key, _, value = str(item).partition("=")
        env[key] = value
    mounts = {str(m.get("Destination") or ""): str(m.get("Source") or "")
              for m in row.get("Mounts") or [] if isinstance(m, dict)}
    files = str(labels.get("com.docker.compose.project.config_files") or "")
    return {
        "name": str(row.get("Name") or "").lstrip("/"),
        "image_id": str(row.get("Image") or ""),
        "image": str(cfg.get("Image") or ""),
        "project": str(labels.get("com.docker.compose.project") or ""),
        "service": str(labels.get("com.docker.compose.service") or ""),
        "working_dir": str(labels.get("com.docker.compose.project.working_dir") or ""),
        "config_files": [f for f in files.split(",") if f],
        "env": env,
        "ports": host.get("PortBindings") or {},
        "mounts": mounts,
        "running": bool(state.get("Running")),
        "restarting": bool(state.get("Restarting")),
        "status": str(state.get("Status") or ""),
        "restart_count": int(row.get("RestartCount") or 0),
        "started_at": str(state.get("StartedAt") or ""),
    }


def carried(info: dict) -> dict:
    """Переменные compose-файла агента — из живого контейнера, а не из догадки."""
    out = {}
    for var, key in CARRY_ENV.items():
        if key in info["env"]:
            out[var] = info["env"][key]
    binding = (info["ports"].get(CONTAINER_PORT) or [{}])[0] or {}
    if binding.get("HostPort"):
        out["HELENE_PORT"] = str(binding["HostPort"])
    if info["mounts"].get("/models"):
        out["HELENE_MODELS"] = info["mounts"]["/models"]
    return out


def code_mounted(info: dict) -> bool:
    """Код агента в этом контейнере — с диска сервера (compose с 27.09), а не из образа."""
    mounts = info.get("mounts") or {}
    return all(mounts.get(IN_CONTAINER_CODE[name]) for name in CODE_DIRS)


def image_repo(image: str) -> tuple[str, str]:
    """«helene-helene» / «helene-helene:latest» -> (repo, tag)."""
    head, _, last = image.rpartition("/")
    name, sep, tag = last.partition(":")
    repo = f"{head}/{name}" if head else name
    return repo, (tag if sep else "latest")


def iso_epoch(text: str) -> float:
    """«2026-09-27T10:00:05.123456789Z» (докер, наносекунды) -> эпоха, с точностью до секунды."""
    try:
        return dt.datetime.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=dt.UTC).timestamp()
    except ValueError:
        return 0.0


# Записки надзора и раннера, как и весь вывод пробы, пишет агент: его `data/` и его python
# в его контейнере. Разбор — без доверия к виду: не словарь — пусто, не число — ноль, а
# ноль нигде не засчитывается «свежим». Мусор даёт «не прошло», а не падение исполнителя
# (прежде `float("abc")` на проверке заклинивал обновление навсегда).

def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _num(value) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        got = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return got if math.isfinite(got) else 0.0


def _text(value, limit: int = 200) -> str:
    return clean(str(value if value is not None else ""))[:limit]


def evaluate(probe: dict, info: dict, checks: list[str], version: str, before: dict) -> list[dict]:
    """Проверки после подъёма. Каждая — по имени из закрытого списка протокола.

    ⚠ Живой прогон 27.09: сломанный выпуск (раннер падал на старте) прошёл «агент жив».
    Квитанцию раннера и записку надзора пишут в `data/` — а её переживает замена
    контейнера, и свежими их оставил ПРЕЖНИЙ контейнер за секунды до остановки. Поэтому
    обе засчитываются, только если написаны после старта нового контейнера.
    """
    probe = _dict(probe)
    state = _dict(_dict(probe.get("state")).get("body"))
    health = _dict(probe.get("health"))
    born = iso_epoch(info.get("started_at", "")) - 1.0     # старт контейнера, запас на округление
    mounts = info.get("mounts") or {}
    out = []
    for name in checks:
        ok, note = False, ""
        if name == "running":
            ok = info.get("running", False) and not info.get("restarting", False)
            note = f"{info.get('status') or '?'}, перезапусков {info.get('restart_count', 0)}"
        elif name == "channel":
            ok = health.get("code") == 200
            note = "отвечает" if ok else _text(health.get("why") or f"код {_text(health.get('code'), 20)}")
        elif name == "version":
            got = _text(_dict(state.get("desk")).get("version") or "", 40)
            ok = bool(version) and got == version
            note = f"канал говорит {got or '—'}, ждём {version or '—'}"
        elif name == "supervisor":
            sup = _dict(probe.get("supervisor"))
            beat = _num(sup.get("beat_epoch"))
            age = _num(probe.get("now")) - beat
            ours = born > 0 and iso_epoch(sup.get("started_utc", "")) >= born
            ok = sup.get("kind") == "serverboot" and beat > 0 and 0 <= age <= protocol.BEAT_STALE and ours
            note = ((f"{_text(sup.get('kind') or 'записки нет', 40)}, бился {age:.0f} с назад"
                     + ("" if ours else " — но это записка прежнего контейнера"))
                    if beat else _text(sup.get("why") or "записки надзора нет"))
        elif name == "runner":
            runner = _dict(state.get("runner"))
            at = _num(_dict(probe.get("reader")).get("at"))
            ours = born > 0 and at >= born
            ok = runner.get("alive") is True and ours
            note = ("жив" if ok else
                    "квитанция раннера — от прежнего контейнера, новый ещё не отметился"
                    if runner.get("alive") is True else
                    f"не отвечает (возраст квитанции {_text(runner.get('age_s'), 20)})")
        elif name == "config":
            now = carried(info)
            keys = sorted(k for k in set(before) | set(now) if not k.startswith("__"))
            lost = [k for k in keys if before.get(k) != now.get(k)]
            same_data = mounts.get(IN_CONTAINER_DATA) == before.get("__data")
            # Тот же путь к моделям — ещё не те же модели: папка в установке (по умолчанию
            # server/models) могла уехать с подменой, и голос онемел бы молча.
            models_gone = bool(before.get("__models_seen")) and info.get("models_seen") == 0
            ok = not lost and same_data and not models_gone
            note = ("перенесено: " + (", ".join(k for k in keys) or "переменных не было") + "; data/ та же"
                    if ok else "разошлось: " + ", ".join(lost + ([] if same_data else ["data/"])
                                                         + (["папка моделей пуста"] if models_gone else [])))
        elif name == "code":
            wrong = [d for d in CODE_DIRS
                     if not before.get(f"__{d}") or mounts.get(IN_CONTAINER_CODE[d]) != before.get(f"__{d}")]
            ok = not wrong
            note = ("tree/ и app/ — с диска сервера" if ok else
                    "не с диска сервера: " + ", ".join(f"{d}/" for d in wrong)
                    + " — правки агента пропадали бы при каждой пересборке")
        elif name == "brain":
            brain = _dict(state.get("brain"))
            ok = brain.get("configured") is True
            note = _text(brain.get("model") or "", 80) if ok else "мозг не настроен"
        out.append({"name": name, "title": protocol.UPDATE_CHECKS.get(name, name),
                    "ok": bool(ok), "note": note})
    return out


#: Столько падений раннера подряд (по записке надзора) — и ждать дальше незачем.
FALLS_DOOMED = 3


def doomed(probe: dict, info: dict) -> str:
    """Новая версия уже не поднимется — по словам самого надзора. -> причина или "".

    Раннер, который падает на старте, serverboot поднимает с растущей паузой, а код 2/3
    («нет дерева», «кривой конфиг») помечает остановленным. Ждать все минуты проверок
    в этих случаях — значит держать агента лежачим зря: откат нужен сейчас.

    Записка надзора — только ЭТОГО контейнера: при откате в `data/` ещё лежит записка
    сломанной версии с её падениями, и по ней откат счёлся бы несостоявшимся.
    """
    sup = _dict(_dict(probe).get("supervisor"))
    if iso_epoch(sup.get("started_utc", "")) < iso_epoch(info.get("started_at", "")) - 1.0:
        return ""
    children = sup.get("children")
    for child in children if isinstance(children, list) else []:
        if not isinstance(child, dict) or child.get("id") != "runner":
            continue
        if child.get("halted"):
            return f"надзор остановил раннер: {_text(child['halted'])}"
        falls = _num(child.get("falls"))
        if falls >= FALLS_DOOMED:
            return f"раннер падает раз за разом (падений подряд: {falls:.0f})"
    return ""


#: Что сломалось — словами для владельца, по имени проверки.
PLAIN_CHECKS = {
    "running": "новая версия падает",
    "channel": "окно не может достучаться до агента",
    "version": "запустилась не та версия",
    "supervisor": "не запустилась служба внутри",
    "runner": "агент не отвечает",
    "config": "не перенеслись адрес, модели или настройки",
    "code": "код агента не подключился",
    "brain": "мозг агента не настроен",
}


def plain_failures(results: list[dict]) -> str:
    """Проваленные проверки -> словами для человека («агент не отвечает, …»)."""
    bad = [PLAIN_CHECKS.get(r.get("name"), r.get("title") or r.get("name")) for r in results
           if isinstance(r, dict) and not r.get("ok")]
    return ", ".join(dict.fromkeys(str(b) for b in bad))


def plain_checks(results: list[dict]) -> str:
    bad = plain_failures(results)
    return "новая версия не заработала" + (f": {bad}" if bad else "")


def layer_paths(diff: str) -> list[str]:
    """Вывод `docker diff` -> правки агента в его коде: пути вида `tree/…`, `app/…`.

    Считает их демон — агенту этот список не подвластен. Строка «C <папка>» значит лишь,
    что внутри что-то поменялось, — папка с изменёнными детьми правкой не считается;
    кэши и байткод — тоже.
    """
    rows: list[tuple[str, str, bool]] = []
    for line in str(diff or "").splitlines():
        kind, _, path = line.strip().partition(" ")
        if kind not in ("A", "C", "D"):
            continue
        for name in CODE_DIRS:
            head = IN_CONTAINER_CODE[name] + "/"
            if not path.startswith(head):
                continue
            rel = path[len(head):].strip("/")
            parts = rel.split("/")
            if rel:
                ignored = (any(p in CODE_IGNORE_DIRS for p in parts)
                           or parts[-1] in CODE_IGNORE_NAMES or rel.endswith(CODE_IGNORE_SUFFIXES))
                rows.append((kind, f"{name}/{rel}", ignored))
            break
    # Папка с детьми — не правка, даже если дети — одни кэши (`tree/core` из-за `__pycache__`).
    every = {path for _kind, path, _ignored in rows}
    return sorted({path for kind, path, ignored in rows
                   if not ignored
                   and not (kind in ("A", "C") and any(other.startswith(path + "/") for other in every))})


def check_zip(path: Path, root_name: str = "Helene") -> int:
    """Архив выпуска: один корень, никаких путей наружу и ссылок. -> размер нужного серверу."""
    total = 0
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = info.filename
            if name.startswith(("/", "\\")) or ".." in Path(name).parts or ":" in name:
                raise UpdateError(f"в архиве путь наружу: {name[:120]}")
            if Path(name).parts[:1] != (root_name,):
                raise UpdateError(f"в архиве чужой корень: {name[:120]} (ждём {root_name}/)")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise UpdateError(f"в архиве ссылка: {name[:120]}")
            if not server_skips(name):
                total += info.file_size
    return total


def server_skips(name: str) -> bool:
    """Член архива, который серверу не нужен (Windows-Python)."""
    parts = Path(name).parts
    return len(parts) >= 2 and parts[1] in SERVER_SKIP


def check_layout(root: Path, version: str) -> dict:
    """Распакованная поставка: всё на месте и паспорт говорит ту версию, что просили."""
    missing = [rel for rel in REQUIRED if not (root / rel).exists()]
    if missing:
        raise UpdateError(f"в поставке нет: {', '.join(missing)}")
    try:
        passport = json.loads((root / "helene-build.json").read_text("utf-8"))
        desk = json.loads((root / "app" / "desk.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError(f"паспорт поставки не читается: {exc}") from exc
    if passport.get("product") != PRODUCT:
        raise UpdateError(f"поставка не {PRODUCT}: {passport.get('product')!r}")
    if str(passport.get("version")) != version or str(desk.get("version")) != version:
        raise UpdateError(f"паспорт говорит {passport.get('version')} (канал {desk.get('version')}), "
                          f"а ставим {version}")
    if passport.get("complete") is False:
        raise UpdateError("поставка собрана неполной: "
                          + ", ".join(passport.get("partial_reason") or []))
    return {"version": version, "built_utc": str(passport.get("built_utc") or ""),
            "git": (passport.get("git") or {}).get("desk", ""),
            "relay_linux": (root / "helene-relay").is_file()}


def dir_size(root: Path) -> int:
    """Сколько места займёт копия папки: обычные файлы по их размеру, ссылки не в счёт."""
    return sum(st.st_size for _rel, st, _dfd, _name in members(root, ignore=False)
               if stat.S_ISREG(st.st_mode))


def extract(zf: zipfile.ZipFile, infos, dest: Path) -> None:
    """Распаковать члены архива и вернуть файлам бит исполнения из архива — как `unzip`.

    `zipfile` режимов не ставит: без этого чистый исходник версии расходился бы с
    установкой, распакованной владельцем руками, на каждом исполняемом файле, и такие
    файлы читались бы «правками агента».
    """
    for info in infos:
        path = Path(zf.extract(info, dest))
        mode = info.external_attr >> 16
        if not info.is_dir() and executable(mode) and not stat.S_ISLNK(mode):
            path.chmod(0o755)


def _only_plain(base: str, names: list[str]) -> list[str]:
    """Для copytree: сокеты, FIFO и устройства в копию не едут (копия зависла бы на FIFO)."""
    skip = []
    for name in names:
        try:
            mode = os.lstat(os.path.join(base, name)).st_mode
        except OSError:
            skip.append(name)
            continue
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
            skip.append(name)
    return skip


#: Проба изнутри контейнера агента: канал, его состояние и записка надзора. Идёт через
#: `docker exec`, поэтому ни ключ окна, ни сеть хоста исполнителю не нужны.
PROBE = r'''
import json, pathlib, time, urllib.request
root = pathlib.Path("/opt/helene")
out = {"now": time.time()}
try:
    cfg = json.loads((root / "helene.json").read_text("utf-8-sig"))
except Exception as exc:
    cfg = {}
    out["cfg_error"] = str(exc)[:200]
port = int(cfg.get("port") or 8094)
tree = pathlib.Path(str(cfg.get("tree") or "data"))
tree = tree if tree.is_absolute() else root / tree
try:
    try:
        key = (tree / ".serverboot" / "desk-token").read_text("utf-8").strip()
    except OSError:
        key = (tree / "memory" / ".state" / "desk-token").read_text("utf-8").strip()
except Exception as exc:
    key = ""
    out["key_error"] = str(exc)[:200]
for name in ("health", "state"):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/{name}?key={key}", timeout=10) as answer:
            body = json.loads(answer.read().decode("utf-8", "replace"))
            if name == "state":
                body = {k: body.get(k) for k in ("desk", "runner", "brain", "level", "phrase")}
            out[name] = {"code": answer.status, "body": body}
    except Exception as exc:
        out[name] = {"code": getattr(exc, "code", 0) or 0, "why": f"{type(exc).__name__}: {exc}"[:300]}
try:
    out["supervisor"] = json.loads((tree / "memory" / ".state" / "supervisor.json").read_text("utf-8"))
except Exception as exc:
    out["supervisor"] = {"why": f"{type(exc).__name__}: {exc}"[:200]}
try:
    reader = json.loads((tree / "memory" / ".control" / "desk_inbox" / ".reader.json").read_text("utf-8"))
    out["reader"] = {"at": reader.get("at"), "pid": reader.get("pid"), "busy": reader.get("busy")}
except Exception as exc:
    out["reader"] = {"why": f"{type(exc).__name__}: {exc}"[:200]}
print(json.dumps(out, ensure_ascii=False))
'''


# --------------------------------------------------------------------------- #
#  Исполнитель
# --------------------------------------------------------------------------- #

class Config:
    def __init__(self, install: Path, container: str = "helene",
                 releases: str = DEFAULT_RELEASES, asset: str = "Helene",
                 keep: int = 2, insecure: bool = False):
        self.install = Path(install)
        self.container = container
        self.releases = releases.rstrip("/")
        self.asset = asset
        self.keep = max(1, keep)
        self.insecure = insecure

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ
        try:
            keep = int(env.get("HELENE_UPDATE_KEEP") or 2)
        except ValueError:
            keep = 2
        return cls(Path(env.get("HELENE_DIR") or IN_CONTAINER_ROOT),
                   container=(env.get("HELENE_CONTAINER") or "helene").strip(),
                   releases=(env.get("HELENE_UPDATE_RELEASES") or DEFAULT_RELEASES).strip(),
                   asset=(env.get("HELENE_UPDATE_ASSET") or "Helene").strip(),
                   keep=keep, insecure=(env.get("HELENE_UPDATE_INSECURE") or "") == "1")


#: Как «да» пришло вместе с планом — словами для расписки и итога (слово владельца агенту
#: едет в плане дословно, `consent_words`).
CONSENT_WORDS = {"window": "нажато «Обновить» в окне",
                 "host": "обновление запущено командой на сервере",
                 "owner-words": "владелец попросил агента обновиться"}

#: Прерванное обновление довожу не чаще раза в минуту: сбой, который повторяется (демон
#: докера перезапускается, диск полон), не должен крутить откат каждые три секунды.
RECOVER_EVERY = 60.0
#: Столько попыток откат делает сам; дальше — итог «нужен человек».
ROLLBACK_TRIES = 3


class Updater:
    def __init__(self, cfg: Config, docker: Docker | None = None, net: Net | None = None,
                 clock=time.time, sleep=time.sleep):
        self.cfg = cfg
        self.install = cfg.install
        self.data = cfg.install / "data"
        self.home = cfg.install / ".updater"
        self.pristine_root = self.home / "pristine"
        self.shared = Shared(self.data)
        self.docker = docker or Docker()
        self.net = net or Net(cfg.insecure)
        self.clock, self.sleep = clock, sleep
        self.lock = threading.RLock()
        self.health: dict = {"ok": False, "why": "ещё не проверял себя", "info": {}}
        self.latest: dict = {}
        self.latest_at = 0.0
        self.busy = ""
        self.started_utc = utc()
        self.reexec = False
        self.recover_at = 0.0
        self.recover_said = ""
        self.save_error = ""
        self._last_probe: dict = {}
        self.state: dict = self._load()

    # --- своё состояние (не в дереве агента) --------------------------------

    def _state_path(self) -> Path:
        return self.home / "state.json"

    def _load(self) -> dict:
        try:
            data = json.loads(self._state_path().read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self) -> None:
        """Состояние — на диск, расписка — агенту и окну.

        Не бросает: на полном диске обновление, идущее прямо сейчас (или его откат), важнее
        записи о нём. Состояние тогда живёт в памяти, пока я жив, а почему — в журнале и в
        записке о себе.
        """
        with self.lock:
            self.state["updated_utc"] = utc(self.clock())
            tmp = self.home / f".state-{secrets.token_hex(4)}.json"
            try:
                self.home.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), "utf-8")
                os.replace(tmp, self._state_path())
                self.save_error = ""
            except OSError as exc:
                if not self.save_error:
                    log(f"состояние не записалось: {exc}")
                self.save_error = f"состояние исполнителя не записывается: {exc}"
                try:
                    tmp.unlink()
                except OSError:
                    pass
            try:
                self.shared.write(*CTL, protocol.UPDATE_RECEIPT, self.receipt())
            except OSError as exc:
                # ⚠ 1.4.0: расписка — то, по чему окно и агент судят «идёт обновление».
                # Прежде провал писался в журнал и МОЛЧА: у окна оставалась СТАРАЯ
                # расписка о живом обновлении. Теперь — попытка №2: та же расписка
                # файлом `.failed-<UTC>` рядом (в папке исполнителя), чтобы следующий
                # save(), которому повезёт, доложил нормальную, а человек мог найти
                # след. Полный диск не должен прятать идущее обновление.
                log(f"расписка в дерево агента не записалась: {exc}")
                self._save_receipt_failed(exc)

    def _save_receipt_failed(self, exc: Exception) -> None:
        """Расписка, не лёгшая в дерево агента, — второй попыткой рядом, потом дома.

        Провал мог быть привязан к ИМЕНИ (подменённый путь), а не к диску — поэтому
        первая попытка пишет ту же расписку свежим именем
        `update-plan.receipt.failed-<UTC>.json` в ту же папку дерева. Не вышло и
        там (диск, права) — копия ложится в дом исполнителя (`.updater/`), чтобы
        человек мог найти след. Полный провал — только журнал: молчать нельзя,
        ронять себя — тоже.
        """
        row = {"at_utc": utc(self.clock()), "error": f"{type(exc).__name__}: {exc}"[:300],
               "receipt": self.receipt()}
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.clock()))
        near = f"{Path(protocol.UPDATE_RECEIPT).stem}.failed-{stamp}.json"
        try:
            self.shared.write(*CTL, near, row)
            log(f"расписка положена резервным файлом рядом: {near}")
            return
        except OSError:
            pass
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            (self.home / f"receipt.failed-{stamp}.json").write_text(
                json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
            log("расписка не в дереве — резервная копия легла в папку исполнителя")
        except OSError as inner:
            log(f"и резервная запись провалившейся расписки не легла: {inner}")

    def receipt(self) -> dict:
        """Что видят окно и агент. Внутренние пути отката — только в моей папке."""
        keys = ("id", "state", "note", "summary", "step", "steps", "plan", "from_version",
                "to_version", "release", "backup", "extensions", "checks", "rollback_checks",
                "carried", "confirmed", "created_utc", "updated_utc", "awaiting_until_utc",
                "awaiting_since_epoch", "finished_utc", "finished_epoch", "rollback", "phase",
                "code_preview", "agent_code", "trial", "retired_trial", "data_restored", "owner_files")
        out = {"schema": protocol.UPDATE_SCHEMA}
        out.update({k: self.state[k] for k in keys if k in self.state})
        if self.state.get("state") == "awaiting":
            out["nonce"] = self.state.get("nonce", "")
        return out

    def step(self, text: str, ok: bool = True, note: str = "") -> None:
        with self.lock:
            steps = self.state.setdefault("steps", [])
            steps.append({"at": utc(self.clock()), "step": text, "ok": ok, "note": note[:600]})
            del steps[:-60]
            self.state["step"] = text
            self.busy = text
        log(f"{'·' if ok else '✗'} {text}{' — ' + note if note else ''}")
        self.save()

    def finish(self, state: str, note: str, summary: str = "") -> None:
        """Итог. `note` — подробно (агенту, в журнал, под «Подробнее»); `summary` — одной
        фразой простыми словами: её первой видит владелец, который не айтишник."""
        now = self.clock()
        with self.lock:
            # «Шаг» у итога — пустой: у отклонённого плана там осталось бы «жду «да»».
            self.state.update(state=state, note=note, step="", finished_utc=utc(now),
                              finished_epoch=now, summary=summary or note)
            self.state.pop("nonce", None)
            self.busy = ""
            # «Последняя версия» в записке — сразу заново: после обновления она иначе
            # до шести часов звала бы на ту, что уже стоит.
            self.latest_at = 0.0
        log(f"итог: {state} — {note}")
        self.save()
        row = {k: self.state.get(k) for k in ("id", "state", "summary", "note", "from_version",
                                              "to_version", "finished_utc")}
        row["asked_by"] = (self.state.get("plan") or {}).get("asked_by", "")
        row["confirmed_by"] = (self.state.get("confirmed") or {}).get("by", "")
        try:
            self.shared.append(*CTL, protocol.UPDATE_HISTORY, row)
        except OSError:
            pass
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            with open(self.home / "history.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # --- о себе -------------------------------------------------------------

    def current_version(self) -> str:
        """Версия установки — по паспорту в её корне.

        Только паспорт: `app/desk.json` с 27.09 на диске агента, и версию оттуда агент
        мог бы назвать любую — «новее» стало бы тем, что скажет он.
        """
        try:
            got = json.loads((self.install / "helene-build.json").read_text("utf-8")).get("version")
        except (OSError, ValueError, AttributeError):
            return ""
        return str(got) if protocol.version_tuple(got) else ""

    def inspect(self) -> dict:
        code, out, err = self.docker.run(["inspect", self.cfg.container], timeout=60)
        if code != 0:
            raise UpdateError(f"контейнер {self.cfg.container} не найден: {(err or out).strip()[:200]}")
        try:
            rows = json.loads(out)
        except ValueError as exc:
            raise UpdateError(f"docker inspect ответил не JSON: {exc}") from exc
        if not rows or not isinstance(rows[0], dict):
            raise UpdateError(f"контейнер {self.cfg.container} не найден")
        return describe(rows[0])

    def check_self(self) -> dict:
        """Могу ли я работать: вижу ли установку тем же путём, что докер хоста.

        Compose разрешает `../data` в пути ХОСТА, и если я смотрю на установку не тем
        путём, каким её видит хост, поднятый мной контейнер получил бы пустую `data/` —
        агент родился бы заново. Поэтому путь сверяется с меткой самого контейнера.
        """
        problems: list[str] = []
        info: dict = {}
        if not (self.install / "server" / "docker-compose.yml").is_file():
            problems.append(f"не вижу установку в {self.install} (нет server/docker-compose.yml)")
        if not self.data.is_dir():
            problems.append(f"не вижу папку агента {self.data}")
        try:
            info = self.inspect()
        except UpdateError as exc:
            problems.append(str(exc))
        if info:
            expected = str(self.install / "server")
            if not info["project"] or not info["service"]:
                problems.append(f"контейнер {self.cfg.container} поднят не через compose — "
                                "обновлять его compose-проект я не возьмусь")
            elif os.path.normpath(info["working_dir"]) != expected:
                problems.append(f"контейнер собран из {info['working_dir'] or '?'}, а я смотрю в "
                                f"{expected}: подними меня с HELENE_DIR=<папка установки на хосте>")
            source = info["mounts"].get(IN_CONTAINER_DATA)
            if source and os.path.normpath(source) != str(self.data):
                problems.append(f"данные агента на хосте — {source}, а я вижу {self.data}")
        self.health = {"ok": not problems, "why": "; ".join(problems), "info": info}
        return self.health

    def compose_files(self, info: dict) -> list[Path]:
        """Файлы compose, с которыми поднят контейнер агента: основной — из установки, и
        свои файлы владельца (override с его портами, томами, сетями), если лежат в `server/`.

        Поднять новую версию без них — молча потерять его настройки; файл вне установки
        мне не виден — тогда отказ словами, а не подъём «примерно как было».
        """
        main = self.install / "server" / "docker-compose.yml"
        listed = [Path(os.path.normpath(raw)) for raw in info.get("config_files") or []]
        if listed and main not in listed:
            raise UpdateError(f"агент поднят не из server/docker-compose.yml, а из "
                              f"{', '.join(map(str, listed))} — такой контейнер я не обновляю")
        extra = []
        for path in listed:
            if path == main:
                continue
            if self.install / "server" not in path.parents:
                raise UpdateError(f"агент поднят ещё и с {path} — вне server/ установки я этот файл не "
                                  "вижу; положи его в server/ и подними агента с ним оттуда")
            if not path.is_file():
                raise UpdateError(f"агент поднят ещё и с {path}, а файла нет")
            extra.append(path)
        return [main, *extra]

    def beat(self) -> None:
        row = {"schema": SCHEMA, "pid": os.getpid(), "started_utc": self.started_utc,
               "beat_utc": utc(self.clock()), "beat_epoch": self.clock(),
               "ok": bool(self.health.get("ok")),
               "why": self.health.get("why", "") or self.save_error,
               "busy": self.busy, "install": str(self.install), "container": self.cfg.container,
               "current_version": self.current_version(), "latest": self.latest,
               "state": self.state.get("state", ""), "plan_id": self.state.get("id", "")}
        self.shared.write(*CTL, protocol.UPDATER_BEAT, row)

    def refresh_latest(self, force: bool = False) -> None:
        if not force and self.clock() - self.latest_at < LATEST_EVERY:
            return
        self.latest_at = self.clock()
        try:
            rel = pick_release(self.net.json(self.cfg.releases + "/latest"), self.cfg.asset)
            self.latest = {"version": rel["version"], "checked_utc": utc(self.clock()), "why": ""}
        except UpdateError as exc:
            self.latest = {"version": self.latest.get("version", ""),
                           "checked_utc": utc(self.clock()), "why": str(exc)[:200]}

    # --- выпуск ---------------------------------------------------------------

    def release_for(self, version: str) -> dict:
        tail = "/latest" if version == "latest" else f"/tags/v{version}"
        rel = pick_release(self.net.json(self.cfg.releases + tail), self.cfg.asset)
        if not rel["sha256"] and rel["sha_url"]:
            rel["sha256"] = sha_in_text(self.net.text(rel["sha_url"]), rel["asset"])
        if not rel["sha256"]:
            rel["sha256"] = rel["sha_from_notes"]
        if not rel["sha256"]:
            raise UpdateError(f"у выпуска {rel['tag']} нет суммы sha256 архива — ставить "
                              "непроверенный архив я не буду")
        if version != "latest" and rel["version"] != version:
            raise UpdateError(f"просили {version}, а выпуск называет себя {rel['version']}")
        return rel

    # --- чистые исходники версий: база для правок агента --------------------

    def pristine(self, version: str) -> Path | None:
        """Чистый `tree/` + `app/` версии — если он у меня есть."""
        if not version:
            return None
        root = self.pristine_root / version
        return root if all((root / name).is_dir() for name in CODE_DIRS) else None

    def shipped(self, version: str) -> set[str] | None:
        """Список файлов выпуска версии (лежит рядом с её чистым исходником), если он есть."""
        try:
            rows = json.loads((self.pristine_root / version / "manifest.json").read_text("utf-8"))
        except (OSError, ValueError):
            return None
        return {str(row) for row in rows} if version and isinstance(rows, list) else None

    def ensure_pristine(self, version: str) -> tuple[Path | None, str]:
        """Чистый исходник версии: из кэша или скачав её выпуск. -> (папка, почему нет)."""
        if not version:
            return None, "версия установки неизвестна (нет паспорта helene-build.json)"
        have = self.pristine(version)
        if have is not None:
            return have, ""
        try:
            rel = self.release_for(version)
        except UpdateError as exc:
            return None, f"исходника {version} нет в выпусках ({exc})"
        self.step(f"скачиваю прежнюю версию {version} — сравнить с ней код агента и найти его правки")
        downloads = self.home / "downloads"
        tmp = self.pristine_root / f".tmp-{version}-{secrets.token_hex(4)}"
        archive = downloads / f"base-{rel['asset']}"
        try:
            downloads.mkdir(parents=True, exist_ok=True)
            got = self.net.download(rel["url"], archive, MAX_ARCHIVE)
            if got != rel["sha256"]:
                return None, f"сумма исходника {version} не сошлась"
            check_zip(archive)
            with zipfile.ZipFile(archive) as zf:
                extract(zf, [info for info in zf.infolist()
                             if len(Path(info.filename).parts) >= 2
                             and Path(info.filename).parts[1] in CODE_DIRS], tmp)
                shipped = sorted("/".join(Path(info.filename).parts[1:]) for info in zf.infolist()
                                 if not info.is_dir() and len(Path(info.filename).parts) >= 2)
            (tmp / "Helene" / "manifest.json").write_text(json.dumps(shipped), "utf-8")
            dest = self.pristine_root / version
            if dest.exists():
                shutil.rmtree(dest)
            os.rename(tmp / "Helene", dest)
        except (UpdateError, OSError, zipfile.BadZipFile) as exc:
            return None, f"исходник {version} не достался: {said(exc)}"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                archive.unlink()
            except OSError:
                pass
        return self.pristine(version), ""

    def save_pristine(self, root: Path, version: str) -> None:
        """Запомнить чистый исходник ставящейся версии — базу следующего обновления."""
        if self.pristine(version) is not None:
            return
        tmp = self.pristine_root / f".tmp-{version}-{secrets.token_hex(4)}"
        try:
            for name in CODE_DIRS:
                shutil.copytree(root / name, tmp / name, symlinks=True)
            shipped = sorted(rel for rel, st, _dfd, _name in members(root, ignore=False)
                             if not stat.S_ISDIR(st.st_mode))
            (tmp / "manifest.json").write_text(json.dumps(shipped), "utf-8")
            dest = self.pristine_root / version
            if dest.exists():
                shutil.rmtree(dest)
            os.rename(tmp, dest)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def layer_edits(self) -> list[str] | None:
        """Правки агента в коде, живущем внутри контейнера (установка до 27.09): по
        `docker diff`. -> пути `tree/…`, `app/…` (None — узнать не вышло)."""
        code, out, _err = self.docker.run(["diff", self.cfg.container], timeout=300)
        return layer_paths(out) if code == 0 else None

    def code_preview(self) -> dict:
        """До «да»: правил ли агент свой код и переедут ли его правки. Для карточки."""
        info = self.health.get("info") or {}
        if not code_mounted(info):
            # Установка до 27.09: код агента жил в самом контейнере. Его правки видны по
            # `docker diff` (список считает демон, агенту он не подвластен) — и переедут:
            # перед подменой я сниму их из контейнера (`_capture_layer`).
            edited = self.layer_edits()
            if edited is None:
                return {"mounted": False, "layer": True, "edited": 0, "files": [],
                        "note": "код агента живёт внутри контейнера; при обновлении я сниму его "
                                "оттуда и перенесу правки агента на новую версию"}
            return {"mounted": False, "layer": True, "edited": len(edited), "files": edited[:12],
                    "note": (f"агент правил свой код (он жил внутри контейнера): {len(edited)} "
                             "файл(ов) — сниму их оттуда и перенесу на новую версию, что не ляжет — "
                             "отдам ему" if edited else "своих правок в коде у агента нет")}
        base, why = self.ensure_pristine(self.current_version())
        if base is None:
            return {"mounted": True, "base": False,
                    "note": f"сравнить не с чем: {why}. Правки агента перенести не смогу — его "
                            "прежний код целиком положу ему в workspace"}
        edited = [f"{name}/{rel}" for name in CODE_DIRS
                  for rel in code_edits(base / name, self.install / name)]
        return {"mounted": True, "base": True, "edited": len(edited), "files": edited[:12],
                "note": (f"агент правил свой код: {len(edited)} файл(ов) — перенесу правки на новую "
                         "версию, что не ляжет — отдам ему" if edited else
                         "своих правок в коде у агента нет")}

    # --- ход --------------------------------------------------------------------

    def tick(self) -> None:
        """Один шаг цикла: прерванное — довести, истечение, «да»/«нет», испытание, новый план."""
        st = self.state
        current = st.get("state")
        if current in ("checking", "confirmed", "running") or (
                current == "trial" and st.get("phase") not in (None, "trial")):
            # Сюда попадаю только после сбоя: сверка и исполнение идут синхронно внутри
            # тика. Довожу прерванное — повторяя, пока не выйдет, но не чаще раза в минуту.
            self.recover_due()
            return
        if current == "trial":
            self.trial_tick()
            return
        plan = self.shared.read(*CTL, protocol.UPDATE_PLAN)
        fresh = bool(plan.get("id")) and plan.get("id") != st.get("id")
        if current == "awaiting":
            if fresh:
                self.finish("superseded", "план заменён новым — этот не исполнялся",
                            "Эта просьба заменена новой.")
            elif self.clock() > float(st.get("awaiting_until_epoch") or 0):
                self.finish("expired", f"«да» не пришло за {protocol.UPDATE_AWAIT_HOURS} ч — "
                                       "план истёк, ничего не тронуто",
                            "Согласия не было сутки — обновление не начиналось, ничего не менялось.")
                return
            else:
                confirm = self.shared.read(*CTL, protocol.UPDATE_CONFIRM)
                if (confirm.get("id") == st.get("id") and st.get("nonce")
                        and confirm.get("nonce") == st.get("nonce")):
                    self.shared.remove(*CTL, protocol.UPDATE_CONFIRM)
                    who = {"by": str(confirm.get("by") or "")[:40],
                           "words": str(confirm.get("words") or "")[:500],
                           "at_utc": str(confirm.get("at_utc") or "")[:40]}
                    if confirm.get("decision") == "yes":
                        self.execute(who)
                    else:
                        self.state["confirmed"] = who
                        by_agent = who["by"] == "agent"
                        self.finish("declined", ("агент отозвал план" if by_agent
                                                 else "владелец ответил «нет»") + " — ничего не тронуто",
                                    "Агент передумал обновляться — ничего не менялось." if by_agent
                                    else "Обновление отменено — ничего не менялось.")
                return
        if fresh:
            self.consider(plan)

    def recover_due(self) -> None:
        """Довести прерванное — с паузой между попытками; неудача — словами в расписку."""
        now = self.clock()
        if now < self.recover_at:
            return
        self.recover_at = now + RECOVER_EVERY
        try:
            self.recover()
            self.recover_said = ""
        except Exception as exc:  # noqa: BLE001 — повторю через минуту, но не молча
            why = said(exc)[:600]
            log(f"довести прерванное обновление не вышло: {why} — повторю через {RECOVER_EVERY:.0f} с")
            if why != self.recover_said:
                self.recover_said = why
                self.step("довести прерванное не вышло — повторяю раз в минуту", ok=False, note=why)

    def consider(self, raw: dict) -> None:
        """Новый план: сверить с выпусками и местом, показать человеку и ждать «да».

        Любой сбой сверки — отказ словами: застрявшая «сверяю» держала бы окно в вечном
        процессе, а новый план не ложился бы вовсе («идёт другое обновление»).
        """
        try:
            self._consider(raw)
        except Exception as exc:  # noqa: BLE001 — причина обязана доехать до расписки
            self.finish("refused", f"сверка плана упала: {said(exc)}",
                        "Проверка новой версии сорвалась — ничего не менялось. Можно попробовать "
                        "ещё раз; подробности ниже.")

    def _consider(self, raw: dict) -> None:
        now = self.clock()
        plan, why = protocol.validate_plan(raw)
        self.state = {"id": str(raw.get("id") or "")[:40], "plan": plan, "steps": [],
                      "created_utc": utc(now), "state": "checking"}
        if plan is None:
            self.finish("refused", f"план не принят: {why}", f"Просьба об обновлении не подошла: {why}.")
            return
        self.check_self()
        if not self.health["ok"]:
            self.finish("refused", f"исполнитель не может работать: {self.health['why']}",
                        "Исполнитель обновлений настроен неверно — обновить не могу. Помогает одна "
                        "команда на сервере, из папки установки: sh server/install.sh")
            return
        try:
            self.compose_files(self.health["info"])
        except UpdateError as exc:
            self.finish("refused", str(exc),
                        "Агент запущен на сервере необычным способом, и обновить его сам я не могу. "
                        "Подробности ниже — их стоит показать тому, кто ставил Hélène.")
            return
        current = self.current_version()
        if not current:
            # Без паспорта «только новее» не проверить: пропусти я эту проверку — план
            # поставил бы любую версию из выпусков, в том числе старую.
            self.finish("refused", "не знаю, какая версия стоит: паспорта helene-build.json в корне "
                                   "установки нет или он не читается. Без него «только новее» не "
                                   "проверить — обнови командой на сервере (README-СЕРВЕР)",
                        "Не могу понять, какая версия стоит сейчас, — обновить отсюда не выйдет. "
                        "Помогает одна команда на сервере: sh Helene/server/install.sh из свежей поставки.")
            return
        self.step("проверяю, есть ли такая версия")
        try:
            rel = self.release_for(plan["version"])
        except UpdateError as exc:
            self.finish("refused", str(exc),
                        "Не нашёл новую версию (или нет связи с GitHub) — ничего не менялось. "
                        "Подробности ниже.")
            return
        target = rel["version"]
        if protocol.version_tuple(target) <= protocol.version_tuple(current):
            self.finish("refused", f"стоит {current}, а {target} не новее — ставить нечего",
                        f"Уже стоит {current} — обновлять нечего.")
            return
        data_bytes = dir_size(self.data) if plan["backup"] == "full" else 0
        free = shutil.disk_usage(self.install).free
        need = max(rel["size"], 1) * 4 + data_bytes + SPACE_MARGIN
        backup = {"mode": plan["backup"], "words": protocol.UPDATE_BACKUPS[plan["backup"]],
                  "data_bytes": data_bytes, "free_bytes": free, "need_bytes": need}
        self.state.update(from_version=current, to_version=target, release=rel, backup=backup)
        if free < need:
            hint = (" План с копией только кода (backup: code) займёт меньше."
                    if plan["backup"] == "full" else "")
            self.finish("refused", f"мало места: нужно около {mb(need)}, свободно {mb(free)}.{hint}",
                        f"На диске сервера мало места: нужно около {mb(need)}, свободно {mb(free)}."
                        + (" Можно обновиться без копии памяти агента — кнопка ниже."
                           if plan["backup"] == "full" else ""))
            return
        preview = self.code_preview()
        self.state["code_preview"] = preview
        details = (f"{current} → {target}; архив {mb(rel['size'])}, sha256 {rel['sha256'][:12]}…; "
                   f"копия — {backup['words']}" + (f" ({mb(data_bytes)})" if data_bytes else "")
                   + f"; {preview['note']}")
        consent = plan.get("consent") or ""
        if consent:
            # «Да» пришло вместе с планом: кнопка «Обновить» в окне или команда на сервере.
            self.state.update(note=f"начинаю: {details}")
            self.step("выпуск на месте — начинаю")
            self.execute({"by": consent,
                          "words": plan.get("consent_words") or CONSENT_WORDS.get(consent, ""),
                          "at_utc": str(plan.get("asked_utc") or utc(now))})
            return
        self.state.update(
            state="awaiting", nonce=secrets.token_hex(12), awaiting_since_epoch=now,
            awaiting_until_epoch=now + protocol.UPDATE_AWAIT_HOURS * 3600,
            awaiting_until_utc=utc(now + protocol.UPDATE_AWAIT_HOURS * 3600),
            note=f"жду подтверждения: {details}")
        self.step("жду согласия владельца")

    # --- исполнение -------------------------------------------------------------

    def compose(self, *args: str, file: Path | None = None, timeout: float = 600):
        """`docker compose` проекта агента.

        Проект и файлы — из моего состояния, записанные при «да»: контейнера агента в этот
        момент может и не быть (упал посреди пересоздания, владелец сделал `rm`), а
        поднимать его обратно всё равно надо.
        """
        st = self.state
        project = st.get("project") or (self.health.get("info") or {}).get("project")
        if not project:
            # Перезапуск без записанного проекта (состояние прежней версии): спрашиваю себя.
            project = (self.check_self().get("info") or {}).get("project")
        if not project:
            raise UpdateError(f"compose-проект контейнера {self.cfg.container} не виден: "
                              f"{self.health.get('why') or 'нет меток'}")
        files = [file] if file else [Path(p) for p in st.get("compose_files") or []] or [
            self.install / "server" / "docker-compose.yml"]
        cmd = ["compose", "-p", project]
        for path in files:
            cmd += ["-f", str(path)]
        return self.docker.run([*cmd, *args], env=st.get("carried") or {}, timeout=timeout)

    def _must(self, done: tuple[int, str, str], what: str) -> str:
        code, out, err = done
        if code != 0:
            tail = "\n".join((err or out).strip().splitlines()[-12:])
            raise UpdateError(f"{what}: код {code}\n{tail}"[:1500])
        return out

    def execute(self, who: dict) -> None:
        st = self.state
        plan = st["plan"]
        bdir = self.home / "backups" / stamp()
        while bdir.exists():                    # два обновления в одну секунду — не одна папка
            bdir = bdir.with_name(f"{bdir.name[:16]}-{secrets.token_hex(2)}")
        st.update(state="confirmed", confirmed=who, backup_dir=str(bdir), phase="prepare",
                  swapped=[], note=f"«да» получено ({who.get('by') or '?'}) — начинаю")
        st.pop("nonce", None)
        self.save()
        try:
            self.check_self()
            if not self.health["ok"]:
                raise UpdateError(f"исполнитель не может работать: {self.health['why']}")
            info = self.health["info"]
            files = self.compose_files(info)
            before = carried(info)
            before["__data"] = info["mounts"].get(IN_CONTAINER_DATA, "")
            for name in CODE_DIRS:
                before[f"__{name}"] = str(self.install / name)
            before["__models_seen"] = self.models_seen(info["mounts"].get("/models"))
            repo, tag = image_repo(info["image"])
            st.update(state="running", carried={k: v for k, v in before.items()
                                                if not k.startswith("__")},
                      carried_before=before, image_repo=repo, image_tag=tag,
                      old_image_id=info["image_id"], project=info["project"],
                      service=info["service"], compose_files=[str(p) for p in files],
                      code_mounted=code_mounted(info),
                      rollback_tag=f"helene-rollback-{bdir.name}")
            self.save()
            self._prepare(bdir)
        except Exception as exc:  # noqa: BLE001 — любой сбой: вернуть или откатить, не застрять
            left = self._undo_prepare()
            self.finish("failed", f"не начато: {said(exc)} — агент не останавливался, "
                                  + (f"но вернулось не всё: {left}" if left else "всё как было"),
                        "Обновление не началось — агент работал всё время, ничего не менялось."
                        + (" Подробности ниже." if not left else
                           " Но вернулось не всё — подробности ниже, их стоит показать тому, кто ставил Hélène."))
            return
        st["phase"] = "switch"
        self.save()
        try:
            self._switch(bdir)
            st["phase"] = "verify"
            self.save()
            results = self._verify(st["to_version"], plan["checks"], plan["wait_min"])
        except Exception as exc:  # noqa: BLE001 — любой сбой: вернуть или откатить, не застрять
            self.step("новая версия не встала на место", ok=False, note=said(exc))
            if st.get("stopped"):
                self._rollback(said(exc), plain="сбой при установке новой версии")
            else:
                self._abort_unstopped(said(exc))
            return
        self._settle(results)

    def _settle(self, results: list[dict]) -> None:
        """Проверки новой версии прошли — испытание (или успех без мозга); нет — откат."""
        if not all(r["ok"] for r in results):
            bad = "; ".join(f"{r['title']}: {r['note']}" for r in results if not r["ok"])
            self._rollback(f"проверки не прошли — {bad}", plain=plain_checks(results))
            return
        try:
            self._after_checks()
        except Exception as exc:  # noqa: BLE001 — зелёные проверки не должны кончаться «идёт» навсегда
            self._rollback(f"после проверок: {said(exc)}", plain="сбой после проверок")

    def _prepare(self, bdir: Path) -> None:
        """Всё, что можно сделать, не трогая живого агента."""
        st = self.state
        rel = st["release"]
        downloads = self.home / "downloads"
        stage = self.home / "stage" / bdir.name
        downloads.mkdir(parents=True, exist_ok=True)
        stage.mkdir(parents=True, exist_ok=True)
        st["stage_dir"] = str(stage)
        if st.get("code_mounted"):
            base, why = self.ensure_pristine(st.get("from_version") or "")
            st["base_why"] = why
            st["base_ready"] = base is not None
        archive = downloads / rel["asset"]
        st["archive"] = str(archive)
        self.step(f"скачиваю {st['to_version']} ({mb(rel['size'])})", note=rel["asset"])
        got = self.net.download(rel["url"], archive, MAX_ARCHIVE)
        if got != rel["sha256"]:
            raise UpdateError(f"сумма архива не сошлась: {got[:16]}… вместо {rel['sha256'][:16]}…")
        self.step("архив скачался целым", note=f"sha256 {got}")
        unpacked = check_zip(archive)
        free = shutil.disk_usage(self.install).free
        data_bytes = dir_size(self.data) if st["plan"]["backup"] == "full" else 0
        if free < unpacked + data_bytes + SPACE_MARGIN:
            raise UpdateError(f"мало места для распаковки и копии: нужно около "
                              f"{mb(unpacked + data_bytes + SPACE_MARGIN)}, свободно {mb(free)}")
        self.step("распаковываю", note=f"{mb(unpacked)}; runtime/ для Windows серверу не нужен")
        with zipfile.ZipFile(archive) as zf:
            extract(zf, [m for m in zf.infolist() if not server_skips(m.filename)], stage)
        self._staged(stage / "Helene")

    def _staged(self, root: Path) -> None:
        """Новая поставка распакована в `root`: сверить, отложить прежний образ, собрать новый,
        прорепетировать расширения — всё, пока агент работает."""
        st = self.state
        passport = check_layout(root, st["to_version"])
        relay = root / "helene-relay"
        if relay.is_file():
            relay.chmod(0o755)
        st["passport"] = passport
        self.step("новая версия на месте", note=f"паспорт {passport['version']}, собрана {passport['built_utc']}")
        code, out, err = self.docker.run(["compose", "-f", str(root / "server" / "docker-compose.yml"),
                                          "config", "--services"], timeout=120)
        services = out.split()
        if code != 0 or st["service"] not in services:
            raise UpdateError(f"в новом compose-файле нет сервиса {st['service']}: "
                              f"{(err or out).strip()[:300]}")
        self._must(self.docker.run(["tag", st["old_image_id"],
                                    f"{st['image_repo']}:{st['rollback_tag']}"], timeout=60),
                   "прежний образ не отложился")
        st["tagged"] = True
        self.step("прежнюю версию откладываю — на случай отката",
                  note=f"образ {st['image_repo']}:{st['rollback_tag']}")
        self.step("готовлю новую версию — это несколько минут", note="сборка образа")
        st["built"] = True
        self._must(self.compose("build", st["service"], file=root / "server" / "docker-compose.yml",
                                timeout=45 * 60), "образ не собрался")
        code, out, _err = self.docker.run(["image", "inspect", "-f", "{{.Id}}",
                                           f"{st['image_repo']}:{st['image_tag']}"], timeout=60)
        if code == 0 and out.strip().startswith("sha256:"):
            st["new_image_id"] = out.strip()
        self.step("новая версия готова", note=st.get("new_image_id", ""))
        self._rehearse_extensions()

    def _rehearse_extensions(self) -> None:
        """Расширения владельца — под новым образом, до подмены.

        Репетиция исполняет код расширений из `data/` агента, поэтому она заперта: без
        сети, без прав, только на чтение, с потолком памяти, процессов и процессора, и под
        своим именем — чтобы убрать контейнер наверняка: по таймауту `subprocess` убивает
        только клиента docker, а контейнер с кодом агента жил бы дальше вне всякого проекта.
        """
        st = self.state
        found = sorted(p.parent.name for p in (self.data / "extensions").glob("*/extension.json")
                       if not p.is_symlink())
        if not found:
            st["extensions"] = {"ok": True, "summary": "расширений нет"}
            return
        self.step(f"проверяю расширения владельца на новой версии: {', '.join(found)}")
        name = f"helene-rehearsal-{stamp().lower()}"
        try:
            code, out, err = self.docker.run([
                "run", "--rm", "--name", name, "--network", "none",
                "--memory", "1g", "--cpus", "1", "--pids-limit", "256",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--read-only", "--tmpfs", "/tmp:rw,size=64m",
                "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp",
                "-v", f"{self.data}:{IN_CONTAINER_DATA}:ro",
                "-v", f"{self.install / 'helene.json'}:{IN_CONTAINER_CONFIG}:ro",
                f"{st['image_repo']}:{st['image_tag']}",
                "python", "app/localharness/runner.py", "--check-extensions",
                "--data", IN_CONTAINER_DATA, "--host-version", st["to_version"]],
                timeout=300, limit=PROBE_LIMIT)
        finally:
            self.docker.run(["rm", "-f", name], timeout=60)
        try:
            report = _dict(json.loads(out[out.index("{"):]))
        except (ValueError, RecursionError):
            report = {"ok": False, "summary": f"репетиция не ответила: {_text((err or out).strip()[-300:], 300)}"}
        summary = _text(report.get("summary") or "", 600)
        st["extensions"] = {"ok": report.get("ok") is True, "summary": summary}
        if report.get("ok") is not True and not st["plan"].get("force_extensions"):
            raise UpdateError(f"расширения владельца не грузятся под {st['to_version']}: "
                              f"{summary} — план с force_extensions обновит всё равно")
        self.step("расширения грузятся" if report.get("ok") is True else "расширения не грузятся",
                  ok=report.get("ok") is True, note=summary)

    def _undo_prepare(self) -> str:
        """Вернуть всё, что успела подготовка. Не бросает: -> что не вернулось (или "")."""
        st = self.state
        problems = []
        try:
            if st.get("swapped"):
                self._undo_code()
        except OSError as exc:
            problems.append(f"прежний код не вернулся: {exc} (он в {st.get('backup_dir')}/code)")
        try:
            if st.get("built") and st.get("tagged"):
                self._retag_old()
        except UpdateError as exc:
            problems.append(str(exc))
        else:
            self._drop_images(tag=True)
        self._cleanup_stage()
        return "; ".join(problems)

    def _abort_unstopped(self, why: str) -> None:
        """Сбой до остановки агента: папки, тег и образ — назад; контейнер не трогаю.

        Пересоздавать работающего агента незачем: пока его не остановили, он жил прежним
        образом и прежними папками (подменённые папки монтирование держит по самой папке,
        а не по пути). Проверяю только, что он на ногах."""
        left = self._undo_prepare()
        down = self._ensure_running()
        self.finish("failed", f"не вышло: {why[:600]} — агента не останавливал, "
                              + (f"вернулось не всё: {left}" if left else "прежний код и образ на месте")
                              + (f"; {down}" if down else ""),
                    "Обновление не удалось ещё до остановки агента — всё осталось как было."
                    if not (left or down) else
                    "Обновление не удалось, и вернулось не всё — подробности ниже, их стоит показать "
                    "тому, кто ставил Hélène.")

    def _ensure_running(self) -> str:
        """Агент на ногах? Нет — поднять прежним образом. -> что не вышло (или "")."""
        try:
            if self.inspect().get("running"):
                return ""
        except UpdateError:
            pass
        try:
            code, out, err = self.compose("up", "-d", "--no-build", "--no-deps",
                                          self.state.get("service") or "helene", timeout=300)
        except UpdateError as exc:
            return f"агент стоит и не поднялся: {exc}"
        return "" if code == 0 else f"агент стоит и не поднялся: {(err or out).strip()[-300:]}"

    def _space_for_data(self, when: str) -> None:
        """Место под копию data/ — по её размеру СЕЙЧАС: сборка образа ест тот же диск, а
        агент пишет, пока не остановлен. Сервер общий: забить его диск доверху нельзя."""
        size = dir_size(self.data)
        free = shutil.disk_usage(self.install).free
        self.state.setdefault("backup", {})["data_bytes"] = size
        if free < size + SPACE_MARGIN:
            raise UpdateError(f"{when}: под копию data/ места не хватает — нужно около "
                              f"{mb(size + SPACE_MARGIN)}, свободно {mb(free)}. План с копией только "
                              "кода (backup: code) займёт меньше")

    def _backup_config(self, bdir: Path) -> None:
        """Копия helene.json — для ручного случая и для возврата вместе с данными."""
        src = self.install / "helene.json"
        try:
            size = os.stat(src).st_size
        except OSError:
            return
        if size > MAX_CONFIG:
            # Его пишет агент: гигабайтный helene.json забил бы копией диск.
            self.step(f"helene.json больше {mb(MAX_CONFIG)} — в копию не беру", ok=False)
            return
        shutil.copy2(src, bdir / "helene.json")

    def _backup_data(self, bdir: Path) -> None:
        """Копия data/ — рядом, с меткой завершения: годной считается только законченная.

        Частичная копия (диск кончился посреди) удаляется: вернуть её «из копии» значило бы
        заменить живую память агента обрывком."""
        st = self.state
        partial, dest = bdir / "data.partial", bdir / "data"
        self.step(f"делаю копию памяти агента ({mb(st['backup'].get('data_bytes') or 0)})", note="data/")
        shutil.rmtree(partial, ignore_errors=True)
        try:
            shutil.copytree(self.data, partial, symlinks=True, ignore=_only_plain)
        except BaseException:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        os.rename(partial, dest)
        st["data_backup"] = str(dest)
        self.step("копия памяти готова")

    def models_seen(self, source: str | None) -> int | None:
        """Сколько записей в папке моделей, если она лежит в установке (мне видна), иначе None."""
        if not source:
            return None
        path = Path(os.path.normpath(source))
        if self.install not in path.parents:
            return None
        try:
            return len(os.listdir(path))
        except OSError:
            return 0

    def _switch(self, bdir: Path) -> None:
        """Подмена: прежняя поставка в копию, новая на место; остановка, копия data/,
        перенос правок агента, подъём."""
        st = self.state
        root = Path(st["stage_dir"]) / "Helene"
        full = st["plan"]["backup"] == "full"
        if full:
            self._space_for_data("после сборки")
        code_dir = bdir / "code"
        code_dir.mkdir(parents=True, exist_ok=True)
        self._backup_config(bdir)
        # Чистый исходник новой версии — база следующего обновления: запомнить ДО того,
        # как в него лягут правки агента.
        self.save_pristine(root, st["to_version"])
        self.step("кладу новую версию на место прежней", note="прежняя — в копии")
        for entry in sorted(root.iterdir(), key=lambda p: p.name):
            if entry.name in KEEP_IN_PLACE:
                continue
            target = self.install / entry.name
            # Запись — ДО переноса: упади я между двумя rename, откат знает, что было
            # (а `_undo_code` смотрит, где что лежит на самом деле, а не верит записи).
            st["swapped"].append({"name": entry.name, "had_old": target.exists() or target.is_symlink()})
            self.save()
            if st["swapped"][-1]["had_old"]:
                os.rename(target, code_dir / entry.name)
            os.rename(entry, target)
        self._carry_owner_files(code_dir)
        if not st.get("code_mounted"):
            self._layer_fits()
        self._wait_idle()
        self.step("останавливаю агента на несколько минут")
        # «Остановлен» — ДО команды: упади я посреди неё, восстановление поднимет агента,
        # а не решит, что он работает, и не оставит его лежать со словами «всё как было».
        st["stopped"] = True
        self.save()
        self._must(self.compose("stop", "-t", "60", st["service"], timeout=180), "агент не остановился")
        if not st.get("code_mounted"):
            self._capture_layer(bdir)
        if full:
            self._space_for_data("после остановки агента")
            self._backup_data(bdir)
        self._carry_agent_code(bdir)
        self.step(f"запускаю {st['to_version']}")
        st["new_started"] = True
        self.save()
        self._must(self.compose("up", "-d", "--no-build", "--force-recreate", "--no-deps",
                                st["service"], timeout=300), "новая версия не поднялась")

    def _carry_owner_files(self, code_dir: Path) -> None:
        """Своё владельца в папках поставки (модели в `server/models`, `server/.env`, его
        override) — в новые папки. Код агента (`tree/`, `app/`) переносит `_carry_agent_code`."""
        st = self.state
        moved: list[str] = []
        shipped = self.shipped(st.get("from_version") or "")
        for row in st.get("swapped") or []:
            name = row["name"]
            if name in CODE_DIRS or not row.get("had_old"):
                continue
            old, new = code_dir / name, self.install / name
            if old.is_dir() and not old.is_symlink() and new.is_dir() and not new.is_symlink():
                moved += carry_owner_files(old, new, name, shipped)
        st["owner_files"] = moved
        if moved:
            self.step("переношу файлы владельца (модели, настройки)", note=", ".join(moved[:20]))

    def _layer_fits(self) -> None:
        """Код агента живёт в слое контейнера: снимок слоя и копия кода займут место — хватит ли.

        Размер слоя считает демон (`SizeRw`), агенту он не подвластен. Проверка — ДО остановки
        агента: не хватит — отказ, пока он ещё работает."""
        code, out, _err = self.docker.run(["inspect", "--size", "-f", "{{.SizeRw}}", self.cfg.container],
                                          timeout=300)
        size = int(_num(out.strip())) if code == 0 else 0
        free = shutil.disk_usage(self.install).free
        if free < 2 * size + SPACE_MARGIN:
            raise UpdateError(f"код агента живёт в контейнере, и его слой — {mb(size)}: снимок и "
                              f"копия займут около {mb(2 * size + SPACE_MARGIN)}, свободно {mb(free)}")

    def _capture_layer(self, bdir: Path) -> None:
        """Код агента из слоя контейнера (установка до 27.09) — пока контейнер ещё тот же.

        Первым делом — снимок всего контейнера образом отката: пересоздай его кто угодно, и слой
        с правками агента пропал бы, а со снимком откат вернёт агента вместе с ними. Потом — сам
        код: из контейнера (сторона агента для переноса) и из прежнего образа (база — каким код
        был до его правок). Всё — через докер: ссылки агента у меня не разыменовываются.
        """
        st = self.state
        box = self.cfg.container
        self.step("сохраняю код агента из контейнера — в нём его правки")
        self._must(self.docker.run(["commit", "--pause=false", box,
                                    f"{st['image_repo']}:{st['rollback_tag']}"], timeout=900),
                   "снимок контейнера агента не сделался")
        st["layer_committed"] = True
        self.save()
        layer, image = bdir / "layer", bdir / "image"
        for root in (layer, image):
            shutil.rmtree(root, ignore_errors=True)
            root.mkdir(parents=True)
        for name in CODE_DIRS:
            self._must(self.docker.run(["cp", f"{box}:{IN_CONTAINER_CODE[name]}", str(layer / name)],
                                       timeout=900), f"код агента ({name}/) не скопировался из контейнера")
        temp = f"helene-base-{secrets.token_hex(4)}"
        self._must(self.docker.run(["create", "--name", temp, st["old_image_id"]], timeout=120),
                   "прежний образ не открылся")
        try:
            for name in CODE_DIRS:
                self._must(self.docker.run(["cp", f"{temp}:{IN_CONTAINER_CODE[name]}", str(image / name)],
                                           timeout=900), f"чистый код ({name}/) не скопировался из образа")
        finally:
            self.docker.run(["rm", "-f", temp], timeout=120)
        st["layer_captured"] = True

    def _carry_agent_code(self, bdir: Path) -> None:
        """Правки агента в `tree/` и `app/` — на новую версию; что не легло — ему материалом.

        Агент уже остановлен: его прежний код (в копии) больше не меняется под руками. База —
        чистый исходник прежней версии; у установки до 27.09 (код в слое контейнера) база —
        прежний образ, а код агента — снятый из контейнера (`_capture_layer`).
        """
        st = self.state
        from_version, to_version = st.get("from_version") or "", st["to_version"]
        report: dict = {"mounted": True, "edited": [], "carried": [], "merged": [], "conflicts": [],
                        "skipped": [], "folder": ""}
        if st.get("code_mounted"):
            base_root, mine_root = self.pristine(from_version), bdir / "code"
            why = st.get("base_why") or "чистого исходника прежней версии нет"
        else:
            report["from_layer"] = True
            mine_root = bdir / "layer"
            base_root = bdir / "image" if all((bdir / "image" / n).is_dir() for n in CODE_DIRS) else None
            why = "чистый код прежней версии из образа не достался"
        if base_root is None:
            self._give_old_code(mine_root, report, why)
            st["agent_code"] = report
            self._remember_carried(bdir)
            return
        self.step("переношу правки агента на новую версию")
        labels = (f"агент ({from_version})", f"чистая {from_version}", f"выпуск {to_version}")
        materials: list[dict] = []
        diffs: list[str] = []
        budget = [MAX_MATERIALS]
        for name in CODE_DIRS:
            rep = carry_code(base_root / name, mine_root / name, self.install / name,
                             work=self.home / "tmp", labels=labels, prefix=name, budget=budget)
            for key in ("edited", "carried", "merged", "conflicts", "skipped"):
                report[key].extend(rep[key])
            materials.extend(rep["materials"])
            diffs.extend(rep["diff"])
        if report["edited"]:
            folder = self._materials_folder(to_version)
            try:
                self.shared.put(("workspace", folder, "README.md"),
                                materials_readme(report, from_version, to_version).encode("utf-8"))
                patch = "".join(diffs).encode("utf-8")
                self.shared.put(("workspace", folder, "edits.diff"), patch[:MAX_DIFF])
                for row in materials:
                    parts = row["path"].split("/")
                    for side in ("mine", "base", "theirs", "merged"):
                        blob = row.get(side)
                        if blob is not None:
                            self.shared.put(("workspace", folder, *parts[:-1], f"{parts[-1]}.{side}"),
                                            blob)
                report["folder"] = f"workspace/{folder}"
            except OSError as exc:
                # Материалы — подсказка агенту, не условие обновления: не легли — сказать,
                # а не валить подмену (например, `workspace` у агента оказался ссылкой).
                report["materials_error"] = f"материалы не легли в workspace/{folder}: {exc}"
        summary = (f"правок агента в коде: {len(report['edited'])}; перенесено {len(report['carried'])}, "
                   f"слито {len(report['merged'])}, не легло {len(report['conflicts'])}"
                   + (f", не переносил {len(report['skipped'])}" if report["skipped"] else ""))
        report["summary"] = summary
        st["agent_code"] = report
        self._remember_carried(bdir)
        self.step("правки агента перенесены" if not report["conflicts"] else "правки агента перенесены не все",
                  ok=not report["conflicts"], note=summary)

    def _give_old_code(self, mine_root: Path, report: dict, why: str) -> None:
        """Без чистой базы правок не видно: прежний код агента — ему целиком, с потолком."""
        st = self.state
        from_version, to_version = st.get("from_version") or "", st["to_version"]
        report["no_base"] = why
        self.step("правки агента не с чем сравнить — отдаю ему прежний код целиком", ok=False, note=why)
        folder = self._materials_folder(to_version)
        written, left = 0, MAX_MATERIALS
        try:
            for name in CODE_DIRS:
                for rel, kind in snapshot(mine_root / name).items():
                    if kind[0] != "file":
                        continue
                    blob = read_plain(mine_root / name / rel)
                    if blob is None or len(blob) > left:
                        continue
                    left -= len(blob)
                    self.shared.put(("workspace", folder, "old-code", name, *rel.split("/")), blob)
                    written += 1
            self.shared.put(("workspace", folder, "README.md"),
                            materials_readme(report, from_version, to_version, no_base=why).encode("utf-8"))
            report["folder"] = f"workspace/{folder}"
        except OSError as exc:
            report["materials_error"] = f"прежний код не лёг в workspace/{folder}: {exc}"
        report["old_code_files"] = written

    def _remember_carried(self, bdir: Path) -> None:
        """Снимок кода новой версии сразу после переноса: по нему откат увидит, что агент
        правил уже В НОВОЙ версии (на испытании), и отдаст ему эти правки."""
        try:
            snap = {name: snapshot(self.install / name) for name in CODE_DIRS}
            (bdir / "code-after-carry.json").write_text(json.dumps(snap), "utf-8")
        except (OSError, ValueError) as exc:
            log(f"снимок кода после переноса не записался: {exc}")

    def _materials_folder(self, version: str) -> str:
        """Свежая папка материалов в доме агента: `update-<версия>`, занята — с меткой времени."""
        name = f"update-{version}"
        if self.shared.exists("workspace", name):
            name = f"update-{version}-{stamp()}"
        return name

    def _wait_idle(self) -> None:
        """Не рвать ход агента на полуслове: ждём его конца, но не вечно."""
        deadline = self.clock() + IDLE_WAIT
        said_once = False
        while self.clock() < deadline:
            if not self._agent_busy():
                return
            if not said_once:
                self.step("агент сейчас отвечает — жду, пока закончит (до 3 минут)")
                said_once = True
            self.sleep(3)
        self.step("агент не закончил за 3 минуты — останавливаю всё равно", ok=False)

    def _agent_busy(self) -> bool:
        reader = self.shared.read(*CTL, "desk_inbox", ".reader.json")
        return reader.get("busy") is True and self.clock() - _num(reader.get("at")) < 60

    def _verify(self, version: str, checks: list[str], wait_min: int,
                key: str = "checks") -> list[dict]:
        """Ждать проверок. `key` — куда в расписку: проверки отката не затирают проверки
        новой версии — иначе «не прошло» читалось бы рядом с зелёным списком."""
        st = self.state
        self.step(f"проверяю, что всё работает (до {wait_min} мин)", note=", ".join(checks))
        deadline = self.clock() + wait_min * 60
        results: list[dict] = []
        down = 0
        while True:
            try:
                info = self.inspect()
            except UpdateError as exc:
                info = {"running": False, "restarting": False, "status": str(exc)[:120],
                        "restart_count": 0, "env": {}, "ports": {}, "mounts": {}}
            probe: dict = {}
            if info.get("running"):
                code, out, _err = self.docker.run(["exec", self.cfg.container, "python", "-c", PROBE],
                                                  timeout=60, limit=PROBE_LIMIT)
                try:
                    probe = _dict(json.loads(out.strip().splitlines()[-1])) if code == 0 else {}
                except (ValueError, IndexError, RecursionError):
                    probe = {}
            info["models_seen"] = self.models_seen((info.get("mounts") or {}).get("/models"))
            self._last_probe = probe
            results = evaluate(probe, info, checks, version, st.get("carried_before") or {})
            st[key] = results
            self.save()
            if all(r["ok"] for r in results):
                self.step("всё работает", note=", ".join(r["name"] for r in results))
                return results
            lost = doomed(probe, info)
            if lost:
                for row in results:
                    if row["name"] == "runner" and not row["ok"]:
                        row["note"] = lost
                st[key] = results
                self.step("новая версия не запускается — ждать дальше незачем", ok=False, note=lost)
                return results
            down = 0 if info.get("running") else down + 1
            if down >= 6:
                self.step("новая версия не держится — падает", ok=False, note=str(info.get("status")))
                return results
            if self.clock() >= deadline:
                return results
            self.sleep(5)

    # --- испытание --------------------------------------------------------------

    def _after_checks(self) -> None:
        """Mechanical checks finish the update; no agent-dependent probation."""
        self._success(extra="Обновление завершено без ожидания проверки агентом")

    def trial_tick(self) -> None:
        """Retire a legacy probation left by an older updater without claiming acceptance."""
        st = self.state
        st["retired_trial"] = st.pop("trial", {})
        self._success(extra="Прежнее автоматическое испытание снято; проверка агентом не требуется")

    def _undo_code(self) -> list[str]:
        """Вернуть прежнюю поставку на место. -> что вернулось.

        Смотрит, где что лежит на самом деле, а не верит записи: строка «перенесено»
        пишется ДО переименований, и упади я между записью и первым rename, прежняя папка
        так и стоит на месте — унести её в `failed-new` значило бы оставить агента без кода.
        Повторный вход (после сбоя посреди отката) ничего не ломает.
        """
        st = self.state
        bdir = Path(st["backup_dir"])
        failed = bdir / "failed-new"
        back = []
        for row in reversed(st.get("swapped") or []):
            name = row["name"]
            target = self.install / name
            old = bdir / "code" / name
            old_there = old.exists() or old.is_symlink()
            if row.get("had_old") and not old_there:
                continue                  # прежнее не уезжало (или уже вернулось) — оно на месте
            if target.exists() or target.is_symlink():
                failed.mkdir(parents=True, exist_ok=True)
                dest = failed / name
                if dest.exists() or dest.is_symlink():
                    dest = failed / f"{name}.{secrets.token_hex(3)}"
                os.rename(target, dest)
            if row.get("had_old"):
                os.rename(old, target)
                back.append(name)
        st["swapped"] = []
        self.save()
        return back

    def _retag_old(self) -> None:
        st = self.state
        self._must(self.docker.run(["tag", f"{st['image_repo']}:{st['rollback_tag']}",
                                    f"{st['image_repo']}:{st['image_tag']}"], timeout=60),
                   "прежний образ не вернулся на место")

    def _drop_images(self, *, tag: bool) -> None:
        """После возврата — свои лишние образы: собранный мной новый (по id; контейнер им уже
        не живёт — докер без -f иначе и не удалит) и, если просили, тег отката (копии, к
        которой он относится, нет — ретеншн его никогда бы не снял)."""
        st = self.state
        new_id = st.get("new_image_id")
        if new_id and new_id != st.get("old_image_id"):
            self.docker.run(["rmi", new_id], timeout=120)
        if tag and st.get("tagged"):
            self.docker.run(["rmi", f"{st['image_repo']}:{st['rollback_tag']}"], timeout=120)

    def _restore_data(self) -> str:
        """Данные (и helene.json) — из копии: только законченной и только когда прежняя версия
        на данных новой не поднимается."""
        st = self.state
        bdir = Path(st["backup_dir"])
        saved, after = bdir / "data", bdir / "data-after-failed"
        if not st.get("data_backup") or not saved.is_dir():
            return "законченной копии data/ нет — вернуть данные нечем"
        try:
            if self.data.exists() and not after.exists():
                os.rename(self.data, after)
            if not self.data.exists():
                os.rename(saved, self.data)
        except OSError as exc:
            return f"data/ не возвращена ({exc}); копия до обновления — {saved}"
        st["data_restored"] = True
        words = f"data/ возвращена из копии; то, что успела записать новая версия, — в {after}"
        config = bdir / "helene.json"
        try:
            if config.is_file():
                live = self.install / "helene.json"
                shutil.copyfile(live, bdir / "helene.json.after-failed")
                shutil.copyfile(config, live)          # тот же inode: он смонтирован файлом
                words += "; helene.json тоже из копии (нынешний — рядом, .after-failed)"
        except OSError as exc:
            words += f"; helene.json не вернулся: {exc}"
        return words

    def _up_old(self) -> tuple[int, str, str]:
        """Поднять прежнюю версию. Обычно — пересоздав контейнер. Но у установки до 27.09 код
        агента живёт в слое контейнера, и пока снимок слоя не сделан (`_capture_layer`),
        пересоздать — значит стереть его правки: тогда только поднять тот же контейнер."""
        st = self.state
        keep = not st.get("code_mounted") and not st.get("layer_committed")
        return self.compose("up", "-d", "--no-build", "--no-deps",
                            *([] if keep else ["--force-recreate"]), st["service"], timeout=300)

    def _rollback(self, why: str, plain: str = "") -> None:
        """Откат: прежние код и образ. Память агента — нет, пока без этого можно.

        «Откат кода не откатывает память — это её жизнь, а не версия продукта» (Егор,
        25.09): переписка и дневник за время новой версии — такая же жизнь агента, и
        `helene.json` (настройки, которые владелец или агент поменяли за это время) — тоже.
        Копия `data/` и `helene.json` идёт в дело, только если прежняя версия на данных
        новой не поднимается.

        `plain` — почему, простыми словами: из этого складывается итог для владельца.

        Повторный вход (после сбоя посреди отката) идёт тем же путём: каждый шаг смотрит,
        что уже сделано. Сбой — ещё попытка через минуту; после трёх — итог «нужен человек».
        """
        st = self.state
        if not (st.get("state") == "running" and st.get("phase") == "rollback"):
            st.update(state="running", phase="rollback", rollback_why=why, rollback_plain=plain)
        why = st.get("rollback_why") or why
        plain = st.get("rollback_plain") or plain or "что-то пошло не так"
        self.step("возвращаю прежнюю версию", ok=False, note=why)
        if not st.get("stopped"):
            self._abort_unstopped(why)
            return
        bdir = Path(st["backup_dir"])
        notes: list[str] = []
        results: list[dict] = []
        try:
            shutil.rmtree(bdir / "data.partial", ignore_errors=True)
            self._must(self.compose("stop", "-t", "30", st["service"], timeout=120), "агент не остановился")
            back = self._undo_code()
            notes.append("прежний код на месте" + (f" ({len(back)} частей)" if back else "")
                         + ("; правки агента в коде — как были до обновления"
                            if st.get("code_mounted") or st.get("layer_committed") else ""))
            self._retag_old()
            self._must(self._up_old(), "прежняя версия не поднялась")
            checks = [c for c in protocol.UPDATE_MANDATORY
                      if (c != "version" or st.get("from_version"))
                      and (c != "code" or st.get("code_mounted"))]
            wait = min(int(st["plan"]["wait_min"]), 10)
            results = self._verify(st.get("from_version") or "", checks, wait, key="rollback_checks")
            if (not all(r["ok"] for r in results) and st.get("new_started") and st.get("data_backup")
                    and not st.get("data_restored")):
                self.step("прежняя версия не запускается на новых данных — возвращаю и память из копии", ok=False)
                self._must(self.compose("stop", "-t", "30", st["service"], timeout=120),
                           "агент не остановился")
                notes.append("прежняя версия на данных новой не поднялась — " + self._restore_data())
                try:
                    self.beat()      # из копии вернулась и моя прежняя записка о себе
                except OSError:
                    pass
                self._must(self._up_old(), "прежняя версия не поднялась")
                results = self._verify(st.get("from_version") or "", checks, wait,
                                       key="rollback_checks")
            elif not st.get("data_restored"):
                notes.append("данные агента не трогал — это его жизнь, а не версия продукта"
                             + (f"; копия до обновления — {st['data_backup']}"
                                if st.get("data_backup") else ""))
        except Exception as exc:  # noqa: BLE001 — любой сбой: ещё попытка, потом — человек
            tries = int(st.get("rollback_tries") or 0) + 1
            st["rollback_tries"] = tries
            if tries < ROLLBACK_TRIES:
                self.recover_at = self.clock() + RECOVER_EVERY
                self.step(f"откат не удался (попытка {tries} из {ROLLBACK_TRIES}) — повторю через минуту",
                          ok=False, note=said(exc))
                raise
            st["rollback"] = {"ok": False, "notes": notes, "why": said(exc)[:600]}
            self._cleanup_stage()
            self.finish("failed", f"обновление не прошло ({why[:300]}), и откат НЕ поднялся: {said(exc)}. "
                                  f"Копии — {st['backup_dir']}; нужен человек на хосте",
                        f"Обновление не прошло ({plain}), и прежняя версия сама не запустилась. "
                        "Нужна помощь того, кто ставил Hélène на сервер: подробности ниже, память "
                        "агента и копии целы.")
            return
        ok = all(r["ok"] for r in results)
        if ok:
            self._drop_images(tag=False)
        try:
            after = self._after_update_edits(bdir)
            if after.get("count"):
                notes.append(f"правки кода, сделанные агентом в {st['to_version']} после подъёма "
                             f"({after['count']}), — ему в {after.get('folder') or 'workspace'}"
                             + (f" ({after['error']})" if after.get("error") else ""))
            self._rollback_note(why, after)
        except Exception as exc:  # noqa: BLE001 — подсказка агенту, не условие отката
            log(f"правки агента после подъёма не отдались: {said(exc)}")
        st["rollback"] = {"ok": ok, "notes": notes, "checks": results}
        self._cleanup_stage()
        was = st.get("from_version") or "прежнюю"
        if ok:
            self.finish("rolled_back", f"{st.get('to_version')} не прошла ({why[:400]}) — вернул "
                                       f"{was}: " + "; ".join(notes),
                        f"Не получилось — вернул прежнюю версию {was}. Почему: {plain}. "
                        "Память и настройки агента целы.")
        else:
            bad = "; ".join(f"{r['title']}: {r['note']}" for r in results if not r["ok"])
            self.finish("failed", f"обновление не прошло ({why[:300]}), откат поднят, но не "
                                  f"проверился: {bad}. Копии — {st['backup_dir']}",
                        f"Обновление не прошло ({plain}); прежняя версия {was} запущена, но работает "
                        f"не полностью: {plain_failures(results) or 'проверки не прошли'}. Нужна помощь "
                        "того, кто ставил Hélène на сервер.")
        # Мой код вернулся вместе с прежней поставкой: перезапускаюсь им.
        self.reexec = True

    def _after_update_edits(self, bdir: Path) -> dict:
        """Правки агента в коде НОВОЙ версии, сделанные после её подъёма (на испытании), — ему.

        Откат уносит новую версию в копию (`failed-new/`), куда агенту хода нет. Что он успел
        в ней поправить, видно против снимка сразу после переноса его прежних правок."""
        st = self.state
        try:
            saved = _dict(json.loads((bdir / "code-after-carry.json").read_text("utf-8")))
        except (OSError, ValueError):
            return {}
        failed = bdir / "failed-new"
        changed = []
        for name in CODE_DIRS:
            before = {rel: tuple(v) for rel, v in _dict(saved.get(name)).items() if isinstance(v, list)}
            now = snapshot(failed / name)
            changed += [(name, rel, now.get(rel)) for rel in sorted(set(before) | set(now))
                        if differs(before.get(rel), now.get(rel))]
        if not changed:
            return {}
        folder = (str((st.get("agent_code") or {}).get("folder") or "").removeprefix("workspace/")
                  or self._materials_folder(st["to_version"]))
        sub = "after-update" if not self.shared.exists("workspace", folder, "after-update") \
            else f"after-update-{stamp()}"
        out = {"count": len(changed), "files": [f"{n}/{r}" for n, r, _ in changed][:50],
               "folder": f"workspace/{folder}/{sub}", "gone": [], "written": 0}
        left = MAX_MATERIALS
        try:
            for name, rel, now in changed:
                blob = read_plain(failed / name / rel) if now and now[0] == "file" else None
                if blob is None or len(blob) > left:
                    out["gone"].append(f"{name}/{rel}")
                    continue
                left -= len(blob)
                self.shared.put(("workspace", folder, sub, name, *rel.split("/")), blob)
                out["written"] += 1
        except OSError as exc:
            out["error"] = f"легли не все: {exc}"
        return out

    def _rollback_note(self, why: str, after: dict) -> None:
        """В папке материалов обновления — что переход не состоялся: README там описывает
        перенос правок на версию, которой у агента теперь нет."""
        st = self.state
        folder = str((st.get("agent_code") or {}).get("folder") or "").removeprefix("workspace/")
        if not folder and after.get("folder"):
            folder = after["folder"].removeprefix("workspace/").split("/")[0]
        if not folder:
            return
        was = st.get("from_version") or "прежней версии"
        lines = [f"# Обновление {st.get('from_version') or '?'} → {st['to_version']} откачено", "",
                 f"Почему: {why}", "",
                 f"Ты снова на {was}: твой код — как был до обновления. README.md рядом описывает "
                 f"перенос твоих правок на {st['to_version']} — сейчас он не о том, что у тебя стоит.", ""]
        if after.get("count"):
            sub = after["folder"].rsplit("/", 1)[-1]
            lines += [f"Правки кода, сделанные тобой в {st['to_version']} после подъёма "
                      f"({after['count']}), — в `{sub}/`: в {was} их нет. Нужны — перенеси сам.", ""]
        if after.get("gone"):
            lines.append("Удалённое или не обычные файлы (не копировал): "
                         + ", ".join(f"`{p}`" for p in after["gone"][:20]))
        try:
            self.shared.put(("workspace", folder, f"ROLLBACK-{stamp()}.md"),
                            ("\n".join(lines) + "\n").encode("utf-8"))
        except OSError as exc:
            log(f"записка об откате не легла: {exc}")

    def _success(self, extra: str = "") -> None:
        """Итог «прошло». Сначала — метка «принимаю» в состоянии: упади я посреди уборки,
        перезапуск доведёт итог, а не забудет слово и не откатит принятое по сроку."""
        st = self.state
        if not (st.get("state") == "running" and st.get("phase") == "accepting"):
            st.update(state="running", phase="accepting", success_extra=extra)
            self.save()
        extra = st.get("success_extra") or extra
        self._cleanup_stage()
        try:
            self._retention()
        except Exception as exc:  # noqa: BLE001 — уборка старого не отменяет принятого
            log(f"уборка старых копий не удалась: {said(exc)}")
        checked = ", ".join(r["name"] for r in st.get("checks") or [])
        parts = [f"{st.get('from_version') or '?'} → {st['to_version']}: проверено {checked}"]
        if extra:
            parts.append(extra)
        code = st.get("agent_code") or {}
        if code.get("summary"):
            parts.append(code["summary"] + (f" (материалы — {code['folder']})" if code.get("conflicts") else ""))
        elif code.get("no_base"):
            parts.append(f"правки агента не с чем было сравнить — его прежний код в {code.get('folder')}")
        if st.get("owner_files"):
            parts.append("своё владельца в папках поставки перенесено: " + ", ".join(st["owner_files"][:10]))
        parts.append(f"прежняя версия отложена ({st['backup_dir']}), образ "
                     f"{st['image_repo']}:{st['rollback_tag']}")
        verdict = _dict(_dict(st.get("trial")).get("verdict"))
        plain = f"Готово: теперь стоит {st['to_version']}."
        if verdict.get("verdict") == "accept":
            plain += (" Агент проверил себя в новой версии — всё работает." if verdict.get("by") == "agent"
                      else " Ты подтвердил, что всё работает.")
        elif "мозг не настроен" in extra:
            plain += (" Сам агент проверить себя не мог — мозг не настроен; загляни, отвечает ли он.")
        if code.get("conflicts"):
            plain += " Часть правок агента в его собственном коде не легла на новую версию — он знает, где они."
        self.finish("done", ". ".join(part[:1].upper() + part[1:] for part in parts), plain)
        # Мой код тоже обновился вместе с поставкой: перезапускаюсь им.
        self.reexec = True

    def _cleanup_stage(self) -> None:
        st = self.state
        for key in ("stage_dir", "archive"):
            path = Path(st.get(key) or "")
            if not st.get(key) or not str(path).startswith(str(self.home)):
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()

    def _retention(self) -> None:
        """Держу последние N копий (и их образы) и исходники двух последних версий; остатки
        прерванных распаковок и слияний — прочь."""
        root = self.home / "backups"
        dirs = sorted((p for p in root.iterdir()
                       if p.is_dir() and re.fullmatch(r"\d{8}T\d{6}Z(-[0-9a-f]{4})?", p.name)),
                      key=lambda p: p.name) if root.is_dir() else []
        for old in dirs[:-self.cfg.keep]:
            shutil.rmtree(old, ignore_errors=True)
            self.docker.run(["rmi", f"{self.state['image_repo']}:helene-rollback-{old.name}"], timeout=120)
            log(f"старая копия убрана: {old.name}")
        if self.pristine_root.is_dir():
            for junk in self.pristine_root.glob(".tmp-*"):
                shutil.rmtree(junk, ignore_errors=True)
            versions = sorted((p for p in self.pristine_root.iterdir()
                               if p.is_dir() and protocol.version_tuple(p.name)),
                              key=lambda p: protocol.version_tuple(p.name))
            for old in versions[:-2]:
                shutil.rmtree(old, ignore_errors=True)
        for transient in ("stage", "tmp"):
            shutil.rmtree(self.home / transient, ignore_errors=True)

    def recover(self) -> None:
        """Прерванное обновление (перезапуск исполнителя или сбой тика): довести или
        откатить, но не бросить. Бросает — тогда `recover_due` повторит через минуту."""
        st = self.state
        current, phase = st.get("state"), st.get("phase")
        again = ("Исполнитель обновлений перезапустился, пока проверял новую версию, — ничего не "
                 "менялось. Можно попросить обновление ещё раз.")
        if current == "checking":
            self.finish("refused", "исполнитель перезапустился посреди сверки плана — ничего не "
                                   "тронуто; положи план снова", again)
            return
        if current == "confirmed":
            self.finish("failed", "исполнитель перезапустился сразу после «да» — ничего не тронуто; "
                                  "положи план снова", again)
            return
        if current == "trial" and phase in ("rollback", "accepting"):
            st["state"] = current = "running"      # расписка прежнего исполнителя: слово уже сказано
        if current != "running":
            return                        # испытание, ожидание «да», итоги — идут своим ходом
        self.check_self()
        log(f"довожу обновление {st.get('id')}: шаг «{st.get('step')}» ({phase})")
        if phase == "prepare":
            left = self._undo_prepare()
            down = self._ensure_running()
            self.finish("failed", "исполнитель перезапустился посреди подготовки; агент не "
                                  "останавливался, " + (f"но вернулось не всё: {left}" if left
                                                        else "всё возвращено как было")
                        + (f"; {down}" if down else ""),
                        "Исполнитель обновлений перезапустился, пока готовил новую версию, — агент "
                        "работал всё время, ничего не менялось. Можно попросить обновление ещё раз."
                        if not (left or down) else
                        "Исполнитель обновлений перезапустился посреди подготовки, и вернулось не всё — "
                        "подробности ниже, их стоит показать тому, кто ставил Hélène.")
            return
        if phase in ("switch", "verify"):
            if not st.get("stopped"):
                self._abort_unstopped("исполнитель перезапустился посреди подмены папок")
                return
            if not st.get("new_started"):
                self._rollback("исполнитель перезапустился посреди подмены, новая версия не поднималась",
                               plain="исполнитель обновлений перезапустился посреди установки")
                return
            results = self._verify(st["to_version"], st["plan"]["checks"], 5)
            if all(r["ok"] for r in results):
                self._settle(results)
            else:
                self._rollback("исполнитель перезапустился посреди подмены, новая версия не проверилась",
                               plain=plain_checks(results))
            return
        if phase == "accepting":
            self._success(extra=st.get("success_extra") or "")
            return
        self._rollback(st.get("rollback_why") or "исполнитель перезапустился посреди отката — довожу откат",
                       plain=st.get("rollback_plain") or "")


def carry_owner_files(old: Path, new: Path, prefix: str, shipped: set[str] | None = None) -> list[str]:
    """То, что владелец положил в папку поставки сам, — в новую папку той же поставки.

    Модели по умолчанию живут в `server/models` (compose: `${HELENE_MODELS:-./models}`),
    рядом — его `server/.env` и свой override. Выпуск их не несёт, и подмена папки целиком
    унесла бы их в копию: голос молча онемел бы, а ретеншн потом удалил бы модели вместе с
    копией. Жёсткими ссылками, где можно: гигабайты не копируются, а прежняя папка в копии
    остаётся целой для отката.

    Что есть в новом выпуске — за выпуском. Что было в ПРЕЖНЕМ выпуске (`shipped` — его
    список файлов), а новый убрал, — не воскрешаю: это не владельца. Списка нет (прежняя
    версия ставилась без меня и её исходника не нашлось) — переношу всё, чего нет в новом.
    -> перенесённое, по верхним именам («server/models», «server/.env»).
    """
    out: list[str] = []
    for rel, st, _dfd, _name in members(old):
        path = f"{prefix}/{rel}"
        src, dst = old / rel, new / rel
        if dst.exists() or dst.is_symlink() or (shipped is not None and path in shipped):
            continue
        if _blocked(new, rel):
            continue                          # в новом выпуске на месте папки — файл
        dst.parent.mkdir(parents=True, exist_ok=True)
        if stat.S_ISLNK(st.st_mode):
            os.symlink(os.readlink(src), dst)
        elif stat.S_ISREG(st.st_mode):
            _link_or_copy(src, dst)
        else:
            continue
        top = f"{prefix}/{rel.split('/')[0]}"
        if top not in out:
            out.append(top)
    return out


def _link_or_copy(src, dst):
    try:
        os.link(src, dst, follow_symlinks=False)
    except OSError:
        shutil.copy2(src, dst, follow_symlinks=False)
    return dst


def keep_self_copy(install: Path) -> None:
    """Копия своего кода в `.updater/self/` — запасной вход compose исполнителя.

    Подмена кладёт новую поставку двумя переименованиями, и между ними `server/updater/`
    нет. Перезапуск контейнера исполнителя ровно в этот миг (хост перезагрузился) без
    запасного входа крутил бы пустое место вечно, а довести подмену могу только я сам.
    """
    here = Path(__file__).resolve().parent
    dest = install / ".updater" / "self"
    if here == dest.resolve():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("updater.py", "protocol.py", "codecarry.py"):
        blob = (here / name).read_bytes()
        target = dest / name
        if target.is_file() and target.read_bytes() == blob:
            continue
        tmp = dest / f".{name}.{secrets.token_hex(3)}"
        tmp.write_bytes(blob)
        os.replace(tmp, target)


#: Итоги, после которых `watch` заканчивает.
FINAL = ("refused", "declined", "expired", "superseded", "done", "rolled_back", "failed")


def cli(argv: list[str]) -> int:
    """Команды для `server/install.sh` — внутри контейнера исполнителя (там его python):

        updater.py status                  готов ли исполнитель (код 0) — или почему нет
        updater.py plan <версия|latest>    обновить: план с согласием хоста (команду на сервере
                                           запускает владелец или его помощник — это и есть «да»)
        updater.py watch [id]              ход обновления словами, пока не итог или испытание

    Служба (без аргументов) и эти команды работают с одними файлами обмена.
    """
    cfg = Config.from_env()
    shared = Shared(cfg.install / "data")
    command = argv[0] if argv else ""
    if command == "status":
        beat = shared.read(*CTL, protocol.UPDATER_BEAT)
        fresh = time.time() - _num(beat.get("beat_epoch")) <= 30
        if fresh and beat.get("ok") is True:
            print(f"исполнитель готов; стоит {beat.get('current_version') or '?'}")
            return 0
        print(str(beat.get("why") or "исполнитель ещё не отметился") if fresh else "исполнитель ещё не отметился")
        return 1
    if command == "plan":
        version = (argv[1] if len(argv) > 1 else "latest").strip().lstrip("vV") or "latest"
        receipt = shared.read(*CTL, protocol.UPDATE_RECEIPT)
        if receipt.get("state") in ("checking", "confirmed", "running", "trial"):
            print(f"сейчас уже идёт обновление ({receipt.get('state')}: {receipt.get('step') or '…'}) — "
                  "дождись его конца")
            return 3
        plan, why = protocol.validate_plan({
            "id": secrets.token_hex(8), "version": version, "backup": "full", "asked_by": "host",
            "asked_utc": utc(), "reason": "обновление командой на сервере", "consent": "host"})
        if plan is None:
            print(f"план не сложился: {why}")
            return 2
        shared.write(*CTL, protocol.UPDATE_PLAN, plan)
        print(plan["id"])
        return 0
    if command == "watch":
        want = argv[1] if len(argv) > 1 else ""
        deadline = time.time() + 90 * 60
        shown, last_state = 0, ""
        while time.time() < deadline:
            r = shared.read(*CTL, protocol.UPDATE_RECEIPT)
            if want and r.get("id") != want:
                time.sleep(2)
                continue
            steps = r.get("steps") if isinstance(r.get("steps"), list) else []
            shown = min(shown, len(steps))
            for row in steps[shown:]:
                if isinstance(row, dict):
                    print(f"  {'·' if row.get('ok') else '✗'} {clean(str(row.get('step') or ''))}", flush=True)
            shown = len(steps)
            state = str(r.get("state") or "")
            if state != last_state and state == "awaiting":
                print("  … план ждёт согласия владельца", flush=True)
            last_state = state
            if state == "trial":
                trial = _dict(r.get("trial"))
                print(f"\n{r.get('to_version')} запущена и работает. Сейчас агент сам проверяет себя "
                      f"(до {trial.get('minutes') or '?'} мин): если что-то не так, исполнитель вернёт "
                      "прежнюю версию сам. Больше ничего делать не нужно.")
                return 0
            if state in FINAL:
                print("\n" + clean(str(r.get("summary") or r.get("note") or state)))
                return 0 if state == "done" else 1
            time.sleep(2)
        print("\nобновление идёт дольше полутора часов — смотри окно или: docker logs helene-updater")
        return 1
    print(cli.__doc__)
    return 2


def main() -> int:
    if len(sys.argv) > 1:
        return cli(sys.argv[1:])
    cfg = Config.from_env()
    updater = Updater(cfg)
    log(f"исполнитель обновлений: установка {cfg.install}, контейнер {cfg.container}, "
        f"выпуски {cfg.releases}")
    try:
        keep_self_copy(cfg.install)
    except OSError as exc:
        log(f"запасная копия своего кода не легла: {exc}")
    health = updater.check_self()
    log("готов" if health["ok"] else f"работать не могу: {health['why']}")

    def beating() -> None:
        while True:
            try:
                updater.beat()
            except Exception as exc:  # noqa: BLE001 — записка не должна ронять исполнителя
                log(f"записка о себе не легла: {exc}")
            time.sleep(BEAT_EVERY)

    threading.Thread(target=beating, name="beat", daemon=True).start()
    last_self = time.monotonic()
    while True:
        try:
            if time.monotonic() - last_self > 60:
                updater.check_self()
                last_self = time.monotonic()
            updater.refresh_latest()
            # Прерванное обновление (перезапуск посреди него) тик доводит сам: `recover_due`.
            updater.tick()
        except Exception as exc:  # noqa: BLE001 — один сбой тика не должен ронять исполнителя
            log(f"тик упал: {type(exc).__name__}: {exc}")
        if updater.reexec:
            updater.reexec = False
            target = cfg.install / "server" / "updater" / "updater.py"
            if target.is_file():
                log("перезапускаюсь кодом установки")
                try:
                    os.execv(sys.executable, [sys.executable, str(target)])
                except OSError as exc:
                    log(f"перезапуск не удался ({exc}) — работаю прежним кодом")
        time.sleep(3)


if __name__ == "__main__":
    sys.exit(main())
