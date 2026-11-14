"""Primary creation keeps UI/frame choices independent before birth authority."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from localharness import first_start, language_settings


class FirstCreationLanguages(unittest.TestCase):
    def test_package_and_transplanted_runner_import_paths_use_frame_axis(self):
        from localharness import boot
        config={'lang':'ru','agent':{'frame_language':'zh'}}
        self.assertEqual(boot.agent_name(config),'智能体')
        source=Path(boot.__file__).resolve()
        with patch.dict(sys.modules), patch.object(sys,'path',[str(source.parent),*sys.path]):
            sys.modules.pop('language_settings',None)
            spec=importlib.util.spec_from_file_location('helene_standalone_boot_fixture',source)
            standalone=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(standalone)
            self.assertEqual(standalone.agent_name(config),'智能体')
            self.assertEqual(standalone.owner_name({'lang':'zh','agent':{'frame_language':'en'}}),'owner')

    def test_all_sixteen_choices_preserve_personal_fields_and_custom_soul(self):
        original={'lang':'ru','agent':{'custom':{'text':'AUTHORED 日本語'}},
                  'owner':{'custom':'AUTHORED owner'},'private_extension':{'keep':True}}
        before=copy.deepcopy(original)
        soul={'kind':'text','text':'# AUTHORED конституция 日本語\n'}
        for ui in language_settings.LANGUAGES:
            for frame in language_settings.LANGUAGES:
                with self.subTest(ui=ui,frame=frame):
                    values={'name':'  AUTHORED $& 日本語  ','owner':'  AUTHORED Owner  ',
                            'lang':ui,'frame_language':frame,'living_prose':True,'soul':soul}
                    selected=first_start.creation_config(original,values)
                    self.assertEqual(selected['lang'],ui)
                    self.assertEqual(selected['agent']['frame_language'],frame)
                    self.assertEqual(selected['agent']['name'],'AUTHORED $& 日本語')
                    self.assertEqual(selected['agent']['custom'],original['agent']['custom'])
                    self.assertEqual(selected['owner']['custom'],'AUTHORED owner')
                    self.assertEqual(selected['private_extension'],original['private_extension'])
                    self.assertTrue(selected['agent']['living_prose'])
                    self.assertEqual(values['soul'],soul)
                    selected['agent']['custom']['text']='changed copy'
                    self.assertEqual(original,before)

    def test_missing_new_choices_keep_legacy_and_existing_independent_settings(self):
        legacy=first_start.creation_config({'lang':'en'}, {'name':'New','owner':'Owner'})
        self.assertNotIn('frame_language',legacy['agent'])
        self.assertEqual(language_settings.frame_language(legacy),'ru')
        self.assertTrue(language_settings.living_prose(legacy))
        current={'lang':'en','agent':{'frame_language':'zh','living_prose':False}}
        selected=first_start.creation_config(current,{'name':'New','owner':'Owner','lang':'fr'})
        self.assertEqual(selected['agent'],{'name':'New','frame_language':'zh','living_prose':False})
        self.assertEqual(current['lang'],'en')

    def test_invalid_choices_refuse_before_any_birth_or_file_write(self):
        base={'name':'Fixture','owner':'Owner','provider':'openai','model':'fixture','key':'fixture'}
        for bad in ({'lang':'de'},{'frame_language':'automatic'},{'frame_language':''},
                    {'living_prose':0},{'living_prose':'false'}):
            with self.subTest(bad=bad), patch.object(first_start,'ready',return_value=True), \
                 patch.object(first_start,'load',return_value={'lang':'en'}), \
                 patch.object(first_start,'_write') as writer, \
                 patch('localharness.first_birth.admit') as admission:
                with self.assertRaises(ValueError):
                    first_start._create_agent_locked(Path('synthetic-root'),{**base,**bad},places=object())
                writer.assert_not_called();admission.assert_not_called()


if __name__=='__main__':unittest.main()
