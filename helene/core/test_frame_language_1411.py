"""Run-owned language selection must not translate authored content or leak agents."""
from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from string import Formatter

import frame_language
import resident_skills


class FrameLanguages(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.config=self.root/'helene.json'
        self.skills=self.root/'data/soul/skills';self.skills.mkdir(parents=True)
        self.env=patch.dict(os.environ,{'HELENE_CONFIG':str(self.config),'HELENE_SKILLS_KIT_ROOT':''})
        self.env.start();self.addCleanup(self.env.stop)
        self.write_config('ru')

    def write_config(self, lang, enabled=True):
        self.config.write_text(json.dumps({'lang':'zh','agent':{'frame_language':lang,'living_prose':enabled}}),encoding='utf-8')

    def add_skill(self, name, text):
        folder=self.skills/name;folder.mkdir(exist_ok=True)
        (folder/'resident.md').write_text(text,encoding='utf-8')
        (self.skills/f'{name}.md').write_text('# Authored skill',encoding='utf-8')

    def test_live_language_changes_preserve_literal_values(self):
        fingerprints=[]
        for lang in frame_language.LANGUAGES:
            self.write_config(lang)
            self.assertEqual(frame_language.language(),lang)
            value=frame_language.text('resident.source',path='PRIVATE Русский Français 中文',slug='unchanged')
            self.assertIn('PRIVATE Русский Français 中文',value)
            fingerprints.append(frame_language.fingerprint())
        self.assertEqual(len(set(fingerprints)),4)
        with self.assertRaises(ValueError):frame_language.text('resident.source',path='missing slug')

    def test_every_frame_catalog_has_identical_keys_and_parameters(self):
        canonical=frame_language.catalog('ru')
        parameters=lambda value: sorted(name for _,name,_,_ in Formatter().parse(value) if name is not None)
        for lang in frame_language.LANGUAGES:
            catalog=frame_language.catalog(lang)
            self.assertEqual(set(catalog),set(canonical))
            for key,value in canonical.items():
                self.assertEqual(parameters(value),parameters(catalog[key]),f'{lang}:{key}')

    def test_turn_pins_both_choices_once_and_next_turn_uses_saved_change(self):
        self.write_config('fr',True)
        with patch.object(frame_language,'settings',wraps=frame_language.settings) as read:
            with frame_language.pin():
                first=frame_language.fingerprint()
                self.write_config('zh',False)
                with frame_language.pin():
                    for _ in range(5):
                        self.assertEqual(frame_language.language(),'fr')
                        self.assertTrue(frame_language.living_prose_enabled())
                        self.assertEqual(frame_language.fingerprint(),first)
                        self.assertIn('dans le contexte',frame_language.text('skills.in_frame'))
                self.assertEqual(read.call_count,1)
        with frame_language.pin():
            self.assertEqual(frame_language.language(),'zh')
            self.assertFalse(frame_language.living_prose_enabled())
        self.write_config('en',True)
        with self.assertRaises(RuntimeError):
            with frame_language.pin():raise RuntimeError('fixture')
        self.write_config('ru',False)
        self.assertEqual(frame_language.language(),'ru')
        self.assertFalse(frame_language.living_prose_enabled())

    def test_toggle_removes_only_living_prose_without_touching_files(self):
        self.add_skill('living-prose','AUTHORED Русский prose')
        self.add_skill('another-skill','OTHER RESIDENT')
        before={str(p):p.read_bytes() for p in self.skills.rglob('*.md')}
        initial=resident_skills.fingerprint(self.skills)
        enabled,rows=resident_skills.render(self.skills)
        self.assertIn('AUTHORED Русский prose',enabled)
        self.write_config('fr',False)
        disabled,rows=resident_skills.render(self.skills)
        self.assertNotIn('AUTHORED Русский prose',disabled)
        self.assertIn('OTHER RESIDENT',disabled)
        self.assertTrue(next(row for row in rows if row['slug']=='living-prose')['disabled'])
        self.assertNotEqual(initial,resident_skills.fingerprint(self.skills))
        self.write_config('en',True)
        self.assertIn('AUTHORED Русский prose',resident_skills.render(self.skills)[0])
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.skills.rglob('*.md')})

    def test_unreadable_config_retains_only_same_agents_last_valid_choice(self):
        self.write_config('fr',False)
        self.assertEqual(frame_language.language(),'fr')
        self.config.write_text('{broken',encoding='utf-8')
        self.assertEqual(frame_language.language(),'fr')
        self.assertFalse(frame_language.living_prose_enabled())
        second=self.root/'second.json';second.write_text('{}',encoding='utf-8')
        with patch.dict(os.environ,{'HELENE_CONFIG':str(second)}):
            self.assertEqual(frame_language.language(),'ru')
            self.assertTrue(frame_language.living_prose_enabled())

    def test_packaged_fallback_is_selected_by_frame_not_interface_and_never_written_home(self):
        kit=self.root/'kit'
        for lang in frame_language.LANGUAGES:
            skills=kit/('soul' if lang=='ru' else f'soul.{lang}')/'skills'
            folder=skills/'living-prose';folder.mkdir(parents=True)
            (folder/'resident.md').write_text(f'STOCK_{lang}',encoding='utf-8')
            (skills/'living-prose.md').write_text('Skill',encoding='utf-8')
        with patch.dict(os.environ,{'HELENE_SKILLS_KIT_ROOT':str(kit)}):
            for lang in frame_language.LANGUAGES:
                self.write_config(lang)
                rendered,rows=resident_skills.render(self.skills)
                self.assertIn(f'STOCK_{lang}',rendered)
                self.assertEqual(rows[0]['source'],'kit')
                self.assertEqual(list(self.skills.iterdir()),[])
            self.add_skill('living-prose','MY OWN VERSION')
            self.assertNotIn('STOCK_',resident_skills.render(self.skills)[0])
            (self.skills/'living-prose/resident.md').write_text('',encoding='utf-8')
            self.assertEqual(resident_skills.render(self.skills)[0],'')

    def test_unchanged_shipped_resident_follows_frame_but_a_personal_edit_wins(self):
        kit = self.root/'kit'
        for lang in frame_language.LANGUAGES:
            folder = kit/('soul' if lang == 'ru' else f'soul.{lang}')/'skills/living-prose'
            folder.mkdir(parents=True)
            (folder/'resident.md').write_text('STOCK_'+lang, encoding='utf-8')
            (folder/'practice.md').write_text('PRACTICE_'+lang, encoding='utf-8')
        self.add_skill('living-prose', 'STOCK_ru')
        before = (self.skills/'living-prose/resident.md').read_bytes()
        with patch.dict(os.environ, {'HELENE_SKILLS_KIT_ROOT': str(kit)}):
            for lang in frame_language.LANGUAGES:
                self.write_config(lang)
                text, rows = resident_skills.render(self.skills)
                self.assertIn('STOCK_'+lang, text)
                self.assertEqual(rows[0]['home_path'], 'soul/skills/living-prose/resident.md')
                self.assertEqual(rows[0]['source'], 'kit')
                self.assertEqual((self.skills/'living-prose/resident.md').read_bytes(), before)
            (self.skills/'living-prose/resident.md').write_text('STOCK_ru + MY EDIT', encoding='utf-8')
            self.assertIn('STOCK_ru + MY EDIT', resident_skills.render(self.skills)[0])
            self.assertNotIn('STOCK_zh', resident_skills.render(self.skills)[0])


if __name__=='__main__':unittest.main()
