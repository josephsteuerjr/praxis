"""Application-owned native failures preserve exception classes and effect gates."""
import json
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
import urllib.error
import urllib.request

DESK=Path(__file__).resolve().parents[1];sys.path[:0]=[str(DESK),str(DESK/'localharness')]
import python_app
from localharness import ui_language as words,program_commands
RAW='Literal Русский 中文 <script>{path}</script>'


class NativeAppErrors(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)

    def test_exact_owned_errors_render_on16_ui_frame_axes_and_keep_wire_values(self):
        captions={}
        for ui in ('ru','en','fr','zh'):
            for frame in ('ru','en','fr','zh'):
                (self.root/'helene.json').write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf-8')
                error=words.NativeFileNotFoundError('window_assets','LEGACY '+RAW,path=RAW)
                self.assertIsInstance(error,FileNotFoundError)
                self.assertEqual(str(error),'LEGACY '+RAW)
                before=words.native_error(error);text=words.error_text(error,self.root)
                self.assertIn(RAW,text);self.assertEqual(words.native_error(error),before)
                self.assertEqual(captions.setdefault(ui,text),text)
                for code,keys in words.NATIVE_ERRORS.items():
                    values={key:RAW for key in keys}
                    self.assertIn(words.text('native.app.error.'+code,ui,values),words.error_text(words.NativeValueError(code,'LEGACY',**values),self.root))
        self.assertEqual(len(set(captions.values())),4)

    def test_raw_exception_or_caption_looking_message_does_not_gain_metadata(self):
        error=ValueError('Select an agent before installing an optional component')
        error.native_caption={'schema':'helene.native-error.v1','code':'agent_missing','values':{}}
        self.assertIsNone(words.native_error(error));self.assertEqual(words.error_text(error,self.root),str(error))
        for code,values in (('unknown',{}),('agent_missing',{'extra':RAW}),('window_assets',{}),('window_assets',{'path':{}})):
            with self.assertRaises(ValueError):words.NativeValueError(code,'LEGACY',**values)

    def test_actual_application_refuses_before_component_dispatch(self):
        app=python_app.Application(self.root,self.root);self.addCleanup(app.server.server_close)
        with patch.object(program_commands,'invoke',return_value=program_commands.UNHANDLED), \
             patch('localharness.app_components.install',side_effect=AssertionError('install')) as install:
            with self.assertRaises(words.NativeValueError) as unavailable:app.invoke('host_forbidden',{})
            self.assertEqual(words.native_error(unavailable.exception)['code'],'call_unavailable')
            with self.assertRaises(words.NativeValueError) as restore:app.invoke('restore_upstream',{})
            self.assertEqual(words.native_error(restore.exception)['code'],'restore_confirm')
            with self.assertRaises(words.NativeRuntimeError) as missing:app.invoke('component_install',{'name':'whisper'})
            self.assertEqual(words.native_error(missing.exception)['code'],'agent_missing')
            install.assert_not_called()

    def test_authenticated_native_http_preserves_owned_envelope_and_raw_failure(self):
        app=python_app.Application(self.root,self.root);app.serve()
        self.addCleanup(app.server.server_close);self.addCleanup(app.server.shutdown)
        def request():
            data=json.dumps({'command':'fixture','args':{}}).encode()
            req=urllib.request.Request(app.origin+'/__python__/invoke',data=data,headers={'Content-Type':'application/json','X-Helene-Window':app.token})
            try:urllib.request.urlopen(req,timeout=3)
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code,400);return json.loads(exc.read())
            self.fail('failure was not returned')
        for error,owned in [(words.NativeRuntimeError('agent_missing',RAW),True),(RuntimeError(RAW),False)]:
            with patch.object(app,'invoke',side_effect=error) as call:
                response=request()
            call.assert_called_once_with('fixture',{})
            self.assertEqual(response['error'],RAW)
            if owned:self.assertEqual(response['native_error'],words.native_error(error))
            else:self.assertNotIn('native_error',response)

    def test_actual_startup_failure_uses_interface_axis_and_keeps_diagnostics(self):
        for ui in ('ru','en','fr','zh'):
            for frame in ('ru','en','fr','zh'):
                (self.root/'helene.json').write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf-8')
                error=words.NativeFileNotFoundError('window_assets','LEGACY '+RAW,path=RAW)
                with patch.object(python_app,'_main',side_effect=error),patch.object(python_app.sys,'platform','darwin'), \
                     patch('subprocess.run') as alert,contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(python_app.main(['--root',str(self.root)]),1)
                args=alert.call_args.args[0]
                self.assertEqual(args[-2],words.text('native.app.startup_title',ui,{}))
                self.assertIn(words.text('native.app.error.window_assets',ui,{'path':RAW}),args[-1])
                self.assertIn(RAW,args[-1]);self.assertNotIn('LEGACY',args[-1])
                self.assertIn('LEGACY',python_app.startup_log(self.root).read_text())
