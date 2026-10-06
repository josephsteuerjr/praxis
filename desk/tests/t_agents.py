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
import os
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

    def test_default_python_follows_the_platform(self):
        # Корневой конфиг без `python`: умолчание — питон поставки этой платформы
        # (Windows — embedded CPython, иначе python-build-standalone). Правило то
        # же, что у оболочки; явное значение в конфиге сильнее (тест выше).
        root = agents.read_config(self.root / "helene.json")
        root.pop("python")
        lay_out(self.root, {"helene.json": root})
        cfg = agents.read_config(agents.create(self.root, "Зоя").config)
        want = "runtime/python.exe" if os.name == "nt" else "runtime/bin/python3"
        self.assertEqual(agents.DEFAULT_PYTHON, want)
        self.assertEqual(cfg["python"], "../../" + want)

    def test_second_of_the_same_name_does_not_overwrite_the_first(self):
        first = agents.create(self.root, "Мира")
        second = agents.create(self.root, "Мира")
        self.assertNotEqual(first.id, second.id)
        self.assertNotEqual(first.port, second.port)
        self.assertEqual([a.id for a in agents.roster(self.root)], ["main", "mira", "mira-2"])


class CreateSoul(unittest.TestCase):
    """Сид души при рождении (1.4.0): чем станет конституция — выбирает владелец."""

    SEED = "# Душа Мир\n\nМеня зовут {{agent}}, живу у {{owner}}.\n"

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

    def test_text_seed_lies_by_the_config_and_config_stays_clean(self):
        made = agents.create(self.root, "Мира", soul={"kind": "text", "text": self.SEED})
        seed = made.dir / agents.SEED_NAME
        self.assertTrue(seed.is_file())
        self.assertEqual(seed.read_text(encoding="utf-8"), self.SEED)
        self.assertNotIn("soul", agents.read_config(made.config), "конфиг души не знает")

    def test_canonical_writes_no_seed(self):
        made = agents.create(self.root, "Мира", soul={"kind": "canonical"})
        self.assertFalse((made.dir / agents.SEED_NAME).exists())
        made = agents.create(self.root, "Зоя")                      # и без параметра тоже
        self.assertFalse((made.dir / agents.SEED_NAME).exists())

    def test_repeat_name_does_not_spoil_the_seed(self):
        first = agents.create(self.root, "Мира", soul={"kind": "text", "text": self.SEED})
        agents.create(self.root, "Мира", soul={"kind": "text", "text": "другой текст\n"})
        self.assertEqual((first.dir / agents.SEED_NAME).read_text(encoding="utf-8"),
                         self.SEED, "сид первого не тронут вторым")

    def test_empty_text_is_refused_before_any_writes(self):
        with self.assertRaises(ValueError):
            agents.create(self.root, "Мира", soul={"kind": "text", "text": "   "})
        with self.assertRaises(ValueError):
            agents.create(self.root, "Мира", soul={"kind": "text"})
        with self.assertRaises(ValueError):
            agents.create(self.root, "Мира", soul={"kind": "чужой"})
        self.assertEqual([a.id for a in agents.roster(self.root)], ["main"],
                         "отказ не оставляет половину агента")

    def test_inherit_takes_the_donor_soul(self):
        donor = agents.create(self.root, "Донор")
        soul = donor.tree / "soul" / "SOUL.md"
        soul.parent.mkdir(parents=True, exist_ok=True)
        soul.write_text("душа донора целиком\n", encoding="utf-8", newline="\n")
        made = agents.create(self.root, "Мира", soul={"kind": "inherit"}, donor_id=donor.id)
        self.assertEqual((made.dir / agents.SEED_NAME).read_text(encoding="utf-8"),
                         "душа донора целиком\n")

    def test_inherit_without_a_donor_is_a_clear_error(self):
        with self.assertRaises(ValueError) as caught:
            agents.create(self.root, "Мира", soul={"kind": "inherit"}, donor_id="нет-такого")
        self.assertIn("донор", str(caught.exception))
        # и от корневого, у которого дома ещё нет: дом создаёт раннер, не create()
        with self.assertRaises(ValueError) as caught:
            agents.create(self.root, "Мира", soul={"kind": "inherit"})
        self.assertIn("main", str(caught.exception))

    def test_inherit_with_text_is_text(self):
        # контракт UI-волны B1: текст всегда в поле text — «inherit с текстом» это text
        made = agents.create(self.root, "Мира",
                             soul={"kind": "inherit", "text": self.SEED})
        self.assertEqual((made.dir / agents.SEED_NAME).read_text(encoding="utf-8"), self.SEED)


class SetEnabled(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        lay_out(self.root, {"helene.json": {"agent": {"name": "Hélène"}, "port": 8094},
                           "agents/mira/helene.json": {"agent": {"name": "Мира"}}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_flags_flip_atomically(self):
        got = agents.set_enabled(self.root, "mira", False)
        self.assertFalse(got.enabled)
        self.assertEqual([a.id for a in agents.raisable(self.root)], ["main"],
                         "погашенный не поднимается")
        got = agents.set_enabled(self.root, "MIRA", True)      # регистр не важен
        self.assertTrue(got.enabled)
        self.assertEqual([a.id for a in agents.raisable(self.root)], ["main", "mira"])
        # конфиг остаётся целым JSON-ом, а не обрубком
        cfg = agents.read_config(self.root / "agents" / "mira" / "helene.json")
        self.assertEqual(cfg["agent"]["name"], "Мира")

    def test_base_and_missing_refuse(self):
        with self.assertRaises(ValueError):
            agents.set_enabled(self.root, "main", False)
        with self.assertRaises(ValueError):
            agents.set_enabled(self.root, "нет-такого", True)


class RemoveAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        lay_out(self.root, {"helene.json": {"agent": {"name": "Hélène"}, "port": 8094},
                           "agents/mira/helene.json": {"agent": {"name": "Мира"}}})
        (self.root / "agents" / "mira" / "soul-seed.md").write_text(
            "сид\n", encoding="utf-8", newline="\n")
        (self.root / "agents" / "mira" / "data" / "memory").mkdir(parents=True)
        (self.root / "agents" / "mira" / "data" / "memory" / "llm.json").write_text(
            "{}", encoding="utf-8", newline="\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_folder_moves_to_attic_whole(self):
        attic = self.root / "соседний" / "attic"
        dest = agents.remove_agent(self.root, "mira", attic)
        self.assertFalse((self.root / "agents" / "mira").exists(), "папка не ушла")
        self.assertTrue(dest.is_dir())
        self.assertTrue((dest / "helene.json").is_file())
        self.assertTrue((dest / "soul-seed.md").is_file())
        self.assertTrue((dest / "data" / "memory" / "llm.json").is_file(),
                        "дом с памятью уехал целиком")
        self.assertEqual([a.id for a in agents.roster(self.root)], ["main"],
                         "из списка удалённый исчез")

    def test_default_attic_is_next_to_the_installation(self):
        # корень — вложенной папкой: чердак по умолчанию (`<base>/../_state/attic`)
        # тогда ложится в тот же tmp и убирается им же, без мусора в %TEMP%
        nested = self.root / "install"
        lay_out(nested, {"helene.json": {"agent": {"name": "Hélène"}, "port": 8094},
                         "agents/mira/helene.json": {"agent": {"name": "Мира"}}})
        dest = agents.remove_agent(nested, "mira")
        want = (nested.parent / "_state" / "attic").resolve()
        self.assertEqual(dest.parent.resolve(), want)
        self.assertTrue(dest.name.startswith("mira-"))

    def test_second_removal_of_the_same_id_never_overwrites(self):
        attic = self.root / "attic"
        first = agents.remove_agent(self.root, "mira", attic)
        lay_out(self.root, {"agents/mira/helene.json": {"agent": {"name": "Мира снова"}}})
        second = agents.remove_agent(self.root, "mira", attic)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir() and second.is_dir())

    def test_refusals(self):
        with self.assertRaises(ValueError):
            agents.remove_agent(self.root, "main")            # корневой — сама установка
        with self.assertRaises(ValueError):
            agents.remove_agent(self.root, "нет-такого")
        with self.assertRaises(ValueError):
            agents.remove_agent(self.root, "")

    def test_unwritable_attic_refuses_and_keeps_the_agent(self):
        blocker = self.root / "blocker"
        blocker.write_text("это файл, а не папка\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            agents.remove_agent(self.root, "mira", blocker / "attic")
        self.assertTrue((self.root / "agents" / "mira" / "helene.json").is_file(),
                        "агент остался на месте")


if __name__ == "__main__":
    unittest.main(verbosity=2)
