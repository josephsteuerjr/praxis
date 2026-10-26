#!/usr/bin/env python3
"""Finder migration window; imports no controller and starts no old agent."""
import argparse
from pathlib import Path
import sys
import threading

from python_app import Application
from localharness.mac_migration import Session


class MigrationWindow(Application):
    def __init__(self,root):
        super().__init__(root,root/'app/resources',None,root)
        self.session=Session(root)

    def invoke(self,command,args):
        if command=='migration_status':return self.session.status()
        if command=='migration_choose':
            if self.window is None:raise RuntimeError('Folder selection needs the native window')
            import webview
            chosen=self.window.create_file_dialog(webview.FOLDER_DIALOG)
            return str(chosen[0]) if chosen else None
        if command=='migration_prepare':
            return self.session.prepare(args.get('old',''),args.get('data',''),
                closed=args.get('closed'),former=args.get('former',''))
        if command=='migration_start':return self.session.start()
        if command=='python_ready':return {'confirmed':False}
        if command in ('python_window','appearance_read'):return super().invoke(command,args)
        raise ValueError('Unavailable migration command')

    def _handler(self):
        base=super()._handler();app=self
        class Handler(base):
            def do_GET(self):
                if self.path=='/':
                    from urllib.parse import urlsplit
                    if self.headers.get('Host')!=urlsplit(app.origin).netloc:return self.reply(403,{'error':'foreign host'})
                    content=(app.ui/'mac-migration.html').read_text(encoding='utf-8')
                    content=content.replace('<head>','<head><script>'+app.bridge().replace('</','<\\/')+'</script>',1)
                    return self.reply(200,content.encode(),'text/html; charset=utf-8')
                return super().do_GET()
        return Handler

    def serve(self):
        # No periodic backup loop against an unconfigured migration destination.
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.backup_thread=threading.Thread(target=lambda:None);self.backup_thread.start()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    args=p.parse_args(argv)
    if sys.platform!='darwin':raise RuntimeError('Finder profile migration is macOS-only')
    import webview
    app=MigrationWindow(args.root.resolve());app.serve()
    try:
        from python_app import window_geometry
        geometry=window_geometry(webview.screens[0])
        geometry.update(width=min(780,geometry['width']),height=min(760,geometry['height']),min_size=(480,440))
        app.window=webview.create_window('Hélène · Перенос с прежней версии',app.origin,
            **geometry,background_color='#f7eddf',text_select=True)
        app.window.events.shown += app.publish_control
        webview.start(private_mode=False,storage_path=str(app.root/'.migration-webview'))
    finally:app.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
