"""Wayland GlobalShortcuts portal through the platform's GIO, without key interception.

Protocol: https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html
No additional Python wheel/runtime or shell process is needed.
"""
import ctypes as C
from ctypes.util import find_library
import time
import uuid

BUS=b'org.freedesktop.portal.Desktop'
PATH=b'/org/freedesktop/portal/desktop'
INTERFACE=b'org.freedesktop.portal.GlobalShortcuts'

def trigger(modifiers,key):
    names={'Ctrl':'CTRL','Alt':'ALT','Shift':'SHIFT','Meta':'LOGO'}
    tail={'Space':'space','Enter':'Return'}.get(key,key.lower() if len(key)==1 else key)
    return '+'.join([names[m] for m in ('Ctrl','Alt','Shift','Meta') if m in modifiers]+[tail])

class PortalKeys:
    def __init__(self,machine,owner):self.machine=machine;self.owner=owner;self.session=None
    def run(self):
        g=C.CDLL(find_library('gio-2.0') or 'libgio-2.0.so.0');b=C.CDLL(find_library('glib-2.0') or 'libglib-2.0.so.0');obj=C.CDLL(find_library('gobject-2.0') or 'libgobject-2.0.so.0')
        P=C.c_void_p;S=C.c_char_p;I=C.c_int;U=C.c_uint
        def bind(lib,name,result,args):
            fn=getattr(lib,name);fn.restype=result;fn.argtypes=args;return fn
        context=bind(b,'g_main_context_new',P,[])()
        push=bind(b,'g_main_context_push_thread_default',None,[P]);pop=bind(b,'g_main_context_pop_thread_default',None,[P]);uncontext=bind(b,'g_main_context_unref',None,[P]);iterate=bind(b,'g_main_context_iteration',I,[P,I])
        parse=bind(b,'g_variant_parse',P,[P,S,P,P,C.POINTER(P)])
        child=bind(b,'g_variant_get_child_value',P,[P,C.c_size_t]);string=bind(b,'g_variant_get_string',S,[P,P]);number=bind(b,'g_variant_get_uint32',U,[P]);lookup=bind(b,'g_variant_lookup_value',P,[P,S,P]);unref=bind(b,'g_variant_unref',None,[P]);count=bind(b,'g_variant_n_children',C.c_size_t,[P])
        unobj=bind(obj,'g_object_unref',None,[P]);free_error=bind(b,'g_error_free',None,[P])
        class Error(C.Structure):_fields_=[('domain',U),('code',I),('message',S)]
        def error_message(error):
            text=C.cast(error,C.POINTER(Error)).contents.message.decode();free_error(error);return text
        def text(value,index=None):
            v=child(value,index) if index is not None else value
            try:return string(v,None).decode()
            finally:
                if index is not None:unref(v)
        push(context);connection=None;subscriptions=[];requests={};phase='create';failure=[];bound={}
        error=P()
        try:
            connection=bind(g,'g_bus_get_sync',P,[I,P,C.POINTER(P)])(2,None,C.byref(error))
            if not connection:raise RuntimeError(error_message(error) if error else 'Нет D-Bus сеанса')
            call=bind(g,'g_dbus_connection_call_sync',P,[P,S,S,S,S,P,P,I,I,P,C.POINTER(P)])
            def method(name,signature,body,path=PATH,interface=INTERFACE):
                error=P();params=parse(None,('@'+signature+' '+body).encode(),None,None,C.byref(error))
                if not params:raise RuntimeError(error_message(error))
                result=call(connection,BUS,path,interface,name.encode(),params,None,0,5000,None,C.byref(error))
                if not result:raise RuntimeError(error_message(error) if error else 'Портал клавиш не ответил')
                try:return text(result,0) if count(result) else ''
                finally:unref(result)
            CALLBACK=C.CFUNCTYPE(None,P,S,S,S,S,P,P)
            @CALLBACK
            def signal(_connection,sender,path,interface,name,value,_data):
                nonlocal phase
                try:
                    if interface==b'org.freedesktop.portal.Request' and name==b'Response':
                        kind=requests.pop(path.decode(),None)
                        if kind is None:return
                        result=child(value,0)
                        try:code=number(result)
                        finally:unref(result)
                        if code:failure.append('Настройка глобальных клавиш отменена или недоступна.');return
                        values=child(value,1)
                        try:
                            if kind=='create':
                                handle=lookup(values,b'session_handle',None)
                                if not handle:raise RuntimeError('Портал не вернул сеанс')
                                try:self.session=text(handle)
                                finally:unref(handle)
                                phase='bind'
                            else:
                                shortcuts=lookup(values,b'shortcuts',None)
                                if shortcuts:
                                    try:
                                        for i in range(count(shortcuts)):
                                            row=child(shortcuts,i);details=child(row,1)
                                            try:
                                                desc=lookup(details,b'trigger_description',None)
                                                if desc:
                                                    try:bound[text(row,0)]=text(desc)
                                                    finally:unref(desc)
                                            finally:unref(details);unref(row)
                                    finally:unref(shortcuts)
                                if 'record' not in bound:raise RuntimeError('Рабочий стол не назначил клавишу записи')
                                self.owner.portal_shortcuts=bound;self.owner.started();phase='active'
                        finally:unref(values)
                    elif interface==INTERFACE and self.session and text(value,0)==self.session:
                        action=text(value,1)
                        if name==b'Activated':
                            if action=='record' and not self.machine.held:self.machine.held=True;self.machine.emit('down')
                            elif action in ('pause','cancel'):self.machine.emit(action)
                        elif name==b'Deactivated' and action=='record' and self.machine.held:self.machine.held=False;self.machine.emit('up')
                except Exception as exc:failure.append(str(exc))
            subscribe=bind(g,'g_dbus_connection_signal_subscribe',U,[P,S,S,S,S,S,I,CALLBACK,P,P]);unsubscribe=bind(g,'g_dbus_connection_signal_unsubscribe',None,[P,U])
            subscriptions.append(subscribe(connection,BUS,b'org.freedesktop.portal.Request',b'Response',None,None,0,signal,None,None))
            for event in (b'Activated',b'Deactivated'):subscriptions.append(subscribe(connection,BUS,INTERFACE,event,PATH,None,0,signal,None,None))
            token='helene_'+uuid.uuid4().hex
            handle=method('CreateSession','(a{sv})',"({'handle_token': <'"+token+"'>, 'session_handle_token': <'"+token+"'>},)")
            requests[handle]='create';phase='waiting';deadline=time.monotonic()+120
            while not self.owner.stop.wait(.01):
                while iterate(context,False):pass
                if failure:raise RuntimeError(failure[0])
                if phase=='bind':
                    primary=trigger(self.machine.mods,self.machine.key)
                    bindings="[('record', {'description': <'Записать голосовое'>, 'preferred_trigger': <'"+primary+"'>}), ('pause', {'description': <'Оставить голосовое на паузе'>, 'preferred_trigger': <'CTRL+ALT+p'>}), ('cancel', {'description': <'Удалить голосовое'>, 'preferred_trigger': <'CTRL+ALT+Escape'>})]"
                    handle=method('BindShortcuts','(oa(sa{sv})sa{sv})',"('"+self.session+"', "+bindings+", '', {})")
                    requests[handle]='bind';phase='waiting'
                if phase!='active' and time.monotonic()>deadline:raise RuntimeError('Рабочий стол не подтвердил глобальные клавиши. В окне запись доступна.')
        finally:
            self.machine.close()
            if connection:
                if self.session:
                    try:method('Close','()','()',self.session.encode(),b'org.freedesktop.portal.Session')
                    except Exception:pass
                for subscription in subscriptions:unsubscribe(connection,subscription)
                unobj(connection)
            pop(context);uncontext(context)
