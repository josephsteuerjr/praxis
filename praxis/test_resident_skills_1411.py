"""Постоянные навыки (14.11): постоянная часть навыка стоит в кадре, материалы — по потребности.

Что закреплено:
- нет постоянных частей — персона байт-в-байт прежняя, секция объявлена отсутствующей;
- есть — текст стоит между VOICE и CURRENT, ровно один раз, с адресом источника;
- порядок детерминирован, пустой файл не считается, ссылка не читается;
- потолок не режет текст посередине, а называет непоместившееся;
- тень видит то же, что живой кадр; capabilities называют механизм, навык и материалы,
  а снимок замечает новую постоянную часть без рестарта;
- поставленный навык living-prose (если он лежит в её soul) влезает в потолок и не
  ссылается мимо своих файлов.

Запуск:  python praxis_test.py test_resident_skills_1411 -v
"""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent
import capabilities
import frame_shadow
import frame_trace
import llm
import resident_skills
import self_model

REPO = Path(__file__).resolve().parent

CURRENT = (
    "# Кто я сейчас\n\nRESIDENT_CURRENT_SENTINEL. Я меняю представление о себе только "
    "из проверяемого опыта и сохраняю прежнюю версию как evidence.\n\n"
    "## Как я действую\n\nДовожу действие до наблюдаемого результата и называю остаток.\n"
)


class _NoCallClient:
    def __init__(self):
        self.messages = self

    def create(self, **kw):
        raise AssertionError("постоянные навыки не зовут модель")


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.soul = self.base / "soul"
        self.skills = self.soul / "skills"
        self.skills.mkdir(parents=True)
        (self.soul / "SOUL.md").write_text("# Soul\n\ntruth\n", encoding="utf-8")
        (self.soul / "VOICE.md").write_text("# Voice\n\nVOICE_SENTINEL\n", encoding="utf-8")
        (self.soul / "self.md").write_text("# Legacy self\n\n" + "old evidence " * 30 + "\n",
                                           encoding="utf-8")
        self_model.SelfModel(self.base).migrate(reason="test", compact_text=CURRENT)
        env = {k: v for k, v in os.environ.items() if k != resident_skills.ENV_MAX}
        self.patches = [
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(agent, "BASE", self.base),
            mock.patch.object(agent, "SOUL_DIR", self.soul),
            mock.patch.object(agent, "SKILLS_DIR", self.skills),
            mock.patch.object(agent, "MEM_DIR", self.base / "memory"),
            mock.patch.object(frame_shadow, "BASE", self.base),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tmp.cleanup()

    def skill(self, slug: str, resident: str | None = None, *, materials=(), skill_file=True):
        if skill_file:
            (self.skills / f"{slug}.md").write_text(f"# {slug}\n", encoding="utf-8")
        folder = self.skills / slug
        if resident is not None or materials:
            folder.mkdir(exist_ok=True)
        if resident is not None:
            (folder / "resident.md").write_text(resident, encoding="utf-8")
        for name in materials:
            (folder / name).write_text("материал\n", encoding="utf-8")
        return folder

    def persona_rows(self):
        with frame_trace.capture():
            persona = agent._persona_text()
            rows = [r for r in frame_trace.sections() if r["name"].startswith("persona.")]
        return persona, rows


class PersonaPlace(Home):
    def test_no_resident_part_keeps_persona_bytes_and_declares_absence(self):
        before, rows = self.persona_rows()
        names = [r["name"] for r in rows]
        self.assertEqual(names.count("persona.resident_skills"), 1)
        row = next(r for r in rows if r["name"] == "persona.resident_skills")
        self.assertFalse(row["included"])
        self.assertEqual(row["reason"], "empty")
        # Папка навыка без resident.md (её конвенция, как praxis-translation-check/) —
        # тоже не постоянная часть.
        self.skill("translation", materials=("check.py",))
        after, _ = self.persona_rows()
        self.assertEqual(after, before)
        current = self_model.current_prompt(self.base)
        self.assertIn("RESIDENT_CURRENT_SENTINEL", current)
        expected = ("# Soul\n\ntruth\n" + "\n\n---\n# Voice\n\nVOICE_SENTINEL\n"
                    + "\n\n---\n" + current)
        self.assertEqual(before, expected)

    def test_resident_part_stands_between_voice_and_current_once(self):
        self.skill("living-prose", "## Живая речь\n\nRESIDENT_SENTINEL\n\n",
                   materials=("workshop.md",))
        persona, rows = self.persona_rows()
        self.assertEqual(persona.count("RESIDENT_SENTINEL"), 1)
        self.assertLess(persona.index("VOICE_SENTINEL"), persona.index("RESIDENT_SENTINEL"))
        self.assertLess(persona.index("RESIDENT_SENTINEL"),
                        persona.index("RESIDENT_CURRENT_SENTINEL"))
        self.assertIn("\n\n---\n## Живая речь\n\nRESIDENT_SENTINEL\n"
                      "[из: soul/skills/living-prose/resident.md · навык целиком и материалы: "
                      "soul/skills/living-prose.md, по потребности]\n\n---\n", persona)
        names = [r["name"] for r in rows]
        self.assertEqual(names, ["persona.soul", "persona.voice", "persona.resident_skills",
                                 "persona.self_current"])
        row = next(r for r in rows if r["name"] == "persona.resident_skills")
        self.assertTrue(row["included"])

    def test_roster_names_the_section(self):
        self.assertIn("persona.resident_skills", frame_trace.SYSTEM_ROSTER)
        roster = list(frame_trace.SYSTEM_ROSTER)
        self.assertLess(roster.index("persona.voice"), roster.index("persona.resident_skills"))
        self.assertLess(roster.index("persona.resident_skills"),
                        roster.index("persona.self_current"))

    def test_shadow_constitution_sees_the_same_part(self):
        k0 = frame_shadow._zone_k()
        self.assertEqual(k0, "# Soul\n\ntruth\n\n\n---\n# Voice\n\nVOICE_SENTINEL\n")
        self.skill("living-prose", "RESIDENT_SENTINEL")
        k1 = frame_shadow._zone_k()
        self.assertTrue(k1.startswith(k0 + "\n\n---\nRESIDENT_SENTINEL\n[из: "))
        coverage = frame_shadow._coverage([{"name": "persona.resident_skills",
                                            "included": True, "chars": 10}])
        self.assertEqual((coverage["covered"], coverage["todo"], coverage["unknown"]), (1, 0, 0))


class Rendering(Home):
    def test_order_empty_and_missing_skill_file(self):
        self.skill("б-второй", "ВТОРОЙ")
        self.skill("A-first", "ПЕРВЫЙ")
        self.skill("empty", "  \n\n")
        self.skill("lonely", "ОДИНОКИЙ", skill_file=False)
        text, rows = resident_skills.render(self.skills)
        self.assertEqual([r["slug"] for r in rows], ["A-first", "lonely", "б-второй"])
        self.assertLess(text.index("ПЕРВЫЙ"), text.index("ОДИНОКИЙ"))
        self.assertLess(text.index("ОДИНОКИЙ"), text.index("ВТОРОЙ"))
        self.assertIn("[из: soul/skills/lonely/resident.md · файла навыка "
                      "soul/skills/lonely.md нет]", text)
        self.assertNotIn("empty", text)
        self.assertEqual(text, resident_skills.render(self.skills)[0], "детерминизм")

    def test_cap_names_what_did_not_fit_and_never_cuts_text(self):
        self.skill("a", "А" * 50)
        self.skill("b", "Б" * 500)
        self.skill("c", "В" * 50)
        text, rows = resident_skills.render(self.skills, limit=300)
        self.assertIn("А" * 50, text)
        self.assertIn("В" * 50, text)
        self.assertNotIn("Б", text.replace("«b»", ""))
        self.assertIn("⚠ Постоянная часть навыка «b» (500 знаков) не поместилась", text)
        self.assertIn("soul/skills/b/resident.md", text)
        self.assertEqual([r["in_frame"] for r in rows], [True, False, True])
        unlimited, _ = resident_skills.render(self.skills, limit=0)
        self.assertIn("Б" * 500, unlimited)

    def test_env_cap(self):
        self.assertEqual(resident_skills.max_chars(), resident_skills.DEFAULT_MAX_CHARS)
        for raw, expected in (("0", 0), ("1200", 1200), ("мусор", 6000), ("-5", 6000)):
            with mock.patch.dict(os.environ, {resident_skills.ENV_MAX: raw}):
                self.assertEqual(resident_skills.max_chars(), expected, raw)

    @unittest.skipIf(os.name == "nt", "symlink fixture is POSIX")
    def test_symlink_is_refused_not_read(self):
        outside = self.base / "outside.md"
        outside.write_text("ЧУЖОЕ", encoding="utf-8")
        (self.skills / "linked").mkdir()
        (self.skills / "linked" / "resident.md").symlink_to(outside)
        text, rows = resident_skills.render(self.skills)
        self.assertNotIn("ЧУЖОЕ", text)
        self.assertEqual(rows[0]["refused"], "symlink")
        self.assertIn("не прочитана (symlink)", text)

    def test_fingerprint_moves_on_add_edit_remove(self):
        f0 = resident_skills.fingerprint(self.skills)
        folder = self.skill("x", "один")
        f1 = resident_skills.fingerprint(self.skills)
        self.assertNotEqual(f0, f1)
        (folder / "resident.md").write_text("один и ещё два", encoding="utf-8")
        f2 = resident_skills.fingerprint(self.skills)
        self.assertNotEqual(f1, f2)
        (folder / "resident.md").unlink()
        self.assertEqual(resident_skills.fingerprint(self.skills), f0)


class Capabilities(Home):
    def setUp(self):
        super().setUp()
        (self.skills / "INDEX.md").write_text(
            "| Skill | About |\n|---|---|\n"
            "| [living-prose](living-prose.md) | живая русская речь |\n"
            "| [email](email.md) | мой ящик |\n", encoding="utf-8")
        self.skill("email")
        self.saved = [(capabilities, "BASE", capabilities.BASE),
                      (capabilities, "SKILLS_INDEX", capabilities.SKILLS_INDEX)]
        capabilities.BASE = self.base
        capabilities.SKILLS_INDEX = self.skills / "INDEX.md"
        self.cache = dict(capabilities._CACHE)
        capabilities._CACHE.update(fp=None, snap=None)
        llm.use_test_client(_NoCallClient())
        self.addCleanup(llm.clear_test_clients)

    def tearDown(self):
        for module, key, value in self.saved:
            setattr(module, key, value)
        capabilities._CACHE.clear()
        capabilities._CACHE.update(self.cache)
        super().tearDown()

    def test_plain_skills_keep_their_old_rows(self):
        self.skill("living-prose")
        rows = {r["name"]: r for r in capabilities._skills_list()}
        self.assertEqual(rows["email"], {"name": "email", "line": "мой ящик"})
        self.assertEqual(rows["living-prose"], {"name": "living-prose",
                                                "line": "живая русская речь"})

    def test_resident_skill_row_names_frame_and_materials(self):
        self.skill("living-prose", "ОПОРА", materials=("workshop.md", "sources.md"))
        rows = {r["name"]: r for r in capabilities._skills_list()}
        row = rows["living-prose"]
        self.assertEqual(row["line"], "живая русская речь")
        self.assertEqual(row["resident"]["path"], "soul/skills/living-prose/resident.md")
        self.assertTrue(row["resident"]["in_frame"])
        self.assertEqual(row["materials"], ["soul/skills/living-prose/sources.md",
                                            "soul/skills/living-prose/workshop.md"])
        self.assertNotIn("resident", rows["email"])

    def test_owner_describe_names_mechanism_skill_and_materials(self):
        empty = capabilities.describe("owner")
        self.assertIn("постоянные навыки: нет (постоянная часть навыка — "
                      "soul/skills/<slug>/resident.md", empty)
        self.skill("living-prose", "ОПОРА", materials=("workshop.md",))
        text = capabilities.describe("owner")
        self.assertIn("постоянные навыки: living-prose (в кадре, 5 зн.) — живая русская речь; "
                      "материалы по потребности: soul/skills/living-prose/workshop.md", text)

    def test_audience_describes_do_not_list_skills(self):
        self.skill("living-prose", "ОПОРА", materials=("workshop.md",))
        for scope in ("group", "known"):
            self.assertNotIn("постоянные навыки", capabilities.describe(scope), scope)

    def test_snapshot_notices_new_resident_part_without_restart(self):
        self.skill("living-prose")
        first = capabilities.snapshot()
        self.assertNotIn("resident", {r["name"]: r for r in first["skills"]}["living-prose"])
        self.skill("living-prose", "ОПОРА")
        second = capabilities.snapshot()
        self.assertIn("resident", {r["name"]: r for r in second["skills"]}["living-prose"])


SHIPPED = REPO / "soul" / "skills" / "living-prose"


@unittest.skipUnless((SHIPPED / "resident.md").is_file(), "living-prose не поставлен в этот soul")
class ShippedLivingProse(unittest.TestCase):
    LINK = re.compile(r"\]\(([^)#]+)\)")

    def test_fits_default_cap_with_room_for_her_own(self):
        text, rows = resident_skills.render(REPO / "soul" / "skills",
                                            limit=resident_skills.DEFAULT_MAX_CHARS)
        row = next(r for r in rows if r["slug"] == "living-prose")
        self.assertTrue(row["in_frame"])
        self.assertTrue(row["skill_file"])
        self.assertLess(row["chars"], resident_skills.DEFAULT_MAX_CHARS // 2)

    def test_links_resolve_inside_the_skill(self):
        files = [REPO / "soul" / "skills" / "living-prose.md", *sorted(SHIPPED.glob("*.md"))]
        for path in files:
            for target in self.LINK.findall(path.read_text(encoding="utf-8")):
                if target.startswith(("http://", "https://")):
                    continue
                resolved = (path.parent / target).resolve()
                self.assertTrue(resolved.is_file(), f"{path.name}: {target}")
                self.assertTrue(resolved.is_relative_to((REPO / "soul" / "skills").resolve()))


if __name__ == "__main__":
    unittest.main()
