"""Python migration UI shipped inside the portable archive; no bootstrap download."""
import argparse,json,os,subprocess,sys,threading,time
from pathlib import Path
from python_app import Application,window_geometry
from localharness import portable_start

class MigrationWindow(Application):
    def __init__(self,root,old='',target='',source_mode='clean',force_extensions=False):
        super().__init__(root,root/'app/resources',None,root)
        self.resume_fresh=False
        if not old and (root/'helene.json').is_file():
            from localharness.package_notice import read
            pending=read(root/'python-migration.json')
            interrupted=pending.get('state') not in (None,'completed','new_app_started','retirement_incomplete')
            if interrupted and pending.get('kind')=='fresh':
                self.resume_fresh=True
            elif interrupted and pending.get('old_root'):
                old=pending['old_root']
            else:
                old=str(root)
                target=target or str(root.with_name(root.name+'-new'))
        self.old=old;self.target=target or str(root);self.running=threading.Lock();self.last_progress=0.0
        from localharness.program_state import window_control
        self.control_file=window_control(root,os.getpid(),role='migrator',create=True)
        self.source_mode=source_mode;self.review_cancelled=threading.Event()
        self.force_extensions=force_extensions
    def invoke(self,command,args):
        if command=='migration_defaults':
            from localharness import windows_installation as installation
            from localharness.portable_profile import default_data as new_data_default
            candidates=portable_start.previous_installations(self.root)
            old='' if self.resume_fresh else self.old or (str(candidates[0]) if candidates else '')
            from localharness.profile_reconnect import discover as reconnect_data
            reconnected=reconnect_data() if not old and not self.resume_fresh else None
            target=self.target
            data=str(reconnected) if reconnected else installation.default_data(self.root if self.resume_fresh else old)
            if old and Path(old)==Path(target) and not (Path(old)/'python-app.json').is_file():target=str(Path(old).with_name(Path(old).name+'-new'))
            from localharness.migration_job import existing_observation
            try:self.admitted_request=existing_observation(self.root,target,data,fresh=not bool(old or reconnected))
            except (OSError,ValueError):self.admitted_request=None
            return {'old':old,'target':target,'data':data,'mode':'connect' if old or reconnected else 'fresh',
                    'python_update':bool(old and installation.is_python_program(old)),
                    'data_destination_default':installation.relocation_default(data),
                    'data_transfer_recommended':installation.relocation_recommended(data) or bool(old and installation.is_python_program(old) and Path(data).resolve().is_relative_to(Path(old).resolve())),
                    'default_target':str(self.root),'policy':installation.POLICY,'version':installation.version(self.root),
                    'source_mode':self.source_mode,'can_restore':(self.root/'helene.json').is_file(),
                    'data_default':str(new_data_default()),'observed_job':(self.admitted_request or {}).get('id')}
        if command=='migration_restore':
            from localharness.upstream_recovery import launch
            if args.get('confirmed') is not True:raise ValueError('Подтверди возврат к upstream-коду')
            return launch(self.root)
        if command=='migration_recovery_status':
            from localharness.upstream_recovery import receipt
            from localharness.package_notice import read
            return read(receipt(self.root,args.get('operation','')))
        if command=='migration_status':
            from localharness.migration_job import observation
            value=getattr(self,'admitted_request',None)
            if not value or args.get('id')!=value['id']:raise ValueError('No matching admitted migration in this window')
            return observation(self.root,value)
        if command=='migration_inspect':
            from localharness import windows_installation as installation
            from localharness.migration_job import failed_session
            old=Path(args.get('old','')).resolve()
            if not (old/'helene.json').is_file():raise ValueError('Настройки прежней программы не найдены. Если она уже удалена, очисти поле «Прежняя программа» и укажи существующую папку данных.')
            data=installation.default_data(old)
            return {'data':data,'version':installation.version(old),'failed_session':failed_session(old),
                    'python_update':installation.is_python_program(old),
                    'data_destination_default':installation.relocation_default(data),
                    'data_transfer_recommended':installation.relocation_recommended(data) or (installation.is_python_program(old) and Path(data).resolve().is_relative_to(old))}
        if command=='migration_release_failed_session':
            from localharness.migration_job import release_failed_session
            if args.get('confirmed') is not True:raise ValueError('Confirm stopping the failed migration session')
            if not self.running.acquire(blocking=False):raise RuntimeError('Migration admission is already in progress')
            try:return release_failed_session(Path(args['old']),str(args['id']))
            finally:self.running.release()
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
                if old and not (old/'helene.json').is_file():raise ValueError('Выбери прежнюю папку Hélène или укажи только папку данных')
                if fresh:old=None
                from localharness import windows_installation as installation
                mode=args.get('sourceMode') or ('preserve' if old and installation.is_python_program(old) else 'clean')
                if mode not in ('preserve','clean') or (mode=='preserve' and not (old and installation.is_python_program(old))):
                    raise ValueError('Legacy migration installs clean code; Python updates may preserve edits')
                cancelled=getattr(self,'review_cancelled',None)
                if cancelled:cancelled.clear()
                self.progress('admitting')
                from localharness.migration_job import admit
                result=admit(self.root,target,old=old,data=args.get('data') or (None if fresh else installation.default_data(old)),
                    fresh=fresh,remove_old=old is not None,progress=self.progress,
                    source_mode=mode,force_extensions=getattr(self,'force_extensions',False),
                    **({'data_destination':args['dataDestination']} if args.get('dataDestination') else {}))
                self.admitted_job=result['job']['id']
                self.admitted_request=result['job']
                self.progress({'stage':'admitted','job_id':self.admitted_job})
                return result
            finally:self.running.release()
        if command=='python_ready':return {'confirmed':False}
        if command=='python_window' and args.get('action') in ('close','exit') and self.running.locked():
            self.progress('admitting');return {'admission_pending':True}
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
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--old',default='');p.add_argument('--target',default='');p.add_argument('--admission',type=Path);p.add_argument('--source-mode',choices=('clean',),default='clean');p.add_argument('--force-extensions',action='store_true');args=p.parse_args(argv)
    if sys.platform.startswith('linux'):os.environ.setdefault('QT_NO_GLIB','1')
    import webview
    root=args.root.resolve();app=MigrationWindow(root,args.old,args.target,args.source_mode,args.force_extensions);app.serve()
    try:
        geometry=window_geometry(webview.screens[0]);geometry.update(width=min(720,geometry['width']),height=min(720,geometry['height']))
        app.window=webview.create_window('Hélène · установка',app.origin,**geometry,min_size=(420,360),frameless=True,easy_drag=False,text_select=True,background_color='#f8eee0')
        app.window.events.shown+=app.publish_control
        from localharness.bootstrap_ready import signal
        app.window.events.loaded+=lambda:signal()
        if args.admission:
            if args.admission.resolve()!=root.parent/'updater-admitted.json' or not root.parent.name.startswith('.helene-update-'):
                raise ValueError('Unexpected legacy updater admission path')
            from localharness.package_notice import write
            app.window.events.shown+=lambda:write(args.admission,{'pid':os.getpid(),'target':str(Path(args.old).resolve()),'preparation_completed':False,'wizard_opened':True})
        from python_app import set_window_icon
        app.window.events.shown+=lambda:set_window_icon(app)
        app.window.events.maximized+=lambda:setattr(app,'window_maximized',True)
        app.window.events.restored+=lambda:setattr(app,'window_maximized',False)
        from localharness.program_state import directory
        webview.start(gui='qt' if sys.platform.startswith('linux') else None,private_mode=False,storage_path=str(directory(root,'migration-webview')),icon=(str(root/('app/resources/python-app.png' if sys.platform.startswith('linux') else 'app/resources/python-app.ico')) if (root/('app/resources/python-app.png' if sys.platform.startswith('linux') else 'app/resources/python-app.ico')).is_file() else None))
    finally:app.close()
    return 0

if __name__=='__main__':raise SystemExit(main())
