"""New sender policy applies to future ingress; owner identity stays distinct."""
import sys
from pathlib import Path
import unittest
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'localharness'), str(Path(__file__).resolve().parents[1])]
from botapi import BotTransport

class Admission(unittest.TestCase):
    def test_owner_choice_applies_and_invalid_mode_returns_to_owner(self):
        bot = BotTransport.__new__(BotTransport)
        bot.owner_id = 'owner'
        bot.allow_from = 'owner'; bot.allowed_ids = set(); bot._muted = {'guest'}
        self.assertFalse(bot.is_allowed('guest'))
        bot.refresh_admission({'allow_from': 'any'})
        self.assertTrue(bot.is_allowed('guest'))
        self.assertEqual(bot.owner_id, 'owner')
        self.assertEqual(bot._muted, set())
        bot.refresh_admission({'allow_from': 'listed', 'allowed_ids': ['guest']})
        self.assertTrue(bot.is_allowed('guest')); self.assertFalse(bot.is_allowed('other'))
        self.assertTrue(bot.is_allowed('owner'))
        bot.refresh_admission({'allow_from': 'unsupported'})
        self.assertFalse(bot.is_allowed('guest')); self.assertTrue(bot.is_allowed('owner'))

if __name__ == '__main__': unittest.main()
