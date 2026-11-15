"""Only coded product errors are localized; upstream results remain evidence."""
import copy
import unittest
from unittest.mock import patch

import frame_language
import windows_mcp_tools as mcp
from catalog_families import PreflightRefusal

RAW='RAW Русский 日本語 {unknown} $&'


class MCPMessageLanguages(unittest.TestCase):
    def setUp(self):
        previous=(mcp._call,mcp._allowed)
        self.addCleanup(lambda:mcp.configure(*previous))

    def test_owned_errors_use_frame_not_the_other_interfaces_language(self):
        import tool_report
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                for code,fields in mcp._MESSAGES.items():
                    spec={'code':code,'values':{key:RAW for key in fields},'detail':RAW}
                    result={'isError':True,'in_doubt':True,'helene_error':spec,
                            'content':[{'type':'text','text':'Interface-only text: '+ui}]}
                    original=copy.deepcopy(result)
                    with self.subTest(ui=ui,frame=language,code=code), \
                         patch.object(frame_language,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}), \
                         patch.object(tool_report,'note') as note:
                        calls=[]
                        mcp.configure(lambda name,args:(calls.append((name,args)) or result),lambda name,args:'')
                        text=mcp.invoke('windows_mcp_Click',{'loc':[1,2]})
                        self.assertEqual(len(calls),1)
                        self.assertEqual(text,frame_language.text('mcp.'+code,**spec['values'])+'\n'+RAW)
                        self.assertEqual(note.call_args.kwargs['outcome'],'in_doubt')
                    self.assertEqual(result,original)

    def test_upstream_text_structured_data_and_unrecognized_envelopes_remain_literal(self):
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):
                for spec in (None,{'code':['not_running']},{'code':'prompt.system'},
                             {'code':'missing_scopes','values':{}},
                             {'code':'not_running','values':{},'detail':{}},
                             {'code':'unconfirmed','cause':{'code':'UNKNOWN'}}):
                    mcp.configure(lambda name,args:{'helene_error':spec,'content':[{'type':'text','text':RAW}]},lambda name,args:'')
                    self.assertEqual(mcp.invoke('windows_mcp_Click',{'loc':[1,2]}),RAW)
                mcp.configure(lambda name,args:{'structuredContent':{'literal':RAW}},lambda name,args:'')
                self.assertIn(RAW,mcp.invoke('windows_mcp_Click',{'loc':[1,2]}))

    def test_permission_refusal_and_validation_never_send_a_request(self):
        for language in frame_language.LANGUAGES:
            with frame_language.use(language),patch.object(mcp,'_call') as request:
                mcp._allowed=lambda name,args:{'code':'missing_scopes','values':{'scopes':'computer.apps'}}
                with self.assertRaises(PreflightRefusal) as denied:mcp.invoke('windows_mcp_Click',{'loc':[1,2]})
                self.assertEqual(str(denied.exception),frame_language.text('mcp.missing_scopes',scopes='computer.apps'))
                with self.assertRaises(PreflightRefusal) as invalid:mcp.invoke('windows_mcp_Click',{'loc':'wrong'})
                self.assertIn('arguments.loc',str(invalid.exception))
                request.assert_not_called()

    def test_coded_nested_cause_uses_the_same_frame_language(self):
        spec={'code':'unconfirmed','values':{},'cause':{'code':'app_unavailable','values':{},'detail':RAW}}
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):
                self.assertEqual(mcp._format_message(spec),frame_language.text('mcp.unconfirmed')+'\n'
                                 +frame_language.text('mcp.app_unavailable')+'\n'+RAW)


if __name__=='__main__':unittest.main()
