"""Installed native suppliers, including both update shapes, use reviewed prose."""
import copy
import importlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import agent
import frame_language
import tool_language
import tool_text_en
from test_tool_language_1511 import structural

SUPPLIERS=(('mount','fence','MOUNT_TOOL'),('self_review','self_review_integration','TOOL'),
 ('kit','kit_proposals','_TOOL'),('inspection','update_inspection','TOOL'),
 ('source_update','source_update','TOOL'),('update_server','updates','TOOL'),
 ('update_desktop','updates','TOOL_DESK'))
RAW='Alex Алекс 中文 {key,value} $&'


def fake():
    return SimpleNamespace(TOOL_IMPL={},BASE_TOOLS=[],OWNER_TOOLS=[],PRAXIS_SELF_TOOLS=[],HAND_PURPOSE={})


def sources():
    result={label:copy.deepcopy(getattr(importlib.import_module('localharness.'+module),symbol))
            for label,module,symbol in SUPPLIERS}
    appearance=importlib.import_module('localharness.window_preferences');subject=fake()
    with tempfile.TemporaryDirectory(prefix='helene-schema-appearance-') as root:
        appearance.install(subject,Path(root))
    result['appearance']=subject.OWNER_TOOLS[0]
    return result


class NativeCatalogLanguages(unittest.TestCase):
    def test_all_exact_actual_suppliers_are_covered_with_distinct_update_variants(self):
        actual=sources();rules={rule['variant']:rule for variants in tool_language.native_registry().values() for rule in variants}
        self.assertEqual(set(actual),set(rules))
        self.assertEqual(tool_language.native_coverage(list(actual.values())),{'reviewed':8,'missing':[]})
        self.assertEqual(sum(len(row['descriptions']) for row in rules.values()),29)
        for label,tool in actual.items():
            self.assertEqual(tool_language.fingerprint(tool),rules[label]['sha256'],label)
            self.assertTrue(tool_language.known(tool))
        self.assertNotEqual(rules['update_desktop']['sha256'],rules['update_server']['sha256'])

    def test_actual_catalog_and_K_use_all16_language_axes_without_changing_calls(self):
        original=sources();rules={rule['variant']:rule for variants in tool_language.native_registry().values() for rule in variants}
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                with patch.object(frame_language,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}):
                    for label,raw in original.items():
                        with self.subTest(ui=ui,frame=language,variant=label):
                            result=tool_language.apply([raw],fallback=tool_text_en.apply)[0]
                            self.assertEqual(structural(result),structural(raw))
                            for entry in rules[label]['descriptions']:
                                cursor=result
                                for part in entry['path']:cursor=cursor[part]
                                self.assertEqual(cursor,frame_language.text(entry['key']))
                            purpose=frame_language.text(rules[label]['purpose'])
                            self.assertEqual(agent._hand_purpose(raw),purpose)
                            self.assertEqual(agent._hand_purpose(result),purpose)
                            with patch.object(agent,'BASE_TOOLS',[raw]):
                                catalog={tool['name']:tool for tool in agent.catalog_tools_for(agent.ChannelContext(owner=True))}
                            self.assertEqual(catalog[raw['name']],result)
                            authored=copy.deepcopy(raw);authored['description']=RAW
                            self.assertIs(tool_language.apply([authored],fallback=tool_text_en.apply)[0],authored)
                            self.assertEqual(agent._hand_purpose(authored),RAW)
                            changed=copy.deepcopy(result);changed['input_schema'].setdefault('required',[]).append('AUTHOR_FIELD')
                            self.assertIsNone(tool_language.purpose(changed))
        self.assertEqual(original,sources())

    def test_actual_installers_offer_the_registered_schemas_without_running_tools(self):
        from localharness import fence,kit_proposals,self_review_integration,source_update,update_inspection,updates,window_preferences
        def only(subject,name):
            return next(tool for tools in (subject.BASE_TOOLS,subject.OWNER_TOOLS) for tool in tools if tool.get('name')==name)
        def covered(subject,name):self.assertTrue(tool_language.known(only(subject,name)),name)
        with tempfile.TemporaryDirectory(prefix='helene-native-installs-') as folder:
            root=Path(folder)
            subject=fake();fence._offer_mount_hand(subject,object());covered(subject,'mount_request')
            kit_proposals.install(subject,root);covered(subject,'review_kit')
            source_update.install(subject,root);covered(subject,'source_update')
            update_inspection.install(subject,root);covered(subject,'update_inspection')
            window_preferences.install(subject,root);covered(subject,'window_appearance')
            integration=self_review_integration.Integration.__new__(self_review_integration.Integration)
            integration.agent=subject;integration.tree=root;integration.code_dir=root/'tree';integration.app_root=root/'app'
            integration.service=SimpleNamespace(forge=SimpleNamespace(SELF_REVIEW_API=1,SOURCE_UPDATE_API=1))
            integration.install();covered(subject,'self_review')
            for server,variant in ((True,'update_server'),(False,'update_desktop')):
                subject=fake()
                with patch.object(updates,'on_server',return_value=server),patch.dict(updates.STATE,{},clear=True):
                    self.assertTrue(updates.install(subject,root))
                schema=only(subject,'update_request')
                rule=next(rule for rule in tool_language.native_registry()['update_request'] if rule['variant']==variant)
                self.assertEqual(tool_language.fingerprint(schema),rule['sha256'])
                covered(subject,'update_inspection')
                if not server:covered(subject,'source_update')


if __name__=='__main__':unittest.main()
