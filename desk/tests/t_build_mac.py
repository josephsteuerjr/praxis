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

    def test_resolve_tags_names_the_build_by_tag_and_takes_the_tree_from_release(self):
        d = build_mac.RELEASE_TAG_DEFAULT
        self.assertEqual(build_mac.resolve_tags("", "", ""), (d, d))
        self.assertEqual(build_mac.resolve_tags("v0.7.2", "", ""), ("v0.7.2", "v0.7.2"))
        # проверочный прогон CI: дерево из прежнего выпуска, имя — новое
        self.assertEqual(build_mac.resolve_tags("v0.7.2", "v0.8.0", ""), ("v0.7.2", "v0.8.0"))
        # дерево с диска: выпуска нет, имя — как попросили или пустое
        self.assertEqual(build_mac.resolve_tags("", "", "/x/tree"), ("", ""))
        self.assertEqual(build_mac.resolve_tags("", "v0.8.0", "/x/tree"), ("", "v0.8.0"))

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

    def test_shell_bundle_asks_for_the_microphone_and_refuses_app_nap(self):
        # Адверсарка 19.09: без NSMicrophoneUsageDescription TCC убивает процесс
        # на первом getUserMedia; App Nap усыпил бы сторожей детей и проверку
        # обновлений, которые спят на thread::sleep за спрятанным окном.
        shell = build_mac.info_plist("shell", "0.7.1")
        self.assertEqual(shell["NSMicrophoneUsageDescription"],
                         "Hélène записывает голосовые сообщения для агента")
        self.assertIs(shell["LSAppNapIsDisabled"], True)
        setup = build_mac.info_plist("setup", "0.7.1")
        self.assertNotIn("NSMicrophoneUsageDescription", setup)
        self.assertNotIn("LSAppNapIsDisabled", setup)

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
            # Служба, мост и тело — свободные бинари корня, как реле:
            # подписывается каждый. Неподписанный arm64-бинарь система убивает
            # при запуске, и launchd не исключение.
            (root / "helene-svc").write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 8)
            (root / "helene-bridge").write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 8)
            (root / "helene-body").write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 8)
            so = root / "runtime" / "lib" / "python3.14" / "site-packages" / "x" / "ext.so"
            so.parent.mkdir(parents=True)
            so.write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 8)
            (root / "runtime" / "lib" / "python3.14" / "site-packages" / "x" / "mod.py").write_text("x = 1\n")
            (root / "runtime" / "git").mkdir()
            (root / "runtime" / "git" / "git").write_bytes(b"\xca\xfe\xba\xbe" + (1).to_bytes(4, "big") + b"\0" * 8)
            got = [p.relative_to(root).as_posix() for p in build_mac.sign_targets(root)]
            self.assertEqual(got, ["Helene.app", "helene-relay", "helene-svc", "helene-bridge", "helene-body",
                                   "runtime/git/git", "runtime/lib/python3.14/site-packages/x/ext.so"])
            # Бандла мастера нет — и в списке его нет: список по факту, не по плану.
            self.assertNotIn("Helene Setup.app", got)
        self.assertEqual(build_mac.ROOT_BINARIES,
                         ("helene-relay", "helene-svc", "helene-bridge", "helene-body"))


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


class Stands(unittest.TestCase):
    def test_all_five_fronts_are_built(self):
        # Стенд пакета desk собирает пакет каждого вида, серверному нужны
        # remote/dist и miniapp/dist — фронты строятся все, не три «для архива».
        self.assertEqual(set(build_mac.FRONTS), {"app", "mobile", "setup/ui", "remote", "miniapp"})
        for rel in build_mac.FRONTS:
            self.assertTrue((DESK / rel / "package-lock.json").is_file(), rel)
            scripts = json.loads((DESK / rel / "package.json").read_text(encoding="utf-8")).get("scripts", {})
            self.assertIn("build", scripts, rel)

    def test_workflow_caches_every_front_lock(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for rel in build_mac.FRONTS:
            self.assertIn(f"desk/{rel}/package-lock.json", text, rel)

    def test_stands_get_the_tree_from_the_build(self):
        # Стенды ищут дерево через layout.tree(): без HELENE_TREE_SRC на раннере
        # они шли бы к соседу live/, которого там нет (второй круг CI, 19.09).
        import inspect  # noqa: PLC0415
        src = inspect.getsource(build_mac.run_stands)
        self.assertIn('"HELENE_TREE_SRC": str(tree)', src)
        self.assertIn("**os.environ", src)
        self.assertIn("env=env", src)


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
                    "APPLE_COMMON_CRYPTO=1", "NO_OPENSSL=1", "SKIP_DASHED_BUILT_INS=YesPlease",
                    # Только системные библиотеки: на Darwin 24+ git сам тянет
                    # libiconv из Homebrew, а curl — через curl-config из PATH.
                    "NO_HOMEBREW=1", "CURL_LDFLAGS=-lcurl", "CURL_CONFIG=/usr/bin/true"):
            self.assertIn(var, build_mac.GIT_MAKE_VARS, var)
        self.assertEqual(build_mac.SYSTEM_DYLIB_PREFIXES, ("/usr/lib/", "/System/"))

    def test_foreign_dylibs(self):
        clean = ("runtime/git/libexec/git-core/git-remote-http:\n"
                 "\t/usr/lib/libcurl.4.dylib (compatibility version 7.0.0, current version 9.0.0)\n"
                 "\t/usr/lib/libz.1.dylib (compatibility version 1.0.0, current version 1.2.12)\n"
                 "\t/System/Library/Frameworks/CoreServices.framework/Versions/A/CoreServices (compatibility version 1.0.0, current version 1226.0.0)\n"
                 "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1351.0.0)\n")
        self.assertEqual(build_mac.foreign_dylibs(clean), [])
        brew = clean + "\t/opt/homebrew/opt/libiconv/lib/libiconv.2.dylib (compatibility version 9.0.0, current version 9.1.0)\n" \
                       "\t@rpath/libfoo.dylib (compatibility version 1.0.0, current version 1.0.0)\n"
        self.assertEqual(build_mac.foreign_dylibs(brew),
                         ["/opt/homebrew/opt/libiconv/lib/libiconv.2.dylib", "@rpath/libfoo.dylib"])
        self.assertEqual(build_mac.foreign_dylibs(""), [])

    def test_vtool_minos(self):
        text = ("runtime/git/bin/git:\nLoad command 10\n      cmd LC_BUILD_VERSION\n  cmdsize 32\n"
                " platform MACOS\n    minos 14.0\n      sdk 15.2\n   ntools 1\n     tool LD\n  version 1115.7.3\n")
        self.assertEqual(build_mac.vtool_minos(text), "14.0")
        self.assertEqual(build_mac.vtool_minos("no build version here"), "")
        self.assertLessEqual(build_mac.version_tuple("14.0"), build_mac.version_tuple(build_mac.MACOS_MIN))


class Passport(unittest.TestCase):
    def _staged(self):
        return {"tree_files": 234, "static_digest": "d0" * 32,
                "desk": {"version": "0.7.1", "flavor": "macos", "digest": "ab" * 32,
                         "files": {"a": "1", "b": "2"}, "skipped": []}}

    def test_shape(self):
        body = build_mac.body_summary(mirror={"head": "3c6b8e35", "taken_at": "t", "dirty": False},
                                      crates=build_mac.BODY_CRATES, digest="cd" * 32, files=40,
                                      exe_sha256={"helene-body": "ee" * 32}, target_dir="/tmp/body-target")
        p = build_mac.build_passport(
            version="0.7.1", declared={"shell/Cargo.toml": "0.7.1"},
            desk_head="abc", desk_dirty=False, tree_head="3b297aa4", tree_dirty=False,
            source_release={"tag": "v0.7.1", "asset": "Helene-0.7.1.zip", "sha256": "ff" * 32,
                            "tree_files": 234, "passport": {}, "core": {"drift": {"undeclared": 0}}},
            staged=self._staged(), relay={"commit": build_mac.RELAY_COMMIT}, freeze="a==1\nb==2",
            downloads={build_mac.PBS_NAME: "x"}, complete=True, partial_reason=[], signed=3,
            git_bundle={"version": "2.55.0"}, macos_floor="14.0", body=body)
        for key in ("product", "version", "built_utc", "complete", "git", "python", "packages",
                    "downloads", "desk", "relay", "body", "tree_files", "static", "declared_versions"):
            self.assertIn(key, p, key)
        self.assertEqual(p["body"]["commit"], "3c6b8e35")
        self.assertEqual(p["body"]["crates"], ["praxis-body", "praxis-bridge"])
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
        # Без тела (--skip-body) поле есть и пусто — паспорт не молчит о составе.
        self.assertIn("body", p)
        self.assertIsNone(p["body"])


class Composition(unittest.TestCase):
    def test_required_root_names_the_contract(self):
        req = build_mac.REQUIRED_ROOT
        for rel in ("Helene.app/Contents/MacOS/helene", "Helene Setup.app/Contents/MacOS/helene-setup",
                    "helene-relay", "runtime/bin/python3", "runtime/git/bin/git",
                    "runtime/git/COPYING",   # GPL-2.0: текст лицензии рядом, make install его не кладёт
                    # 0.8.0: тело тула computer — мост, тело, их лицензии и модуль движка;
                    # служба без входа в систему — `helene-svc` (демон launchd).
                    "helene-bridge", "helene-body", "licenses/body/README.md", "app/localharness/body.py",
                    "helene-svc",
                    "helene.json", "helene-build.json", "install.sh", "tree", "data",
                    "ПЕРВЫЙ-ЗАПУСК.md", "ОБНОВЛЕНИЕ.md", "КАК-УСТРОЕН-HELENE.md",
                    "ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md", "NOTICE"):
            self.assertIn(rel, req, rel)
        # Windows-имён здесь нет: `.exe` не бывает ни у одного файла поставки.
        # Служба здесь ЕСТЬ (0.8.0) — но без суффикса и без скриптов PowerShell:
        # install-service.ps1 и uninstall-service.ps1 на Mac не значат ничего.
        for rel in req:
            self.assertFalse(rel.endswith(".exe"), rel)
            self.assertFalse(rel.endswith(".ps1"), rel)
        self.assertIn("helene-svc", req)
        # Что корень получает от тела — ровно то, что --skip-body вправе не ждать.
        self.assertEqual(build_mac.BODY_ROOT_ENTRIES, ("helene-bridge", "helene-body", "licenses/body/README.md"))
        for rel in build_mac.BODY_ROOT_ENTRIES:
            self.assertIn(rel, req, rel)

    def test_the_service_binary_comes_from_its_own_crate(self):
        """Служба собирается голым cargo из `desk/svc` — тем же крейтом, что на
        Windows. Разойдись имена, в поставке оказался бы не тот бинарь (или не
        оказалось бы вовсе, а `missing_in_root` сказал бы об этом уже в конце)."""
        self.assertEqual(build_mac.SVC_BIN, "helene-svc", "имя службы без `.exe` — это macOS")
        self.assertEqual(build_mac.SVC_CRATE.name, "svc")
        self.assertTrue((build_mac.SVC_CRATE / "Cargo.toml").is_file(), "крейта службы нет на месте")
        # Бандлом служба НЕ собирается: окна у неё нет и быть не должно.
        self.assertNotIn("svc", build_mac.BUNDLES)

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
        for word in ("computer", "Intel", "Developer ID", "нотариз", "dmg",
                     "брандмауэр", "seatbelt", "~/Applications/Helene", "install.sh", "ОБНОВЛЕНИЕ.md"):
            self.assertIn(word, text, word)
        self.assertNotIn("SmartScreen", text)
        # Windows-имён на Mac нет нигде: тело здесь — helene-body и helene-bridge без .exe.
        self.assertNotIn(".exe", text)
        # Список отсутствующего — от заголовка до абзаца об ограде.
        absent = text.split("Чего в сборке для macOS нет")[1].split("Ограда тула")[0]
        for word in ("Intel", "Developer ID", "брандмауэр"):
            self.assertIn(word, absent, word)
        # 0.8.0: тело, брокер и СЛУЖБА есть — в списке отсутствующего их быть
        # не должно. Нулевая сессия — должна: её на Mac нет как механизма.
        self.assertNotIn("тела", absent)
        self.assertNotIn("брокер", absent)
        self.assertNotIn("LaunchDaemon", absent)
        self.assertNotIn("службы без входа", absent)
        self.assertIn("нулевой сессии", absent)

    def test_first_run_explains_the_service(self):
        """§6 плана 19.09: что служба даёт, чего не даёт и как её снять.

        ⛔ «На macOS этого нет» на экран не пишем — а вот в документе поставки
        обязаны быть названы ОБЕ границы режима: окон у него нет, и при
        FileVault до первого входа не идёт ничего.
        """
        text = build_mac.FIRST_RUN_MAC
        for word in ("## Работать без входа в систему", "launchd", "app.helene.svc",
                     "/Library/LaunchDaemons", "пароль администратора",
                     "окон и экрана у неё нет", "FileVault", "data/service.log",
                     "launchctl bootout system/app.helene.svc",
                     "Снять службу", "Поставить службу"):
            self.assertIn(word, text, word)
        # Демон идёт от имени владельца — не от root: это несущее свойство.
        self.assertIn("не от root", text)
        # И чек-лист человека с Маком спрашивает про службу тоже.
        checks = text.split("## Что проверить руками")[1]
        self.assertIn("Служба", checks)
        self.assertIn("Telegram", checks)

    def test_first_run_explains_the_body_permissions_and_the_broker(self):
        # План 19.09 §5 и §3(D): два разрешения и где они, ⚠ после каждого
        # обновления они слетают (ad-hoc) — убрать из списка и добавить снова;
        # брокер — диалог пароля macOS; чек-лист прокликки для человека с Маком.
        text = build_mac.FIRST_RUN_MAC
        for word in ("## Управление компьютером", "helene-body", "helene-bridge",
                     "Запись экрана и системного звука", "Универсальный доступ",
                     "Конфиденциальность и безопасность", "Открыть настройки",
                     "После каждого обновления", "ad-hoc", "убрать", "«−»", "добавить снова",
                     "## Права администратора", "пароль", "with\nadministrator privileges",
                     "## Что проверить руками", "сделай снимок экрана", "прочитай окно Finder",
                     "нажми кнопку", "попроси права", "снаружи ограды", "не врёт"):
            self.assertIn(word, text, word)
        # Честность: живьём человеком не проверено — так и написано.
        self.assertIn("не прогонялось", text)
        # Порядок разделов: включение → разрешения → брокер → чек-лист → чего нет.
        order = [text.index(s) for s in ("## Управление компьютером", "## Права администратора",
                                         "## Что проверить руками", "Чего в сборке для macOS нет")]
        self.assertEqual(order, sorted(order))
        # Судья 19.09: на Sequoia обхода правой кнопкой нет — путь через
        # Настройки; скачанный браузером архив отдаётся скрипту (--from), а не
        # открывается бандлами; первым открывают мастер, не Helene.app.
        self.assertIn("Открыть всё равно", text)
        self.assertIn("--from", text)
        self.assertIn("releases/latest/download/install.sh", text)
        self.assertNotRegex(text, r"правая кнопка по\s+`Helene\.app`")
        self.assertIn("Первым открывается мастер", text)

    def test_third_party_is_the_mac_composition(self):
        text = build_mac.THIRD_PARTY_MAC
        self.assertNotIn("__GIT_VERSION__", text)
        self.assertIn(build_mac.GIT_VERSION, text)
        for word in ("GPL-2.0", "licenses/rust/", "licenses/relay/", "licenses/body/",
                     "python-build-standalone", "runtime/lib/python3.14/LICENSE.txt",
                     "faster-whisper", "piper-tts", "Apache-2.0"):
            self.assertIn(word, text, word)
        # Тело: те же слова о лицензии, что у Windows (installer/THIRD-PARTY.md) —
        # код тела Apache-2.0 по решению автора, поле PolyForm в манифесте устарело.
        body = text.split("## Тело тула `computer`")[1].split("## ")[0]
        for word in ("praxis/body", "praxis-body-protocol", "core-graphics", "PolyForm-Noncommercial-1.0.0",
                     "Apache-2.0", "27.08.2026", "licenses/body/README.md"):
            self.assertIn(word, body, word)
        # Windows-только компоненты названы как ОТСУТСТВУЮЩИЕ, а не как состав;
        # тело и мост — уже состав, в этом списке их нет.
        self.assertIn("Чего в этой поставке нет", text)
        absent = text.split("Чего в этой поставке нет")[1]
        self.assertNotIn("helene-body", absent)
        self.assertNotIn("helene-bridge", absent)
        self.assertNotIn("MinGit, минимальная сборка", text)


class Body(unittest.TestCase):
    """Тело тула `computer` в сборке: исходник в репозитории, отпечаток, паспорт,
    лицензии, флаг полусборки. Сам cargo идёт только на macOS (workflow)."""

    def test_source_is_in_the_repository_and_names_match_the_engine(self):
        self.assertTrue((build_mac.BODY_SRC / "Cargo.toml").is_file(), build_mac.BODY_SRC)
        self.assertTrue((build_mac.BODY_SRC / "Cargo.lock").is_file(), "без Cargo.lock лицензии не собрать")
        for crate in build_mac.BODY_CRATES:
            self.assertTrue((build_mac.BODY_SRC / "crates" / crate / "Cargo.toml").is_file(), crate)
        self.assertEqual(build_mac.BODY_CRATES, ("praxis-body", "praxis-bridge"))
        self.assertEqual(build_mac.BODY_BINARIES, {"praxis-bridge": "helene-bridge", "praxis-body": "helene-body"})
        # Имена без .exe — те же, что объявляет движок на darwin (localharness/body.py).
        sys.path.insert(0, str(DESK / "localharness"))
        import body as engine_body  # noqa: PLC0415
        if sys.platform == "darwin":
            self.assertEqual({engine_body.BRIDGE_EXE, engine_body.BODY_EXE}, set(build_mac.BODY_BINARIES.values()))
        else:
            for name in build_mac.BODY_BINARIES.values():
                self.assertIn(name, (engine_body.BRIDGE_EXE.replace(".exe", ""), engine_body.BODY_EXE.replace(".exe", "")))
        self.assertTrue(build_mac.CORE_SOURCE.is_file(), "нет CORE-SOURCE.json — паспорт тела без коммита зеркала")

    def test_source_files_and_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            (src / "crates" / "praxis-body" / "src" / "sub").mkdir(parents=True)
            (src / "target" / "release").mkdir(parents=True)
            (src / "Cargo.toml").write_text("[workspace]\n")
            (src / "Cargo.lock").write_text("# lock\n")
            (src / "README.md").write_text("не исходник\n")
            (src / "crates" / "praxis-body" / "Cargo.toml").write_text("[package]\n")
            (src / "crates" / "praxis-body" / "src" / "main.rs").write_text("fn main() {}\n")
            (src / "crates" / "praxis-body" / "src" / "sub" / "m.rs").write_text("pub fn f() {}\n")
            (src / "target" / "release" / "praxis-body").write_bytes(b"\xcf\xfa\xed\xfe")
            files = [p.relative_to(src).as_posix() for p in build_mac.body_source_files(src)]
            self.assertEqual(files, ["Cargo.lock", "Cargo.toml", "crates/praxis-body/Cargo.toml",
                                     "crates/praxis-body/src/main.rs", "crates/praxis-body/src/sub/m.rs"])
            digest, n = build_mac.body_source_digest(src)
            self.assertEqual(n, 5)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            # CRLF не меняет отпечаток: зеркало на Windows и оригинал на проде — одно.
            (src / "crates" / "praxis-body" / "src" / "main.rs").write_bytes(b"fn main() {}\r\n")
            self.assertEqual(build_mac.body_source_digest(src), (digest, 5))
            (src / "crates" / "praxis-body" / "src" / "main.rs").write_bytes(b"fn main() { }\n")
            self.assertNotEqual(build_mac.body_source_digest(src)[0], digest)

    def test_core_source_is_the_prod_mirror(self):
        mirror = build_mac.core_source()
        self.assertRegex(str(mirror.get("head")), r"^[0-9a-f]{7,40}$")
        self.assertEqual(build_mac.core_source(Path(tempfile.gettempdir()) / "нет-такого.json"), {})

    def test_body_summary_shape(self):
        info = build_mac.body_summary(mirror={"head": "3c6b8e35", "taken_at": "2026-09-19T02:41:04+00:00",
                                              "dirty": False},
                                      crates=build_mac.BODY_CRATES, digest="ab" * 32, files=41,
                                      exe_sha256={"helene-body": "cd" * 32, "helene-bridge": "ef" * 32},
                                      target_dir="/x/cache/body-target")
        self.assertEqual(info["source"], "praxis/body")
        self.assertEqual(info["commit"], "3c6b8e35")
        # 25.09: из дерева поставки — источник и коммит дерева, не зеркала (A8 F3 / A9 F2)
        tree_info = build_mac.body_summary(mirror={"head": "3c6b8e35"}, crates=build_mac.BODY_CRATES,
                                           digest="ab" * 32, files=41, exe_sha256={},
                                           target_dir="/x", source="tree/body", tree_head="267eca7")
        self.assertEqual((tree_info["source"], tree_info["commit"]), ("tree/body", "267eca7"))
        self.assertEqual(info["crates"], ["praxis-body", "praxis-bridge"])
        self.assertEqual(info["binaries"], build_mac.BODY_BINARIES)
        self.assertEqual(info["files"], 41)
        self.assertEqual(info["digest"], "ab" * 32)
        self.assertEqual(set(info["exe_sha256"]), {"helene-body", "helene-bridge"})
        self.assertFalse(info["mirror_dirty"])
        self.assertRegex(info["built_utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        json.dumps(info, ensure_ascii=False)
        # Без CORE-SOURCE.json коммит пуст, а не выдуман.
        self.assertEqual(build_mac.body_summary(mirror={}, crates=(), digest="", files=0,
                                                exe_sha256={}, target_dir="")["commit"], "")

    def test_body_target_dir_is_in_the_build_cache(self):
        self.assertEqual(build_mac.body_target_dir(Path("/o/cache")), Path("/o/cache") / "body-target")
        # Workflow гоняет cargo test тела тем же каталогом.
        self.assertIn("cache/body-target", WORKFLOW.read_text(encoding="utf-8"))

    def test_license_texts_dedupe_and_name_the_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry"
            for crate, files in (("a-1.0.0", {"LICENSE-MIT": "MIT text"}),
                                 ("b-2.0.0", {}),
                                 ("c-3.0.0", {"LICENSE-MIT": "MIT text", "LICENSE-APACHE": "Apache text"})):
                (registry / crate).mkdir(parents=True)
                for name, text in files.items():
                    (registry / crate / name).write_text(text)
            dest = Path(tmp) / "licenses" / "body"
            index, missing = build_mac.license_texts(dest, [("a", "1.0.0"), ("b", "2.0.0"), ("c", "3.0.0"),
                                                            ("a", "1.0.0")], registry)
            self.assertEqual(missing, ["b 2.0.0"])
            self.assertEqual(len(index), 2)
            self.assertTrue(index[0].startswith("- **a 1.0.0** — [LICENSE-MIT](texts/"))
            # Один и тот же текст лежит один раз: два разных — два файла.
            self.assertEqual(len(list((dest / "texts").glob("*.txt"))), 2)
            self.assertEqual(build_mac._missing_note([]), [])
            self.assertIn("b 2.0.0", build_mac._missing_note(missing)[0])

    def test_body_license_head_tells_the_truth(self):
        head = "\n".join(build_mac.body_license_head(87, "3c6b8e35abcdef", ["x 1.0"]))
        for word in ("helene-bridge и helene-body", "praxis/body", "3c6b8e3", "praxis-body-protocol",
                     "Apache-2.0", "PolyForm-Noncommercial-1.0.0", "27.08.2026", "87 крейтов",
                     "ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md", "x 1.0"):
            self.assertIn(word, head, word)
        tree_head = "\n".join(build_mac.body_license_head(87, "267eca7abc", [], source="tree/body"))
        self.assertIn("`tree/body`", tree_head)
        self.assertIn("коммит дерева 267eca7", tree_head)
        self.assertNotIn("зеркало кода Праксис, коммит прода", tree_head)
        self.assertIn("?", "\n".join(build_mac.body_license_head(1, "", [])))
        # Коммит для шапки — из записи паспорта тела (`commit`), из CORE-SOURCE.json
        # (`head`) или пусто: сборка передаёт сюда запись build_body, не зеркало.
        self.assertEqual(build_mac.mirror_head({"commit": "3c6b8e35"}), "3c6b8e35")
        self.assertEqual(build_mac.mirror_head({"head": "0453d75"}), "0453d75")
        self.assertEqual(build_mac.mirror_head(None), "")
        self.assertEqual(build_mac.mirror_head(build_mac.core_source()), build_mac.core_source()["head"])

    def test_collect_body_licenses_runs_and_names_the_tree_source(self):
        # 25.09 (ревью V3/V4 F1): сборщик падал `TypeError` (`set(crates, source=…)`), а стенд
        # проверял только текст вызова. Теперь — вызов с подменёнными реестром и текстами.
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            src = Path(tmp) / "tree" / "body"
            src.mkdir(parents=True)
            (src / "Cargo.lock").write_text("[[package]]\nname = \"a\"\n", encoding="utf-8")
            with (mock.patch.object(build_mac.bd, "_lock_crates", return_value=["a 1.0", "b 2.0"]),
                  mock.patch.object(build_mac.bd, "_cargo_registry_src", return_value=Path(tmp)),
                  mock.patch.object(build_mac, "license_texts",
                                    return_value=(["- a 1.0 — MIT"], ["b 2.0"]))):
                n = build_mac.collect_body_licenses(out, src, False, {"commit": "267eca7abc"})
            self.assertEqual(n, 1)
            readme = (out / "licenses" / "body" / "README.md").read_text(encoding="utf-8")
            self.assertIn("`tree/body`", readme)
            self.assertIn("267eca7", readme)
            self.assertIn("2 крейтов", readme)
            self.assertIn("b 2.0", readme)
            with (mock.patch.object(build_mac.bd, "_lock_crates", return_value=["a 1.0"]),
                  mock.patch.object(build_mac.bd, "_cargo_registry_src", return_value=Path(tmp)),
                  mock.patch.object(build_mac, "license_texts", return_value=(["- a 1.0"], []))):
                build_mac.collect_body_licenses(out, build_mac.BODY_SRC, False, None)
            readme = (out / "licenses" / "body" / "README.md").read_text(encoding="utf-8")
            self.assertIn("praxis/body", readme)

    def test_skip_body_is_a_declared_debug_flag(self):
        parser = build_mac.arg_parser()
        self.assertFalse(parser.parse_args([]).skip_body)
        self.assertTrue(parser.parse_args(["--skip-body"]).skip_body)
        for flag in ("--skip-runtime", "--skip-rust", "--skip-tests", "--allow-partial"):
            self.assertTrue(getattr(parser.parse_args([flag]), flag[2:].replace("-", "_")), flag)
        # Шапка файла обещает флаг — и объясняет, что это полусборка.
        self.assertIn("--skip-body", build_mac.__doc__)
        import inspect  # noqa: PLC0415
        src = inspect.getsource(build_mac.main)
        self.assertIn("skipped_body", src)
        self.assertIn("body=body_info", src)
        self.assertIn("collect_body_licenses(out, body_src_for(live)", src)

    def test_git_check_runs_after_the_runtime_stage(self):
        # 25.09: проверка «git уже лежит» стояла ДО stage_git_bundle (переставлена вместе
        # со сборкой тела) и валила Mac-прогон 0.8.6 на первом круге — git кладёт именно
        # шаг рантайма, до него файла нет по построению.
        import inspect  # noqa: PLC0415
        src = inspect.getsource(build_mac.main)
        check = src.index("нет runtime/git/bin/git")
        self.assertLess(src.index("stage_git_bundle(out, cache)"), check, "проверка git раньше шага рантайма")
        self.assertLess(check, src.index("smoke_runtime(out)"), "проверка git после дымового теста — поздно")


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
                       "Library/Caches/app.helene.install", "staging",
                       "ps -axo pid=,ppid=,comm=", "index(comm[id], home) == 1",
                       "open \"$HOME_DIR/Helene.app\"", "releases/download"):
            self.assertIn(needle, self.text, needle)
        # Кэш — свой каталог, не bundle id окна (app.helene.desk: туда пишет
        # WKWebView, снятие его сносит). Процессы — по пути исполняемого файла,
        # не по строке команды: `pgrep -f` гасил бы и `tail -f helene.log`.
        self.assertNotIn("Caches/app.helene.desk", self.text)
        self.assertNotIn("pgrep -f", self.text)
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
        # ⚠ Служба — тоже решение владельца. `False` здесь означал бы, что тихое
        # обновление молча снимает демон: мастер снимает прежнюю службу ПЕРЕД
        # копированием и ставит обратно только по этому полю.
        self.assertIn('"service": bool((cfg.get("installed") or {}).get("service"))', self.text)
        self.assertNotIn('"service": False', self.text)
        # Нулевая сессия — механизм Windows, отсюда она не пишется никогда.
        self.assertIn('"session0": False', self.text)
        # Адверсарка 19.09: umask только вокруг JSON решений и обратно мастеру;
        # BOM в helene.json; ждать выход оболочки по HELENE_OLD_PID, а не гасить;
        # код выхода мастера при снятии не глотать.
        self.assertIn("umask 077", self.text)
        self.assertIn('umask "$old_umask"', self.text)
        self.assertIn('encoding="utf-8-sig"', self.text)
        self.assertIn("HELENE_OLD_PID", self.text)
        self.assertIn('kill -0 "$pid"', self.text)
        self.assertIn("helene-uninstall.log", self.text)
        self.assertRegex(self.text, r'--uninstall( --purge)? --quiet && rc=0 \|\| rc=\$\?')
        # Отказ после остановки старой копии возвращает её человеку: ловушка на
        # выход, флаг после stop_running, журналы названы, бандл открыт обратно.
        self.assertIn("trap on_exit EXIT", self.text)
        self.assertIn("STOPPED=1", self.text)
        self.assertIn("install-sh.log", self.text)
        self.assertIn('open "$HOME_DIR/Helene.app" 2>/dev/null', self.text)
        update = self.text[self.text.index("update() {"):self.text.index("fresh() {")]
        self.assertNotIn("die ", update, "die в update() обошёл бы возврат прежней копии")
        self.assertNotIn("exit 0", update.split("STOPPED=1", 1)[1].split("STOPPED=0", 1)[0])

    def test_stands_bridge_pythonpath_to_the_tree(self):
        import inspect  # noqa: PLC0415
        src = inspect.getsource(build_mac.run_stands)
        self.assertIn('"PYTHONPATH": str(tree)', src)
        self.assertIn("os.pathsep", src)


class Workflow(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_static(self):
        for needle in ("workflow_dispatch", "macos-15", "build_mac.py", "--from-release",
                       "dtolnay/rust-toolchain@stable", "Swatinem/rust-cache@v2", "actions/setup-node@v4",
                       "actions/upload-artifact@v4", "gh release upload", "--clobber",
                       "cargo test --features custom-protocol", "t_fence_macos.py",
                       "--install", "--quiet", "screencapture", "/api/health", "/api/state",
                       "desk-token", "helene-build.json", "if: always()",
                       # Судья 19.09: pipefail на дымовых шагах, пустой снимок —
                       # вслух, видимые процессы — в журнал; выкладка только по
                       # кнопке; прогон от push, пока файла нет в main.
                       "shell: bash", "stat -f %z", "osascript", "cache-on-failure: true",
                       "github.event_name == 'workflow_dispatch' && inputs.upload",
                       "push:", "gh run download",
                       # 0.8.0, тело: стенды крейтов на настоящем Mac тем же target-dir,
                       # что у сборки; тихая установка с включённым «Управлением
                       # компьютером»; дымовой шаг ждёт connected: true в снимке
                       # сторожа; живые стенды тела бинарями сборки; журналы тела.
                       "cargo test -p praxis-body -p praxis-bridge --target-dir \"$BUILD/cache/body-target\"",
                       '"computer": true', "data/memory/.state/body.json", 'get("connected") is True',
                       "data/body/bridge.log", "data/body/body.log",
                       'HELENE_BODY_DIR="$BUILD/Helene"', "tests/t_body.py", "tests/t_body_macos.py",
                       "praxis/body", "cache-directories:", "build/cache/body-target",
                       # 0.8.0, служба: крейт svc собирается и гоняется стендами на
                       # Mac; тихая установка ставит демон; канал отвечает БЕЗ окна;
                       # окно потом становится клиентом и поднимает тело; bootout
                       # снимает демон; журнал службы уезжает в артефакт.
                       "cd desk/svc && cargo test", "desk/svc",
                       '"service": true', "helene-svc",
                       "/Library/LaunchDaemons/app.helene.svc.plist",
                       "launchctl bootstrap system", "launchctl bootout system/app.helene.svc",
                       "подключаюсь без своих детей", "body-token", "data/service.log",
                       # Судья 19.09, выкладка: гард «дерево из того же выпуска»
                       # стоит В ШАГЕ выкладки, а имя артефакта не выглядит
                       # выпускным, когда дерево приехало из другого тега.
                       '[ "$TREE_TAG" = "$TAG" ]',
                       "ARTIFACT_NAME=Helene-$TAG-macos-arm64-tree-$TREE_TAG",
                       "name: ${{ env.ARTIFACT_NAME }}",
                       # Судья 19.09, обновление: раннер ставил только начисто —
                       # теперь гоняет и обновление ПОВЕРХ тем же install.sh.
                       "install.sh --from", "обновляю поверх", "обновлено до",
                       "installed.service", "computer.enabled"):
            self.assertIn(needle, self.text, needle)
        self.assertNotIn("\t", self.text, "табуляция в YAML")
        self.assertNotIn("| head", self.text, "под pipefail обрезанный конвейер валит шаг")
        # Ловушка 19.09: `runner` в env job — ноль задач; пути в $GITHUB_ENV.
        self.assertNotIn("${{ runner.temp }}/build\"\n    env", self.text)
        self.assertIn('>> "$GITHUB_ENV"', self.text)
        # Живые стенды тела — после дымового запуска: дерево читается с окна Helene.app.
        self.assertLess(self.text.index("Дымовой запуск"), self.text.index("Тело — живые стенды"))
        self.assertLess(self.text.index("Тело — живые стенды"), self.text.index("name: Журналы"))
        # Служба — ДО окна: иначе нельзя отличить «канал держит демон» от
        # «канал держит окно», и проверка «отвечает без окна» ничего не значит.
        self.assertLess(self.text.index("Служба — канал отвечает без окна"),
                        self.text.index("Дымовой запуск"))
        # Обновление поверх — после живых стендов и ДО снятия службы: иначе
        # проверять «служба поднялась обратно» будет не на чем.
        self.assertLess(self.text.index("Тело — живые стенды"),
                        self.text.index("Обновление поверх установленного"))
        self.assertLess(self.text.index("Обновление поверх установленного"),
                        self.text.index("Служба — снятие"))
        # Снятие — после всего живого, но до сбора журналов.
        self.assertLess(self.text.index("Тело — живые стенды"), self.text.index("Служба — снятие"))
        self.assertLess(self.text.index("Служба — снятие"), self.text.index("name: Журналы"))
        # Имя артефакта считается РАНЬШЕ сборки: шаг артефакта идёт с
        # `if: always()`, и на красном прогоне переменная обязана уже быть.
        self.assertLess(self.text.index("name: Имя артефакта"),
                        self.text.index("name: Сборка (build_mac.py)"))
        # Гард выкладки живёт именно в шаге выкладки, а не где-то рядом.
        upload = self.text[self.text.index("name: Выложить в выпуск"):]
        self.assertIn('[ "$TREE_TAG" = "$TAG" ]', upload,
                      "шаг выкладки без гарда: артефакт с чужим деревом уехал бы в выпуск")
        self.assertLess(upload.index('[ "$TREE_TAG" = "$TAG" ]'), upload.index("gh release upload"),
                        "гард обязан стоять ДО выкладки")
        # ⚠ Живой ключ устройства в артефакт не уезжает.
        artifact = self.text[self.text.index("upload-artifact"):]
        self.assertNotIn("body-token", artifact, "ключ к телу уехал бы в артефакт прогона")

    def test_install_sh_guard_is_exercised_on_a_real_mac(self):
        """20.09: под sudo и после su в другого пользователя install.sh обязан
        отказать словами до закачки. Стенд идёт после обновления поверх (скрипт
        уже доказал, что в своей сессии работает) и до снятия службы; второго
        пользователя создаёт sysadminctl и убирает за собой."""
        step = "install.sh — отказ словами под sudo и после su"
        self.assertIn(step, self.text)
        self.assertLess(self.text.index("Обновление поверх установленного"), self.text.index(step))
        self.assertLess(self.text.index(step), self.text.index("Служба — снятие"))
        body = self.text[self.text.index(step):self.text.index("Служба — снятие")]
        for needle in ("sudo -n sh /tmp/helene-install.sh", "sysadminctl -addUser", "su helenesu -c",
                       "launchctl manageruid", 'grep -q "без sudo"',
                       '-e "после su" -e "нет входа на экран"', "распаковываю",
                       "sysadminctl -deleteUser"):
            self.assertIn(needle, body, needle)

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
        self.assertEqual(job["env"]["TAG"], "${{ inputs.tag || '%s' }}" % build_mac.RELEASE_TAG_DEFAULT)
        names = [s.get("name") or s.get("uses") for s in job["steps"]]
        self.assertEqual(names[0], "actions/checkout@v4")
        cache = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("Swatinem/rust-cache"))
        self.assertIn("praxis/body", cache["with"]["workspaces"])
        self.assertIn("build/cache/body-target", cache["with"]["cache-directories"])
        self.assertTrue(any("gh release upload" in (s.get("run") or "") for s in job["steps"]))
        upload = next(s for s in job["steps"] if "gh release upload" in (s.get("run") or ""))
        self.assertIn("inputs.upload", str(upload.get("if")))
        # Имя артефакта — переменной, а не литералом с тегом: на прогоне с
        # чужим деревом оно другое (см. шаг «Имя артефакта»).
        art = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact"))
        self.assertEqual(art["with"]["name"], "${{ env.ARTIFACT_NAME }}")
        # Шаг обновления — есть, идёт bash-ем (pipefail) и зовёт install.sh поверх.
        upd = next(s for s in job["steps"] if "Обновление поверх" in str(s.get("name")))
        self.assertEqual(upd.get("shell"), "bash")
        self.assertIn('install.sh" --from', upd["run"])


class InstallShSession(unittest.TestCase):
    """Случай 20.09: человек обновлялся из Терминала одного пользователя после
    `su` в другого. Скрипт искал установку в доме второго (пусто → «первая
    установка»), а мастер через `open` стартовал от первого — `open` отдаёт окно
    сессии Терминала — и не смог прочитать папку второго («you don't have
    permission to view it»), после 224 МБ закачки. Ограда: сессия проверяется
    ДО закачки, отказ — словами; отказ `open` тоже говорит, что делать."""

    def setUp(self):
        self.text = INSTALL_SH.read_text(encoding="utf-8").replace("\r\n", "\n")

    def test_session_is_checked_before_download(self):
        main = self.text[self.text.index("main() {"):]
        self.assertLess(main.index("check_platform"), main.index("check_session"))
        self.assertLess(main.index("check_session"), main.index("download"))
        guard = self.text[self.text.index("check_session() {"):self.text.index("download() {")]
        # root — отказ; su — по uid сессии launchd; без графической сессии — отказ.
        self.assertIn('[ "$uid" -eq 0 ]', guard)
        self.assertIn("launchctl manageruid", guard)
        self.assertIn('launchctl print "gui/$uid"', guard)
        for words in ("без sudo", "после su", "без su и sudo", "нет входа на экран"):
            self.assertIn(words, guard, words)
        # Системный домен (manageruid 0) — не улика против человека: по нему не отказываем.
        self.assertIn('[ "$manager" -ne 0 ] && [ "$manager" -ne "$uid" ]', guard)
        tools = self.text[self.text.index("for tool in"):].split("\n", 1)[0]
        self.assertIn("launchctl", tools)

    def test_open_failure_at_first_install_says_what_to_do(self):
        fresh = self.text[self.text.index("fresh() {"):self.text.index("uninstall() {")]
        self.assertIn('if ! open "$STAGING/$FOLDER/Helene Setup.app"; then', fresh)
        self.assertIn("мастер не открылся", fresh)
        self.assertIn("без sudo", fresh)

    def test_documents_say_who_runs_it(self):
        self.assertIn("без `sudo` и без `su`", build_mac.FIRST_RUN_MAC)
        self.assertIn("без sudo и su", self.text[self.text.index("usage() {"):self.text.index("while [ $# -gt 0 ]")])
        readme = (DESK.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("no `su`", readme)


class BodySourceIsStrictForReleases(unittest.TestCase):
    def test_a_tree_without_body_is_refused_when_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            tree.mkdir()
            self.assertEqual(build_mac.body_src_for(tree), build_mac.BODY_SRC, "откат на зеркало — только не строго")
            with self.assertRaises(SystemExit):
                build_mac.body_src_for(tree, strict=True)
            (tree / "body").mkdir()
            (tree / "body" / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
            self.assertEqual(build_mac.body_src_for(tree, strict=True), tree / "body")


if __name__ == "__main__":
    unittest.main(verbosity=1)
