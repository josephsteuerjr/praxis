"""The real binary Python entry selects exactly one first-start/ordinary window."""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import windows_start
import first_start_window
from localharness import first_start


class Entry(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parent/'not-a-real-installed-programme'
        self.places=first_start.Places(self.root/'profile',(),(),self.root/'Downloads')
        environment=patch.dict(os.environ);environment.start();self.addCleanup(environment.stop)
    def test_completed_receipt_with_agent_opens_ordinary_window_without_prepare(self):
        with patch.object(first_start,'ready',return_value=True),patch.object(first_start,'ensure_config'), \
             patch.object(first_start,'agent_exists',return_value=True),patch.object(first_start,'prepare') as prepare, \
             patch.object(windows_start,'ordinary',return_value=17) as ordinary:
            self.assertEqual(windows_start.main(['--root',str(self.root)],places=self.places),17)
        prepare.assert_not_called();ordinary.assert_called_once_with(self.root,[])
    def test_new_archive_or_missing_agent_opens_only_minimal_first_start_window(self):
        for ready in (False,True):
            with self.subTest(ready=ready),patch.object(first_start,'ready',return_value=ready), \
                 patch.object(first_start,'ensure_config'),patch.object(first_start,'agent_exists',return_value=False), \
                 patch('first_start_window.main',return_value=23) as window,patch.object(windows_start,'ordinary') as ordinary:
                self.assertEqual(windows_start.main(['--root',str(self.root)],places=self.places),23)
                window.assert_called_once_with(self.root,places=self.places);ordinary.assert_not_called()
    def test_stop_and_existing_admitted_startup_never_launch_cleanup(self):
        for flag,args in (('--panic',[]),('--migration',['exact-id']),('--session-job',['exact-job'])):
            with self.subTest(flag=flag),patch.object(first_start,'ready',return_value=False),patch.object(first_start,'prepare') as prepare, \
                 patch.object(windows_start,'ordinary',return_value=0) as ordinary:
                self.assertEqual(windows_start.main(['--root',str(self.root),flag,*args],places=self.places),0)
                ordinary.assert_called_once_with(self.root,[flag,*args]);prepare.assert_not_called()


class FirstViewCleanup(unittest.TestCase):
    def run_view(self,error=None):
        """Use real TemporaryDirectory cleanup with a portable share-denial fake."""
        original_temp=tempfile.TemporaryDirectory;original_unlink=os.unlink
        with original_temp() as private:
            root=Path(private);control=root/'control'/'window.json';control.parent.mkdir()
            app=MagicMock(worker=None,origin='http://private.invalid',control_file=control)
            app.serve.side_effect=lambda:control.write_text('{}')
            app.close.side_effect=lambda:control.unlink()
            view=types.SimpleNamespace(settings={},screens=[object()],create_window=MagicMock(return_value=MagicMock()))
            held=[True];denied=[];caches=[]
            def unlink(path,*args,**kwargs):
                if held[0] and Path(path).name=='held.cache':
                    denied.append(str(path));raise PermissionError('private simulated WebView share denial')
                return original_unlink(path,*args,**kwargs)
            def start(**kwargs):
                self.assertTrue(kwargs['private_mode'])
                cache=Path(kwargs['storage_path']);caches.append(cache)
                (cache/'held.cache').write_bytes(b'private temporary cache')
                if error is not None:raise error
            view.start=start
            def temporary(**kwargs):return original_temp(**kwargs,dir=root)
            try:
                with patch.dict(sys.modules,{'webview':view}),patch.object(first_start_window,'FirstStartWindow',return_value=app), \
                     patch.object(first_start_window,'window_geometry',return_value={'width':800,'height':800}), \
                     patch.object(first_start_window.tempfile,'TemporaryDirectory',side_effect=temporary),patch.object(os,'unlink',side_effect=unlink):
                    if error is None:self.assertEqual(first_start_window.main(root,places=object()),0)
                    else:
                        with self.assertRaises(type(error)) as raised:first_start_window.main(root,places=object())
                        self.assertIs(raised.exception,error)
                app.close.assert_called_once_with();self.assertFalse(control.exists())
                self.assertTrue(denied,'the real stdlib cleanup must encounter the held cache')
                self.assertEqual(len(caches),1);self.assertTrue(caches[0].is_dir())
                self.assertTrue((caches[0]/'held.cache').is_file())
            finally:
                held[0]=False
                for cache in caches:
                    if cache.exists():shutil.rmtree(cache)
            self.assertFalse(any(cache.exists() for cache in caches))

    def test_busy_first_view_cache_does_not_turn_successful_close_into_startup_error(self):
        self.run_view()

    def test_genuine_view_error_propagates_while_app_closes_and_busy_cache_is_best_effort(self):
        self.run_view(ValueError('genuine view error'))


if __name__=='__main__':unittest.main()
