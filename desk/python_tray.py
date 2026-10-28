"""Windows notification icon owned by the Python window's WinForms thread."""
import sys


def install(app):
    if app.tray is not None or sys.platform != 'win32':
        return
    from System import Action
    from System.Drawing import Icon, SystemIcons
    from System.Windows.Forms import CloseReason, ContextMenuStrip, NotifyIcon, ToolStripMenuItem

    native = app.window.native

    def create():
        menu = ContextMenuStrip()
        tray = NotifyIcon()
        image = None

        def restore(sender, event):
            native.Show()
            if native.WindowState.ToString() == 'Minimized':
                app.window.restore()
            native.Activate()

        def exit_app(sender, event):
            app.exiting = True
            native.Close()

        def closing(sender, event):
            if event.CloseReason == CloseReason.UserClosing and not app.exiting and not app.relaunch:
                native.Hide()
                event.Cancel = True

        def closed(sender, event):
            tray.Visible = False
            tray.Dispose()
            menu.Dispose()
            if image is not None:
                image.Dispose()
            app.tray = None

        try:
            icon_path = app.program / 'app/resources/python-app.ico'
            image = Icon(str(icon_path)) if icon_path.is_file() else None
            tray.Icon = image if image is not None else SystemIcons.Application
            tray.Text = 'Hélène'
            open_item = ToolStripMenuItem('Open Hélène')
            exit_item = ToolStripMenuItem('Exit')
            open_item.Click += restore
            exit_item.Click += exit_app
            menu.Items.Add(open_item)
            menu.Items.Add(exit_item)
            tray.ContextMenuStrip = menu
            tray.DoubleClick += restore
            tray.Visible = True
            native.FormClosing += closing
            native.FormClosed += closed
            app.tray = tray
        except Exception as error:
            tray.Dispose()
            menu.Dispose()
            if image is not None:
                image.Dispose()
            app.event('tray-error', {'error': str(error)})

    native.Invoke(Action(create))
