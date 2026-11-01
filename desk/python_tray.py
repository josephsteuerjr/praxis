"""Windows notification icon owned by the Python window's WinForms thread."""
import sys


def _popup(native,restore,exit_app):
    """Application-owned beige tiles, not the Windows context-menu renderer."""
    from System.Drawing import Color,Font,Point,Size,Region
    from System.Drawing.Drawing2D import GraphicsPath
    from System.Windows.Forms import (Form,Button,FormBorderStyle,FormStartPosition,
        FlatStyle,Padding,Keys,AutoScaleMode)
    from System.Drawing import ContentAlignment
    scale=max(1.0,float(native.DeviceDpi)/96.0)
    px=lambda value:round(value*scale)
    menu=Form();menu.FormBorderStyle=getattr(FormBorderStyle,'None')
    menu.ShowInTaskbar=False;menu.ShowIcon=False;menu.TopMost=True
    menu.StartPosition=FormStartPosition.Manual
    menu.AutoScaleMode=getattr(AutoScaleMode,'None');menu.KeyPreview=True
    menu.ClientSize=Size(px(224),px(108))
    menu.BackColor=Color.FromArgb(248,238,224)
    menu.Font=Font('Segoe UI',9.5)
    def rounded(control,radius):
        path=GraphicsPath();diameter=radius*2
        path.AddArc(0,0,diameter,diameter,180,90)
        path.AddArc(control.Width-diameter,0,diameter,diameter,270,90)
        path.AddArc(control.Width-diameter,control.Height-diameter,diameter,diameter,0,90)
        path.AddArc(0,control.Height-diameter,diameter,diameter,90,90)
        path.CloseFigure();control.Region=Region(path);path.Dispose()
    rounded(menu,px(12))
    for index,(text,action) in enumerate((('Открыть Hélène',restore),('Выйти',exit_app))):
        button=Button();button.Text=text;button.AccessibleName=text
        button.Location=Point(px(6),px(6+index*50));button.Size=Size(px(212),px(46))
        button.FlatStyle=FlatStyle.Flat;button.FlatAppearance.BorderSize=0
        button.UseVisualStyleBackColor=False;button.BackColor=Color.FromArgb(255,249,240)
        button.ForeColor=Color.FromArgb(48,40,31)
        button.FlatAppearance.MouseOverBackColor=Color.FromArgb(238,220,198)
        button.FlatAppearance.MouseDownBackColor=Color.FromArgb(225,199,168)
        button.TextAlign=ContentAlignment.MiddleLeft;button.Padding=Padding(px(13),0,0,0)
        button.TabIndex=index
        rounded(button,px(8))
        def click(sender,event,callback=action):
            menu.Hide();callback(sender,event)
        button.Click+=click;menu.Controls.Add(button)
    def escape(sender,event):
        if event.KeyCode==Keys.Escape:
            menu.Hide();event.Handled=True;event.SuppressKeyPress=True
        elif event.KeyCode in (Keys.Up,Keys.Down):
            menu.Controls[0 if menu.Controls[1].Focused else 1].Focus()
            event.Handled=True;event.SuppressKeyPress=True
        elif event.KeyCode==Keys.Enter:
            next((button for button in menu.Controls if button.Focused),menu.Controls[0]).PerformClick()
            event.Handled=True;event.SuppressKeyPress=True
    menu.KeyDown+=escape
    menu.Deactivate+=lambda sender,event:menu.Hide()
    def close_popup(sender,event):
        event.Cancel=True;menu.Hide()
    menu.FormClosing+=close_popup
    return menu


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
    from System.Windows.Forms import CloseReason,NotifyIcon,MouseButtons,Cursor,Screen
    from System.Drawing import Point

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
        menu = None
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
            app.relaunch = False
            app.exit_session = True
            app.session_exit_intent = True
            app.exiting = True
            native.Close()

        def closing(sender, event):
            if event.CloseReason == CloseReason.UserClosing and not app.exiting and not app.relaunch:
                native.Hide()
                event.Cancel = True

        def closed(sender, event):
            tray.Visible = False
            tray.Dispose()
            if menu is not None:menu.Dispose()
            if image is not None:
                image.Dispose()
            app.tray = None

        try:
            icon_path = app.program / 'app/resources/python-app.ico'
            image = Icon(str(icon_path)) if icon_path.is_file() else None
            tray.Icon = image if image is not None else SystemIcons.Application
            tray.Text = 'Hélène'
            menu=_popup(native,restore,exit_app)
            def mouse_up(sender,event):
                if event.Button==MouseButtons.Right:
                    position=Cursor.Position;area=Screen.FromPoint(position).WorkingArea
                    menu.Location=Point(max(area.Left,min(position.X,area.Right-menu.Width)),
                                        max(area.Top,min(position.Y,area.Bottom-menu.Height)))
                    menu.Show();menu.Activate()
                elif event.Button==MouseButtons.Left:restore(sender,event)
            tray.MouseUp+=mouse_up
            tray.DoubleClick += restore
            tray.Visible = True
            native.FormClosing += closing
            native.FormClosed += closed
            app.tray = tray
        except Exception as error:
            tray.Dispose()
            if menu is not None:menu.Dispose()
            if image is not None:
                image.Dispose()
            app.event('tray-error', {'error': str(error)})

    native.Invoke(Action(create))
