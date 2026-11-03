"""Cancel this window's pending Cocoa JS calls after its native loop closes.

pywebview6.2.1 starts a non-daemon JS-injection thread whose Cocoa semaphore
has no closed-window path. Keep live evaluation semantics and cancel only the
owned window; neither daemonize arbitrary threads nor skip session shutdown.
"""
import json
import threading


class CocoaJavascriptClosed(SystemExit):
    """End only the obsolete bridge call, without a false loaded event."""
    def __init__(self):super().__init__(0)


class CocoaJavascriptBridge:
    def __init__(self,window,backend):
        self.window=window;self.backend=backend
        self._closed=threading.Event();self._lock=threading.Lock();self._pending=set()
        original=backend.BrowserView.evaluate_js
        def evaluate(view,script,parse_json):
            if getattr(view,'pywebview_window',None) is not window:
                return original(view,script,parse_json)
            return self.evaluate(view,script,parse_json)
        backend.BrowserView.evaluate_js=evaluate
        original_dispatch=backend.evaluate_js
        def dispatch(script,uid,parse_json=True):
            if uid==window.uid and (self._closed.is_set() or uid not in backend.BrowserView.instances):
                # Cocoa removes the native instance before a late injection
                # can reach its method. Do not turn that disappearance into
                # a successful JS return and a spurious loaded callback.
                raise CocoaJavascriptClosed()
            return original_dispatch(script,uid,parse_json)
        backend.evaluate_js=dispatch
        window.events.closed+=self.close

    def close(self,*_args):
        with self._lock:
            self._closed.set()
            for pending in self._pending:pending.set()

    def evaluate(self,view,script,parse_json):
        done=threading.Event();value=[None];failure=[]
        with self._lock:
            if self._closed.is_set():raise CocoaJavascriptClosed()
            self._pending.add(done)
        def completed(result,error):
            if not self._closed.is_set():
                if parse_json and result:
                    try:result=json.loads(result)
                    except (ValueError,TypeError):pass
                value[0]=result
            done.set()
        def evaluate_on_main():
            if self._closed.is_set():done.set();return
            try:view.webview.evaluateJavaScript_completionHandler_(script,completed)
            except Exception as error:failure.append(error);done.set()
        try:
            self.backend.AppHelper.callAfter(evaluate_on_main)
            done.wait()  # Live views retain the existing synchronous API.
            if self._closed.is_set():raise CocoaJavascriptClosed()
            if failure:raise failure[0]
            return value[0]
        finally:
            with self._lock:self._pending.discard(done)


def install(app,backend=None):
    if getattr(app,'cocoa_js_bridge',None) is not None:return app.cocoa_js_bridge
    if backend is None:
        from webview.platforms import cocoa as backend
    app.cocoa_js_bridge=CocoaJavascriptBridge(app.window,backend)
    return app.cocoa_js_bridge
