"""Capabilities use the agent locale without altering authored metadata or tools."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import capabilities
import frame_language


class CapabilityLanguage(unittest.TestCase):
    def test_all_ui_frame_pairs_preserve_authored_metadata_and_tool_authority(self):
        with tempfile.TemporaryDirectory() as raw, ExitStack() as stack:
            base = Path(raw)
            skills = base/'soul/skills'
            skills.mkdir(parents=True)
            authored = 'Личное описание Français 中文 {unchanged}'
            (skills/'private.md').write_text('# My skill', encoding='utf-8')
            (skills/'INDEX.md').write_text(
                '| [PRIVATE](private.md) | '+authored+' |\n', encoding='utf-8')
            config = base/'config.json'
            stack.enter_context(patch.dict(os.environ, {
                'HELENE_CONFIG': str(config), 'HELENE_SKILLS_KIT_ROOT': ''}))
            stack.enter_context(patch.object(capabilities, 'SKILLS_INDEX', skills/'INDEX.md'))
            stack.enter_context(patch.object(capabilities, '_CACHE', {'fp': None, 'snap': None}))
            # No model calls or backend credentials are needed to render this contract.
            stack.enter_context(patch.object(capabilities.llm, 'snapshot', return_value={}))
            stack.enter_context(patch.object(capabilities.llm, 'usage_line', return_value=''))
            stack.enter_context(patch.object(capabilities.forge, 'state_line', return_value=''))
            stack.enter_context(patch.object(capabilities.rails, 'state_line', return_value=''))
            headings = {
                'ru': 'Мои возможности сейчас', 'en': 'My current capabilities',
                'fr': 'Mes capacités actuelles', 'zh': '我当前的能力',
            }
            snapshots, rendered = {}, {}
            for ui in frame_language.LANGUAGES:
                for frame in frame_language.LANGUAGES:
                    config.write_text(json.dumps({'lang': ui, 'agent': {
                        'frame_language': frame, 'living_prose': True}}), encoding='utf-8')
                    snapshot = capabilities.snapshot()
                    text = capabilities.describe('owner')
                    self.assertTrue(text.startswith(headings[frame]), (ui, frame, text[:80]))
                    self.assertNotIn('Егор', text)
                    row = next(row for row in snapshot['skills'] if row['name'] == 'private')
                    self.assertEqual(row['line'], authored)
                    self.assertIn(authored, capabilities.skill_navigation(skills/'INDEX.md'))
                    if frame in rendered:
                        self.assertEqual(text, rendered[frame], 'UI locale changed model text')
                    rendered[frame] = text
                    snapshots[frame] = snapshot
                    group = capabilities.describe('group', offered=['fs_read', 'PRIVATE_TOOL'])
                    self.assertIn('fs_read, PRIVATE_TOOL', group)
                    self.assertNotIn(headings[frame], group)
            self.assertEqual(len(set(rendered.values())), 4)
            reference = snapshots['ru']['tools']
            for snapshot in snapshots.values():
                self.assertEqual(snapshot['tools'], reference)
            self.assertNotIn('на ГОЛОСЕ к non-owner проверяется', rendered['ru'])
            self.assertIn('судьи приватности нет', rendered['ru'])


if __name__ == '__main__':
    unittest.main()
