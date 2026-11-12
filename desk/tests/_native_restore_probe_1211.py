"""Real WinForms placement/foreground probe in disposable offscreen windows."""
from pathlib import Path
import ctypes,importlib.util,json,sys,traceback
import clr
clr.AddReference('System.Windows.Forms');clr.AddReference('System.Drawing')
from System.Drawing import Point,Size
from System.Threading import Thread,ThreadStart,ApartmentState
from System.Windows.Forms import Form,FormStartPosition,FormWindowState,Application,Button,Label,Timer

source,output=map(Path,sys.argv[1:3])
interactive='--interactive' in sys.argv[3:]
spec=importlib.util.spec_from_file_location('candidate_tray',source)
product=importlib.util.module_from_spec(spec);spec.loader.exec_module(product)
api=ctypes.WinDLL('user32',use_last_error=True)
api.GetForegroundWindow.restype=ctypes.c_void_p
api.SetForegroundWindow.argtypes=[ctypes.c_void_p];api.SetForegroundWindow.restype=ctypes.c_int
prior=api.GetForegroundWindow();result={'cases':[],'scope':'Actual WinForms restore function; synthetic gesture, not a physical notification-icon click'}

def run():
    windows=[]
    try:
        target=Form();cover=Form();windows.extend((target,cover))
        for form in windows:
            form.ShowInTaskbar=False;form.StartPosition=FormStartPosition.Manual
            form.Location=Point(-30000,-30000);form.Show()
        for state in (FormWindowState.Normal,FormWindowState.Maximized):
            # Keep even the maximized fixture outside the owner's visible desktop.
            if state==FormWindowState.Maximized:
                from System.Drawing import Rectangle
                target.MaximizedBounds=Rectangle(-30000,-30000,600,400)
            target.WindowState=state;Application.DoEvents()
            target.WindowState=FormWindowState.Minimized;Application.DoEvents()
            target.Hide();cover.Activate();api.SetForegroundWindow(cover.Handle.ToInt64());Application.DoEvents()
            accepted=product.restore_window(target);Application.DoEvents()
            row={'before':str(state),'after':str(target.WindowState),'visible':target.Visible,
                 'foreground':api.GetForegroundWindow()==target.Handle.ToInt64(),'topmost':target.TopMost,
                 'accepted':accepted}
            result['cases'].append(row)
            assert row['foreground'] and row['visible'] and not row['topmost'] and row['after']==row['before'],row
        result['passed']=True
    except BaseException:result.update(passed=False,error=traceback.format_exc())
    finally:
        own={form.Handle.ToInt64() for form in windows if not form.IsDisposed}
        if prior and api.GetForegroundWindow() in own:api.SetForegroundWindow(prior)
        for form in windows:form.Dispose()

def interactive_run():
    control=Form();control.Text='Hélène QA — восстановление окна'
    control.StartPosition=FormStartPosition.Manual;control.Location=Point(80,120);control.ClientSize=Size(540,180)
    start=Button();start.Text='Проверить восстановление';start.Location=Point(20,20);start.Size=Size(300,38)
    label=Label();label.Text='Изолированный стенд. Настройки и агент не затрагиваются.'
    label.Location=Point(20,68);label.Size=Size(500,48)
    close=Button();close.Text='Завершить стенд';close.Location=Point(20,125);close.Size=Size(200,35)
    def test(sender,event):
        start.Enabled=False;result['scope']='Actual WinForms restore with a real UI click granting foreground authority; physical tray click separate'
        run();output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
        label.Text='PASS: обычное и развёрнутое окно восстановлены поверх другого окна.' if result.get('passed') else 'FAIL — подробности сохранены в квитанции.'
    start.Click+=test;close.Click+=lambda *_:control.Close()
    control.Controls.Add(start);control.Controls.Add(label);control.Controls.Add(close)
    timer=Timer();timer.Interval=180000;timer.Tick+=lambda *_:control.Close();timer.Start()
    try:Application.Run(control)
    finally:timer.Dispose();control.Dispose()

thread=Thread(ThreadStart(interactive_run if interactive else run));thread.SetApartmentState(ApartmentState.STA);thread.Start()
while thread.IsAlive:thread.Join(100)
output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
print(json.dumps(result));raise SystemExit(0 if result.get('passed') else 1)
