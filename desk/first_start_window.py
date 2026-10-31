"""Minimal first-start progress and creation screen, without an agent scheduler."""
import copy
import json
import os
from pathlib import Path
import subprocess
import threading
import tempfile
from urllib.parse import urlsplit

from python_app import Application,window_geometry,set_window_icon
from localharness import first_start


class FirstStartWindow(Application):
    def __init__(self,root,places):
        super().__init__(root,root/'app/resources',None,root)
        self.places=places;self.worker=None;self.opening=False
        self.value={'stage':'starting','label':'Подготовка'}
        self.value_lock=threading.Lock()

    def publish(self,value):
        with self.value_lock:self.value=dict(value)

    def status(self):
        with self.value_lock:return copy.deepcopy(self.value)

    def prepare(self):
        try:
            result=first_start.prepare(self.root,places=self.places,progress=self.publish)
            first_start.ensure_config(self.root,places=self.places)
            self.publish({'stage':'ready' if result['agent'] else 'create','label':'Готово'})
        except Exception as error:
            self.publish({'stage':'error','label':'Не удалось завершить подготовку','error':str(error)})

    def invoke(self,command,args):
        if command=='first_start':
            if self.worker is None:
                self.worker=threading.Thread(target=self.prepare,name='helene-first-start',daemon=False);self.worker.start()
            return self.status()
        if command=='first_status':return self.status()
        if command=='first_create':
            result=first_start.create_agent(self.root,args,places=self.places)
            self.publish({'stage':'ready','label':'Открываем агента'});return result
        if command=='first_open':
            if self.opening:return {'opening':True}
            if not first_start.ready(self.root,self.places) or not first_start.agent_exists(self.places.data):
                raise ValueError('Сначала заверши создание агента')
            self.opening=True
            try:
                # A fresh launcher owns normal window readiness. No migration
                # job, observation receipt or previous supervisor participates.
                process=subprocess.Popen([str(self.root/'helene.exe')],cwd=self.root,
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if process.wait(timeout=100):raise RuntimeError('Обычное окно не подтвердило запуск')
                self.exiting=True;self.exit_session=False
                if self.window is not None:self.window.destroy()
                return {'opened':True}
            finally:self.opening=False
        if command=='python_ready':return {'confirmed':True}
        if command in ('python_window','appearance_read'):return super().invoke(command,args)
        raise ValueError('Unavailable first-start action')

    def _handler(self):
        parent=super()._handler();app=self
        class Handler(parent):
            def do_GET(self):
                if urlsplit(self.path).path=='/':
                    if self.headers.get('Host')!=urlsplit(app.origin).netloc:return self.reply(403,{'error':'foreign host'})
                    body=(app.ui/'first-start.html').read_text(encoding='utf-8')
                    body=body.replace('<head>','<head><script>'+app.bridge().replace('</','<\\/')+'</script>',1)
                    return self.reply(200,body.encode(),'text/html; charset=utf-8')
                return super().do_GET()
        return Handler

    def serve(self):
        self.thread=threading.Thread(target=self.server.serve_forever,name='helene-first-window',daemon=True)
        self.thread.start()
        self.backup_thread=threading.Thread(target=lambda:None);self.backup_thread.start()


def main(root,*,places=None):
    import webview
    root=Path(root).resolve();app=FirstStartWindow(root,places or first_start.Places.current());app.serve()
    try:
        webview.settings['DRAG_REGION_SELECTOR']='.brand'
        webview.settings['DRAG_REGION_DIRECT_TARGET_ONLY']=True
        geometry=window_geometry(webview.screens[0]);geometry.update(width=min(500,geometry['width']),height=min(650,geometry['height']))
        app.window=webview.create_window('Hélène',app.origin,**geometry,min_size=(360,420),
            frameless=True,easy_drag=False,text_select=True,background_color='#f8eee0')
        from localharness.bootstrap_ready import signal
        def loaded():
            app.ui_ready=True;app.publish_control();signal()
        app.window.events.loaded+=loaded
        app.window.events.shown+=lambda:set_window_icon(app)
        with tempfile.TemporaryDirectory(prefix='agent-view-',ignore_cleanup_errors=True) as storage:
            webview.start(private_mode=True,storage_path=storage)
    finally:
        # Preparation is mechanical and must settle even if the view closes.
        if app.worker is not None:app.worker.join()
        app.close()
        try:app.control_file.parent.rmdir()
        except OSError:pass
    return 0
