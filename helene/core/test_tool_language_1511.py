"""Native MCP schemas stay callable in every frame language and audience."""
import copy
import json
from string import Formatter
import unittest
from unittest.mock import patch

import agent
import frame_language
import tool_language
import windows_mcp_tools as mcp
import tool_text_en

RAW='AUTHORED Русский 日本語 {unknown} $&'


def structural(value):
    if isinstance(value,dict):
        return {key:structural(item) for key,item in value.items() if key!='description'}
    if isinstance(value,list):return [structural(item) for item in value]
    return value


class ToolLanguages(unittest.TestCase):
    def test_stock_english_fallback_stays_exact_before_general_translations(self):
        names=('BASE_TOOLS','SHARED_CONTEXT_TOOLS','OWNER_TOOLS','WEB_READ_TOOL','WEB_FIND_TOOL',
               'SEND_EMAIL_TOOL','CHECK_EMAIL_TOOL','MAIL_READ_TOOL','MAIL_DRAFT_TOOL',
               'TASK_CONTROL_TOOL','DESCRIBE_TOOL','CALL_TOOL')
        stock={}
        for name in names:
            value=getattr(agent,name)
            for tool in value if isinstance(value,list) else [value]:
                if tool.get('input_schema'):stock[tool['name']]=tool
        self.assertEqual(set(stock),set(tool_language.sources()))
        before=copy.deepcopy(list(stock.values()))
        with frame_language.use('en'):
            self.assertEqual(tool_language.apply(list(stock.values()),fallback=tool_text_en.apply),
                             tool_text_en.apply(list(stock.values())))
        self.assertEqual(list(stock.values()),before)

    def test_general_author_edit_is_preserved_before_legacy_overlay_can_erase_it(self):
        authored=copy.deepcopy(agent.DESCRIBE_TOOL);authored['description']=RAW
        with patch.object(tool_text_en,'apply',wraps=tool_text_en.apply) as legacy:
            for language in frame_language.LANGUAGES:
                with frame_language.use(language):
                    self.assertIs(tool_language.apply([authored],fallback=legacy)[0],authored)
                    self.assertEqual(agent._hand_purpose(authored),RAW)
                    self.assertFalse(tool_language.known(authored))
        legacy.assert_not_called()

    def test_complete_native_catalog_has_fresh_reviewed_text_and_no_template_parameters(self):
        self.assertEqual(tool_language.coverage(mcp.TOOLS),{'reviewed':21,'missing':[],'stale':[]})
        self.assertEqual(sum(len(row['descriptions']) for row in tool_language.registry().values()),112)
        for rule in tool_language.registry().values():
            for entry in rule['descriptions']:
                for language in frame_language.LANGUAGES:
                    text=frame_language.catalog(language)[entry['key']]
                    self.assertFalse([key for literal,key,fmt,conv in Formatter().parse(text) if key is not None])

    def test_actual_catalog_and_pointer_follow_all16_language_pairs_for_both_audiences(self):
        original=json.dumps(mcp.TOOLS,ensure_ascii=False,sort_keys=True)
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                settings={'lang':ui,'agent':{'frame_language':language}}
                with patch.object(frame_language,'settings',return_value=settings):
                    for owner in (True,False):
                        with self.subTest(ui=ui,frame=language,owner=owner):
                            catalog=agent.catalog_tools_for(agent.ChannelContext(owner=owner))
                            subset=[tool for tool in catalog if tool.get('name') in mcp.SCHEMAS]
                            self.assertEqual(structural(subset),structural(mcp.TOOLS))
                            for tool in subset:
                                rule=tool_language.registry()[tool['name']]
                                for entry in rule['descriptions']:
                                    value=tool
                                    for part in entry['path']:value=value[part]
                                    self.assertEqual(value,frame_language.text(entry['key']))
                                self.assertEqual(agent._hand_purpose(tool),frame_language.text(rule['purpose']))
                                # The same typed validator still accepts exact arguments.
                            args={'mode':'read','path':'C:\\literal space\\file.txt','offset':2,'limit':3}
                            schema=next(x['input_schema'] for x in subset if x['name']=='windows_mcp_FileSystem')
                            mcp.validate(args,schema)
                            self.assertEqual(args['path'],'C:\\literal space\\file.txt')
                            pointer=agent.hands_pointer_text(catalog)
                            self.assertIn('windows_mcp_FileSystem(',pointer)
                            self.assertIn(frame_language.text(tool_language.registry()['windows_mcp_FileSystem']['purpose']),pointer)
        self.assertEqual(json.dumps(mcp.TOOLS,ensure_ascii=False,sort_keys=True),original)

    def test_authored_and_stale_schemas_are_literal_and_named_by_coverage(self):
        authored=copy.deepcopy(mcp.TOOLS[0]);authored['description']=RAW
        changed=copy.deepcopy(mcp.TOOLS[1]);changed['input_schema']['properties']['mode']['enum'].append('NEW-MODE')
        before=copy.deepcopy([authored,changed])
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):
                self.assertEqual(tool_language.apply([authored,changed]),before)
                self.assertIsNone(tool_language.purpose(authored))
                self.assertIsNone(tool_language.purpose(changed))
                self.assertEqual(agent._hand_purpose(authored),RAW)
        result=tool_language.coverage([authored,changed]+mcp.TOOLS[2:])
        self.assertEqual(result['stale'],[authored['name'],changed['name']])
        custom={'name':'authored_tool','description':RAW,'input_schema':{'type':'object'}}
        self.assertIs(tool_language.apply([custom])[0],custom)

    def test_one_projection_pins_the_language_and_does_not_mutate_source(self):
        settings={'agent':{'frame_language':'fr'}}
        original_text=frame_language.text
        changed=False
        def text(key,**values):
            nonlocal changed
            if not changed:
                settings['agent']['frame_language']='zh';changed=True
            return original_text(key,**values)
        with patch.object(frame_language,'settings',side_effect=lambda:copy.deepcopy(settings)), \
             patch.object(frame_language,'text',side_effect=text):
            result=tool_language.apply(mcp.TOOLS)
        key=tool_language.registry()[mcp.TOOLS[-1]['name']]['descriptions'][0]['key']
        self.assertEqual(result[-1]['description'],frame_language.catalog('fr')[key].format())
        self.assertEqual(settings['agent']['frame_language'],'zh')


if __name__=='__main__':unittest.main()
