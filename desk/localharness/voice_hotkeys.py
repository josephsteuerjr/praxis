"""Scoped, configurable hold-to-talk. Never stores typed keys or record audio here."""
from __future__ import annotations
import ctypes as C
import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time

DEFAULT = 'Ctrl+Alt+Space'
MODIFIERS = {'Ctrl', 'Alt', 'Shift', 'Meta'}

def chord(value):
    parts=str(value).split('+')
    if len(parts)<2 or len(parts)>5 or len(set(parts))!=len(parts):raise ValueError('Нужно сочетание с Ctrl, Alt, Shift или Meta')
    if not set(parts[:-1])<=MODIFIERS or not re.fullmatch(r'Space|Enter|F(?:[1-9]|1[0-9]|2[0-4])|[A-Z0-9]',parts[-1]):raise ValueError('Неподдерживаемое сочетание клавиш')
    return frozenset(parts[:-1]),parts[-1]

class Chord:
    """Release exactly once, even if modifiers release before the main key."""
    def __init__(self,value,emit):
        self.mods,self.key=chord(value);self.emit=emit;self.held=False;self.eaten=set()
    def feed(self,key,down,mods):
        if not down:
            consumed=key in self.eaten;self.eaten.discard(key)
            if self.held and (key==self.key or key in self.mods):
                self.held=False;self.emit('up')
            return consumed
        if key in self.eaten:return True
        if self.held and key=='Escape':self.eaten.add(key);self.emit('cancel');return True
        if self.held and key=='Shift' and key not in self.mods:self.emit('pause');return False
        if key==self.key and frozenset(mods)==self.mods:
            self.eaten.add(key)
            if not self.held:self.held=True;self.emit('down')
            return True
        return False
    def close(self):
        if self.held:self.held=False;self.emit('cancel')

class GlobalVoiceKeys:
    def __init__(self,app):
        self.app=app;self.stop=threading.Event();self.thread=None;self.backend=None
        self.ready=threading.Event();self.active=False;self.error='';self.value=None
        self.messages=queue.Queue();self.closed=False;self.recording=False
        self.configuration_lock=threading.RLock();self.portal_shortcuts={}
        self.dispatcher=threading.Thread(target=self._dispatch,name='voice-actions',daemon=True);self.dispatcher.start()
    def _emit(self,action):self.messages.put((time.monotonic(),action))
    def _dispatch(self):
        while True:
            item=self.messages.get()
            if item is None:return
            at,action=item
            if self.closed:return
            if time.monotonic()-at>2:action='cancel'
            window=self.app.window
            if window is None:continue
            try:
                window.evaluate_js("window.dispatchEvent(new CustomEvent('helene:voice-shortcut',{detail:"+json.dumps({'action':action})+"}))")
            except Exception:pass  # renderer is retiring; never replay into its replacement
    def configure(self,args):
        with self.configuration_lock:return self._configure(args)
    def _configure(self,args):
        value=str(args.get('hotkey') or DEFAULT);chord(value)
        enabled=bool(args.get('global',True)) and not args.get('capture',False)
        signature=(value,enabled)
        if self.value==signature and (not enabled or (self.thread and self.thread.is_alive())):return self.status()
        self._stop();self.value=signature;self.error='';self.portal_shortcuts={}
        if not enabled:return self.status()
        self.stop=threading.Event();self.ready=threading.Event()
        self.thread=threading.Thread(target=self._run,args=(value,),name='voice-global-keys',daemon=True);self.thread.start()
        self.ready.wait(3)
        return self.status()
    def status(self):return {'active':self.active,'error':self.error,'label':self.value[0] if self.value else DEFAULT,'bindings':dict(self.portal_shortcuts)}
    def _run(self,value):
        try:
            machine=Chord(value,self._emit)
            if sys.platform=='win32':self.backend=WindowsKeys(machine,self)
            elif sys.platform=='darwin':self.backend=MacKeys(machine,self)
            elif os.environ.get('WAYLAND_DISPLAY'):
                from .voice_portal import PortalKeys
                self.backend=PortalKeys(machine,self)
            else:self.backend=X11Keys(machine,self)
            self.backend.run()
        except Exception as exc:self.error=str(exc)
        finally:
            self.active=False;self.ready.set()
            self.app.event('voice-native-state',self.status())
    def started(self):
        self.active=True;self.ready.set();self.app.event('voice-native-state',self.status())
    def _stop(self):
        self.stop.set()
        if self.thread:self.thread.join(3)
        if self.thread and self.thread.is_alive():raise RuntimeError('Предыдущие клавиши ещё освобождаются. Повтори сохранение.')
        self.thread=None;self.backend=None;self.active=False
    def show(self,args):
        self.recording=args.get('state') in ('recording','paused','starting','sending')
        if self.backend and hasattr(self.backend,'status'):self.backend.status=dict(args)
    def close(self):
        self.closed=True;self._stop();self.messages.put(None);self.dispatcher.join(1)

class WindowsKeys:
    def __init__(self,machine,owner):self.machine=machine;self.owner=owner;self.status={}
    def run(self):
        from ctypes import wintypes as W
        u=C.WinDLL('user32',use_last_error=True);k=C.WinDLL('kernel32',use_last_error=True)
        LRESULT=C.c_ssize_t;HOOK=C.WINFUNCTYPE(LRESULT,C.c_int,W.WPARAM,W.LPARAM)
        class KEY(C.Structure):_fields_=[('vk',W.DWORD),('scan',W.DWORD),('flags',W.DWORD),('time',W.DWORD),('extra',C.c_size_t)]
        u.SetWindowsHookExW.argtypes=[C.c_int,HOOK,W.HINSTANCE,W.DWORD];u.SetWindowsHookExW.restype=W.HANDLE
        u.CallNextHookEx.argtypes=[W.HANDLE,C.c_int,W.WPARAM,W.LPARAM];u.CallNextHookEx.restype=LRESULT
        u.UnhookWindowsHookEx.argtypes=[W.HANDLE];u.GetAsyncKeyState.argtypes=[C.c_int];u.GetAsyncKeyState.restype=C.c_short
        k.GetModuleHandleW.argtypes=[W.LPCWSTR];k.GetModuleHandleW.restype=W.HMODULE
        mapping={0x20:'Space',0x0d:'Enter',0x1b:'Escape',0x10:'Shift',0xa0:'Shift',0xa1:'Shift',0x11:'Ctrl',0xa2:'Ctrl',0xa3:'Ctrl',0x12:'Alt',0xa4:'Alt',0xa5:'Alt',0x5b:'Meta',0x5c:'Meta'}
        mapping.update({v:chr(v) for v in range(65,91)});mapping.update({v:chr(v) for v in range(48,58)});mapping.update({0x6f+i:f'F{i}' for i in range(1,25)})
        vkey=next(v for v,key in mapping.items() if key==self.machine.key)
        flags=sum({'Alt':1,'Ctrl':2,'Shift':4,'Meta':8}[m] for m in self.machine.mods)
        if not u.RegisterHotKey(None,0x4845,flags|0x4000,vkey):raise RuntimeError('Это сочетание занято другой программой. Выбери другое.')
        @HOOK
        def hook(code,wp,lp):
            if code>=0:
                event=C.cast(lp,C.POINTER(KEY)).contents
                key=mapping.get(event.vk,'');down=wp in (0x100,0x104)
                mods={name for name,v in [('Ctrl',0x11),('Alt',0x12),('Shift',0x10),('Meta',0x5b)] if u.GetAsyncKeyState(v)&0x8000}
                if u.GetAsyncKeyState(0x5c)&0x8000:mods.add('Meta')
                if key in MODIFIERS:
                    if down:mods.add(key)
                    else:mods.discard(key)
                if self.owner.recording and key=='Escape':
                    if down:self.machine.emit('cancel')
                    return 1
                if self.machine.feed(key,down,mods):return 1
            return u.CallNextHookEx(None,code,wp,lp)
        handle=u.SetWindowsHookExW(13,hook,k.GetModuleHandleW(None),0)
        if not handle:
            u.UnregisterHotKey(None,0x4845);raise C.WinError(C.get_last_error())
        # Small native status, never activates or takes focus from the current app.
        u.CreateWindowExW.argtypes=[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,W.LPVOID];u.CreateWindowExW.restype=W.HWND
        u.SetWindowTextW.argtypes=[W.HWND,W.LPCWSTR];u.ShowWindow.argtypes=[W.HWND,C.c_int];u.DestroyWindow.argtypes=[W.HWND]
        u.SetWindowPos.argtypes=[W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT]
        badge=u.CreateWindowExW(0x08000088,'STATIC','',0x80800001,0,0,470,60,None,None,k.GetModuleHandleW(None),None)
        previous=None
        self.owner.started()
        try:
            msg=W.MSG()
            while not self.owner.stop.wait(.008):
                while u.PeekMessageW(C.byref(msg),None,0,0,1):u.TranslateMessage(C.byref(msg));u.DispatchMessageW(C.byref(msg))
                state=self.status
                if state!=previous:
                    previous=dict(state)
                    if badge:
                        if state.get('state','idle')=='idle':u.ShowWindow(badge,0)
                        else:
                            label={'starting':'Микрофон…','recording':'● Запись','paused':'Ⅱ На паузе','sending':'Отправляю…'}.get(state.get('state'),'')
                            u.SetWindowTextW(badge,label+' · '+str(state.get('seconds',0))+' с → '+str(state.get('target',''))[:40]+'\nShift — пауза   ·   Esc — удалить')
                            u.SetWindowPos(badge,W.HWND(-1),max(0,u.GetSystemMetrics(0)-490),max(0,u.GetSystemMetrics(1)-140),470,60,0x50)
        finally:
            self.machine.close();u.UnhookWindowsHookEx(handle);u.UnregisterHotKey(None,0x4845)
            if badge:u.DestroyWindow(badge)

class MacKeys:
    def __init__(self,machine,owner):self.machine=machine;self.owner=owner
    def run(self):
        import Quartz as Q
        import CoreFoundation as F
        keys={49:'Space',36:'Enter',53:'Escape',56:'Shift',60:'Shift',59:'Ctrl',62:'Ctrl',58:'Alt',61:'Alt',55:'Meta',54:'Meta'}
        codes=[0,11,8,2,14,3,5,4,34,38,40,37,46,45,31,35,12,15,1,17,32,9,13,7,16,6]
        keys.update({v:chr(65+i) for i,v in enumerate(codes)})
        keys.update({v:str(i) for i,v in enumerate([29,18,19,20,21,23,22,26,28,25])})
        keys.update({v:f'F{i+1}' for i,v in enumerate([122,120,99,118,96,97,98,100,101,109,103,111,105,107,113,106,64,79,80,90])})
        flags={'Ctrl':Q.kCGEventFlagMaskControl,'Alt':Q.kCGEventFlagMaskAlternate,'Shift':Q.kCGEventFlagMaskShift,'Meta':Q.kCGEventFlagMaskCommand}
        tap=None
        def callback(proxy,kind,event,ref):
            if kind in (Q.kCGEventTapDisabledByTimeout,Q.kCGEventTapDisabledByUserInput):Q.CGEventTapEnable(tap,True);return event
            key=keys.get(Q.CGEventGetIntegerValueField(event,Q.kCGKeyboardEventKeycode),'')
            mask=Q.CGEventGetFlags(event);mods={name for name,flag in flags.items() if mask&flag}
            down=kind==Q.kCGEventKeyDown or (kind==Q.kCGEventFlagsChanged and key in mods)
            if self.owner.recording and key=='Escape':
                if down:self.machine.emit('cancel')
                return None
            return None if self.machine.feed(key,down,mods) else event
        mask=sum(1<<v for v in (Q.kCGEventKeyDown,Q.kCGEventKeyUp,Q.kCGEventFlagsChanged))
        tap=Q.CGEventTapCreate(Q.kCGSessionEventTap,Q.kCGHeadInsertEventTap,0,mask,callback,None)
        if tap is None:raise RuntimeError('Разреши Hélène управление в macOS → Конфиденциальность → Универсальный доступ, затем включи клавиши снова.')
        source=F.CFMachPortCreateRunLoopSource(None,tap,0);loop=F.CFRunLoopGetCurrent();F.CFRunLoopAddSource(loop,source,F.kCFRunLoopCommonModes)
        Q.CGEventTapEnable(tap,True);self.owner.started()
        try:
            while not self.owner.stop.is_set():F.CFRunLoopRunInMode(F.kCFRunLoopDefaultMode,.05,False)
        finally:self.machine.close();Q.CGEventTapEnable(tap,False);F.CFRunLoopRemoveSource(loop,source,F.kCFRunLoopCommonModes)

class X11Keys:
    def __init__(self,machine,owner):self.machine=machine;self.owner=owner
    def run(self):
        from ctypes.util import find_library
        x=C.CDLL(find_library('X11') or 'libX11.so.6')
        x.XOpenDisplay.argtypes=[C.c_char_p];x.XOpenDisplay.restype=C.c_void_p
        x.XDefaultRootWindow.argtypes=[C.c_void_p];x.XDefaultRootWindow.restype=C.c_ulong
        x.XStringToKeysym.argtypes=[C.c_char_p];x.XStringToKeysym.restype=C.c_ulong
        x.XKeysymToKeycode.argtypes=[C.c_void_p,C.c_ulong];x.XKeysymToKeycode.restype=C.c_uint
        x.XGrabKey.argtypes=[C.c_void_p,C.c_int,C.c_uint,C.c_ulong,C.c_int,C.c_int,C.c_int]
        x.XUngrabKey.argtypes=[C.c_void_p,C.c_int,C.c_uint,C.c_ulong]
        x.XQueryKeymap.argtypes=[C.c_void_p,C.c_void_p];x.XSync.argtypes=[C.c_void_p,C.c_int];x.XCloseDisplay.argtypes=[C.c_void_p]
        x.XPending.argtypes=[C.c_void_p];x.XPending.restype=C.c_int;x.XNextEvent.argtypes=[C.c_void_p,C.c_void_p]
        d=x.XOpenDisplay(None)
        if not d:raise RuntimeError('Нет подключения к X11. Сочетание остаётся доступным в окне.')
        root=x.XDefaultRootWindow(d);names={'Space':'space','Enter':'Return','Escape':'Escape','Ctrl':'Control_L','Alt':'Alt_L','Shift':'Shift_L','Meta':'Super_L'}
        def code(name):return x.XKeysymToKeycode(d,x.XStringToKeysym(names.get(name,name).encode()))
        main=code(self.machine.key);mods=sum({'Shift':1,'Ctrl':4,'Alt':8,'Meta':64}[m] for m in self.machine.mods)
        failures=[];ERROR=C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_void_p)
        @ERROR
        def error(_d,event):failures.append(True);return 0
        x.XSetErrorHandler.argtypes=[C.c_void_p];x.XSetErrorHandler.restype=C.c_void_p
        previous=x.XSetErrorHandler(C.cast(error,C.c_void_p))
        for lock in (0,2,16,18):x.XGrabKey(d,main,mods|lock,root,False,1,1)
        x.XSync(d,False);x.XSetErrorHandler(previous)
        if failures:
            for lock in (0,2,16,18):x.XUngrabKey(d,main,mods|lock,root)
            x.XCloseDisplay(d);raise RuntimeError('Сочетание занято. Выбери другое.')
        codes={self.machine.key:[main],'Escape':[code('Escape')],**{name:[code(name),x.XKeysymToKeycode(d,x.XStringToKeysym(right.encode()))] for name,right in [('Ctrl','Control_R'),('Alt','Alt_R'),('Shift','Shift_R'),('Meta','Super_R')]}}
        prior=set();self.owner.started()
        try:
            while not self.owner.stop.wait(.008):
                while x.XPending(d):
                    event=(C.c_long*24)();x.XNextEvent(d,C.byref(event))
                data=(C.c_ubyte*32)();x.XQueryKeymap(d,data)
                pressed={name for name,indices in codes.items() if any(i and data[i//8]&(1<<(i%8)) for i in indices)}
                modifier=pressed&MODIFIERS
                for key in prior-pressed:self.machine.feed(key,False,modifier)
                for key in pressed-prior:
                    if key=='Escape' and self.owner.recording:self.machine.emit('cancel')
                    else:self.machine.feed(key,True,modifier)
                prior=pressed
        finally:
            self.machine.close()
            for lock in (0,2,16,18):x.XUngrabKey(d,main,mods|lock,root)
            x.XCloseDisplay(d)
