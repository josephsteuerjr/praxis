"""Shared actual registered preference owner, scoped reset and conflict tests."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import window_preferences as wp
import python_app

class Appearance(unittest.TestCase):
    def test_actual_openai_strict_schema_preserves_sparse_entry_semantics(self):
        import ast
        source=Path(__file__).resolve().parents[2]/'helene/core/llm.py'
        node=next(n for n in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef) and n.name=='_openai_strict_schema')
        namespace={};exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
        agent=SimpleNamespace(TOOL_IMPL={},OWNER_TOOLS=[],_active_scope=lambda:'owner');wp.install(agent,self.root)
        schema=namespace['_openai_strict_schema'](agent.OWNER_TOOLS[0]['input_schema'])
        entries=schema['properties']['patch']['anyOf'][0]
        self.assertEqual(entries['type'],'array');self.assertEqual(entries['items']['required'],['key','value'])
        wp.change(self.root,{'helene.look':'{"uiScale":0.8}','frame.rail.width':'300'})
        tool=agent.TOOL_IMPL['window_appearance'];seen=json.loads(tool())['seen']
        result=json.loads(tool(action='patch',patch=[{'key':'frame.theme','value':'dark'}],seen=seen))
        self.assertTrue(result['ok']);self.assertEqual(wp.storage(self.root)['helene.look'],'{"uiScale":0.8}')
        self.assertEqual(wp.storage(self.root)['frame.rail.width'],'300')
        deleted=json.loads(tool(action='patch',patch=[{'key':'helene.look','value':None}],seen=result['seen']))
        self.assertTrue(deleted['ok']);self.assertNotIn('helene.look',wp.storage(self.root));self.assertEqual(wp.storage(self.root)['frame.rail.width'],'300')
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_registered_owner_tool_and_manual_writer_share_freshness(self):
        agent=SimpleNamespace(TOOL_IMPL={},OWNER_TOOLS=[],_active_scope=lambda:'owner')
        wp.install(agent,self.root);wp.install(agent,self.root)
        self.assertEqual(len(agent.OWNER_TOOLS),1)
        tool=agent.TOOL_IMPL['window_appearance'];first=json.loads(tool())
        manual=wp.change(self.root,{'helene.look':json.dumps({'uiScale':.85,'style':'clean'})},first['seen'])
        stale=json.loads(tool(action='patch',patch=[{'key':'frame.theme','value':'dark'}],seen=first['seen']))
        self.assertFalse(stale['ok']);self.assertEqual(stale['code'],'conflict')
        updated=json.loads(tool(action='patch',patch=[{'key':'frame.theme','value':'dark'}],seen=manual['seen']))
        self.assertTrue(updated['ok']);self.assertEqual(wp.storage(self.root)['frame.theme'],'dark')
        agent._active_scope=lambda:'public';self.assertEqual(json.loads(tool())['code'],'owner_only')
    def test_draft_write_does_not_invalidate_appearance_reset_or_erase_state(self):
        first=wp.read(self.root)
        wp.change(self.root,{'helene.look':'{"uiScale":0.8}','frame.theme':'dark'},first['seen'])
        before=wp.read(self.root);wp.change(self.root,{'draft.main':'private draft','settings.group':'look','frame.room':'room-id'})
        self.assertTrue(wp.reset(self.root,before['seen'])['ok'])
        self.assertEqual(wp.storage(self.root),{'draft.main':'private draft','settings.group':'look','frame.room':'room-id'})
    def test_invalid_scale_color_geometry_refuse_without_replacing_bytes(self):
        wp.change(self.root,{'draft.main':'kept'})
        path=self.root/'window-preferences.json';before=path.read_bytes()
        for look in ({'uiScale':False},{'uiScale':99},{'dayColor':'url(secret)'},{'density':'invalid'}):
            with self.assertRaises(ValueError):wp.change(self.root,{'helene.look':json.dumps(look)},wp.read(self.root)['seen'])
        with self.assertRaises(ValueError):wp.change(self.root,{'frame.rail.width':'nan'})
        self.assertEqual(path.read_bytes(),before)
    def test_application_commands_and_random_origins_persist(self):
        one=python_app.Application(self.root/'one',self.root);two=python_app.Application(self.root/'one',self.root)
        try:
            self.assertNotEqual(one.origin,two.origin)
            first=one.invoke('appearance_read',{})
            result=one.invoke('appearance_patch',{'patch':{'frame.theme':'dark'},'seen':first['seen']})
            self.assertTrue(result['ok']);self.assertEqual(two.preferences()['frame.theme'],'dark')
            self.assertIn('syncAppearance',two.bridge());self.assertIn(result['seen'],two.bridge())
            self.assertTrue(two.invoke('appearance_reset',{'seen':result['seen']})['ok'])
        finally:one.server.server_close();two.server.server_close()

if __name__=='__main__':unittest.main()
