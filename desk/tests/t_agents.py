# -*- coding: utf-8 -*-
"""Список агентов установки: те же случаи, что читает Rust (`roster-cases.json`).

Запуск:  python tests/t_agents.py

Здесь проверяется ПРАВИЛО, а не одна раскладка: корневой агент есть всегда,
соседи берутся из `agents/*` по алфавиту, порт по умолчанию идёт от места в
списке, спор за порт называется вслух, мусор и битый конфиг список не рушат.
Тот же файл случаев читает тест в `common/agents.rs` — если два языка разойдутся,
падать будет тот, который разошёлся, а не оба молча.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "localharness"))

import agents  # noqa: E402

CASES = json.loads((HERE / "roster-cases.json").read_text(encoding="utf-8"))


def lay_out(root: Path, files: dict) -> None:
    """Разложить случай на диске. Объект — JSON, строка — как есть (битый файл)."""
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2)
        path.write_text(text, encoding="utf-8", newline="\n")


class Roster(unittest.TestCase):
    def test_cases(self):
        for case in CASES["cases"]:
            with self.subTest(case["name"]), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                lay_out(root, case["files"])
                got = agents.roster(root)
                self.assertEqual([a.id for a in got], [e["id"] for e in case["expect"]],
                                 case["name"])
                for mine, want in zip(got, case["expect"]):
                    self.assertEqual(mine.name, want["name"], case["name"])
                    self.assertEqual(mine.port, want["port"], case["name"])
                    self.assertEqual(mine.enabled, want["enabled"], case["name"])
                    self.assertEqual(mine.base, want["base"], case["name"])
                    self.assertEqual(mine.conflict, want["conflict"], case["name"])
                    rel = mine.tree.resolve().relative_to(root.resolve()).as_posix()
                    self.assertEqual(rel, want["tree"], case["name"])

    def test_raisable_skips_disabled_and_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lay_out(root, {
                "helene.json": {"agent": {"name": "Hélène"}, "port": 8094},
                "agents/off/helene.json": {"agent": {"name": "Спит"}, "enabled": False},
                "agents/clash/helene.json": {"agent": {"name": "Спорит"}, "port": 8094},
                "agents/ok/helene.json": {"agent": {"name": "Живой"}, "port": 8200},
            })
            self.assertEqual([a.id for a in agents.raisable(root)], ["main", "ok"])

    def test_find_is_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lay_out(root, {"helene.json": {"agent": {"name": "Hélène"}},
                           "agents/mira/helene.json": {"agent": {"name": "Мира"}}})
            self.assertEqual(agents.find(root, "mira").name, "Мира")
            self.assertEqual(agents.find(root, "MIRA").name, "Мира")   # регистр не важен
            self.assertIsNone(agents.find(root, "нет такого"))
            # ⚠ Неизвестный id НЕ подменяется корневым: иначе ярлык на удалённого
            # агента молча открывал бы окно чужого.
            self.assertIsNone(agents.find(root, ""))


class Slug(unittest.TestCase):
    def test_cyrillic_becomes_readable_folder(self):
        self.assertEqual(agents.slug("Мира"), "mira")
        self.assertEqual(agents.slug("Добрыня Никитич"), "dobrynya-nikitich")
        self.assertEqual(agents.slug("Hélène"), "h-l-ne")   # без выдумок: латиница как есть
        self.assertEqual(agents.slug(""), "agent")
        self.assertEqual(agents.slug("!!!"), "agent")

    def test_taken_names_get_a_number(self):
        self.assertEqual(agents.slug("Мира", {"mira"}), "mira-2")
        self.assertEqual(agents.slug("Мира", {"mira", "mira-2"}), "mira-3")

    def test_id_always_matches_the_pattern(self):
        for name in ("Мира", "  ", "!!!", "Ё" * 60, "ok-name", "9lives"):
            self.assertRegex(agents.slug(name), agents.ID_PATTERN.pattern)


class Create(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        lay_out(self.root, {"helene.json": {
            "mode": "local", "python": "runtime/python.exe", "app": "app/deskapp.py",
            "runner": "app/localharness/runner.py", "code": "tree", "tree": "data",
            "port": 8094, "agent": {"name": "Hélène"}, "owner": {"name": "Егор", "room": "Hélène"},
            "model": {"framework": "openai", "key": "sk-live", "model": "gpt-5"},
            "telegram": {"bot_token": "111:AAA", "owner_id": 42},
            "sandbox": {"enabled": True, "mounts": [{"name": "docs", "path": "C:/docs"}]},
            "computer": {"enabled": True, "port": 9480, "scopes": ["computer.read"]},
            "relay": {"enabled": True}, "update": {"url": "https://example/latest"},
            "setup_complete": True,
        }})

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_agent_is_reachable_and_separate(self):
        made = agents.create(self.root, "Мира")
        self.assertEqual(made.id, "mira")
        self.assertEqual(made.name, "Мира")
        self.assertEqual(made.port, 8095)
        self.assertEqual(made.tree.resolve(),
                         (self.root / "agents" / "mira" / "data").resolve())
        self.assertEqual([a.id for a in agents.roster(self.root)], ["main", "mira"])

    def test_paths_point_back_at_the_installation(self):
        made = agents.create(self.root, "Мира")
        cfg = agents.read_config(made.config)
        self.assertEqual(cfg["python"], "../../runtime/python.exe")
        self.assertEqual(cfg["app"], "../../app/deskapp.py")
        self.assertEqual(cfg["runner"], "../../app/localharness/runner.py")
        self.assertEqual(cfg["code"], "../../tree")
        # Путь обязан ВЕСТИ туда, где эти файлы лежат, а не просто выглядеть верным.
        self.assertEqual((made.dir / cfg["app"]).resolve(),
                         (self.root / "app" / "deskapp.py").resolve())

    def test_brain_is_inherited_and_the_bot_is_not(self):
        cfg = agents.read_config(agents.create(self.root, "Мира").config)
        self.assertEqual(cfg["model"]["key"], "sk-live")          # мозг настроен один раз
        self.assertEqual(cfg["sandbox"]["mounts"][0]["name"], "docs")
        self.assertEqual(cfg["telegram"]["bot_token"], "")        # ⚠ бот у каждого свой
        self.assertEqual(cfg["telegram"]["owner_id"], 42)         # …а владелец тот же
        self.assertEqual(cfg["owner"]["name"], "Егор")
        self.assertEqual(cfg["owner"]["room"], "Мира")           # комната — его имя, не чужое
        self.assertNotIn("setup_complete", cfg)
        self.assertNotIn("update", cfg)                           # обновление — про установку
        self.assertFalse(cfg["relay"]["enabled"])                 # реле одно на установку

    def test_body_gets_its_own_door(self):
        cfg = agents.read_config(agents.create(self.root, "Мира").config)
        self.assertNotEqual(cfg["computer"]["port"], 9480)
        self.assertFalse(cfg["computer"]["enabled"])

    def test_second_of_the_same_name_does_not_overwrite_the_first(self):
        first = agents.create(self.root, "Мира")
        second = agents.create(self.root, "Мира")
        self.assertNotEqual(first.id, second.id)
        self.assertNotEqual(first.port, second.port)
        self.assertEqual([a.id for a in agents.roster(self.root)], ["main", "mira", "mira-2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
