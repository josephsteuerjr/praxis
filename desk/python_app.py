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

from localharness.python_controller import Controller


class Application:
    def __init__(self, root: Path, ui: Path, host=None, program: Path | None = None):
        self.root, self.ui, self.host = root, ui.resolve(), host
        self.program = program or root
        self.window = None
        self.window_maximized = False
        self.relaunch = False
        self.tray = None
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
        if command == "window_preferences":
            key, value = args.get("key"), args.get("value")
            if not isinstance(key, str) or len(key) > 512 or not self.preference_key(key):
                raise ValueError("Invalid window preference key")
            if value is not None and (not isinstance(value, str) or len(value) > 1024 * 1024):
                raise ValueError("Invalid window preference value")
            with self.lock:
                values = self.preferences()
                if value is None:
                    values.pop(key, None)
                else:
                    values[key] = value
                self.root.mkdir(parents=True, exist_ok=True)
                temporary = self.root / 'window-preferences.json.tmp'
                temporary.write_text(json.dumps(values, ensure_ascii=False), encoding='utf-8')
                os.replace(temporary, self.root / 'window-preferences.json')
            return {"ok": True}
        if command == "components_status":
            from localharness import app_components
            return app_components.status(self.program, self.root)
        if command == "restart_self":
            self.relaunch = True
            self.event("exit", {})
            return None
        if command == "component_install":
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

    @staticmethod
    def preference_key(key):
        return key.startswith(('frame.', 'helene-', 'helene.', 'settings.', 'draft.'))

    def preferences(self):
        path = self.root / 'window-preferences.json'
        if not path.exists():
            return {}
        values = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(values, dict):
            raise ValueError('Invalid saved window preferences')
        return {key: value for key, value in values.items()
                if self.preference_key(key) and isinstance(value, str)}

    def bridge(self):
        return """window.DESK_CONFIG_OVERRIDE = %s;
window.__HELENE__ = {shell:'python', platform:%s,
 invoke: async (command,args={}) => {
  const r = await fetch('/__python__/invoke', {method:'POST',headers:{
   'Content-Type':'application/json','X-Helene-Window':%s},body:JSON.stringify({command,args})});
  const v = await r.json(); if(!r.ok) throw new Error(v.error); return v.result;
 }, win(action){this.invoke('python_window',{action}).catch(console.error)}};
(function preferences(saved){
 const store=window.localStorage, originalSet=Storage.prototype.setItem, originalRemove=Storage.prototype.removeItem;
 for(const [key,value] of Object.entries(saved)) originalSet.call(store,key,value);
 let pending=Promise.resolve();
 const persist=(key,value)=>{if(/^(frame[.]|helene[-.]|settings[.]|draft[.])/.test(key))
  pending=pending.then(()=>window.__HELENE__.invoke('window_preferences',{key,value})).catch(error=>{
   console.error(error);window.dispatchEvent(new CustomEvent('helene:preference-error',{detail:String(error)}));});};
 Storage.prototype.setItem=function(key,value){originalSet.call(this,key,value);if(this===store)persist(String(key),String(value));};
 Storage.prototype.removeItem=function(key){originalRemove.call(this,key);if(this===store)persist(String(key),null);};
})(%s);
(async function events(){try {
 const r=await fetch('/__python__/events',{headers:{'X-Helene-Window':%s}});
 if(r.ok) for(const e of await r.json()) {
  if(e.name==='replace-window') location.replace('/');
  else if(e.name==='retire-window') location.replace('/__python__/retired?id='+encodeURIComponent(e.data.id));
  else if(e.name==='exit') window.__HELENE__.win('exit');
  else window.dispatchEvent(new CustomEvent('helene:'+e.name,{detail:e.data}));
 }
} catch(e){console.error(e)} setTimeout(events,500)})();
""" % (json.dumps(self.channel, ensure_ascii=False), json.dumps('windows' if sys.platform=='win32' else sys.platform),
       json.dumps(self.token), json.dumps(self.preferences(), ensure_ascii=False), json.dumps(self.token))

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
<h1>Hélène · Python</h1><p>Install the native components from the included offline bundle.
Their exact source and toolchain stay beside the application. Your agent data is not replaced.</p>
<div id="components"></div><button id="start">Start Hélène</button><pre id="status"></pre>
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
        self.stopping.set()
        self.backup_thread.join()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        if self.host:
            self.host.close()


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
            from python_tray import install
            install(app)

    def maximized():
        app.window_maximized = True

    def restored():
        app.window_maximized = False

    app.window.events.maximized += maximized
    app.window.events.restored += restored
    app.window.events.shown += shown


def migration_proof(host, root):
    from localharness import agents
    proof = host.readiness(timeout=60, require_runner=True,
                           agent=agents.find(root, agents.BASE_ID))
    proof['agents'] = [host.readiness(timeout=60, require_runner=True, agent=agent)
                       for agent in agents.raisable(root)]
    return proof


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--program", type=Path)
    parser.add_argument("--agent", default="")
    parser.add_argument("--panic", action="store_true", help="latch owner Stop without starting the application")
    parser.add_argument("--notify", action="store_true", help="show the emergency-stop result")
    parser.add_argument("--migration", default="", help="confirm this prepared migration after the real window loads")
    parser.add_argument("--browser", action="store_true", help="open the same Python-owned UI in the system browser")
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
    if program != root:
        raise ValueError("Program and configuration root must currently match; agent data may live elsewhere via absolute tree")
    ui = program / "app" / "static"
    if not (ui / "index.html").is_file():
        raise FileNotFoundError(f"Нет сборки окна: {ui}")
    app = Application(root, ui, None, program)
    host = Controller(root, app.event, app.origin, args.agent)
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
            if args.migration:
                def migration_loaded():
                    from localharness import app_migration
                    try:
                        if owner_stop.stopped():
                            # Installing a new control window is not permission
                            # to undo the owner's Stop or resume model work.
                            proof={'stopped':True,'root':str(root)}
                        else:
                            proof = migration_proof(app.host, root)
                        app_migration.confirm_started(root, args.migration, proof=proof)
                    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                        app.event('migration-error',{'error':str(exc)})
                app.window.events.loaded += migration_loaded
            icon=program/'app/resources/python-app.ico'
            webview.start(private_mode=False,icon=str(icon) if icon.is_file() else None)
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
    if app.relaunch:
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
