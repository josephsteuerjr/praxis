"""Every hand is native in every turn; h.hand is a scripting tool, not a context saving (Egor 10.11)."""
import json
import os
import unittest
from unittest.mock import patch

import agent
import test_hand_bridge_1011 as bridge_tests

REMOVED = {'admit', 'computer_access'}  # Egor 10.11: leftovers of the old mini app


class AllHandsNative(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'PRAXIS_TOOLS_POINTERS':'off'})
        self.env.start();self.addCleanup(self.env.stop)

    def ctx(self,owner=True,known=True):
        return agent.ChannelContext(chat_id='42',room_id='42',is_dm=True,owner=owner,known=known)

    def test_every_catalogue_schema_is_offered_natively(self):
        for ctx in (self.ctx(),self.ctx(False),self.ctx(False,False)):
            self.assertEqual(agent.offered_tools_for(ctx),agent.catalog_tools_for(ctx))
        names={t['name'] for t in agent.offered_tools_for(self.ctx())}
        self.assertTrue({'python','edit','fs_read','shell','proposal','code_outline','manage_room',
                         'manage_identity','manage_desire','second_look','coding_review',
                         'computer','panic','freeze_chat','stay_silent','restart_self'}<=names)
        self.assertFalse({'call','describe'}&names)

    def test_every_speaker_has_all_rights_and_the_old_pair_is_gone(self):
        owner=agent.offered_tools_for(self.ctx())
        for other in (self.ctx(False),self.ctx(False,False)):
            self.assertEqual(owner,agent.offered_tools_for(other))
        self.assertFalse(REMOVED&{t['name'] for t in owner})
        self.assertFalse(REMOVED&set(agent.TOOL_IMPL))
        self.assertEqual([t['name'] for t in agent.PRAXIS_SELF_TOOLS],[t['name'] for t in agent.OWNER_TOOLS])

    def test_delivery_finish_and_provider_tools_are_present_in_every_context(self):
        window=agent.ChannelContext(chat_id=None,principal_id=agent.PRAXIS_SELF_PRINCIPAL,
                                    is_dm=True,owner=False,known=True)
        provider={'type':'web_search_20250305','name':'web_search'}
        with patch.object(agent.work_loop,'reply_hand_enabled',return_value=True), \
             patch.object(agent.work_loop,'active_for',return_value=True), \
             patch.object(agent,'_hosted_web_search_tool',return_value=provider):
            for ctx in (self.ctx(),self.ctx(False),window):
                names={t.get('name') for t in agent.offered_tools_for(ctx)}
                self.assertTrue({'end_turn','task_control','send_message','send_file','speak','web_search'}<=names)
                self.assertEqual('reply' in names,ctx.chat_id is not None)

    def test_contract_names_every_family_and_h_hand_as_a_scripting_tool(self):
        text=agent._hands_contract_text()
        for _,names in agent.HAND_GROUPS:
            for name in names:self.assertIn(name,text)
        self.assertIn("h.hand('имя'",text);self.assertIn("r['outcome']",text)
        self.assertIn("h.describe('имя')['content']",text)
        self.assertNotIn('Редкие руки',text);self.assertNotIn('Делегирование владельца',text)
        for owner in (True,False):
            prompt=agent.build_system_prompt(speaker='fixture',owner=owner)
            self.assertIn('h.hand(',prompt);self.assertIn('in_doubt',prompt)

    def test_soul_and_intentions_stay_native_for_every_audience(self):
        for owner in (True,False):
            names=[t['name'] for t in agent.offered_tools_for(self.ctx(owner))]
            for name in ('manage_identity','manage_desire'):
                self.assertEqual(names.count(name),1,(owner,name))

    def test_full_catalog_binding_and_explicit_override_remain_distinct(self):
        with agent._bind_tool_catalog(self.ctx()):
            self.assertIn('proposal',agent._catalog_for_turn())
        with agent._bind_tool_catalog(self.ctx(),[{'name':'fs_read'}]):
            self.assertEqual(set(agent._catalog_for_turn()),{'fs_read'})

    def test_script_calls_a_native_hand_and_reads_its_schema_via_child_ipc(self):
        fixture=bridge_tests.LiveHands('runTest');fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        (fixture.root/'sample.py').write_text('def visible_name():\n    return 1\n',encoding='utf-8')
        with patch.object(agent.workshop,'BASE',fixture.root), agent._bind_tool_catalog(fixture.channel):
            self.assertIn('code_outline',{t.get('name') for t in agent.offered_tools_for(fixture.channel)})
            schema=agent._catalog_for_turn()['code_outline']['input_schema']
            out=fixture.call("import json\ns,_=json.JSONDecoder().raw_decode(h.describe('code_outline')['content']);print('SCHEMA',json.dumps(s['input_schema'],sort_keys=True));r=h.hand('code_outline',path='sample.py');print(r['outcome'],r['content'])")
        self.assertIn('visible_name',str(out));self.assertIn(json.dumps(schema,sort_keys=True),str(out))
        self.assertEqual([r['tool'] for r in fixture.nested()],['describe','code_outline'])

    def test_restricted_override_cannot_gain_a_hand_through_a_script(self):
        fixture=bridge_tests.LiveHands('runTest');fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with agent._bind_tool_catalog(fixture.channel,[{'name':'python'}]):
            out=fixture.call("try:h.hand('proposal',action='list')\nexcept h.Refused:print('CATALOGUE-REFUSED')")
        self.assertIn('CATALOGUE-REFUSED',str(out));self.assertEqual(fixture.nested(),[])


if __name__=='__main__':unittest.main()
