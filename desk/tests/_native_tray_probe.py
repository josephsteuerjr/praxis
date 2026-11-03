"""Exercise the actual product tray form and the actual WinForms message loop."""
from pathlib import Path
import importlib.util, json, sys, traceback
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from System.Drawing import Point
from System.Threading import Thread, ThreadStart, ApartmentState
from System.Windows.Forms import Application, Form, Timer

module_path, output = map(Path, sys.argv[1:])
spec=importlib.util.spec_from_file_location('product_tray_under_test', module_path)
product=importlib.util.module_from_spec(spec);spec.loader.exec_module(product)
value={'product':str(module_path),'closing_events':[],'watchdog':False}
def exercise():
    try:
        native=Form();native.ShowInTaskbar=False;native.Text='Disposable tray exit check'
        native.Location=Point(-30000,-30000)
        from System.Windows.Forms import FormStartPosition
        native.StartPosition=FormStartPosition.Manual
        native.Show()
        menu=product._popup(native,lambda *args:None,lambda *args:None)
        menu.Location=Point(-30000,-30000);menu.Show();menu.Hide()
        def observe(sender,event):
            value['closing_events'].append({'reason':str(event.CloseReason),'cancel':bool(event.Cancel)})
        menu.FormClosing+=observe
        def closed(sender,event):
            # pywebview's on_close runs first; python_tray's cleanup runs later.
            Application.Exit()
            menu.Dispose()
        native.FormClosed+=closed
        tick=[0];timer=Timer();timer.Interval=150
        def advance(sender,event):
            tick[0]+=1
            if tick[0]==1:
                menu.Close()
                value['user_close_keeps_popup_reusable']=not menu.IsDisposed
                value['user_close_hides_popup']=not menu.Visible
            elif tick[0]==2:
                native.Close()
            elif tick[0]>=14:
                value['watchdog']=True
                Application.ExitThread()
        timer.Tick+=advance;timer.Start()
        Application.Run()
        timer.Stop();timer.Dispose()
        if not native.IsDisposed:native.Dispose()
        if not menu.IsDisposed:menu.Dispose()
        value['message_loop_returned_without_watchdog']=not value['watchdog']
        value['passed']=(value['user_close_keeps_popup_reusable'] and value['user_close_hides_popup']
            and value['message_loop_returned_without_watchdog']
            and any(row=={'reason':'ApplicationExitCall','cancel':False} for row in value['closing_events']))
    except BaseException:
        value['error']=traceback.format_exc();value['passed']=False
thread=Thread(ThreadStart(exercise));thread.SetApartmentState(ApartmentState.STA);thread.Start()
while thread.IsAlive:thread.Join(200)
output.write_text(json.dumps(value,indent=2)+'\n')
print(json.dumps(value))
raise SystemExit(0 if value.get('passed') else 1)
