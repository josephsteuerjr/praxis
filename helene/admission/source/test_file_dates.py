"""Файл, принесённый минуту назад, обязан выглядеть иначе, чем файл из июля.

ДОСЛОВНО, ЛИЧКА 13.08.2026. Егор прислал архив, минутой позже спросил про него, она не
связала `wedding-auth-sync.tgz` со словом «wedding». Его объяснение: «у тебя не
отображается время создания и изменения файла по ходу, поэтому так». Так и было: в
листинге стояли только имя и размер, и «самое свежее» приходилось угадывать по названию.

⚠ ЧАСЫ ЕЁ, А НЕ КОНТЕЙНЕРА. `fromtimestamp` без пояса берёт UTC, и «сегодня 16:13»
напечаталось бы как «12:13» — час, которого у неё не было. Это тот же разрыв, что
[[praxis-utc-day-vs-samara]], только в другом приборе.
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PRAXIS_TEST", "1")

import praxis_time  # noqa: E402
import workshop  # noqa: E402


class TheStampIsInHerOwnHours(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="praxis-dates-"))
        self.addCleanup(shutil.rmtree, self.home, True)

    def _file(self, name: str, when: _dt.datetime) -> Path:
        path = self.home / name
        path.write_text("x", encoding="utf-8")
        os.utime(path, (when.timestamp(), when.timestamp()))
        return path

    def test_a_file_from_this_conversation_says_today(self):
        now = praxis_time.now()
        path = self._file("wedding-auth-sync.tgz", now)
        self.assertTrue(workshop.when(path).startswith("сегодня"))

    def test_yesterday_is_a_word_not_a_date(self):
        path = self._file("вчерашний.md", praxis_time.now() - _dt.timedelta(days=1))
        self.assertTrue(workshop.when(path).startswith("вчера"))

    def test_older_files_carry_a_date(self):
        path = self._file("июльский.md", praxis_time.now() - _dt.timedelta(days=30))
        stamp = workshop.when(path)
        self.assertNotIn("сегодня", stamp)
        self.assertNotIn("вчера", stamp)
        self.assertRegex(stamp, r"^\d{2}\.\d{2} \d{2}:\d{2}$")

    def test_the_hour_is_hers_and_not_the_container_utc(self):
        """Пояс контейнера — UTC; её — Europe/Samara. Печатается её."""
        moment = praxis_time.now().replace(hour=16, minute=13, second=0, microsecond=0)
        path = self._file("скрин.png", moment)
        self.assertIn("16:13", workshop.when(path))

    def test_an_unreadable_file_gives_no_stamp_instead_of_a_guess(self):
        missing = self.home / "нет-такого"
        self.assertEqual(workshop.when(missing), "")


class TheListingsCarryIt(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="praxis-dates-listing-"))
        self.addCleanup(shutil.rmtree, self.home, True)

    def test_fs_ls_prints_size_and_when(self):
        (self.home / "свежий.txt").write_text("hello", encoding="utf-8")
        with patch.object(workshop, "_resolve_read", return_value=self.home):
            listing = workshop.fs_ls("workspace")
        self.assertIn("свежий.txt", listing)
        self.assertIn("Б · ", listing)
        self.assertIn("сегодня", listing)

    def test_inbox_list_prints_it_too(self):
        """Именно inbox — туда падают файлы, которые ей только что прислали."""
        inbox = self.home / "workspace" / "inbox" / "private" / "Yegor"
        inbox.mkdir(parents=True)
        (inbox / "20260813_screen.png").write_bytes(b"\x89PNG" + b"0" * 16)
        with (
            patch.object(workshop, "BASE", self.home),
            patch.object(workshop, "INBOX", self.home / "workspace" / "inbox"),
            patch.object(workshop, "_resolve_inbox", return_value=inbox),
        ):
            listing = workshop.inbox_list("private/Yegor")
        self.assertIn("20260813_screen.png", listing)
        self.assertIn("сегодня", listing)

    def test_a_directory_is_stamped_but_has_no_size(self):
        (self.home / "проект").mkdir()
        with patch.object(workshop, "_resolve_read", return_value=self.home):
            listing = workshop.fs_ls("workspace")
        row = next(line for line in listing.splitlines() if line.startswith("проект"))
        self.assertNotIn("Б", row)
        self.assertIn("сегодня", row)


class TheToolSaysSoInItsOwnDescription(unittest.TestCase):
    """Прибор, который умеет, но об этом не сказано, для неё не существует."""

    def test_fs_ls_description_mentions_when_it_changed(self):
        import agent
        spec = next(t for t in agent.WORKSHOP_TOOLS if t["name"] == "fs_ls")
        self.assertIn("WHEN", spec["description"])

    def test_inbox_list_description_mentions_it(self):
        import agent
        self.assertIn("сегодня", agent.INBOX_LIST_TOOL["description"])


if __name__ == "__main__":
    unittest.main()
