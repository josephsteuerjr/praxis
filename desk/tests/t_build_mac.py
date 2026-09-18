# -*- coding: utf-8 -*-
"""Стенд сборки для macOS: чистые части `installer/build_mac.py` на любой ОС.

Запуск:  python tests/t_build_mac.py

Сама сборка идёт только на macOS (sips, iconutil, codesign, ditto), и до раннера
её не проверить. Здесь держится то, что от ОС не зависит и что ломается молча:
имена активов (оболочка выбирает вложение выпуска по префиксу — Mac-архив
обязан отличаться суффиксом), Info.plist обоих бандлов (идентификаторы —
контракт четырёх инженеров), шаблон конфига (одно отличие от Windows, а не
вторая копия), штамп тега в install.sh, отбор записей Windows-архива (только
tree/ и server/, только внутрь), распознавание Mach-O для ad-hoc подписи,
минимум macOS по тегам колёс, форма паспорта, обязательный состав архива —
и статика install.sh с workflow: без bash-измов, с теми ручками, что
обещаны документами.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESK = HERE.parent
sys.path.insert(0, str(DESK / "installer"))
sys.path.insert(0, str(DESK))

import build_dist  # noqa: E402
import build_mac  # noqa: E402
import deskpkg  # noqa: E402

INSTALL_SH = DESK / "installer" / "install.sh"
WORKFLOW = DESK.parent / ".github" / "workflows" / "macos.yml"


class Tags(unittest.TestCase):
    def test_version_from_tag(self):
        self.assertEqual(build_mac.version_from_tag("v0.7.1"), "0.7.1")
        self.assertEqual(build_mac.version_from_tag(" V1.2.3 "), "1.2.3")
        self.assertEqual(build_mac.version_from_tag("0.7.1"), "0.7.1")
        for bad in ("helene-1.0.0", "1.0", "", "v0.7.1-rc1"):
            with self.assertRaises(SystemExit, msg=bad):
                build_mac.version_from_tag(bad)

    def test_asset_names_differ_from_windows_by_suffix(self):
        names = build_mac.asset_names("0.7.1")
        self.assertEqual(names["zip"], "Helene-0.7.1-macos-arm64.zip")
        self.assertEqual(names["sha256"], "Helene-0.7.1-macos-arm64.zip.sha256")
        self.assertEqual(names["install"], "install.sh")
        self.assertEqual(names["windows_zip"], "Helene-0.7.1.zip")
        # Оболочка выбирает вложение по префиксу `helene-` (shell::pick_update_zip):
        # оба архива под него подходят, различает их только суффикс платформы.
        self.assertTrue(names["zip"].lower().startswith("helene-"))
        self.assertIn("-macos-arm64", names["zip"])
        self.assertNotEqual(names["zip"], names["windows_zip"])

    def test_default_release_tag_is_the_product_version(self):
        version = deskpkg.product_version()[0]
        self.assertEqual(build_mac.version_from_tag(build_mac.RELEASE_TAG_DEFAULT), version)


class Plist(unittest.TestCase):
    def test_both_bundles(self):
        want = {
            "shell": ("Helene.app", "helene", "app.helene.desk", "Hélène"),
            "setup": ("Helene Setup.app", "helene-setup", "app.helene.setup", "Hélène Setup"),
        }
        for kind, (app, exe, ident, name) in want.items():
            b = build_mac.BUNDLES[kind]
            self.assertEqual((b["app"], b["exe"], b["identifier"], b["name"]), (app, exe, ident, name))
            p = build_mac.info_plist(kind, "0.7.1")
            self.assertEqual(p["CFBundleIdentifier"], ident)
            self.assertEqual(p["CFBundleExecutable"], exe)
            self.assertEqual(p["CFBundleName"], name)
            self.assertEqual(p["CFBundleDisplayName"], name)
            self.assertEqual(p["CFBundleShortVersionString"], "0.7.1")
            self.assertEqual(p["CFBundleVersion"], "0.7.1")
            self.assertEqual(p["CFBundlePackageType"], "APPL")
            self.assertEqual(p["CFBundleIconFile"], "icon")
            self.assertEqual(p["CFBundleInfoDictionaryVersion"], "6.0")
            self.assertEqual(p["LSMinimumSystemVersion"], build_mac.MACOS_MIN)
            self.assertEqual(p["LSApplicationCategoryType"], "public.app-category.productivity")
            self.assertIs(p["NSHighResolutionCapable"], True)
            self.assertIs(p["NSAppTransportSecurity"]["NSAllowsLocalNetworking"], True)
            # plistlib обязан уметь это записать и прочитать без потерь.
            raw = plistlib.dumps(p, sort_keys=False)
            self.assertEqual(plistlib.loads(raw), p)

    def test_min_macos_is_a_version(self):
        self.assertRegex(build_mac.MACOS_MIN, r"^\d+\.\d+$")
        # Колёса голоса под cp314/arm64 собраны под macOS 14 — меньше обещать нельзя.
        self.assertGreaterEqual(build_mac.version_tuple(build_mac.MACOS_MIN), (14, 0))


class Config(unittest.TestCase):
    def test_helene_json_differs_from_windows_only_by_python(self):
        mac = json.loads(build_mac.helene_json_mac())
        win = json.loads(build_dist.HELENE_JSON)
        self.assertEqual(mac["python"], "runtime/bin/python3")
        self.assertEqual(win["python"], "runtime/python.exe")
        mac.pop("python")
        win.pop("python")
        self.assertEqual(mac, win)
        self.assertTrue(build_mac.helene_json_mac().endswith("}\n"))

    def test_stamp_install_sh(self):
        text = 'x=1\nHELENE_TAG_DEFAULT="v0.0.0"   # комментарий\nHELENE_MACOS_MIN="12"\n'
        out = build_mac.stamp_install_sh(text, "v9.9.9", "14.0")
        self.assertIn('HELENE_TAG_DEFAULT="v9.9.9"   # комментарий', out)
        self.assertIn('HELENE_MACOS_MIN="14"', out)
        with self.assertRaises(SystemExit):
            build_mac.stamp_install_sh("echo nothing\n", "v9.9.9")

    def test_real_install_sh_takes_the_stamp(self):
        text = INSTALL_SH.read_text(encoding="utf-8").replace("\r\n", "\n")
        out = build_mac.stamp_install_sh(text, "v9.9.9")
        self.assertRegex(out, r'(?m)^HELENE_TAG_DEFAULT="v9\.9\.9"')
        self.assertRegex(out, r'(?m)^HELENE_MACOS_MIN="%s"' % build_mac.MACOS_MIN.split(".")[0])
        # Штамп в репозитории и умолчание сборки — одно и то же число.
        self.assertRegex(text, r'(?m)^HELENE_TAG_DEFAULT="%s"' % re.escape(build_mac.RELEASE_TAG_DEFAULT))


class ArchiveMembers(unittest.TestCase):
    NAMES = [
        "Helene/", "Helene/helene.exe", "Helene/helene-build.json",
        "Helene/tree/", "Helene/tree/agent.py", "Helene/tree/core/secrets.py",
        "Helene/tree/LICENSE", "Helene/server/Dockerfile", "Helene/server/desk-recipe/x.py",
        "Helene/app/deskapp.py", "Helene/runtime/python.exe", "Helene/data/",
        "Helene/licenses/rust/README.md", "Helene/ПЕРВЫЙ-ЗАПУСК.md",
        "other/tree/x.py",
    ]

    def test_only_tree_and_server_inside_the_top_folder(self):
        plan = build_mac.zip_members_for(self.NAMES)
        self.assertEqual(plan, {
            "Helene/tree/agent.py": "tree/agent.py",
            "Helene/tree/core/secrets.py": "tree/core/secrets.py",
            "Helene/tree/LICENSE": "tree/LICENSE",
            "Helene/server/Dockerfile": "server/Dockerfile",
            "Helene/server/desk-recipe/x.py": "server/desk-recipe/x.py",
        })
        # app/ из архива не берётся никогда: пакет desk собирается заново.
        self.assertNotIn("app/", build_mac.FROM_RELEASE_DIRS)

    def test_refuses_to_climb_out(self):
        for bad in ("Helene/tree/../x.py", "Helene/tree//x.py", "Helene/tree/./x.py"):
            with self.assertRaises(SystemExit, msg=bad):
                build_mac.zip_members_for(["Helene/tree/ok.py", bad])

    def test_tree_added_are_the_two_apache_files(self):
        self.assertEqual(build_mac.TREE_ADDED, ("tree/LICENSE", "tree/NOTICE"))

    def test_core_summary_drops_owner_paths(self):
        passport = {"core": {
            "core": {"path": "C:\\Users\\x\\praxis", "files": 550, "digest": "abc", "head": "0453d75", "dirty": False},
            "layer": {"path": "C:\\Users\\x\\helene\\core", "files": 61, "digest": "def", "names": ["a.py"]},
            "drift": {"undeclared": 1, "stale": 0, "names_undeclared": ["a.py"]},
        }}
        got = build_mac.core_summary(passport)
        self.assertEqual(got["core"], {"files": 550, "digest": "abc", "head": "0453d75", "dirty": False})
        self.assertEqual(got["layer"], {"files": 61, "digest": "def"})
        self.assertEqual(got["drift"], {"undeclared": 1, "stale": 0})
        self.assertNotIn("path", json.dumps(got))
        self.assertIsNone(build_mac.core_summary({}))
        self.assertIsNone(build_mac.core_summary({"core": None}))


class MachO(unittest.TestCase):
    def test_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = {
                "thin64": (b"\xcf\xfa\xed\xfe" + b"\x0c\x00\x00\x01" + b"\0" * 32, True),
                "thin32": (b"\xce\xfa\xed\xfe" + b"\0" * 36, True),
                "fat": (b"\xca\xfe\xba\xbe" + (2).to_bytes(4, "big") + b"\0" * 32, True),
                "java_class": (b"\xca\xfe\xba\xbe" + b"\x00\x00\x00\x41" + b"\0" * 32, False),
                "text": (b"#!/bin/sh\necho hi\n", False),
                "short": (b"\xcf\xfa", False),
                "empty": (b"", False),
            }
            for name, (raw, want) in cases.items():
                p = root / name
                p.write_bytes(raw)
                self.assertEqual(build_mac.is_mach_o(p), want, name)
            self.assertFalse(build_mac.is_mach_o(root / "nope"))

    def test_sign_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Helene.app" / "Contents" / "MacOS").mkdir(parents=True)
            (root / "Helene.app" / "Contents" / "MacOS" / "helene").write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 8)
            (root / "helene-relay").write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 8)
            so = root / "runtime" / "lib" / "python3.14" / "site-packages" / "x" / "ext.so"
            so.parent.mkdir(parents=True)
            so.write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 8)
            (root / "runtime" / "lib" / "python3.14" / "site-packages" / "x" / "mod.py").write_text("x = 1\n")
            (root / "runtime" / "git").mkdir()
            (root / "runtime" / "git" / "git").write_bytes(b"\xca\xfe\xba\xbe" + (1).to_bytes(4, "big") + b"\0" * 8)
            got = [p.relative_to(root).as_posix() for p in build_mac.sign_targets(root)]
            self.assertEqual(got, ["Helene.app", "helene-relay",
                                   "runtime/git/git", "runtime/lib/python3.14/site-packages/x/ext.so"])
            # Бандла мастера нет — и в списке его нет: список по факту, не по плану.
            self.assertNotIn("Helene Setup.app", got)


class Wheels(unittest.TestCase):
    def test_macos_floor_of_tags(self):
        tags = ["py3-none-any", "cp314-cp314-macosx_11_0_arm64",
                "cp311-abi3-macosx_14_0_arm64", "cp314-cp314-macosx_10_15_universal2"]
        self.assertEqual(build_mac.macos_floor_of_tags(tags), "14.0")
        self.assertEqual(build_mac.macos_floor_of_tags(["py3-none-any"]), "")
        self.assertEqual(build_mac.macos_floor_of_tags([]), "")
        self.assertEqual(build_mac.version_tuple("14.0"), (14, 0))
        self.assertGreater(build_mac.version_tuple("14.0"), build_mac.version_tuple("12.0"))

    def test_sdist_exception_is_only_pyaes(self):
        # Проверено `pip download` 19.09.2026: колёса cp314/arm64 есть у всего
        # состава, кроме pyaes (чистый Python, зависимость telethon).
        self.assertEqual(build_mac.SDIST_OK, ("pyaes",))

    def test_deps_follow_the_declared_flavor(self):
        if not hasattr(deskpkg, "MACOS"):
            with self.assertRaises(SystemExit):
                build_mac.deps()
            return
        got = build_mac.deps()
        for dep in build_dist.TREE_DEPS + build_dist.VOICE_DEPS:
            self.assertIn(dep, got)
        self.assertEqual(got[len(build_dist.TREE_DEPS) + len(build_dist.VOICE_DEPS):],
                         deskpkg.requirements(deskpkg.MACOS))


class Downloads(unittest.TestCase):
    def test_pins(self):
        self.assertEqual(len(build_mac.SHA256), 2)
        for url, digest in build_mac.SHA256.items():
            self.assertRegex(digest, r"^[0-9a-f]{64}$", url)
            self.assertTrue(url.startswith("https://"), url)
        self.assertIn(build_mac.PY_VERSION, build_mac.PBS_URL)
        self.assertIn("aarch64-apple-darwin", build_mac.PBS_URL)
        self.assertIn(build_mac.PBS_TAG, build_mac.PBS_URL)
        self.assertTrue(build_mac.PBS_URL.endswith("/" + build_mac.PBS_NAME))
        self.assertIn(build_mac.GIT_VERSION, build_mac.GIT_URL)
        self.assertTrue(build_mac.GIT_URL.endswith("/" + build_mac.GIT_NAME))
        self.assertRegex(build_mac.RELAY_COMMIT, r"^[0-9a-f]{40}$")
        self.assertEqual(build_mac.RELAY_BIN, "codex-proxy-server")

    def test_git_make_vars_keep_it_portable(self):
        for var in ("RUNTIME_PREFIX=YesPlease", "prefix=/", "NO_PERL=1", "NO_TCLTK=1",
                    "APPLE_COMMON_CRYPTO=1", "NO_OPENSSL=1", "SKIP_DASHED_BUILT_INS=YesPlease"):
            self.assertIn(var, build_mac.GIT_MAKE_VARS)


class Passport(unittest.TestCase):
    def _staged(self):
        return {"tree_files": 234, "static_digest": "d0" * 32,
                "desk": {"version": "0.7.1", "flavor": "macos", "digest": "ab" * 32,
                         "files": {"a": "1", "b": "2"}, "skipped": []}}

    def test_shape(self):
        p = build_mac.build_passport(
            version="0.7.1", declared={"shell/Cargo.toml": "0.7.1"},
            desk_head="abc", desk_dirty=False, tree_head="3b297aa4", tree_dirty=False,
            source_release={"tag": "v0.7.1", "asset": "Helene-0.7.1.zip", "sha256": "ff" * 32,
                            "tree_files": 234, "passport": {}, "core": {"drift": {"undeclared": 0}}},
            staged=self._staged(), relay={"commit": build_mac.RELAY_COMMIT}, freeze="a==1\nb==2",
            downloads={build_mac.PBS_NAME: "x"}, complete=True, partial_reason=[], signed=3,
            git_bundle={"version": "2.55.0"}, macos_floor="14.0")
        for key in ("product", "version", "built_utc", "complete", "git", "python", "packages",
                    "downloads", "desk", "relay", "tree_files", "static", "declared_versions"):
            self.assertIn(key, p, key)
        self.assertEqual(p["platform"], "macos")
        self.assertEqual(p["arch"], "arm64")
        self.assertEqual(p["product"], "Hélène")
        self.assertEqual(p["python"], build_mac.PY_VERSION)
        self.assertEqual(p["git_bundle"], {"version": "2.55.0"})
        self.assertEqual(p["macos_min"], build_mac.MACOS_MIN)
        self.assertEqual(p["macos_floor_wheels"], "14.0")
        self.assertEqual(p["source_release"]["tag"], "v0.7.1")
        self.assertEqual(p["core"], {"drift": {"undeclared": 0}})
        self.assertEqual(p["git"], {"desk": "abc", "desk_dirty": False, "tree": "3b297aa4",
                                    "tree_dirty": False, "dirty": False})
        self.assertEqual(p["packages"], ["a==1", "b==2"])
        self.assertEqual(p["desk"]["files"], 2)
        self.assertEqual(p["bundles"], {"Helene.app": "app.helene.desk", "Helene Setup.app": "app.helene.setup"})
        self.assertEqual(p["signed_adhoc"], 3)
        self.assertRegex(p["built_utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        json.dumps(p, ensure_ascii=False)

    def test_dirty_is_either_side(self):
        p = build_mac.build_passport(
            version="0.7.1", declared={}, desk_head="abc-dirty", desk_dirty=True,
            tree_head="def", tree_dirty=False, source_release=None, staged=self._staged(),
            relay=None, freeze="", downloads={}, complete=False, partial_reason=["helene-relay — нет"],
            signed=0, git_bundle=None, macos_floor="")
        self.assertTrue(p["git"]["dirty"])
        self.assertFalse(p["complete"])
        self.assertIsNone(p["core"])
        self.assertIsNone(p["source_release"])


class Composition(unittest.TestCase):
    def test_required_root_names_the_contract(self):
        req = build_mac.REQUIRED_ROOT
        for rel in ("Helene.app/Contents/MacOS/helene", "Helene Setup.app/Contents/MacOS/helene-setup",
                    "helene-relay", "runtime/bin/python3", "runtime/git/bin/git",
                    "helene.json", "helene-build.json", "install.sh", "tree", "data",
                    "ПЕРВЫЙ-ЗАПУСК.md", "ОБНОВЛЕНИЕ.md", "КАК-УСТРОЕН-HELENE.md",
                    "ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md", "NOTICE"):
            self.assertIn(rel, req, rel)
        # Windows-состава здесь нет: ни exe, ни службы, ни тела.
        for rel in req:
            self.assertFalse(rel.endswith(".exe"), rel)
            self.assertNotIn("svc", rel)
            self.assertNotIn("body", rel)

    def test_missing_in_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "helene.json").write_text("{}")
            (root / "tree").mkdir()
            missing = build_mac.missing_in_root(root)
            self.assertNotIn("helene.json", missing)
            self.assertNotIn("tree", missing)
            self.assertIn("helene-build.json", missing)
            self.assertIn("Helene.app/Contents/MacOS/helene", missing)

    def test_sha256_line_matches_windows_format(self):
        self.assertEqual(build_mac.sha256_line("ab" * 32, "Helene-0.7.1-macos-arm64.zip"),
                         "ab" * 32 + " *Helene-0.7.1-macos-arm64.zip\n")


class Texts(unittest.TestCase):
    def test_first_run_names_what_is_absent(self):
        text = build_mac.FIRST_RUN_MAC
        for word in ("computer", "служб", "брокер", "Intel", "Developer ID", "нотариз", "dmg",
                     "брандмауэр", "seatbelt", "~/Applications/Helene", "install.sh", "ОБНОВЛЕНИЕ.md"):
            self.assertIn(word, text, word)
        self.assertNotIn("SmartScreen", text)
        # Windows-имена здесь допустимы только в списке того, чего нет.
        head = text.split("Чего в сборке для macOS нет")[0]
        self.assertNotIn(".exe", head)

    def test_third_party_is_the_mac_composition(self):
        text = build_mac.THIRD_PARTY_MAC
        self.assertNotIn("__GIT_VERSION__", text)
        self.assertIn(build_mac.GIT_VERSION, text)
        for word in ("GPL-2.0", "licenses/rust/", "licenses/relay/", "python-build-standalone",
                     "runtime/lib/python3.14/LICENSE.txt", "faster-whisper", "piper-tts", "Apache-2.0"):
            self.assertIn(word, text, word)
        # Windows-только компоненты названы как ОТСУТСТВУЮЩИЕ, а не как состав.
        self.assertIn("Чего в этой поставке нет", text)
        self.assertNotIn("MinGit, минимальная сборка", text)


class InstallSh(unittest.TestCase):
    def setUp(self):
        self.text = INSTALL_SH.read_text(encoding="utf-8")

    def test_posix_sh_without_bashisms(self):
        self.assertTrue(self.text.startswith("#!/bin/sh\n"))
        self.assertNotIn("\r", self.text, "CRLF в sh-скрипте — /bin/sh падает на первой строке")
        body = "\n".join(line for line in self.text.splitlines() if not line.lstrip().startswith("#"))
        for bashism in ("[[ ", "function ", "declare ", "local ", "${BASH", "=~", "&>", "pushd", "popd", "source "):
            self.assertNotIn(bashism, body, bashism)
        self.assertIn("set -eu", body)

    def test_promised_handles(self):
        for needle in ("--from", "--relaunch", "--uninstall", "--purge", "--help",
                       "HELENE_TAG", "uname -m", "sw_vers -productVersion", "hw.optional.arm64",
                       "shasum -a 256", "ditto -x -k", "xattr -dr com.apple.quarantine",
                       "Helene Setup.app/Contents/MacOS/helene-setup", "--install", "--quiet",
                       "Library/Caches/app.helene.desk", "staging", "pgrep -f",
                       "open \"$HOME_DIR/Helene.app\"", "releases/download"):
            self.assertIn(needle, self.text, needle)
        # Все пути с пробелом (Helene Setup.app) — только в кавычках.
        for line in self.text.splitlines():
            if "Helene Setup.app" in line and not line.lstrip().startswith("#"):
                self.assertRegex(line, r'"[^"]*Helene Setup\.app', line)

    def test_update_keeps_the_owner_decisions(self):
        # Тихое обновление берёт решения из установленного helene.json, а не
        # из умолчаний мастера: имя агента, владельца, модель, режим, Telegram.
        for key in ('"agent"', '"owner"', '"constitution"', '"provider"', '"agent_mode"',
                    '"telegram"', '"computer"', '"dir"', "sk-frame-", "SOUL.md"):
            self.assertIn(key, self.text, key)
        self.assertIn("umask 077", self.text)


class Workflow(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_static(self):
        for needle in ("workflow_dispatch", "macos-15", "build_mac.py", "--from-release",
                       "dtolnay/rust-toolchain@stable", "Swatinem/rust-cache@v2", "actions/setup-node@v4",
                       "actions/upload-artifact@v4", "gh release upload", "--clobber",
                       "cargo test --features custom-protocol", "t_fence_macos.py",
                       "--install", "--quiet", "screencapture", "/api/health", "/api/state",
                       "desk-token", "helene-build.json", "if: always()"):
            self.assertIn(needle, self.text, needle)
        self.assertNotIn("\t", self.text, "табуляция в YAML")

    def test_yaml_parses_if_pyyaml_is_around(self):
        try:
            import yaml  # noqa: PLC0415
        except ImportError:
            self.skipTest("PyYAML не установлен — структура не разбиралась")
        doc = yaml.safe_load(self.text)
        # `on` в YAML 1.1 читается как True — PyYAML так и делает.
        trigger = doc.get("on", doc.get(True))
        inputs = trigger["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["tag"]["default"], build_mac.RELEASE_TAG_DEFAULT)
        self.assertEqual(inputs["upload"]["type"], "boolean")
        self.assertIs(inputs["upload"]["default"], False)
        job = doc["jobs"]["build"]
        self.assertEqual(job["runs-on"], "macos-15")
        names = [s.get("name") or s.get("uses") for s in job["steps"]]
        self.assertEqual(names[0], "actions/checkout@v4")
        self.assertTrue(any("gh release upload" in (s.get("run") or "") for s in job["steps"]))
        upload = next(s for s in job["steps"] if "gh release upload" in (s.get("run") or ""))
        self.assertIn("inputs.upload", str(upload.get("if")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
