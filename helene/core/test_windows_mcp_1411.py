import unittest
from unittest.mock import patch
import agent
import windows_mcp_tools as mcp
import frame_language
from catalog_families import PreflightRefusal


class NativeWindowsMCP(unittest.TestCase):
    def setUp(self):
        self.old=(mcp._call,mcp._allowed)
        self.addCleanup(lambda:mcp.configure(*self.old))

    def test_all_audiences_receive_the_same_native_schemas_and_k(self):
        expected=set(mcp.SCHEMAS)
        self.assertEqual(len(expected),21)
        self.assertTrue(expected <= set(agent.TOOL_IMPL))
        for owner in (True,False):
            ctx=agent.ChannelContext(owner=owner)
            self.assertTrue(expected <= {t.get('name') for t in agent.catalog_tools_for(ctx)})
            self.assertIn('Windows MCP',agent.build_system_prompt(speaker='fixture',owner=owner))

    def test_exact_typed_arguments_and_ordinary_result(self):
        calls=[]
        mcp.configure(lambda n,a:(calls.append((n,a)) or {'content':[{'type':'text','text':'exact reply'}]}), lambda n,a:'')
        args={'mode':'read','path':'C:\\fixture space\\read.txt','offset':2,'limit':3}
        self.assertEqual(mcp.invoke('windows_mcp_FileSystem',args),'exact reply')
        self.assertEqual(calls,[('FileSystem',args)])
        with self.assertRaises(PreflightRefusal):mcp.invoke('windows_mcp_Click',{'loc':'[1, 2]'})
        with self.assertRaises(PreflightRefusal):mcp.invoke('windows_mcp_Click',{'loc':[1,2,3]})
        with self.assertRaises(PreflightRefusal):mcp.invoke('windows_mcp_FileSystem',{'mode':'read'})
        self.assertEqual(len(calls),1)

    def test_actual_K_target_contract_follows_frame_for_all_ui_pairs_and_audiences(self):
        markers={'ru':'парном Windows','en':'paired Windows','fr':'associé sélectionné','zh':'配对 Windows'}
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                with patch.object(frame_language,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}):
                    prefix=frame_language.text('owned.agent.a70451c5d2a9',v0=1,v1=2,v2=3).split('\n',2)[1]
                    self.assertIn(markers[language],prefix)
                    self.assertIn('FileSystem(mode=read,path,offset,limit)',prefix)
                    for owner in (True,False):
                        prompt=agent.build_system_prompt(speaker='PRIVATE Русский 中文 {literal}',owner=owner)
                        self.assertIn(prefix,prompt)

    def test_permission_refusal_never_crosses_the_transport(self):
        with patch.object(mcp,'_call') as transport:
            mcp._allowed=lambda n,a:'fixture refused'
            with self.assertRaisesRegex(PreflightRefusal,'fixture refused'):
                mcp.invoke('windows_mcp_PowerShell',{'command':'echo fixture'})
            transport.assert_not_called()

    def test_uncertain_effect_is_not_replayed_and_retains_outcome(self):
        import tool_report
        with patch.object(mcp,'_call',return_value={'isError':True,'in_doubt':True,'content':[{'type':'text','text':'uncertain'}]}) as transport, patch.object(tool_report,'note') as note:
            mcp._allowed=lambda n,a:''
            self.assertEqual(mcp.invoke('windows_mcp_Click',{'loc':[1,2]}),'uncertain')
            transport.assert_called_once();self.assertEqual(note.call_args.kwargs['outcome'],'in_doubt')

    def test_scrape_does_not_silently_request_an_extra_model(self):
        with patch.object(mcp,'_call',return_value={'content':[]}) as transport:
            mcp._allowed=lambda n,a:''
            mcp.invoke('windows_mcp_Scrape',{'url':'https://example.org'})
            self.assertIs(transport.call_args.args[1]['use_sampling'],False)
            with self.assertRaises(PreflightRefusal):mcp.invoke('windows_mcp_Scrape',{'url':'https://example.org','use_sampling':True})

    def test_recovery_distinguishes_reads_from_effects(self):
        for name,args,effect in [('Snapshot',{},False),('Snapshot',{'save_path':'C:/image.png'},True),
            ('FileSystem',{'mode':'read'},False),('FileSystem',{'mode':'write'},True),
            ('PowerShell',{'command':'echo fixture'},True),('Clipboard',{'mode':'set'},True)]:
            self.assertEqual(agent._tool_has_side_effect('windows_mcp_'+name,args),effect)


if __name__=='__main__':unittest.main()
