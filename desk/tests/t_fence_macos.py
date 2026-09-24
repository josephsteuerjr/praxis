# -*- coding: utf-8 -*-
"""Стенд «ограда на macOS»: то же обещание, механизм seatbelt.

Запуск:  python tests/t_fence_macos.py
         (разбор профиля идёт на любой платформе — это чистый расчёт; живые
          проверки — только на macOS с /usr/bin/sandbox-exec, их прогоняет
          раннер macOS)

Ограда в основу порта входит обязательно (решение владельца): без неё `shell`
и исполняющие тулы мастерской идут с правами пользователя, а текст режима
«Песочница» обещает обратное. Здесь стерегут ровно то, что делает обещание
правдой:

  * секреты владельца закрыты ПОСЛЕ разрешения на память, внутри которой они
    лежат (в SBPL побеждает последнее совпавшее правило), и вдобавок исключены
    из самого разрешения — под любой трактовкой порядка `cat memory/llm.json`
    из shell ключей не выдаст;
  * дом агента — на запись, код продукта — только на чтение, чужое — только
    метаданные;
  * сеть выключается флагом, а не надеждой;
  * смонтированные папки идут по словам доступа владельца;
  * пути в профиле экранированы: кавычка или пробел в имени папки не ломают
    профиль и не открывают лишнего;
  * `bash -lc` получает PATH поставки ПЕРВЫМ — иначе `python3` внутри ограды
    был бы заглушкой Apple, а не питоном поставки;
  * когда `sandbox-exec` недоступен, ограда отказывается ВСЛУХ.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))

import fence  # noqa: E402
import fence_macos  # noqa: E402

DARWIN = sys.platform == "darwin"
SANDBOX = Path(fence_macos.SANDBOX_EXEC).is_file()


class Ground:
    """Раскладка установки во временной папке: root/{app,tree,runtime,data/…}."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="helene-seatbelt-")
        self.root = Path(self.tmp.name).resolve()
        self.tree = self.root / "data"
        self.workspace = self.tree / "workspace"
        for rel in ("app", "tree", "runtime/bin", "runtime/git/bin",
                    "data/workspace", "data/memory/.state", "data/soul"):
            (self.root / rel).mkdir(parents=True, exist_ok=True)

    def box(self, network: bool = True) -> fence_macos.Container:
        return fence_macos.Container(self.root, self.workspace, network, tree=self.tree,
                                     secrets=fence.secret_paths(self.root, self.tree))

    def close(self):
        self.tmp.cleanup()


def _rules(profile: str) -> list[str]:
    """Профиль -> список правил `(allow …)`/`(deny …)` одной строкой каждое."""
    out, current = [], []
    for line in profile.splitlines():
        if line.startswith(";;") or not line.strip():
            continue
        if line.startswith("("):
            if current:
                out.append(" ".join(current))
            current = [line.strip()]
        else:
            current.append(line.strip())
    if current:
        out.append(" ".join(current))
    return out


class Profile(unittest.TestCase):
    """Что уйдёт ядру. Разбирается на любой платформе: это чистый расчёт."""

    def setUp(self):
        self.g = Ground()
        self.addCleanup(self.g.close)

    def q(self, path) -> str:
        return fence_macos._q(fence_macos._abs(path))

    def test_дом_на_запись_код_на_чтение(self):
        rules = _rules(self.g.box().profile())
        rw = [r for r in rules if r.startswith("(allow file-read* file-write*")]
        ro = [r for r in rules if r.startswith("(allow file-read* ") and "file-write*" not in r]
        home = " ".join(rw)
        # `.git` НЕ в общем rw — у него свои правила (см. test_git_…): чтение
        # целиком, запись без config/hooks/info.
        for path in (self.g.workspace, self.g.tree / "memory", self.g.tree / "soul"):
            self.assertIn(f"(subpath {self.q(path)})", home, f"{path} не выдан на запись")
        code = " ".join(ro)
        for path in (self.g.root / "app", self.g.root / "tree", self.g.root / "runtime"):
            self.assertIn(f"(subpath {self.q(path)})", code, f"{path} не выдан на чтение")
            self.assertNotIn(f"(subpath {self.q(path)})", home, f"{path} выдан на запись")
        # Корни — только пройти: сама папка, не содержимое.
        self.assertIn(f"(literal {self.q(self.g.root)})", code)
        self.assertNotIn(f"(subpath {self.q(self.g.root)})", code + home)
        self.assertNotIn(f"(subpath {self.q(self.g.tree)})", code + home,
                         "дерево данных целиком — это и relay/, и telegram/")

    def test_git_config_и_хуки_из_ограды_только_на_чтение(self):
        # Побег: движок вне ограды коммитит на каждый shell без --no-verify, и
        # хук `.git/hooks/pre-commit` или core.hooksPath из `.git/config`,
        # записанные ИЗ ограды, исполнились бы снаружи неё с полной средой.
        rules = _rules(self.g.box().profile())
        git = self.q(self.g.tree / ".git")
        read = [r for r in rules if r.startswith("(allow file-read*") and "file-write*" not in r]
        self.assertTrue(any(f"(subpath {git})" in r for r in read),
                        "весь .git должен читаться — иначе git внутри ограды не работает")
        write = [r for r in rules if r.startswith("(allow file-write*")]
        self.assertEqual(len(write), 1, "запись в .git — одним правилом с исключениями")
        w = write[0]
        self.assertIn(f"(require-all (subpath {git})", w)
        for name in ("config", "hooks", "info"):
            self.assertIn(f"(require-not (subpath {self.q(self.g.tree / '.git' / name)}))", w,
                          f".git/{name} обязан быть закрыт на запись из ограды")
        # И .git НЕ в общем rw-правиле — иначе хук писался бы через него.
        rw = " ".join(r for r in rules if r.startswith("(allow file-read* file-write*"))
        self.assertNotIn(f"(subpath {git})", rw)

    def test_mach_lookup_поимённо_без_launchservices_и_pasteboard(self):
        text = self.g.box().profile()
        self.assertNotIn("(allow mach-lookup)", text, "полный mach-lookup открывает open/pbpaste")
        rules = _rules(text)
        mach = next(r for r in rules if r.startswith("(allow mach-lookup"))
        for name in ("com.apple.trustd", "com.apple.mDNSResponder",
                     "com.apple.cfprefsd.daemon", "com.apple.system.opendirectoryd.libinfo"):
            self.assertIn(f'(global-name "{name}")', mach, f"{name} нужен bash/python/git")
        # По ПРАВИЛАМ (не комментариям — их `_rules` отбрасывает): ни одно allow
        # не выдаёт launchservicesd (open) или pasteboard (pbpaste).
        allows = "\n".join(r for r in rules if r.startswith("(allow"))
        for banned in ("launchservicesd", "pasteboard", "com.apple.pbs"):
            self.assertNotIn(banned, allows, f"{banned} открыл бы процесс/буфер вне ограды")

    def test_закрыто_по_умолчанию_и_системное_только_на_чтение(self):
        text = self.g.box().profile()
        self.assertTrue(text.startswith("(version 1)"))
        self.assertIn("(deny default)", text)
        rules = _rules(text)
        ro = " ".join(r for r in rules if r.startswith("(allow file-read* ")
                      and "file-write*" not in r)
        for path in ("/usr", "/System", "/Library", "/private/etc", "/opt/homebrew"):
            self.assertIn(f'(subpath "{path}")', ro)
        # Оба написания ссылок: ядро сверяет путь после разбора.
        for path in ("/tmp", "/private/tmp", "/var", "/private/var", "/etc"):
            self.assertIn(f'(literal "{path}")', ro)
        rw = " ".join(r for r in rules if r.startswith("(allow file-read* file-write*"))
        self.assertNotIn('(subpath "/usr")', rw)
        self.assertNotIn('(subpath "/Users")', text, "дом владельца целиком не открыт")

    def test_секреты_после_разрешений_и_исключены_из_памяти(self):
        text = self.g.box().profile()
        rules = _rules(text)
        deny = [i for i, r in enumerate(rules) if r.startswith("(deny file-read* file-write*")]
        self.assertEqual(len(deny), 1, "секреты закрываются одним правилом")
        allows = [i for i, r in enumerate(rules) if r.startswith("(allow file-")]
        self.assertGreater(deny[0], max(allows),
                           "запрет секретов обязан стоять ПОСЛЕ всех разрешений на файлы")
        denied = rules[deny[0]]
        for secret in fence.secret_paths(self.g.root, self.g.tree):
            self.assertIn(f"(literal {self.q(secret)})", denied, f"{secret} не закрыт")
            self.assertIn(f"(subpath {self.q(secret)})", denied)
        # Память выдана — но с исключением ключей и снимка устройства.
        memory_rule = next(r for r in rules if r.startswith("(allow file-read* file-write*"))
        self.assertIn(f"(require-all (subpath {self.q(self.g.tree / 'memory')})", memory_rule)
        for inside in (self.g.tree / "memory" / "llm.json",
                       self.g.tree / "memory" / ".state" / "anatomy.json"):
            self.assertIn(f"(require-not (subpath {self.q(inside)}))", memory_rule)
        # А helene.json лежит вне памяти — в исключениях ему делать нечего.
        self.assertNotIn(f"(require-not (subpath {self.q(self.g.root / 'helene.json')}))",
                         memory_rule)

    def test_сеть_по_флагу(self):
        with_net = self.g.box(network=True).profile()
        # Наружу — куда угодно; слушать/принимать — только на localhost, чтобы
        # команда из ограды не раздала workspace по LAN. Не `(allow network*)`.
        self.assertNotIn("(allow network*)", with_net,
                         "blanket network* дал бы bind/inbound на всех интерфейсах")
        self.assertIn("(allow network-outbound)", with_net)
        self.assertIn('(allow network-bind (local ip "localhost:*"))', with_net)
        self.assertIn('(allow network-inbound (local ip "localhost:*"))', with_net)
        self.assertNotIn("(deny network*)", with_net)
        without = self.g.box(network=False).profile()
        self.assertIn("(deny network*)", without)
        self.assertNotIn("(allow network-outbound)", without)
        self.assertNotIn("(allow network-bind", without)
        self.assertNotIn("(allow system-socket)", without)

    def test_монтирования_по_словам_доступа(self):
        box = self.g.box()
        docs, work = self.g.root.parent / "docs-ro", self.g.root.parent / "work-rw"
        volx = Path("/Volumes/x")
        box.sync_mounts([
            {"path": str(docs), "real": str(docs), "link": str(self.g.workspace / "mnt" / "docs"),
             "access": "read", "error": ""},
            {"path": str(work), "real": str(work), "link": str(self.g.workspace / "mnt" / "work"),
             "access": "write", "error": ""},
            # С ошибкой — в профиль не попадает (папки нет).
            {"path": "/Users/кто-то", "real": "/Users/кто-то", "link": "",
             "access": "write", "error": "папки нет"},
            # БЕЗ стыка, но без ошибки — попадает: право даётся по реальному пути,
            # стык (символическая ссылка) — удобство, не право (как на Windows).
            {"path": str(volx), "real": str(volx), "link": "", "access": "write",
             "error": ""},
        ])
        rules = _rules(box.profile())
        self.assertIn(f"(allow file-read* (subpath {self.q(docs)}))", rules)
        self.assertIn(f"(allow file-read* file-write* (subpath {self.q(work)}))", rules)
        self.assertIn(f"(allow file-read* file-write* (subpath {self.q(volx)}))", rules,
                      "папка без стыка, но без ошибки, открывается по реальному пути")
        self.assertNotIn("кто-то", "\n".join(rules))
        self.assertIn("смонтировано папок: 4", box.describe())

    def test_проба_повторяется_после_монтирования_и_снимает_кривое(self):
        # prepare() пробовал профиль БЕЗ папок; кривое правило монтирования упало
        # бы на КАЖДОЙ команде. Пробуем профиль С папками и при отказе снимаем
        # монтирования, а не ограду.
        box = self.g.box()
        box.sandbox_exec = fence_macos.SANDBOX_EXEC       # притворимся подготовленной
        docs = self.g.root.parent / "docs"
        row = {"path": str(docs), "real": str(docs), "link": "", "access": "read", "error": ""}

        class _R:
            def __init__(self, rc, err=""):
                self.returncode, self.stdout, self.stderr = rc, "", err

        with patch.object(fence_macos.subprocess, "run", lambda *a, **k: _R(1, "плохое правило")):
            box.sync_mounts([dict(row)])
        self.assertEqual(box.mounts, [], "кривое монтирование не снято из профиля")
        self.assertIn("плохое", box.mounts_fault)
        self.assertEqual(box.sandbox_exec, fence_macos.SANDBOX_EXEC, "ограда осталась поднятой")

        with patch.object(fence_macos.subprocess, "run", lambda *a, **k: _R(0)):
            box.sync_mounts([dict(row)])
        self.assertEqual(len(box.mounts), 1, "исправное монтирование остаётся")
        self.assertEqual(box.mounts_fault, "")

    def test_экранирование_кавычки_и_пробела(self):
        weird = self.g.root / 'папка "с кавычкой" и пробелом\\хвост'
        box = fence_macos.Container(self.g.root, self.g.workspace, True, tree=self.g.tree)
        box.sync_mounts([{"path": str(weird), "real": str(weird), "access": "read",
                          "link": str(self.g.workspace / "mnt" / "weird"), "error": ""}])
        text = box.profile()
        self.assertIn('\\"с кавычкой\\"', text, "кавычка в пути обязана быть экранирована")
        self.assertIn("\\\\хвост", text, "обратный слэш в пути обязан быть экранирован")
        # Каждое правило по-прежнему закрыто скобкой: профиль не развалился.
        for rule in _rules(text):
            self.assertEqual(rule.count("("), rule.count(")"), rule)
        self.assertEqual(fence_macos._q('a"b\\c'), '"a\\"b\\\\c"')

    def test_временные_папки_пользователя_закрыты(self):
        # ⚠ Найдено первым живым прогоном на macOS: разрешение на
        # /private/var/folders накрывало чужие папки и код продукта. Проверяем
        # именно это: ни одно allow не даёт subpath на набор temp пользователя
        # ЦЕЛИКОМ (/private/var/folders) и на его корень <xx>/<hash> с T/C/X.
        # Пути ВНУТРИ фикстуры (сама она живёт в /private/var/folders/…/T на
        # раннере) — не в счёт: это дом и код установки, они и должны быть выданы.
        for tmpdir in ("/private/var/folders/ab/cdef123/T/", "/var/folders/zz/abc123/T/",
                       "", "/tmp"):
            with patch.dict(os.environ, {"TMPDIR": tmpdir}):
                rules = _rules(self.g.box().profile())
            allows = "\n".join(r for r in rules if r.startswith("(allow"))
            for whole in ("/private/var/folders", "/var/folders", "/private/var",
                          "/private/tmp", "/tmp"):
                self.assertNotIn(f"(subpath {fence_macos._q(whole)})", allows,
                                 f"TMPDIR={tmpdir!r}: набор temp открыт целиком — {whole}")
            for root in ("/private/var/folders/ab/cdef123", "/private/var/folders/ab/cdef123/T",
                         "/private/var/folders/ab/cdef123/C", "/private/var/folders/zz/abc123"):
                self.assertNotIn(f"(subpath {fence_macos._q(root)})", allows,
                                 f"TMPDIR={tmpdir!r}: корень temp пользователя открыт — {root}")
        # Свой temp команды — в доме, и он выдан на запись; туда и кладёт mktemp.
        env = self.g.box().env()
        tmp = str(fence_macos._abs(self.g.workspace) / ".tmp")
        self.assertEqual((env["TMPDIR"], env["TMP"], env["TEMP"]), (tmp, tmp, tmp))
        rw = " ".join(r for r in _rules(self.g.box().profile())
                      if r.startswith("(allow file-read* file-write*"))
        self.assertIn(f"(subpath {fence_macos._q(tmp)})", rw)

    def test_командная_строка_и_PATH_поставки_для_login_shell(self):
        box = self.g.box()
        line = box.argv(["bash", "-lc", "python3 -V"], self.g.workspace)
        self.assertEqual(line[:2], [fence_macos.SANDBOX_EXEC, "-p"])
        self.assertEqual(line[2], box.profile())
        self.assertEqual(line[3], "--")
        self.assertEqual(line[4:6], ["bash", "-lc"])
        runtime = fence_macos._abs(self.g.root) / "runtime"
        self.assertTrue(line[6].startswith("export PATH="), line[6])
        self.assertIn(str(runtime / "bin"), line[6])
        self.assertIn(str(runtime / "git" / "bin"), line[6])
        self.assertIn(':"$PATH"; ', line[6])
        # TMPDIR поставки — тоже первой командой (launchd раздаёт свой /var/folders,
        # закрытый оградой, и `mktemp` без этого падал бы Operation not permitted).
        self.assertIn("export TMPDIR=", line[6])
        self.assertIn(str(fence_macos._abs(self.g.workspace) / ".tmp"), line[6])
        self.assertTrue(line[6].endswith("; python3 -V"), line[6])
        # `sh -c` и голый argv — как есть: PATH и TMPDIR им даёт среда.
        self.assertEqual(box.argv(["/bin/sh", "-c", "ls"], self.g.workspace)[4:],
                         ["/bin/sh", "-c", "ls"])
        self.assertEqual(box.argv(["pytest", "-q"], self.g.workspace)[4:], ["pytest", "-q"])

    def test_среда_команды(self):
        with patch.dict(os.environ, {"HELENE_TOKEN": "секрет", "PRAXIS_OWNER_ID": "1",
                                     "OPENAI_API_KEY": "sk-1", "LANG": "ru_RU.UTF-8"}):
            env = self.g.box().env()
        home = fence_macos._abs(self.g.workspace)
        self.assertEqual(env["HOME"], str(home))
        self.assertEqual(env["TMPDIR"], str(home / ".tmp"))
        self.assertEqual(env["TMP"], str(home / ".tmp"))
        self.assertEqual(env["HELENE_SANDBOX"], "1")
        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(env["LANG"], "ru_RU.UTF-8")
        self.assertTrue(env["PATH"].startswith(str(fence_macos._abs(self.g.root) / "runtime" / "bin")))
        self.assertTrue(env["PATH"].endswith("/usr/bin:/bin:/usr/sbin:/sbin"))
        for leak in ("HELENE_TOKEN", "PRAXIS_OWNER_ID", "OPENAI_API_KEY"):
            self.assertNotIn(leak, env, f"{leak} утёк в среду команды")

    def test_lang_по_умолчанию_если_пусто(self):
        # GUI-процесс из Finder не получает LANG — без него питон садится на ASCII
        # и спотыкается о кириллицу. Ограда ставит en_US.UTF-8, если LANG пуст.
        clean = {k: v for k, v in os.environ.items() if k != "LANG"}
        with patch.dict(os.environ, clean, clear=True):
            self.assertEqual(self.g.box().env()["LANG"], "en_US.UTF-8")
        with patch.dict(os.environ, {"LANG": "ru_RU.UTF-8"}):
            self.assertEqual(self.g.box().env()["LANG"], "ru_RU.UTF-8", "заданный LANG не трогаем")

    def test_описание_и_без_prepare_не_запускает(self):
        box = self.g.box(network=False)
        self.assertIn("seatbelt", box.describe())
        self.assertIn("сеть нет", box.describe())
        with self.assertRaises(fence_macos.FenceUnavailable):
            box.run(["true"], self.g.workspace, 5)

    def test_нет_sandbox_exec_отказ_словами(self):
        box = self.g.box()
        with patch.object(fence_macos, "SANDBOX_EXEC", str(self.g.root / "нет-такого")):
            with self.assertRaises(fence_macos.FenceUnavailable) as caught:
                box.prepare()
        self.assertIn("sandbox-exec", str(caught.exception))
        self.assertEqual(box.sandbox_exec, "", "неподнятая ограда не считается подготовленной")

    def test_слово_контейнера_по_платформе(self):
        # Отчёт «в ограде» обязан называть механизм, а не всегда AppContainer.
        want = "AppContainer" if os.name == "nt" else ("seatbelt" if DARWIN else "bubblewrap")
        self.assertEqual(fence.container_word(), want)


class Children(unittest.TestCase):
    """Реестр живых pgid: дети команды не должны пережить движок (killpg по всем
    на atexit/мягком выходе). Здесь — чистая логика реестра, `killpg` подменён."""

    def setUp(self):
        self._saved = set(fence_macos._LIVE_PGIDS)
        fence_macos._LIVE_PGIDS.clear()
        self.addCleanup(lambda: (fence_macos._LIVE_PGIDS.clear(),
                                 fence_macos._LIVE_PGIDS.update(self._saved)))

    def test_все_живые_группы_снимаются_и_реестр_чистится(self):
        fence_macos._register_pgid(4242)
        fence_macos._register_pgid(4243)
        killed = []
        # create=True: на Windows у os нет killpg, а стенды гоняются и там.
        with patch.object(fence_macos.os, "killpg", lambda pg, sig: killed.append(pg),
                          create=True):
            fence_macos.kill_live_children()
        self.assertEqual(sorted(killed), [4242, 4243])
        self.assertEqual(fence_macos._LIVE_PGIDS, set(), "реестр очищен после снятия")

    def test_забытая_группа_не_снимается(self):
        fence_macos._register_pgid(51)
        fence_macos._forget_pgid(51)
        killed = []
        with patch.object(fence_macos.os, "killpg", lambda pg, sig: killed.append(pg),
                          create=True):
            fence_macos.kill_live_children()
        self.assertEqual(killed, [])


@unittest.skipIf(os.name == "nt", "диспетчер ограды на POSIX: на Windows ветка AppContainer")
class Dispatch(unittest.TestCase):
    """`fence.install` на darwin берёт seatbelt — и отказ его называет."""

    def test_darwin_takes_fence_macos_and_names_the_refusal(self):
        import types
        g = Ground()
        self.addCleanup(g.close)
        agent = types.SimpleNamespace(subprocess=subprocess, TOOL_IMPL={}, BASE_TOOLS=[])
        cfg = {"agent_mode": "sandbox", "sandbox": {"enabled": True, "network": True}}
        saved = dict(fence.STATE)
        self.addCleanup(lambda: (fence.STATE.clear(), fence.STATE.update(saved)))
        # Платформу подменяем ради диспетчера; sandbox-exec на Linux нет, и
        # отказ обязан прийти из fence_macos, а не из bubblewrap.
        with patch.object(sys, "platform", "darwin"), \
                patch.object(fence_macos, "SANDBOX_EXEC", str(g.root / "нет-такого")):
            fence.install(agent, g.tree, cfg, config_path=g.root / "helene.json")
        self.assertFalse(fence.STATE["container"])
        self.assertIn("sandbox-exec", fence.STATE["reason"])
        self.assertNotIn("bubblewrap", fence.STATE["reason"])


@unittest.skipUnless(DARWIN and SANDBOX, "нужен macOS с /usr/bin/sandbox-exec")
class Live(unittest.TestCase):
    """Живая ограда: то, что нельзя доказать разбором профиля."""

    def setUp(self):
        self.g = Ground()
        self.addCleanup(self.g.close)
        (self.g.tree / "memory" / "llm.json").write_text('{"key": "sk-живой"}', encoding="utf-8")
        (self.g.root / "helene.json").write_text('{"model": {"key": "sk-живой"}}', encoding="utf-8")
        self.box = self.g.box()
        try:
            self.box.prepare()
        except fence_macos.FenceUnavailable as exc:
            self.fail(f"ограда не поднялась на macOS: {exc}")

    def sh(self, command: str, timeout: float = 30, box=None):
        return (box or self.box).run(["/bin/bash", "-lc", command], self.g.workspace, timeout)

    def test_команда_идёт_и_пишет_в_дом(self):
        out, code, timed = self.sh("echo привет > note.txt && cat note.txt")
        self.assertEqual(code, 0, out)
        self.assertFalse(timed)
        self.assertIn("привет", out)
        self.assertTrue((self.g.workspace / "note.txt").is_file(),
                        "запись в доме обязана доехать до диска")

    def test_ключи_владельца_не_читаются(self):
        for secret in (self.g.root / "helene.json", self.g.tree / "memory" / "llm.json"):
            out, code, _ = self.sh(f"cat {secret}")
            self.assertNotEqual(code, 0, f"{secret} прочитался ИЗ ОГРАДЫ: {out}")
            self.assertNotIn("sk-живой", out)

    def test_память_агента_видна_и_пишется(self):
        (self.g.tree / "memory" / "note.md").write_text("это моя память", encoding="utf-8")
        out, code, _ = self.sh(f"cat {self.g.tree / 'memory' / 'note.md'} && "
                               f"echo ещё >> {self.g.tree / 'memory' / 'note.md'}")
        self.assertEqual(code, 0, out)
        self.assertIn("это моя память", out)

    def test_код_продукта_только_на_чтение(self):
        (self.g.root / "app" / "x.py").write_text("print(1)\n", encoding="utf-8")
        out, code, _ = self.sh(f"cat {self.g.root / 'app' / 'x.py'}")
        self.assertEqual(code, 0, out)
        out, code, _ = self.sh(f"echo hack >> {self.g.root / 'app' / 'x.py'}")
        self.assertNotEqual(code, 0, "код продукта записался из ограды")

    def test_чужая_папка_не_видна(self):
        outside = Path(tempfile.mkdtemp(prefix="чужое-"))
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "секрет.txt").write_text("не для агента", encoding="utf-8")
        out, code, _ = self.sh(f"cat {outside / 'секрет.txt'}")
        self.assertNotEqual(code, 0, "папка вне ограды прочиталась: " + out)
        self.assertNotIn("не для агента", out)

    def test_смонтированная_папка_открывается_по_слову_доступа(self):
        outside = Path(tempfile.mkdtemp(prefix="смонтировано-"))
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "a.txt").write_text("текст владельца", encoding="utf-8")
        link = self.g.workspace / "mnt" / "docs"
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(outside, link, target_is_directory=True)
        self.box.sync_mounts([{"path": str(outside), "real": str(outside.resolve()),
                               "link": str(link), "access": "read", "error": ""}])
        out, code, _ = self.sh(f"cat {link / 'a.txt'}")
        self.assertEqual(code, 0, out)
        self.assertIn("текст владельца", out)
        out, code, _ = self.sh(f"echo x >> {link / 'a.txt'}")
        self.assertNotEqual(code, 0, "папка на чтение записалась")

    def test_временные_папки_пользователя_закрыты_живьём(self):
        # Настоящая temp раннера (/private/var/folders/…/T) — та самая дыра
        # первого прогона: файл рядом с фикстурой не читается и не пишется…
        runner_tmp = Path(os.path.realpath(tempfile.gettempdir()))
        stray = runner_tmp / f"helene-чужой-{os.getpid()}.txt"
        stray.write_text("не для агента", encoding="utf-8")
        self.addCleanup(lambda: stray.unlink(missing_ok=True))
        out, code, _ = self.sh(f"cat {stray}")
        self.assertNotEqual(code, 0, "temp пользователя прочитался из ограды: " + out)
        self.assertNotIn("не для агента", out)
        fresh = runner_tmp / f"helene-запись-{os.getpid()}.txt"
        self.addCleanup(lambda: fresh.unlink(missing_ok=True))
        out, code, _ = self.sh(f"echo x > {fresh}")
        self.assertNotEqual(code, 0, "в temp пользователя записалось из ограды")
        self.assertFalse(fresh.exists())
        # …а свой temp команды — в доме: mktemp кладёт туда, и запись доезжает.
        # ⚠ Шестой круг CI показал живьём: TMPDIR/TMP/TEMP внутри ограды верные
        # (<workspace>/.tmp), но Apple-овский `/usr/bin/mktemp` БЕЗ шаблона всё равно
        # идёт в DARWIN_USER_TEMP_DIR (/var/folders/…/T), а он закрыт — «Operation not
        # permitted». Это свойство утилиты, не ограды: с явным шаблоном mktemp честно
        # берёт TMPDIR, и им же живут питон, git, curl, pip. Стережём то, что обещаем:
        # среда команды указывает в дом, и temp с явным путём там пишется.
        out, code, _ = self.sh('echo "TMPDIR=$TMPDIR"; f=$(mktemp "$TMPDIR/x.XXXXXX") '
                               '&& d=$(mktemp -d "$TMPDIR/d.XXXXXX") && echo "$f $d" '
                               '&& echo x > "$f" && echo y > "$d/y"')
        self.assertEqual(code, 0, out)
        tmp = str(fence_macos._abs(self.g.workspace) / ".tmp")
        self.assertIn(f"TMPDIR={tmp}", out)
        self.assertIn(f"{tmp}/x.", out)
        self.assertIn(f"{tmp}/d.", out)

    def test_питон_поставки_первый_в_PATH(self):
        fake = self.g.root / "runtime" / "bin" / "python3"
        fake.write_text("#!/bin/sh\necho питон-поставки\n", encoding="utf-8")
        fake.chmod(0o755)
        out, code, _ = self.sh("python3 -V")
        self.assertEqual(code, 0, out)
        self.assertIn("питон-поставки", out, "login-shell переставил PATH — python3 не из поставки")

    def test_без_сети_наружу_не_ходит(self):
        box = self.g.box(network=False)
        box.prepare()
        out, _code, _ = self.sh("curl -sS -m 5 http://1.1.1.1/ >/dev/null 2>&1 && "
                                "echo ЕСТЬ-СЕТЬ || echo НЕТ-СЕТИ", box=box)
        self.assertIn("НЕТ-СЕТИ", out, out)

    def test_таймаут_уносит_дерево_процессов(self):
        out, code, timed = self.box.run(["/bin/sh", "-c", "sleep 137 & sleep 137"],
                                        self.g.workspace, 2)
        self.assertTrue(timed)
        self.assertEqual(code, 124)
        # Строку про таймаут добавляет сам тул («[прервано по таймауту]») — ограда
        # своей НЕ пишет, иначе на экране две подряд (как виндовая ограда).
        self.assertNotIn("оборвано по таймауту", out)
        time.sleep(0.5)
        left = subprocess.run(["pgrep", "-f", "sleep 137"], capture_output=True, text=True)
        self.assertEqual(left.stdout.strip(), "", "внук пережил таймаут: " + left.stdout)

    def test_секрет_канала_desk_token_не_читается(self):
        # desk-token в memory (rw) — секрет канала: cat из ограды + curl отдавали
        # бы shell роль владельца. Он в fence.secret_paths, значит закрыт.
        state = self.g.tree / "memory" / ".state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "desk-token").write_text("токенканалаABC123", encoding="utf-8")
        box = self.g.box()          # secrets перечитались из fence.secret_paths
        box.prepare()
        out, code, _ = box.run(["/bin/bash", "-lc", f"cat {state / 'desk-token'}"],
                               self.g.workspace, 30)
        self.assertNotEqual(code, 0, f"desk-token прочитался из ограды: {out}")
        self.assertNotIn("токенканала", out)

    def test_ключ_тела_не_читается(self):
        """⚠ п.4 (судьи 19.09): `memory/.state/body-token` — ключ УСТРОЙСТВА.

        По нему тело пускает к экрану, мыши и клавиатуре владельца. Пока его не
        было в `fence.secret_paths`, агент читал его своей же командой `shell` и
        мог ходить в тело МИМО тула `computer` — мимо его проверок и мимо
        журнала.
        """
        state = self.g.tree / "memory" / ".state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "body-token").write_text("ключтелаXYZ789", encoding="utf-8")
        box = self.g.box()
        box.prepare()
        out, code, _ = box.run(["/bin/bash", "-lc", f"cat {state / 'body-token'}"],
                               self.g.workspace, 30)
        self.assertNotEqual(code, 0, f"body-token прочитался из ограды: {out}")
        self.assertNotIn("ключтела", out)

    def test_ответы_брокера_из_ограды_не_пишутся(self):
        """⚠ п.4: `broker-answers.json` пишет ОБОЛОЧКА, читает рука брокера.

        Файл лежит в `memory`, выданной на изменение. Агент с записью туда мог
        подделать СВОЙ ЖЕ ответ со словом `allowed` — и рука прочитала бы
        согласие, которого владелец не давал. Просьбы (`broker-asks.json`)
        закрыты тем же правилом: переписанное «зачем» — это подпись под одним, а
        выполнение другого.
        """
        state = self.g.tree / "memory" / ".state"
        state.mkdir(parents=True, exist_ok=True)
        answers = state / "broker-answers.json"
        answers.write_text('{"v": 1, "answers": []}', encoding="utf-8")
        box = self.g.box()
        box.prepare()
        out, code, _ = box.run(
            ["/bin/bash", "-lc",
             f"printf '{{\"answers\":[{{\"decision\":\"allowed\"}}]}}' > {answers}"],
            self.g.workspace, 30)
        self.assertNotEqual(code, 0, f"broker-answers.json переписался из ограды: {out}")
        self.assertNotIn("allowed", answers.read_text(encoding="utf-8"),
                         "подделанный ответ владельца доехал до диска")
        # И прочитать их тоже нельзя: id чужих просьб — это карта того, чем
        # занят владелец.
        out, code, _ = box.run(["/bin/bash", "-lc", f"cat {state / 'broker-asks.json'}"],
                               self.g.workspace, 30)
        self.assertNotEqual(code, 0, f"broker-asks.json прочитался из ограды: {out}")

    def test_git_хук_и_config_из_ограды_не_пишутся(self):
        # Побег: движок вне ограды коммитит на каждый shell без --no-verify.
        git = self.g.tree / ".git"
        (git / "hooks").mkdir(parents=True, exist_ok=True)
        (git / "objects").mkdir(parents=True, exist_ok=True)
        (git / "config").write_text("[core]\n", encoding="utf-8")
        # Читать .git можно (git внутри ограды работает)…
        out, code, _ = self.sh(f"cat {git / 'config'}")
        self.assertEqual(code, 0, out)
        # …писать в objects — тоже (git add кладёт объекты)…
        out, code, _ = self.sh(f"echo x > {git / 'objects' / 'proba'}")
        self.assertEqual(code, 0, out)
        # …а хук и config — нет: иначе движок исполнит их снаружи ограды.
        out, code, _ = self.sh(f"echo '#!/bin/sh' > {git / 'hooks' / 'pre-commit'}")
        self.assertNotEqual(code, 0, "хук записался из ограды — это побег")
        out, code, _ = self.sh(f"echo x >> {git / 'config'}")
        self.assertNotEqual(code, 0, "config записался из ограды (core.hooksPath — тот же побег)")

    def test_сеть_наружу_и_dns_живут_а_lan_bind_закрыт(self):
        # Положительный стенд: allowlist mach + DNS не должны сломать сеть.
        # Если сломают — это увидит CI, и список расширят.
        # Три попытки на код 28 (таймаут): DNS раннера macOS иногда молчит все 15 с
        # (прогон 36067936467, 25.09: «Resolving timed out»). Ограда закрывает сеть
        # СТАБИЛЬНО — все три раза подряд, и стенд по-прежнему краснеет.
        for _attempt in range(3):
            out, code, _ = self.sh("curl -fsS --max-time 15 https://github.com/robots.txt && echo OK")
            if code != 28:
                break
            time.sleep(2)
        self.assertEqual(code, 0, "исходящий HTTPS/TLS не прошёл под allowlist: " + out)
        self.assertIn("OK", out)
        out, code, _ = self.sh("python3 -c \"import socket; socket.getaddrinfo('github.com', 443); "
                               "print('DNS-OK')\"")
        self.assertEqual(code, 0, "getaddrinfo не прошёл — DNS/mDNSResponder закрыт: " + out)
        self.assertIn("DNS-OK", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
