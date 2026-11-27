"""2111: переключение модели в окне не должно возвращать искусственный потолок.

Живой случай: владелец переключил Атласа на GLM через окно — настройка
переписала memory/llm.json с max_tokens 8192/1024, и reasoning-модель снова
сжигала потолок до первого символа. Дефолты boot-сборщика конфига обязаны
совпадать с комнатными дефолтами ядра (llm.py: 32768/8192).
"""
from pathlib import Path
import re
import unittest

BOOT = Path(__file__).resolve().parents[1] / 'localharness' / 'boot.py'


class NoArtificialCeilings(unittest.TestCase):
    def test_boot_defaults_match_core(self):
        text = BOOT.read_text(encoding='utf-8')
        match = re.search(r'default_tokens in \(\("voice", voice, (\d+)\),\s*\("evaluator", judge if judge else voice, (\d+)\)\)', text)
        self.assertIsNotNone(match, 'строка дефолтов boot-конфига не найдена')
        voice, evaluator = int(match.group(1)), int(match.group(2))
        self.assertGreaterEqual(voice, 32768, 'voice-потолок снова искусственный')
        self.assertGreaterEqual(evaluator, 8192, 'evaluator-потолок снова искусственный')


if __name__ == '__main__':
    unittest.main(verbosity=2)
