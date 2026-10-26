"""Small updater-owned observer using the already packaged pywebview backend.

No Controller, agent, owner lock or network server: the only bridge reads one
operation receipt. This window runs in the updater itself, not another app owner.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time

HTML = r'''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:light dark;--paper:#f4eee3;--ink:#29251f;--muted:#655d52;--accent:#765735}
@media(prefers-color-scheme:dark){:root{--paper:#1d1914;--ink:#eee6d9;--muted:#b9ad9c;--accent:#d2b186}}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 system-ui,sans-serif;padding:28px}
small{color:var(--muted);letter-spacing:.08em}h1{font-size:23px;font-weight:550;line-height:1.25;margin:12px 0 20px}
p{margin:9px 0;overflow-wrap:anywhere}#detail,#history{color:var(--muted);font-size:13px}#error{color:var(--ink);white-space:pre-wrap}
.track{height:3px;background:color-mix(in srgb,var(--accent) 20%,transparent);overflow:hidden;margin:20px 0}
.track:before{content:'';display:block;width:28%;height:100%;background:var(--accent);animation:moving 2s ease-in-out infinite}
@keyframes moving{0%{transform:translateX(-110%)}100%{transform:translateX(460%)}}
body.done .track:before{animation:none;width:100%}body.failed .track:before{animation:none;width:0}
</style><small>HÉLÈNE · ОБНОВЛЕНИЕ</small><h1 id="stage">Подготавливаю переключение</h1>
<div class="track" aria-hidden="true"></div><p id="detail" role="status" aria-live="polite"></p><p id="error"></p>
<p id="history"></p><script>
const stage=document.getElementById('stage'),detail=document.getElementById('detail'),error=document.getElementById('error'),history=document.getElementById('history');
function paint(r){stage.textContent=r.label||r.stage||'Подготавливаю переключение';
const now=Date.now()/1000, s=r.stages?.at(-1); const seconds=s?Math.max(0,now-s.started_at):0;
const parts=[`Этап: ${Math.floor(seconds)} с`];
if(r.total_files)parts.push(`${r.files||0} / ${r.total_files} файлов`);
if(r.bytes)parts.push(`${(r.bytes/1048576).toFixed(1)} МиБ${r.total_bytes?' / '+(r.total_bytes/1048576).toFixed(1)+' МиБ':''}`);
detail.textContent=parts.join(' · '); error.textContent=r.error||'';
history.textContent=r.stage==='completed'?'Новое окно подтвердило запуск.':r.stage==='failed'?'Подробности сохранены в журнале обновления.': 'Настройки и память остаются на своих путях.';
document.body.className=r.stage==='completed'?'done':r.stage==='failed'?'failed':'';}
let timer;
async function tick(){try{paint(await window.pywebview.api.status());}catch(e){error.textContent='Не удалось прочитать состояние: '+String(e);} }
window.addEventListener('pywebviewready',async()=>{await tick();requestAnimationFrame(()=>requestAnimationFrame(()=>window.pywebview.api.ready()));timer=setInterval(tick,350)});
window.addEventListener('beforeunload',()=>clearInterval(timer));
</script></html>'''


def run(transaction, receipt, ready, *, browser=False):
    """Keep native presentation on the main thread and transaction on one worker.

    The transaction waits for ready before updater admission; a broken native
    backend never asks the old application to close. Error text stays visible
    briefly, while the operation's durable receipt remains after this exits.
    """
    if browser:return _browser(transaction, receipt, ready)
    import webview
    from .update_progress import Progress
    receipt = Path(receipt)
    progress = Progress(receipt)
    operation = progress.value['id']
    result = []
    failure = []
    class Bridge:
        def status(self):
            value = json.loads(receipt.read_text(encoding='utf-8'))
            if value.get('id') != operation:raise ValueError('Update operation changed')
            return value
        def ready(self):
            ready.set()
            return True
    window = webview.create_window('Hélène — обновление', html=HTML, js_api=Bridge(),
                                   width=520, height=330, min_size=(400, 280), resizable=True,
                                   background_color='#1d1914', text_select=True)
    def work():
        try:result.append(transaction())
        except BaseException as exc:
            failure.append(exc)
            Progress(receipt).phase('failed', error=str(exc))
        finally:
            time.sleep(12 if failure else 2)
            window.destroy()
    thread = threading.Thread(target=work, name='update-transaction')
    try:
        webview.start(func=thread.start, gui='qt' if sys.platform.startswith('linux') else None,
                      private_mode=True)
    finally:
        # No worker is orphaned if the observer is dismissed. This is the same
        # updater process and transaction, not a second supervisor.
        if thread.ident is not None:thread.join()
    if failure:raise failure[0]
    return result[0] if result else None


def _browser(transaction, receipt, ready):
    """Explicit --browser keeps working on a headless host, with no Qt import.

    Transport readiness is reported as such, not as a painted native window.
    The page uses a one-operation random URL; no application/agent API exists.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from .update_progress import Progress
    import secrets
    import webbrowser
    token = secrets.token_urlsafe(32)
    path = Path(receipt)
    identity = Progress(path).value['id']
    stub = """<script>window.pywebview={api:{status:async()=>{const r=await fetch(location.pathname+'/status',{cache:'no-store'});if(!r.ok)throw Error(r.status);return r.json()},ready:async()=>true}};window.addEventListener('DOMContentLoaded',()=>window.dispatchEvent(new Event('pywebviewready')));</script>"""
    page = HTML.replace('<small>', stub + '<small>', 1).encode('utf-8')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):pass  # random observer URL stays out of logs
        def do_GET(self):
            if self.path == '/' + token:
                content, mime = page, 'text/html; charset=utf-8'
            elif self.path == '/' + token + '/status':
                value = json.loads(path.read_text(encoding='utf-8'))
                if value.get('id') != identity:
                    self.send_error(409);return
                content, mime = json.dumps(value).encode(), 'application/json'
            else:
                self.send_error(404);return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers();self.wfile.write(content)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        opened = webbrowser.open(f'http://127.0.0.1:{server.server_port}/{token}')
        progress = Progress(path)
        progress.phase(progress.value['stage'], observer_backend='browser',
                       observer_transport_ready=True, browser_open_requested=bool(opened))
        ready.set()
        try:return transaction()
        except BaseException as exc:
            Progress(path).phase('failed', error=str(exc))
            raise
        finally:time.sleep(2)
    finally:
        server.shutdown();server.server_close();thread.join(5)
