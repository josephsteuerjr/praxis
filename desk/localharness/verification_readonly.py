"""Attempt-local Windows input protection, not a hostile-code sandbox.

One inherited deny ACE prevents ordinary write/create/replace/delete by native
children. The owner retains WRITE_DAC for restoration after all children settle.
Never apply this to installed files or shared hardlinks. Crash recovery retains
its exact preimage beside the private attempt, not an age/prefix-based sweeper.
"""
from pathlib import Path
import ctypes as c
from ctypes import wintypes as w
import os
from .package_notice import read, write


def _api():
    if os.name != 'nt':raise ValueError('Native readonly inputs require Windows ACLs')
    api=c.WinDLL('advapi32',use_last_error=True)
    kernel=c.WinDLL('kernel32',use_last_error=True)
    api.GetNamedSecurityInfoW.argtypes=[w.LPWSTR,c.c_int,w.DWORD,c.POINTER(w.LPVOID),c.POINTER(w.LPVOID),c.POINTER(w.LPVOID),c.POINTER(w.LPVOID),c.POINTER(w.LPVOID)]
    api.GetNamedSecurityInfoW.restype=w.DWORD
    api.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes=[w.LPVOID,w.DWORD,w.DWORD,c.POINTER(w.LPWSTR),c.POINTER(w.ULONG)]
    api.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype=w.BOOL
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,c.POINTER(w.LPVOID),c.POINTER(w.ULONG)]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype=w.BOOL
    api.GetSecurityDescriptorDacl.argtypes=[w.LPVOID,c.POINTER(w.BOOL),c.POINTER(w.LPVOID),c.POINTER(w.BOOL)]
    api.GetSecurityDescriptorDacl.restype=w.BOOL
    api.SetNamedSecurityInfoW.argtypes=[w.LPWSTR,c.c_int,w.DWORD,w.LPVOID,w.LPVOID,w.LPVOID,w.LPVOID]
    api.SetNamedSecurityInfoW.restype=w.DWORD
    kernel.LocalFree.argtypes=[w.LPVOID];kernel.LocalFree.restype=w.LPVOID
    return api,kernel


def _descriptor(path):
    api,kernel=_api();dacl=w.LPVOID();descriptor=w.LPVOID();text=w.LPWSTR()
    code=api.GetNamedSecurityInfoW(str(path),1,4,None,None,c.byref(dacl),None,c.byref(descriptor))
    if code:raise c.WinError(code)
    try:
        if not api.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor,1,4,c.byref(text),None):raise c.WinError(c.get_last_error())
        return text.value
    finally:
        if text:kernel.LocalFree(text)
        kernel.LocalFree(descriptor)


def _set(path,text):
    api,kernel=_api();descriptor=w.LPVOID();acl=w.LPVOID();present=w.BOOL();default=w.BOOL()
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(text,1,c.byref(descriptor),None):raise c.WinError(c.get_last_error())
    try:
        if not api.GetSecurityDescriptorDacl(descriptor,c.byref(present),c.byref(acl),c.byref(default)):raise c.WinError(c.get_last_error())
        if not present.value or not acl:raise ValueError('Missing private input DACL')
        # Preserve inherited policy, and propagate the changed inheritable ACEs.
        code=api.SetNamedSecurityInfoW(str(path),1,4,None,None,acl,None)
        if code:raise c.WinError(code)
    finally:kernel.LocalFree(descriptor)


def protect(root,cache):
    root=Path(root).resolve();marker=Path(cache)/'readonly-inputs.json'
    if root.name!='shared-inputs' or not root.parent.name.startswith('helene-update-tests-'):
        raise ValueError('Readonly base is not attempt-local')
    for here,dirs,names in os.walk(root):
        for name in [*dirs,*names]:
            path=Path(here)/name;info=path.lstat()
            if path.is_symlink() or info.st_file_attributes&0x400 or path.is_file() and info.st_nlink!=1:
                raise ValueError('Shared readonly base contains a link')
    original=_descriptor(root)
    # Deny DELETE, DELETE_CHILD, WRITE_DATA/ADD_FILE, APPEND/ADD_SUBDIR,
    # WRITE_EA and WRITE_ATTRIBUTES. Keep READ, EXECUTE and WRITE_DAC.
    at=original.find('(')
    if at<0:raise ValueError('Readonly base has no explicit DACL entries')
    protected=original[:at]+'(D;OICI;0x10156;;;WD)'+original[at:]
    write(marker,{'root':str(root),'original':original})
    _set(root,protected)


def release(work,cache):
    marker=Path(cache)/'readonly-inputs.json';state=read(marker)
    if not state:return
    root=Path(work).resolve()/'shared-inputs'
    if state.get('root')!=str(root):raise ValueError('Readonly restoration belongs to another attempt')
    if root.exists():_set(root,state['original'])
    marker.unlink(missing_ok=True)
