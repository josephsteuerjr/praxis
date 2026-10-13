"""Pure Linux edition contracts; run on the isolated server with other checks."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / 'localharness'))
import computer_steps
import package_notice
import update_inspection

spec = importlib.util.spec_from_file_location('linux_desktop', DESK.parent / 'helene/core/body/crates/praxis-body/src/linux_desktop.py')
desktop = importlib.util.module_from_spec(spec); spec.loader.exec_module(desktop)


class LinuxContracts(unittest.TestCase):
    def test_xkb_group_padding_does_not_admit_an_owner_replacement(self):
        self.assertTrue(desktop.UnicodeKeys.matches((42,42,0,0),(42,42)))
        self.assertFalse(desktop.UnicodeKeys.matches((42,43),(42,42)))
        self.assertFalse(desktop.UnicodeKeys.matches((0,42),(42,42)))

    def test_tree_filters_retain_only_matches_and_their_ancestors(self):
        def row(name, children=()):
            return {'name':name,'value':'','state':{'visible':True},'children':list(children)}
        root=row('dialog',[row('group',[row('desired')]),row('other')])
        kept,count=desktop.filter_tree(root,{'text_contains':'desired'})
        self.assertEqual(count,3)
        self.assertEqual(kept['children'][0]['children'][0]['name'],'desired')
        self.assertEqual(len(kept['children']),1)
        self.assertEqual(desktop.filter_tree(root,{'text_contains':'missing'}),(None,0))

    def test_wayland_including_xwayland_and_service_refuse_before_tools(self):
        for env, expected in (({'WAYLAND_DISPLAY':'wayland-0','DISPLAY':':stand'},'wayland'),
                              ({'XDG_SESSION_TYPE':'wayland','DISPLAY':':stand'},'wayland'),
                              ({'DISPLAY':':stand','HELENE_SERVICE':'1'},'headless'), ({},'headless')):
            with self.subTest(env=env), patch.dict(desktop.os.environ, env, clear=True), patch.object(desktop, 'xdo') as xdo:
                self.assertEqual(desktop.session()[0], expected)
                with self.assertRaises(RuntimeError): desktop.dispatch('desktop.input.perform', {'events':[]}, '.')
                xdo.assert_not_called()

    def test_complete_input_batch_validation_precedes_any_dispatch(self):
        batches = [[{'type':'key','key':'End'}, {'type':'key','key':'--window'}],
                   [{'type':'mouse','x':1}], [{'type':'pause','ms':True}], [{'type':'text','text':'valid'}, {'type':'bogus'}]]
        for events in batches:
            with patch.object(desktop, 'xdo') as xdo:
                with self.assertRaises(ValueError): desktop.perform({'events':events})
                xdo.assert_not_called()

    def test_common_v2_events_validate_as_a_whole_before_input(self):
        events=[{'type':'hotkey','keys':['ctrl','a']}, {'type':'click','count':2},
                {'type':'wheel','delta':-120,'horizontal':True}, {'type':'mouse','x':2,'y':-2,'relative':True},
                {'type':'mouse_down','button':'left'}, {'type':'mouse_up','button':'left'},
                {'type':'drag','x':1,'y':2,'to_x':4,'to_y':5,'steps':2}]
        self.assertEqual(desktop.input_plan({'events':events})[0],events)
        for invalid in ({'type':'drag','x':1,'y':2,'to_x':4,'to_y':5,'steps':0},
                        {'type':'hotkey','keys':['ctrl','--window']}, {'type':'wheel','delta':True}):
            with patch.object(desktop,'xdo') as xdo:
                with self.assertRaises(ValueError): desktop.perform({'events':events+[invalid]})
                xdo.assert_not_called()

    def test_missing_foreground_only_allows_unbound_desktop_input(self):
        with patch.object(desktop,'check_stop'), patch.object(desktop,'xdo',side_effect=RuntimeError('absent')):
            self.assertIsNone(desktop.guard({}))
            with self.assertRaisesRegex(RuntimeError,'target-bound'):
                desktop.guard({'expected_foreground':'0x42'})

    def test_focus_change_aborts_text_without_retrying_and_releases_held_key(self):
        calls=[]; checks=[]
        sent=[]
        keys=types.SimpleNamespace(send=sent.append,close=lambda:None,check_focus=lambda target:None,
                                  record_key=lambda key:True)
        def guard(_):
            checks.append(1)
            if len(sent) >= 2: raise RuntimeError('changed')
        with patch.object(desktop, 'UnicodeKeys',return_value=keys), patch.object(desktop, 'guard', side_effect=guard), patch.object(desktop, 'xdo', side_effect=lambda *args: calls.append(args)):
            with self.assertRaises(RuntimeError):
                desktop.perform({'events':[{'type':'key','key':'ctrl','action':'down'}, {'type':'text','text':'abc'}]})
        self.assertEqual(sent,['a','b'])
        self.assertEqual(calls[-1], ('keyup','Control_L'))

    def test_windows_read_backend_on_linux_refuses_before_input(self):
        calls=[]
        agent=types.SimpleNamespace(TOOL_IMPL={'computer':lambda **args:'original'}, BASE_TOOLS=[],
                                    _computer_actor=lambda:'owner', _computer_allowed=lambda scope:True)
        computer_steps.install(agent, lambda *args, **kwargs: calls.append(args), platform='linux')
        result=agent.TOOL_IMPL['computer'](action='step',hwnd='0xabc',idempotency_key='fixture',events=[{'type':'key','key':'End'}],read={'backend':'win32'})
        self.assertIn('недоступен', result); self.assertEqual(calls, [])

    def test_native_linux_composition_rejects_windows_read_before_input(self):
        with patch.dict(desktop.os.environ, {'DISPLAY':':stand'}, clear=True), patch.object(desktop.os, 'geteuid', return_value=1000, create=True), patch.object(desktop,'xdo') as xdo:
            with self.assertRaises(ValueError):
                desktop.dispatch('desktop.compose.validate', {'input':{'events':[{'type':'key','key':'End'}]},'read':{'backend':'win32'}}, '.')
            xdo.assert_not_called()

    def test_linux_package_repair_and_inspection_do_not_invent_previous_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary); program=base/'program'; tree=base/'tree'; program.mkdir()
            state=tree/'memory/.state'; state.mkdir(parents=True)
            (state/'born.json').write_text('{}'); (state/'package-version.json').write_text('{"bad":true}')
            current = json.loads((DESK/'resources/update-inspection.json').read_text(encoding='utf8'))['version']
            (program/'helene-build.json').write_text(json.dumps({'version':current,'platform':'linux','complete':True}))
            package_notice.observe(tree, program, {'installed':'broken'})
            receipt=package_notice.pending(tree); self.assertEqual(receipt['from_version'], '')
            self.assertEqual(receipt['platform'],'linux')
            request=update_inspection.ensure(tree, receipt)
            self.assertTrue(any('DEB/RPM' in goal['title'] for goal in request['goals']))
            package_notice.observe(tree, program, {})
            self.assertEqual(receipt['id'], package_notice.pending(tree)['id'])


if __name__ == '__main__': unittest.main(verbosity=2)
