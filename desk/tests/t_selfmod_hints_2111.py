"""Самомодификация 2111: подсказки агентам и людям едут в поставке.

Источник: инцидент Атласа 21.11 и решение владельца «агенты правят себя сами».
HOME.md объясняет агенту, что его (llm.json, исходники рядом, бандлы не править),
START-HERE говорит то же человеку. Тест привязывает поставку к этим секциям,
чтобы подсказки не потерялись при переупаковке.

Запуск: python t_selfmod_hints_2111 -v (репо) / в упакованной поставке, где
HOME.md лежит в tree/, а не в praxis/.
"""
from pathlib import Path
import unittest

DESK = Path(__file__).resolve().parents[1]


def _home_candidates():
    root = DESK.parent
    return [root / 'praxis' / 'HOME.md', root / 'tree' / 'HOME.md']


class SelfModificationHints(unittest.TestCase):
    def test_home_map_teaches_the_agent(self):
        candidates = [p for p in _home_candidates() if p.is_file()]
        self.assertTrue(candidates, 'нет HOME.md ни в praxis/, ни в tree/')
        home = candidates[0].read_text(encoding='utf-8')
        self.assertIn('## Как я меняю себя', home)
        self.assertIn('memory/llm.json', home)
        self.assertIn('Искусственных потолков не держу', home)

    def test_all_start_here_variants_tell_the_human(self):
        names = ('PYTHON-START-HERE.md', 'PYTHON-MACOS-START-HERE.md',
                 'PYTHON-MACOS-INTEL-START-HERE.md', 'PYTHON-LINUX-START-HERE.md')
        for name in names:
            text = (DESK / 'installer' / name).read_text(encoding='utf-8')
            self.assertIn('## Агент может менять себя', text)
            self.assertIn('data/memory/llm.json', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
