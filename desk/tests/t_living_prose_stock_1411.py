"""The shipped skill has four complete, bounded and attributed editions."""
import re
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'resources'
FILES={'resident.md','workshop.md','practice.md','sources.md'}


class LivingProseStock(unittest.TestCase):
    def test_each_language_has_a_full_skill_and_resolving_local_links(self):
        residents=[]
        for lang in ('ru','en','fr','zh'):
            skills=ROOT/('soul' if lang=='ru' else f'soul.{lang}')/'skills'
            folder=skills/'living-prose'
            self.assertEqual({p.name for p in folder.iterdir()},FILES)
            for path in [skills/'living-prose.md',*folder.glob('*.md')]:
                text=path.read_text(encoding='utf-8')
                self.assertTrue(text.strip())
                for target in re.findall(r'\]\(([^)#]+)\)',text):
                    if target.startswith(('https://','http://')):continue
                    resolved=(path.parent/target).resolve()
                    self.assertTrue(resolved.is_relative_to(skills.resolve()))
                    self.assertTrue(resolved.is_file(),f'{path}: {target}')
            text=(folder/'resident.md').read_text(encoding='utf-8')
            residents.append(text)
            self.assertLess(len(text)+500,6000)
            self.assertEqual(len(re.findall(r'^> ',text,re.M)),7)
            self.assertIn('Ouroboros',text)
            if lang!='ru':self.assertNotRegex(text,r'[А-Яа-яЁё]')
        self.assertEqual(len(set(residents)),4)

    def test_neutral_examples_do_not_prescribe_persona_or_gender(self):
        text=(ROOT/'soul/skills/living-prose/resident.md').read_text(encoding='utf-8')
        for phrase in ('Нашла причину','Нашёл причину','Представила','Я бы оставила','я перепутала','я перепутал','выбрался'):
            self.assertNotIn(phrase,text)
        self.assertIn('Не автобиография',text)


if __name__=='__main__':unittest.main()
