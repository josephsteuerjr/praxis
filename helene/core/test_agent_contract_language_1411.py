"""Actual prompt-builder contracts follow frame language and transport branch."""
import json
import os
from unittest.mock import patch

import agent
import frame_language
import frame_trace
from test_frame_trace import FrameBase


class ContractLanguages(FrameBase):
    def test_all_ui_frame_pairs_keep_audience_and_reply_transport_contracts(self):
        config=self.tmp/'config.json'
        env={'HELENE_CONFIG':str(config),'HELENE_SKILLS_KIT_ROOT':'',
             'PRAXIS_FRAME_HEAD_STABLE':'off','PRAXIS_RUNS_HEAD_STABLE':'off',
             'PRAXIS_FRAME_EPOCH':'off','PRAXIS_TOOLS_POINTERS':'off'}
        original=frame_trace.mark
        marked={}
        def mark(name,zone,kind,text,**kwargs):
            marked[name]=text
            return original(name,zone,kind,text,**kwargs)
        cases=[(self.owner_dm(),'owned.agent.267672350827'),
               (self.own_run(),'owned.agent.87627492bcff'),
               (self.family_dm(),'owned.agent.e9fc05d4e3cb'),
               (self.unknown_group(),'owned.agent.64c81f5c3a98')]
        stable={}
        with patch.dict(os.environ,env),patch.object(frame_trace,'mark',side_effect=mark):
            for ui in frame_language.LANGUAGES:
                for lang in frame_language.LANGUAGES:
                    config.write_text(json.dumps({'lang':ui,'agent':{'frame_language':lang}}),encoding='utf-8')
                    for ctx,expected in cases:
                        for reply_enabled in (False,True):
                            marked.clear()
                            with patch.object(agent.work_loop,'reply_hand_enabled',return_value=reply_enabled),frame_trace.capture():
                                persona,dynamic,evidence=agent._build_prompt_parts(speaker='PRIVATE Русский 中文',ctx=ctx)
                            self.assertIn(frame_language.text(expected),dynamic)
                            self.assertEqual(marked['contract.base'],frame_language.text('owned.agent.818295802af1'))
                            self.assertEqual(marked['contract.living_documents'],frame_language.text('owned.agent.14b237d46b0c'))
                            check='owned.agent.847631b3dce8' if reply_enabled else 'owned.agent.411ba0dc732c'
                            self.assertEqual(marked['contract.checkable_in_chat'],frame_language.text(check))
                            if ctx.owner_audience:
                                self.assertEqual(marked['contract.owner_tools'],frame_language.text('contract.owner_tools'))
                                self.assertEqual(marked['contract.appetite'],frame_language.text('contract.appetite'))
                            # Only owned contract sections are compared here: unchanged
                            # personal documents and live state have separate provenance.
                            contracts={name:text for name,text in marked.items() if name in {
                                'contract.base','contract.living_documents','contract.checkable_in_chat',
                                'contract.owner_tools','contract.appetite','contract.family_audience',
                                'contract.unknown_authority','state.owner_place'}}
                            self.assertTrue(all('Yegor' not in text for text in contracts.values()))
                            key=(lang,ctx.chat_id,reply_enabled)
                            if key in stable:self.assertEqual(stable[key],contracts)
                            stable[key]=dict(contracts)
                            self.assertIn('Я — Praxis.',persona,'authored persona was translated')
        self.assertEqual(len(stable),32)
