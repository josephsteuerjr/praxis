#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hélène's Python desktop application, retaining the shipped window UI.

Usage: runtime/python.exe app/python_app.py --root <owner directory>
The native host is a replaceable OS integration organ, not the window runtime.
Python/UI updates therefore need no Electron or Tauri rebuild.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
import sys
import threading
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

def recover_before_imports(argv):
    """The startup consumer repairs an interrupted overlay before body imports."""
    if '--panic' in argv:return False  # Stop never waits for update recovery
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--root',type=Path)
    parser.add_argument('--migration',default='')
    known,_rest=parser.parse_known_args(argv)
    if known.root is None:return False
    from localharness.python_source_updater import recover_startup
    root=known.root.resolve()
    if not recover_startup(root,known.migration):return False
    executable=root/('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')
    os.execv(str(executable),[str(executable),str(root/'app/python_app.py'),*argv])
    return True


if __name__=='__main__':
    recover_before_imports(sys.argv[1:])

from localharness.python_controller import Controller
from localharness.python_platform import desktop_identity


class Application:
    def __init__(self, root: Path, ui: Path, host=None, program: Path | None = None):
        self.root, self.ui, self.host = root, ui.resolve(), host
        self.program = program or root
        self.window = None
        self.window_maximized = False
        self.relaunch = False
        self.migration_wizard = False
        self.migration_id = ''
        self.confirm_lock = threading.Lock()
        self.confirmed_ui = False
        self.browser_mode = False
        self.tray = None
        self.voice_keys = None
        self.exiting = False
        self.token = secrets.token_urlsafe(32)
        self.events: list[dict] = []
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.channel = (host.channel() if isinstance(host, Controller) else
                        self._channel(host.ready.result(timeout=60)["script"]) if host else {})
        if host:
            self.host.on_event = self.event
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.control_file = self.root / f'.python-window-{os.getpid()}.json'

    def publish_control(self):
        """Expose the existing authenticated exit surface only after UI readiness."""
        value = {'schema': 1, 'pid': os.getpid(), 'root': str(self.root.resolve()),
                 'origin': self.origin, 'token': self.token}
        temporary = self.control_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(value), encoding='utf-8')
        os.replace(temporary, self.control_file)

    @staticmethod
    def _channel(script):
        prefix = "window.DESK_CONFIG_OVERRIDE = "
        line = script.strip().splitlines()[0]
        if not line.startswith(prefix) or not line.endswith(";"):
            raise ValueError("Движок не передал канал окна")
        return json.loads(line[len(prefix):-1])

    def event(self, name, data):
        if name == "relaunch":
            self.relaunch = True
            name = "exit"
        if name == "replace-window":
            self.channel = self._channel(data["script"])
        with self.lock:
            self.events.append({"name": name, "data": data})

    def invoke(self, command, args):
        if not isinstance(command, str) or command.startswith("host_"):
            raise ValueError("Недоступный вызов оболочки")
        if command == 'voice_shortcuts':
            if self.browser_mode:return {'active':False,'error':'В браузере сочетание работает только внутри окна.'}
            if self.voice_keys is None:
                from localharness.voice_hotkeys import GlobalVoiceKeys
                self.voice_keys=GlobalVoiceKeys(self)
            return self.voice_keys.configure(args)
        if command == 'voice_status':
            if self.voice_keys:self.voice_keys.show(args)
            return {'ok':True}
        if command in ('window_preferences','appearance_read','appearance_patch','appearance_reset'):
            from localharness import window_preferences as preferences
            if command == 'appearance_read':return preferences.read(self.root)
            if command == 'appearance_reset':return preferences.reset(self.root,args.get('seen'))
            if command == 'appearance_patch':return preferences.change(self.root,args.get('patch'),args.get('seen'),appearance_only=True)
            return preferences.change(self.root,{args.get('key'):args.get('value')},args.get('seen'))
        if command == "components_status":
            from localharness import standalone_relay, component_downloads
            return [standalone_relay.status(self.program)]+component_downloads.status(self.program)
        if command == 'python_ready':
            return self.confirm_ui()
        if command == 'migration_wizard':
            if not sys.platform.startswith('linux'):
                raise ValueError('This offline migration wizard is available in the Linux application')
            self.migration_wizard = True
            self.relaunch = True
            self.event('exit', {})
            return None
        if command == "restart_self":
            self.relaunch = True
            self.event("exit", {})
            return None
        if command == "component_install":
            name=str(args.get('name',''))
            if name == 'relay':
                from localharness import standalone_relay
                return standalone_relay.install(self.program,lambda value:self.event('package-progress',value))
            if name == 'toolchain':raise ValueError('Инструменты сборки больше не поставляются с Hélène')
            if name in ('xtts','whisper'):
                from localharness import component_downloads
                if self.host is None:raise RuntimeError('Select an agent before installing an optional component')
                tree=self.host.current().tree
                return component_downloads.install(self.program,[name],lambda value:self.event('package-progress',value),tree=tree)
            from localharness import app_components
            if self.host is not None:
                self.host.close()
                self.host = None
            return app_components.install(self.program, self.root, str(args.get("name", "")))
        if command == "components_start":
            if self.host is None:
                host = Controller(self.root, self.event, self.origin)
                try:
                    host.start()
                    channel = host.channel()
                except BaseException:
                    host.close()
                    raise
                self.host, self.channel = host, channel
            return {"ok": True}
        if command == "backup_now":
            from localharness import essential_backup
            return essential_backup.snapshot(self.root, essential_backup.destination(self.root))
        if command == "backup_list":
            from localharness import essential_backup
            return essential_backup.listing(self.root)
        if command == "python_window":
            action = args.get("action")
            if action == "exit":
                self.exiting = True
                if args.get('relaunch') is False:
                    self.relaunch = False
                if self.window is None:
                    self.stopping.set()
                else:
                    self.window.destroy()
                return None
            if action == "close" and self.window is None:
                self.stopping.set()
            if self.window is not None:
                if action == "minimize":
                    self.window.minimize()
                elif action == "maximize":
                    if self.window_maximized:
                        self.window.restore()
                    else:
                        self.window.maximize()
                elif action == "close" and self.tray is not None and not self.exiting:
                    self.window.hide()
                elif action in ("close", "exit"):
                    self.exiting = True
                    self.window.destroy()
            return None
        if self.host is None:
            raise RuntimeError("Install the bundled components and start the application first")
        return self.host.invoke(command, args)

    def confirm_ui(self):
        if not self.migration_id:return {'confirmed':False}
        with self.confirm_lock:
            if not self.confirmed_ui:
                from localharness import app_migration, owner_stop
                proof=({'stopped':True,'root':str(self.root)} if owner_stop.stopped()
                       else migration_proof(self.host,self.root))
                app_migration.confirm_started(self.root,self.migration_id,proof=proof)
                self.confirmed_ui=True
        return {'confirmed':True,'migration_id':self.migration_id}

    @staticmethod
    def preference_key(key):
        from localharness.window_preferences import preference_key
        return preference_key(key)

    def preferences(self):
        from localharness.window_preferences import storage
        return storage(self.root)

    def bridge(self):
        from localharness.window_preferences import bootstrap
        saved, seen = bootstrap(self.root)
        return """window.DESK_CONFIG_OVERRIDE = %s;
window.__HELENE__ = {shell:'python', platform:%s,
 invoke: async (command,args={}) => {
  const r = await fetch('/__python__/invoke', {method:'POST',headers:{
   'Content-Type':'application/json','X-Helene-Window':%s},body:JSON.stringify({command,args})});
  const v = await r.json(); if(!r.ok) throw new Error(v.error); return v.result;
 }, win(action){this.invoke('python_window',{action}).catch(console.error)}};
window.addEventListener('helene:ui-ready',()=>window.__HELENE__.invoke('python_ready').catch(console.error),{once:true});
(function preferences(saved){
 const store=window.localStorage, originalSet=Storage.prototype.setItem, originalRemove=Storage.prototype.removeItem;
 for(const [key,value] of Object.entries(saved)) originalSet.call(store,key,value);
 let pending=Promise.resolve(), seen=%s, revision=0, appearanceEpoch=0;
 const unsaved=new Map();
 const owned=['helene.look','frame.theme','frame.rail','frame.panel','frame.rail.width','frame.panel.width','helene.zoom'];
 const fail=error=>{console.error(error);window.dispatchEvent(new CustomEvent('helene:preference-error',{detail:String(error)}));};
 // A revision is acknowledged only after its canonical values reach this renderer.
 const accept=result=>{
  for(const key of owned){if(key in result.values)originalSet.call(store,key,result.values[key]);else originalRemove.call(store,key);}
  seen=result.seen;window.dispatchEvent(new CustomEvent('helene:appearance',{detail:result}));
 };
 const persist=(key,value)=>{if(/^(frame[.]|helene[-.]|settings[.]|draft[.])/.test(key)) {
  revision++;const appearance=owned.includes(key), issuedEpoch=appearanceEpoch;
  pending=pending.then(async()=>{
   if(appearance && issuedEpoch!==appearanceEpoch)return; // stale queued full-Look intent
   try {
    if(appearance && seen===null){
     appearanceEpoch++;
     const canonical=await window.__HELENE__.invoke('appearance_read');
     appearanceEpoch++;accept(canonical); // also invalidate edits issued while recovery was awaiting I/O
     fail('Appearance refreshed after an uncertain save; repeat your appearance edit.');return;
    }
    const result=await window.__HELENE__.invoke('window_preferences',{key,value,seen:appearance?seen:null});
    if(!result.ok){
     if(appearance){appearanceEpoch++;accept(result);}
     unsaved.set(key,result.error);fail(result.error);return;
    }
    unsaved.delete(key);
    if(appearance)accept(result); // unrelated draft/settings writes acknowledge no appearance
   } catch(error) {
    unsaved.set(key,error);
    if(appearance){appearanceEpoch++;seen=null;}
    fail(error);
   }
  });}};
 window.__HELENE__.syncAppearance=()=>{const at=revision;pending=pending.then(async()=>{
   const result=await window.__HELENE__.invoke('appearance_read');if(at!==revision || result.seen===seen)return;
   accept(result);
 }).catch(fail);};
 Storage.prototype.setItem=function(key,value){originalSet.call(this,key,value);if(this===store)persist(String(key),String(value));};
 Storage.prototype.removeItem=function(key){originalRemove.call(this,key);if(this===store)persist(String(key),null);};
 // Production WebView2 disables native browser accelerators. Reload only the
 // renderer, never the Python host; let queued draft/preferences writes settle.
 window.__HELENE__.reloadCapability='safe-renderer-v2';
 let reloading=false;
 const requestReload=()=>{
  if(reloading)return;
  reloading=true;
  (async()=>{
   let saving;
   do{saving=pending;await saving;}while(saving!==pending);
   if(unsaved.size)throw new Error('Reload cancelled: unsaved preferences or drafts. Keep this window open and retry saving.');
   window.location.reload();
  })().catch(error=>{reloading=false;fail(error);});
 };
 window.__HELENE__.requestReload=requestReload;
 window.addEventListener('keydown',event=>{
  const reload=!event.altKey&&!event.shiftKey&&!event.isComposing&&(
   (event.key==='F5'&&!event.ctrlKey&&!event.metaKey)||
   ((event.ctrlKey||event.metaKey)&&(event.code==='KeyR'||event.key.toLowerCase()==='r')));
  if(!reload)return;
  event.preventDefault();
  if(!event.repeat)requestReload();
 },true);
})(%s);
(async function events(){try {
 const r=await fetch('/__python__/events',{headers:{'X-Helene-Window':%s}});
 if(r.ok) for(const e of await r.json()) {
  if(e.name==='window-preferences') window.__HELENE__.syncAppearance();
  else if(e.name==='replace-window') location.replace('/');
  else if(e.name==='retire-window') location.replace('/__python__/retired?id='+encodeURIComponent(e.data.id));
  else if(e.name==='exit') window.__HELENE__.win('exit');
  else window.dispatchEvent(new CustomEvent('helene:'+e.name,{detail:e.data}));
 }
} catch(e){console.error(e)} setTimeout(events,500)})();
""" % (json.dumps(self.channel, ensure_ascii=False), json.dumps(desktop_identity()['platform']),
       json.dumps(self.token), json.dumps(seen), json.dumps(saved, ensure_ascii=False), json.dumps(self.token))

    def _handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # never log channel keys or file contents

            def reply(self, code, value, content_type="application/json; charset=utf-8"):
                data = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, (dict, list)) else value
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)

            def authorized(self):
                return (self.headers.get("Host") == urlsplit(app.origin).netloc
                        and self.headers.get("Origin", app.origin) == app.origin
                        and secrets.compare_digest(self.headers.get("X-Helene-Window", ""), app.token))

            def do_GET(self):
                if self.headers.get("Host") != urlsplit(app.origin).netloc:
                    return self.reply(403, {"error": "foreign host"})
                path = urlsplit(self.path).path
                if path == "/config.js":
                    return self.reply(200, b"/* Python window configuration is injected before the UI. */", "application/javascript")
                if path == "/__python__/retired":
                    from urllib.parse import parse_qs
                    retire_id = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
                    # A real document navigation retires the previous renderer's
                    # JavaScript, sockets and credential-bearing globals before ACK.
                    content = '<!doctype html><html><head><meta charset="utf-8"></head><body><script>' + app.bridge() + "\nwindow.__HELENE__.invoke('window_retire_ack',{retireId:" + json.dumps(retire_id) + "}).catch(console.error);</script></body></html>"
                    return self.reply(200, content.encode(), "text/html; charset=utf-8")
                if path == "/" and app.host is None:
                    content = '''<!doctype html><html><head><meta charset="utf-8"><title>Hélène</title></head>
<body style="background:#1d1914;color:#e5d7b8;font:18px system-ui;max-width:760px;margin:10vh auto;padding:24px">
<h1>Hélène · Python</h1><p>Дополнительные голосовые компоненты и praxis-relay можно установить здесь.
Реле — отдельный продукт под лицензией MIT. Для запуска инструменты сборки не нужны.</p>
<div id="components"></div><button id="start">Запустить Hélène</button><pre id="status"></pre>
<script>
async function paint(){const rows=await window.__HELENE__.invoke('components_status');
const box=document.getElementById('components');box.replaceChildren();
for(const row of rows){const p=document.createElement('p');p.textContent=row.id+' · '+(row.installed?'installed':row.error||'ready to install');
const b=document.createElement('button');b.textContent='Install '+row.id;b.disabled=!row.ready;
b.onclick=async()=>{try{await window.__HELENE__.invoke('component_install',{name:row.id});await paint()}
catch(e){document.getElementById('status').textContent=String(e)}};p.append(' ',b);box.append(p)}}
document.getElementById('start').onclick=async()=>{try{await window.__HELENE__.invoke('components_start');location.reload()}
catch(e){document.getElementById('status').textContent=String(e)}};paint().catch(e=>document.getElementById('status').textContent=String(e));
</script></body></html>'''
                    content = content.replace('<head>', '<head><script>' + app.bridge().replace('</', '<\\/') + '</script>', 1)
                    return self.reply(200, content.encode(), "text/html; charset=utf-8")
                if path == "/__python__/events":
                    if not self.authorized():
                        return self.reply(403, {"error": "window token required"})
                    with app.lock:
                        items, app.events = app.events, []
                    items.append({'name':'window-preferences','data':{}})
                    return self.reply(200, items)
                target = (app.ui / unquote(path).lstrip("/")).resolve()
                if path == "/":
                    target = app.ui / "index.html"
                if not target.is_relative_to(app.ui) or not target.is_file():
                    return self.reply(404, {"error": "not found"})
                content = target.read_bytes()
                kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if target.name == "index.html":
                    script = app.bridge().replace("</", "<\\/")
                    content = content.replace(b"<head>", ("<head><script>" + script + "</script>").encode(), 1)
                    kind = "text/html; charset=utf-8"
                self.reply(200, content, kind)

            def do_POST(self):
                if urlsplit(self.path).path != "/__python__/invoke" or not self.authorized():
                    return self.reply(403, {"error": "window token required"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 90 * 1024 * 1024:
                        raise ValueError("invalid window request size")
                    request = json.loads(self.rfile.read(length))
                    args = request.get("args", {})
                    if not isinstance(args, dict):
                        raise ValueError("command args must be an object")
                    result = app.invoke(request["command"], args)
                    self.reply(200, {"result": result})
                except (OSError, ValueError, KeyError, RuntimeError, TimeoutError) as exc:
                    self.reply(400, {"error": str(exc)})

        return Handler

    def serve(self):
        self.thread = threading.Thread(target=self.server.serve_forever, name="helene-window", daemon=True)
        self.thread.start()
        self.backup_thread = threading.Thread(target=self._backups, name="helene-essential-backup", daemon=True)
        self.backup_thread.start()

    def _backups(self):
        from localharness import essential_backup
        while not self.stopping.wait(1800):
            try:
                if essential_backup.due(self.root):
                    essential_backup.snapshot(self.root, essential_backup.destination(self.root), "auto", self.stopping.is_set)
            except (OSError, ValueError, InterruptedError) as exc:
                self.event("backup-error", {"error": str(exc)})

    def close(self):
        try:
            self.stopping.set()
            if self.voice_keys:self.voice_keys.close()
            self.backup_thread.join()
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
            if self.host:
                self.host.close()
        finally:
            self.control_file.unlink(missing_ok=True)


def window_geometry(screen):
    frame = screen.frame if sys.platform == "win32" else None
    left = frame.X if frame is not None else screen.x
    top = frame.Y if frame is not None else screen.y
    available_width = frame.Width if frame is not None else screen.width
    available_height = frame.Height if frame is not None else screen.height
    width = min(1360, max(1, available_width - 48))
    height = min(880, max(1, available_height - 48))
    return {"width": width, "height": height,
            "x": left + (available_width - width) // 2,
            "y": top + (available_height - height) // 2}


def bind_window_state(app):
    def shown():
        if sys.platform == "win32":
            from System import Action
            from System.Windows.Forms import Screen
            native = app.window.native
            def set_bounds():
                native.MaximizedBounds = Screen.FromHandle(native.Handle).WorkingArea
            native.Invoke(Action(set_bounds))
            set_window_icon(app)
            from python_tray import install
            install(app)
        app.publish_control()

    def maximized():
        app.window_maximized = True

    def restored():
        app.window_maximized = False

    app.window.events.maximized += maximized
    app.window.events.restored += restored
    app.window.events.shown += shown


def set_window_icon(app):
    if sys.platform!='win32':return
    from System import Action
    from System.Drawing import Icon,Size
    path=app.program/'app/resources/python-app.ico'
    if not path.is_file():return
    def install():
        app._window_icon=Icon(str(path),Size(256,256))
        app.window.native.Icon=app._window_icon
    app.window.native.Invoke(Action(install))


def bind_native_reload(app):
    """Intercept WebView2 accelerators without enabling unsafe browser reload.

    pywebview's loaded/shown events run off the UI thread. Install and execute
    on the WinForms UI thread; never wait for JavaScript inside an accelerator
    callback (WebView2 blocks renderer execution until that callback returns).
    The HTTP-injected bridge owns persistence and is recreated each navigation.
    """
    if sys.platform != 'win32':
        return

    def loaded():
        from System import Action
        from System.Windows.Forms import Control, Keys
        from System.Reflection import BindingFlags
        form = app.window.native

        def install():
            if getattr(app, '_native_reload_bound', False):
                return
            view = form.browser.webview
            if view.CoreWebView2 is None:
                return  # loaded will retry once initialization completes

            def matches(key, modifiers):
                return ((key == 116 and modifiers == getattr(Keys, 'None')) or
                        (key == 82 and modifiers == Keys.Control))

            def request():
                # Always defer past the native input callback. Missing bridge is
                # a no-op, NOT CoreWebView2.Reload(): never bypass draft safety.
                form.BeginInvoke(Action(lambda: view.CoreWebView2.ExecuteScriptAsync(
                    "window.__HELENE__?.requestReload?.();")))

            def accelerator(sender, event):
                if str(event.KeyEventKind) not in ('KeyDown', 'SystemKeyDown'):
                    return
                if not matches(event.VirtualKey, Control.ModifierKeys):
                    return
                event.Handled = True
                if not event.PhysicalKeyStatus.WasKeyDown:
                    request()

            def form_key(sender, event):
                # Fallback when native chrome, rather than renderer, has focus.
                if view.ContainsFocus or not matches(int(event.KeyCode), event.Modifiers):
                    return
                event.Handled = True
                event.SuppressKeyPress = True
                request()

            # The bundled WebView2 WinForms SDK exposes no public controller
            # property. Resolve its verified backing field; do not modify the
            # vendored pywebview or turn on all browser accelerators.
            field = view.GetType().GetField('_coreWebView2Controller',
                                           BindingFlags.Instance | BindingFlags.NonPublic)
            if field is None:
                raise RuntimeError('Unsupported WebView2 SDK: reload controller field missing')
            controller = field.GetValue(view)
            controller.AcceleratorKeyPressed += accelerator
            form.KeyPreview = True
            form.KeyDown += form_key
            app._native_reload_bound = (accelerator, form_key)

        form.Invoke(Action(install))

    app.window.events.loaded += loaded


def migration_proof(host, root):
    from localharness import agents
    proof = host.readiness(timeout=60, require_runner=True,
                           agent=agents.find(root, agents.BASE_ID))
    proof['agents'] = [host.readiness(timeout=60, require_runner=True, agent=agent)
                       for agent in agents.raisable(root)]
    return proof


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--program", type=Path)
    parser.add_argument("--agent", default="")
    parser.add_argument("--panic", action="store_true", help="latch owner Stop without starting the application")
    parser.add_argument("--notify", action="store_true", help="show the emergency-stop result")
    parser.add_argument("--migration", default="", help="confirm this prepared migration after the real window loads")
    parser.add_argument('--migration-wizard', action='store_true', help='open the offline profile migration wizard before starting any agent')
    parser.add_argument("--browser", action="store_true", help="open the same Python-owned UI in the system browser")
    parser.add_argument("--launch-intent", choices=("manual", "automatic"), default="manual")
    args = parser.parse_args(argv)
    if args.panic:
        from localharness import owner_stop
        owner_stop.latch_client('shortcut', 'owner emergency stop')
        if args.notify and os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, 'Emergency Stop requested. Automatic starts remain paused.', 'Hélène', 0)
        return 0
    os.environ['PYTHONNOUSERSITE']='1'
    root = args.root.resolve()
    program = (args.program or root).resolve()
    from localharness import owner_stop
    if args.launch_intent == "automatic" and owner_stop.stopped():
        return 0
    if program != root:
        raise ValueError("Program and configuration root must currently match; agent data may live elsewhere via absolute tree")
    if sys.platform=='win32' and not args.migration:
        from localharness import portable_start
        incomplete=root/'python-migration.json'
        needs_migration=incomplete.exists() and json.loads(incomplete.read_text(encoding='utf-8')).get('state') not in ('completed','new_app_started')
        if args.migration_wizard or needs_migration or (not (root/'helene.json').exists() and portable_start.previous_installations(root)):
            from python_migration_window import main as migration_window
            return migration_window(['--root',str(root)])
        portable_start.initialize(root)
    ui = program / "app" / "static"
    if not (ui / "index.html").is_file():
        raise FileNotFoundError(f"Нет сборки окна: {ui}")
    pending=root/'python-migration.json'
    interrupted=pending.is_file() and json.loads(pending.read_text(encoding='utf-8')).get('state')=='preserving'
    from localharness.essential_backup import load
    fresh=(root/'helene.json').is_file() and load(root/'helene.json').get('setup_complete') is False and not pending.exists()
    if sys.platform.startswith('linux') and (args.migration_wizard or interrupted or (fresh and not args.browser)):
        from localharness.migration_wizard import run
        if not run(root):return 0
    elif interrupted:
        raise RuntimeError('Interrupted profile copy: resume migration before starting the application')
    if pending.is_file() and not args.migration:
        migrated=json.loads(pending.read_text(encoding='utf-8'))
        if migrated.get('state')=='prepared':args.migration=migrated['id']
    app = Application(root, ui, None, program)
    app.migration_id=args.migration
    app.browser_mode=args.browser
    host = Controller(root, app.event, app.origin, args.agent)
    host.browser_mode=args.browser
    try:
        host.start()
        from localharness import owner_stop
        if not owner_stop.stopped():
            host.readiness(timeout=30)
        app.channel = host.channel()
        app.host = host
    except BaseException:
        if host:
            host.close()
        raise
    app.serve()
    print('HELENE_WINDOW_ORIGIN', app.origin, flush=True)
    try:
        if args.browser:
            import webbrowser
            app.publish_control()
            webbrowser.open(app.origin)
            app.stopping.wait()
        else:
            import webview
            webview.settings['DRAG_REGION_SELECTOR'] = '#chrome, .chrome-drag, .rail-sign'
            webview.settings['DRAG_REGION_DIRECT_TARGET_ONLY'] = True
            app.window = webview.create_window("Hélène", app.origin, **window_geometry(webview.screens[0]),
                                               frameless=True, easy_drag=False, text_select=True,
                                               background_color="#1d1914")
            bind_window_state(app)
            bind_native_reload(app)
            # The shared renderer's python_ready is the sole confirmation
            # writer for native and browser windows alike.
            icon=program/('app/resources/python-app.png' if sys.platform.startswith('linux') else 'app/resources/python-app.ico')
            webview.start(gui='qt' if sys.platform.startswith('linux') else None,
                          private_mode=False,storage_path=str(root/'.webview'),icon=str(icon) if icon.is_file() else None)
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
    if app.relaunch:
        arguments=[a for a in sys.argv[1:] if a!='--migration-wizard']
        if app.migration_wizard:arguments.append('--migration-wizard')
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *arguments])
    return 0


def main(argv=None):
    # Console code pages may not represent the product name or a path in an
    # error. Reporting/help must not turn into a startup failure and GUI alert.
    # Keep the caller's encoding; only unrepresentable characters are escaped.
    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(errors="backslashreplace")
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main(arguments)
    except Exception as exc:
        import traceback
        root = Path(__file__).resolve().parents[1]
        if "--root" in arguments:
            position = arguments.index("--root") + 1
            if position < len(arguments):
                root = Path(arguments[position]).resolve()
        log = root / "startup-error.log"
        detail = "".join(traceback.format_exception(exc))
        try:
            with log.open("a", encoding="utf-8") as output:
                output.write(detail + "\n")
            message = f"Hélène could not start.\n\n{exc}\n\nDetails: {log}"
        except OSError as log_error:
            message = f"Hélène could not start.\n\n{exc}\n\nStartup log unavailable: {log_error}"
        print(detail, file=sys.stderr)
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Hélène — startup failed", 0x10)
        elif sys.platform == "darwin":
            import subprocess
            subprocess.run(["osascript", "-e", 'on run argv\ndisplay alert "Hélène — startup failed" message (item 1 of argv) as critical\nend run', message], check=False)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
