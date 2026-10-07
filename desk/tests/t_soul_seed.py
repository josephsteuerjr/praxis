# -*- coding: utf-8 -*-
"""Сид души: чем родится конституция агента — выбирает владелец (1.4.0).

Запуск:  python tests/t_soul_seed.py

До 1.4.0 конституция у каждого следующего агента была каноном молча: выбор был
только у корневого (визард). Теперь `agents.create(soul=…)` кладёт файл
`soul-seed.md` рядом с конфигом, раннер читает его до `ensure_layout` и передаёт
`boot.ensure_layout(soul_seed=…)` — тот пишет его вместо канона при рождении
дома. Стенд держит правило целиком:

  * seed без души → в доме лежит ПОДСТАВЛЕННЫЙ текст сида ({{agent}}/{{owner}});
  * без сида → канон (`boot.soul_text`);
  * живая душа сильнее сида: существующий SOUL.md не трогается (идемпотентно);
  * BOM на сиде не ломает рождение (раннер читает `read_config_text`);
  * `refresh_kit` не трогает кастомную душу (правило «только нетронутые тексты
    поставки» уже работало — здесь оно застраховано тестом).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESK = HERE.parent
sys.path.insert(0, str(DESK / "localharness"))

import agents  # noqa: E402
import boot  # noqa: E402

CFG = {"agent": {"name": "Мира"}, "owner": {"name": "Егор"}}
SEED = "# Моя конституция\n\nМеня зовут {{agent}}. Я живу у {{owner}}.\n"


class SoulSeed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        lay_out(self.root, {"helene.json": {
            "mode": "local", "python": "runtime/python.exe", "app": "app/deskapp.py",
            "runner": "app/localharness/runner.py", "code": "tree", "tree": "data",
            "port": 8094, "agent": {"name": "Hélène"}, "owner": {"name": "Егор", "room": "Hélène"},
            "model": {"framework": "openai", "key": "sk-live", "model": "gpt-5"},
            "telegram": {"bot_token": "111:AAA", "owner_id": 42},
            "setup_complete": True,
        }})

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_writes_substituted_text_and_stays_by_the_config(self):
        made = agents.create(self.root, "Мира", soul={"kind": "text", "text": SEED})
        seed = made.dir / agents.SEED_NAME
        self.assertTrue(seed.is_file(), "сид не лёг рядом с конфигом")
        self.assertEqual(seed.read_text(encoding="utf-8"), SEED, "сид искажён при записи")
        boot.ensure_layout(made.tree, CFG, soul_seed=SEED)
        soul = (made.tree / "soul" / "SOUL.md").read_text(encoding="utf-8")
        self.assertIn("Меня зовут Мира", soul)
        self.assertIn("Я живу у Егор", soul)
        self.assertNotIn("{{", soul, "плейсхолдер не подставлен")
        # конфиг души не знает: машина пишет конфиг, владелец — сид
        self.assertNotIn("soul", agents.read_config(made.config))
        # запись о рождении не удаляется после того, как душа легла
        self.assertTrue(seed.is_file(), "сид удалён после ensure_layout")

    def test_without_seed_the_canon_is_written(self):
        made = agents.create(self.root, "Мира")
        self.assertFalse((made.dir / agents.SEED_NAME).exists(), "канон не пишет сида")
        boot.ensure_layout(made.tree, CFG)
        self.assertEqual((made.tree / "soul" / "SOUL.md").read_text(encoding="utf-8"),
                         boot.soul_text(CFG))

    def test_live_soul_ignores_the_seed(self):
        made = agents.create(self.root, "Мира", soul={"kind": "text", "text": SEED})
        soul = made.tree / "soul" / "SOUL.md"
        soul.parent.mkdir(parents=True, exist_ok=True)
        soul.write_text("живая душа — не трогай\n", encoding="utf-8", newline="\n")
        boot.ensure_layout(made.tree, CFG, soul_seed=SEED)
        self.assertEqual(soul.read_text(encoding="utf-8"), "живая душа — не трогай\n")
        boot.ensure_layout(made.tree, CFG, soul_seed=SEED)   # идемпотентно и во второй раз
        self.assertEqual(soul.read_text(encoding="utf-8"), "живая душа — не трогай\n")

    def test_refresh_kit_never_touches_the_custom_soul(self):
        made = agents.create(self.root, "Мира", soul={"kind": "text", "text": SEED})
        boot.ensure_layout(made.tree, CFG, soul_seed=SEED)
        soul = made.tree / "soul" / "SOUL.md"
        before = soul.read_text(encoding="utf-8")
        refreshed = boot.refresh_kit(made.tree, CFG)
        self.assertEqual(soul.read_text(encoding="utf-8"), before)
        self.assertNotIn("soul/SOUL.md", refreshed,
                         "кастомная душа не совпадает с редакциями поставки — но и это сказано")

    def test_bom_in_seed_does_not_break_the_birth(self):
        # Владелец мог открыть и пересохранить сид в Блокноте: UTF-8 с BOM.
        # Раннер читает его `boot.read_config_text` — BOM обязан сниматься.
        made = agents.create(self.root, "Мира", soul={"kind": "text", "text": SEED})
        seed = made.dir / agents.SEED_NAME
        seed.write_bytes(b"\xef\xbb\xbf" + SEED.encode("utf-8"))
        text = boot.read_config_text(seed)          # так его читает runner.py
        boot.ensure_layout(made.tree, CFG, soul_seed=text)
        soul = (made.tree / "soul" / "SOUL.md").read_text(encoding="utf-8")
        self.assertIn("Меня зовут Мира", soul)
        self.assertNotIn("﻿", soul.strip()[:1] + "", "BOM утёк в душу")


def lay_out(root: Path, files: dict) -> None:
    """Разложить файлы: объект — JSON (как конфиг), строка — как есть."""
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2)
        path.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
