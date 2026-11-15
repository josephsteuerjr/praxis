"""Actual first-start preflights expose typed UI outcomes before creation effects."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

DESK=Path(__file__).resolve().parents[1];sys.path.insert(0,str(DESK))
from localharness import first_start,ui_language as words


class FirstStartErrors(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.places=SimpleNamespace(data=self.root/'data')

    def test_public_create_refuses_before_data_custody_without_preparation(self):
        with patch.object(first_start,'ready',return_value=False),patch('localharness.boot.birth_custody',side_effect=AssertionError('custody')) as custody:
            with self.assertRaises(ValueError) as caught:first_start._create_agent(self.root,{},places=self.places)
        custody.assert_not_called();self.assertFalse(self.places.data.exists())
        self.assertEqual(words.native_error(caught.exception)['code'],'first_preparation_needed')
        self.assertEqual(str(caught.exception),'Сначала нужно завершить подготовку')

    def test_actual_creation_fields_on16_axes_keep_config_and_never_admit_birth(self):
        inputs=[({},'first_agent_fields'),({'name':'Literal 日本語','model':'fixture','provider':'openai'},'first_api_key'),
                ({'name':'Literal 日本語','model':'fixture','provider':'openai','key':'synthetic-fixture','soul':{'kind':'unknown'}},'first_soul_choice')]
        for ui in ('ru','en','fr','zh'):
            for frame in ('ru','en','fr','zh'):
                path=self.root/'helene.json';path.write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf8');before=path.read_bytes()
                with patch.object(first_start,'ready',return_value=True),patch('localharness.first_birth.admit',side_effect=AssertionError('birth')) as admit:
                    for values,code in inputs:
                        with self.assertRaises(ValueError) as caught:first_start._create_agent_locked(self.root,values,places=self.places)
                        self.assertEqual(words.native_error(caught.exception)['code'],code)
                        self.assertEqual(words.error_text(caught.exception,self.root),words.text('native.app.error.'+code,ui,{}))
                admit.assert_not_called();self.assertEqual(path.read_bytes(),before);self.assertFalse(self.places.data.exists())


if __name__=='__main__':unittest.main()
