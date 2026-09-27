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
    def test_names_and_control(self):
        names = bl.asset_names("1.2.3")
        self.assertEqual(names["deb"], "Helene_1.2.3_amd64.deb")
        text = bl.control_text("1.2.3", 123456)
        self.assertIn("Package: helene\n", text)
        self.assertIn("Version: 1.2.3\n", text)
        self.assertIn("Architecture: amd64\n", text)
        self.assertIn("Installed-Size: 123456\n", text)
        self.assertIn("libc6 (>= 2.28)", text)
        for dep in ("git", "bubblewrap"):
            self.assertIn(dep, text.split("Depends:")[1].split("\n")[0])
        # Продолжение описания в control — строки с пробела; пустых строк быть не может.
        body = text.split("Description:")[1].splitlines()[1:]
        self.assertTrue(all(line.startswith(" ") and line.strip() for line in body), body)
        with self.assertRaises(SystemExit):
            bl.deb_version("1.2")

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
        self.assertIn('[ "$1" = "remove" ]', bl.PRERM)
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
