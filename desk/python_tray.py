"""Windows notification icon owned by the Python window's WinForms thread."""
import sys


def _enable_taskbar_minimize(native):
    """Restore the shell's minimize contract without adding visible chrome.

    WinForms' FormBorderStyle.None (pywebview's frameless mode) removes
    WS_MINIMIZEBOX/WS_SYSMENU, which the shell uses for taskbar minimization.
    Restore these native capabilities rather than toggling on focus. Leave activation,
    owner, maximize and notification-icon behavior to their existing owners.
    Called only on the form's UI thread, including after handle recreation.
    """
    import ctypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos.restype = ctypes.c_int
    handle = native.Handle.ToInt64()
    ctypes.set_last_error(0)
    before = user32.GetWindowLongW(handle, -16)  # GWL_STYLE is a 32-bit LONG on both ABIs.
    if before == 0 and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    after = before | 0x00020000 | 0x00080000  # WS_MINIMIZEBOX requires WS_SYSMENU.
    if after != before:
        ctypes.set_last_error(0)
        if user32.SetWindowLongW(handle, -16, after) == 0 and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        # Recalculate cached frame styles, without moving, resizing or activating.
        if not user32.SetWindowPos(handle, None, 0, 0, 0, 0, 0x0037):
            raise ctypes.WinError(ctypes.get_last_error())
    return after & 0xffffffff


def install(app):
    if app.tray is not None or sys.platform != 'win32':
        return
    from System import Action
    from System.Drawing import Icon, SystemIcons
    from System.Windows.Forms import CloseReason, ContextMenuStrip, NotifyIcon, ToolStripMenuItem

    native = app.window.native

    def create():
        if not getattr(app, '_taskbar_minimize_bound', None):
            def refresh_handle():
                if native.IsDisposed or not native.IsHandleCreated:
                    return
                try:
                    _enable_taskbar_minimize(native)
                except OSError as error:
                    app.event('taskbar-error', {'error': str(error)})
            def handle_created(sender, event):
                # WinForms applies its own styles after raising HandleCreated.
                # Defer until that lifecycle step has finished.
                native.BeginInvoke(Action(refresh_handle))
            _enable_taskbar_minimize(native)
            native.HandleCreated += handle_created
            app._taskbar_minimize_bound = handle_created
        menu = ContextMenuStrip()
        tray = NotifyIcon()
        image = None

        def restore(sender, event):
            native.Show()
            if native.WindowState.ToString() == 'Minimized':
                # pywebview.restore() forces Normal, losing a maximized placement.
                import ctypes
                show = ctypes.WinDLL('user32', use_last_error=True).ShowWindow
                show.argtypes = [ctypes.c_void_p, ctypes.c_int]
                show.restype = ctypes.c_int
                show(native.Handle.ToInt64(), 9)  # SW_RESTORE honors native placement.
            native.Activate()

        def exit_app(sender, event):
            app.exit_session = True
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
