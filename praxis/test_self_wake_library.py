"""Self sources are full, fresh per activation, in every audience."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import agent
import frame_shadow
import self_wake_library as library


class SelfWakeSources(unittest.TestCase):
    def test_full_sources_and_next_turn_edit_for_each_audience(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            skills = base / 'soul' / 'skills'
            skills.mkdir(parents=True)
            code_map = base / 'CODEMAP.md'
            code_map.write_text('initial map', encoding='utf8')
            body = 'long skill ' * 10000 + 'END'
            (skills / 'uncatalogued.md').write_text(body, encoding='utf8')
            (skills / 'INDEX.md').write_text('INDEX ONLY', encoding='utf8')
            folder = skills / 'uncatalogued'
            folder.mkdir()
            (folder / 'resident.md').write_text('RESIDENT ONLY', encoding='utf8')
            (folder / 'practice.md').write_text('EXAMPLE ONLY', encoding='utf8')
            with (patch.object(agent, 'BASE', base), patch.object(agent, 'SOUL_DIR', skills.parent),
                  patch.object(agent, 'SKILLS_DIR', skills), patch.object(frame_shadow, 'BASE', base)):
                contexts = (
                    agent.ChannelContext(chat_id=None, principal_id=agent.PRAXIS_SELF_PRINCIPAL),
                    agent.ChannelContext(chat_id=41, owner=True, is_dm=True),
                    agent.ChannelContext(chat_id=42, owner=False, is_dm=True),
                    agent.ChannelContext(chat_id=-43, owner=False, is_dm=False),
                )
                for ctx in contexts:
                    with self.subTest(chat=ctx.chat_id):
                        first, _ = agent.build_system_parts(ctx=ctx)
                        self.assertEqual(first.count(body), 1)
                        self.assertIn('initial map', first)
                self.assertIn(body, frame_shadow._zone_k())
                self.assertNotIn('INDEX ONLY', first)
                self.assertNotIn('EXAMPLE ONLY', first)
                self.assertEqual(first.count('RESIDENT ONLY'), 1)
                code_map.write_text('agent edit between wakes', encoding='utf8')
                (skills / 'uncatalogued.md').write_text('revised skill', encoding='utf8')
                second, _ = agent.build_system_parts(ctx=contexts[-1])
                for frame in (second, frame_shadow._zone_k()):
                    self.assertIn('agent edit between wakes', frame)
                    self.assertIn('revised skill', frame)
                    self.assertNotIn('initial map', frame)
                    self.assertNotIn(body, frame)

    def test_map_edit_with_normal_hand(self):
        import workshop
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / 'CODEMAP.md'
            path.write_text('first map', encoding='utf8')
            with patch.object(workshop, 'BASE', base):
                import forge_roots
                sibling = base / 'workspace' / 'active-project'
                def occupied(_forge, _task, *, root):
                    return 'occupied' if forge_roots.overlaps(root, sibling) else ''
                with patch.object(forge_roots, 'foreign_writer_reason', side_effect=occupied):
                    outcome = workshop.fs_edit_result('CODEMAP.md', 'first map', 'second map')
                self.assertTrue(forge_roots.overlaps(workshop._edit_root(path), base))
                self.assertEqual(outcome['status'], 'confirmed', outcome)
                self.assertIn('second map', library.render(base, base/'soul'/'skills'))
                for forbidden in ('agent.py', 'CODEMAP.md.py', 'core/CODEMAP.md'):
                    self.assertIsNone(workshop._resolve_write(forbidden)[0])

    def test_symlinks(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            skills = base / 'skills'
            skills.mkdir()
            secret = base / 'private'
            secret.write_text('never include secret content', encoding='utf8')
            (skills / 'redirect.md').symlink_to(secret)
            (base / 'CODEMAP.md').symlink_to(secret)
            result = library.render(base, skills)
            self.assertNotIn(secret.read_text(), result)
            self.assertIn('unavailable', result)
