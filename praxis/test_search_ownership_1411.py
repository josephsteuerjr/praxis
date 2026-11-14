"""Real hanging read/regex children, same ownership for parent and nested hand."""
import concurrent.futures
import contextvars
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import forge
import forge_search_step
import hands
import run_context
import search_execution
import step_control
import test_step_control_1311 as fixture
import test_hand_bridge_1011 as bridge_fixture


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux search ownership')
class OwnedSearch(unittest.TestCase):
    setUp = fixture.ForceControl.setUp
    stop_all = fixture.ForceControl.stop_all
    cancel = fixture.ForceControl.cancel
    wait_for = fixture.ForceControl.wait_for

    def walk(self):
        return hands.walk('^(a+)+$', '*.txt', self.base, cap=60, scan_cap=5000,
            seconds=0, skip_runs=False, base=self.base)

    def search_process(self):
        with self.registry.lock:ps=list(self.registry.handles.get(self.ctx.run_id,()))
        return next((p for p in ps if type(p).__name__=='CommandProcess'),None)

    def hung_search(self,action):
        (self.base/'hang.txt').write_text('a'*25000+'!')
        with patch.dict(os.environ,{'PRAXIS_TEST':'','PRAXIS_BASE':str(self.base)}), run_context.bind_run(self.ctx):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future=executor.submit(contextvars.copy_context().run,action)
                self.wait_for(lambda:self.search_process() is not None)
                process=self.search_process()
                time.sleep(.15)
                self.assertFalse(future.done())
                self.cancel()
                with self.assertRaises(step_control.StepCancelled):future.result(2)
                self.assertIsNotNone(process.poll())
                receipt=self.base/'memory/.control/run-cancel'/(self.ctx.run_id+'.receipt')
                self.wait_for(lambda:receipt.exists() and json.loads(receipt.read_text())['ok'])

    def test_home_regex_child_is_owned_and_reaped(self):self.hung_search(self.walk)

    def test_forge_regex_fallback_child_is_owned_and_reaped(self):
        task={'id':'code-fixture','scope':'local','root':str(self.base)}
        with patch.object(forge,'_task_root',return_value=(task,self.base,'')), patch.object(forge,'_remote_client',return_value=None), patch.object(forge.shutil,'which',return_value=None):
            self.hung_search(lambda:forge.inspect('code-fixture','search',path='.',query='^(a+)+$',glob='*.txt'))

    def test_home_literal_and_walk_results_match_previous_path(self):
        (self.base/'normal.txt').write_text('needle α\nother\nneedle two\n',encoding='utf-8')
        def action():return hands.walk('needle','*.txt',self.base,cap=60,scan_cap=5000,seconds=4,skip_runs=False,base=self.base)
        expected=action()
        with patch.dict(os.environ,{'PRAXIS_TEST':'','PRAXIS_BASE':str(self.base)}),run_context.bind_run(self.ctx):actual=action()
        self.assertEqual(actual,expected)

    def test_forge_rg_and_fallback_exact_projection(self):
        (self.base/'normal.txt').write_text('needle α\nother\nneedle two\n',encoding='utf-8')
        task={'id':'code-fixture','scope':'local','root':str(self.base)}
        for rg in (None,shutil.which('rg')):
            with patch.object(forge,'_task_root',return_value=(task,self.base,'')), patch.object(forge,'_remote_client',return_value=None), patch.object(forge.shutil,'which',return_value=rg):
                expected=forge.inspect('code-fixture','search',query='needle',glob='*.txt')
                with patch.dict(os.environ,{'PRAXIS_TEST':'','PRAXIS_BASE':str(self.base)}),run_context.bind_run(self.ctx):
                    actual=forge.inspect('code-fixture','search',query='needle',glob='*.txt')
                self.assertEqual(str(actual),str(expected))
                self.assertEqual(actual.outcome,expected.outcome)

    def test_cancel_before_launch_has_no_search_effect(self):
        self.cancel()
        with patch.dict(os.environ,{'PRAXIS_TEST':'','PRAXIS_BASE':str(self.base)}),run_context.bind_run(self.ctx):
            with self.assertRaises(step_control.StepCancelled):self.walk()
            self.assertIsNone(self.search_process())


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux nested search ownership')
class NestedSearch(unittest.TestCase):
    setUp=bridge_fixture.LiveHands.setUp
    echo=bridge_fixture.LiveHands.echo
    call=bridge_fixture.LiveHands.call
    nested=bridge_fixture.LiveHands.nested

    def test_script_exit_does_not_hide_parent_search_and_reset_settles_both(self):
        import agent
        (self.root/'hang.txt').write_text('a'*25000+'!')
        def blocking(value=''):
            (self.root/'started').write_text('started')
            return str(hands.walk('^(a+)+$','*.txt',self.root,cap=60,scan_cap=5000,seconds=0,skip_runs=False,base=self.root))
        registry=step_control.start(self.root);self.addCleanup(registry.close)
        receipt=self.root/'memory/.control/run-cancel'/(self.current.run_id+'.receipt')
        with patch.dict(os.environ,{'PRAXIS_TEST':'','PRAXIS_BASE':str(self.root)}),patch.dict(agent.TOOL_IMPL,{'fixture':blocking}),patch.object(agent,'TOOL_CEILING_SEC',6):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future=executor.submit(contextvars.copy_context().run,self.call,bridge_fixture.LiveHands.LEAVE_EARLY)
                deadline=time.monotonic()+5
                selected=None
                while time.monotonic()<deadline:
                    with registry.lock:ps=list(registry.handles.get(self.current.run_id,()))
                    for process in ps:
                        directory=getattr(process,'_custody',None)
                        if directory:
                            try:request=json.loads((directory/'request.json').read_text())
                            except FileNotFoundError:continue
                            if any(str(a).endswith('hands_search.py') for a in request['command']):selected=process
                    if selected:break
                    time.sleep(.02)
                self.assertIsNotNone(selected,'nested search must have its own parent-owned handle')
                self.assertFalse(future.done())
                step_control._write(receipt.with_suffix('.json'),{'id':'nested-search-reset','run_id':self.current.run_id,'action':'cancel'})
                with self.assertRaises(agent.RunStopped):future.result(3)
                deadline=time.monotonic()+3
                while time.monotonic()<deadline and not (receipt.exists() and json.loads(receipt.read_text())['ok']):time.sleep(.02)
                self.assertTrue(json.loads(receipt.read_text())['ok'])
                self.assertIsNotNone(selected.poll())
                manifest=self.manager.manifest(self.current.run_id)
                # Preserve unresolved original intents despite proven death.
                self.assertEqual(manifest['status'],'paused')
                self.assertEqual(manifest['control']['action'],'cancel')
                self.assertTrue(self.manager.outstanding_tools(self.current.run_id))
                self.assertTrue(self.nested())


if __name__=='__main__':unittest.main()
