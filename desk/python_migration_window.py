"""Python migration UI shipped inside the portable archive; no bootstrap download."""
import argparse,json,os,subprocess,sys,threading,time
from pathlib import Path
from python_app import Application,window_geometry
from localharness import portable_start

class MigrationWindow(Application):
    def __init__(self,root,old='',target='',source_mode='preserve',force_extensions=False):
        super().__init__(root,root/'app/resources',None,root)
        self.resume_fresh=False
        if not old and (root/'helene.json').is_file():
            from localharness.package_notice import read
            pending=read(root/'python-migration.json')
            interrupted=pending.get('state') not in (None,'completed','new_app_started')
            if interrupted and pending.get('kind')=='fresh':
                self.resume_fresh=True
            elif interrupted and pending.get('old_root'):
                old=pending['old_root']
            else:
                old=str(root)
                target=target or str(root.with_name(root.name+'-new'))
        self.old=old;self.target=target or str(root);self.running=threading.Lock();self.last_progress=0.0
        self.control_file=root/f'.python-migrator-{os.getpid()}.json'
        self.source_mode=source_mode;self.review_cancelled=threading.Event()
        self.force_extensions=force_extensions
    def invoke(self,command,args):
        if command=='migration_defaults':
            from localharness import windows_installation as installation
            candidates=portable_start.previous_installations(self.root)
            old='' if self.resume_fresh else self.old or (str(candidates[0]) if candidates else '')
            target=self.target
            data=installation.default_data(self.root if self.resume_fresh else old)
            if old and Path(old)==Path(target) and not (Path(old)/'python-app.json').is_file():target=str(Path(old).with_name(Path(old).name+'-new'))
            return {'old':old,'target':target,'data':data,'mode':'connect' if old else 'fresh',
                    'default_target':str(self.root),'policy':installation.POLICY,'version':installation.version(self.root),
                    'source_mode':self.source_mode,'can_restore':(self.root/'helene.json').is_file()}
        if command=='migration_restore':
            from localharness.upstream_recovery import launch
            if args.get('confirmed') is not True:raise ValueError('Подтверди возврат к upstream-коду')
            return launch(self.root)
        if command=='migration_recovery_status':
            from localharness.upstream_recovery import receipt
            from localharness.package_notice import read
            return read(receipt(self.root,args.get('operation','')))
        if command=='migration_inspect':
            from localharness import windows_installation as installation
            old=Path(args.get('old','')).resolve()
            if not (old/'helene.json').is_file():raise ValueError('В этой папке нет настроек прежней Hélène')
            return {'data':installation.default_data(old),'version':installation.version(old)}
        if command=='migration_pick_path':
            import webview
            chosen=self.window.create_file_dialog(webview.FOLDER_DIALOG)
            return str(chosen[0]) if chosen else None
        if command in ('migration_install','migration_run','migration_fresh'):
            if not self.running.acquire(blocking=False):raise RuntimeError('Перенос уже выполняется')
            try:
                old=Path(args['old']).resolve() if args.get('old') else None
                target=Path(args.get('target') or self.root).resolve()
                fresh=command=='migration_fresh' or args.get('mode')=='fresh'
                if args.get('sourceMode') not in (None,'preserve','clean'):raise ValueError('Choose preserve or clean source')
                if old and not (old/'helene.json').is_file():raise ValueError('Выбери прежнюю папку Hélène или укажи только папку данных')
                if fresh:old=None
                from localharness import windows_installation as installation
                cancelled=getattr(self,'review_cancelled',None)
                if cancelled:cancelled.clear()
                return installation.run(self.root,target,old=old,data=args.get('data') or (None if fresh else installation.default_data(old)),
                    fresh=fresh,remove_old=args.get('removeOld') is True,progress=self.progress,
                    source_mode=args.get('sourceMode'),cancelled=cancelled,force_extensions=getattr(self,'force_extensions',False))
            finally:self.running.release()
        if command=='python_ready':return {'confirmed':False}
        if command=='python_window' and args.get('action') in ('close','exit') and self.running.locked():
            self.review_cancelled.set();self.progress('cancelling');return {'cancelling':True}
        if command in ('python_window','appearance_read'):return super().invoke(command,args)
        raise ValueError('Unavailable migration command')
    def progress(self,value):
        value=value if isinstance(value,dict) else {'stage':value};now=time.monotonic()
        if now-self.last_progress>=.1 or value.get('stage')in ('completed','starting_new','stopping_old'):
            self.event('migration-progress',value);self.last_progress=now
    def open_app(self):
        from localharness.update_process import interpreter_for,background_flags
        subprocess.Popen([str(interpreter_for(self.root)),'-s',str(self.root/'app/python_app.py'),'--root',str(self.root)],cwd=self.root,creationflags=background_flags())
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
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--old',default='');p.add_argument('--target',default='');p.add_argument('--admission',type=Path);p.add_argument('--source-mode',choices=('preserve','clean'),default='preserve');p.add_argument('--force-extensions',action='store_true');args=p.parse_args(argv)
    import webview
    root=args.root.resolve();app=MigrationWindow(root,args.old,args.target,args.source_mode,args.force_extensions);app.serve()
    try:
        geometry=window_geometry(webview.screens[0]);geometry.update(width=min(720,geometry['width']),height=min(720,geometry['height']))
        app.window=webview.create_window('Hélène · установка',app.origin,**geometry,min_size=(420,360),frameless=True,easy_drag=False,text_select=True,background_color='#f8eee0')
        app.window.events.shown+=app.publish_control
        if args.admission:
            if args.admission.resolve()!=root.parent/'updater-admitted.json' or not root.parent.name.startswith('.helene-update-'):
                raise ValueError('Unexpected legacy updater admission path')
            from localharness.package_notice import write
            app.window.events.shown+=lambda:write(args.admission,{'pid':os.getpid(),'target':str(Path(args.old).resolve()),'preparation_completed':False,'wizard_opened':True})
        from python_app import set_window_icon
        app.window.events.shown+=lambda:set_window_icon(app)
        app.window.events.maximized+=lambda:setattr(app,'window_maximized',True)
        app.window.events.restored+=lambda:setattr(app,'window_maximized',False)
        webview.start(gui='qt' if sys.platform.startswith('linux') else None,private_mode=False,storage_path=str(root/'.migration-webview'),icon=str(root/('app/resources/python-app.png' if sys.platform.startswith('linux') else 'app/resources/python-app.ico')))
    finally:app.close()
    return 0

if __name__=='__main__':raise SystemExit(main())
