# -*- coding: utf-8 -*-
"""Исполнитель обновления Hélène на сервере — сосед СНАРУЖИ контейнера агента.

Повод — жалоба Дмитрия К (26.09): его агент весь день готовил обновление и упирался
в «нет доступа». Агент живёт внутри контейнера, который надо заменить; изнутри у него
нет докера, сокета и путей хоста, и каждый шаг шёл кругом через человека.

Отмычку внутрь агента (сокет докера в его контейнер) мы не даём: это был бы root на
хосте у всего, что умеет писать в его дерево. Вместо неё — этот исполнитель: свой
контейнер рядом, со своим сокетом, и файловый протокол в дереве агента — тот же
приём, что у надзора (`app/deskd/control.py`, раздел «обновление на сервере»):

    memory/.control/update-plan.json          агент (рука update_request) или окно кладёт план
    memory/.control/update-plan.receipt.json  я отвечаю: сверил / жду «да» / иду / итог
    memory/.control/update-plan.confirm.json  «да» или «нет» человека
    memory/.control/updater.json              я о себе каждые ~5 с (окно и рука видят, что я есть)
    memory/.control/update-history.jsonl      итоги прошлых обновлений

Что я делаю по «да» — в таком порядке, чтобы живой агент не трогался до последнего:
  1. качаю архив выпуска, сверяю sha256 и паспорт (версия, полнота), распаковываю рядом;
  2. откладываю прежний образ тегом отката и собираю новый ИЗ РАСПАКОВАННОГО — старые
     папки на месте, агент работает;
  3. репетирую расширения владельца под новым образом (как мастер на Windows);
  4. откладываю прежний код в копию и кладу новый; `data/` и `helene.json` не трогаю;
  5. жду конца хода агента, останавливаю его контейнер, копирую `data/` (если план
     просит полную копию) и поднимаю новую версию с теми же адресом, портом, именами
     хостов и моделями, что были у прежнего контейнера (беру их из самого контейнера:
     владелец задавал их в командной строке, и больше они нигде не записаны);
  6. проверяю из плана — каждая по закрытому списку; провал любой обязательной —
     откат: прежний код, прежний образ, прежние данные, подъём, проверка, расписка.

Чего я не делаю по построению:
  * без «да» человека — ничего; «да» привязано к одноразовому ключу моей расписки;
  * ставлю только официальный выпуск и только версию НОВЕЕ текущей. Адрес выпусков —
    из МОЕЙ среды, не из helene.json: тот правит агент, и адрес оттуда значил бы
    «поставь root'ом код, на который укажет агент»;
  * трогаю только один контейнер — названный мне (`HELENE_CONTAINER`) — и только его
    compose-проект, по меткам самого контейнера; никаких prune, down и --remove-orphans;
  * своё состояние держу в `<установка>/.updater/`, куда контейнеру агента хода нет;
    файлы в его дереве для меня — только ввод (план, «да») и вывод (расписка), и читаю
    я их без следования ссылкам: подложенная ссылка не уведёт меня за пределы `data/`.

Запуск — из compose-файла рядом (`server/updater/docker-compose.yml`):
    python3 /opt/helene/server/updater/updater.py
Среда: HELENE_DIR (папка установки — ТЕМ ЖЕ путём, что на хосте), HELENE_CONTAINER,
HELENE_UPDATE_RELEASES, HELENE_UPDATE_ASSET, HELENE_UPDATE_KEEP, HELENE_UPDATE_INSECURE.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
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

# Протокол — общий с каналом и рукой агента. В поставке он лежит в `app/deskd`, в
# репозитории — в `desk/deskd`; оба пути на два уровня выше этого файла.
_HERE = Path(__file__).resolve()
for _cand in (_HERE.parents[2] / "app", _HERE.parents[2]):
    if (_cand / "deskd" / "control.py").is_file():
        if str(_cand) not in sys.path:
            sys.path.insert(0, str(_cand))
        break
from deskd import control  # noqa: E402 — путь известен только здесь

SCHEMA = "helene.updater.v1"
DEFAULT_RELEASES = "https://api.github.com/repos/josephsteuerjr/praxis/releases"
PRODUCT = "Hélène"

#: Внутри контейнера агента (Dockerfile: WORKDIR /opt/helene, compose: ../data).
IN_CONTAINER_ROOT = "/opt/helene"
IN_CONTAINER_DATA = "/opt/helene/data"
IN_CONTAINER_CONFIG = "/opt/helene/helene.json"
#: Порт канала ВНУТРИ контейнера фиксирован в compose ("127.0.0.1:${HELENE_PORT}:8094").
CONTAINER_PORT = "8094/tcp"

#: Что остаётся на месте при подмене: данные агента и его конфиг. Остальное —
#: поставка, её меняем целиком (как «распаковать поверх» в README-СЕРВЕР).
KEEP_IN_PLACE = frozenset({"data", "helene.json", ".updater"})

#: Без чего распакованная поставка — не поставка сервера.
REQUIRED = ("helene-build.json", "requirements.txt", "app/deskapp.py", "app/desk.json",
            "app/localharness/runner.py", "app/deskd/control.py", "tree",
            "server/Dockerfile", "server/docker-compose.yml", "server/serverboot.py")

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
SPACE_MARGIN = 512 * 1024 ** 2
BEAT_EVERY = 5.0
LATEST_EVERY = 6 * 3600
IDLE_WAIT = 180                   # сколько ждать конца хода агента перед остановкой


class UpdateError(RuntimeError):
    """Отказ шага с причиной словами — она уходит в расписку как есть."""


def utc(epoch: float | None = None) -> str:
    moment = dt.datetime.fromtimestamp(epoch, dt.UTC) if epoch else dt.datetime.now(dt.UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def mb(size: int | float) -> str:
    return f"{size / 1024 ** 2:.0f} МБ" if size < 1024 ** 3 else f"{size / 1024 ** 3:.1f} ГБ"


def log(text: str) -> None:
    print(f"[updater {utc()}] {text}", flush=True)


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

    def append(self, *path_and_row) -> None:
        *path, row = path_and_row
        *parts, name = path
        dfd = self._dir_fd(tuple(parts), create=True)
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o644,
                         dir_fd=dfd)
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
    """Клиент `docker` (с плагином compose) из образа исполнителя, по сокету хоста."""

    def run(self, args: list[str], *, env: dict | None = None,
            timeout: float = 600) -> tuple[int, str, str]:
        full = dict(os.environ)
        full.update({k: str(v) for k, v in (env or {}).items()})
        try:
            done = subprocess.run(["docker", *args], capture_output=True, env=full,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", f"docker {' '.join(args[:3])}: не уложился в {timeout:.0f} с"
        except OSError as exc:
            return 127, "", f"docker не запускается: {exc}"
        return (done.returncode, done.stdout.decode("utf-8", "replace"),
                done.stderr.decode("utf-8", "replace"))


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
    if control.version_tuple(version) is None:
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


def image_repo(image: str) -> tuple[str, str]:
    """«helene-helene» / «helene-helene:latest» -> (repo, tag)."""
    head, _, last = image.rpartition("/")
    name, sep, tag = last.partition(":")
    repo = f"{head}/{name}" if head else name
    return repo, (tag if sep else "latest")


def evaluate(probe: dict, info: dict, checks: list[str], version: str, before: dict) -> list[dict]:
    """Проверки после подъёма. Каждая — по имени из закрытого списка протокола."""
    state = ((probe.get("state") or {}).get("body") or {}) if isinstance(probe, dict) else {}
    health = (probe.get("health") or {}) if isinstance(probe, dict) else {}
    out = []
    for name in checks:
        ok, note = False, ""
        if name == "running":
            ok = info.get("running", False) and not info.get("restarting", False)
            note = f"{info.get('status') or '?'}, перезапусков {info.get('restart_count', 0)}"
        elif name == "channel":
            ok = health.get("code") == 200
            note = "отвечает" if ok else (health.get("why") or f"код {health.get('code')}")
        elif name == "version":
            got = str((state.get("desk") or {}).get("version") or "")
            ok = bool(version) and got == version
            note = f"канал говорит {got or '—'}, ждём {version or '—'}"
        elif name == "supervisor":
            sup = probe.get("supervisor") or {}
            age = float(probe.get("now") or 0) - float(sup.get("beat_epoch") or 0)
            ok = sup.get("kind") == "serverboot" and 0 <= age <= control.BEAT_STALE
            note = (f"{sup.get('kind') or 'записки нет'}, бился {age:.0f} с назад"
                    if sup.get("beat_epoch") else str(sup.get("why") or "записки надзора нет"))
        elif name == "runner":
            runner = state.get("runner") or {}
            ok = bool(runner.get("alive"))
            note = "жив" if ok else f"не отвечает (возраст квитанции {runner.get('age_s')})"
        elif name == "config":
            now = carried(info)
            keys = sorted(k for k in set(before) | set(now) if not k.startswith("__"))
            lost = [k for k in keys if before.get(k) != now.get(k)]
            same_data = (info.get("mounts") or {}).get(IN_CONTAINER_DATA) == before.get("__data")
            ok = not lost and same_data
            note = ("перенесено: " + (", ".join(k for k in keys) or "переменных не было") + "; data/ та же"
                    if ok else "разошлось: " + ", ".join(lost + ([] if same_data else ["data/"])))
        elif name == "brain":
            brain = state.get("brain") or {}
            ok = bool(brain.get("configured"))
            note = str(brain.get("model") or "") if ok else "мозг не настроен"
        out.append({"name": name, "title": control.UPDATE_CHECKS.get(name, name),
                    "ok": bool(ok), "note": note})
    return out


def check_zip(path: Path, root_name: str = "Helene") -> int:
    """Архив выпуска: один корень, никаких путей наружу и ссылок. -> размер распакованного."""
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
            total += info.file_size
    return total


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
    total = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in files:
            try:
                info = os.lstat(os.path.join(base, name))
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


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
        return cls(Path(env.get("HELENE_DIR") or IN_CONTAINER_ROOT),
                   container=(env.get("HELENE_CONTAINER") or "helene").strip(),
                   releases=(env.get("HELENE_UPDATE_RELEASES") or DEFAULT_RELEASES).strip(),
                   asset=(env.get("HELENE_UPDATE_ASSET") or "Helene").strip(),
                   keep=int(env.get("HELENE_UPDATE_KEEP") or 2),
                   insecure=(env.get("HELENE_UPDATE_INSECURE") or "") == "1")


class Updater:
    def __init__(self, cfg: Config, docker: Docker | None = None, net: Net | None = None,
                 clock=time.time, sleep=time.sleep):
        self.cfg = cfg
        self.install = cfg.install
        self.data = cfg.install / "data"
        self.home = cfg.install / ".updater"
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
        with self.lock:
            self.state["updated_utc"] = utc(self.clock())
            self.home.mkdir(parents=True, exist_ok=True)
            tmp = self.home / f".state-{secrets.token_hex(4)}.json"
            tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), "utf-8")
            os.replace(tmp, self._state_path())
            try:
                self.shared.write(*CTL, control.UPDATE_RECEIPT, self.receipt())
            except OSError as exc:
                log(f"расписка в дерево агента не записалась: {exc}")

    def receipt(self) -> dict:
        """Что видят окно и агент. Внутренние пути отката — только в моей папке."""
        keys = ("id", "state", "note", "step", "steps", "plan", "from_version", "to_version",
                "release", "backup", "extensions", "checks", "carried", "confirmed", "created_utc",
                "updated_utc", "awaiting_until_utc", "awaiting_since_epoch", "finished_utc",
                "finished_epoch", "rollback", "phase")
        out = {"schema": control.UPDATE_SCHEMA}
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

    def finish(self, state: str, note: str) -> None:
        now = self.clock()
        with self.lock:
            self.state.update(state=state, note=note, finished_utc=utc(now), finished_epoch=now)
            self.state.pop("nonce", None)
            self.busy = ""
            # «Последняя версия» в записке — сразу заново: после обновления она иначе
            # до шести часов звала бы на ту, что уже стоит.
            self.latest_at = 0.0
        log(f"итог: {state} — {note}")
        self.save()
        row = {k: self.state.get(k) for k in ("id", "state", "note", "from_version", "to_version",
                                              "finished_utc")}
        row["asked_by"] = (self.state.get("plan") or {}).get("asked_by", "")
        row["confirmed_by"] = (self.state.get("confirmed") or {}).get("by", "")
        try:
            self.shared.append(*CTL, control.UPDATE_HISTORY, row)
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
        for rel in ("helene-build.json", "app/desk.json"):
            try:
                got = json.loads((self.install / rel).read_text("utf-8")).get("version")
            except (OSError, ValueError, AttributeError):
                continue
            if control.version_tuple(got):
                return str(got)
        return ""

    def inspect(self) -> dict:
        code, out, err = self.docker.run(["inspect", self.cfg.container], timeout=60)
        if code != 0:
            raise UpdateError(f"контейнер {self.cfg.container} не найден: {(err or out).strip()[:200]}")
        try:
            rows = json.loads(out)
        except ValueError as exc:
            raise UpdateError(f"docker inspect ответил не JSON: {exc}") from exc
        if not rows:
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

    def beat(self) -> None:
        row = {"schema": SCHEMA, "pid": os.getpid(), "started_utc": self.started_utc,
               "beat_utc": utc(self.clock()), "beat_epoch": self.clock(),
               "ok": bool(self.health.get("ok")), "why": self.health.get("why", ""),
               "busy": self.busy, "install": str(self.install), "container": self.cfg.container,
               "current_version": self.current_version(), "latest": self.latest,
               "state": self.state.get("state", ""), "plan_id": self.state.get("id", "")}
        self.shared.write(*CTL, control.UPDATER_BEAT, row)

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

    # --- ход --------------------------------------------------------------------

    def tick(self) -> None:
        """Один шаг цикла: истечение, «да»/«нет», новый план."""
        st = self.state
        current = st.get("state")
        if current in ("confirmed", "running"):
            return                       # исполнение идёт синхронно; сюда — только после сбоя
        plan = self.shared.read(*CTL, control.UPDATE_PLAN)
        fresh = bool(plan.get("id")) and plan.get("id") != st.get("id")
        if current == "awaiting":
            if fresh:
                self.finish("superseded", "план заменён новым — этот не исполнялся")
            elif self.clock() > float(st.get("awaiting_until_epoch") or 0):
                self.finish("expired", f"«да» не пришло за {control.UPDATE_AWAIT_HOURS} ч — "
                                       "план истёк, ничего не тронуто")
                return
            else:
                confirm = self.shared.read(*CTL, control.UPDATE_CONFIRM)
                if (confirm.get("id") == st.get("id") and st.get("nonce")
                        and confirm.get("nonce") == st.get("nonce")):
                    self.shared.remove(*CTL, control.UPDATE_CONFIRM)
                    who = {"by": str(confirm.get("by") or "")[:40],
                           "words": str(confirm.get("words") or "")[:500],
                           "at_utc": str(confirm.get("at_utc") or "")[:40]}
                    if confirm.get("decision") == "yes":
                        self.execute(who)
                    else:
                        self.state["confirmed"] = who
                        self.finish("declined", "человек ответил «нет» — ничего не тронуто")
                return
        if fresh:
            self.consider(plan)

    def consider(self, raw: dict) -> None:
        """Новый план: сверить с выпусками и местом, показать человеку и ждать «да»."""
        now = self.clock()
        plan, why = control.validate_plan(raw)
        self.state = {"id": str(raw.get("id") or "")[:40], "plan": plan, "steps": [],
                      "created_utc": utc(now), "state": "checking"}
        if plan is None:
            self.finish("refused", f"план не принят: {why}")
            return
        self.check_self()
        if not self.health["ok"]:
            self.finish("refused", f"исполнитель не может работать: {self.health['why']}")
            return
        self.step("сверяю план с выпусками")
        current = self.current_version()
        try:
            rel = self.release_for(plan["version"])
        except UpdateError as exc:
            self.finish("refused", str(exc))
            return
        target = rel["version"]
        if current and control.version_tuple(target) <= control.version_tuple(current):
            self.finish("refused", f"стоит {current}, а {target} не новее — ставить нечего")
            return
        data_bytes = dir_size(self.data) if plan["backup"] == "full" else 0
        free = shutil.disk_usage(self.install).free
        need = max(rel["size"], 1) * 4 + data_bytes + SPACE_MARGIN
        backup = {"mode": plan["backup"], "words": control.UPDATE_BACKUPS[plan["backup"]],
                  "data_bytes": data_bytes, "free_bytes": free, "need_bytes": need}
        self.state.update(from_version=current, to_version=target, release=rel, backup=backup)
        if free < need:
            hint = (" План с копией только кода (backup: code) займёт меньше."
                    if plan["backup"] == "full" else "")
            self.finish("refused", f"мало места: нужно около {mb(need)}, свободно {mb(free)}.{hint}")
            return
        self.state.update(
            state="awaiting", nonce=secrets.token_hex(12), awaiting_since_epoch=now,
            awaiting_until_epoch=now + control.UPDATE_AWAIT_HOURS * 3600,
            awaiting_until_utc=utc(now + control.UPDATE_AWAIT_HOURS * 3600),
            note=(f"жду подтверждения: {current or '?'} → {target}; архив {mb(rel['size'])}, "
                  f"sha256 {rel['sha256'][:12]}…; копия — {backup['words']}"
                  + (f" ({mb(data_bytes)})" if data_bytes else "")))
        self.step("жду «да» человека")

    # --- исполнение -------------------------------------------------------------

    def compose(self, *args: str, file: Path | None = None, timeout: float = 600):
        info = self.health["info"]
        compose_file = file or (self.install / "server" / "docker-compose.yml")
        return self.docker.run(["compose", "-p", info["project"], "-f", str(compose_file), *args],
                               env=self.state.get("carried") or {}, timeout=timeout)

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
        st.update(state="confirmed", confirmed=who, backup_dir=str(bdir), phase="prepare",
                  swapped=[], note=f"«да» получено ({who.get('by') or '?'}) — начинаю")
        st.pop("nonce", None)
        self.save()
        self.check_self()
        try:
            if not self.health["ok"]:
                raise UpdateError(f"исполнитель не может работать: {self.health['why']}")
            info = self.health["info"]
            before = carried(info)
            before["__data"] = info["mounts"].get(IN_CONTAINER_DATA, "")
            repo, tag = image_repo(info["image"])
            st.update(state="running", carried={k: v for k, v in before.items()
                                                if not k.startswith("__")},
                      carried_before=before, image_repo=repo, image_tag=tag,
                      old_image_id=info["image_id"], service=info["service"],
                      rollback_tag=f"helene-rollback-{bdir.name}")
            self.save()
            self._prepare(bdir)
        except (UpdateError, OSError) as exc:
            left = self._undo_prepare()
            self.finish("failed", f"не начато: {exc} — агент не останавливался, "
                                  + (f"но вернулось не всё: {left}" if left else "всё как было"))
            return
        st["phase"] = "switch"
        self.save()
        try:
            self._switch(bdir)
            st["phase"] = "verify"
            self.save()
            results = self._verify(st["to_version"], plan["checks"], plan["wait_min"])
        except (UpdateError, OSError) as exc:
            self.step("подмена не удалась", ok=False, note=str(exc))
            self._rollback(str(exc))
            return
        if all(r["ok"] for r in results):
            self._success()
        else:
            bad = "; ".join(f"{r['title']}: {r['note']}" for r in results if not r["ok"])
            self._rollback(f"проверки не прошли — {bad}")

    def _prepare(self, bdir: Path) -> None:
        """Всё, что можно сделать, не трогая живого агента."""
        st = self.state
        rel = st["release"]
        downloads = self.home / "downloads"
        stage = self.home / "stage" / bdir.name
        downloads.mkdir(parents=True, exist_ok=True)
        stage.mkdir(parents=True, exist_ok=True)
        st["stage_dir"] = str(stage)
        archive = downloads / rel["asset"]
        st["archive"] = str(archive)
        self.step(f"качаю {rel['asset']} ({mb(rel['size'])})")
        got = self.net.download(rel["url"], archive, MAX_ARCHIVE)
        if got != rel["sha256"]:
            raise UpdateError(f"сумма архива не сошлась: {got[:16]}… вместо {rel['sha256'][:16]}…")
        self.step("сумма sha256 сошлась", note=got)
        unpacked = check_zip(archive)
        free = shutil.disk_usage(self.install).free
        data_bytes = dir_size(self.data) if st["plan"]["backup"] == "full" else 0
        if free < unpacked + data_bytes + SPACE_MARGIN:
            raise UpdateError(f"мало места для распаковки и копии: нужно около "
                              f"{mb(unpacked + data_bytes + SPACE_MARGIN)}, свободно {mb(free)}")
        self.step(f"распаковываю ({mb(unpacked)})")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(stage)
        root = stage / "Helene"
        passport = check_layout(root, st["to_version"])
        relay = root / "helene-relay"
        if relay.is_file():
            relay.chmod(0o755)
        st["passport"] = passport
        self.step("поставка на месте, паспорт сходится",
                  note=f"{passport['version']}, собрана {passport['built_utc']}")
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
        self.step("прежний образ отложен", note=f"{st['image_repo']}:{st['rollback_tag']}")
        self.step("собираю новый образ — это минуты")
        st["built"] = True
        self._must(self.compose("build", st["service"], file=root / "server" / "docker-compose.yml",
                                timeout=45 * 60), "образ не собрался")
        self.step("образ собран")
        self._rehearse_extensions()

    def _rehearse_extensions(self) -> None:
        st = self.state
        found = sorted(p.parent.name for p in (self.data / "extensions").glob("*/extension.json")
                       if not p.is_symlink())
        if not found:
            st["extensions"] = {"ok": True, "summary": "расширений нет"}
            return
        self.step(f"репетирую расширения под новой версией: {', '.join(found)}")
        code, out, err = self.docker.run([
            "run", "--rm", "--network", "none",
            "-v", f"{self.data}:{IN_CONTAINER_DATA}:ro",
            "-v", f"{self.install / 'helene.json'}:{IN_CONTAINER_CONFIG}:ro",
            f"{st['image_repo']}:{st['image_tag']}",
            "python", "app/localharness/runner.py", "--check-extensions",
            "--data", IN_CONTAINER_DATA, "--host-version", st["to_version"]], timeout=300)
        try:
            report = json.loads(out[out.index("{"):])
        except ValueError:
            report = {"ok": False, "summary": f"репетиция не ответила: {(err or out).strip()[-300:]}"}
        st["extensions"] = {"ok": bool(report.get("ok")), "summary": str(report.get("summary") or "")}
        if not report.get("ok") and not st["plan"].get("force_extensions"):
            raise UpdateError(f"расширения владельца не грузятся под {st['to_version']}: "
                              f"{report.get('summary')} — план с force_extensions обновит всё равно")
        self.step("расширения", ok=bool(report.get("ok")), note=str(report.get("summary") or ""))

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
        self._cleanup_stage()
        return "; ".join(problems)

    def _switch(self, bdir: Path) -> None:
        """Подмена: прежний код в копию, новый на место; остановка, копия data/, подъём."""
        st = self.state
        root = Path(st["stage_dir"]) / "Helene"
        code_dir = bdir / "code"
        code_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.install / "helene.json", bdir / "helene.json")
        self.step("откладываю прежний код и кладу новый")
        for entry in sorted(root.iterdir(), key=lambda p: p.name):
            if entry.name in KEEP_IN_PLACE:
                continue
            target = self.install / entry.name
            # Запись — ДО переноса: упади я между двумя rename, откат знает, что вернуть.
            st["swapped"].append({"name": entry.name, "had_old": target.exists() or target.is_symlink()})
            self.save()
            if target.exists() or target.is_symlink():
                os.rename(target, code_dir / entry.name)
            os.rename(entry, target)
        self._wait_idle()
        self.step("останавливаю агента")
        self._must(self.compose("stop", "-t", "60", st["service"], timeout=180), "агент не остановился")
        st["stopped"] = True
        if st["plan"]["backup"] == "full":
            self.step(f"копирую data/ ({mb(st['backup'].get('data_bytes') or 0)})")
            shutil.copytree(self.data, bdir / "data", symlinks=True, ignore=_only_plain)
            st["data_backup"] = str(bdir / "data")
            self.step("копия data/ готова")
        self.step(f"поднимаю {st['to_version']}")
        self._must(self.compose("up", "-d", "--no-build", "--force-recreate", "--no-deps",
                                st["service"], timeout=300), "новая версия не поднялась")

    def _wait_idle(self) -> None:
        """Не рвать ход агента на полуслове: ждём его конца, но не вечно."""
        deadline = self.clock() + IDLE_WAIT
        said = False
        while self.clock() < deadline:
            reader = self.shared.read(*CTL, "desk_inbox", ".reader.json")
            fresh = self.clock() - float(reader.get("at") or 0) < 60
            if not (reader.get("busy") and fresh):
                return
            if not said:
                self.step("агент в ходе — жду его конца (до 3 минут)")
                said = True
            self.sleep(3)
        self.step("агент не закончил ход за 3 минуты — останавливаю всё равно", ok=False)

    def _verify(self, version: str, checks: list[str], wait_min: int) -> list[dict]:
        st = self.state
        self.step(f"проверяю: {', '.join(checks)} (жду до {wait_min} мин)")
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
                code, out, err = self.docker.run(["exec", self.cfg.container, "python", "-c", PROBE],
                                                 timeout=60)
                try:
                    probe = json.loads(out.strip().splitlines()[-1]) if code == 0 else {}
                except (ValueError, IndexError):
                    probe = {}
            results = evaluate(probe, info, checks, version, st.get("carried_before") or {})
            st["checks"] = results
            self.save()
            if all(r["ok"] for r in results):
                self.step("проверки прошли: " + ", ".join(r["name"] for r in results))
                return results
            down = 0 if info.get("running") else down + 1
            if down >= 6:
                self.step("контейнер не держится на ногах", ok=False, note=str(info.get("status")))
                return results
            if self.clock() >= deadline:
                return results
            self.sleep(5)

    def _undo_code(self) -> list[str]:
        st = self.state
        bdir = Path(st["backup_dir"])
        failed = bdir / "failed-new"
        failed.mkdir(parents=True, exist_ok=True)
        notes = []
        for row in reversed(st.get("swapped") or []):
            name = row["name"]
            target = self.install / name
            if (target.exists() or target.is_symlink()) and not (failed / name).exists():
                os.rename(target, failed / name)
            old = bdir / "code" / name
            if row.get("had_old") and (old.exists() or old.is_symlink()):
                os.rename(old, target)
                notes.append(name)
        st["swapped"] = []
        self.save()
        return notes

    def _retag_old(self) -> None:
        st = self.state
        self._must(self.docker.run(["tag", f"{st['image_repo']}:{st['rollback_tag']}",
                                    f"{st['image_repo']}:{st['image_tag']}"], timeout=60),
                   "прежний образ не вернулся на место")

    def _restore_data(self) -> str:
        st = self.state
        bdir = Path(st["backup_dir"])
        saved, after = bdir / "data", bdir / "data-after-failed"
        if not saved.is_dir():
            return "копии data/ не делалось — данные остались как их оставила новая версия"
        try:
            if self.data.exists() and not after.exists():
                os.rename(self.data, after)
            if not self.data.exists():
                os.rename(saved, self.data)
        except OSError as exc:
            return f"data/ не возвращена ({exc}); копия до обновления — {saved}"
        return f"data/ возвращена из копии; то, что успела записать новая версия, — в {after}"

    def _rollback(self, why: str) -> None:
        st = self.state
        st["phase"] = "rollback"
        self.step("откатываю", ok=False, note=why)
        notes: list[str] = []
        if st.get("stopped") or st.get("swapped"):
            self.compose("stop", "-t", "30", st["service"], timeout=120)
        try:
            back = self._undo_code()
            notes.append("прежний код на месте" + (f" ({len(back)} частей)" if back else ""))
            saved = Path(st["backup_dir"]) / "helene.json"
            if saved.is_file() and saved.read_bytes() != (self.install / "helene.json").read_bytes():
                # Тот же inode: helene.json смонтирован в контейнер файлом.
                shutil.copyfile(saved, self.install / "helene.json")
                notes.append("helene.json возвращён")
            notes.append(self._restore_data())
            self._retag_old()
            self._must(self.compose("up", "-d", "--no-build", "--force-recreate", "--no-deps",
                                    st["service"], timeout=300), "прежняя версия не поднялась")
            checks = [c for c in control.UPDATE_MANDATORY
                      if c != "version" or st.get("from_version")]
            results = self._verify(st.get("from_version") or "", checks,
                                   min(int(st["plan"]["wait_min"]), 10))
        except (UpdateError, OSError) as exc:
            st["rollback"] = {"ok": False, "notes": notes, "why": str(exc)[:600]}
            self._cleanup_stage()
            self.finish("failed", f"обновление не прошло ({why[:300]}), и откат НЕ поднялся: {exc}. "
                                  f"Копии — {st['backup_dir']}; нужен человек на хосте")
            return
        ok = all(r["ok"] for r in results)
        st["rollback"] = {"ok": ok, "notes": notes, "checks": results}
        self._cleanup_stage()
        if ok:
            self.finish("rolled_back", f"{st.get('to_version')} не прошла ({why[:400]}) — вернул "
                                       f"{st.get('from_version') or 'прежнюю'}: " + "; ".join(notes))
        else:
            bad = "; ".join(f"{r['title']}: {r['note']}" for r in results if not r["ok"])
            self.finish("failed", f"обновление не прошло ({why[:300]}), откат поднят, но не "
                                  f"проверился: {bad}. Копии — {st['backup_dir']}")

    def _success(self) -> None:
        st = self.state
        self._cleanup_stage()
        self._retention()
        checked = ", ".join(r["name"] for r in st.get("checks") or [])
        self.finish("done", f"{st.get('from_version') or '?'} → {st['to_version']}: проверено "
                            f"{checked}. Прежняя версия отложена ({st['backup_dir']}), образ "
                            f"{st['image_repo']}:{st['rollback_tag']}")
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
        """Держу последние N копий (и их образы); старые — только свои, по имени."""
        root = self.home / "backups"
        dirs = sorted((p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{8}T\d{6}Z", p.name)),
                      key=lambda p: p.name)
        for old in dirs[:-self.cfg.keep]:
            shutil.rmtree(old, ignore_errors=True)
            self.docker.run(["rmi", f"{self.state['image_repo']}:helene-rollback-{old.name}"], timeout=120)
            log(f"старая копия убрана: {old.name}")

    def recover(self) -> None:
        """Старт после сбоя посреди обновления: довести или откатить, но не бросить."""
        st = self.state
        if st.get("state") == "confirmed":
            self.finish("failed", "исполнитель перезапустился сразу после «да» — ничего не тронуто; "
                                  "положи план снова")
            return
        if st.get("state") != "running":
            return
        self.check_self()
        phase = st.get("phase")
        log(f"после сбоя: обновление {st.get('id')} было на шаге «{st.get('step')}» ({phase})")
        if phase == "prepare":
            left = self._undo_prepare()
            self.finish("failed", "исполнитель перезапустился посреди подготовки; агент не "
                                  "останавливался, " + (f"но вернулось не всё: {left}" if left
                                                        else "всё возвращено как было"))
            return
        if phase in ("switch", "verify"):
            if not st.get("stopped"):
                # Агента я ещё не останавливал: он работает прежним образом, и вернуть
                # надо только папки и тег — без перезапуска его контейнера.
                left = self._undo_prepare()
                self.finish("failed", "исполнитель перезапустился посреди подмены папок, агента "
                                      "не останавливал — " + (f"вернулось не всё: {left}" if left
                                                              else "прежний код и образ на месте"))
                return
            results = self._verify(st["to_version"], st["plan"]["checks"], 5)
            if all(r["ok"] for r in results):
                self._success()
                return
            self._rollback("исполнитель перезапустился посреди подмены, новая версия не проверилась")
            return
        self._rollback("исполнитель перезапустился посреди отката — довожу откат")


def main() -> int:
    cfg = Config.from_env()
    updater = Updater(cfg)
    log(f"исполнитель обновлений: установка {cfg.install}, контейнер {cfg.container}, "
        f"выпуски {cfg.releases}")
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
    updater.recover()
    last_self = time.monotonic()
    while True:
        try:
            if time.monotonic() - last_self > 60:
                updater.check_self()
                last_self = time.monotonic()
            updater.refresh_latest()
            updater.tick()
        except Exception as exc:  # noqa: BLE001 — один сбой тика не должен ронять исполнителя
            log(f"тик упал: {type(exc).__name__}: {exc}")
        if updater.reexec:
            log("перезапускаюсь новым кодом")
            os.execv(sys.executable, [sys.executable, str(cfg.install / "server" / "updater" / "updater.py")])
        time.sleep(3)


if __name__ == "__main__":
    sys.exit(main())
