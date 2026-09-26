# -*- coding: utf-8 -*-
"""Стенд исполнителя обновлений на сервере (27.09): `server/updater/updater.py`.

Запуск (только Linux — O_NOFOLLOW, dir_fd; на Windows стенд пропускается):
    python tests/t_updater_2709.py

Докер и сеть подменены целиком: докер — объектом, который помнит контейнер, образы и
теги и отвечает на те же команды (`inspect`, `compose build/stop/up`, `tag`, `exec`,
`run`); сеть — ответами GitHub Releases и архивом, собранным здесь же. Файлы — настоящие:
папка установки, `data/`, копии, распаковка. Проверяется то, что может соврать:
  * без «да» с ключом расписки ничего не начинается; чужой ключ — не «да»;
  * счастливый путь: новый код на месте, прежний и data/ — в копии, helene.json не тронут,
    переменные прежнего контейнера перенесены, образ отложен тегом отката;
  * новая версия не прошла проверки — откат: прежний код, данные, образ, подъём, расписка;
  * сборка упала или сумма не сошлась — живой агент не останавливался, папки не менялись;
  * не новее, мало места, истёк, отклонён, заменён — отказы словами;
  * подложенная агентом ссылка или FIFO в `data/` не уводят исполнителя наружу;
  * сбой посреди подготовки — всё возвращается; посреди подмены — проверка или откат.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / "server" / "updater")]
from deskd import control  # noqa: E402
import updater as up  # noqa: E402

LINUX = os.name == "posix" and hasattr(os, "O_NOFOLLOW")
OLD, NEW = "1.1.1", "1.1.2"


def dist_files(version: str) -> dict[str, bytes]:
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
        "tree/agent.py": f"VERSION = {version!r}\n".encode(),
        "server/Dockerfile": b"FROM python:3.12-slim\n",
        "server/docker-compose.yml": b"name: helene\nservices:\n  helene: {}\n",
        "server/serverboot.py": b"# serverboot\n",
        "server/updater/updater.py": f"# updater {version}\n".encode(),
        "helene.json": b'{"mode": "local"}\n',
    }


def make_zip(version: str, extra: dict[str, bytes] | None = None, root: str = "Helene") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for rel, blob in {**dist_files(version), **(extra or {})}.items():
            zf.writestr(f"{root}/{rel}", blob)
        zf.writestr(f"{root}/data/", b"")
    return buf.getvalue()


class FakeNet:
    def __init__(self, releases: str):
        self.releases = releases
        self.pages: dict[str, dict] = {}
        self.texts: dict[str, str] = {}
        self.blobs: dict[str, bytes] = {}
        self.downloads = 0

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
        self.downloads += 1
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
        self.on_up = None           # что «новая версия» делает с data/ при подъёме
        # Жизнь контейнера. Квитанция раннера и записка надзора лежат в data/ и ПЕРЕЖИВАЮТ
        # замену контейнера: мёртвый новый раннер оставляет свежей квитанцию прежнего —
        # живой прогон 27.09 на этом и поймал «агент жив» у сломанного выпуска.
        self.started = 1_000_000.0
        self.reader_at = self.started + 2
        self.sup_started = self.started
        self.sup_falls = 0
        self.channel_says_alive = None     # канал верит свежей чужой квитанции (как в жизни)

    def _start(self):
        self.started += 100
        if self.alive[self.image]:
            self.reader_at = self.started + 2
        # надзор нового контейнера пишет свою записку всегда — он жив, даже когда раннер нет
        self.sup_started = self.started
        self.sup_falls = self.falls[self.image]

    @staticmethod
    def _iso(epoch: float) -> str:
        return up.utc(epoch).replace("Z", ".123456789Z")

    def row(self) -> dict:
        labels = {"com.docker.compose.project": "helene",
                  "com.docker.compose.service": "helene",
                  "com.docker.compose.project.working_dir": self.working_dir,
                  "com.docker.compose.project.config_files": self.working_dir + "/docker-compose.yml"}
        return {"Name": "/helene", "Image": self.image, "RestartCount": self.restarts,
                "Config": {"Image": "helene-helene", "Labels": labels,
                           "Env": [f"{k}={v}" for k, v in self.env.items()]},
                "HostConfig": {"PortBindings": {"8094/tcp": [{"HostIp": "127.0.0.1", "HostPort": self.port}]}},
                "Mounts": [{"Destination": "/opt/helene/data", "Source": str(self.install / "data")},
                           {"Destination": "/opt/helene/helene.json", "Source": str(self.install / "helene.json")},
                           {"Destination": "/models", "Source": self.models}],
                "State": {"Running": self.running, "Restarting": False,
                          "Status": "running" if self.running else "exited",
                          "StartedAt": self._iso(self.started)}}

    def run(self, args, *, env=None, timeout=600):
        self.calls.append(list(args))
        env = env or {}
        if args[0] == "inspect":
            return 0, json.dumps([self.row()]), ""
        if args[0] == "tag":
            src, dst = args[1], args[2]
            self.tags[dst] = self.tags.get(src, src)
            return 0, "", ""
        if args[0] == "rmi":
            self.tags.pop(args[1], None)
            return 0, "", ""
        if args[0] == "exec":
            now = self.started + 5
            alive = (self.alive[self.image] if self.channel_says_alive is None
                     else self.channel_says_alive)
            probe = {"now": now,
                     "health": {"code": 200, "body": {}},
                     "state": {"code": 200, "body": {
                         "desk": {"version": self.versions[self.image]},
                         "runner": {"alive": alive, "age_s": round(now - self.reader_at)},
                         "brain": {"configured": True, "model": "glm"}}},
                     "supervisor": {"kind": "serverboot", "beat_epoch": now - 2,
                                    "started_utc": up.utc(self.sup_started), "children": [
                                        {"id": "runner", "alive": self.alive[self.image],
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
                self.image = self.tags["helene-helene:latest"]
                self.running = True
                self.restarts = 0
                self.env = {"HELENE_HOSTS": env.get("HELENE_HOSTS", ""),
                            "HELENE_PUBLIC_URL": env.get("HELENE_PUBLIC_URL", ""),
                            "PRAXIS_STT_CPU_THREADS": env.get("HELENE_STT_THREADS", "3"),
                            "PRAXIS_STT_KEEP_LOADED": env.get("HELENE_STT_KEEP_LOADED", "0")}
                self.port = env.get("HELENE_PORT", "8094")
                self.models = env.get("HELENE_MODELS", "./models")
                self._start()
                if self.on_up:
                    self.on_up(self.image)
                return 0, "", ""
        return 0, "", ""

    def did(self, *words) -> bool:
        return any(all(w in call for w in words) for call in self.calls)


@unittest.skipUnless(LINUX, "исполнитель живёт на Linux: O_NOFOLLOW и dir_fd")
class UpdaterFlow(unittest.TestCase):
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
        self.cfg = up.Config(self.install, releases="https://api.example.test/releases")
        self.net = FakeNet(self.cfg.releases)
        self.sha = self.net.publish(NEW, make_zip(NEW))
        self.docker = FakeDocker(self.install)
        self.now = [1_000_000.0]
        self.u = up.Updater(self.cfg, docker=self.docker, net=self.net,
                            clock=lambda: self.now[0], sleep=self.tick_clock)

    def tick_clock(self, seconds):
        self.now[0] += seconds

    def plan(self, **fields) -> str:
        """Положить план так, как это делает канал или рука (через протокол)."""
        up.Shared(self.install / "data").write(*up.CTL, control.UPDATER_BEAT,
                                              {"beat_epoch": __import__("time").time(), "ok": True})
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
        # ничего не качалось, не собиралось, не останавливалось
        self.assertEqual(self.net.downloads, 0)
        self.assertFalse(self.docker.did("build"))
        self.assertFalse(self.docker.did("stop"))
        # повторный тик без «да» — то же ожидание
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
        self.assertEqual(self.net.downloads, 0)

    def test_счастливый_путь(self):
        (self.install / "data" / "extensions" / "quota").mkdir(parents=True)
        (self.install / "data" / "extensions" / "quota" / "extension.json").write_text("{}")
        self.plan(version="latest")
        self.u.tick()
        self.yes()
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "done", r.get("note"))
        self.assertNotIn("nonce", r)
        # новый код на месте, прежний — в копии; данные и конфиг — те же
        desk = json.loads((self.install / "app" / "desk.json").read_text("utf-8"))
        self.assertEqual(desk["version"], NEW)
        bdir = Path(self.u.state["backup_dir"])
        old = json.loads((bdir / "code" / "app" / "desk.json").read_text("utf-8"))
        self.assertEqual(old["version"], OLD)
        self.assertTrue((bdir / "data" / "memory" / "journal" / "2026-09-27.md").is_file())
        self.assertIn("sk-secret", (self.install / "helene.json").read_text("utf-8"))
        self.assertTrue((self.install / "data" / "memory" / "journal" / "2026-09-27.md").is_file())
        # поставочный шаблон helene.json и пустая data/ из архива не легли поверх
        self.assertFalse((bdir / "code" / "helene.json").exists())
        # переменные прежнего контейнера — у нового
        self.assertEqual(self.docker.env["HELENE_HOSTS"], "helene.example.com")
        self.assertEqual(self.docker.models, "/opt/praxis-models")
        # образ отложен, расширения прорепетированы, проверки все
        self.assertIn(f"helene-helene:{self.u.state['rollback_tag']}", self.docker.tags)
        self.assertTrue(self.docker.did("run", "--check-extensions"))
        self.assertTrue(all(c["ok"] for c in r["checks"]))
        self.assertEqual({c["name"] for c in r["checks"]}, set(control.UPDATE_MANDATORY))
        # сборка шла ИЗ распакованного, до остановки агента
        build = next(i for i, c in enumerate(self.docker.calls) if "build" in c)
        stop = next(i for i, c in enumerate(self.docker.calls) if "stop" in c)
        self.assertLess(build, stop)
        self.assertIn("stage", " ".join(self.docker.calls[build]))
        # за собой убрано, история легла, исполнитель перезапустится новым кодом
        self.assertFalse(Path(self.u.state["stage_dir"]).exists())
        self.assertTrue(self.u.reexec)
        hist = control.update_history(self.install / "data")
        self.assertEqual(hist[-1]["state"], "done")
        self.assertEqual(hist[-1]["confirmed_by"], "window")

    def test_новая_версия_не_прошла_проверки_откат(self):
        self.docker.alive["sha256:new"] = False

        def new_version_writes(image):
            if image == "sha256:new":
                (self.install / "data" / "memory" / "migrated.flag").write_text("x")

        self.docker.on_up = new_version_writes
        self.plan(version=NEW, wait_min=2)
        self.u.tick()
        self.yes()
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertIn("агент (раннер) жив", r["note"])
        desk = json.loads((self.install / "app" / "desk.json").read_text("utf-8"))
        self.assertEqual(desk["version"], OLD)
        # данные — из копии; то, что записала новая версия, — рядом, не потеряно
        self.assertFalse((self.install / "data" / "memory" / "migrated.flag").exists())
        bdir = Path(self.u.state["backup_dir"])
        self.assertTrue((bdir / "data-after-failed" / "memory" / "migrated.flag").exists())
        self.assertTrue((bdir / "failed-new" / "app" / "desk.json").exists())
        # прежний образ вернулся под рабочий тег и поднят
        self.assertEqual(self.docker.tags["helene-helene:latest"], "sha256:old")
        self.assertEqual(self.docker.image, "sha256:old")
        self.assertTrue(r["rollback"]["ok"])
        # проверки в расписке — НОВОЙ версии (красные), проверки отката — отдельно (зелёные)
        runner = {c["name"]: c for c in r["checks"]}["runner"]
        self.assertFalse(runner["ok"])
        self.assertTrue(all(c["ok"] for c in r["rollback_checks"]))

    def test_квитанция_прежнего_контейнера_не_живость(self):
        # Живой прогон 27.09: новый раннер мёртв, но квитанцию в data/ свежей оставил
        # прежний контейнер, и канал честно говорит «жив». Это не живость нового.
        self.docker.alive["sha256:new"] = False
        self.docker.channel_says_alive = True
        self.plan(version=NEW, wait_min=2)
        self.u.tick()
        self.yes()
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertIn("от прежнего контейнера", r["note"])
        self.assertEqual(self.docker.image, "sha256:old")
        self.assertTrue(r["rollback"]["ok"])

    def test_раннер_падает_раз_за_разом_откат_без_ожидания(self):
        self.docker.alive["sha256:new"] = False
        self.docker.falls["sha256:new"] = 3
        self.plan(version=NEW, wait_min=30)
        self.u.tick()
        self.yes()
        started = self.now[0]
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "rolled_back", r.get("note"))
        self.assertIn("падает раз за разом", r["note"])
        self.assertLess(self.now[0] - started, 60, "ждал полчаса вместо отката сразу")
        self.assertEqual(self.docker.image, "sha256:old")

    def test_сборка_упала_живой_агент_не_тронут(self):
        self.docker.fail_build = True
        before = (self.install / "app" / "desk.json").read_bytes()
        self.plan(version=NEW)
        self.u.tick()
        self.yes()
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "failed")
        self.assertIn("не начато", r["note"])
        self.assertIn("pip: resolution failed", r["note"])
        self.assertFalse(self.docker.did("stop"))
        self.assertEqual((self.install / "app" / "desk.json").read_bytes(), before)
        self.assertEqual(self.docker.tags["helene-helene:latest"], "sha256:old")

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
        self.plan(version=NEW)
        self.u.tick()
        self.yes()
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "failed")
        self.assertIn("quota", r["note"])
        self.assertFalse(self.docker.did("stop"))
        self.assertEqual(json.loads((self.install / "app" / "desk.json").read_text())["version"], OLD)

    # --- отказы -------------------------------------------------------------

    def test_не_новее(self):
        self.net.publish(OLD, make_zip(OLD))
        self.plan(version=OLD)
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "refused")
        self.assertIn("не новее", r["note"])

    def test_мало_места_и_подсказка(self):
        with mock.patch.object(up.shutil, "disk_usage", return_value=mock.Mock(free=10 * 1024 ** 2)):
            self.plan(version=NEW)
            self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "refused")
        self.assertIn("мало места", r["note"])
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
        self.assertEqual(self.receipt()["state"], "awaiting")
        self.now[0] += control.UPDATE_AWAIT_HOURS * 3600 + 1
        self.u.tick()
        self.assertEqual(self.receipt()["state"], "expired")

    def test_не_тот_путь_установки(self):
        self.docker.working_dir = "/srv/helene/server"
        self.plan(version=NEW)
        self.u.tick()
        r = self.receipt()
        self.assertEqual(r["state"], "refused")
        self.assertIn("HELENE_DIR", r["note"])

    def test_мусорный_план(self):
        up.Shared(self.install / "data").write(*up.CTL, control.UPDATE_PLAN,
                                              {"id": "abcdef12", "version": "1.2.3; rm -rf /"})
        self.u.tick()
        self.assertEqual(self.receipt()["state"], "refused")

    # --- сбой посреди -------------------------------------------------------

    def test_сбой_на_сборке_всё_возвращается(self):
        self.plan(version=NEW)
        self.u.tick()
        self.yes()
        orig = self.u._rehearse_extensions
        self.u._rehearse_extensions = lambda: (_ for _ in ()).throw(SystemExit("убит после сборки"))
        with self.assertRaises(SystemExit):
            self.u.tick()
        self.u._rehearse_extensions = orig
        self.assertEqual(self.docker.tags["helene-helene:latest"], "sha256:new")   # собран
        again = up.Updater(self.cfg, docker=self.docker, net=self.net,
                           clock=lambda: self.now[0], sleep=self.tick_clock)
        self.assertEqual((again.state["state"], again.state["phase"]), ("running", "prepare"))
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

        # папки уже подменены, агент ещё не остановлен — и тут исполнитель умер
        self.u._wait_idle = die
        with self.assertRaises(SystemExit):
            self.u.tick()
        self.assertEqual(json.loads((self.install / "app" / "desk.json").read_text())["version"], NEW)
        again = up.Updater(self.cfg, docker=self.docker, net=self.net,
                           clock=lambda: self.now[0], sleep=self.tick_clock)
        again.recover()
        r = self.receipt()
        self.assertEqual(r["state"], "failed")
        self.assertIn("агента не останавливал", r["note"])
        self.assertEqual(json.loads((self.install / "app" / "desk.json").read_text())["version"], OLD)
        self.assertEqual((self.install / "tree" / "agent.py").read_text(), f"VERSION = {OLD!r}\n")
        self.assertEqual(self.docker.tags["helene-helene:latest"], "sha256:old")
        self.assertFalse(self.docker.did("stop"))
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
        again = up.Updater(self.cfg, docker=self.docker, net=self.net,
                           clock=lambda: self.now[0], sleep=self.tick_clock)
        again.recover()
        self.assertEqual(self.receipt()["state"], "done")


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
        self.assertFalse((self.outside / control.UPDATE_RECEIPT).exists())

    def test_файл_ссылкой_заменяется_а_не_пишется_насквозь(self):
        ctl = self.data / "memory" / ".control"
        ctl.mkdir()
        target = self.outside / "updater.py"
        target.write_text("# код исполнителя")
        os.symlink(target, ctl / control.UPDATE_RECEIPT)
        self.assertEqual(self.shared.read(*up.CTL, control.UPDATE_RECEIPT), {})
        self.shared.write(*up.CTL, control.UPDATE_RECEIPT, {"state": "done"})
        self.assertEqual(target.read_text(), "# код исполнителя")
        self.assertFalse((ctl / control.UPDATE_RECEIPT).is_symlink())

    def test_fifo_и_большой_файл(self):
        ctl = self.data / "memory" / ".control"
        ctl.mkdir()
        os.mkfifo(ctl / control.UPDATE_PLAN)
        self.assertEqual(self.shared.read(*up.CTL, control.UPDATE_PLAN), {})   # не зависает
        (ctl / control.UPDATE_CONFIRM).write_text('{"a": "' + "x" * (up.MAX_SHARED + 10) + '"}')
        self.assertEqual(self.shared.read(*up.CTL, control.UPDATE_CONFIRM), {})


class PureParts(unittest.TestCase):
    def test_выпуск(self):
        rel = {"tag_name": "v1.2.0", "body": "# Изменения\nЧинит голос\nsha256 Helene-1.2.0.zip: " + "b" * 64,
               "assets": [{"name": "Helene-1.2.0.zip", "browser_download_url": "https://x/Helene-1.2.0.zip",
                           "size": 5, "digest": "sha256:" + "a" * 64},
                          {"name": "Helene-1.2.0-macos-arm64.zip", "browser_download_url": "https://x/m.zip"}]}
        got = up.pick_release(rel, "Helene")
        self.assertEqual((got["version"], got["sha256"], got["notes"]), ("1.2.0", "a" * 64, "Чинит голос"))
        self.assertEqual(got["sha_from_notes"], "b" * 64)
        with self.assertRaises(up.UpdateError):
            up.pick_release({**rel, "assets": rel["assets"][1:]}, "Helene")
        with self.assertRaises(up.UpdateError):
            up.pick_release({**rel, "draft": True}, "Helene")
        with self.assertRaises(up.UpdateError):
            up.pick_release({**rel, "tag_name": "nightly"}, "Helene")

    def test_сумма_из_текста(self):
        self.assertEqual(up.sha_in_text(("c" * 64) + "  Helene-1.2.0.zip\n", "Helene-1.2.0.zip"), "c" * 64)
        self.assertEqual(up.sha_in_text("Praxis: " + "d" * 64 + "\nHelene-1.2.0.zip: " + "e" * 64,
                                        "Helene-1.2.0.zip"), "e" * 64)
        self.assertEqual(up.sha_in_text("ничего"), "")

    def test_перенос_переменных_и_тег(self):
        docker = FakeDocker(Path("/opt/helene"))
        info = up.describe(docker.row())
        self.assertEqual((info["project"], info["service"]), ("helene", "helene"))
        carried = up.carried(info)
        self.assertEqual(carried, {"HELENE_PUBLIC_URL": "https://helene.example.com",
                                   "HELENE_HOSTS": "helene.example.com", "HELENE_STT_THREADS": "3",
                                   "HELENE_STT_KEEP_LOADED": "0", "HELENE_PORT": "8094",
                                   "HELENE_MODELS": "/opt/praxis-models"})
        self.assertEqual(up.image_repo("helene-helene"), ("helene-helene", "latest"))
        self.assertEqual(up.image_repo("localhost:5000/a/b:1.2"), ("localhost:5000/a/b", "1.2"))

    def test_архив_без_путей_наружу(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.zip"
            good.write_bytes(make_zip(NEW))
            self.assertGreater(up.check_zip(good), 0)
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
        probe = {"now": 100.0, "health": {"code": 200},
                 "state": {"body": {"desk": {"version": "1.1.2"}, "runner": {"alive": False, "age_s": 99}}},
                 "supervisor": {"kind": "serverboot", "beat_epoch": 99.0}}
        rows = {r["name"]: r for r in up.evaluate(probe, info, list(control.UPDATE_MANDATORY), "1.1.2", before)}
        self.assertTrue(rows["version"]["ok"])
        self.assertTrue(rows["config"]["ok"], rows["config"])
        self.assertFalse(rows["runner"]["ok"])
        self.assertIn("99", rows["runner"]["note"])
        before["HELENE_HOSTS"] = "другой.example.com"
        rows = {r["name"]: r for r in up.evaluate(probe, info, ["config"], "1.1.2", before)}
        self.assertFalse(rows["config"]["ok"])
        self.assertIn("HELENE_HOSTS", rows["config"]["note"])

    def test_записки_прежнего_контейнера_не_засчитываются(self):
        docker = FakeDocker(Path("/opt/helene"))
        docker.started = 1_000_000.0
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
        # и «быстрый провал» не верит падениям из записки сломанного прежнего контейнера
        stale["supervisor"]["children"] = [{"id": "runner", "falls": 5, "halted": ""}]
        self.assertEqual(up.doomed(stale, info), "")
        fresh["supervisor"]["children"] = [{"id": "runner", "falls": 5, "halted": ""}]
        self.assertIn("падений подряд: 5", up.doomed(fresh, info))
        self.assertEqual(up.iso_epoch("1970-01-12T13:46:40.123456789Z"), 1_000_000.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
