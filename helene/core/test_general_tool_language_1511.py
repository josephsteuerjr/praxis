"""Reviewed general schemas retain exact calls; personal text is never translated."""
import copy
from string import Formatter
import unittest
from unittest.mock import patch

import agent
import frame_language
import tool_language
import tool_text_en
import windows_mcp_tools
from test_tool_language_1511 import RAW, structural

REVIEWED = ('generate_image','recall','clear_owner_marks','remember','journal','manage_identity',
            'manage_perception','reply','end_turn','preview_reply','switch_brain','stay_silent',
            'focus','rest','my_capabilities','read_run_result','list_active_runs','reconcile_run',
            'group_context','speak','search_chats','search_private_messages','read_other_chat','reread_this_chat',
            'python','run_command','code_query','proposal','project','memory_graph')


def stock():
    result={}
    for name in ('BASE_TOOLS','SHARED_CONTEXT_TOOLS','OWNER_TOOLS','WEB_READ_TOOL','WEB_FIND_TOOL',
                 'SEND_EMAIL_TOOL','CHECK_EMAIL_TOOL','MAIL_READ_TOOL','MAIL_DRAFT_TOOL',
                 'TASK_CONTROL_TOOL','DESCRIBE_TOOL','CALL_TOOL'):
        value=getattr(agent,name)
        for tool in value if isinstance(value,list) else [value]:
            if tool.get('input_schema'):result[tool['name']]=tool
    return result


class GeneralToolLanguages(unittest.TestCase):
    def test_reviewed_inventory_is_complete_fresh_and_has_no_runtime_format_fields(self):
        registered=set(tool_language.registry())-set(windows_mcp_tools.SCHEMAS)
        self.assertEqual(registered,set(REVIEWED))
        tools=stock()
        self.assertEqual(tool_language.coverage(list(tools.values())),
                         {'reviewed':21+len(REVIEWED),'missing':[],'stale':[]})
        for name in REVIEWED:
            for entry in tool_language.registry()[name]['descriptions']:
                self.assertIn('present',entry);self.assertIn('source',entry)
                for language in frame_language.LANGUAGES:
                    fields=[key for text,key,fmt,conv in Formatter().parse(frame_language.catalog(language)[entry['key']]) if key is not None]
                    self.assertEqual(fields,[])

    def test_all16_axes_preserve_every_callable_field_and_exact_authored_arguments(self):
        raw=stock();selected=[raw[name] for name in REVIEWED];before=copy.deepcopy(selected)
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                with self.subTest(ui=ui,frame=language), \
                     patch.object(frame_language,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}):
                    translated=tool_language.apply(selected,fallback=tool_text_en.apply)
                    self.assertEqual(structural(translated),structural(selected))
                    for tool in translated:
                        rule=tool_language.registry()[tool['name']]
                        for entry in rule['descriptions']:
                            text=tool
                            for part in entry['path']:text=text[part]
                            self.assertEqual(text,frame_language.text(entry['key']))
                        self.assertEqual(agent._hand_purpose(tool),frame_language.text(rule['purpose']))
                        self.assertEqual(agent._hand_purpose(raw[tool['name']]),frame_language.text(rule['purpose']))
                    by_name={tool['name']:tool for tool in translated}
                    for name,arguments in (('reply',{'text':RAW}),('remember',{'person':RAW,'fact':RAW})):
                        original=copy.deepcopy(arguments)
                        windows_mcp_tools.validate(arguments,by_name[name]['input_schema'])
                        self.assertEqual(arguments,original)
                    self.assertNotIn('Yegor',by_name['manage_identity']['description'])
                    identity_action=by_name['manage_identity']['input_schema']['properties']['action']
                    self.assertEqual(set(identity_action['description'].split(' | ')),set(identity_action['enum']))
                    actual={tool['name']:tool for tool in agent.catalog_tools_for(agent.ChannelContext(owner=False))}
                    for tool in translated:
                        if tool['name'] in actual:self.assertEqual(actual[tool['name']],tool)
        self.assertEqual(selected,before)

    def test_changed_general_tool_is_literal_even_when_its_name_is_registered(self):
        authored=copy.deepcopy(stock()['manage_identity']);authored['description']=RAW
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):
                self.assertIs(tool_language.apply([authored],fallback=tool_text_en.apply)[0],authored)
                self.assertEqual(agent._hand_purpose(authored),RAW)
                self.assertEqual(tool_language.coverage([authored],names=['manage_identity'])['stale'],['manage_identity'])


if __name__=='__main__':unittest.main()
