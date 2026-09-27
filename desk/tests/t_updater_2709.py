# -*- coding: utf-8 -*-
"""Стенд исполнителя обновлений на сервере (27.09): `server/updater/updater.py`.

Запуск:  python tests/t_updater_2709.py
Весь ход исполнителя — только на Linux (O_NOFOLLOW, dir_fd); чистые разборы, перенос
правок агента и сверка копии протокола — и на Windows. Слияние правок требует `git`.

Докер и сеть подменены целиком: докер — объектом, который помнит контейнер, образы и
теги, монтирования кода и жизнь контейнера и отвечает на те же команды (`inspect`,
`compose build/stop/up`, `tag`, `exec`, `run`); сеть — ответами GitHub Releases и
архивами, собранными здесь же. Файлы — настоящие: папка установки, `data/`, `tree/`,
`app/`, копии, распаковка. Проверяется то, что может соврать:
  * без «да» с ключом расписки ничего не начинается; чужой ключ — не «да»;
  * счастливый путь: новый код на месте, прежний и data/ — в копии, helene.json не тронут,
    переменные прежнего контейнера перенесены, образ отложен тегом отката, runtime/ не тронут;
  * ПРАВКИ АГЕНТА в его коде: видны до «да»; не тронутое выпуском — переезжает как есть,
    тронутое обоими — сливается, не слившееся — вариант выпуска + материалы агенту;
    нет чистой базы — прежний код целиком агенту;
  * ИСПЫТАНИЕ: «принимаю» закрывает, «сломано» и молчание — откат, занятому агенту срок
    продлевается, но не больше чем вдвое; без мозга испытание пропускается словами;
  * ОТКАТ не трогает память агента; копия data/ — только если прежняя версия на новых
    данных не поднимается;
  * сборка упала, сумма не сошлась, расширения не грузятся — живой агент не тронут;
  * квитанции прежнего контейнера — не живость нового;
  * подложенная агентом ссылка или FIFO не уводят исполнителя наружу;
  * копия протокола у исполнителя не разъехалась с оригиналом и не импортирует код агента.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / "server" / "updater")]
from deskd import control  # noqa: E402
import updater as up  # noqa: E402

LINUX = os.name == "posix" and hasattr(os, "O_NOFOLLOW")
GIT = shutil.which("git") is not None
OLD, NEW = "1.1.1", "1.1.2"


def agent_py(version: str, *, hello: str = "hi", helper: str = "1") -> bytes:
    """`tree/agent.py` в миниатюре: версия сверху, две функции далеко друг от друга —
    чтобы правка агента в одной и правка выпуска в другой сливались чисто."""
    return (f"# agent\nVERSION = {version!r}\n\n\n"
            f"def hello():\n    return {hello!r}\n\n\n"
            "def middle_one():\n    return 'm1'\n\n\n"
            "def middle_two():\n    return 'm2'\n\n\n"
            f"def helper():\n    return {helper}\n").encode()


def dist_files(version: str, **agent) -> dict[str, bytes]:
    """Поставка в миниатюре: всё, что исполнитель требует от распакованного."""
    return {
        "helene-build.json": json.dumps({"product": "Hélène", "version": version,
                                         "complete": True, "built_utc": "2026-09-27T10:00:00Z",
                                         "git": {"desk": "abc"}}).encode(),
        "requirements.txt": b"aiohttp\n",
        "app/deskapp.py": b"# channel\n",
        "app/desk.json": json.dumps({"product": "desk", "version": version}).encode(),
        "app/localharness/runner.py": b"# runner\n",
        "app/deskd/control.py": b"# control\n",
        "tree/agent.py": agent_py(version, **agent),
        "tree/memory_life.py": b"# memory\nKEEP = 3\n",
        "server/Dockerfile": b"FROM python:3.12-slim\n",
        "server/docker-compose.yml": b"name: helene\nservices:\n  helene: {}\n",
        "server/serverboot.py": b"# serverboot\n",
        "server/updater/updater.py": f"# updater {version}\n".encode(),
        "server/updater/protocol.py": b"# protocol\n",
        "runtime/python.exe": f"windows python for {version}".encode(),
        "helene.json": b'{"mode": "local"}\n',
    }


def make_zip(version: str, extra: dict[str, bytes] | None = None, root: str = "Helene",
             **agent) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for rel, blob in {**dist_files(version, **agent), **(extra or {})}.items():
            zf.writestr(f"{root}/{rel}", blob)
        zf.writestr(f"{root}/data/", b"")
    return buf.getvalue()


class FakeNet:
    def __init__(self, releases: str):
        self.releases = releases
        self.pages: dict[str, dict] = {}
        self.texts: dict[str, str] = {}
        self.blobs: dict[str, bytes] = {}
        self.downloads: list[str] = []

    def publish(self, version: str, blob: bytes, *, digest: bool = True, latest: bool = True):
        name = f"Helene-{version}.zip"
        url = f"https://example.test/{name}"
        self.blobs[url] = blob
        sha = hashlib.sha256(blob).hexdigest()
        assets = [{"name": name, "browser_download_url": url, "size": len(blob),
                   **({"digest": "sha256:" + sha} if digest else {})},
                  {"name": name + ".sha256", "browser_download_url": url + ".sha256", "size": 83}]
        self.texts[url + ".sha256"] = f"{sha}  {name}\n"
        rel = {"tag_name": f"v{version}", "assets": assets, "body": "# Изменения\nЧинит голос",
               "html_url": "https://example.test/rel"}
        self.pages[f"{self.releases}/tags/v{version}"] = rel
        if latest:
            self.pages[f"{self.releases}/latest"] = rel
        return sha

    def json(self, url):
        if url not in self.pages:
            raise up.UpdateError(f"{url} ответил 404")
        return self.pages[url]

    def text(self, url, limit=4096):
        return self.texts[url]

    def download(self, url, dest, limit, progress=None):
        self.downloads.append(url)
        blob = self.blobs[url]
        Path(dest).write_bytes(blob)
        return hashlib.sha256(blob).hexdigest()


class FakeDocker:
    """Демон в миниатюре: один контейнер агента, образы по тегам, ответы на команды."""

    def __init__(self, install: Path):
        self.install = install
        self.calls: list[list[str]] = []
        self.tags = {"helene-helene:latest": "sha256:old"}
        self.image = "sha256:old"
        self.running = True
        self.restarts = 0
        self.env = {"HELENE_HOSTS": "helene.example.com", "HELENE_PUBLIC_URL": "https://helene.example.com",
                    "PRAXIS_STT_CPU_THREADS": "3", "PRAXIS_STT_KEEP_LOADED": "0", "PATH": "/usr/bin"}
        self.port = "8094"
        self.models = "/opt/praxis-models"
        self.working_dir = str(install / "server")
        self.fail_build = False
        self.alive = {"sha256:old": True, "sha256:new": True}
        self.versions = {"sha256:old": OLD, "sha256:new": NEW}
        self.extensions_report = {"ok": True, "summary": "все 1 расширений грузятся"}
        self.falls = {"sha256:old": 0, "sha256:new": 0}   # падения раннера в записке надзора
        self.brain = False                                 # мозг настроен? (испытание — только с ним)
        #: Код с диска (compose с 27.09) — у какого образа какой compose.
        self.code_mounts = {"sha256:old": True, "sha256:new": True}
        #: Прежняя версия не поднимается на данных, которые тронула новая (метка в data/).
        self.data_breaks_old = False
        self.on_up = None           # что «новая версия» делает с data/ при подъёме
        # Жизнь контейнера. Квитанция раннера и записка надзора лежат в data/ и ПЕРЕЖИВАЮТ
        # замену контейнера: мёртвый новый раннер оставляет свежей квитанцию прежнего —
        # живой прогон 27.09 на этом и поймал «агент жив» у сломанного выпуска.
        self.started = 1_000_000.0
        self.reader_at = self.started + 2
        self.sup_started = self.started
        self.sup_falls = 0
        self.channel_says_alive = None     # канал верит свежей чужой квитанции (как в жизни)
        # Ревью 27.09: подделка не должна быть добрее демона.
        self.missing = False               # контейнера нет (владелец сделал rm; убит посреди пересоздания)
        self.fail_up = 0                   # столько следующих `compose up` падают
        self.images = {"sha256:old", "sha256:new"}
        self.removed: list[str] = []       # что снято rmi
        self.config_files = [self.working_dir + "/docker-compose.yml"]
        self.garbage = {}                  # образ -> что его «python» печатает вместо пробы
        # Установка до 27.09: код агента жил в самом контейнере — в образе и слое поверх него.
        self.fs: dict[str, Path] = {}      # образ -> его /opt/helene (tree/, app/)
        self.layer: Path | None = None     # /opt/helene живого контейнера: образ + правки агента
        self.created: dict[str, str] = {}  # временные контейнеры: имя -> образ
        self.fail_commit = False

    def _layer_diff(self) -> str:
        """`docker diff`: чем слой контейнера отличается от его образа (папки — строкой «C»)."""
        base = self.fs.get(self.image)
        if self.layer is None or base is None:
            return ""
        rows = []
        for name in up.CODE_DIRS:
            mine = {p.relative_to(self.layer).as_posix(): p.read_bytes()
                    for p in (self.layer / name).rglob("*") if p.is_file()}
            was = {p.relative_to(base).as_posix(): p.read_bytes()
                   for p in (base / name).rglob("*") if p.is_file()}
            touched = False
            for rel in sorted(set(mine) | set(was)):
                kind = "A" if rel not in was else "D" if rel not in mine else "C" if mine[rel] != was[rel] else ""
                if kind:
                    rows.append(f"{kind} /opt/helene/{rel}")
                    touched = True
            if touched:
                rows.insert(0, f"C /opt/helene/{name}")
        return "\n".join(rows) + "\n"

    def _box_root(self, box: str) -> Path:
        if box == "helene":
            if self.layer is None:
                raise FileNotFoundError("у контейнера нет своего кода")
            return self.layer
        return self.fs[self.created[box]]

    def _alive_now(self) -> bool:
        if (self.data_breaks_old and self.image == "sha256:old"
                and (self.install / "data" / "memory" / "migrated.flag").exists()):
            return False
        return self.alive[self.image]

    def _start(self):
        self.started += 100
        alive = self._alive_now()
        if alive:
            self.reader_at = self.started + 2
        # надзор нового контейнера пишет свою записку всегда — он жив, даже когда раннер нет
        self.sup_started = self.started
        self.sup_falls = self.falls[self.image] if alive or not self.data_breaks_old else 3

    @staticmethod
    def _iso(epoch: float) -> str:
        return up.utc(epoch).replace("Z", ".123456789Z")

    def row(self) -> dict:
        labels = {"com.docker.compose.project": "helene",
                  "com.docker.compose.service": "helene",
                  "com.docker.compose.project.working_dir": self.working_dir,
                  "com.docker.compose.project.config_files": ",".join(self.config_files)}
        mounts = [{"Destination": "/opt/helene/data", "Source": str(self.install / "data")},
                  {"Destination": "/opt/helene/helene.json", "Source": str(self.install / "helene.json")},
                  {"Destination": "/models", "Source": self.models}]
        if self.code_mounts.get(self.image, True):
            mounts += [{"Destination": "/opt/helene/tree", "Source": str(self.install / "tree")},
                       {"Destination": "/opt/helene/app", "Source": str(self.install / "app")}]
        return {"Name": "/helene", "Image": self.image, "RestartCount": self.restarts,
                "Config": {"Image": "helene-helene", "Labels": labels,
                           "Env": [f"{k}={v}" for k, v in self.env.items()]},
                "HostConfig": {"PortBindings": {"8094/tcp": [{"HostIp": "127.0.0.1", "HostPort": self.port}]}},
                "Mounts": mounts,
                "State": {"Running": self.running, "Restarting": False,
                          "Status": "running" if self.running else "exited",
                          "StartedAt": self._iso(self.started)}}

    def run(self, args, *, env=None, timeout=600, limit=None):
        self.calls.append(list(args))
        env = env or {}
        if args[0] == "inspect":
            if self.missing:
                return 1, "[]", "Error: No such object: helene"
            if "--size" in args:
                size = sum(p.stat().st_size for p in self.layer.rglob("*") if p.is_file()) if self.layer else 0
                return 0, f"{size}\n", ""
            return 0, json.dumps([self.row()]), ""
        if args[0] == "diff":
            return 0, self._layer_diff(), ""
        if args[0] == "commit":
            if self.fail_commit:
                return 1, "", "Error response from daemon: commit failed"
            snap = self.install.parent / f"commit-{len(self.images)}"
            shutil.copytree(self.layer, snap)
            ident = f"sha256:commit{len(self.images)}"
            self.images.add(ident)
            self.fs[ident] = snap
            for table in (self.alive, self.versions, self.falls, self.code_mounts):
                table[ident] = table[self.image]
            self.tags[args[-1]] = ident
            return 0, ident + "\n", ""
        if args[0] == "create":
            self.created[args[args.index("--name") + 1]] = args[-1]
            return 0, "", ""
        if args[0] == "cp":
            box, _, inner = args[1].partition(":")
            src = self._box_root(box) / inner.removeprefix("/opt/helene/")
            shutil.copytree(src, args[2], symlinks=True)
            return 0, "", ""
        if args[:2] == ["image", "inspect"]:
            ref = args[-1]
            return (0, self.tags[ref] + "\n", "") if ref in self.tags else (1, "", "No such image")
        if args[0] == "tag":
            src, dst = args[1], args[2]
            if src in self.tags:
                self.tags[dst] = self.tags[src]
            elif src in self.images:
                self.tags[dst] = src
            else:
                return 1, "", f"Error response from daemon: No such image: {src}"
            return 0, "", ""
        if args[0] == "rmi":
            ref = args[1]
            if ref in self.tags:
                self.tags.pop(ref)
            elif ref in self.images:
                if not self.missing and self.image == ref:
                    return 1, "", "conflict: image is being used by running container"
                for name in [n for n, v in self.tags.items() if v == ref]:
                    self.tags.pop(name)
                self.images.discard(ref)
            else:
                return 1, "", f"No such image: {ref}"
            self.removed.append(ref)
            return 0, "", ""
        if args[0] == "rm":
            self.created.pop(args[-1], None)
            return 0, "", ""
        if args[0] == "exec":
            if self.image in self.garbage:
                return 0, self.garbage[self.image], ""
            now = self.started + 5
            alive = self._alive_now()
            said = alive if self.channel_says_alive is None else self.channel_says_alive
            probe = {"now": now,
                     "health": {"code": 200, "body": {}},
                     "state": {"code": 200, "body": {
                         "desk": {"version": self.versions[self.image]},
                         "runner": {"alive": said, "age_s": round(now - self.reader_at)},
                         "brain": {"configured": self.brain, "model": "glm" if self.brain else ""}}},
                     "supervisor": {"kind": "serverboot", "beat_epoch": now - 2,
                                    "started_utc": up.utc(self.sup_started), "children": [
                                        {"id": "runner", "alive": alive,
                                         "falls": self.sup_falls, "halted": ""}]},
                     "reader": {"at": self.reader_at, "pid": 8, "busy": False}}
            return 0, "ignored line\n" + json.dumps(probe), ""
        if args[0] == "run":
            return (0 if self.extensions_report.get("ok") else 2), json.dumps(self.extensions_report), ""
        if args[0] == "compose":
            if "config" in args:
                return 0, "helene\n", ""
            if "build" in args:
                if self.fail_build:
                    return 1, "", "pip: resolution failed"
                self.tags["helene-helene:latest"] = "sha256:new"
                return 0, "built", ""
            if "stop" in args:
                self.running = False
                return 0, "", ""
            if "up" in args:
                if self.fail_up > 0:
                    self.fail_up -= 1
                    return 1, "", "Error response from daemon: port is already allocated"
                self.missing = False
                image = self.tags["helene-helene:latest"]
                if "--force-recreate" in args or image != self.image:
                    # пересоздание: слой прежнего контейнера пропадает, новый — из образа
                    fresh = self.fs.get(image)
                    self.layer = None
                    if fresh is not None:
                        self.layer = self.install.parent / f"layer-{len(self.calls)}"
                        shutil.copytree(fresh, self.layer)
                self.image = image
                self.running = True
                self.restarts = 0
                self.env = {"HELENE_HOSTS": env.get("HELENE_HOSTS", ""),
                            "HELENE_PUBLIC_URL": env.get("HELENE_PUBLIC_URL", ""),
                            "PRAXIS_STT_CPU_THREADS": env.get("HELENE_STT_THREADS", "3"),
                            "PRAXIS_STT_KEEP_LOADED": env.get("HELENE_STT_KEEP_LOADED", "0")}
                self.port = env.get("HELENE_PORT", "8094")
                self.models = env.get("HELENE_MODELS", "./models")
                if self.on_up:
                    self.on_up(self.image)
                self._start()
                return 0, "", ""
        return 0, "", ""

    def did(self, *words) -> bool:
        return any(all(w in call for w in words) for call in self.calls)


class Base(unittest.TestCase):
    """Установка 1.1.1 на диске, выпуски 1.1.1 и 1.1.2 на «GitHub», агент жив."""

    new_agent: dict = {}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="helene-updater-")
        self.addCleanup(self._tmp.cleanup)
        self.install = Path(self._tmp.name) / "opt" / "helene"
        for rel, blob in dist_files(OLD).items():
            if rel == "helene.json":
                continue
            path = self.install / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        (self.install / "helene.json").write_text('{"port": 8094, "model": {"key": "sk-secret"}}',
                                                  encoding="utf-8")
        memory = self.install / "data" / "memory"
        (memory / "journal").mkdir(parents=True)
        (memory / "journal" / "2026-09-27.md").write_text("- 10:00 жива\n", encoding="utf-8")
        (self.install / "data" / "workspace").mkdir()
        self.cfg = up.Config(self.install, releases="https://api.example.test/releases")
        self.net = FakeNet(self.cfg.releases)
        self.net.publish(OLD, make_zip(OLD), latest=False)
        self.sha = self.net.publish(NEW, make_zip(NEW, **self.new_agent))
        self.docker = FakeDocker(self.install)
        # часы исполнителя — настоящие по величине: «давний» итог записки не получает
        self.now = [time.time()]
        self.u = up.Updater(self.cfg, docker=self.docker, net=self.net,
                            clock=lambda: self.now[0], sleep=self.tick_clock)

    def tick_clock(self, seconds):
        self.now[0] += seconds

    def again(self) -> up.Updater:
        """Тот же исполнитель после перезапуска: состояние — с диска."""
        return up.Updater(self.cfg, docker=self.docker, net=self.net,
                          clock=lambda: self.now[0], sleep=self.tick_clock)

    def plan(self, **fields) -> str:
        """Положить план так, как это делает канал или рука (через протокол)."""
        up.Shared(self.install / "data").write(*up.CTL, control.UPDATER_BEAT,
                                              {"beat_epoch": time.time(), "ok": True})
        got = control.update_plan(self.install / "data", **fields)
        self.assertTrue(got["ok"], got)
        return got["plan"]["id"]

    def receipt(self) -> dict:
        return json.loads((self.install / "data" / "memory" / ".control" /
                           control.UPDATE_RECEIPT).read_text("utf-8"))

    def yes(self, decision="yes"):
        r = self.receipt()
        got = control.update_confirm(self.install / "data", r["id"], r["nonce"], decision,
                                     by="window", words="да")
        self.assertTrue(got["ok"], got)

    def verdict(self, word: str, by: str = "agent", words: str = "проверено: думаю, помню, руки живы"):
        r = self.receipt()
        got = control.update_verdict(self.install / "data", r["id"], r["trial"]["key"], word,
                                     by=by, words=words)
        self.assertTrue(got["ok"], got)

    def run_update(self, **plan):
        self.plan(version=NEW, **plan)
        self.u.tick()
        self.yes()
        self.u.tick()
        return self.receipt()

    def code(self, rel: str) -> str:
        return (self.install / rel).read_text("utf-8")

    def edit_agent(self, rel: str, blob: bytes | None):
        """Агент правит свой код — на диске сервера (tree/ и app/ смонтированы)."""
        path = self.install / rel
        if blob is None:
            path.unlink()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)

    def old_style(self, edits: dict[str, bytes | None] | None = None):
        """Установка до 27.09 (как у Дмитрия): код агента — в образе, его правки — в слое
        контейнера, на диске сервера — нетронутая поставка. `edits`: путь -> содержимое в
        слое (None — агент файл удалил)."""
        self.docker.code_mounts["sha256:old"] = False
        image = Path(self._tmp.name) / "image-old"
        for rel, blob in dist_files(OLD).items():
            if rel.startswith(("tree/", "app/")):
                (image / rel).parent.mkdir(parents=True, exist_ok=True)
                (image / rel).write_bytes(blob)
        self.docker.fs["sha256:old"] = image
        self.docker.layer = Path(self._tmp.name) / "layer-old"
        shutil.copytree(image, self.docker.layer)
        for rel, blob in (edits or {}).items():
            path = self.docker.layer / rel
            if blob is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(blob)


@unittest.skipUnless(LINUX, "исполнитель живёт на Linux: O_NOFOLLOW и dir_fd")
class UpdaterFlow(Base):
    # --- путь до «да» -------------------------------------------------------

    def test_план_сверяется_и_ждёт_да_без_единого_касания(self):
        self.plan(version=NEW, reason="починка голоса")
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "awaiting", r.get("note"))
        self.assertEqual((r["from_version"], r["to_version"]), (OLD, NEW))
        self.assertEqual(r["release"]["sha256"], self.sha)
        self.assertTrue(r["nonce"])
        self.assertGreater(r["backup"]["data_bytes"], 0)
        self.assertIn("жду подтверждения", r["note"])
        self.assertEqual(r["code_preview"]["edited"], 0)
        # новый архив не качался, ничего не собиралось и не останавливалось; скачан
        # только исходник стоящей версии — база для правок агента
        self.assertEqual(self.net.downloads, [f"https://example.test/Helene-{OLD}.zip"])
        self.assertFalse(self.docker.did("build"))
        self.assertFalse(self.docker.did("stop"))
        self.u.tick()
        self.assertEqual(self.receipt()["state"], "awaiting")

    def test_чужой_ключ_не_да(self):
        self.plan(version=NEW)
        self.u.tick()
        r = self.receipt()
        up.Shared(self.install / "data").write(*up.CTL, control.UPDATE_CONFIRM,
                                              {"id": r["id"], "nonce": "подобран", "decision": "yes"})
        self.u.tick()
        self.assertEqual(self.receipt()["state"], "awaiting")
        self.assertFalse(self.docker.did("build"))

    def test_счастливый_путь(self):
        (self.install / "data" / "extensions" / "quota").mkdir(parents=True)
        (self.install / "data" / "extensions" / "quota" / "extension.json").write_text("{}")
        junk = self.install / ".updater" / "pristine" / ".tmp-1.1.0-dead"
        junk.mkdir(parents=True)                       # остаток прерванной распаковки
        self.plan(version="latest")
        self.u.tick()
        self.yes()
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "done", r.get("note"))
        self.assertIn("Испытание агентом пропущено: мозг не настроен", r["note"])
        self.assertNotIn("nonce", r)
        desk = json.loads(self.code("app/desk.json"))
        self.assertEqual(desk["version"], NEW)
        bdir = Path(self.u.state["backup_dir"])
        old = json.loads((bdir / "code" / "app" / "desk.json").read_text("utf-8"))
        self.assertEqual(old["version"], OLD)
        self.assertTrue((bdir / "data" / "memory" / "journal" / "2026-09-27.md").is_file())
        self.assertIn("sk-secret", self.code("helene.json"))
        self.assertFalse((bdir / "code" / "helene.json").exists())
        # runtime/ для Windows не распаковывался и не трогался
        self.assertEqual(self.code("runtime/python.exe"), f"windows python for {OLD}")
        self.assertFalse((bdir / "code" / "runtime").exists())
        # переменные прежнего контейнера — у нового; код — с диска
        self.assertEqual(self.docker.env["HELENE_HOSTS"], "helene.example.com")
        self.assertEqual(self.docker.models, "/opt/praxis-models")
        checks = {c["name"]: c for c in r["checks"]}
        self.assertEqual(set(checks), set(control.UPDATE_MANDATORY))
        self.assertTrue(all(c["ok"] for c in r["checks"]), r["checks"])
        self.assertIn(f"helene-helene:{self.u.state['rollback_tag']}", self.docker.tags)
        self.assertTrue(self.docker.did("run", "--check-extensions"))
        # репетиция исполняет код расширений агента — заперта и убирается по имени
        rehearsal = next(c for c in self.docker.calls if c[0] == "run")
        for flag in ("--read-only", "--cap-drop", "--pids-limit", "--memory", "no-new-privileges"):
            self.assertIn(flag, rehearsal)
        self.assertEqual(rehearsal[rehearsal.index("--network") + 1], "none")
        name = rehearsal[rehearsal.index("--name") + 1]
        self.assertTrue(self.docker.did("rm", "-f", name))
        self.assertFalse(junk.exists())
        self.assertEqual(r.get("owner_files"), [])
        build = next(i for i, c in enumerate(self.docker.calls) if "build" in c)
        stop = next(i for i, c in enumerate(self.docker.calls) if "stop" in c)
        self.assertLess(build, stop)
        self.assertIn("stage", " ".join(self.docker.calls[build]))
        # чистые исходники обеих версий запомнены — база следующего обновления
        self.assertIsNotNone(self.u.pristine(NEW))
        self.assertIsNotNone(self.u.pristine(OLD))
        self.assertEqual((self.u.pristine(NEW) / "tree" / "agent.py").read_bytes(), agent_py(NEW))
        self.assertFalse(Path(self.u.state["stage_dir"]).exists())
        self.assertTrue(self.u.reexec)
        self.assertEqual(r["agent_code"]["edited"], [])
        hist = control.update_history(self.install / "data")
        self.assertEqual((hist[-1]["state"], hist[-1]["confirmed_by"]), ("done", "window"))

    # --- правки агента в его коде -------------------------------------------

    def test_правки_агента_видны_до_да(self):
        self.edit_agent("tree/agent.py", agent_py(OLD, hello="привет от Йоно"))
        self.edit_agent("tree/yono_tool.py", b"def tool():\n    return 42\n")
        self.plan(version=NEW)
        self.u.tick()
        preview = self.receipt()["code_preview"]
        self.assertEqual(preview["edited"], 2)
        self.assertEqual(sorted(preview["files"]), ["tree/agent.py", "tree/yono_tool.py"])
        self.assertIn("агент правил свой код", self.receipt()["note"])

    def test_правки_агента_переезжают_и_сливаются(self):
        if not GIT:
            self.skipTest("нет git — слияние не проверить")
        self.edit_agent("tree/agent.py", agent_py(OLD, hello="привет от Йоно"))      # выпуск меняет VERSION
        self.edit_agent("tree/memory_life.py", "# memory\nKEEP = 7  # Йоно\n".encode())      # выпуск не трогал
        self.edit_agent("tree/yono_tool.py", b"def tool():\n    return 42\n")        # добавил
        self.edit_agent("app/deskd/control.py", None)                                 # удалил (выпуск не трогал)
        (self.install / "tree" / "__pycache__").mkdir()
        (self.install / "tree" / "__pycache__" / "agent.cpython-312.pyc").write_bytes(b"\x00junk")
        r = self.run_update()
        self.assertEqual(r["state"], "done", r.get("note"))
        merged = self.code("tree/agent.py")
        self.assertIn(f"VERSION = {NEW!r}", merged)          # правка выпуска
        self.assertIn("привет от Йоно", merged)              # правка агента
        self.assertEqual(self.code("tree/memory_life.py"), "# memory\nKEEP = 7  # Йоно\n")
        self.assertEqual(self.code("tree/yono_tool.py"), "def tool():\n    return 42\n")
        self.assertFalse((self.install / "app" / "deskd" / "control.py").exists())
        code = r["agent_code"]
        self.assertEqual(code["merged"], ["tree/agent.py"])
        self.assertEqual(sorted(code["carried"]), ["app/deskd/control.py", "tree/memory_life.py",
                                                   "tree/yono_tool.py"])
        self.assertEqual(code["conflicts"], [])
        self.assertNotIn("tree/__pycache__/agent.cpython-312.pyc", code["edited"])
        folder = self.install / "data" / code["folder"]
        self.assertTrue((folder / "README.md").is_file())
        diff = (folder / "edits.diff").read_text("utf-8")
        self.assertIn("+    return 'привет от Йоно'", diff)
        self.assertIn("Правок агента в коде: 4", r["note"])
        # база нетронута: чистый исходник новой версии — без правок агента
        self.assertEqual((self.u.pristine(NEW) / "tree" / "agent.py").read_bytes(), agent_py(NEW))

    def test_правки_агента_на_откате_возвращаются_как_были(self):
        self.edit_agent("tree/memory_life.py", "# memory\nKEEP = 7  # Йоно\n".encode())
        self.docker.alive["sha256:new"] = False
        self.docker.falls["sha256:new"] = 3
        r = self.run_update()
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertEqual(self.code("tree/memory_life.py"), "# memory\nKEEP = 7  # Йоно\n")
        self.assertIn(f"VERSION = {OLD!r}", self.code("tree/agent.py"))
        self.assertIn("правки агента в коде — как были до обновления", r["note"])


class ConflictBase(Base):
    new_agent = {"helper": "3"}          # выпуск меняет helper()


@unittest.skipUnless(LINUX, "исполнитель живёт на Linux: O_NOFOLLOW и dir_fd")
class AgentCodeConflicts(ConflictBase):
    def test_не_слившееся_уходит_агенту_материалом(self):
        if not GIT:
            self.skipTest("нет git — слияние не проверить")
        self.edit_agent("tree/agent.py", agent_py(OLD, helper="2"))      # агент тоже меняет helper()
        r = self.run_update()
        self.assertEqual(r["state"], "done", r.get("note"))
        self.assertEqual(self.code("tree/agent.py").encode(), agent_py(NEW, helper="3"))   # вариант выпуска
        code = r["agent_code"]
        self.assertEqual([c["path"] for c in code["conflicts"]], ["tree/agent.py"])
        self.assertIn("одни и те же строки", code["conflicts"][0]["why"])
        folder = self.install / "data" / code["folder"]
        self.assertEqual((folder / "tree" / "agent.py.mine").read_bytes(), agent_py(OLD, helper="2"))
        self.assertEqual((folder / "tree" / "agent.py.base").read_bytes(), agent_py(OLD))
        self.assertEqual((folder / "tree" / "agent.py.theirs").read_bytes(), agent_py(NEW, helper="3"))
        marked = (folder / "tree" / "agent.py.merged").read_text("utf-8")
        self.assertIn("<<<<<<<", marked)
        self.assertIn("агент (1.1.1)", marked)
        readme = (folder / "README.md").read_text("utf-8")
        self.assertIn("НЕ легло", readme)
        self.assertIn("`tree/agent.py`", readme)
        self.assertIn("не легло 1", r["note"])

    def test_нет_чистой_базы_прежний_код_агенту_целиком(self):
        del self.net.pages[f"{self.cfg.releases}/tags/v{OLD}"]
        self.edit_agent("tree/yono_tool.py", b"def tool():\n    return 42\n")
        self.plan(version=NEW)
        self.u.tick()
        self.assertIn("сравнить не с чем", self.receipt()["note"])
        self.yes()
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "done", r.get("note"))
        code = r["agent_code"]
        self.assertTrue(code["no_base"])
        folder = self.install / "data" / code["folder"]
        self.assertEqual((folder / "old-code" / "tree" / "yono_tool.py").read_text("utf-8"),
                         "def tool():\n    return 42\n")
        self.assertIn("не с чем", (folder / "README.md").read_text("utf-8"))
        self.assertFalse((self.install / "tree" / "yono_tool.py").exists())   # не переносил вслепую

    def test_папка_материалов_занята_берётся_новая(self):
        if not GIT:
            self.skipTest("нет git — слияние не проверить")
        (self.install / "data" / "workspace" / f"update-{NEW}").mkdir()
        self.edit_agent("tree/agent.py", agent_py(OLD, helper="2"))
        r = self.run_update()
        self.assertRegex(r["agent_code"]["folder"], rf"workspace/update-{re.escape(NEW)}-\d{{8}}T")

    def test_workspace_ссылкой_не_валит_обновление(self):
        if not GIT:
            self.skipTest("нет git — слияние не проверить")
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        shutil.rmtree(self.install / "data" / "workspace")
        os.symlink(outside, self.install / "data" / "workspace")
        self.edit_agent("tree/agent.py", agent_py(OLD, helper="2"))
        r = self.run_update()
        self.assertEqual(r["state"], "done", r.get("note"))
        self.assertIn("материалы не легли", r["agent_code"]["materials_error"])
        self.assertEqual(list(outside.iterdir()), [])


@unittest.skipUnless(LINUX, "исполнитель живёт на Linux: O_NOFOLLOW и dir_fd")
class Trial(Base):
    def setUp(self):
        super().setUp()
        self.docker.brain = True

    def test_испытание_и_слово_агента_принимаю(self):
        r = self.run_update()
        self.assertEqual(r["state"], "trial", r.get("note"))
        self.assertTrue(r["trial"]["key"])
        self.assertEqual(r["trial"]["minutes"], control.UPDATE_TRIAL_DEFAULT)
        self.assertIn("агент проверяет себя", r["note"])
        self.assertFalse(self.u.reexec)
        # чужой ключ — не слово
        up.Shared(self.install / "data").write(*up.CTL, control.UPDATE_VERDICT,
                                              {"id": r["id"], "key": "чужой", "verdict": "accept"})
        self.u.tick()
        self.assertEqual(self.receipt()["state"], "trial")
        self.verdict("accept")
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "done", r.get("note"))
        self.assertIn("Агент принял на испытании", r["note"])
        self.assertEqual(r["trial"]["verdict"]["by"], "agent")
        self.assertTrue(self.u.reexec)
        # об итоге, принятом самим агентом, записки второй раз не будет
        self.assertIsNone(control.update_unreported(self.install / "data"))

    def test_сломано_откат_память_цела(self):
        self.run_update()
        life = self.install / "data" / "memory" / "journal" / "2026-09-27-trial.md"
        life.write_text("- 10:30 испытание: владелец спросил, как я\n", encoding="utf-8")
        self.verdict("reject", words="не вызывается рука shell")
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertIn("не вызывается рука shell", r["note"])
        self.assertIn(f"VERSION = {OLD!r}", self.code("tree/agent.py"))
        self.assertEqual(self.docker.image, "sha256:old")
        self.assertTrue(life.is_file(), "откат стёр жизнь агента за время испытания")
        self.assertNotIn("data_restored", r)
        self.assertIn("данные агента не трогал", r["note"])
        self.assertTrue(all(c["ok"] for c in r["rollback_checks"]))

    def test_владелец_поверх_агента(self):
        self.run_update()
        self.verdict("reject", by="window", words="")
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "rolled_back")
        self.assertIn("владелец на испытании сказал «сломано»", r["note"])

    def test_молчание_до_срока_откат(self):
        self.run_update()
        self.now[0] += control.UPDATE_TRIAL_DEFAULT * 60 + 1
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertIn("не ответил на испытании", r["note"])
        self.assertEqual(r["trial"]["verdict"]["verdict"], "timeout")
        # старая версия получит записку об откате
        self.assertEqual(control.update_unreported(self.install / "data")["state"], "rolled_back")

    def test_занятому_агенту_срок_продлевается_но_не_бесконечно(self):
        self.run_update(trial_min=10)
        busy = self.install / "data" / "memory" / ".control" / "desk_inbox"
        busy.mkdir(parents=True)
        self.now[0] += 10 * 60 + 1
        # квитанция — по тем же часам, что у исполнителя (прежде он смотрел на настоящие)
        (busy / ".reader.json").write_text(json.dumps({"busy": True, "at": self.now[0]}))
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "trial")
        self.assertEqual(r["trial"]["extended"], up.TRIAL_EXTEND)
        self.now[0] += up.TRIAL_EXTEND + 1
        self.u.tick()
        self.assertEqual(self.receipt()["state"], "rolled_back")
        self.assertIn("с продлением", self.receipt()["note"])

    def test_испытание_переживает_перезапуск_исполнителя(self):
        self.run_update()
        again = self.again()
        again.recover()
        self.assertEqual(self.receipt()["state"], "trial")
        self.verdict("accept")
        again.tick()                     # compose нужен для уборки — health спросит себя сам
        self.assertEqual(self.receipt()["state"], "done")


@unittest.skipUnless(LINUX, "исполнитель живёт на Linux: O_NOFOLLOW и dir_fd")
class RollbackAndFailures(Base):
    def test_новая_версия_не_прошла_откат_без_касания_данных(self):
        self.docker.alive["sha256:new"] = False

        def new_version_writes(image):
            if image == "sha256:new":
                (self.install / "data" / "memory" / "migrated.flag").write_text("x")

        self.docker.on_up = new_version_writes
        r = self.run_update(wait_min=2)
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertIn("агент (раннер) жив", r["note"])
        self.assertEqual(json.loads(self.code("app/desk.json"))["version"], OLD)
        # память — жизнь агента: откат её не трогает
        self.assertTrue((self.install / "data" / "memory" / "migrated.flag").exists())
        self.assertNotIn("data_restored", r)
        bdir = Path(self.u.state["backup_dir"])
        self.assertTrue((bdir / "data").is_dir(), "копия data/ должна лежать для ручного случая")
        self.assertTrue((bdir / "failed-new" / "app" / "desk.json").exists())
        self.assertEqual(self.docker.tags["helene-helene:latest"], "sha256:old")
        self.assertEqual(self.docker.image, "sha256:old")
        runner = {c["name"]: c for c in r["checks"]}["runner"]
        self.assertFalse(runner["ok"])
        self.assertTrue(all(c["ok"] for c in r["rollback_checks"]))
        # собранный образ несостоявшейся версии не висит без тега
        self.assertIn("sha256:new", self.docker.removed)

    def test_прежняя_не_поднимается_на_новых_данных_данные_из_копии(self):
        self.docker.alive["sha256:new"] = False
        self.docker.data_breaks_old = True

        def new_version_migrates(image):
            if image == "sha256:new":
                (self.install / "data" / "memory" / "migrated.flag").write_text("x")

        self.docker.on_up = new_version_migrates
        r = self.run_update(wait_min=2)
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertTrue(r["data_restored"])
        self.assertFalse((self.install / "data" / "memory" / "migrated.flag").exists())
        bdir = Path(self.u.state["backup_dir"])
        self.assertTrue((bdir / "data-after-failed" / "memory" / "migrated.flag").exists())
        self.assertIn("на данных новой не поднялась", " ".join(r["rollback"]["notes"]))

    def test_квитанция_прежнего_контейнера_не_живость(self):
        self.docker.alive["sha256:new"] = False
        self.docker.channel_says_alive = True
        r = self.run_update(wait_min=2)
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertIn("от прежнего контейнера", r["note"])

    def test_раннер_падает_раз_за_разом_откат_без_ожидания(self):
        self.docker.alive["sha256:new"] = False
        self.docker.falls["sha256:new"] = 3
        started = self.now[0]
        r = self.run_update(wait_min=30)
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertIn("падает раз за разом", r["note"])
        self.assertLess(self.now[0] - started, 60)

    def test_сборка_упала_живой_агент_не_тронут(self):
        self.docker.fail_build = True
        before = (self.install / "app" / "desk.json").read_bytes()
        r = self.run_update()
        self.assertEqual(r["state"], "failed")
        self.assertIn("pip: resolution failed", r["note"])
        self.assertFalse(self.docker.did("stop"))
        self.assertEqual((self.install / "app" / "desk.json").read_bytes(), before)
        self.assertEqual(self.docker.tags["helene-helene:latest"], "sha256:old")
        # тег отката без копии никто бы не снял — снят сразу
        self.assertNotIn(f"helene-helene:{self.u.state['rollback_tag']}", self.docker.tags)

    def test_сумма_не_сошлась(self):
        self.plan(version=NEW)
        self.u.tick()
        self.net.blobs[f"https://example.test/Helene-{NEW}.zip"] = make_zip(NEW, {"app/evil.py": b"x"})
        self.yes()
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "failed")
        self.assertIn("сумма архива не сошлась", r["note"])
        self.assertFalse(self.docker.did("build"))

    def test_расширения_не_грузятся(self):
        (self.install / "data" / "extensions" / "quota").mkdir(parents=True)
        (self.install / "data" / "extensions" / "quota" / "extension.json").write_text("{}")
        self.docker.extensions_report = {"ok": False, "summary": "не грузятся: quota — api 2.0"}
        r = self.run_update()
        self.assertEqual(r["state"], "failed")
        self.assertIn("quota", r["note"])
        self.assertFalse(self.docker.did("stop"))

    def test_не_новее(self):
        self.plan(version=OLD)
        self.u.tick()
        self.assertEqual(self.receipt()["state"], "refused")
        self.assertIn("не новее", self.receipt()["note"])

    def test_мало_места_и_подсказка(self):
        with mock.patch.object(up.shutil, "disk_usage", return_value=mock.Mock(free=10 * 1024 ** 2)):
            self.plan(version=NEW)
            self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "refused")
        self.assertIn("backup: code", r["note"])

    def test_истёк_отклонён_заменён(self):
        self.plan(version=NEW)
        self.u.tick()
        self.yes("no")
        self.u.tick()
        self.assertEqual(self.receipt()["state"], "declined")
        self.plan(version=NEW)
        self.u.tick()
        first = self.receipt()["id"]
        self.plan(version=NEW)
        self.u.tick()
        hist = control.update_history(self.install / "data")
        self.assertIn({"id": first, "state": "superseded"},
                      [{"id": h["id"], "state": h["state"]} for h in hist])
        self.now[0] += control.UPDATE_AWAIT_HOURS * 3600 + 1
        self.u.tick()
        self.assertEqual(self.receipt()["state"], "expired")

    def test_не_тот_путь_установки(self):
        self.docker.working_dir = "/srv/helene/server"
        self.plan(version=NEW)
        self.u.tick()
        self.assertIn("HELENE_DIR", self.receipt()["note"])

    def test_старая_установка_правки_из_контейнера_переезжают(self):
        if not GIT:
            self.skipTest("нет git — слияние не проверить")
        # Как у Дмитрия: 1.1.0-style, код в контейнере, Йоно правил его там
        self.old_style({"tree/agent.py": agent_py(OLD, hello="привет от Йоно"),
                        "tree/yono_tool.py": b"def tool():\n    return 42\n"})
        self.plan(version=NEW)
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "awaiting")
        preview = r["code_preview"]
        self.assertTrue(preview["layer"])
        self.assertEqual(sorted(preview["files"]), ["tree/agent.py", "tree/yono_tool.py"])
        self.assertIn("жил внутри контейнера", r["note"])
        self.assertEqual(self.net.downloads, [])            # база — из прежнего образа, не с GitHub
        self.yes()
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "done", r.get("note"))
        merged = self.code("tree/agent.py")
        self.assertIn(f"VERSION = {NEW!r}", merged)
        self.assertIn("привет от Йоно", merged)
        self.assertEqual(self.code("tree/yono_tool.py"), "def tool():\n    return 42\n")
        code = r["agent_code"]
        self.assertTrue(code["from_layer"])
        self.assertEqual(code["merged"], ["tree/agent.py"])
        self.assertEqual(code["carried"], ["tree/yono_tool.py"])
        # снимок контейнера — образ отката: с ним откат вернул бы агента вместе с правками
        self.assertTrue(self.docker.tags[f"helene-helene:{self.u.state['rollback_tag']}"].startswith("sha256:commit"))
        self.assertTrue(self.docker.did("commit", "--pause=false"))
        self.assertEqual(self.docker.created, {})           # временный контейнер базы убран
        self.assertTrue({c["name"]: c for c in r["checks"]}["code"]["ok"])

    def test_старая_установка_откат_возвращает_правки_из_снимка(self):
        self.old_style({"tree/yono_tool.py": b"def tool():\n    return 42\n"})
        self.docker.alive["sha256:new"] = False
        self.docker.falls["sha256:new"] = 3
        r = self.run_update()
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertTrue(self.docker.image.startswith("sha256:commit"))
        self.assertEqual((self.docker.layer / "tree" / "yono_tool.py").read_bytes(),
                         b"def tool():\n    return 42\n")
        self.assertIn("правки агента в коде — как были до обновления", r["note"])

    def test_старая_установка_снимок_не_сделался_контейнер_не_пересоздан(self):
        self.old_style({"tree/yono_tool.py": b"def tool():\n    return 42\n"})
        self.docker.fail_commit = True
        r = self.run_update()
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertIn("снимок контейнера агента не сделался", r["note"])
        # пересоздать контейнер значило бы стереть слой с правками агента
        rollback_up = [c for c in self.docker.calls if c[0] == "compose" and "up" in c][-1]
        self.assertNotIn("--force-recreate", rollback_up)
        self.assertEqual(self.docker.image, "sha256:old")
        self.assertEqual((self.docker.layer / "tree" / "yono_tool.py").read_bytes(),
                         b"def tool():\n    return 42\n")
        self.assertTrue(self.docker.running)

    def test_чужой_сбой_сверки_не_оставляет_вечное_сверяю(self):
        self.u.release_for = lambda version: {}["нет такого ключа"]
        self.plan(version=NEW)
        self.u.tick()
        self.assertEqual(self.receipt()["state"], "refused")
        self.assertIn("KeyError", self.receipt()["note"])
        self.assertTrue(control.update_plan(self.install / "data", NEW)["ok"])

    def test_чужой_сбой_до_остановки_возвращает_без_пересоздания(self):
        self.plan(version=NEW)
        self.u.tick()
        self.yes()

        def broken():
            raise KeyError("carried_before")

        self.u._wait_idle = broken
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "failed", r.get("note"))
        self.assertIn("KeyError", r["note"])
        self.assertIn("агента не останавливал", r["note"])
        self.assertEqual(json.loads(self.code("app/desk.json"))["version"], OLD)
        # агент жил прежним образом и папками — пересоздавать его было незачем
        self.assertFalse(self.docker.did("--force-recreate"))
        self.assertFalse(self.docker.did("stop"))
        self.assertTrue(self.docker.running)

    def test_сбой_на_сборке_всё_возвращается(self):
        self.plan(version=NEW)
        self.u.tick()
        self.yes()
        self.u._rehearse_extensions = lambda: (_ for _ in ()).throw(SystemExit("убит после сборки"))
        with self.assertRaises(SystemExit):
            self.u.tick()
        self.assertEqual(self.docker.tags["helene-helene:latest"], "sha256:new")
        again = self.again()
        again.recover()
        r = self.receipt()
        self.assertEqual(r["state"], "failed")
        self.assertIn("перезапустился посреди подготовки", r["note"])
        self.assertEqual(self.docker.tags["helene-helene:latest"], "sha256:old")
        self.assertFalse(self.docker.did("stop"))

    def test_сбой_посреди_подмены_папок_агент_не_тронут(self):
        self.plan(version=NEW)
        self.u.tick()
        self.yes()

        def die():
            raise SystemExit("убит посреди подмены")

        self.u._wait_idle = die
        with self.assertRaises(SystemExit):
            self.u.tick()
        again = self.again()
        again.recover()
        r = self.receipt()
        self.assertEqual(r["state"], "failed")
        self.assertIn("агента не останавливал", r["note"])
        self.assertEqual(json.loads(self.code("app/desk.json"))["version"], OLD)
        self.assertTrue(self.docker.running)

    def test_сбой_после_подъёма_проверка_доводит(self):
        self.plan(version=NEW)
        self.u.tick()
        self.yes()
        orig = self.u._verify
        self.u._verify = lambda *a, **k: (_ for _ in ()).throw(SystemExit("убит на проверке"))
        with self.assertRaises(SystemExit):
            self.u.tick()
        self.u._verify = orig
        again = self.again()
        again.recover()
        self.assertEqual(self.receipt()["state"], "done")

    def test_мусорный_план(self):
        up.Shared(self.install / "data").write(*up.CTL, control.UPDATE_PLAN,
                                              {"id": "abcdef12", "version": "1.2.3; rm -rf /"})
        self.u.tick()
        self.assertEqual(self.receipt()["state"], "refused")

    def test_версия_только_из_паспорта(self):
        # app/desk.json теперь на диске агента: назвать там «9.9.9» — не способ запретить
        # или разрешить обновление
        (self.install / "app" / "desk.json").write_text(json.dumps({"version": "9.9.9"}))
        self.assertEqual(self.u.current_version(), OLD)
        (self.install / "helene-build.json").unlink()
        self.assertEqual(self.u.current_version(), "")


@unittest.skipUnless(LINUX, "исполнитель живёт на Linux: O_NOFOLLOW и dir_fd")
class Recovery(Base):
    """Сбой (перезапуск исполнителя) посреди любого шага: доводится, агент не лежит (ревью 27.09)."""

    def trial(self) -> dict:
        self.docker.brain = True
        r = self.run_update()
        self.assertEqual(r["state"], "trial", r.get("note"))
        return r

    def test_слово_записано_до_отката_и_перезапуск_его_доводит(self):
        self.trial()
        self.verdict("reject", words="рука shell молчит")
        self.u._undo_code = lambda: (_ for _ in ()).throw(SystemExit("убит посреди отката"))
        with self.assertRaises(SystemExit):
            self.u.tick()
        r = self.receipt()
        self.assertEqual((r["state"], r["phase"]), ("running", "rollback"))
        self.assertFalse(self.docker.running)              # агента успели остановить
        # «Принять» посреди отката — не ложное «прошло»
        got = control.update_verdict(self.install / "data", r["id"], r["trial"]["key"], "accept",
                                     by="window")
        self.assertFalse(got["ok"], got)
        # прежняя версия не получит «поднята новая — проверь себя»
        self.assertIsNone(control.update_unreported(self.install / "data"))
        self.again().tick()                                  # не ждёт срока испытания
        r = self.receipt()
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertIn("рука shell молчит", r["note"])
        self.assertTrue(self.docker.running)
        self.assertEqual(self.docker.image, "sha256:old")

    def test_принято_и_упал_посреди_уборки_итог_доводится(self):
        self.trial()
        self.verdict("accept")
        self.u._retention = lambda: (_ for _ in ()).throw(SystemExit("убит посреди уборки"))
        with self.assertRaises(SystemExit):
            self.u.tick()
        self.assertEqual((self.receipt()["state"], self.receipt()["phase"]), ("running", "accepting"))
        again = self.again()
        again.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "done", r.get("note"))
        self.assertIn("Агент принял на испытании", r["note"])     # слово не потерялось
        self.assertTrue(again.reexec)

    def test_контейнера_нет_откат_поднимает_по_записанному_проекту(self):
        self.trial()
        self.docker.missing, self.docker.running = True, False   # владелец сделал docker rm
        self.verdict("reject")
        self.again().tick()                  # свежий процесс: о контейнере он ничего не знает
        r = self.receipt()
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertFalse(self.docker.missing)
        self.assertTrue(self.docker.running)
        self.assertTrue(self.docker.did("compose", "-p", "helene", "up"))

    def test_откат_не_выходит_повтор_раз_в_минуту_потом_человек(self):
        self.trial()
        self.docker.fail_up = 99
        self.verdict("reject")
        with self.assertRaises(up.UpdateError):
            self.u.tick()                    # попытка 1: наверх, повтор — через минуту
        self.assertEqual(self.receipt()["state"], "running")
        self.u.tick()                        # раньше минуты — не повторяет
        self.assertEqual(self.u.state["rollback_tries"], 1)
        self.now[0] += up.RECOVER_EVERY + 1
        self.u.tick()                        # попытка 2 — ловится, не роняет тик
        self.assertEqual(self.u.state["rollback_tries"], 2)
        self.now[0] += up.RECOVER_EVERY + 1
        self.u.tick()                        # попытка 3 — итог «нужен человек»
        r = self.receipt()
        self.assertEqual(r["state"], "failed", r.get("note"))
        self.assertIn("нужен человек", r["note"])

    def test_перезапуск_посреди_сверки_не_застревает(self):
        self.plan(version=NEW)
        self.u.code_preview = lambda: (_ for _ in ()).throw(SystemExit("убит посреди сверки"))
        with self.assertRaises(SystemExit):
            self.u.tick()
        self.assertEqual(self.receipt()["state"], "checking")
        self.again().tick()
        r = self.receipt()
        self.assertEqual(r["state"], "refused")
        self.assertIn("посреди сверки", r["note"])
        self.assertTrue(control.update_plan(self.install / "data", NEW)["ok"])

    def test_упал_сразу_после_остановки_агент_поднят(self):
        self.plan(version=NEW)
        self.u.tick()
        self.yes()
        real = self.docker.run

        def dies_after_stop(args, **kw):
            done = real(args, **kw)
            if args[0] == "compose" and "stop" in args:
                raise SystemExit("убит сразу после остановки")
            return done

        self.docker.run = dies_after_stop
        with self.assertRaises(SystemExit):
            self.u.tick()
        self.docker.run = real
        self.assertFalse(self.docker.running)
        self.again().tick()
        r = self.receipt()
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertIn("новая версия не поднималась", r["note"])
        self.assertTrue(self.docker.running)
        self.assertEqual(self.docker.image, "sha256:old")
        self.assertEqual(json.loads(self.code("app/desk.json"))["version"], OLD)

    def test_упал_между_записью_и_переименованием_прежний_код_на_месте(self):
        self.plan(version=NEW)
        self.u.tick()
        self.yes()
        real = os.rename
        backups = str(self.install / ".updater" / "backups")

        def rename(src, dst, *a, **k):
            if str(dst).startswith(backups) and f"{os.sep}code{os.sep}" in str(dst):
                raise SystemExit("убит между записью и первым переименованием")
            return real(src, dst, *a, **k)

        with mock.patch.object(up.os, "rename", rename), self.assertRaises(SystemExit):
            self.u.tick()
        self.again().tick()
        r = self.receipt()
        self.assertEqual(r["state"], "failed", r.get("note"))
        self.assertIn("агента не останавливал", r["note"])
        # прежняя папка так и стояла на месте — её не унесло в failed-new
        self.assertEqual(json.loads(self.code("app/desk.json"))["version"], OLD)
        self.assertTrue(self.docker.running)

    def test_частичная_копия_data_не_становится_памятью(self):
        real = up.shutil.copytree
        data = self.install / "data"

        def copytree(src, dst, *a, **k):
            if Path(src) == data:
                Path(dst).mkdir(parents=True)
                (Path(dst) / "half.txt").write_text("обрывок")
                raise OSError(28, "No space left on device")
            return real(src, dst, *a, **k)

        with mock.patch.object(up.shutil, "copytree", copytree):
            r = self.run_update()
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertNotIn("data_restored", r)
        self.assertTrue((data / "memory" / "journal" / "2026-09-27.md").is_file())
        bdir = Path(self.u.state["backup_dir"])
        self.assertFalse((bdir / "data.partial").exists())
        self.assertFalse((bdir / "data").exists())
        self.assertTrue(self.docker.running)

    def test_данные_из_копии_только_если_новая_поднималась(self):
        self.docker.alive["sha256:old"] = False      # прежняя «не поднимается», но новая не запускалась
        self.u._carry_agent_code = lambda bdir: (_ for _ in ()).throw(OSError("диск"))
        r = self.run_update(wait_min=2)
        self.assertEqual(r["state"], "failed", r.get("note"))
        self.assertNotIn("data_restored", r)
        self.assertIn("откат поднят, но не проверился", r["note"])

    def test_откат_не_переписывает_helene_json_и_отдаёт_правки_испытания(self):
        self.trial()
        (self.install / "helene.json").write_text('{"port": 8094, "model": {"key": "sk-новый"}}',
                                                  encoding="utf-8")
        self.edit_agent("tree/trial_tool.py", "def t():\n    return 'на испытании'\n".encode())
        self.verdict("reject", words="сломано")
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertIn("sk-новый", self.code("helene.json"))            # настройки — жизнь, не версия
        self.assertFalse((self.install / "tree" / "trial_tool.py").exists())
        folder = self.install / "data" / "workspace" / f"update-{NEW}"
        self.assertEqual((folder / "after-update" / "tree" / "trial_tool.py").read_text("utf-8"),
                         "def t():\n    return 'на испытании'\n")
        note = next(folder.glob("ROLLBACK-*.md")).read_text("utf-8")
        self.assertIn("откачено", note)
        self.assertIn("после подъёма", r["note"])

    def test_агент_отозвал_план(self):
        self.plan(version=NEW)
        self.u.tick()
        r = self.receipt()
        control.update_confirm(self.install / "data", r["id"], r["nonce"], "no", by="agent",
                               words="передумал")
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "declined")
        self.assertIn("агент отозвал план", r["note"])
        self.assertNotIn("владелец", r["note"])

    def test_без_паспорта_отказ_а_не_любая_версия(self):
        (self.install / "helene-build.json").unlink()
        self.plan(version=OLD)
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "refused")
        self.assertIn("паспорта", r["note"])
        self.assertFalse(self.docker.did("build"))

    def test_мусор_в_пробе_не_роняет_исполнителя(self):
        garbage = json.dumps({"now": "abc", "state": [], "health": "x", "reader": {"at": {"x": 1}},
                              "supervisor": {"beat_epoch": "abc", "children": "x", "started_utc": 5}})
        self.docker.garbage["sha256:new"] = garbage
        r = self.run_update(wait_min=2)
        self.assertEqual(r["state"], "rolled_back", r.get("note"))     # мусор — «не прошло»
        self.assertTrue(all(c["ok"] for c in r["rollback_checks"]))

    def test_ответ_пробы_глубже_разбора(self):
        self.docker.garbage["sha256:new"] = "[" * 100000               # RecursionError, не ValueError
        r = self.run_update(wait_min=2)
        self.assertEqual(r["state"], "rolled_back", r.get("note"))


@unittest.skipUnless(LINUX, "исполнитель живёт на Linux: O_NOFOLLOW и dir_fd")
class Simple(Base):
    """«Всё максимально просто» (Егор, 27.09): одна кнопка, одно слово, одна команда."""

    def test_кнопка_окна_одним_нажатием(self):
        up.Shared(self.install / "data").write(*up.CTL, control.UPDATER_BEAT,
                                              {"beat_epoch": time.time(), "ok": True})
        got = control.update_plan(self.install / "data", NEW, consent="window")   # так зовёт окно
        self.assertTrue(got["ok"], got)
        self.assertIn("начнёт сам", got["note"])
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "done", r.get("note"))       # без «жду да»
        self.assertEqual(r["confirmed"]["by"], "window")
        self.assertIn("выпуск на месте — начинаю", [s["step"] for s in r["steps"]])
        self.assertTrue(r["summary"].startswith(f"Готово: теперь стоит {NEW}."), r["summary"])

    def test_слово_владельца_агенту_и_есть_да(self):
        up.Shared(self.install / "data").write(*up.CTL, control.UPDATER_BEAT,
                                              {"beat_epoch": time.time(), "ok": True})
        control.update_plan(self.install / "data", NEW, by="agent", consent="owner-words",
                            consent_words="Йоно, обновись")
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "done", r.get("note"))
        self.assertEqual((r["confirmed"]["by"], r["confirmed"]["words"]), ("owner-words", "Йоно, обновись"))

    def test_придуманное_согласие_не_согласие(self):
        self.plan(version=NEW, reason="сам решил")
        raw = json.loads((self.install / "data" / "memory" / ".control" / control.UPDATE_PLAN).read_text("utf-8"))
        raw.update(id="abcdef1234", consent="agent")                  # не из закрытого списка
        up.Shared(self.install / "data").write(*up.CTL, control.UPDATE_PLAN, raw)
        self.u.tick()
        self.assertEqual(self.receipt()["state"], "awaiting")
        self.assertFalse(self.docker.did("build"))

    def test_команда_на_сервере(self):
        up.Shared(self.install / "data").write(*up.CTL, control.UPDATER_BEAT,
                                              {"beat_epoch": time.time(), "ok": True, "current_version": OLD})
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HELENE_DIR": str(self.install)}), \
                contextlib.redirect_stdout(out):
            self.assertEqual(up.cli(["status"]), 0)
            self.assertEqual(up.cli(["plan", NEW]), 0)
        plan_id = out.getvalue().split()[-1]
        plan = json.loads((self.install / "data" / "memory" / ".control" / control.UPDATE_PLAN).read_text("utf-8"))
        self.assertEqual((plan["id"], plan["consent"], plan["asked_by"]), (plan_id, "host", "host"))
        self.u.tick()
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HELENE_DIR": str(self.install)}), \
                contextlib.redirect_stdout(out):
            self.assertEqual(up.cli(["watch", plan_id]), 0)
            # идёт обновление — второе командой не положить
            self.u.state["state"] = "running"
            self.u.save()
            self.assertEqual(up.cli(["plan", NEW]), 3)
        text = out.getvalue()
        self.assertIn("· архив скачался целым", text)
        self.assertIn(f"Готово: теперь стоит {NEW}.", text)

    def test_итоги_простыми_словами(self):
        self.docker.alive["sha256:new"] = False
        self.docker.falls["sha256:new"] = 3
        r = self.run_update()
        self.assertEqual(r["state"], "rolled_back")
        self.assertEqual(r["summary"], f"Не получилось — вернул прежнюю версию {OLD}. Почему: новая "
                                       "версия не заработала: агент не отвечает. Память и настройки агента целы.")
        for word in ("раннер", "sha256", "compose", "data/"):
            self.assertNotIn(word, r["summary"])


@unittest.skipUnless(LINUX, "исполнитель живёт на Linux: O_NOFOLLOW и dir_fd")
class OwnerFiles(Base):
    """Своё владельца в папках поставки переживает подмену (ревью 27.09)."""

    def owner_setup(self) -> Path:
        server = self.install / "server"
        (server / "models" / "audio").mkdir(parents=True)
        (server / "models" / "audio" / "whisper.bin").write_bytes(b"\x00" * 1024)
        (server / ".env").write_text("HELENE_PORT=8094\n")
        override = server / "docker-compose.override.yml"
        override.write_text("services:\n  helene: {}\n")
        self.docker.models = str(server / "models")
        self.docker.config_files = [str(server / "docker-compose.yml"), str(override)]
        return override

    def test_модели_env_и_override_переезжают(self):
        override = self.owner_setup()
        server = self.install / "server"
        r = self.run_update()
        self.assertEqual(r["state"], "done", r.get("note"))
        live = server / "models" / "audio" / "whisper.bin"
        old = Path(self.u.state["backup_dir"]) / "code" / "server" / "models" / "audio" / "whisper.bin"
        self.assertEqual(os.stat(live).st_ino, os.stat(old).st_ino)      # жёсткая ссылка, не копия
        self.assertTrue((server / ".env").is_file())
        self.assertTrue(override.is_file())
        self.assertEqual(sorted(r["owner_files"]),
                         ["server/.env", "server/docker-compose.override.yml", "server/models"])
        last_up = next(c for c in reversed(self.docker.calls) if "up" in c)
        self.assertIn(str(override), last_up)
        self.assertTrue({c["name"]: c for c in r["checks"]}["config"]["ok"])

    def test_модели_пропали_проверка_красная(self):
        self.owner_setup()
        with mock.patch.object(up, "carry_owner_files", lambda *a, **k: []):
            r = self.run_update(wait_min=2)
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertIn("папка моделей пуста", r["note"])

    def test_убранное_выпуском_не_воскресает(self):
        self.net.publish(OLD, make_zip(OLD, {"server/old_helper.py": b"# old\n"}), latest=False)
        (self.install / "server" / "old_helper.py").write_bytes(b"# old\n")
        r = self.run_update()
        self.assertEqual(r["state"], "done", r.get("note"))
        self.assertFalse((self.install / "server" / "old_helper.py").exists())
        self.assertNotIn("server/old_helper.py", r["owner_files"])

    def test_compose_вне_установки_отказ(self):
        self.docker.config_files = [str(self.install / "server" / "docker-compose.yml"), "/root/my.yml"]
        self.plan(version=NEW)
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "refused")
        self.assertIn("/root/my.yml", r["note"])


@unittest.skipUnless(LINUX, "O_NOFOLLOW и dir_fd — Linux")
class SharedNoFollow(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="helene-shared-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.outside = self.root / "server"
        self.outside.mkdir()
        (self.data / "memory").mkdir(parents=True)
        self.shared = up.Shared(self.data)

    def test_подложенная_папка_ссылкой(self):
        (self.outside / control.UPDATE_PLAN).write_text('{"id": "abcdef12"}')
        os.symlink(self.outside, self.data / "memory" / ".control")
        self.assertEqual(self.shared.read(*up.CTL, control.UPDATE_PLAN), {})
        with self.assertRaises(OSError):
            self.shared.write(*up.CTL, control.UPDATE_RECEIPT, {"state": "x"})
        with self.assertRaises(OSError):
            self.shared.put(("memory", ".control", "x.txt"), b"x")
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), [control.UPDATE_PLAN])

    def test_файл_ссылкой_заменяется_а_не_пишется_насквозь(self):
        ctl = self.data / "memory" / ".control"
        ctl.mkdir()
        target = self.outside / "updater.py"
        target.write_text("# код исполнителя")
        os.symlink(target, ctl / control.UPDATE_RECEIPT)
        self.assertEqual(self.shared.read(*up.CTL, control.UPDATE_RECEIPT), {})
        self.shared.write(*up.CTL, control.UPDATE_RECEIPT, {"state": "done"})
        self.assertEqual(target.read_text(), "# код исполнителя")
        os.symlink(target, ctl / "planted.mine")
        with self.assertRaises(OSError):
            self.shared.put(("memory", ".control", "planted.mine"), b"root writes here?")
        self.assertEqual(target.read_text(), "# код исполнителя")

    def test_fifo_и_большой_файл(self):
        ctl = self.data / "memory" / ".control"
        ctl.mkdir()
        os.mkfifo(ctl / control.UPDATE_PLAN)
        self.assertEqual(self.shared.read(*up.CTL, control.UPDATE_PLAN), {})   # не зависает
        (ctl / control.UPDATE_CONFIRM).write_text('{"a": "' + "x" * (up.MAX_SHARED + 10) + '"}')
        self.assertEqual(self.shared.read(*up.CTL, control.UPDATE_CONFIRM), {})

    def test_fifo_на_месте_журнала_не_вешает_итог(self):
        ctl = self.data / "memory" / ".control"
        ctl.mkdir()
        os.mkfifo(ctl / control.UPDATE_HISTORY)
        done = threading.Event()

        def append():
            try:
                self.shared.append(*up.CTL, control.UPDATE_HISTORY, {"id": "x"})
            except OSError:
                pass                               # ENXIO: читателя нет — и ладно
            done.set()

        threading.Thread(target=append, daemon=True).start()
        self.assertTrue(done.wait(5), "запись в журнал на месте FIFO повисла")


class CarryCode(unittest.TestCase):
    """Перенос правок агента на чистых папках — без докера, и на Windows тоже."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="helene-carry-")
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.base, self.mine, self.new = root / "base", root / "mine", root / "new"
        self.work = root / "work"
        files = {"a.py": b"a = 1\n", "b.py": agent_py(OLD), "c.py": agent_py(OLD),
                 "e.py": b"e = 1\n", "f.py": b"f = 1\n", "g.bin": b"\x00\x01base"}
        for folder in (self.base, self.mine, self.new):
            for rel, blob in files.items():
                (folder / rel).parent.mkdir(parents=True, exist_ok=True)
                (folder / rel).write_bytes(blob)
        # агент
        (self.mine / "a.py").write_bytes("a = 2  # агент\n".encode())                  # выпуск не трогал
        (self.mine / "b.py").write_bytes(agent_py(OLD, hello="йо"))            # выпуск — VERSION
        (self.mine / "c.py").write_bytes(agent_py(OLD, helper="2"))            # выпуск — тот же helper
        (self.mine / "d.py").write_bytes("d = 'новое'\n".encode())                     # добавил
        (self.mine / "e.py").unlink()                                          # удалил, выпуск не трогал
        (self.mine / "f.py").unlink()                                          # удалил, выпуск менял
        (self.mine / "g.bin").write_bytes(b"\x00\x01mine")
        (self.mine / "__pycache__").mkdir()
        (self.mine / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\x00")
        # выпуск
        (self.new / "b.py").write_bytes(agent_py(NEW))
        (self.new / "c.py").write_bytes(agent_py(NEW, helper="3"))
        (self.new / "f.py").write_bytes("f = 2  # выпуск\n".encode())
        (self.new / "g.bin").write_bytes(b"\x00\x01theirs")

    def carry(self):
        return up.carry_code(self.base, self.mine, self.new, work=self.work,
                             labels=("агент", "база", "выпуск"), prefix="tree")

    def test_правила_переноса(self):
        if not GIT:
            self.skipTest("нет git — слияние не проверить")
        rep = self.carry()
        self.assertEqual(sorted(rep["edited"]), [f"tree/{n}" for n in
                                                 ("a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.bin")])
        self.assertEqual(sorted(rep["carried"]), ["tree/a.py", "tree/d.py", "tree/e.py"])
        self.assertEqual(rep["merged"], ["tree/b.py"])
        self.assertEqual(sorted(c["path"] for c in rep["conflicts"]), ["tree/c.py", "tree/f.py", "tree/g.bin"])
        self.assertEqual((self.new / "a.py").read_bytes(), "a = 2  # агент\n".encode())
        self.assertIn(b"'\xd0\xb9\xd0\xbe'", (self.new / "b.py").read_bytes())      # «йо»
        self.assertIn(f"VERSION = {NEW!r}".encode(), (self.new / "b.py").read_bytes())
        self.assertEqual((self.new / "c.py").read_bytes(), agent_py(NEW, helper="3"))
        self.assertEqual((self.new / "d.py").read_bytes(), "d = 'новое'\n".encode())
        self.assertFalse((self.new / "e.py").exists())
        self.assertEqual((self.new / "f.py").read_bytes(), "f = 2  # выпуск\n".encode())
        self.assertEqual((self.new / "g.bin").read_bytes(), b"\x00\x01theirs")
        by = {m["path"]: m for m in rep["materials"]}
        self.assertIn(b"<<<<<<<", by["tree/c.py"]["merged"])
        self.assertIsNone(by["tree/f.py"]["mine"])
        self.assertEqual(by["tree/g.bin"]["mine"], b"\x00\x01mine")
        self.assertIn("Binary files a/tree/g.bin", "".join(rep["diff"]))
        self.assertNotIn("tree/__pycache__/a.cpython-312.pyc", rep["edited"])

    def test_без_git_тронутое_обоими_уходит_агенту(self):
        with mock.patch.object(up.subprocess, "run", side_effect=FileNotFoundError("git")):
            rep = self.carry()
        paths = {c["path"]: c["why"] for c in rep["conflicts"]}
        self.assertIn("tree/b.py", paths)
        self.assertIn("слить не вышло", paths["tree/b.py"])
        self.assertEqual((self.new / "b.py").read_bytes(), agent_py(NEW))     # вариант выпуска
        self.assertEqual((self.new / "a.py").read_bytes(), "a = 2  # агент\n".encode())  # нетронутое — всё равно

    @unittest.skipUnless(LINUX, "ссылки — Linux")
    def test_ссылки_агента_не_переносятся_и_не_читаются(self):
        secret = Path(self._tmp.name) / "state.json"
        secret.write_text('{"nonce": "секрет исполнителя"}')
        os.symlink(secret, self.mine / "leak.py")
        os.symlink(Path(self._tmp.name), self.mine / "dirlink")
        rep = self.carry()
        skipped = {s["path"]: s["why"] for s in rep["skipped"]}
        self.assertEqual(set(skipped), {"tree/leak.py", "tree/dirlink"})
        self.assertIn(f"ссылка → {secret}", skipped["tree/leak.py"])   # агенту видно, что было
        self.assertFalse((self.new / "leak.py").exists())
        self.assertNotIn("секрет", "".join(rep["diff"]))

    @unittest.skipUnless(LINUX, "биты режима — Linux")
    def test_бит_исполнения_переезжает_снятый_не_правка(self):
        for folder in (self.base, self.mine, self.new):
            (folder / "run.py").write_bytes(b"print(1)\n")
            (folder / "h.py").write_bytes(b"h = 1\n")
        (self.mine / "run.py").chmod(0o755)                      # агент поставил только бит
        (self.mine / "tool.sh").write_bytes(b"#!/bin/sh\necho hi\n")
        (self.mine / "tool.sh").chmod(0o755)                     # новый исполняемый файл агента
        (self.new / "a.py").chmod(0o755)                         # выпуск сделал исполняемым правленный агентом
        (self.base / "h.py").chmod(0o755)                        # бит «снят» — распаковкой без режимов
        self.assertNotIn("h.py", up.code_edits(self.base, self.mine))
        rep = self.carry()
        self.assertIn("tree/run.py", rep["carried"])
        self.assertIn("tree/tool.sh", rep["carried"])
        for name in ("run.py", "tool.sh", "a.py"):
            self.assertTrue(os.stat(self.new / name).st_mode & 0o111, name)

    def test_файл_на_месте_папки_конфликт_а_не_падение(self):
        (self.new / "pkg").write_bytes(b"release file\n")        # в новой версии pkg — файл
        (self.mine / "pkg").mkdir()
        (self.mine / "pkg" / "mod.py").write_bytes(b"x = 1\n")   # а агент добавил pkg/mod.py
        rep = self.carry()
        conflicts = {c["path"]: c["why"] for c in rep["conflicts"]}
        self.assertIn("tree/pkg/mod.py", conflicts)
        self.assertIn("tree/pkg", conflicts["tree/pkg/mod.py"])
        self.assertEqual((self.new / "pkg").read_bytes(), b"release file\n")
        by = {m["path"]: m for m in rep["materials"]}
        self.assertEqual(by["tree/pkg/mod.py"]["mine"], b"x = 1\n")

    def test_материалы_с_потолком(self):
        rep = up.carry_code(self.base, self.mine, self.new, work=self.work,
                            labels=("агент", "база", "выпуск"), prefix="tree", budget=[10])
        self.assertTrue(rep["conflicts"])
        self.assertTrue(all("не поместились" in c["why"] for c in rep["conflicts"]))
        self.assertTrue(all(row.get("mine") is None for row in rep["materials"]))

    def test_снимок_и_записка(self):
        snap = up.snapshot(self.mine)
        self.assertNotIn("__pycache__/a.cpython-312.pyc", snap)
        self.assertEqual(snap["a.py"][0], "file")
        self.assertEqual(up.snapshot(None), {})
        text = up.materials_readme({"carried": ["tree/a.py"], "merged": [], "skipped": [],
                                    "conflicts": [{"path": "tree/c.py", "why": "одни строки"}]},
                                   OLD, NEW)
        self.assertIn("`tree/c.py` — одни строки", text)
        self.assertIn("*.mine", text)


class DriftFromControl(unittest.TestCase):
    """Копия протокола у исполнителя (`server/updater/protocol.py`) не разъехалась с каналом."""

    NAMES = ("UPDATE_SCHEMA", "UPDATE_PLAN", "UPDATE_RECEIPT", "UPDATE_CONFIRM", "UPDATE_VERDICT",
             "UPDATE_REPORTED", "UPDATER_BEAT", "UPDATE_HISTORY", "UPDATE_BACKUPS", "UPDATE_CHECKS",
             "UPDATE_MANDATORY", "UPDATE_VERDICTS", "UPDATE_AWAIT_HOURS", "UPDATE_WAIT_MIN",
             "UPDATE_WAIT_DEFAULT", "UPDATE_TRIAL_MIN", "UPDATE_TRIAL_DEFAULT", "UPDATE_CONSENTS")

    def test_константы(self):
        for name in self.NAMES:
            self.assertEqual(getattr(up.protocol, name), getattr(control, name), name)
        self.assertEqual(up.protocol.BEAT_STALE, control.BEAT_STALE)

    def test_разбор_плана_одинаков(self):
        corpus = [None, [], {}, {"id": "ABCDEF12"}, {"id": "abcdef12"},
                  {"id": "abcdef12", "version": "v1.2.3", "backup": "CODE", "checks": ["brain"],
                   "wait_min": "99", "trial_min": 1, "reason": "  зачем \n  так ", "junk": 1},
                  {"id": "abcdef12", "checks": ["docker exec"]}, {"id": "abcdef12", "checks": "x"},
                  {"id": "abcdef12", "wait_min": "ten"}, {"id": "abcdef12", "trial_min": "ten"},
                  {"id": "abcdef12", "version": "1.2"}, {"id": "abcdef12", "force_extensions": 1,
                                                          "asked_by": "x" * 99, "chat": "c" * 999},
                  {"id": "abcdef12", "consent": "window"}, {"id": "abcdef12", "consent": "agent"},
                  {"id": "abcdef12", "consent": " Owner-Words ", "consent_words": "да " * 300},
                  {"id": "abcdef12", "consent_words": "без согласия"}]
        for raw in corpus:
            self.assertEqual(up.protocol.validate_plan(raw), control.validate_plan(raw), raw)
        for text in ("1.2.3", "v1.10.0", "latest", "", None, "1.2"):
            self.assertEqual(up.protocol.version_tuple(text), control.version_tuple(text), text)

    def test_исполнитель_не_исполняет_код_агента(self):
        # app/ и tree/ с 27.09 — на диске агента: импорт оттуда дал бы агенту root на хосте
        src = (DESK / "server" / "updater" / "updater.py").read_text("utf-8")
        self.assertNotRegex(src, r"(?m)^\s*(from|import)\s+deskd\b")
        self.assertNotIn('"app")', src.split("SCHEMA =")[0])
        proto = (DESK / "server" / "updater" / "protocol.py").read_text("utf-8")
        self.assertNotRegex(proto, r"(?m)^\s*(from|import)\s+(?!__future__|re\b)")


class PureParts(unittest.TestCase):
    def test_выпуск(self):
        rel = {"tag_name": "v1.2.0", "body": "# Изменения\nЧинит голос\nsha256 Helene-1.2.0.zip: " + "b" * 64,
               "assets": [{"name": "Helene-1.2.0.zip", "browser_download_url": "https://x/Helene-1.2.0.zip",
                           "size": 5, "digest": "sha256:" + "a" * 64},
                          {"name": "Helene-1.2.0-macos-arm64.zip", "browser_download_url": "https://x/m.zip"}]}
        got = up.pick_release(rel, "Helene")
        self.assertEqual((got["version"], got["sha256"], got["notes"]), ("1.2.0", "a" * 64, "Чинит голос"))
        self.assertEqual(got["sha_from_notes"], "b" * 64)
        for broken in ({**rel, "assets": rel["assets"][1:]}, {**rel, "draft": True},
                       {**rel, "tag_name": "nightly"}):
            with self.assertRaises(up.UpdateError):
                up.pick_release(broken, "Helene")

    def test_сумма_из_текста(self):
        self.assertEqual(up.sha_in_text(("c" * 64) + "  Helene-1.2.0.zip\n", "Helene-1.2.0.zip"), "c" * 64)
        self.assertEqual(up.sha_in_text("Praxis: " + "d" * 64 + "\nHelene-1.2.0.zip: " + "e" * 64,
                                        "Helene-1.2.0.zip"), "e" * 64)
        self.assertEqual(up.sha_in_text("ничего"), "")

    def test_перенос_переменных_и_тег(self):
        info = up.describe(FakeDocker(Path("/opt/helene")).row())
        self.assertEqual((info["project"], info["service"]), ("helene", "helene"))
        self.assertTrue(up.code_mounted(info))
        self.assertEqual(up.carried(info), {"HELENE_PUBLIC_URL": "https://helene.example.com",
                                            "HELENE_HOSTS": "helene.example.com", "HELENE_STT_THREADS": "3",
                                            "HELENE_STT_KEEP_LOADED": "0", "HELENE_PORT": "8094",
                                            "HELENE_MODELS": "/opt/praxis-models"})
        self.assertEqual(up.image_repo("helene-helene"), ("helene-helene", "latest"))
        self.assertEqual(up.image_repo("localhost:5000/a/b:1.2"), ("localhost:5000/a/b", "1.2"))

    def test_архив_без_путей_наружу_и_без_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.zip"
            good.write_bytes(make_zip(NEW))
            runtime = len(f"windows python for {NEW}".encode())
            with zipfile.ZipFile(good) as zf:
                total = sum(i.file_size for i in zf.infolist())
            self.assertEqual(up.check_zip(good), total - runtime)
            for bad in (make_zip(NEW, {"../../etc/cron.d/x": b"x"}), make_zip(NEW, root="Praxis")):
                path = Path(tmp) / "bad.zip"
                path.write_bytes(bad)
                with self.assertRaises(up.UpdateError):
                    up.check_zip(path)
            link = Path(tmp) / "link.zip"
            with zipfile.ZipFile(link, "w") as zf:
                info = zipfile.ZipInfo("Helene/app")
                info.external_attr = (0o120777 << 16)
                zf.writestr(info, "/etc")
            with self.assertRaises(up.UpdateError):
                up.check_zip(link)

    def test_паспорт_поставки(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, blob in dist_files(NEW).items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(blob)
            self.assertEqual(up.check_layout(root, NEW)["version"], NEW)
            with self.assertRaises(up.UpdateError):
                up.check_layout(root, "9.9.9")
            (root / "server" / "serverboot.py").unlink()
            with self.assertRaises(up.UpdateError) as caught:
                up.check_layout(root, NEW)
            self.assertIn("serverboot", str(caught.exception))

    def test_проверки_говорят_словами(self):
        info = up.describe(FakeDocker(Path("/opt/helene")).row())
        before = up.carried(info)
        before["__data"] = info["mounts"]["/opt/helene/data"]
        for name in up.CODE_DIRS:
            before[f"__{name}"] = info["mounts"][up.IN_CONTAINER_CODE[name]]
        probe = {"now": 100.0, "health": {"code": 200},
                 "state": {"body": {"desk": {"version": "1.1.2"}, "runner": {"alive": False, "age_s": 99}}},
                 "supervisor": {"kind": "serverboot", "beat_epoch": 99.0}}
        rows = {r["name"]: r for r in up.evaluate(probe, info, list(control.UPDATE_MANDATORY), "1.1.2", before)}
        self.assertTrue(rows["version"]["ok"])
        self.assertTrue(rows["config"]["ok"], rows["config"])
        self.assertTrue(rows["code"]["ok"], rows["code"])
        self.assertFalse(rows["runner"]["ok"])
        before["HELENE_HOSTS"] = "другой.example.com"
        before["__tree"] = "/somewhere/else"
        rows = {r["name"]: r for r in up.evaluate(probe, info, ["config", "code"], "1.1.2", before)}
        self.assertIn("HELENE_HOSTS", rows["config"]["note"])
        self.assertFalse(rows["code"]["ok"])
        self.assertIn("tree/", rows["code"]["note"])

    def test_мусор_в_записках_агента_не_роняет_разбор(self):
        info = up.describe(FakeDocker(Path("/opt/helene")).row())
        for probe in ({"now": "abc", "state": [], "health": "x"},
                      {"state": {"body": {"runner": "жив", "desk": ["1.1.2"], "brain": 1}},
                       "supervisor": {"beat_epoch": "abc", "kind": "serverboot", "children": "x"},
                       "reader": {"at": [1]}},
                      {"supervisor": {"beat_epoch": float("nan"), "started_utc": {"x": 1},
                                      "children": [{"id": "runner", "falls": "много", "halted": ""}]}},
                      ["не словарь"], None):
            rows = up.evaluate(probe, info, list(control.UPDATE_MANDATORY) + ["brain"], "1.1.2", {})
            self.assertFalse(any(r["ok"] for r in rows if r["name"] not in ("running", "config", "code")),
                             probe)
            self.assertEqual(up.doomed(probe, info), "", probe)

    def test_правки_из_docker_diff(self):
        diff = ("C /opt/helene\nC /opt/helene/tree\nA /opt/helene/tree/yono_tool.py\n"
                "C /opt/helene/tree/agent.py\nC /opt/helene/tree/__pycache__\n"
                "A /opt/helene/tree/__pycache__/agent.cpython-312.pyc\nA /opt/helene/tree/new_pkg\n"
                "A /opt/helene/tree/new_pkg/mod.py\nD /opt/helene/app/deskd/old.py\nC /opt/helene/app\n"
                "C /opt/helene/app/deskd\nA /opt/helene/data/memory/x.md\nC /tmp\nA /opt/helene/tree/empty_dir\n"
                # живой прогон 27.09: папка «изменена» только кэшем внутри — не правка агента
                "C /opt/helene/tree/core\nC /opt/helene/tree/core/__pycache__\n"
                "A /opt/helene/tree/core/__pycache__/goals.cpython-312.pyc\n")
        self.assertEqual(up.layer_paths(diff), ["app/deskd/old.py", "tree/agent.py", "tree/empty_dir",
                                                "tree/new_pkg/mod.py", "tree/yono_tool.py"])
        self.assertEqual(up.layer_paths(""), [])

    def test_причина_словами(self):
        rows = [{"name": "runner", "ok": False}, {"name": "channel", "ok": False}, {"name": "code", "ok": True},
                {"name": "нечто", "title": "своя проверка", "ok": False}]
        self.assertEqual(up.plain_checks(rows), "новая версия не заработала: агент не отвечает, окно не может "
                                                "достучаться до агента, своя проверка")
        self.assertEqual(up.plain_checks([]), "новая версия не заработала")

    def test_модели_пропали_проверка_config(self):
        info = up.describe(FakeDocker(Path("/opt/helene")).row())
        before = {**up.carried(info), "__data": info["mounts"]["/opt/helene/data"], "__models_seen": 3}
        rows = {r["name"]: r for r in up.evaluate({}, {**info, "models_seen": 3}, ["config"], "", before)}
        self.assertTrue(rows["config"]["ok"], rows["config"])
        rows = {r["name"]: r for r in up.evaluate({}, {**info, "models_seen": 0}, ["config"], "", before)}
        self.assertFalse(rows["config"]["ok"])
        self.assertIn("папка моделей пуста", rows["config"]["note"])

    def test_журнал_без_управляющих_символов(self):
        text = up.clean("ok\x1b[2J\x1b]0;evil\x07\r\n[updater 2026-09-27T00:00:00Z] готов")
        for char in ("\x1b", "\x07", "\r"):
            self.assertNotIn(char, text)
        self.assertIn("\\x1b", text)
        self.assertIn("\n    [updater", text)                  # поддельная строка — с отступом

    def test_поставка_без_исполнителя_не_поставка(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, blob in dist_files(NEW).items():
                if rel != "server/updater/protocol.py":
                    (root / rel).parent.mkdir(parents=True, exist_ok=True)
                    (root / rel).write_bytes(blob)
            with self.assertRaises(up.UpdateError) as caught:
                up.check_layout(root, NEW)
            self.assertIn("server/updater/protocol.py", str(caught.exception))

    @unittest.skipUnless(LINUX, "исполняемый скрипт вместо docker — Linux")
    def test_вывод_docker_с_пределом(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "docker"
            script.write_text("#!/bin/sh\necho 'не туда' >&2\nexec head -c 3000000 /dev/zero\n")
            script.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": tmp + os.pathsep + os.environ.get("PATH", "")}):
                code, out, err = up.Docker().run(["exec", "helene", "python"], limit=1024 ** 2, timeout=30)
                self.assertEqual(up.Docker().run(["version"], timeout=30)[0], 0)
        self.assertEqual(code, 125)
        self.assertLessEqual(len(out), 1024 ** 2)
        self.assertIn("не туда", err)
        self.assertIn("прерван", err)

    def test_записки_прежнего_контейнера_не_засчитываются(self):
        docker = FakeDocker(Path("/opt/helene"))
        info = up.describe(docker.row())
        born = docker.started
        fresh = {"now": born + 5, "state": {"body": {"runner": {"alive": True}}},
                 "supervisor": {"kind": "serverboot", "beat_epoch": born + 3,
                                "started_utc": up.utc(born + 1)},
                 "reader": {"at": born + 4}}
        rows = {r["name"]: r["ok"] for r in up.evaluate(fresh, info, ["runner", "supervisor"], "", {})}
        self.assertEqual(rows, {"runner": True, "supervisor": True})
        stale = json.loads(json.dumps(fresh))
        stale["supervisor"]["started_utc"] = up.utc(born - 600)
        stale["reader"]["at"] = born - 3
        rows = {r["name"]: r for r in up.evaluate(stale, info, ["runner", "supervisor"], "", {})}
        self.assertFalse(rows["runner"]["ok"])
        self.assertIn("прежнего контейнера", rows["runner"]["note"])
        self.assertFalse(rows["supervisor"]["ok"])
        stale["supervisor"]["children"] = [{"id": "runner", "falls": 5, "halted": ""}]
        self.assertEqual(up.doomed(stale, info), "")
        fresh["supervisor"]["children"] = [{"id": "runner", "falls": 5, "halted": ""}]
        self.assertIn("падений подряд: 5", up.doomed(fresh, info))
        self.assertEqual(up.iso_epoch("1970-01-12T13:46:40.123456789Z"), 1_000_000.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
