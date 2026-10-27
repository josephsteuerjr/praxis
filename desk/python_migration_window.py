"""Python migration UI shipped inside the portable archive; no bootstrap download."""
import argparse,json,os,subprocess,sys,threading,time
from pathlib import Path
from python_app import Application,window_geometry
from localharness import portable_start

class MigrationWindow(Application):
    def __init__(self,root,old='',target=''):
        super().__init__(root,root/'app/resources',None,root)
        self.old=old;self.target=target or str(root);self.running=threading.Lock();self.last_progress=0.0
        self.control_file=root/f'.python-migrator-{os.getpid()}.json'
    def invoke(self,command,args):
        if command=='migration_defaults':
            candidates=portable_start.previous_installations(self.root)
            return {'old':self.old or (str(candidates[0]) if candidates else ''),'target':self.target,'mode':'migrate',
                    'default_target':str(self.root),'portable':True,'version':json.loads((self.root/'python-app.json').read_text(encoding='utf-8'))['version']}
        if command=='migration_pick_path':
            import webview
            chosen=self.window.create_file_dialog(webview.FOLDER_DIALOG)
            return str(chosen[0]) if chosen else None
        if command=='migration_fresh':
            portable_start.initialize(self.root)
            self.open_app();return {'ok':True}
        if command=='migration_run':
            if not self.running.acquire(blocking=False):raise RuntimeError('Перенос уже выполняется')
            try:
                old=Path(args.get('old','')).resolve()
                target=Path(args.get('target') or self.root).resolve()
                source_mode=args.get('sourceMode','preserve')
                if source_mode not in ('preserve','clean'):raise ValueError('Choose preserve or clean migration source')
                if not (old/'helene.json').is_file():raise ValueError('Выбери прежнюю папку Hélène')
                from localharness import migration_windows
                import ctypes
                if migration_windows.service(old) and not ctypes.windll.shell32.IsUserAnAdmin():
                    parameters=subprocess.list2cmdline(['-s',str(self.root/'app/python_migration_window.py'),'--root',str(self.root),'--old',str(old),'--target',str(target)])
                    outcome=ctypes.windll.shell32.ShellExecuteW(None,'runas',str(self.root/'runtime/pythonw.exe'),parameters,str(self.root),0)
                    if outcome<=32:raise RuntimeError('Windows не подтвердил права для переноса прежней службы')
                    self.event('exit',{});return {'elevated':True}
                import python_migrate
                with python_migrate.installation_lock(target):
                    return python_migrate.migrate(old,self.root,target,target/'profile',stop_service=True,
                        prepared=target==self.root,portable=True,source_mode=source_mode,
                        progress=self.progress,extensions=args.get('extensions') or [])
            finally:self.running.release()
        if command=='python_ready':return {'confirmed':False}
        if command in ('python_window','appearance_read'):return super().invoke(command,args)
        raise ValueError('Unavailable migration command')
    def progress(self,value):
        value=value if isinstance(value,dict) else {'stage':value};now=time.monotonic()
        if now-self.last_progress>=.1 or value.get('stage')in ('completed','starting_new','stopping_old'):
            self.event('migration-progress',value);self.last_progress=now
    def open_app(self):
        subprocess.Popen([str(self.root/'runtime/pythonw.exe'),'-s',str(self.root/'app/python_app.py'),'--root',str(self.root)],cwd=self.root,creationflags=0x08000000)
        self.event('exit',{})
    def _handler(self):
        base=super()._handler();app=self
        class Handler(base):
            def do_GET(self):
                if self.path=='/':
                    from urllib.parse import urlsplit
                    if self.headers.get('Host')!=urlsplit(app.origin).netloc:return self.reply(403,{'error':'foreign host'})
                    content=(app.ui/'python-migration.html').read_text(encoding='utf-8')
                    content=content.replace('<head>','<head><script>'+app.bridge().replace('</','<\\/')+'</script>',1)
                    return self.reply(200,content.encode(),'text/html; charset=utf-8')
                return super().do_GET()
        return Handler
    def serve(self):
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.backup_thread=threading.Thread(target=lambda:None);self.backup_thread.start()

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--old',default='');p.add_argument('--target',default='');args=p.parse_args(argv)
    if sys.platform!='win32':raise RuntimeError('This entry is for Windows')
    import webview
    root=args.root.resolve();app=MigrationWindow(root,args.old,args.target);app.serve()
    try:
        geometry=window_geometry(webview.screens[0]);geometry.update(width=min(720,geometry['width']),height=min(720,geometry['height']))
        app.window=webview.create_window('Hélène · перенос',app.origin,**geometry,min_size=(420,360),frameless=True,easy_drag=False,text_select=True,background_color='#f8eee0')
        app.window.events.shown+=app.publish_control
        from python_app import set_window_icon
        app.window.events.shown+=lambda:set_window_icon(app)
        app.window.events.maximized+=lambda:setattr(app,'window_maximized',True)
        app.window.events.restored+=lambda:setattr(app,'window_maximized',False)
        webview.start(private_mode=False,storage_path=str(root/'.migration-webview'),icon=str(root/'app/resources/python-app.ico'))
    finally:app.close()
    return 0

if __name__=='__main__':raise SystemExit(main())
