"""
Тесты рук Praxis: чистый Python, одна реализация на операцию.

Прежде здесь сверялись две реализации (питон и бинарь praxis-hands). Теперь исполнитель один,
поэтому проверять надо другое:
  1. корпус фактов о мастерской — тот же, что держал обе реализации;
  2. второго исполнителя нет: заданный PRAXIS_HANDS ни при каких условиях не запускается;
  3. контракт правки: подтверждено чтением результата, отказ до действия, честное
     «исход неизвестен» — и байты файла не теряются ни на одном из отказов;
  4. запуск и поиск ведут себя как обещают под нагрузкой и на сбоях.

Запуск:  python praxis_test.py test_hands -v
"""

from __future__ import annotations

import collections
import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import forge  # noqa: E402
import hands  # noqa: E402
import workshop  # noqa: E402


class _ReleaseFails:
    """Замок, который взялся, а отпуститься не смог."""

    lost_to_takeover = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        raise OSError("release failed")


class Home:
    """Одноразовый дом мастерской."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_hands_t_"))
        for d in ("workspace/projects", "soul", "memory/.state"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.tmp / ".env").write_text("SECRET=x\n", encoding="utf-8")
        (self.tmp / "memory" / "llm.json").write_text('{"k":"secret"}', encoding="utf-8")
        self._orig = [(workshop, k, getattr(workshop, k)) for k in ("BASE", "REPO", "PROJECTS")]
        workshop.BASE = workshop.REPO = self.tmp
        workshop.PROJECTS = self.tmp / "workspace" / "projects"

    def tearDown(self):
        for m, k, v in self._orig:
            setattr(m, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def leftovers(self) -> list[str]:
        return [p.name for p in self.tmp.rglob(".praxis-write-*")]


# --------------------------------------------------------------------------- #
#  1. Корпус фактов о мастерской
# --------------------------------------------------------------------------- #

class TestWorkshopPython(Home, unittest.TestCase):
    # --- зоны; имя файла не является границей ----------------------------- #

    def test_core_write_needs_proposal(self):
        out = workshop.fs_write("core.py", "x = 2")
        self.assertIn("предложение", out.lower())
        self.assertEqual((self.tmp / "core.py").read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_credential_named_file_can_be_edited(self):
        out = workshop.fs_edit("memory/llm.json", "secret", "changed")
        self.assertIn("1 вхождение", out)
        self.assertEqual((self.tmp / "memory" / "llm.json").read_text(encoding="utf-8"),
                         '{"k":"changed"}')

    def test_escape_from_home_refused(self):
        out = workshop.fs_write("../../etc/evil.txt", "boom")
        self.assertTrue(out.startswith("Не пишу"), out)

    # --- fs_write: перезапись и гард усечения (16.5) ------------------------ #

    def test_new_file_written(self):
        self.assertIn("Записала", workshop.fs_write("workspace/a.py", "print(1)\n"))
        self.assertEqual((self.tmp / "workspace" / "a.py").read_text(encoding="utf-8"), "print(1)\n")

    def test_existing_file_refused_without_overwrite(self):
        workshop.fs_write("workspace/a.py", "x" * 100)
        out = workshop.fs_write("workspace/a.py", "y" * 100)
        self.assertIn("уже существует", out)
        self.assertIn("overwrite", out)

    def test_overwrite_allowed_when_asked(self):
        workshop.fs_write("workspace/a.py", "x" * 100)
        out = workshop.fs_write("workspace/a.py", "y" * 100, overwrite=True)
        self.assertIn("Записала", out)
        self.assertEqual((self.tmp / "workspace" / "a.py").read_text(encoding="utf-8"), "y" * 100)

    def test_shrink_guard_blocks_truncation(self):
        workshop.fs_write("workspace/a.py", "x" * 100)
        out = workshop.fs_write("workspace/a.py", "y" * 50, overwrite=True)
        self.assertIn("усечение", out)
        self.assertEqual(len((self.tmp / "workspace" / "a.py").read_text(encoding="utf-8")), 100)

    def test_shrink_guard_lifted_by_force(self):
        workshop.fs_write("workspace/a.py", "x" * 100)
        out = workshop.fs_write("workspace/a.py", "y" * 50, overwrite=True, force=True)
        self.assertIn("Записала", out)
        self.assertEqual(len((self.tmp / "workspace" / "a.py").read_text(encoding="utf-8")), 50)

    # --- fs_edit: точность и подсказки строк (16.5) ------------------------- #

    def test_edit_unique_occurrence(self):
        workshop.fs_write("workspace/a.py", "a = 1\nb = 2\n")
        out = workshop.fs_edit("workspace/a.py", "b = 2", "b = 3")
        self.assertIn("1 вхождение", out)
        self.assertIn("b = 3", (self.tmp / "workspace" / "a.py").read_text(encoding="utf-8"))

    def test_edit_zero_matches_is_honest(self):
        workshop.fs_write("workspace/a.py", "a = 1\n")
        out = workshop.fs_edit("workspace/a.py", "нет такого", "x")
        self.assertIn("0 совпадений", out)

    def test_edit_ambiguous_names_line_numbers(self):
        workshop.fs_write("workspace/a.py", "dup\nx\ndup\ny\ndup\n")
        out = workshop.fs_edit("workspace/a.py", "dup", "new")
        self.assertIn("3 совпадений", out)
        self.assertIn("строка 1", out)
        self.assertIn("строка 3", out)
        self.assertIn("dup\nx\ndup", (self.tmp / "workspace" / "a.py").read_text(encoding="utf-8"))

    def test_edit_missing_file(self):
        self.assertIn("Нет файла", workshop.fs_edit("workspace/нет.py", "a", "b"))

    # --- code_outline (16.5) ----------------------------------------------- #

    def test_outline_lists_defs_and_classes(self):
        workshop.fs_write("workspace/m.py", "import os\n\n\nclass A:\n    def m(self):\n        pass\n\n\ndef top():\n    pass\n")
        out = workshop.code_outline("workspace/m.py")
        self.assertIn("class A", out)
        self.assertIn("def m", out)
        self.assertIn("def top", out)
        self.assertIn("строка 4", out)

    def test_outline_of_non_python_file_is_not_secret_refusal(self):
        out = workshop.code_outline("memory/llm.json")
        self.assertNotIn("секрет", out.lower())

    def test_outline_names_async_def_and_survives_broken_syntax(self):
        workshop.fs_write("workspace/b.py", "async def fetch(x):\n    pass\n\ndef broken(:\n")
        out = workshop.code_outline("workspace/b.py")
        self.assertIn("async def fetch", out)
        self.assertIn("def broken", out)

    # --- run: таймаут и потолок -------------------------------------------- #

    def test_run_captures_output(self):
        workshop.project_create("демо", "бриф")
        out = workshop.run(f'"{sys.executable}" -c "print(42)"', "демо")
        self.assertIn("42", out)

    def test_run_timeout_is_reported(self):
        workshop.project_create("демо", "бриф")
        out = workshop.run(f'"{sys.executable}" -c "import time; time.sleep(5)"', "демо", timeout=1)
        self.assertIn("та", out.lower())  # «таймаут» / «тайм-ауту»

    # --- чтение и поиск ---------------------------------------------------- #

    def test_credential_named_files_are_readable(self):
        out = workshop.fs_read(".env")
        self.assertIn("SECRET=x", out)
        out = workshop.fs_read("memory/llm.json")
        self.assertIn("secret", out)

    def test_search_literal_same_hits(self):
        workshop.fs_write("workspace/alpha.py", "needle_alpha = 1\n")
        workshop.fs_write("workspace/beta.py", "x = 0\nneedle_alpha = 2\n")
        out = workshop.fs_search("needle_alpha", glob="**/*.py").replace("\\", "/")
        self.assertIn("workspace/alpha.py:1", out)
        self.assertIn("workspace/beta.py:2", out)

    def test_search_includes_credential_named_files(self):
        out = workshop.fs_search("SECRET", glob="**/*").replace("\\", "/")
        self.assertIn(".env", out)

    # --- расписки и сторож границ ------------------------------------------ #

    def test_receipts_are_written(self):
        workshop.fs_write("workspace/a.py", "print(1)\n")
        rec = hands.receipts_path(self.tmp)
        self.assertTrue(rec.exists(), "каждая операция обязана оставить расписку")
        self.assertIn('"op":"write"', rec.read_text(encoding="utf-8"))

    def test_refusal_leaves_a_receipt_too(self):
        workshop.fs_edit("workspace/нет.py", "a", "b")
        row = hands.receipts_path(self.tmp).read_text(encoding="utf-8")
        self.assertIn('"op":"edit"', row)
        self.assertIn('"ok":false', row)

    def test_filenames_are_not_a_secret_boundary(self):
        for name in (".env", "praxis.session", "llm.json", "deploy.env"):
            self.assertIsNone(workshop._SECRET_NAME.search(name), name)

    def test_state_line_is_honest(self):
        self.assertIn("питон", hands.state_line())
        self.assertTrue(hands.available())


# --------------------------------------------------------------------------- #
#  2. Второго исполнителя нет
# --------------------------------------------------------------------------- #

class TestNoSecondExecutor(Home, unittest.TestCase):
    def test_configured_binary_is_never_started(self):
        marker = self.tmp / "binary-was-started"
        fake = self.tmp / "fake-hands"
        fake.write_text(f'#!/bin/sh\ntouch "{marker}"\necho \'{{"ok":true,"msg":"fake"}}\'\n', encoding="utf-8")
        fake.chmod(0o755)
        workshop.project_create("демо", "бриф")
        with mock.patch.dict(os.environ, {"PRAXIS_HANDS": str(fake)}):
            workshop.fs_write("workspace/a.py", "x = 1\n")
            workshop.fs_edit("workspace/a.py", "x = 1", "x = 2")
            workshop.fs_read("workspace/a.py")
            workshop.fs_search("x", glob="**/*.py")
            workshop.fs_search("x", glob="**/*.py", root="workspace")
            workshop.code_outline("workspace/a.py")
            workshop.run("echo hi", "демо")
        self.assertFalse(marker.exists(), "настроенный бинарь не должен запускаться ни одной операцией")
        self.assertEqual((self.tmp / "workspace" / "a.py").read_text(encoding="utf-8"), "x = 2\n")

    def test_image_builds_no_rust_floor(self):
        root = Path(__file__).resolve().parent
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        for word in ("praxis-hands", "PRAXIS_HANDS", "hands-builder", "cargo"):
            self.assertNotIn(word, dockerfile)
        self.assertFalse((root / "hands").exists(), "исходники Rust-пола не должны лежать в дереве")

    def test_run_executes_exactly_once(self):
        workshop.project_create("демо", "бриф")
        workshop.run("echo x >> count.txt", "демо")
        count = workshop.PROJECTS / workshop._slug("демо") / "count.txt"
        self.assertEqual(count.read_text(encoding="utf-8").count("x"), 1)


# --------------------------------------------------------------------------- #
#  3. Контракт правки
# --------------------------------------------------------------------------- #

class TestVersionedEdits(Home, unittest.TestCase):
    def test_read_then_edit_chain_of_versions(self):
        workshop.fs_write("workspace/a.py", "a = 1\nb = 2\n")
        digest = re.search(r"sha256=([0-9a-f]{64})", workshop.fs_read("workspace/a.py")).group(1)
        first = workshop.fs_edit("workspace/a.py", "a = 1", "a = 10", expected_sha256=digest)
        self.assertIn("confirmed sha256=", first)
        stale = workshop.fs_edit("workspace/a.py", "b = 2", "b = 20", expected_sha256=digest)
        self.assertIn("Конфликт версии", stale)
        self.assertIn("b = 2\n", (self.tmp / "workspace/a.py").read_text(encoding="utf-8"))
        fresh = re.search(r"confirmed sha256=([0-9a-f]{64})", first).group(1)
        self.assertIn("confirmed", workshop.fs_edit("workspace/a.py", "b = 2", "b = 20", expected_sha256=fresh))

    def test_confirmed_hash_is_the_hash_of_real_bytes(self):
        out = workshop.fs_write("workspace/a.py", "привет\n")
        reported = re.search(r"confirmed sha256=([0-9a-f]{64})", out).group(1)
        self.assertEqual(reported, hands._digest_of(self.tmp / "workspace/a.py"))

    def test_legacy_short_digest_is_still_honoured(self):
        workshop.fs_write("workspace/a.py", "a = 1\n")
        digest = hands._digest_of(self.tmp / "workspace/a.py")
        self.assertIn("confirmed", workshop.fs_edit("workspace/a.py", "a = 1", "a = 2", expected_sha256=digest[:16]))

    def test_malformed_expected_is_refused_without_touching(self):
        workshop.fs_write("workspace/a.py", "a = 1\n")
        out = workshop.fs_edit("workspace/a.py", "a = 1", "a = 2", expected_sha256="abc")
        self.assertIn("expected_sha256", out)
        self.assertEqual((self.tmp / "workspace/a.py").read_text(encoding="utf-8"), "a = 1\n")

    def test_write_with_expected_on_missing_file_is_a_conflict(self):
        out = workshop.fs_write("workspace/new.py", "x", expected_sha256="0" * 64)
        self.assertIn("Конфликт версии", out)
        self.assertFalse((self.tmp / "workspace/new.py").exists())

    def test_invalid_utf8_edit_keeps_every_byte(self):
        path = self.tmp / "workspace/latin.txt"
        original = b"caf\xe9 old value\n"
        path.write_bytes(original)
        out = workshop.fs_edit("workspace/latin.txt", "old", "new")
        self.assertIn("UTF-8", out)
        self.assertEqual(path.read_bytes(), original)

    def test_read_of_invalid_utf8_is_flagged_and_hash_covers_raw_bytes(self):
        path = self.tmp / "workspace/latin.txt"
        path.write_bytes(b"caf\xe9\n")
        out = workshop.fs_read("workspace/latin.txt")
        self.assertIn("не UTF-8", out)
        self.assertIn(hands._digest_of(path), out)

    def test_crlf_outside_the_edit_survives(self):
        path = self.tmp / "workspace/dos.txt"
        path.write_bytes(b"one\r\ntwo\r\nthree\r\n")
        self.assertIn("confirmed", workshop.fs_edit("workspace/dos.txt", "two", "2"))
        self.assertEqual(path.read_bytes(), b"one\r\n2\r\nthree\r\n")

    def test_lf_old_in_crlf_file_gets_a_hint(self):
        path = self.tmp / "workspace/dos.txt"
        path.write_bytes(b"one\r\ntwo\r\n")
        out = workshop.fs_edit("workspace/dos.txt", "one\ntwo", "x")
        self.assertIn("CRLF", out)
        self.assertEqual(path.read_bytes(), b"one\r\ntwo\r\n")

    def test_symlink_inside_home_is_written_through(self):
        real = self.tmp / "workspace/real.txt"
        real.write_text("a\n", encoding="utf-8")
        link = self.tmp / "workspace/link.txt"
        link.symlink_to(real)
        self.assertIn("confirmed", workshop.fs_edit("workspace/link.txt", "a", "b"))
        self.assertTrue(link.is_symlink())
        self.assertEqual(real.read_text(encoding="utf-8"), "b\n")

    def test_lost_lock_is_reported_as_unknown(self):
        workshop.fs_write("workspace/a.py", "a = 1\n")
        lost = contextlib.nullcontext(types.SimpleNamespace(lost_to_takeover=True))
        with mock.patch.object(workshop, "mutation_lock", lambda: lost):
            out = workshop.fs_edit("workspace/a.py", "a = 1", "a = 2")
        self.assertIn("outcome unknown", out)
        self.assertNotIn("confirmed", out)

    def test_lock_release_failure_after_the_write_is_not_a_refusal(self):
        with mock.patch.object(workshop, "mutation_lock", _ReleaseFails):
            out = workshop.fs_write("workspace/a.py", "x = 1\n")
        self.assertIn("Исход неизвестен", out)
        self.assertNotIn("confirmed", out)
        self.assertNotIn("not admitted", out)
        self.assertEqual((self.tmp / "workspace/a.py").read_bytes(), b"x = 1\n")

    def test_root_lock_release_failure_after_the_write_is_not_a_refusal(self):
        import forge_roots
        real = forge_roots.transaction

        @contextlib.contextmanager
        def failing(*args, **kwargs):
            with real(*args, **kwargs):
                yield
            raise OSError("release failed")

        with mock.patch.object(forge_roots, "transaction", failing):
            out = workshop.fs_write("workspace/a.py", "x = 1\n")
        self.assertIn("Исход неизвестен", out)
        self.assertEqual((self.tmp / "workspace/a.py").read_bytes(), b"x = 1\n")

    def test_release_failure_keeps_a_refusal_a_refusal(self):
        workshop.fs_write("workspace/a.py", "a = 1\n")
        with mock.patch.object(workshop, "mutation_lock", _ReleaseFails):
            out = workshop.fs_edit("workspace/a.py", "нет такого", "b")
        self.assertIn("Вхождение не найдено", out)
        self.assertEqual((self.tmp / "workspace/a.py").read_bytes(), b"a = 1\n")

    def test_lock_that_cannot_be_taken_is_a_refusal(self):
        def busy():
            raise TimeoutError("занято")

        with mock.patch.object(workshop, "mutation_lock", busy):
            out = workshop.fs_write("workspace/a.py", "x = 1\n")
        self.assertIn("not admitted", out)
        self.assertFalse((self.tmp / "workspace/a.py").exists())

    def test_writers_under_the_shared_lock_have_real_compare_and_swap(self):
        """Гарантия сравнения-и-замены — про них; окно для писателя вне замка — в TestPublish."""
        workshop.fs_write("workspace/a.py", "n = 0\n")
        digest = hands._digest_of(self.tmp / "workspace/a.py")
        barrier = threading.Barrier(6)
        answers: list[str] = []

        def edit(number: int) -> None:
            barrier.wait()
            answers.append(workshop.fs_edit("workspace/a.py", "n = 0", f"n = {number}", expected_sha256=digest))

        threads = [threading.Thread(target=edit, args=(i + 1,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum("confirmed" in answer for answer in answers), 1, answers)
        self.assertEqual(sum("Конфликт версии" in answer for answer in answers), 5, answers)

    def test_every_write_goes_through_the_shared_lock(self):
        entered = []

        @contextlib.contextmanager
        def counted():
            entered.append(1)
            yield types.SimpleNamespace(lost_to_takeover=False)

        with mock.patch.object(workshop, "mutation_lock", counted):
            workshop.fs_write("workspace/a.py", "a = 1\n")
            workshop.fs_edit("workspace/a.py", "a = 1", "a = 2")
        self.assertEqual(len(entered), 2)


class TestPublish(Home, unittest.TestCase):
    def target(self, content: bytes | None = b"old\n") -> tuple[Path, str | None]:
        path = self.tmp / "workspace" / "f.txt"
        if content is None:
            return path, None
        path.write_bytes(content)
        return path, hands._digest_of(path)

    def create_under_umask(self, mask: int, folder_mode: int = 0o755, gid: int | None = None) -> tuple[int, int, Path]:
        """umask — свойство всего процесса, поэтому меряем в отдельном.

        -> (режим файла из publish, режим обычно созданного рядом, каталог)."""
        folder = Path(tempfile.mkdtemp(dir=self.tmp / "workspace"))
        if gid is not None:
            with contextlib.suppress(PermissionError):
                os.chown(folder, -1, gid)
        folder.chmod(folder_mode)
        script = ("import os, sys, pathlib, hands\n"
                  "os.umask(int(sys.argv[2], 8))\n"
                  "folder = pathlib.Path(sys.argv[1])\n"
                  "result = hands.publish(folder / 'new.txt', b'x', None)\n"
                  "assert result['status'] == 'confirmed', result\n"
                  "(folder / 'plain.txt').open('x').close()\n")
        subprocess.run([sys.executable, "-c", script, str(folder), format(mask, "o")], check=True,
                       cwd=Path(hands.__file__).resolve().parent, timeout=30)
        modes = [(folder / name).stat().st_mode & 0o7777 for name in ("new.txt", "plain.txt")]
        return modes[0], modes[1], folder

    def test_confirmed_replaces_and_reads_back(self):
        path, before = self.target()
        result = hands.publish(path, b"new\n", before)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(path.read_bytes(), b"new\n")
        self.assertEqual(result["before_sha256"], before)
        self.assertEqual(result["after_sha256"], hands._digest_of(path))
        self.assertEqual(self.leftovers(), [])

    def test_changed_preimage_is_refused_and_keeps_the_other_writer(self):
        path, before = self.target()
        path.write_bytes(b"someone else\n")
        result = hands.publish(path, b"mine\n", before)
        self.assertEqual(result["status"], "refused")
        self.assertIn("conflict", result["message"])
        self.assertEqual(path.read_bytes(), b"someone else\n")
        self.assertEqual(self.leftovers(), [])

    def test_recheck_refusal_publishes_nothing(self):
        path, before = self.target()
        result = hands.publish(path, b"mine\n", before, recheck=lambda: "путь изменился")
        self.assertEqual(result["status"], "refused")
        self.assertEqual(path.read_bytes(), b"old\n")
        self.assertEqual(self.leftovers(), [])

    def test_create_does_not_clobber_a_file_that_appeared(self):
        path, _ = self.target(None)
        path.write_bytes(b"concurrent creator\n")
        result = hands.publish(path, b"mine\n", None)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(path.read_bytes(), b"concurrent creator\n")

    def test_create_race_after_the_recheck_is_refused_not_clobbered(self):
        path, _ = self.target(None)
        path.write_bytes(b"concurrent creator\n")
        with mock.patch.object(hands, "snapshot", return_value=(None, None)):
            result = hands.publish(path, b"mine\n", None)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(path.read_bytes(), b"concurrent creator\n")
        self.assertEqual(self.leftovers(), [])

    def test_parallel_creators_have_one_winner(self):
        path, _ = self.target(None)
        barrier = threading.Barrier(8)
        results: list[dict] = []

        def create(number: int) -> None:
            barrier.wait()
            results.append(hands.publish(path, f"{number}\n".encode(), None))

        threads = [threading.Thread(target=create, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        statuses = collections.Counter(r["status"] for r in results)
        self.assertEqual(statuses, {"confirmed": 1, "refused": 7})
        winner = next(r for r in results if r["status"] == "confirmed")
        self.assertEqual(winner["after_sha256"], hands._digest_of(path))
        self.assertEqual(self.leftovers(), [])

    def test_failed_readback_is_never_confirmed(self):
        path, before = self.target()
        with mock.patch.object(hands, "_digest_of", side_effect=OSError("boom")):
            result = hands.publish(path, b"new\n", before)
        self.assertEqual(result["status"], "in_doubt")
        self.assertIn("не повторяй", result["message"])

    def test_readback_mismatch_is_in_doubt(self):
        path, before = self.target()
        with mock.patch.object(hands, "_digest_of", return_value="0" * 64):
            result = hands.publish(path, b"new\n", before)
        self.assertEqual(result["status"], "in_doubt")

    def test_failure_of_publication_itself_is_in_doubt_and_leaves_no_temp(self):
        path, before = self.target()
        with mock.patch("hands.os.replace", side_effect=OSError("boom")):
            result = hands.publish(path, b"new\n", before)
        self.assertEqual(result["status"], "in_doubt")
        self.assertEqual(path.read_bytes(), b"old\n")
        self.assertEqual(self.leftovers(), [])

    def test_failure_before_publication_is_a_refusal(self):
        path, before = self.target()
        with mock.patch("hands.tempfile.mkstemp", side_effect=OSError("no space")):
            result = hands.publish(path, b"new\n", before)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(path.read_bytes(), b"old\n")

    def test_failure_before_creating_a_new_file_is_a_refusal(self):
        path, _ = self.target(None)
        with mock.patch("hands.os.open", side_effect=OSError("no space")):
            result = hands.publish(path, b"new\n", None)
        self.assertEqual(result["status"], "refused")
        self.assertFalse(path.exists())
        self.assertEqual(self.leftovers(), [])

    def test_writer_outside_the_lock_after_the_last_check_is_the_stated_limit(self):
        """Обычный rename этого окна не закрывает, поэтому обещание сужено до писателей под замком."""
        path, before = self.target()
        real = os.replace

        def shell_lands_in_the_window(src, dst):
            Path(dst).write_bytes(b"shell\n")
            real(src, dst)

        with mock.patch("hands.os.replace", shell_lands_in_the_window):
            result = hands.publish(path, b"mine\n", before)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(path.read_bytes(), b"mine\n")
        self.assertIn("между ней и os.replace", hands.publish.__doc__, "предел обязан быть назван")

    def test_creating_a_file_has_no_such_window(self):
        path, _ = self.target(None)
        real = os.link

        def shell_lands_in_the_window(src, dst):
            Path(dst).write_bytes(b"shell\n")
            real(src, dst)

        with mock.patch("hands.os.link", shell_lands_in_the_window):
            result = hands.publish(path, b"mine\n", None)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(path.read_bytes(), b"shell\n")
        self.assertEqual(self.leftovers(), [])

    def test_directory_in_place_of_file_is_refused(self):
        path = self.tmp / "workspace" / "dir"
        path.mkdir()
        result = hands.publish(path, b"new\n", "0" * 64)
        self.assertEqual(result["status"], "refused")
        self.assertTrue(path.is_dir())

    @unittest.skipUnless(os.name == "posix", "POSIX modes")
    def test_existing_mode_survives(self):
        path, before = self.target()
        path.chmod(0o640)
        self.assertEqual(hands.publish(path, b"new\n", before)["status"], "confirmed")
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    @unittest.skipUnless(os.name == "posix", "POSIX modes")
    def test_new_file_does_not_keep_private_tempfile_mode(self):
        self.assertEqual(self.create_under_umask(0o022)[0], 0o644)

    @unittest.skipUnless(os.name == "posix", "POSIX modes")
    def test_new_file_mode_is_what_ordinary_creation_gives_under_any_umask(self):
        for mask, expected in ((0o077, 0o600), (0o027, 0o640), (0o007, 0o660), (0o002, 0o664), (0o000, 0o666)):
            with self.subTest(umask=oct(mask)):
                published, ordinary, _ = self.create_under_umask(mask)
                self.assertEqual(published, expected)
                self.assertEqual(published, ordinary)

    @unittest.skipUnless(os.name == "posix", "POSIX modes")
    def test_open_directory_does_not_widen_a_new_file(self):
        self.assertEqual(self.create_under_umask(0o077, folder_mode=0o777)[0], 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX modes")
    def test_new_file_in_a_group_directory_takes_its_group_and_bits(self):
        gid = os.stat(self.tmp).st_gid + 1 if os.geteuid() == 0 else None
        published, ordinary, folder = self.create_under_umask(0o007, folder_mode=0o2770, gid=gid)
        self.assertEqual(published, 0o660)
        self.assertEqual(published, ordinary)
        self.assertEqual((folder / "new.txt").stat().st_gid, folder.stat().st_gid)

    def test_new_file_mode_does_not_touch_the_process_umask(self):
        path, _ = self.target(None)
        with mock.patch("hands.os.umask", side_effect=AssertionError("umask общий на весь процесс")):
            self.assertEqual(hands.publish(path, b"new\n", None)["status"], "confirmed")

    @unittest.skipUnless(os.name == "posix", "POSIX modes")
    def test_replacement_stays_private_until_it_gets_the_old_mode(self):
        path, before = self.target()
        path.chmod(0o600)
        seen: list[int] = []
        real = hands._copy_attributes

        def spy(temp, target):
            seen.append(os.stat(temp).st_mode & 0o777)
            real(temp, target)

        with mock.patch.object(hands, "_copy_attributes", spy):
            self.assertEqual(hands.publish(path, b"new\n", before)["status"], "confirmed")
        self.assertEqual(seen, [0o600])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_missing_parent_directories_are_created(self):
        path = self.tmp / "workspace" / "deep" / "er" / "f.txt"
        self.assertEqual(hands.publish(path, b"x", None)["status"], "confirmed")
        self.assertEqual(path.read_bytes(), b"x")


class TestForgeEditSharesTheCore(Home, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "workspace"
        self.patches = [mock.patch.object(forge, "_task_root", return_value=({"id": "t"}, self.root, "")),
                        mock.patch.object(forge, "_remote_client", return_value=None),
                        mock.patch.object(forge, "_event")]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        super().tearDown()

    def edit(self, action, **kw):
        return forge._edit_unlocked("t", action, **kw)

    def locked_edit(self, **kw):
        @contextlib.contextmanager
        def task_lock(_task_id):
            yield lambda *_: None

        with mock.patch.object(forge, "_mutation_lock", task_lock), \
             mock.patch.object(forge, "_admission_reason", return_value=""), \
             mock.patch.object(forge, "get", return_value={}):
            return forge.edit("t", "write", **kw)

    def test_lock_release_failure_after_the_edit_is_reported_as_unknown(self):
        path = self.root / "a.py"
        path.write_text("x\n", encoding="utf-8")
        with mock.patch.object(workshop, "mutation_lock", _ReleaseFails):
            out = self.locked_edit(path="a.py", content="y\n")
        self.assertIn("Исход правки неизвестен", out)
        self.assertEqual(path.read_text(encoding="utf-8"), "y\n")

    def test_busy_lock_before_the_edit_is_still_not_started(self):
        def busy():
            raise TimeoutError("занято")

        path = self.root / "a.py"
        path.write_text("x\n", encoding="utf-8")
        with mock.patch.object(workshop, "mutation_lock", busy):
            out = self.locked_edit(path="a.py", content="y\n")
        self.assertIn("Правка не началась", out)
        self.assertEqual(path.read_text(encoding="utf-8"), "x\n")

    def test_replace_reports_a_full_hash_of_real_bytes(self):
        path = self.root / "a.py"
        path.write_text("return 41\n", encoding="utf-8")
        out = self.edit("replace", path="a.py", old="41", new="42")
        self.assertIn(hands._digest_of(path), out)
        self.assertEqual(path.read_text(encoding="utf-8"), "return 42\n")

    def test_replace_of_invalid_utf8_keeps_every_byte(self):
        path = self.root / "latin.txt"
        original = b"caf\xe9 old\n"
        path.write_bytes(original)
        out = self.edit("replace", path="latin.txt", old="old", new="new")
        self.assertIn("UTF-8", out)
        self.assertEqual(path.read_bytes(), original)

    def test_replace_keeps_crlf(self):
        path = self.root / "dos.txt"
        path.write_bytes(b"a\r\nb\r\n")
        self.edit("replace", path="dos.txt", old="b", new="c")
        self.assertEqual(path.read_bytes(), b"a\r\nc\r\n")

    def test_stale_version_is_a_conflict(self):
        path = self.root / "a.py"
        path.write_text("x\n", encoding="utf-8")
        out = self.edit("write", path="a.py", content="y\n", expected_sha256="0" * 64)
        self.assertIn("Конфликт версии", out)
        self.assertEqual(path.read_text(encoding="utf-8"), "x\n")

    def test_lost_readback_is_not_reported_as_success(self):
        path = self.root / "a.py"
        path.write_text("x\n", encoding="utf-8")
        with mock.patch.object(hands, "_digest_of", side_effect=OSError("boom")):
            out = self.edit("write", path="a.py", content="y\n")
        self.assertIn("Исход неизвестен", out)
        forge._event.assert_not_called()

    def test_ambiguous_old_names_lines(self):
        (self.root / "a.py").write_text("x\nx\n", encoding="utf-8")
        out = self.edit("replace", path="a.py", old="x", new="y")
        self.assertIn("2 совпадений", out)
        self.assertIn("строка 2", out)

    def test_write_creates_new_file(self):
        out = self.edit("write", path="new/deep.txt", content="hello")
        self.assertTrue(out.startswith("write: "), out)
        self.assertEqual((self.root / "new/deep.txt").read_text(encoding="utf-8"), "hello")


# --------------------------------------------------------------------------- #
#  4. Запуск и поиск
# --------------------------------------------------------------------------- #

def process_gone(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return True
    return state in ("Z", "X")


@unittest.skipUnless(Path("/proc").is_dir(), "нужен Linux /proc")
class TestRun(Home, unittest.TestCase):
    def setUp(self):
        super().setUp()
        workshop.project_create("демо", "бриф")
        self.place = workshop.PROJECTS / workshop._slug("демо")

    def wait_for(self, predicate, seconds=5.0) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if predicate():
                return True
            time.sleep(0.05)
        return predicate()

    def test_timeout_actually_kills_in_time(self):
        started = time.monotonic()
        out = workshop.run(f'"{sys.executable}" -c "import time; time.sleep(20)"', "демо", timeout=2)
        self.assertLess(time.monotonic() - started, 15)
        self.assertIn("тайм-ауту", out)

    def test_timeout_kills_the_whole_group(self):
        out = workshop.run("sleep 60 & echo $! > pid.txt; wait", "демо", timeout=1)
        self.assertIn("тайм-ауту", out)
        pid = int((self.place / "pid.txt").read_text(encoding="utf-8"))
        self.assertTrue(self.wait_for(lambda: process_gone(pid)), "внук пережил тайм-аут")

    def test_background_process_does_not_hold_the_answer(self):
        started = time.monotonic()
        out = workshop.run("sleep 30 & echo $! > pid.txt; echo started", "демо", timeout=20)
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("started", out)
        pid = int((self.place / "pid.txt").read_text(encoding="utf-8"))
        self.addCleanup(lambda: os.kill(pid, 9) if not process_gone(pid) else None)
        self.assertFalse(process_gone(pid), "запущенный намеренно фоновый процесс должен жить дальше")

    def test_nonzero_exit_is_named(self):
        self.assertIn("[exit 3]", workshop.run("echo boom; exit 3", "демо"))

    def test_stderr_is_in_the_answer(self):
        self.assertIn("oops", workshop.run("echo oops 1>&2", "демо"))

    def test_output_is_capped_head_and_tail(self):
        cmd = f'"{sys.executable}" -c "print(\'A\' + \'x\' * 200000 + \'Z\')"'
        out = workshop.run(cmd, "демо")
        self.assertIn("вырезано", out)
        self.assertTrue(out.startswith("A"))
        self.assertTrue(out.rstrip().endswith("Z"))
        self.assertLess(len(out), workshop.RUN_OUT_CAP + 200)

    def test_cwd_outside_home_is_refused_without_running(self):
        marker = self.tmp.parent / f"{self.tmp.name}-escaped"
        result = hands.execute(f'touch "{marker}"', "/tmp", base=self.tmp)
        self.assertFalse(result["ok"])
        self.assertNotIn("out", result)
        self.assertFalse(marker.exists())

    def test_receipt_of_a_run(self):
        workshop.run("true", "демо")
        self.assertIn('"op":"exec"', hands.receipts_path(self.tmp).read_text(encoding="utf-8"))


class TestSearch(Home, unittest.TestCase):
    def test_runs_outside_this_process(self):
        (self.tmp / "workspace/a.txt").write_text("needle\n", encoding="utf-8")
        with mock.patch("hands.subprocess.run", wraps=subprocess.run) as run:
            answer = hands.search("needle", glob="**/*.txt", root="workspace", base=self.tmp)
        run.assert_called_once()
        self.assertEqual(Path(run.call_args.args[0][-1]).name, "hands_search.py")
        self.assertEqual(answer["hits"], ["a.txt:1: needle"])

    def test_child_has_no_access_to_app_modules(self):
        with mock.patch("hands.subprocess.run", wraps=subprocess.run) as run:
            hands.search("x", glob="*.txt", root="workspace", base=self.tmp)
        self.assertEqual(run.call_args.args[0][1:3], ["-I", "-S"])

    def test_hit_format_and_line_numbers_with_unicode(self):
        (self.tmp / "workspace/a.txt").write_text("раз\n  иголка тут  \nтри\n", encoding="utf-8")
        answer = hands.search("иголка", glob="**/*.txt", root="workspace", base=self.tmp)
        self.assertEqual(answer["hits"], ["a.txt:2: иголка тут"])

    def test_stray_byte_does_not_hide_a_file(self):
        (self.tmp / "workspace/a.txt").write_bytes(b"caf\xe9\nneedle here\n")
        answer = hands.search("needle", glob="**/*.txt", root="workspace", base=self.tmp)
        self.assertEqual(len(answer["hits"]), 1)

    def test_binary_is_skipped(self):
        (self.tmp / "workspace/a.bin").write_bytes(b"\0\0needle\n")
        answer = hands.search("needle", glob="**/*", root="workspace", base=self.tmp)
        self.assertEqual(answer["hits"], [])

    def test_budget_of_files_and_skipped_dirs(self):
        for i in range(5):
            (self.tmp / f"workspace/f{i}.txt").write_text("needle\n", encoding="utf-8")
        (self.tmp / "workspace/node_modules").mkdir()
        (self.tmp / "workspace/node_modules/x.txt").write_text("needle\n", encoding="utf-8")
        with mock.patch.object(hands, "SEARCH_FILE_BUDGET", 3):
            answer = hands.search("needle", glob="**/*.txt", root="workspace", base=self.tmp)
        self.assertEqual(answer["files_seen"], 3)
        self.assertNotIn("node_modules", "".join(answer["hits"]))

    def test_cap_of_hits(self):
        (self.tmp / "workspace/a.txt").write_text("needle\n" * 10, encoding="utf-8")
        answer = hands.search("needle", glob="**/*.txt", root="workspace", cap=4, base=self.tmp)
        self.assertEqual(len(answer["hits"]), 4)
        self.assertTrue(answer["capped"])

    def test_oversized_file_is_named_not_silently_missed(self):
        (self.tmp / "workspace/big.txt").write_text("needle\n" * 100, encoding="utf-8")
        with mock.patch.object(hands, "SEARCH_MAX_FILE_BYTES", 10):
            out = workshop.fs_search("needle", glob="**/*.txt", root="workspace")
        self.assertIn("пропустила файлов крупнее", out)

    def test_running_out_of_time_is_named(self):
        (self.tmp / "workspace/a.txt").write_text("needle\n", encoding="utf-8")
        with mock.patch.object(hands, "SEARCH_SECONDS", 1e-9):
            out = workshop.fs_search("needle", glob="**/*.txt", root="workspace")
        self.assertIn("не успела", out)

    def test_bad_root_is_a_refusal(self):
        self.assertFalse(hands.search("x", root="../..", base=self.tmp)["ok"])
        self.assertFalse(hands.search("x", root="workspace/нет", base=self.tmp)["ok"])
        self.assertFalse(hands.search("", root="workspace", base=self.tmp)["ok"])

    def test_silent_child_is_named_and_nothing_is_read_in_this_process(self):
        (self.tmp / "workspace/a.py").write_text("needle = 1\n", encoding="utf-8")
        with mock.patch("hands.subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)), \
             mock.patch.object(Path, "read_text", side_effect=AssertionError("основной процесс читает файлы поиска")):
            self.assertIsNone(hands.search("needle", glob="**/*.py", root="workspace", base=self.tmp))
            answers = [workshop.fs_search("needle", glob="**/*.py", root="workspace"),
                       workshop.fs_search("needle", glob="**/*.py"),
                       workshop.fs_search(r"need.e", glob="**/*.py", root="workspace")]
        for out in answers:
            self.assertIn("не ответил", out)
            self.assertNotIn("a.py:1", out)
            self.assertNotIn("Ничего не нашла", out, "молчание поиска — не отсутствие совпадений")

    def test_regex_runs_outside_this_process(self):
        (self.tmp / "workspace/a.txt").write_text("needle\n", encoding="utf-8")
        with mock.patch("hands.subprocess.run", wraps=subprocess.run) as run, \
             mock.patch.object(Path, "read_text", side_effect=AssertionError("основной процесс читает файлы поиска")):
            out = workshop.fs_search(r"need.e", glob="**/*.txt", root="workspace")
        self.assertEqual(Path(run.call_args.args[0][-1]).name, "hands_search.py")
        self.assertIn("a.txt:1: needle", out)

    def test_regex_that_never_finishes_is_killed_and_named(self):
        (self.tmp / "workspace/a.txt").write_text("a" * 40 + "!\n", encoding="utf-8")
        started = time.monotonic()
        with mock.patch.object(hands, "_SEARCH_PARENT_PAD", 0), \
             mock.patch.object(workshop, "SEARCH_SECONDS", 0.5):
            out = workshop.fs_search(r"(a+)+$", glob="**/*.txt", root="workspace")
        self.assertIn("не ответил", out)
        self.assertLess(time.monotonic() - started, 20)

    def test_regex_path_names_an_oversized_file(self):
        (self.tmp / "workspace/big.txt").write_text("needle\n" * 100, encoding="utf-8")
        with mock.patch.object(hands, "SEARCH_MAX_FILE_BYTES", 10):
            out = workshop.fs_search(r"need.e", glob="**/*.txt", root="workspace")
        self.assertIn("пропустила файлов крупнее", out)
        self.assertNotIn("big.txt:1", out)

    def test_regex_path_refuses_a_bad_mask(self):
        out = workshop.fs_search(r"need.e", glob="/etc/*", root="workspace")
        self.assertIn("плохая маска", out)

    def test_zero_seconds_means_the_parent_does_not_set_a_deadline_either(self):
        with mock.patch("hands.subprocess.run", wraps=subprocess.run) as run, \
             mock.patch.object(hands, "SEARCH_SECONDS", 0):
            hands.search("x", glob="*.txt", root="workspace", base=self.tmp)
        self.assertIsNone(run.call_args.kwargs["timeout"])

    def test_symlinked_directory_is_not_descended(self):
        outside = Path(tempfile.mkdtemp(prefix="praxis_hands_out_"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (outside / "a.txt").write_text("needle\n", encoding="utf-8")
        (self.tmp / "workspace/link").symlink_to(outside, target_is_directory=True)
        answer = hands.search("needle", glob="**/*.txt", root="workspace", base=self.tmp)
        self.assertEqual(answer["hits"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
