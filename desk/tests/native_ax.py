"""Native Accessibility actions for a disposable Mac app, no test RPC added."""
import ctypes,time
from ctypes import c_void_p,c_char_p,c_long,c_int,c_bool,byref
CF=ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
AX=ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
CF.CFStringCreateWithCString.argtypes=[c_void_p,c_char_p,c_int];CF.CFStringCreateWithCString.restype=c_void_p
CF.CFRelease.argtypes=[c_void_p]
CF.CFRetain.argtypes=[c_void_p];CF.CFRetain.restype=c_void_p
_OWNED=[]
CF.CFGetTypeID.argtypes=[c_void_p];CF.CFGetTypeID.restype=ctypes.c_ulong
CF.CFStringGetTypeID.restype=ctypes.c_ulong
CF.CFArrayGetTypeID.restype=ctypes.c_ulong
CF.CFStringGetCString.argtypes=[c_void_p,c_void_p,c_long,c_int];CF.CFStringGetCString.restype=c_bool
CF.CFArrayGetCount.argtypes=[c_void_p];CF.CFArrayGetCount.restype=c_long
CF.CFArrayGetValueAtIndex.argtypes=[c_void_p,c_long];CF.CFArrayGetValueAtIndex.restype=c_void_p
AX.AXUIElementCreateApplication.argtypes=[c_int];AX.AXUIElementCreateApplication.restype=c_void_p
AX.AXUIElementCopyAttributeValue.argtypes=[c_void_p,c_void_p,ctypes.POINTER(c_void_p)];AX.AXUIElementCopyAttributeValue.restype=c_int
AX.AXUIElementSetAttributeValue.argtypes=[c_void_p,c_void_p,c_void_p];AX.AXUIElementSetAttributeValue.restype=c_int
AX.AXUIElementPerformAction.argtypes=[c_void_p,c_void_p];AX.AXUIElementPerformAction.restype=c_int
AX.AXIsProcessTrusted.restype=c_bool

def string(value):return CF.CFStringCreateWithCString(None,str(value).encode('utf-8'),0x08000100)
def get(element,name):
    key=string(name);value=c_void_p()
    try:code=AX.AXUIElementCopyAttributeValue(element,key,byref(value))
    finally:CF.CFRelease(key)
    return value.value if code==0 else None
def text(element,name):
    value=get(element,name)
    if not value:return ''
    try:
        if CF.CFGetTypeID(value)!=CF.CFStringGetTypeID():return ''
        buf=ctypes.create_string_buffer(65536)
        return buf.value.decode('utf-8','replace') if CF.CFStringGetCString(value,buf,len(buf),0x08000100) else ''
    finally:CF.CFRelease(value)
def children(element):
    value=get(element,'AXChildren')
    if not value:return []
    try:
        if CF.CFGetTypeID(value)!=CF.CFArrayGetTypeID():return []
        result=[CF.CFRetain(CF.CFArrayGetValueAtIndex(value,i)) for i in range(CF.CFArrayGetCount(value))]
        _OWNED.extend(result)
        return result
    finally:CF.CFRelease(value)
def walk(pid):
    app=AX.AXUIElementCreateApplication(pid);todo=[(app,0)];result=[]
    try:
        while todo and len(result)<3000:
            element,depth=todo.pop();row={'element':element,'depth':depth}
            for key in ('AXRole','AXTitle','AXDescription','AXIdentifier','AXValue'):row[key]=text(element,key)
            result.append(row)
            if depth<40:todo.extend((child,depth+1) for child in reversed(children(element)))
        return result
    finally:_OWNED.append(app)  # Retain returned native element handles through actions.
def press(element):
    key=string('AXPress')
    try:code=AX.AXUIElementPerformAction(element,key)
    finally:CF.CFRelease(key)
    if code:raise RuntimeError('Native AXPress refused: '+str(code))
def fill(element,value):
    key=string('AXValue');body=string(value)
    try:code=AX.AXUIElementSetAttributeValue(element,key,body)
    finally:CF.CFRelease(key);CF.CFRelease(body)
    if code:raise RuntimeError('Native AX text input refused: '+str(code))
def find(pid,label,role=None,timeout=20):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        matches=[row for row in walk(pid) if (not role or row['AXRole']==role) and any(row[k]==label for k in ('AXTitle','AXDescription','AXIdentifier','AXValue'))]
        if role and matches:return matches[0]['element']
        for row in matches:
            if row['AXRole'] in ('AXButton','AXLink','AXRadioButton','AXPopUpButton','AXMenuItem'):return row['element']
        for row in matches:
            parent=get(row['element'],'AXParent')
            if parent:
                _OWNED.append(parent)
                if text(parent,'AXRole') in ('AXButton','AXLink','AXRadioButton','AXPopUpButton'):return parent
        time.sleep(.2)
    raise RuntimeError('Native control not found: '+str((role,label)))
