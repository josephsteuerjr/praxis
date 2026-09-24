# -*- coding: utf-8 -*-
"""Стенд пакета desk: один состав на обе установки.

Запуск:  python tests/t_deskpkg.py

Класс ошибки, ради которого стенд написан: «на сервере есть, а в поставке нет».
Он уже стрелял дважды — `rooms.py` 07.09 не уехал в поставку, мини-апп ехал
только на сервер, — потому что состав перечисляли в двух местах. Здесь
проверяется, что состав объявлен один раз (`deskpkg.PARTS`), что собранный
пакет сходится со своим манифестом, что байт-код и полусборки не уезжают молча
и что обе установки берут состав ИМЕННО отсюда: выкладка на сервер и сборка
поставки.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import deskpkg  # noqa: E402
from deskd import readers  # noqa: E402


class Composition(unittest.TestCase):
    def test_flavors_differ_only_where_declared(self):
        server = {p.dest for p in deskpkg.parts(deskpkg.SERVER)}
        windows = {p.dest for p in deskpkg.parts(deskpkg.WINDOWS)}
        macos = {p.dest for p in deskpkg.parts(deskpkg.MACOS)}
        # Общее ядро — канал, читалки и оба фронта: в этом и смысл одного пакета.
        self.assertLessEqual({"deskapp.py", "deskd", "static", "mobile"}, server & windows)
        # Мини-апп открывает Telegram по публичному адресу — у настольных его нет.
        self.assertIn("miniapp", server)
        self.assertNotIn("miniapp", windows)
        self.assertNotIn("miniapp", macos)
        # Движок и ресурсы — настольные: на сервере ходы ведёт её собственный код.
        self.assertLessEqual({"localharness", "resources"}, windows)
        self.assertFalse({"localharness", "resources"} & server)
        # macOS — тот же состав, что Windows: разница в том, что сборка кладёт
        # ВОКРУГ пакета (рантайм, exe/app), а не в самом пакете.
        self.assertEqual(macos, windows)
        self.assertEqual([p.src for p in deskpkg.parts(deskpkg.MACOS)],
                         [p.src for p in deskpkg.parts(deskpkg.WINDOWS)])
        self.assertEqual(deskpkg.FLAVORS, (deskpkg.SERVER, deskpkg.WINDOWS, deskpkg.MACOS))
        self.assertEqual(deskpkg.MACOS, "macos")

    def test_unknown_flavor_is_refused(self):
        with self.assertRaises(ValueError):
            deskpkg.parts("linux")

    def test_requirements_follow_the_parts(self):
        self.assertEqual(deskpkg.requirements(deskpkg.SERVER), deskpkg.DEPS_CHANNEL)
        self.assertEqual(deskpkg.requirements(deskpkg.WINDOWS),
                         deskpkg.DEPS_CHANNEL + deskpkg.DEPS_RUNNER)
        self.assertEqual(deskpkg.requirements(deskpkg.MACOS),
                         deskpkg.requirements(deskpkg.WINDOWS))

    def test_version_is_the_product_version(self):
        version, declared = deskpkg.product_version()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(declared["shell/Cargo.toml"], version)

    def test_sources_are_all_in_place(self):
        for flavor in deskpkg.FLAVORS:
            self.assertEqual(deskpkg.check(flavor), [],
                             f"{flavor}: собери фронты (npm --prefix … run build)")


class Build(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_package_matches_its_manifest(self):
        man = deskpkg.build(self.tmp / "srv", deskpkg.SERVER, clean=True, log=lambda *_: None)
        self.assertEqual(man["product"], "desk")
        self.assertEqual(man["flavor"], deskpkg.SERVER)
        self.assertEqual(deskpkg.verify(self.tmp / "srv"), [])
        # Манифест — не украшение: подмена файла видна сверкой.
        (self.tmp / "srv" / "deskapp.py").write_text("# подменён\n", encoding="utf-8")
        self.assertEqual(deskpkg.verify(self.tmp / "srv"), ["изменён: deskapp.py"])

    def test_digest_is_stable(self):
        one = deskpkg.build(self.tmp / "a", deskpkg.SERVER, clean=True, log=lambda *_: None)
        two = deskpkg.build(self.tmp / "b", deskpkg.SERVER, clean=True, log=lambda *_: None)
        self.assertEqual(one["digest"], two["digest"])
        # Разный вид — разный отпечаток: иначе выкладка сверила бы не то.
        win = deskpkg.build(self.tmp / "c", deskpkg.WINDOWS, clean=True, log=lambda *_: None)
        self.assertNotEqual(one["digest"], win["digest"])
        # macOS кладёт те же файлы, но манифест и requirements называют свой
        # вид — отпечаток свой, и по нему поставки различимы.
        mac = deskpkg.build(self.tmp / "d", deskpkg.MACOS, clean=True, log=lambda *_: None)
        self.assertEqual(mac["flavor"], deskpkg.MACOS)
        self.assertEqual([p["name"] for p in mac["parts"]], [p["name"] for p in win["parts"]])
        self.assertNotEqual(mac["digest"], win["digest"])

    def test_top_level_covers_everything_installed(self):
        for flavor in deskpkg.FLAVORS:
            root = self.tmp / flavor
            deskpkg.build(root, flavor, clean=True, log=lambda *_: None)
            self.assertEqual(sorted(p.name for p in root.iterdir()),
                             sorted(deskpkg.top_level(flavor)),
                             "то, что установка подменяет, разошлось с тем, что кладёт пакет")

    def test_bytecode_and_non_python_never_travel(self):
        root = self.tmp / "win"
        deskpkg.build(root, deskpkg.WINDOWS, clean=True, log=lambda *_: None)
        travelled = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
        self.assertFalse([r for r in travelled if "__pycache__" in r or r.endswith(".pyc")])
        # Часть «py» — только исходники: чужой мусор рядом с runner.py не едет.
        self.assertTrue(all(r.endswith(".py") for r in travelled if r.startswith("localharness/")))
        self.assertIn("localharness/runner.py", travelled)

    def test_static_manifest_lies_inside_static(self):
        root = self.tmp / "srv"
        man = deskpkg.build(root, deskpkg.SERVER, clean=True, log=lambda *_: None)
        inside = json.loads((root / "static" / deskpkg.STATIC_MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(inside["digest"], man["static"])
        self.assertIn("index.html", inside["files"])

    def test_partial_is_loud_and_written_down(self):
        """Полусборка без фронта — только под флагом, и она видна в манифесте."""
        said: list[str] = []
        with self.assertRaises(SystemExit):
            _fake_missing_front(self, deskpkg.build, self.tmp / "hard", deskpkg.SERVER)
        man = _fake_missing_front(self, deskpkg.build, self.tmp / "soft", deskpkg.SERVER,
                                  allow_partial=True, log=said.append)
        self.assertEqual([s["name"] for s in man["skipped"]], ["mobile"])
        self.assertTrue(any("MOBILE" in s for s in said))
        self.assertFalse((self.tmp / "soft" / "mobile").exists())
        # Всё остальное по-прежнему обязательно: пакет собран, но неполон.
        self.assertEqual(deskpkg.verify(self.tmp / "soft"), [])

    def test_missing_python_is_never_forgiven(self):
        """Нет `deskapp.py` — отказ даже с --allow-partial: это не фронт."""
        with tempfile.TemporaryDirectory() as fake:
            _copy_repo_without(Path(fake), skip=("deskapp.py",))
            with _desk_at(Path(fake)):
                with self.assertRaises(SystemExit):
                    deskpkg.build(self.tmp / "x", deskpkg.SERVER, clean=True,
                                  allow_partial=True, version="0.0.0", log=lambda *_: None)


class ChannelReadsIt(unittest.TestCase):
    """Канал называет свой пакет: /api/state -> desk. Иначе на сервере версию
    узнать неоткуда — оболочки, которая отвечает `app_info`, там нет."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_reads_version_and_short_digest(self):
        man = deskpkg.build(self.root, deskpkg.SERVER, clean=False, log=lambda *_: None)
        got = readers.desk_build(self.root)
        self.assertEqual(got["version"], man["version"])
        self.assertEqual(got["flavor"], deskpkg.SERVER)
        self.assertEqual(got["digest"], man["digest"][:12])

    def test_without_package_says_nothing(self):
        self.assertEqual(readers.desk_build(self.root / "нет-такой-папки"), {})
        (self.root / "desk.json").write_text("{не json", encoding="utf-8")
        self.assertEqual(readers.desk_build(self.root), {})
        (self.root / "desk.json").write_text('{"product": "чужое"}', encoding="utf-8")
        self.assertEqual(readers.desk_build(self.root), {})


class BothInstallsTakeItFromHere(unittest.TestCase):
    """Выкладка на сервер и сборка поставки берут состав из пакета, а не свой."""

    def test_deploy_builds_the_package(self):
        sys.path.insert(0, str(ROOT / "server"))
        import deploy_desk  # noqa: PLC0415 — стенд смотрит именно на модуль выкладки
        self.assertIs(deploy_desk.deskpkg, deskpkg)
        # Собирается ровно то, что объявлено фронтами пакета для сервера.
        # ⚠ На сервере окно — Praxis, а не Элен: приложения разделены 10.09, и
        # окно Элен знает про местного агента, которого на сервере нет.
        self.assertEqual(deploy_desk.FRONTS, ["miniapp", "mobile", "remote"])
        src = (ROOT / "server" / "deploy_desk.py").read_text(encoding="utf-8")
        self.assertNotIn("MODULE", src, "список путей вернулся в выкладку")

    def test_distribution_deps_include_the_package(self):
        sys.path.insert(0, str(ROOT / "installer"))
        import build_dist  # noqa: PLC0415 — стенд смотрит на сборку поставки
        self.assertIs(build_dist.product_version, deskpkg.product_version)
        self.assertIs(build_dist.copy_static, deskpkg.copy_static)
        for dep in deskpkg.requirements(deskpkg.WINDOWS):
            self.assertIn(dep, build_dist.DEPS)
        src = (ROOT / "installer" / "build_dist.py").read_text(encoding="utf-8")
        self.assertIn("deskpkg.build(dest / \"app\"", src)


# --- вспомогательное ----------------------------------------------------------

def _copy_repo_without(dest: Path, skip: tuple[str, ...]) -> None:
    """Копия корня репозитория без части файлов — чтобы проверять отказы."""
    for part in deskpkg.PARTS:
        if part.src in skip:
            continue
        src, dst = deskpkg.DESK / part.src, dest / part.src
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_file():
            shutil.copy2(src, dst)
        elif src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for rel in ("shell/Cargo.toml", "setup/Cargo.toml", "svc/Cargo.toml"):
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(deskpkg.DESK / rel, dest / rel)


class _desk_at:
    """Подменить корень репозитория для deskpkg на время проверки."""

    def __init__(self, root: Path):
        self.root, self.was = root, deskpkg.DESK

    def __enter__(self):
        deskpkg.DESK = self.root
        return self

    def __exit__(self, *exc):
        deskpkg.DESK = self.was
        return False


def _fake_missing_front(case, build, dest, flavor, **kw):
    """Собрать пакет из копии репозитория, где телефон не собран."""
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    fake = Path(tmp.name)
    _copy_repo_without(fake, skip=("mobile/dist",))
    with _desk_at(fake):
        return build(dest, flavor, clean=True, version="0.0.0", **kw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
