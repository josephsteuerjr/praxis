# -*- coding: utf-8 -*-
"""Starter skills of 15 October: real kit seeding and INDEX discovery, no live tree.

Run: python tests/t_base_skills_1510.py (from desk/).
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "localharness"))
import boot  # noqa: E402

SKILLS = ("secrets-hygiene", "infra-by-owner-word", "windows-paths", "verify-and-report")
STOCK = DESK / "resources" / "soul" / "skills"


class IncidentStarterSkills(unittest.TestCase):
    def test_fresh_seed_places_real_files_and_index_describes_each(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            boot._seed_kit(tree, {})
            seeded = tree / "soul" / "skills"
            index = (seeded / "INDEX.md").read_text(encoding="utf-8")
            for name in SKILLS:
                filename = f"{name}.md"
                self.assertEqual((seeded / filename).read_bytes(), (STOCK / filename).read_bytes())
                # INDEX is the actual file read by runner's capability snapshot; the
                # stock table must map each seed exactly once to a one-line purpose.
                rows = re.findall(r"^\| \[" + re.escape(name)
                                  + r"\]\(" + re.escape(filename)
                                  + r"\) \| ([^|\n]+) \|$", index, re.MULTILINE)
                self.assertEqual(len(rows), 1, filename)
                self.assertTrue(rows[0].strip())
            self.assertIn("## Написаны мной", index)

    def test_existing_authored_skills_and_index_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            seeded = tree / "soul" / "skills"
            seeded.mkdir(parents=True)
            (seeded / "INDEX.md").write_text("# Мой переписанный указатель\n", encoding="utf-8")
            (seeded / "secrets-hygiene.md").write_text("# Моё решение\n", encoding="utf-8")
            boot._seed_kit(tree, {})
            self.assertEqual((seeded / "INDEX.md").read_text(encoding="utf-8"),
                             "# Мой переписанный указатель\n")
            self.assertEqual((seeded / "secrets-hygiene.md").read_text(encoding="utf-8"),
                             "# Моё решение\n")
            for name in SKILLS[1:]:
                self.assertTrue((seeded / f"{name}.md").is_file(), name)
            self.assertNotIn("soul/skills/INDEX.md", boot.refresh_kit(tree, {}))
            self.assertEqual((seeded / "INDEX.md").read_text(encoding="utf-8"),
                             "# Мой переписанный указатель\n")

    def test_previous_stock_index_refreshes_but_never_replaces_authored_text(self):
        # The release integration records the pre-edit stock INDEX as an exact
        # predecessor in the kit history. No Git subprocess or live tree needed.
        history = boot._kit_history().get("soul/skills/INDEX.md", [])
        self.assertTrue(history, "pre-edit stock INDEX must enter kit history")
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            index = tree / "soul" / "skills" / "INDEX.md"
            index.parent.mkdir(parents=True)
            index.write_text(history[-1], encoding="utf-8", newline="\n")
            boot._seed_kit(tree, {})
            self.assertEqual(index.read_text(encoding="utf-8"), history[-1])
            self.assertIn("soul/skills/INDEX.md", boot.refresh_kit(tree, {}))
            self.assertEqual(index.read_text(encoding="utf-8"),
                             (STOCK / "INDEX.md").read_text(encoding="utf-8"))
            self.assertEqual(boot.refresh_kit(tree, {}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
