"""Native MCP captions follow UI, independently of frame; no server is started."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import ui_language
from localharness import windows_mcp_words as words, windows_mcp_runtime as runtime
from localharness import windows_mcp_client as client, windows_mcp_access as access
from localharness import windows_mcp_component as component

RAW='RAW Русский 日本語 {unknown} $&'


class MCPInterfaceLanguage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='native-mcp-caption-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.owner=SimpleNamespace(root=self.root,lock=threading.RLock(),stop=threading.Event())

    def config(self,ui,frame):
        cfg={'lang':ui,'agent':{'frame_language':frame},'computer':{'enabled':False}}
        (self.root/'helene.json').write_text(json.dumps(cfg),encoding='utf-8')
        return cfg

    def test_all_owned_code_parameters_match_in_every_interface_catalog(self):
        keys={key for key in ui_language._catalog('ru') if key.startswith('native.mcp.')}
        self.assertEqual(len(keys),24)
        for ui in ('ru','en','fr','zh'):
            self.config(ui,'zh' if ui!='zh' else 'ru')
            catalog=ui_language._catalog(ui)
            self.assertEqual({key for key in catalog if key.startswith('native.mcp.')},keys)
            for key in keys:
                fields=set(ui_language.PARAMETER.findall(ui_language._catalog('ru')[key]))
                self.assertEqual(set(ui_language.PARAMETER.findall(catalog[key])),fields)
                values={field:RAW for field in fields}
                self.assertEqual(words.text(self.root,key.removeprefix('native.mcp.'),**values),
                                 ui_language.text(key,ui,values))

    def test_current_status_component_and_refusal_follow_all16_independent_pairs(self):
        manager=runtime.Manager(self.owner);manager.state='error'
        manager.failure={'code':'start_exited','values':{'log':RAW},'detail':RAW}
        for ui in ('ru','en','fr','zh'):
            for frame in ('ru','en','fr','zh'):
                with self.subTest(ui=ui,frame=frame):
                    cfg=self.config(ui,frame)
                    expected=ui_language.text('native.mcp.start_exited',ui,{'log':RAW})+'\n'+RAW
                    self.assertEqual(manager.status()['error'],expected)
                    self.assertEqual(access.refusal(cfg,'Click',{'loc':[1,2]}),
                                     ui_language.text('native.mcp.computer_disabled',ui,{}))
                    with patch('localharness.python_platform.runtime_target',return_value='linux-x86_64'), \
                         patch.object(component,'directory',return_value=self.root/'not-installed'):
                        self.assertEqual(component.status(self.root)['error'],
                                         ui_language.text('native.mcp.unsupported_platform',ui,{}))
        self.assertEqual(manager.failure['detail'],RAW)

    def test_call_timeout_is_one_attempt_and_carries_coded_unknown_outcome(self):
        self.config('fr','zh');manager=runtime.Manager(self.owner)
        manager.child=SimpleNamespace(handle=object(),poll=lambda:None)
        manager.state='running';manager.tools={'Click':{}}
        with patch.object(runtime,'rpc',side_effect=TimeoutError(RAW)) as request:
            result=manager.call('Click',{'loc':[1,2]})
        request.assert_called_once()
        self.assertTrue(result['isError']);self.assertTrue(result['in_doubt'])
        self.assertEqual(result['helene_error'],{'code':'unconfirmed','values':{},'detail':RAW})
        self.assertEqual(result['content'][0]['text'],ui_language.text('native.mcp.unconfirmed','fr',{})+'\n'+RAW)

    def test_dead_app_cause_stays_coded_for_the_model_to_choose_its_language(self):
        self.config('fr','zh')
        with patch.object(client,'invoke',side_effect=words.Error(self.root,'app_unavailable')) as request:
            result=client.call(self.root,self.root/'agent','Click',{'loc':[1,2]})
        request.assert_called_once()
        self.assertTrue(result['in_doubt'])
        self.assertEqual(result['helene_error']['cause']['code'],'app_unavailable')
        self.assertIn(ui_language.text('native.mcp.app_unavailable','fr',{}),result['content'][0]['text'])


if __name__=='__main__':unittest.main()
