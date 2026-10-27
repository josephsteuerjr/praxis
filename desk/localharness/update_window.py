"""Small updater-owned observer using the already packaged pywebview backend.

No Controller, agent, owner lock or network server: the only bridge reads one
operation receipt. This window runs in the updater itself, not another app owner.
"""
from __future__ import annotations

import json
import base64
import re
from pathlib import Path
import sys
import threading
import time

HTML = r'''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:light;--paper:#f8eee0;--ink:#1d1a17;--muted:#7d6c5a;--accent:#9a4a1e;--line:rgba(29,26,23,.14);--hand:'Zen Kurenaido','Segoe Print',cursive}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;--paper:#1d1914;--ink:#f1e4d0;--muted:#ab9982;--accent:#eda676;--line:rgba(241,228,208,.13)}}
:root[data-theme=dark]{color-scheme:dark;--paper:#1d1914;--ink:#f1e4d0;--muted:#ab9982;--accent:#eda676;--line:rgba(241,228,208,.13)}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15.5px/1.6 'Golos Text','Segoe UI',sans-serif;padding:28px 32px;overflow:auto;height:auto;min-height:100vh}
body>main{position:relative;max-width:660px;margin:auto}small{color:var(--muted);font-size:11px;letter-spacing:.13em}h1{font-family:var(--hand);font-size:32px;font-weight:400;line-height:1.22;margin:18px 0 22px}
#update-chrome{display:flex;align-items:center;justify-content:space-between;min-height:28px;gap:12px;margin:-12px -12px 0;padding-left:12px;user-select:none}
.update-actions{display:flex;gap:2px}.update-actions button{font:20px/1 'Golos Text',sans-serif;width:30px;height:28px;padding:0;border:0;border-radius:5px;background:transparent;color:var(--muted);cursor:pointer}
.update-actions button:hover:not(:disabled){background:var(--line);color:var(--ink)}.update-actions button:disabled{opacity:.3;cursor:default}
p{margin:9px 0;overflow-wrap:anywhere}#detail,#history{color:var(--muted);font-size:13px}#error{color:var(--ink);white-space:pre-wrap}#history{border-top:1px solid var(--line);padding-top:15px;margin-top:22px}
#source-choice{border:1px solid var(--line);border-radius:8px;padding:16px;margin:16px 0}#source-choice[hidden]{display:none}.source-options{display:flex;flex-wrap:wrap;gap:8px}.source-options button{font:inherit;border:1px solid var(--line);border-radius:6px;padding:9px 12px;background:transparent;color:var(--ink);cursor:pointer}.source-options button:first-child{background:var(--accent);color:var(--paper)}#source-choice p{font-size:13px;color:var(--muted)}
.track{height:3px;background:color-mix(in srgb,var(--accent) 16%,transparent);overflow:hidden;margin:22px 0}
.track:before{content:'';display:block;width:28%;height:100%;background:var(--accent);animation:moving 2s ease-in-out infinite}
@keyframes moving{0%{transform:translateX(-110%)}100%{transform:translateX(460%)}}
body.done .track:before{animation:none;width:100%}body.failed .track:before{animation:none;width:0}
@media(prefers-reduced-motion:reduce){.track:before{animation:none;transform:none}}
@media(max-width:430px){body{padding:22px}h1{font-size:28px}}
</style><!--shared-appearance--><main><header id="update-chrome"><small>HÉLÈNE · ОБНОВЛЕНИЕ</small><div class="update-actions"><button id="minimize" aria-label="Свернуть окно">−</button><button id="close" aria-label="Закрыть окно" disabled>×</button></div></header><h1 id="stage">Подготавливаю обновление</h1>
<div class="track" aria-hidden="true"></div><p id="detail" role="status" aria-live="polite"></p><p id="error"></p>
<section id="source-choice" hidden><strong id="source-version"></strong><p>Сохранить изменения — агент совместит ваши правки с выпуском. Чистое обновление заменит локальные изменения кода. Прежний код останется для отката; память, переписка и настройки сохранятся.</p><div class="source-options"><button data-source="preserve">Сохранить правки · поручить агенту</button><button data-source="clean">Установить чистый код выпуска</button><button id="cancel-choice">Отмена</button></div></section>
<p id="history"></p></main><script>
const stage=document.getElementById('stage'),detail=document.getElementById('detail'),error=document.getElementById('error'),history=document.getElementById('history');
function paint(r){stage.textContent=r.label||r.stage||'Подготавливаю переключение';
const choice=r.stage==='choosing_source'&&r.source_choice;document.getElementById('source-choice').hidden=!choice;if(choice)document.getElementById('source-version').textContent='Код Hélène '+(choice.version||'нового выпуска');
const now=r.finished_at||Date.now()/1000, s=r.stages?.at(-1); const seconds=s?Math.max(0,now-s.started_at):0;
const parts=[`Этап: ${Math.floor(seconds)} с`];
if(r.total_files)parts.push(`${r.files||0} / ${r.total_files} файлов`);
if(r.bytes)parts.push(`${(r.bytes/1048576).toFixed(1)} МиБ${r.total_bytes?' / '+(r.total_bytes/1048576).toFixed(1)+' МиБ':''}`);
detail.textContent=parts.join(' · '); error.textContent=r.error||'';
history.textContent=r.stage==='completed'?'Новое окно подтвердило запуск.':r.stage==='failed'?'Подробности сохранены в журнале обновления.': 'Настройки и память остаются на своих путях.';
document.body.className=r.stage==='completed'?'done':r.stage==='failed'?'failed':'';document.getElementById('close').disabled=!r.can_close;}
document.getElementById('minimize').onclick=()=>window.pywebview.api.minimize();
document.getElementById('close').onclick=()=>window.pywebview.api.dismiss();
document.querySelectorAll('[data-source]').forEach(button=>button.onclick=async()=>{try{await window.pywebview.api.choose_source(button.dataset.source);await tick();}catch(e){error.textContent=String(e);}});
document.getElementById('cancel-choice').onclick=()=>window.pywebview.api.cancel_choice();
let timer;
async function tick(){try{paint(await window.pywebview.api.status());}catch(e){error.textContent='Не удалось прочитать состояние: '+String(e);} }
window.addEventListener('pywebviewready',async()=>{await tick();requestAnimationFrame(()=>requestAnimationFrame(()=>window.pywebview.api.ready()));timer=setInterval(tick,350)});
window.addEventListener('beforeunload',()=>clearInterval(timer));
</script></html>'''


def choose_source(receipt,mode):
    from .update_progress import read_json
    from .update_source import select
    row=read_json(receipt)
    if row.get('stage')!='choosing_source' or not row.get('source_choice'):
        raise ValueError('Source choice is not active')
    bound=row['source_choice'];new=Path(bound['new']).resolve();old=Path(bound['old']).resolve()
    if str(old)!=row['target'] or new.parent!=Path(receipt).parent/('.helene-update-'+row['id']):
        raise ValueError('Source choice leaves this update operation')
    return select(old,new,mode,by='updater')


def cancel_choice(receipt):
    from .update_progress import Progress
    progress=Progress(receipt)
    if progress.value['stage']!='choosing_source':raise ValueError('Source choice is not active')
    progress.phase('choosing_source',source_choice_cancelled=True)
    return True


def page(root):
    """Use the application's shipped fonts/tokens, with no external requests.

    Resolve only the stylesheet selected by static/index.html: old hashed
    assets can legitimately remain after an in-place update.
    """
    root = Path(root)
    app = Path(__file__).resolve().parents[1]
    index = app / 'static/index.html'
    sheets = []
    if index.is_file():
        for ref in re.findall(r'href=[\"\']([^\"\']+\.css)[\"\']', index.read_text(encoding='utf-8')):
            candidate = (index.parent / ref.lstrip('/')).resolve()
            if candidate.is_relative_to(index.parent.resolve()) and candidate.is_file():
                sheets.append(candidate)
    else:  # source preview; production uses its exact built stylesheet
        sheets = [app / 'ui-kit/fonts.css', app / 'ui-kit/window/styles/paper-tokens.css']
    def embed(sheet):
        css = sheet.read_text(encoding='utf-8')
        def resource(match):
            ref = match[1].strip('\"\' ')
            if ref.startswith('data:'):return match[0]
            path = (sheet.parent / ref).resolve()
            if not path.is_relative_to(app) or not path.is_file():return 'url(data:,)'
            return 'url(data:font/woff2;base64,' + base64.b64encode(path.read_bytes()).decode() + ')'
        return re.sub(r'url\(([^)]+)\)', resource, css)
    # Insert shared styles first, then the small window's layout overrides.
    shared = '\n'.join(embed(sheet) for sheet in sheets if sheet.is_file())
    output = HTML.replace('<style>', '<style>' + shared + '</style><style>', 1)
    from .window_preferences import storage
    prefs = storage(root)
    theme = prefs.get('frame.theme', 'system')
    if theme in {'light', 'dark'}:
        output = output.replace('<html lang="ru">', '<html lang="ru" data-theme="' + theme + '">')
    return output


def run(transaction, receipt, ready, *, browser=False):
    """Keep native presentation on the main thread and transaction on one worker.

    The transaction waits for ready before updater admission; a broken native
    backend never asks the old application to close. Error text stays visible
    briefly, while the operation's durable receipt remains after this exits.
    """
    if browser:return _browser(transaction, receipt, ready)
    import webview
    from .update_progress import Progress, read_json
    receipt = Path(receipt)
    progress = Progress(receipt)
    operation = progress.value['id']
    content = page(progress.value['target'])
    result = []
    failure = []
    finished = threading.Event()
    class Bridge:
        def choose_source(self,mode):return choose_source(receipt,mode)
        def cancel_choice(self):return cancel_choice(receipt)
        def status(self):
            value = read_json(receipt)
            if value.get('id') != operation:raise ValueError('Update operation changed')
            return dict(value, can_close=finished.is_set())
        def ready(self):
            ready.set()
            return True
        def minimize(self):
            window.minimize()
        def dismiss(self):
            if not finished.is_set():return False
            window.destroy()
            return True
    webview.settings['DRAG_REGION_SELECTOR'] = '#update-chrome, #update-chrome small'
    webview.settings['DRAG_REGION_DIRECT_TARGET_ONLY'] = True
    window = webview.create_window('Hélène — обновление', html=content, js_api=Bridge(),
                                   width=560, height=390, min_size=(360, 300), resizable=True,
                                   frameless=True, easy_drag=False,
                                   background_color='#f8eee0', text_select=True)
    def work():
        try:result.append(transaction())
        except BaseException as exc:
            failure.append(exc)
            Progress(receipt).phase('failed', error=str(exc))
        finally:
            finished.set()
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
    from .update_progress import Progress, read_json
    import secrets
    import webbrowser
    token = secrets.token_urlsafe(32)
    path = Path(receipt)
    identity = Progress(path).value['id']
    stub = """<script>async function sourceChoice(mode){const r=await fetch(location.pathname+'/choice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});if(!r.ok)throw Error(await r.text());return r.json()}window.pywebview={api:{status:async()=>{const r=await fetch(location.pathname+'/status',{cache:'no-store'});if(!r.ok)throw Error(r.status);return r.json()},choose_source:sourceChoice,cancel_choice:()=>sourceChoice('cancel'),ready:async()=>true}};window.addEventListener('DOMContentLoaded',()=>window.dispatchEvent(new Event('pywebviewready')));</script>"""
    content = page(Progress(path).value['target']).replace('<small>', stub + '<small>', 1)
    content = content.replace('<!--shared-appearance-->', '<style>.update-actions{display:none}</style>').encode('utf-8')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):pass  # random observer URL stays out of logs
        def do_POST(self):
            if self.path!='/'+token+'/choice' or self.headers.get('Content-Type')!='application/json':self.send_error(404);return
            if self.headers.get('Origin') not in (None,f'http://127.0.0.1:{self.server.server_port}'):self.send_error(403);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=512:raise ValueError('Invalid source choice')
                mode=json.loads(self.rfile.read(size))['mode']
                value=cancel_choice(path) if mode=='cancel' else choose_source(path,mode)
                body=json.dumps(value).encode()
            except (ValueError,KeyError,OSError) as exc:self.send_error(409,str(exc));return
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.path == '/' + token:
                body, mime = content, 'text/html; charset=utf-8'
            elif self.path == '/' + token + '/status':
                value = read_json(path)
                if value.get('id') != identity:
                    self.send_error(409);return
                body, mime = json.dumps(value).encode(), 'application/json'
            else:
                self.send_error(404);return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers();self.wfile.write(body)
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
