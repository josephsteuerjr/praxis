# -*- coding: utf-8 -*-
"""Сверка констант продукта с `ui-kit/contract.json` (задача A п. 1.12).

Запуск:  python tests/t_contract.py

Порт канала 8094 лежал в семи местах, порт реле 5011 — в пяти, порт моста
9480 — в четырёх, четыре права `computer.*` — в четырёх (ревью 06.09, §3).
Одно место правды — `ui-kit/contract.json`; этот стенд сверяет с ним
Python-копии (харнесс и канал). Rust-копии сверяют тесты в shell/svc/setup
(`contract_json_matches_constants`), TypeScript — B в окне.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "localharness"))
sys.path.insert(0, str(ROOT))

import body  # noqa: E402
import modes  # noqa: E402

CONTRACT = json.loads((ROOT / "ui-kit" / "contract.json").read_text(encoding="utf-8"))


class Contract(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(CONTRACT["v"], 1)
        for key in ("desk", "relay", "body"):
            self.assertIsInstance(CONTRACT["ports"][key], int)
        self.assertEqual(len(CONTRACT["computer_scopes"]), 4)
        re.compile(CONTRACT["rooms"]["pattern"])

    def test_harness_matches(self):
        self.assertEqual(modes.COMPUTER_PORT_DEFAULT, CONTRACT["ports"]["body"])
        self.assertEqual(body.DEFAULT_PORT, CONTRACT["ports"]["body"])
        self.assertEqual(list(modes.COMPUTER_SCOPES), CONTRACT["computer_scopes"])
        self.assertEqual(list(body.SCOPES), CONTRACT["computer_scopes"])
        import transport
        self.assertEqual(transport.ROOM_DEFAULT, CONTRACT["rooms"]["default"])
        self.assertEqual(transport.ROOM_PATTERN.pattern, CONTRACT["rooms"]["pattern"])
        self.assertEqual(transport.ROOM_ARCHIVE_DIR, CONTRACT["rooms"]["archive_dir"])

    def test_channel_matches(self):
        import deskapp
        from deskd import readers
        self.assertEqual(deskapp.DEFAULT_PORT, CONTRACT["ports"]["desk"])
        self.assertEqual(deskapp.PROTOCOL, CONTRACT["protocol"])
        self.assertEqual(readers.RELAY_PORT_DEFAULT, CONTRACT["ports"]["relay"])
        self.assertEqual(readers.CONFIG_NAME, CONTRACT["config_name"])

    def test_no_stray_literals_in_harness(self):
        """Числа портов в коде харнесса и канала — только через константы."""
        allowed = {
            "localharness/modes.py": {"9480"},        # COMPUTER_PORT_DEFAULT
            "localharness/body.py": {"9480"},         # DEFAULT_PORT
            "deskapp.py": {"8094"},                   # DEFAULT_PORT
            "deskd/readers.py": {"5011"},             # RELAY_PORT_DEFAULT
        }
        for rel in ("localharness/modes.py", "localharness/body.py", "localharness/runner.py",
                    "localharness/transport.py", "localharness/boot.py", "deskapp.py",
                    "deskd/readers.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
            for port in ("8094", "5011", "9480"):
                hits = len(re.findall(r"(?<![\d.])" + port + r"(?![\d.])", code))
                cap = 1 if port in allowed.get(rel, set()) else 0
                self.assertLessEqual(hits, cap, f"{rel}: литерал {port} встречается {hits} раз")


if __name__ == "__main__":
    unittest.main(verbosity=2)
