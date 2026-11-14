"""Actual stock indexes feed the actual capability parser without live imports."""
import ast
from pathlib import Path
import re
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import frame_language
import resident_skills

CORE=Path(__file__).resolve().parent
RESOURCES=CORE.parents[1]/'desk/resources'


def function(file,name,namespace):
    source=(CORE/file).read_text(encoding='utf-8')
    nodes=[node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name==name]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),file,'exec'),namespace)
    return namespace[name]


class SkillNavigationLanguage(unittest.TestCase):
    def test_four_stock_indexes_keep_all110_file_addresses_and_localized_purposes(self):
        read=function('capabilities.py','_skills_list',{'Path':Path,'frame_language':frame_language,
                                                     'resident_skills':resident_skills})
        expected={p.stem for p in (RESOURCES/'soul/skills').glob('*.md') if p.name!='INDEX.md'}
        self.assertEqual(len(expected),110)
        with tempfile.TemporaryDirectory() as temporary:
            for language in frame_language.LANGUAGES:
                root=Path(temporary)/language
                shutil.copytree(RESOURCES/'soul/skills',root)
                if language!='ru':shutil.copytree(RESOURCES/('soul.'+language)/'skills',root,dirs_exist_ok=True)
                with frame_language.use(language):rows=read(root/'INDEX.md')
                self.assertEqual({row['name'] for row in rows},expected)
                self.assertEqual(len(rows),110)
                self.assertFalse(any(row.get('orphan') for row in rows))
                self.assertTrue(all(row['line'] and row['title']==row['name'] for row in rows))
                if language!='ru':self.assertFalse(any(re.search('[А-Яа-яЁё]',row['line']) for row in rows))
                gnome=next(row for row in rows if row['name']=='merge-gates-gnome-review')
                self.assertNotIn('обязатель',gnome['line'])
                self.assertEqual((root/'INDEX.md').read_text(encoding='utf-8').count('](merge-gates-gnome-review.md)'),1)

    def test_lossless_index_read_failures_follow_frame_language(self):
        navigation=SimpleNamespace(skill_navigation=lambda index:'VALID MAP')
        namespace={'Path':Path,'frame_language':frame_language,'capabilities':navigation}
        read=function('skill_navigation.py','owner_map',namespace)
        with tempfile.TemporaryDirectory() as temporary:
            index=Path(temporary)/'INDEX.md'
            for language in frame_language.LANGUAGES:
                index.write_bytes(b'\xff')
                with frame_language.use(language):
                    self.assertEqual(read(index),frame_language.text('skills.index_unreadable',error_type='UnicodeDecodeError'))
                index.write_text('AUTHORED 日本語',encoding='utf-8')
                with frame_language.use(language):self.assertEqual(read(index),'VALID MAP')

    def test_changed_or_unavailable_index_is_not_reported_as_confirmed(self):
        namespace={'Path':Path,'frame_language':frame_language,
                   'capabilities':SimpleNamespace(skill_navigation=lambda index:'UNCONFIRMED MAP')}
        read=function('skill_navigation.py','owner_map',namespace)
        for language in frame_language.LANGUAGES:
            for changed in (True,False):
                calls=[]
                def content():
                    calls.append(1)
                    if len(calls)==1:return b'original'
                    if changed:return b'changed'
                    raise PermissionError('fixture only')
                index=SimpleNamespace(exists=lambda:True,read_bytes=content)
                with patch.dict(namespace,{'Path':lambda value:index}),frame_language.use(language):
                    self.assertEqual(read('fixture'),frame_language.text('skills.index_changed' if changed else 'skills.index_unavailable'))


if __name__=='__main__':unittest.main()
