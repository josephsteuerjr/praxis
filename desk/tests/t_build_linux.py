# -*- coding: utf-8 -*-
"""Сборка для Linux (.deb) — чистые части, на любой ОС.

Запуск:  python tests/t_build_linux.py

Порог glibc — главное обещание пакета: собранное не должно требовать libc новее Debian 10
(Astra 1.7), иначе пакет встанет и не запустится. Порог считается двумя путями — по тегам
колёс рантайма и по символам GLIBC_x.y в каждом ELF — и оба держит этот стенд.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "installer"))
sys.path.insert(0, str(HERE.parent))

import build_linux as bl  # noqa: E402


class GlibcFloor(unittest.TestCase):
    def test_manylinux_tags_name_their_glibc(self):
        self.assertEqual(bl.glibc_of_manylinux("cp314-cp314-manylinux_2_28_x86_64"), (2, 28))
        self.assertEqual(bl.glibc_of_manylinux("cp314-cp314-manylinux2014_x86_64"), (2, 17))
        self.assertEqual(bl.glibc_of_manylinux("py3-none-manylinux1_x86_64"), (2, 5))
        self.assertIsNone(bl.glibc_of_manylinux("cp314-cp314-musllinux_1_2_x86_64"))
        self.assertIsNone(bl.glibc_of_manylinux("py3-none-any"))

    def test_a_wheel_needs_its_lowest_tag_and_the_runtime_its_worst_wheel(self):
        tags = [
            ["cp314-cp314-manylinux_2_17_x86_64", "cp314-cp314-manylinux2014_x86_64"],
            ["cp314-cp314-manylinux_2_27_x86_64", "cp314-cp314-manylinux_2_28_x86_64"],
            ["py3-none-any"],
        ]
        self.assertEqual(bl.glibc_floor_of_tags(tags), (2, 27))
        self.assertIsNone(bl.glibc_floor_of_tags([["py3-none-any"]]))

    def test_elf_floor_is_the_newest_glibc_symbol(self):
        fake = b"\x7fELF" + b"\0" * 16 + b"GLIBC_2.2.5\0GLIBC_2.17\0GLIBC_2.28\0GLIBC_PRIVATE\0"
        self.assertEqual(bl.glibc_floor_of_elf(fake), (2, 28))
        self.assertIsNone(bl.glibc_floor_of_elf(b"\x7fELF static, no libc"))
        self.assertIsNone(bl.glibc_floor_of_elf(b"MZ not an elf GLIBC_2.99"))
        self.assertLessEqual((2, 28), bl.GLIBC_FLOOR)


class Package(unittest.TestCase):
    def test_names_follow_each_family(self):
        names = bl.asset_names("1.2.3")
        self.assertEqual(names["deb"], "helene_1.2.3_amd64.deb")
        self.assertEqual(names["rpm"], "helene-1.2.3-1.x86_64.rpm")
        with self.assertRaises(SystemExit):
            bl.deb_version("1.2")

    def test_one_nfpm_config_makes_both_families(self):
        files = {k: f"/w/{k}" for k in ("unit", "policy", "home", "postinst", "prerm", "postrm")}
        cfg = bl.nfpm_config("1.2.3", [{"src": "/o/helene-svc", "dst": "/opt/helene/helene-svc"}], files)
        self.assertEqual((cfg["name"], cfg["version"], cfg["release"], cfg["arch"]),
                         ("helene", "1.2.3", "1", "amd64"))
        deb, rpm = cfg["overrides"]["deb"], cfg["overrides"]["rpm"]
        self.assertIn("libc6 (>= 2.28)", deb["depends"])
        self.assertIn("glibc >= 2.28", rpm["depends"])
        self.assertIn("procps", deb["depends"])
        self.assertIn("procps-ng", rpm["depends"])
        for both in ("git", "bubblewrap"):
            self.assertIn(both, deb["depends"])
            self.assertIn(both, rpm["depends"])
        units = {(c.get("packager"), c["dst"]) for c in cfg["contents"] if c["dst"].endswith("helene@.service")}
        self.assertEqual(units, {("deb", "/lib/systemd/system/helene@.service"),
                                 ("rpm", "/usr/lib/systemd/system/helene@.service")})
        self.assertEqual(cfg["scripts"]["preremove"], "/w/prerm")
        json.dumps(cfg)  # JSON — это валидный YAML для nfpm

    def test_program_contents_keep_modes_and_symlinks(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runtime" / "bin").mkdir(parents=True)
            (root / "runtime" / "bin" / "python3.14").write_bytes(b"\x7fELF")
            (root / "runtime" / "bin" / "python3.14").chmod(0o755)
            (root / "helene.json").write_text("{}")
            (root / "data").mkdir()
            try:
                os.symlink("python3.14", root / "runtime" / "bin" / "python3")
            except (OSError, NotImplementedError):
                self.skipTest("симлинк на этой машине не создать (Windows без прав)")
            items = {i["dst"]: i for i in bl.program_contents(root)}
            self.assertEqual(items["/opt/helene/runtime/bin/python3"]["type"], "symlink")
            self.assertEqual(items["/opt/helene/runtime/bin/python3"]["src"], "python3.14")
            if os.name != "nt":
                self.assertEqual(items["/opt/helene/runtime/bin/python3.14"]["file_info"]["mode"], 0o755)
            self.assertEqual(items["/opt/helene/helene.json"]["file_info"]["mode"], 0o644)
            self.assertEqual(items["/opt/helene/data"]["type"], "dir")

    def test_prerm_understands_both_dpkg_and_rpm(self):
        self.assertIn('case "$1" in remove|0)', bl.PRERM)

    def test_polkit_policy_names_the_program_path_the_service_calls(self):
        self.assertIn('<annotate key="org.freedesktop.policykit.exec.path">/opt/helene/helene-svc</annotate>',
                      bl.POLKIT_POLICY)
        self.assertIn('id="app.helene.service"', bl.POLKIT_POLICY)
        self.assertIn("auth_admin", bl.POLKIT_POLICY)

    def test_maintainer_scripts_never_touch_owner_data(self):
        for script in (bl.POSTINST, bl.PRERM, bl.POSTRM):
            self.assertTrue(script.startswith("#!/bin/sh\n"))
            self.assertNotIn(".local/share/helene", script.replace("# Данные владельцев (~/.local/share/helene)", ""))
            self.assertNotIn("rm -rf", script)
        # Снятие пакета гасит службы владельцев; обновление — перезапускает, не гасит.
        self.assertIn('remove|0', bl.PRERM)
        self.assertIn("try-restart", bl.POSTINST)

    def test_the_config_template_points_at_the_linux_python(self):
        cfg = json.loads(bl.helene_json_linux())
        self.assertEqual(cfg["python"], "runtime/bin/python3")
        self.assertEqual(cfg["mode"], "local")

    def test_first_run_text_is_honest_about_the_window_and_wayland(self):
        text = bl.FIRST_RUN_LINUX
        self.assertIn("Окна под Linux в этой сборке ещё нет", text)
        self.assertIn("Wayland", text)
        self.assertIn("helene-svc service install", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
