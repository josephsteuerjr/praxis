"""Lesson recall works for French and Chinese goals; earlier tokens are unchanged.

Before: the tokenizer accepted only ASCII and Cyrillic, so "réseau" became "seau" and a
Chinese goal produced no tokens at all. Saved lessons are read as recorded, never rewritten.
"""
import re
import tempfile
import unittest
from pathlib import Path

import forge_learning


def old_tokens(text):
    return {t.casefold() for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9_.-]{3,}", text or "")}


class LessonTokens(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name)
        self.root = Path(temp.name) / 'other-project'
        rows = [
            {'id': 'lesson-fr', 'at': '2026-11-16T00:00:00', 'project': 'p1',
             'goal': 'Corriger la connexion réseau défaillante', 'lesson': 'vérifier le délai',
             'changed': ['réseau.py'], 'tags': []},
            {'id': 'lesson-zh', 'at': '2026-11-16T00:00:01', 'project': 'p2',
             'goal': '修复登录错误', 'lesson': '先检查缓存', 'changed': [], 'tags': []},
            {'id': 'lesson-en', 'at': '2026-11-16T00:00:02', 'project': 'p3',
             'goal': 'Fix forge_learning.py recall', 'lesson': 'keep tokens', 'changed': [],
             'tags': ['forge_learning.py']},
        ]
        for row in rows:
            forge_learning._append(forge_learning.lesson_path(self.state), row)
        self.before = forge_learning.lesson_path(self.state).read_bytes()

    def recalled(self, goal):
        return [row['id'] for row in forge_learning.recall(self.state, goal, self.root)]

    def test_french_and_chinese_goals_find_their_lessons(self):
        self.assertEqual(self.recalled('réseau défaillant à corriger')[:1], ['lesson-fr'])
        self.assertEqual(self.recalled('登录错误又出现了')[:1], ['lesson-zh'])
        self.assertEqual(self.recalled('forge_learning.py tokens')[:1], ['lesson-en'])
        self.assertEqual(forge_learning.lesson_path(self.state).read_bytes(), self.before,
                         'recall never rewrites saved lessons')

    def test_ascii_and_cyrillic_tokens_are_unchanged(self):
        for sample in ('Fix forge_learning.py recall for task-id v2.8.0',
                       'Починить вход в Telegram и проверить кэш', 'a b c', ''):
            self.assertEqual(forge_learning._tokens(sample), old_tokens(sample), sample)
        self.assertIn('réseau', forge_learning._tokens('Le réseau'))
        self.assertTrue({'登录', '错误'} <= forge_learning._tokens('修复登录错误'))


if __name__ == '__main__':
    unittest.main()
