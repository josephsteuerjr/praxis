"""Actual stock seeding keeps UI/frame settings and authored files independent."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

DESK=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(DESK/'localharness'))
import boot


class StockLanguage(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)

    def test_four_by_four_birth_uses_agent_language_and_full_inventory(self):
        expected={str(relative) for relative,_ in boot._stock_files('soul',{})}
        for ui in ('ru','en','fr','zh'):
            for frame in ('ru','en','fr','zh'):
                config={'lang':ui,'agent':{'name':'My Agent','frame_language':frame},'owner':{'name':'My Owner'}}
                tree=self.root/(ui+'-'+frame)
                boot._seed_kit(tree,config)
                self.assertEqual({str(p.relative_to(tree/'soul')) for p in (tree/'soul').rglob('*') if p.is_file()},expected)
                for relative in ('VOICE.md','self.md','skills/living-prose/resident.md'):
                    source=boot._RESOURCES/('soul' if frame=='ru' else 'soul.'+frame)/relative
                    self.assertEqual((tree/'soul'/relative).read_text(encoding='utf-8'),boot._names(source.read_text(encoding='utf-8'),config))
                self.assertNotIn('{{',(tree/'soul/VOICE.md').read_text(encoding='utf-8'))
                expected_day=boot._RESOURCES/('self-day-zero.md' if frame=='ru' else f'self-day-zero.{frame}.md')
                self.assertEqual(boot._stock_path('self-day-zero.md',config),expected_day)

    def test_postbirth_proposes_translated_text_but_does_not_overwrite_home(self):
        tree=self.root/'agent';voice=tree/'soul/VOICE.md';voice.parent.mkdir(parents=True)
        voice.write_text('MY AUTHORED VOICE — Русский 日本語',encoding='utf-8')
        before=voice.read_bytes();staged=[]
        with patch('kit_proposals.stage',side_effect=lambda tree,relative,data,**kw:staged.append((relative,data))):
            boot._seed_kit(tree,{'lang':'ru','agent':{'frame_language':'fr'}},birth=False)
        self.assertEqual(voice.read_bytes(),before)
        self.assertEqual(len(list((tree/'soul').rglob('*.md'))),1)
        candidate=next(data for relative,data in staged if relative=='soul/VOICE.md')
        self.assertTrue(candidate.startswith(b'# Voix'))

    def test_missing_names_are_localized_without_changing_given_names(self):
        for frame in ('en','fr','zh'):
            cfg={'lang':'ru','agent':{'frame_language':frame}}
            self.assertNotIn('Агент',boot._names('{{agent}} {{owner}}',cfg))
            self.assertNotIn('владелец',boot._names('{{agent}} {{owner}}',cfg))
            cfg['agent']['name']='Личное имя';cfg['owner']={'name':'名字'}
            self.assertEqual(boot._names('{{agent}} {{owner}}',cfg),'Личное имя 名字')


if __name__=='__main__':unittest.main()
