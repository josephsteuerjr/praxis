"""Independent adversarial contracts for the Linux executor (server only)."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

SOURCE=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('reviewed_linux',SOURCE/'helene/core/body/crates/praxis-body/src/linux_desktop.py')
linux=importlib.util.module_from_spec(spec); spec.loader.exec_module(linux)

class FakeSpi:
    actions=[]
    def __init__(self, timeout):
        self.fn={'get_desktop':lambda _:1, 'accessible_get_child_count':lambda p,_:len(self.children(p)),
            'accessible_get_child_at_index':lambda p,i,_:self.children(p)[i],
            'accessible_get_process_id':lambda p,_:42, 'accessible_get_state_set':lambda p:p,
            'state_set_contains':lambda p,state:True, 'text_get_character_count':lambda p,_:0,
            'action_get_n_actions':lambda p,_:1, 'action_do_action':self.act,'accessible_clear_cache':lambda p:None}
    def children(self,p): return {1:[2],2:[3],3:[4,5]}.get(p,[])
    def act(self,*args): self.actions.append(args); return True
    def own(self,p): return p
    def close(self): pass
    def rect(self,p): return {'x':10,'y':20,'width':200,'height':100}
    def iface(self,p,name): return p if name in ('component','action') else None
    def text(self,name,p,*args):
        if name=='accessible_get_name': return 'Window' if p==3 else 'Save'
        if name=='accessible_get_role_name': return 'frame' if p==3 else 'push button'
        if name=='action_get_action_name': return 'click'
        return ''

class Contracts(unittest.TestCase):
    def test_window_manager_wait_failure_still_releases_input_and_closes_display(self):
        keys=linux.UnicodeKeys.__new__(linux.UnicodeKeys);order=[]
        keys.display=1;keys.borrowed={12:([0,0],[1,1])};keys.held={};keys.buttons=set();keys.nonce=None
        keys.sync=lambda *args:None;keys.restore_slots=lambda slots:order.append('restore')
        keys.release_held=lambda held,buttons:order.append('release')
        def wait(): order.append('wait');raise RuntimeError('WM blocked')
        keys.wait_window_manager=wait;keys.close_display=lambda display:order.append('close')
        with patch.object(linux.time,'sleep'),self.assertRaisesRegex(RuntimeError,'WM blocked'):keys.close()
        self.assertEqual(order,['restore','release','wait','close'])
        self.assertIsNone(keys.display)

    def test_unicode_mapping_batches_only_adjacent_owned_rows_of_equal_width(self):
        keys=linux.UnicodeKeys.__new__(linux.UnicodeKeys);keys.display=None;changes=[]
        keys.change=lambda display,first,width,data,count:changes.append((first,width,list(data),count))
        keys.change_rows({12:(1,1),13:(2,2),15:(3,3),16:(4,4,4,4)})
        self.assertEqual(changes,[(12,2,[1,1,2,2],2),(15,2,[3,3],1),(16,4,[4,4,4,4],1)])

    def test_activation_waits_for_a_new_window_without_forcing_its_initial_map(self):
        with patch.object(linux,'session',return_value=('x11','')), patch.object(linux.os,'geteuid',return_value=1000), \
             patch.object(linux,'property_of',side_effect=['WM_STATE: not found.','window state: Normal']), \
             patch.object(linux,'command',return_value='Map State: IsViewable'):
            result=linux.dispatch('desktop.window.activate',{'hwnd':16,'timeout_ms':1000},'.')
        self.assertEqual(result['foreground_hwnd'],'0x10')
        self.assertNotIn(unittest.mock.call('windowmap',16),linux.xdo.call_args_list)
        self.assertIn(unittest.mock.call('windowactivate',16),linux.xdo.call_args_list)

    def test_capture_clips_regions_and_reports_the_visible_intersection(self):
        notes=[]
        self.assertEqual(linux.capture_rectangle({'x':1270,'y':790,'width':100,'height':100},1280,800,notes),
                         {'x':1270,'y':790,'width':10,'height':10})
        self.assertTrue(notes)
        self.assertEqual(linux.capture_rectangle({'x':-10,'y':-20,'width':100,'height':100},1280,800,[]),
                         {'x':0,'y':0,'width':90,'height':80})
        for rect in ({'x':1280,'y':0,'width':100,'height':100},
                     {'x':0,'y':0,'width':True,'height':100},
                     {'x':0,'y':0,'width':0,'height':100}):
            with self.assertRaises(ValueError): linux.capture_rectangle(rect,1280,800,[])

    def setUp(self):
        FakeSpi.actions=[]
        self.addCleanup(patch.stopall)
        patch.object(linux,'pid_of',return_value=42).start()
        patch.object(linux,'geometry',return_value={'x':10,'y':20,'width':200,'height':100}).start()
        patch.object(linux,'check_stop').start()
        patch.object(linux,'xdo',side_effect=lambda *a: 'Window' if a[0]=='getwindowname' else '16').start()
        patch.object(linux,'Atspi',FakeSpi).start()
        patch.object(linux,'window_title',return_value='Window').start()
    def test_shared_default_window_limit_is_accepted(self):
        patch.object(linux,'command',return_value='_NET_CLIENT_LIST_STACKING(WINDOW): window id # 0x10').start()
        result=linux.windows({'limit':2048})
        self.assertEqual(result['returned'],1)
        self.assertTrue(result['items'][0]['foreground'])
    def test_exact_node_cap_discloses_unvisited_sibling(self):
        result=linux.accessible({'hwnd':16,'max_nodes':2},'desktop.window.read')
        self.assertTrue(result['truncated'])
        self.assertFalse(result['total_known'])
    def test_partial_tree_never_turns_duplicate_button_into_unique_action(self):
        with self.assertRaisesRegex(RuntimeError,'partial'):
            linux.accessible({'hwnd':16,'max_nodes':2,'select':{'name':'Save'},'do':'invoke'},'desktop.element.act')
        self.assertEqual(FakeSpi.actions,[])
    def test_blank_selector_does_not_widen_to_all_buttons(self):
        with self.assertRaisesRegex(ValueError,'blank'):
            linux.accessible({'hwnd':16,'select':{'name_contains':' ','nth':0},'do':'invoke'},'desktop.element.act')
        self.assertEqual(FakeSpi.actions,[])
    def test_action_fields_are_validated_before_reading_or_focus(self):
        with self.assertRaisesRegex(ValueError,'text'):
            linux.accessible({'hwnd':16,'select':{'name':'Save','nth':0},'do':'focus','text':'ignored'},'desktop.element.act')
    def test_read_limit_changes_are_explicit(self):
        result=linux.accessible({'hwnd':16,'max_nodes':20000},'desktop.window.read')
        self.assertTrue(any('max_nodes' in note for note in result['notes']))
    def test_find_returns_bounded_items_not_only_bounded_alias(self):
        result=linux.accessible({'hwnd':16,'select':{'name':'Save'},'limit':1},'desktop.element.find')
        self.assertEqual(result['matched'],2)
        self.assertEqual(len(result['items']),1)
    def test_process_offset_changes_page_and_unavailable_path_does_not_hide_process(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for pid in (10,20,30):
                folder=root/str(pid); folder.mkdir(); (folder/'comm').write_text('worker')
            original=linux.Path
            patch.object(linux,'Path',side_effect=lambda p:root if p=='/proc' else original(p)).start()
            result=linux._dispatch('os.process.list',{'offset':1,'limit':1},'.')
            self.assertEqual([p['pid'] for p in result['items']],[20])
            self.assertEqual(result['total'],3)
            self.assertTrue(result['has_more'])
    def test_unsupported_windows_process_filter_is_not_silently_ignored(self):
        with self.assertRaisesRegex(ValueError,'session_id'):
            linux._dispatch('os.process.list',{'session_id':1},'.')
    def test_unreadable_process_name_keeps_pid_and_discloses_incomplete_filter(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); folder=root/'123'; folder.mkdir()
            original=linux.Path
            patch.object(linux,'Path',side_effect=lambda p:root if p=='/proc' else original(p)).start()
            with patch.object(Path,'read_text',side_effect=PermissionError('denied')):
                result=linux._dispatch('os.process.list',{},'.')
                self.assertEqual(result['items'][0]['pid'],123)
                self.assertFalse(result['total_known']); self.assertEqual(result['names_unavailable'],1)
    def test_non_utf8_process_name_and_executable_path_do_not_crash_json_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); folder=root/'123'; folder.mkdir(); (folder/'comm').write_bytes(b'worker\xff\n')
            original=linux.Path
            patch.object(linux,'Path',side_effect=lambda p:root if p=='/proc' else original(p)).start()
            with patch.object(linux.os,'readlink',return_value='bad\udcff'):
                result=linux._dispatch('os.process.list',{},'.')
            self.assertEqual(result['items'][0]['pid'],123); self.assertEqual(result['items'][0]['path'],'')
            self.assertEqual(result['paths_unavailable'],1)
            json.dumps(result,ensure_ascii=False).encode('utf8')
    def test_unavailable_sibling_cannot_make_action_unique(self):
        original=FakeSpi.children
        with patch.object(FakeSpi,'children',lambda self,p:[4,None] if p==3 else original(self,p)):
            with self.assertRaisesRegex(RuntimeError,'partial'):
                linux.accessible({'hwnd':16,'select':{'name':'Save'},'do':'invoke'},'desktop.element.act')
        self.assertEqual(FakeSpi.actions,[])
    def test_selected_element_label_is_rechecked_before_native_action(self):
        original=FakeSpi.text; reads=[]
        def renamed(self,name,p,*a):
            if name=='accessible_get_name' and p==4:
                reads.append(p)
                if len(reads)>1: return 'Delete'
            return original(self,name,p,*a)
        with patch.object(FakeSpi,'text',renamed):
            with self.assertRaisesRegex(RuntimeError,'changed'):
                linux.accessible({'hwnd':16,'select':{'name':'Save','nth':0},'do':'invoke'},'desktop.element.act')
        self.assertEqual(FakeSpi.actions,[])
    def test_large_text_budget_is_partial_and_never_a_complete_selector_search(self):
        original=FakeSpi.text
        children=FakeSpi.children
        with patch.object(FakeSpi,'children',lambda self,p:list(range(4,84)) if p==3 else children(self,p)), patch.object(FakeSpi,'text',lambda self,name,p,*a: 'Save'+'x'*100000 if name=='accessible_get_name' and p!=3 else original(self,name,p,*a)):
            result=linux.accessible({'hwnd':16,'max_text_chars':100000},'desktop.window.read')
            self.assertTrue(result['truncated']); self.assertFalse(result['total_known'])
            self.assertIn('text_byte_budget',result['truncated_by'])
            self.assertLessEqual(len(json.dumps(result)),5000000)
    def test_foreign_display_recovery_does_not_destroy_the_other_journal(self):
        with tempfile.TemporaryDirectory() as td:
            file=Path(td)/'journal.json'
            value={'schema':'helene.x11.unicode.v1','nonce':'a'*32,'slots':{},'display':':17'}
            file.write_text(json.dumps(value))
            keys=linux.UnicodeKeys.__new__(linux.UnicodeKeys); keys.journal=file
            with patch.dict(os.environ,{'DISPLAY':':18'}):
                with self.assertRaisesRegex(RuntimeError,'another X11 display'): keys.recover()
            self.assertEqual(json.loads(file.read_text()),value)
    def test_invalid_unicode_is_refused_before_any_native_input(self):
        for text in ('\ud800','escape\x1b'):
            with self.assertRaises(ValueError): linux.input_plan({'events':[{'type':'text','text':text}]})
    def test_interrupted_batch_never_claims_earlier_input_did_not_happen(self):
        with patch.object(linux,'UnicodeKeys',return_value=SimpleNamespace(close=lambda:None)), patch.object(linux,'guard',side_effect=[16,16,RuntimeError('Owner emergency stop is active')]):
            with self.assertRaisesRegex(RuntimeError,'input may have partially occurred; completed_events=1') as raised:
                linux.perform({'events':[{'type':'mouse','x':1,'y':2},{'type':'pause','ms':1}]})
        self.assertIn('new request key',str(raised.exception))
        linux.xdo.assert_called_with('mousemove','--',1,2)
    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux readiness contract')
    def test_first_failed_linux_desktop_probe_keeps_explicit_unknown_readiness(self):
        sys.path.insert(0,str(SOURCE/'desk/localharness'))
        spec=importlib.util.spec_from_file_location('review_harness_body',SOURCE/'desk/localharness/body.py')
        harness=importlib.util.module_from_spec(spec); sys.modules[spec.name]=harness
        spec.loader.exec_module(harness)
        self.assertEqual(harness.STATE['platform'],'linux'); self.assertIsNone(harness.STATE['console'])
        with patch.object(harness,'call',return_value={'ok':False,'error':'desktop probe timeout'}):
            harness.Body.probe_desktop(SimpleNamespace(),timeout=.1)
        self.assertEqual(harness.STATE['platform'],'linux'); self.assertIsNone(harness.STATE['console'])
    def test_group_stop_gives_descendant_cleanup_grace_after_leader_exits(self):
        sys.path.insert(0,str(SOURCE/'desk/localharness'))
        spec=importlib.util.spec_from_file_location('review_group_body',SOURCE/'desk/localharness/body.py')
        harness=importlib.util.module_from_spec(spec); sys.modules[spec.name]=harness; spec.loader.exec_module(harness)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); marker=root/'released'; ready=root/'ready'
            child=root/'child.py'
            child.write_text('import signal,time,sys\nfrom pathlib import Path\ndef stop(*args):\n time.sleep(.15)\n Path(sys.argv[1]).write_text("released")\n raise SystemExit(0)\nsignal.signal(signal.SIGTERM,stop)\nPath(sys.argv[2]).write_text("ready")\ntime.sleep(20)\n')
            parent='import subprocess,sys,time; from pathlib import Path; subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2],sys.argv[3]]);\nwhile not Path(sys.argv[3]).exists(): time.sleep(.01)\nprint("READY",flush=True);time.sleep(20)'
            proc=subprocess.Popen([sys.executable,'-c',parent,str(child),str(marker),str(ready)],start_new_session=True,stdout=subprocess.PIPE,text=True)
            try:
                self.assertEqual(proc.stdout.readline().strip(),'READY')
                harness.kill_group(proc,grace=.7)
                self.assertEqual(marker.read_text(),'released')
                self.assertIsNotNone(proc.returncode)
            finally:
                if proc.returncode is None: harness.kill_group(proc,grace=.1)
                proc.stdout.close()

@unittest.skipUnless(sys.platform.startswith('linux'),'Linux shell hook simulation runs on the server')
class PackageHooks(unittest.TestCase):
    @staticmethod
    def unit(owner='reviewowner'): return 'enable --now '+'helene@'+owner+'.service'
    def run_hook(self,config,owner='reviewowner'):
        source=ast.parse((SOURCE/'desk/installer/build_linux.py').read_text(encoding='utf8'))
        hook=next(ast.literal_eval(n.value) for n in source.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='POSTINST' for t in n.targets))
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); home=root/'custom/home'; state=home/'.local/share/helene'; state.mkdir(parents=True)
            if config is not None: (state/'helene.json').write_text(config if isinstance(config,str) else json.dumps(config))
            system=root/'system'; system.mkdir(); binaries=root/'bin'; binaries.mkdir(); log=root/'commands'
            for name,text in {'systemctl':f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\n',
                    'getent':f'#!/bin/sh\nprintf "%s\\n" "{owner}:x:1000:1000:Owner:{home}:/bin/sh"\n',
                    'loginctl':'#!/bin/sh\nexit 0\n'}.items():
                file=binaries/name; file.write_text(text); file.chmod(0o755)
            script=hook.replace('/run/systemd/system',str(system)).replace('/etc/systemd/system/multi-user.target.wants',str(root/'wants'))
            script=script.replace('/opt/helene/runtime/bin/python3',sys.executable)
            env={**os.environ,'PATH':str(binaries)+':'+os.environ['PATH'],'SUDO_USER':owner,'PKEXEC_UID':''}
            subprocess.run(['sh','-c',script],env=env,check=True,timeout=10,capture_output=True)
            return log.read_text().splitlines()
    def test_custom_passwd_home_preserves_explicit_service_refusal(self):
        self.assertNotIn(self.unit(),self.run_hook({'installed':{'service':False}}))
    def test_fresh_custom_home_starts_intended_owner(self):
        self.assertIn(self.unit(),self.run_hook(None))
    def test_unrelated_false_service_fragment_does_not_disable_install(self):
        self.assertIn(self.unit(),self.run_hook({'extension':{'service':False}}))
    def test_invalid_config_does_not_guess_owner_consent(self):
        self.assertNotIn(self.unit(),self.run_hook('{broken'))
    def test_root_is_not_turned_into_a_desktop_owner_service(self):
        self.assertNotIn(self.unit('root'),self.run_hook(None,owner='root'))

if __name__=='__main__': unittest.main(verbosity=2)
