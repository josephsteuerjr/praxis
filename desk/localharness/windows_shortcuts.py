"""Unicode Windows shell links through IShellLinkW; no script interpreter."""
from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import uuid


def _guid(text):return (c.c_ubyte*16).from_buffer_copy(uuid.UUID(text).bytes_le)


def _method(pointer,index,*types):
    table=c.cast(pointer,c.POINTER(c.POINTER(c.c_void_p))).contents
    return c.WINFUNCTYPE(c.c_long,c.c_void_p,*types)(table[index])


def _check(status,operation):
    if status!=0:
        raise OSError(f'Windows shortcut {operation} failed (HRESULT 0x{status & 0xffffffff:08x})')


@contextmanager
def _link():
    ole=c.WinDLL('ole32')
    ole.CoInitializeEx.argtypes=[c.c_void_p,w.DWORD];ole.CoInitializeEx.restype=c.c_long
    ole.CoCreateInstance.argtypes=[c.c_void_p,c.c_void_p,w.DWORD,c.c_void_p,c.POINTER(c.c_void_p)]
    ole.CoCreateInstance.restype=c.c_long
    initialized=ole.CoInitializeEx(None,2)
    if initialized not in (0,1,-2147417850):_check(initialized,'initialize')
    link=c.c_void_p();persist=c.c_void_p()
    try:
        _check(ole.CoCreateInstance(c.byref(_guid('00021401-0000-0000-c000-000000000046')),None,1,
            c.byref(_guid('000214f9-0000-0000-c000-000000000046')),c.byref(link)),'create')
        query=_method(link,0,c.c_void_p,c.POINTER(c.c_void_p))
        _check(query(link,c.byref(_guid('0000010b-0000-0000-c000-000000000046')),c.byref(persist)),'file interface')
        yield link,persist
    finally:
        if persist:_method(persist,2)(persist)
        if link:_method(link,2)(link)
        if initialized>=0:ole.CoUninitialize()


def read(path):
    path=Path(path)
    if not path.is_file():return None
    with _link() as (link,persist):
        _check(_method(persist,5,w.LPCWSTR,w.DWORD)(persist,str(path),0),'load')
        target=c.create_unicode_buffer(32768);cwd=c.create_unicode_buffer(32768);args=c.create_unicode_buffer(32768)
        _check(_method(link,3,w.LPWSTR,c.c_int,c.c_void_p,w.DWORD)(link,target,len(target),None,4),'read target')
        _check(_method(link,8,w.LPWSTR,c.c_int)(link,cwd,len(cwd)),'read directory')
        _check(_method(link,10,w.LPWSTR,c.c_int)(link,args,len(args)),'read arguments')
        return {'target':target.value,'cwd':cwd.value,'arguments':args.value}


def write(path,target,cwd,arguments='',icon=None):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with _link() as (link,persist):
        for index,value,label in ((20,str(target),'target'),(9,str(cwd),'directory'),(11,arguments,'arguments')):
            _check(_method(link,index,w.LPCWSTR)(link,value),'set '+label)
        if icon:_check(_method(link,17,w.LPCWSTR,c.c_int)(link,str(icon),0),'set icon')
        _check(_method(link,7,w.LPCWSTR)(link,'Hélène portable application'),'set description')
        _check(_method(persist,6,w.LPCWSTR,w.BOOL)(persist,str(path),True),'save')
