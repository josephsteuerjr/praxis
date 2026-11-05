"""Clickable portable Windows entries; all product lifecycle logic stays Python."""
from pathlib import Path
import subprocess
import hashlib,json,shutil,struct,sys

OBSOLETE_ENTRIES=('Start Helene.vbs','Helene setup and recovery.vbs','Migrate Helene.vbs')
INSTRUCTION_FILES=('START-HERE.md',)
OBSOLETE_INSTRUCTIONS=('FIRST-RUN.md','ПЕРВЫЙ-ЗАПУСК.md')


def embed_icon(binary,icon):
    """Bind the shipped product ICO to the tiny PE; no icon helper EXE."""
    import ctypes
    from ctypes import wintypes
    data=Path(icon).read_bytes()
    if len(data)<6:raise ValueError('Truncated application icon')
    reserved,kind,count=struct.unpack_from('<HHH',data)
    if reserved or kind!=1 or not 0<count<256 or len(data)<6+16*count:raise ValueError('Invalid application icon')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.BeginUpdateResourceW.argtypes=[wintypes.LPCWSTR,wintypes.BOOL];kernel.BeginUpdateResourceW.restype=wintypes.HANDLE
    kernel.UpdateResourceW.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.c_void_p,wintypes.WORD,ctypes.c_void_p,wintypes.DWORD];kernel.UpdateResourceW.restype=wintypes.BOOL
    kernel.EndUpdateResourceW.argtypes=[wintypes.HANDLE,wintypes.BOOL];kernel.EndUpdateResourceW.restype=wintypes.BOOL
    images=[];group=bytearray(struct.pack('<HHH',0,1,count))
    for index in range(count):
        width,height,colors,zero,planes,bits,size,offset=struct.unpack_from('<BBBBHHII',data,6+16*index)
        if zero or offset<6+16*count or offset+size>len(data) or not size:raise ValueError('Invalid icon image')
        images.append(data[offset:offset+size]);group.extend(struct.pack('<BBBBHHIH',width,height,colors,0,planes,bits,size,index+1))
    handle=kernel.BeginUpdateResourceW(str(Path(binary).resolve()),False)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    try:
        for index,payload in enumerate(images):
            buffer=ctypes.create_string_buffer(payload)
            if not kernel.UpdateResourceW(handle,3,index+1,0,buffer,len(payload)):raise ctypes.WinError(ctypes.get_last_error())
        buffer=ctypes.create_string_buffer(bytes(group))
        if not kernel.UpdateResourceW(handle,14,1,0,buffer,len(group)):raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        kernel.EndUpdateResourceW(handle,True);raise
    if not kernel.EndUpdateResourceW(handle,False):raise ctypes.WinError(ctypes.get_last_error())


def launcher(root,binary=None):
    """A tiny native entry, not the previous native product shell."""
    root=Path(root)
    source=Path(__file__).with_name('windows_launcher.rs')
    icon=Path(__file__).resolve().parents[1]/'shell/icons/icon.ico'
    icon_sha=hashlib.sha256(icon.read_bytes()).hexdigest()
    target=root/'helene.exe'
    source_sha=hashlib.sha256(source.read_bytes()).hexdigest()
    if binary is not None:
        binary=Path(binary);meta=json.loads(binary.with_name(binary.stem+'-bootstrap.json').read_text(encoding='utf-8'))
        if meta.get('source_sha256')!=source_sha or meta.get('icon_sha256')!=icon_sha or meta.get('binary_sha256')!=hashlib.sha256(binary.read_bytes()).hexdigest():
            raise ValueError('Windows bootstrap does not match its source-bound receipt')
        if binary.resolve()!=target.resolve():
            target.unlink(missing_ok=True);shutil.copy2(binary,target)
    else:
        if sys.platform!='win32':raise ValueError('Supply the verified Windows bootstrap binary and JSON receipt when assembling on another OS')
        target.unlink(missing_ok=True)  # a donor may share immutable files by link
        subprocess.run(['rustc','--edition=2021','-C','opt-level=s','-C','strip=symbols',
                        '--crate-name','helene_entry',str(source),'-o',str(target)],check=True,capture_output=True)
        embed_icon(target,icon)
    content=target.read_bytes();pe=struct.unpack_from('<I',content,0x3c)[0]
    if content[:2]!=b'MZ' or content[pe:pe+4]!=b'PE\0\0' or struct.unpack_from('<H',content,pe+4)[0]!=0x8664 or struct.unpack_from('<H',content,pe+24+68)[0]!=2:
        raise ValueError('Expected an x64 Windows GUI bootstrap')
    target.with_name(target.stem+'-bootstrap.json').write_text(json.dumps({'kind':'helene-python-bootstrap.v1','source_sha256':source_sha,
        'binary_sha256':hashlib.sha256(content).hexdigest(),'icon_sha256':icon_sha},indent=2),encoding='utf-8')
    return target

def write(root,*,launcher_binary=None):
    root=Path(root)
    def put(name,text):
        p=root/name;p.unlink(missing_ok=True);p.write_text(text,encoding='utf-8')
    launcher(root,launcher_binary)
    # One entry for first launch and setup. Remove compatibility entries from
    # donor archives as well; no Windows Script Host dependency is needed.
    for name in (*OBSOLETE_ENTRIES,*OBSOLETE_INSTRUCTIONS):
        (root/name).unlink(missing_ok=True)
    for name in INSTRUCTION_FILES:put(name,instructions())


def instructions():
    return '''# Hélène for Windows

Extract the complete archive into a new folder, then open **helene.exe**.
Python and the complete runtime are included.

For a clean upgrade of an existing Python Hélène, the completed extraction is
used directly: the application reconnects the existing data, opens the new
version and removes the previous programme folder after its startup acknowledgement.
There is no comparison of files in either programme folder and no five-minute
trial. Save any source edits you want separately before choosing clean code.

The usual data home is:

    %USERPROFILE%\\Helene\\data

Memory, conversations, credentials and settings are kept separately from the
programme. Pinned Whisper and XTTS dependencies stay in their permanent store;
the updater reuses their small references instead of copying the weights.

If there is no recognised Python owner, the first-start window connects existing
data or creates an agent. Legacy native installations use their migration flow.
Later launches open the ordinary application directly.

Update inside Hélène offers two explicit choices. Clean code uses the new
programme directly. Preserve edits reconciles the selected changes and retains
its review and validation policy.

The programme is portable. Whisper and XTTS are optional in Settings.
No compiler or build tools are needed.
'''
