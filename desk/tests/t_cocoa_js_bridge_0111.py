"""The real lost-callback shutdown failure, without a platform GUI dependency."""
from pathlib import Path
from types import SimpleNamespace
import sys,tempfile,threading,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness.cocoa_js_bridge import install,CocoaJavascriptClosed

class Hook:
    def __init__(self):self.handlers=[]
    def __iadd__(self,handler):self.handlers.append(handler);return self
    def fire(self):
        for handler in self.handlers:handler()

class CocoaBridge(unittest.TestCase):
    def setUp(self):
        self.queued=[];self.called=threading.Event();self.callbacks=[]
        class View:
            instances={}
            def evaluate_js(view,script,parse_json):return ('foreign',script,parse_json)
        self.backend=SimpleNamespace(BrowserView=View,AppHelper=SimpleNamespace(callAfter=lambda f:self.queued.append(f)))
        def dispatch(script,uid,parse_json=True):
            view=View.instances.get(uid)
            if view:return view.evaluate_js(script,parse_json)
        self.backend.evaluate_js=dispatch
        self.app=SimpleNamespace(window=SimpleNamespace(uid='owned',events=SimpleNamespace(closed=Hook())))
        self.bridge=install(self.app,self.backend);self.addCleanup(self.bridge.close)
        def execute(script,callback):self.callbacks.append(callback);self.called.set()
        self.view=View();self.view.pywebview_window=self.app.window
        View.instances['owned']=self.view
        self.view.webview=SimpleNamespace(evaluateJavaScript_completionHandler_=execute)
        self.results=[];self.cancelled=[];self.errors=[]
    def launch(self,parse=True):
        def execute():
            try:self.results.append(self.view.evaluate_js('fixture-script',parse))
            except CocoaJavascriptClosed:self.cancelled.append(True)
            except BaseException as error:self.errors.append(error)
        thread=threading.Thread(target=execute,daemon=False);thread.start()
        def cleanup():self.bridge.close();thread.join(2);self.assertFalse(thread.is_alive())
        self.addCleanup(cleanup)
        for _ in range(200):
            if self.queued:return thread
            threading.Event().wait(.005)
        self.fail('Evaluation did not reach the native dispatcher')
    def test_lost_native_callback_cannot_hold_non_daemon_injection_at_shutdown(self):
        thread=self.launch();self.queued.pop()();self.assertTrue(self.called.wait(1))
        self.assertTrue(thread.is_alive());self.app.window.events.closed.fire();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(self.cancelled,[True]);self.assertEqual(self.results,[])
    def test_close_before_queued_native_evaluation_prevents_any_javascript_execution(self):
        thread=self.launch();self.bridge.close();self.queued.pop()();thread.join(2)
        self.assertFalse(self.called.is_set());self.assertEqual(self.cancelled,[True])
    def test_live_callback_preserves_json_result(self):
        thread=self.launch();self.queued.pop()();self.callbacks.pop()('{"value":7}',None);thread.join(2)
        self.assertEqual(self.results,[{'value':7}]);self.assertEqual(self.errors,[])
    def test_live_raw_callback_is_not_json_decoded(self):
        thread=self.launch(False);self.queued.pop()();self.callbacks.pop()('{"value":7}',None);thread.join(2)
        self.assertEqual(self.results,['{"value":7}'])
    def test_late_callback_cannot_resurrect_a_closed_result(self):
        thread=self.launch();self.queued.pop()();callback=self.callbacks.pop()
        self.bridge.close();thread.join(2);callback('{"late":true}',None)
        self.assertEqual(self.results,[]);self.assertEqual(self.cancelled,[True])
        with self.assertRaises(CocoaJavascriptClosed):self.view.evaluate_js('late',True)
    def test_removed_cocoa_instance_is_not_a_false_successful_injection(self):
        self.backend.BrowserView.instances.pop('owned')
        with self.assertRaises(CocoaJavascriptClosed):self.backend.evaluate_js('late','owned',False)
    def test_foreign_window_keeps_its_original_implementation_after_owned_close(self):
        foreign=self.backend.BrowserView();foreign.pywebview_window=object();self.bridge.close()
        self.assertEqual(foreign.evaluate_js('foreign',True),('foreign','foreign',True))
    def test_scheduling_error_does_not_leave_a_pending_thread(self):
        def failed(_fn):raise RuntimeError('native dispatcher stopped')
        self.backend.AppHelper.callAfter=failed
        with self.assertRaisesRegex(RuntimeError,'dispatcher stopped'):self.view.evaluate_js('test',True)
        self.assertFalse(self.bridge._pending)
    def test_install_is_idempotent_and_closing_another_window_does_not_cancel_this_one(self):
        self.assertIs(install(self.app,self.backend),self.bridge)
        second=SimpleNamespace(window=SimpleNamespace(uid='second',events=SimpleNamespace(closed=Hook())))
        other=install(second,self.backend);other.close()
        thread=self.launch();self.queued.pop()();self.callbacks.pop()('3',None);thread.join(2)
        self.assertEqual(self.results,[3]);self.assertEqual(self.cancelled,[])

    def test_application_cleanup_cancels_javascript_before_a_host_shutdown_error(self):
        import python_app
        with tempfile.TemporaryDirectory() as folder:
            app=python_app.Application(Path(folder),Path(folder));events=[]
            app.cocoa_js_bridge=SimpleNamespace(close=lambda:events.append('bridge'))
            def failed_host():events.append('host');raise RuntimeError('host cleanup fixture')
            app.host=SimpleNamespace(shutdown=failed_host);app.exit_session=True
            with self.assertRaisesRegex(RuntimeError,'host cleanup fixture'):app.close()
            self.assertEqual(events,['bridge','host'])

if __name__=='__main__':unittest.main()
