"""Independent language axes, real config writes and resident context selection."""
from __future__ import annotations
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
from localharness import config_cli, language_settings
from deskd import phone_settings, agentcfg


class LanguageSettings(unittest.TestCase):
    def test_all_sixteen_pairs_remain_independent_after_phone_patch(self):
        for ui in language_settings.LANGUAGES:
            for frame in language_settings.LANGUAGES:
                original = {'lang':ui, 'agent':{'name':'Fixture','frame_language':frame,'custom':'keep'}}
                changed = phone_settings.prepare(original, {'agent.living_prose':False})
                self.assertEqual(language_settings.interface_language(changed, ['ru-RU']), ui)
                self.assertEqual(language_settings.frame_language(changed), frame)
                self.assertFalse(language_settings.living_prose(changed))
                self.assertEqual(changed['agent']['custom'], 'keep')
                changed = phone_settings.prepare(changed, {'lang':'fr'})
                self.assertEqual(language_settings.frame_language(changed), frame)

    def test_missing_legacy_setting_does_not_follow_interface(self):
        for ui in language_settings.LANGUAGES:
            self.assertEqual(language_settings.frame_language({'lang':ui}), 'ru')
            self.assertTrue(language_settings.living_prose({'lang':ui}))

    def test_system_preference_and_chinese_script_policy(self):
        for tags, expected in ((['de-DE','fr-FR'],'fr'),(['zh-Hant','en-US'],'en'),
                               (['zh-CN'],'zh'),(['zh-Hans-SG'],'zh'),(['zh-HK'],'en'),
                               (['rubbish','english'],'en'),(['ru_RU'],'ru')):
            self.assertEqual(language_settings.interface_language({}, tags), expected)
        self.assertEqual(language_settings.interface_language({'lang':'fr'}, ['zh-CN']), 'fr')

    def test_actual_writer_preserves_axes_and_rejects_invalid_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'helene.json'
            config={'lang':'zh','agent':{'name':'Fixture','frame_language':'en','living_prose':False,'custom':{'x':1}}}
            config_cli.save(path, json.dumps(config), None)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), config)
            before=path.read_bytes()
            for change in ({'lang':'de'}, {'agent':{'frame_language':'automatic'}},
                           {'agent':{'living_prose':'false'}}, {'agent':[]}):
                with self.assertRaises(ValueError):
                    config_cli.save(path, json.dumps({**config, **change}), None)
                self.assertEqual(path.read_bytes(), before)

    def test_server_writer_enforces_the_same_setting_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'helene.json'
            path.write_text(json.dumps({'lang':'en','agent':{'frame_language':'fr'}}),encoding='utf-8')
            with patch.object(agentcfg.readers,'config_path',return_value=path):
                result=agentcfg.save({'agent':{'frame_language':'zh','living_prose':False}})
                self.assertTrue(result['ok'])
                value=json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(value['lang'],'en')
                self.assertEqual(value['agent']['frame_language'],'zh')
                before=path.read_bytes()
                self.assertFalse(agentcfg.save({'agent':{'living_prose':0}})['ok'])
                self.assertEqual(path.read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
