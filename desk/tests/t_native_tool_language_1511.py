"""Actual native schema adapters, literal install data, late language and bounds."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import agent
import frame_language
import tool_language
import tool_text_en
import shell_words
from test_general_tool_language_1511 import stock
from test_tool_language_1511 import structural

RAW = 'Alex — Алекс — 日本語 {literal} $&'


class NativeSchemaLanguages(unittest.TestCase):
    def setUp(self):
        guard = patch.dict(tool_language._runtime, {}, clear=True)
        guard.start(); self.addCleanup(guard.stop)

    def shell(self, *, platform='win32', root=None):
        root = root or Path('C:/'+RAW)
        tool = copy.deepcopy(stock()['shell'])
        fake = SimpleNamespace(BASE_TOOLS=[tool], OWNER_TOOLS=[tool], SHELL_TOOL=tool,
            tool_language=tool_language, WORKDIR=str(root/'custom-cwd'), SHELL_TIMEOUT=47)
        old = copy.deepcopy(tool)
        self.assertEqual(shell_words.install(fake,root/'data',root,platform=platform),1)
        return fake, old, root

    def test_actual_native_variants_all16_axes_three_platforms_preserve_shape_and_literal_paths(self):
        english_before = copy.deepcopy(tool_text_en.EN)
        for platform, system in (('win32','Windows'),('darwin','macOS'),('linux','Linux')):
            fake, raw, root = self.shell(platform=platform)
            native = fake.SHELL_TOOL; saved = copy.deepcopy(native)
            self.assertEqual(structural(native),structural(raw))
            for ui in frame_language.LANGUAGES:
                for language in frame_language.LANGUAGES:
                    with self.subTest(platform=platform,ui=ui,frame=language), patch.object(
                        frame_language,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}):
                        projected = tool_language.apply([native],fallback=tool_text_en.apply)[0]
                        self.assertEqual(structural(projected),structural(raw))
                        self.assertIn((root/'data').as_posix(),projected['description'])
                        self.assertIn((root/'custom-cwd').as_posix(),projected['description'])
                        self.assertIn(system,projected['description'])
                        self.assertIn('47',projected['input_schema']['properties']['timeout']['description'])
                        self.assertIn(RAW,projected['description'])
                        key = 'native.shell.windows' if platform=='win32' else 'native.shell.posix'
                        values = dict(home=(root/'data').as_posix(),code=(root/'tree').as_posix(),
                            app=(root/'app').as_posix(),cwd=(root/'custom-cwd').as_posix(),timeout=47)
                        if platform!='win32':values['system']=system
                        self.assertEqual(projected['description'],frame_language.text(key,**values))
                        purpose = frame_language.text('native.shell.purpose',system=system,cwd=(root/'custom-cwd').as_posix())
                        self.assertEqual(agent._hand_purpose(native),purpose)
                        self.assertEqual(agent._hand_purpose(projected),purpose)
                        with patch.object(agent,'OWNER_TOOLS',[native]):
                            actual={tool['name']:tool for tool in agent.catalog_tools_for(agent.ChannelContext(owner=True))}
                        self.assertEqual(actual['shell'],projected)
            self.assertEqual(native,saved)
        self.assertEqual(tool_text_en.EN,english_before)

    def test_unknown_authored_source_and_after_mutation_stay_literal(self):
        authored = copy.deepcopy(stock()['shell']);authored['description']=RAW
        fake=SimpleNamespace(BASE_TOOLS=[authored],tool_language=tool_language)
        self.assertEqual(shell_words.install(fake,Path('C:/home'),Path('C:/app')),0)
        self.assertEqual(authored['description'],RAW)
        self.assertEqual(tool_language._runtime,{})
        fake,_,_=self.shell()
        native=copy.deepcopy(fake.SHELL_TOOL)
        for path,value in ((['description'],RAW),(['input_schema','properties','command','type'],'number')):
            changed=copy.deepcopy(native);cursor=changed
            for part in path[:-1]:cursor=cursor[part]
            cursor[path[-1]]=value
            for language in frame_language.LANGUAGES:
                with frame_language.use(language):
                    self.assertIs(tool_language.apply([changed],fallback=tool_text_en.apply)[0],changed)
                    self.assertIsNone(tool_language.purpose(changed))
                    self.assertFalse(tool_language.register_variant(changed,changed,descriptions=[]))

    def test_hot_language_literal_template_values_and_retention_bound(self):
        fake,_,_=self.shell();native=fake.SHELL_TOOL
        settings={'agent':{'frame_language':'fr'}}
        original=frame_language.text
        def switching(key,**values):
            settings['agent']['frame_language']='zh'
            return original(key,**values)
        with patch.object(frame_language,'settings',side_effect=lambda:copy.deepcopy(settings)),patch.object(frame_language,'text',side_effect=switching):
            projected=tool_language.apply([native])[0]
        self.assertEqual(projected['input_schema']['properties']['command']['description'],frame_language.catalog('fr')['native.shell.command'])
        for number in range(20):self.shell(root=Path('C:/variant-'+str(number)))
        self.assertEqual(len(tool_language._runtime),1)
        self.assertEqual(len(tool_language._runtime['shell']),tool_language._RUNTIME_LIMIT)
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):
                self.assertIs(tool_language.apply([native])[0],native)
                self.assertIsNone(tool_language.purpose(native))

    def test_native_chain_inherits_only_unchanged_reviewed_paths_and_rejects_non_prose_targets(self):
        before=copy.deepcopy(stock()['reply']);after=copy.deepcopy(before)
        after['input_schema']['properties']['new_flag']={'type':'boolean'}
        self.assertTrue(tool_language.register_variant(before,after,descriptions=[]))
        final=copy.deepcopy(after);final['input_schema']['properties']['text']['description']=RAW
        self.assertTrue(tool_language.register_variant(after,final,descriptions=[]))
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):
                translated=tool_language.apply([final])[0]
                self.assertEqual(translated['input_schema']['properties']['text']['description'],RAW)
                self.assertEqual(translated['input_schema']['properties']['new_flag'],{'type':'boolean'})
                self.assertEqual(translated['description'],frame_language.text(tool_language.registry()['reply']['descriptions'][0]['key']))
                self.assertIsNotNone(tool_language.purpose(translated))
        with self.assertRaises(ValueError):
            tool_language.register_variant(before,after,descriptions=[{'path':['input_schema','type'],'key':'native.shell.command'}])
        with self.assertRaises(ValueError):
            tool_language.register_variant(before,after,descriptions=[{'path':['description'],'key':'native.shell.timeout','values':{}}])

    def test_legacy_linux_description_never_claims_mac_or_rewrites_path_contents(self):
        root=Path('/tmp/Mac владельца/owner\'s Mac/{literal}')
        for method in (shell_words.text,shell_words.text_en):
            words=method(root,root,platform='linux')
            self.assertIn(root.as_posix(),words)
            self.assertIn('Linux',words)
        self.assertNotIn('Mac',shell_words.text(Path('/tmp/home'),Path('/tmp/app'),platform='linux'))


if __name__=='__main__':unittest.main()
