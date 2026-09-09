# -*- coding: utf-8 -*-
"""Стенд монтирования (задача C1/C2): разбор списка и решение «пускать или нет».

Запуск:  python tests/t_fence_mounts.py

Живьём здесь НЕ создаётся ни AppContainer, ни одна ACE: `Container` в прогоне
`install` подменён заглушкой, аргументы icacls проверяются как аргументы. Что
делается по-настоящему — стыки `mklink /J` во ВРЕМЕННОЙ папке (класс `Fenced`);
они не требуют прав, и папка сносится вместе с тестом. Их неудача ограду не
роняет, поэтому на машине с запретом junction'ов стенд всё равно зелёный.
Стенд лежит вне `localharness/`, потому что сборка поставки забирает оттуда ВСЕ
*.py (`installer/build_dist.py`), а тесты владельцу в папку программы не нужны.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))

import fence  # noqa: E402


def _cfg(mounts=None, denied=None):
    block = {"enabled": True, "network": True}
    if mounts is not None:
        block["mounts"] = mounts
    if denied is not None:
        block["mounts_denied"] = denied
    return {"agent_mode": "sandbox", "sandbox": block}


class Ground:
    """Папка установки, дерево данных и папка владельца — на время одного теста."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="helene-mounts-")
        base = Path(self.tmp.name).resolve()
        self.root = base / "Helene"
        self.tree = self.root / "data"
        self.workspace = self.tree / "workspace"
        self.outside = base / "Документы"
        self.other = base / "Проект"
        for path in (self.root, self.tree, self.workspace, self.outside, self.other):
            path.mkdir(parents=True, exist_ok=True)
        self.config = self.root / "helene.json"
        self.write({})

    def write(self, cfg: dict) -> None:
        self.config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        # mtime у файла на NTFS шагает не по наносекунде: в стенде две записи
        # подряд могут дать один штамп, и Mounts честно решит, что ничего не
        # менялось. Размер входит в штамп, а тут добьём и время.
        stamp = os.stat(self.config)
        os.utime(self.config, ns=(stamp.st_atime_ns + 10**9,
                                  stamp.st_mtime_ns + 10**9))

    def mounts(self, cfg: dict | None = None) -> fence.Mounts:
        if cfg is not None:
            self.write(cfg)
        return fence.Mounts(self.config, self.root, self.tree, self.workspace,
                            links=False)

    def close(self):
        self.tmp.cleanup()


class MountParsing(unittest.TestCase):
    def setUp(self):
        self.g = Ground()
        self.addCleanup(self.g.close)

    def test_string_row_is_read_only(self):
        rows = fence.parse_mounts(_cfg([str(self.g.outside)]), self.g.root, self.g.tree)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["access"], "read")
        self.assertEqual(rows[0]["error"], "")

    def test_access_words(self):
        rows = fence.parse_mounts(
            _cfg([{"path": str(self.g.outside), "access": "write"},
                  {"path": str(self.g.other), "write": True}]),
            self.g.root, self.g.tree)
        self.assertEqual([r["access"] for r in rows], ["write", "write"])

    def test_unknown_access_falls_back_to_read(self):
        # Опечатка в конфиге не должна тихо ВЫДАВАТЬ запись.
        rows = fence.parse_mounts(_cfg([{"path": str(self.g.outside),
                                         "access": "полный"}]),
                                  self.g.root, self.g.tree)
        self.assertEqual(rows[0]["access"], "read")

    def test_duplicate_row_ignored(self):
        rows = fence.parse_mounts(
            _cfg([str(self.g.outside), {"path": str(self.g.outside) + "\\",
                                        "access": "write"}]),
            self.g.root, self.g.tree)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["access"], "read")

    def test_broken_row_is_named_not_dropped(self):
        rows = fence.parse_mounts(_cfg(["данные", str(self.g.outside)]),
                                  self.g.root, self.g.tree)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["error"])
        self.assertEqual(rows[1]["error"], "")

    def test_no_block_no_rows(self):
        self.assertEqual(fence.parse_mounts({}, self.g.root, self.g.tree), [])
        self.assertEqual(fence.parse_mounts(_cfg(), self.g.root, self.g.tree), [])

    def test_denied_parsed(self):
        rows = fence.parse_denied(_cfg(denied=[{"path": str(self.g.other),
                                                "why": "личное", "at": "04.09.2026"}]))
        self.assertEqual(rows[0]["why"], "личное")


class MountRefusal(unittest.TestCase):
    def setUp(self):
        self.g = Ground()
        self.addCleanup(self.g.close)

    def refuse(self, path):
        return fence.mount_refusal(fence.normalize_mount_path(path)
                                   if isinstance(path, str) else path,
                                   self.g.root, self.g.tree)

    def test_folder_outside_is_fine(self):
        self.assertEqual(self.refuse(str(self.g.outside)), "")

    def test_drive_root_refused(self):
        self.assertIn("корень диска", self.refuse("C:\\"))

    def test_system_folder_refused(self):
        windir = os.environ.get("windir") or "C:\\Windows"
        self.assertIn("системная папка", self.refuse(windir))
        self.assertIn("системная папка", self.refuse(str(Path(windir) / "System32")))

    def test_folder_holding_helene_refused(self):
        # Папка ВЫШЕ установки: смонтировать её значит отдать helene.json.
        self.assertIn("сама Hélène", self.refuse(str(self.g.root.parent)))

    def test_own_home_refused(self):
        self.assertIn("внутри папки Hélène", self.refuse(str(self.g.tree)))
        self.assertIn("внутри папки Hélène", self.refuse(str(self.g.workspace)))

    def test_missing_and_file(self):
        self.assertIn("папки нет", self.refuse(str(self.g.outside / "нет")))
        stray = self.g.outside / "файл.txt"
        stray.write_text("x", encoding="utf-8")
        self.assertIn("это файл", self.refuse(str(stray)))

    def test_relative_path_refused(self):
        self.assertIsNone(fence.normalize_mount_path("Документы"))
        self.assertIn("полный путь", self.refuse("Документы"))


class Letting(unittest.TestCase):
    """Решение «пускать или нет» — то самое, ради чего список вообще есть."""

    def setUp(self):
        self.g = Ground()
        self.addCleanup(self.g.close)

    def test_nothing_mounted_nothing_open(self):
        m = self.g.mounts(_cfg([]))
        self.assertIsNone(m.resolve(str(self.g.outside / "а.txt")))
        self.assertEqual(m.roots(), [])

    def test_read_mount_opens_read_not_write(self):
        m = self.g.mounts(_cfg([str(self.g.outside)]))
        target = str(self.g.outside / "а.txt")
        self.assertIsNotNone(m.resolve(target))
        self.assertIsNone(m.resolve(target, write=True))

    def test_write_mount_opens_both(self):
        m = self.g.mounts(_cfg([{"path": str(self.g.outside), "access": "write"}]))
        target = str(self.g.outside / "вглубь" / "а.txt")
        self.assertIsNotNone(m.resolve(target))
        self.assertIsNotNone(m.resolve(target, write=True))

    def test_neighbour_folder_stays_shut(self):
        # Смонтирована одна папка — соседняя не открывается по префиксу имени.
        m = self.g.mounts(_cfg([str(self.g.outside)]))
        twin = self.g.outside.parent / (self.g.outside.name + "-2")
        twin.mkdir()
        self.assertIsNone(m.resolve(str(twin / "а.txt")))

    def test_dotdot_does_not_escape(self):
        m = self.g.mounts(_cfg([str(self.g.outside)]))
        self.assertIsNone(m.resolve(str(self.g.outside / ".." / "Проект" / "а.txt")))

    def test_broken_row_opens_nothing(self):
        m = self.g.mounts(_cfg([str(self.g.root.parent)]))   # отказ: держит Hélène
        self.assertEqual(m.roots(), [])
        self.assertIsNone(m.resolve(str(self.g.root / "helene.json")))

    def test_owner_edit_takes_effect_without_restart(self):
        m = self.g.mounts(_cfg([]))
        target = str(self.g.other / "а.txt")
        self.assertIsNone(m.resolve(target))
        self.g.write(_cfg([{"path": str(self.g.other), "access": "write"}]))
        self.assertIsNotNone(m.resolve(target, write=True))

    def test_unmount_closes_again(self):
        m = self.g.mounts(_cfg([str(self.g.other)]))
        self.assertIsNotNone(m.resolve(str(self.g.other / "а.txt")))
        self.g.write(_cfg([]))
        self.assertIsNone(m.resolve(str(self.g.other / "а.txt")))


class Asking(unittest.TestCase):
    """C2: просьба агента, ответ владельца и запрет спрашивать по кругу."""

    def setUp(self):
        self.g = Ground()
        self.addCleanup(self.g.close)

    def test_request_recorded_once(self):
        m = self.g.mounts(_cfg([]))
        first = m.ask(str(self.g.other), "там лежит текст, который ты просил править",
                      "write")
        self.assertIn("записал просьбу", first)
        rows = m.load_requests()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["access"], "write")
        second = m.ask(str(self.g.other), "всё ещё нужна", "write")
        self.assertIn("уже ждёт владельца", second)
        self.assertEqual(len(m.load_requests()), 1)
        self.assertEqual(m.load_requests()[0]["asked"], 2)

    def test_why_is_required(self):
        m = self.g.mounts(_cfg([]))
        self.assertIn("ЗАЧЕМ", m.ask(str(self.g.other), " ", "read"))
        self.assertEqual(m.load_requests(), [])

    def test_impossible_folder_is_not_recorded(self):
        m = self.g.mounts(_cfg([]))
        said = m.ask("C:\\", "хочу всё", "write")
        self.assertIn("корень диска", said)
        self.assertEqual(m.load_requests(), [])

    def test_denied_answer_repeats_itself(self):
        m = self.g.mounts(_cfg([], denied=[{"path": str(self.g.other),
                                            "why": "там личное",
                                            "at": "04.09.2026"}]))
        said = m.ask(str(self.g.other), "очень нужно", "read")
        self.assertIn("владелец отказал", said)
        self.assertIn("там личное", said)
        self.assertEqual(m.load_requests(), [])

    def test_granted_folder_answers_itself(self):
        m = self.g.mounts(_cfg([{"path": str(self.g.other), "access": "write"}]))
        said = m.ask(str(self.g.other), "нужна на запись", "write")
        self.assertIn("уже открыта", said)

    def test_answered_request_forgotten(self):
        m = self.g.mounts(_cfg([]))
        m.ask(str(self.g.other), "нужна папка проекта", "read")
        self.assertEqual(len(m.load_requests()), 1)
        self.g.write(_cfg([str(self.g.other)]))          # владелец подтвердил
        m.refresh()
        m.forget_answered()
        self.assertEqual(m.load_requests(), [])

    def test_hand_lists_without_asking(self):
        m = self.g.mounts(_cfg([str(self.g.other)],
                               denied=[{"path": str(self.g.outside), "why": "нет"}]))
        hand = fence._mount_hand(m)
        said = hand(action="list")
        self.assertIn(str(self.g.other), said)
        self.assertIn("Отказано", said)

    def test_hand_survives_a_broken_call(self):
        m = self.g.mounts(_cfg([]))
        hand = fence._mount_hand(m)
        self.assertIn("action бывает", hand(action="что-то", path="x", why="y"))


class Arguments(unittest.TestCase):
    """Аргументы icacls и mklink: проверяем ИХ, а не выполнение."""

    def test_grant_rights(self):
        sid = "S-1-15-2-1-2-3"
        read = fence.mount_grant_args(sid, Path("C:\\Док"), "read")
        write = fence.mount_grant_args(sid, Path("C:\\Док"), "write")
        self.assertEqual(read, ["C:\\Док", "/grant", f"*{sid}:(OI)(CI)RX"])
        self.assertEqual(write, ["C:\\Док", "/grant", f"*{sid}:(OI)(CI)M"])
        # Ни /T, ни /inheritance:r, ни F: разбор — в докстрингах.
        for args in (read, write):
            self.assertNotIn("/T", args)
            self.assertNotIn("/inheritance:r", args)
        self.assertNotIn(":(OI)(CI)F", write[2])

    def test_revoke_takes_both_kinds_of_ace(self):
        args = fence.mount_revoke_args("S-1-15-2-9", Path("C:\\Док"))
        self.assertEqual(args, ["C:\\Док", "/remove:g", "*S-1-15-2-9",
                                "/remove:d", "*S-1-15-2-9"])

    def test_link_is_a_junction(self):
        args = fence.mount_link_args(Path("C:\\H\\mnt\\Док"), Path("D:\\Док"))
        self.assertEqual(args[:4], ["cmd", "/c", "mklink", "/J"])

    def test_link_names_do_not_collide(self):
        taken: set[str] = set()
        first = fence.mount_link_name(Path("C:\\a\\Документы"), taken)
        taken.add(first.lower())
        second = fence.mount_link_name(Path("D:\\b\\Документы"), taken)
        self.assertEqual(first, "Документы")
        self.assertNotEqual(first, second)
        self.assertTrue(second.startswith("Документы-"))


class TreeGuard(unittest.TestCase):
    """Гард дерева: расширяем его решение, а не заменяем."""

    def setUp(self):
        self.g = Ground()
        self.addCleanup(self.g.close)
        self.saved = sys.modules.get("workshop")
        self.shop = types.ModuleType("workshop")
        home = self.g.tree

        def _resolve_read(path):
            p = Path(path)
            p = p if p.is_absolute() else home / p
            try:
                p.resolve().relative_to(home)
            except ValueError:
                return None
            return p.resolve()

        def _resolve_write(path, proposal_id=""):
            got = _resolve_read(path)
            return (got, "") if got is not None else (None, "путь вне дома")

        self.shop._resolve_read = _resolve_read
        self.shop._resolve_write = _resolve_write
        sys.modules["workshop"] = self.shop

    def tearDown(self):
        if self.saved is None:
            sys.modules.pop("workshop", None)
        else:
            sys.modules["workshop"] = self.saved

    def test_home_still_works_and_mount_opens(self):
        m = self.g.mounts(_cfg([{"path": str(self.g.other), "access": "write"}]))
        fence._open_home(types.SimpleNamespace(), m)
        inside = self.g.tree / "memory" / "заметка.md"
        self.assertIsNotNone(self.shop._resolve_read(str(inside)))
        self.assertIsNotNone(self.shop._resolve_read(str(self.g.other / "а.txt")))
        self.assertIsNone(self.shop._resolve_read(str(self.g.outside / "а.txt")))

    def test_read_only_mount_says_what_to_do(self):
        m = self.g.mounts(_cfg([str(self.g.other)]))
        fence._open_home(types.SimpleNamespace(), m)
        got, err = self.shop._resolve_write(str(self.g.other / "а.txt"))
        self.assertIsNone(got)
        self.assertIn("mount_request", err)
        got, err = self.shop._resolve_write(str(self.g.outside / "а.txt"))
        self.assertIsNone(got)
        self.assertEqual(err, "путь вне дома")

    def test_patch_is_applied_once(self):
        m = self.g.mounts(_cfg([]))
        fence._open_home(types.SimpleNamespace(), m)
        once = self.shop._resolve_read
        fence._open_home(types.SimpleNamespace(), m)
        self.assertIs(once, self.shop._resolve_read)


class Fenced(unittest.TestCase):
    """Обёртка ограды: те же руки, но корни считаются на КАЖДОМ вызове."""

    def setUp(self):
        self.g = Ground()
        self.addCleanup(self.g.close)
        self.saved_state = dict(fence.STATE)
        self.addCleanup(lambda: fence.STATE.update(self.saved_state))
        self.saved_shop = sys.modules.get("workshop")
        shop = types.ModuleType("workshop")
        shop._resolve_read = lambda path: None
        shop._resolve_write = lambda path, proposal_id="": (None, "путь вне дома")
        sys.modules["workshop"] = shop
        self.addCleanup(self._restore_shop)
        self.saved_container = fence.Container
        fence.Container = self._fake_container()
        self.addCleanup(lambda: setattr(fence, "Container", self.saved_container))

    def _restore_shop(self):
        if self.saved_shop is None:
            sys.modules.pop("workshop", None)
        else:
            sys.modules["workshop"] = self.saved_shop

    @staticmethod
    def _fake_container():
        class Fake:               # ни AppContainer, ни icacls: стенд их не трогает
            def __init__(self, root, workspace, network):
                self.sid_text = "S-1-15-2-СТЕНД"

            def prepare(self):
                pass

            def sync_mounts(self, rows):
                pass
        return Fake

    def agent_with(self, cfg):
        self.g.write(cfg)
        seen = []
        agent = types.SimpleNamespace(
            TOOL_IMPL={"fs_read": lambda path="": seen.append(("read", path)) or "ок",
                       "fs_write": lambda path="", content="":
                           seen.append(("write", path)) or "ок"},
            BASE_TOOLS=[], BASE=str(self.g.tree), subprocess=__import__("subprocess"))
        fence.install(agent, self.g.tree, cfg, config_path=self.g.config)
        return agent, seen

    def test_read_mount_opens_reading_and_refuses_writing_by_name(self):
        agent, seen = self.agent_with(_cfg([str(self.g.outside)]))
        target = str(self.g.outside / "а.txt")
        self.assertEqual(agent.TOOL_IMPL["fs_read"](path=target), "ок")
        said = agent.TOOL_IMPL["fs_write"](path=target, content="x")
        self.assertIn("только на чтение", said)
        self.assertIn("mount_request", said)
        self.assertEqual(seen, [("read", target)])

    def test_foreign_path_refused_and_points_at_the_hand(self):
        agent, _ = self.agent_with(_cfg([]))
        said = agent.TOOL_IMPL["fs_read"](path=str(self.g.outside / "а.txt"))
        self.assertIn("вне разрешённого", said)
        self.assertIn("mount_request", said)

    def test_new_mount_works_without_restart(self):
        agent, _ = self.agent_with(_cfg([]))
        target = str(self.g.other / "а.txt")
        self.assertIn("вне разрешённого", agent.TOOL_IMPL["fs_read"](path=target))
        self.g.write(_cfg([{"path": str(self.g.other), "access": "write"}]))
        self.assertEqual(agent.TOOL_IMPL["fs_read"](path=target), "ок")
        self.assertEqual(agent.TOOL_IMPL["fs_write"](path=target, content="x"), "ок")


class Windows(unittest.TestCase):
    """Рука окон — не дело ограды: строку про окна ограда берёт у тела.

    Сама рука и её отказы проверяются в `tests/t_body.py`; здесь только шов:
    ограда не заводит второй правды, а спрашивает `body.windows_truth()`.
    """

    def test_state_line_comes_from_the_body_module(self):
        import body
        body.STATE.update({"enabled": False, "available": False})
        self.assertEqual(fence.windows_truth(), body.windows_truth())
        self.assertIn("окна:", fence.windows_truth())
        body.STATE.update({"enabled": True, "available": True, "port": 9480,
                           "scopes": ["computer.apps"], "reason": "проба"})
        self.assertIn("9480", fence.windows_truth())
        self.assertIn("computer.apps", fence.windows_truth())
        body.STATE.update({"enabled": False, "available": False, "port": 0,
                           "scopes": [], "reason": "не поднималось"})


class Hand(unittest.TestCase):
    def setUp(self):
        self.g = Ground()
        self.addCleanup(self.g.close)

    def test_hand_lands_in_both_lists(self):
        agent = types.SimpleNamespace(TOOL_IMPL={}, BASE_TOOLS=[])
        m = self.g.mounts(_cfg([]))
        fence._offer_mount_hand(agent, m)
        fence._offer_mount_hand(agent, m)          # второй раз — без дубля
        self.assertIn("mount_request", agent.TOOL_IMPL)
        self.assertEqual([t["name"] for t in agent.BASE_TOOLS], ["mount_request"])
        self.assertTrue(callable(agent.TOOL_IMPL["mount_request"]))


class Interactive(unittest.TestCase):
    """Без ограды файловые руки видят всё, что доступно учётке (слово владельца 06.09)."""

    def setUp(self):
        self.g = Ground()
        self.addCleanup(self.g.close)
        self.saved_state = dict(fence.STATE)
        self.addCleanup(lambda: fence.STATE.update(self.saved_state))
        self.saved_shop = sys.modules.get("workshop")
        home = self.g.tree
        shop = types.ModuleType("workshop")

        def _resolve_read(path):           # гард дерева: только дом
            p = Path(path)
            p = p if p.is_absolute() else home / p
            try:
                p.resolve().relative_to(home)
            except ValueError:
                return None
            if p.name == "llm.json":       # секрет внутри дома — отказ дерева
                return None
            return p.resolve()

        shop._resolve_read = _resolve_read
        shop._resolve_write = lambda path, proposal_id="": (
            (_resolve_read(path), "") if _resolve_read(path) is not None else (None, "путь вне дома"))
        sys.modules["workshop"] = shop
        self.addCleanup(self._restore_shop)
        self.saved_container = fence.Container
        fence.Container = Fenced._fake_container()
        self.addCleanup(lambda: setattr(fence, "Container", self.saved_container))
        self.shop = shop

    def _restore_shop(self):
        if self.saved_shop is None:
            sys.modules.pop("workshop", None)
        else:
            sys.modules["workshop"] = self.saved_shop

    def install(self, cfg):
        self.g.write(cfg)
        agent = types.SimpleNamespace(TOOL_IMPL={}, BASE_TOOLS=[], BASE=str(self.g.tree),
                                      subprocess=__import__("subprocess"))
        fence.install(agent, self.g.tree, cfg, config_path=self.g.config)
        return agent

    def test_outside_opens_for_reading_and_writing(self):
        self.install({"agent_mode": "interactive", "sandbox": {"enabled": False}})
        target = str(self.g.outside / "а.txt")
        self.assertIsNotNone(self.shop._resolve_read(target))
        got, err = self.shop._resolve_write(target)
        self.assertIsNotNone(got)
        self.assertEqual(err, "")

    def test_tree_rules_inside_home_survive(self):
        self.install({"agent_mode": "interactive", "sandbox": {"enabled": False}})
        secret = str(self.g.tree / "memory" / "llm.json")
        self.assertIsNone(self.shop._resolve_read(secret), "секрет внутри дома остаётся отказом дерева")
        # Относительный путь решает дерево: внутри дома — пускает, побег через
        # `..` — нет, и обёртка его не спасает.
        self.assertIsNotNone(self.shop._resolve_read("заметка.md"))
        self.assertIsNone(self.shop._resolve_read("../../Документы/а.txt"), "относительный побег — отказ дерева")

    def test_no_mount_hand_and_state_says_why(self):
        agent = self.install({"agent_mode": "interactive",
                              "sandbox": {"enabled": False, "mounts": [str(self.g.other)]}})
        self.assertNotIn("mount_request", agent.TOOL_IMPL)
        self.assertFalse(any(t.get("name") == "mount_request" for t in agent.BASE_TOOLS))
        self.assertIn("монтирование не нужно", fence.STATE["reason"])
        self.assertFalse((self.g.workspace / "mnt").exists(), "стыков без ограды не делаем")

    def test_sandbox_still_mounts(self):
        agent = self.install(_cfg([str(self.g.other)]))
        self.assertIn("mount_request", agent.TOOL_IMPL)
        self.assertIsNone(self.shop._resolve_read(str(self.g.outside / "а.txt")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
