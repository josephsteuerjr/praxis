"""Real preparation refusals preserve custody, diagnostics and native UI recipes."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

DESK=Path(__file__).resolve().parents[1];sys.path[:0]=[str(DESK),str(DESK/'localharness')]
import boot
import first_start_window
from localharness import ui_language as words

RAW='Literal Русский 中文 {path} </script>'


class LayoutLanguage(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.tree=self.root/'data';self.tree.mkdir();self.config=self.root/'helene.json'

    def choose(self,ui,frame):self.config.write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf8')

    def test_real_explicit_seed_refuses_without_replacing_an_unreadable_choice(self):
        seed=self.tree/'memory/.state/soul-seed.md';seed.parent.mkdir(parents=True);seed.write_text(RAW,encoding='utf8');before=seed.read_bytes()
        for ui in ('ru','en','fr','zh'):
            for frame in ('ru','en','fr','zh'):
                self.choose(ui,frame)
                with patch.object(boot,'read_config_text',side_effect=PermissionError(RAW)):
                    with self.assertRaises(boot.LayoutError) as caught:boot.initial_soul_seed(self.config,self.tree)
                error=caught.exception;self.assertIsInstance(error,RuntimeError)
                self.assertIsInstance(error,words.NativeRuntimeError)
                self.assertIn(RAW,str(error));self.assertEqual(words.native_error(error)['code'],'layout_seed')
                self.assertEqual(words.error_text(error,self.root),words.text('native.app.error.layout_seed',ui,{'path':str(seed),'detail':RAW}))
                self.assertEqual(seed.read_bytes(),before);self.assertFalse((self.tree/'soul').exists())

    def test_actual_birth_and_transfer_preflight_refusals_keep_data_and_claim_records(self):
        marker=self.root/'birth.json';marker.write_text(json.dumps({'schema':1,'tree':str(self.tree),'state':'started'}),encoding='utf8')
        before=marker.read_bytes()
        with self.assertRaises(boot.LayoutError) as caught:boot.ensure_layout(self.tree,{},birth_receipt=marker)
        self.assertEqual(words.native_error(caught.exception)['code'],'layout_birth_incomplete');self.assertEqual(marker.read_bytes(),before)
        transfer=self.tree/'memory/.state/server-transfer.json';transfer.parent.mkdir(parents=True,exist_ok=True);transfer.write_text('{',encoding='utf8')
        with self.assertRaises(boot.LayoutError) as caught:boot.claim_tree(self.tree)
        self.assertEqual(words.native_error(caught.exception)['code'],'layout_transfer_unreadable');self.assertEqual(transfer.read_text(),'{')
        self.assertFalse(boot.tree_lock_path(self.tree).exists())

    def test_first_window_worker_preserves_owned_recipe_and_raw_diagnostic_for_live_ui_change(self):
        self.choose('fr','zh');error=boot.NativeLayoutError('layout_marker_tree','LEGACY '+RAW,path=RAW)
        flow=SimpleNamespace(prepare=Mock(side_effect=error));app=first_start_window.FirstStartWindow(self.root,SimpleNamespace(data=self.tree),flow)
        self.addCleanup(app.server.server_close)
        with patch('localharness.startup_diagnostics.record') as record:app.prepare()
        value=app.status();self.assertEqual(value['error'],'LEGACY '+RAW)
        self.assertEqual(value['native_error'],words.native_error(error));record.assert_called_once_with(self.root,self.tree,error)
        self.assertEqual(words.bootstrap_data(self.root)['native_errors']['layout_marker_tree'],['path'])
        with patch.object(app,'flow',SimpleNamespace(prepare=Mock(side_effect=RuntimeError(RAW)))):
            with patch('localharness.startup_diagnostics.record'):app.prepare()
        self.assertNotIn('native_error',app.status());self.assertEqual(app.status()['error'],RAW)

    def test_first_open_refuses_before_launch_with_owned_category(self):
        self.choose('en','ru');app=first_start_window.FirstStartWindow(self.root,None,SimpleNamespace(ready=lambda *args:False))
        self.addCleanup(app.server.server_close)
        with patch.object(first_start_window.subprocess,'Popen',side_effect=AssertionError('launch')) as launch:
            with self.assertRaises(ValueError) as caught:app.invoke('first_open',{})
        self.assertEqual(words.native_error(caught.exception)['code'],'first_ready');launch.assert_not_called()
        self.assertFalse(app.opening)


if __name__=='__main__':unittest.main()
