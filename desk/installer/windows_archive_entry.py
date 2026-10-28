"""Clickable portable Windows entries; all product lifecycle logic stays Python."""
from pathlib import Path
import subprocess
import hashlib,json,shutil,struct,sys


def launcher(root,binary=None):
    """A tiny native entry, not the previous native product shell."""
    root=Path(root)
    source=Path(__file__).with_name('windows_launcher.rs')
    target=root/'helene.exe'
    source_sha=hashlib.sha256(source.read_bytes()).hexdigest()
    if binary is not None:
        binary=Path(binary);meta=json.loads(binary.with_name(binary.stem+'-bootstrap.json').read_text(encoding='utf-8'))
        if meta.get('source_sha256')!=source_sha or meta.get('binary_sha256')!=hashlib.sha256(binary.read_bytes()).hexdigest():
            raise ValueError('Windows bootstrap does not match its source-bound receipt')
        if binary.resolve()!=target.resolve():
            target.unlink(missing_ok=True);shutil.copy2(binary,target)
    else:
        if sys.platform!='win32':raise ValueError('Supply the verified Windows bootstrap binary and JSON receipt when assembling on another OS')
        target.unlink(missing_ok=True)  # a donor may share immutable files by link
        subprocess.run(['rustc','--edition=2021','-C','opt-level=s','-C','strip=symbols',
                        '--crate-name','helene_entry',str(source),'-o',str(target)],check=True,capture_output=True)
    content=target.read_bytes();pe=struct.unpack_from('<I',content,0x3c)[0]
    if content[:2]!=b'MZ' or content[pe:pe+4]!=b'PE\0\0' or struct.unpack_from('<H',content,pe+4)[0]!=0x8664 or struct.unpack_from('<H',content,pe+24+68)[0]!=2:
        raise ValueError('Expected an x64 Windows GUI bootstrap')
    target.with_name(target.stem+'-bootstrap.json').write_text(json.dumps({'kind':'helene-python-bootstrap.v1','source_sha256':source_sha,
        'binary_sha256':hashlib.sha256(content).hexdigest()},indent=2),encoding='utf-8')
    return target

def write(root,*,launcher_binary=None):
    root=Path(root)
    def put(name,text):
        p=root/name;p.unlink(missing_ok=True);p.write_text(text,encoding='utf-8')
    # VBScript is only a zero-console entry into the bundled interpreter. It
    # performs no install/copy/update and never needs a native product rebuild.
    launcher(root,launcher_binary)
    for name,entry in [('Start Helene.vbs','python_app.py'),
                       ('Helene setup and recovery.vbs','helene_setup.py'),
                       ('Migrate Helene.vbs','helene_setup.py')]:
        put(name,'Option Explicit\nDim fs, shell, root, command\nSet fs = CreateObject("Scripting.FileSystemObject")\nSet shell = CreateObject("WScript.Shell")\nroot = fs.GetParentFolderName(WScript.ScriptFullName)\nshell.CurrentDirectory = root\ncommand = Chr(34) & root & "\\runtime\\pythonw.exe" & Chr(34) & " -s " & Chr(34) & root & "\\app\\'+entry+'" & Chr(34) & " --root " & Chr(34) & root & Chr(34)\nshell.Run command, 0, False\n')
    text='''# Hélène for Windows

Extract the complete archive. Open **helene.exe**. Its first launch opens setup:
create an agent or connect an existing installation and data folder.
Python and the ready runtime are already inside; no compiler is needed to run it.

To open the same wizard again, use **Helene setup and recovery.vbs**.
The older **Migrate Helene.vbs** name remains a compatibility entry.

The reusable Python entry is **app/helene_setup.py**. For emergency restoration
of installed upstream code while keeping existing data directories, run:

    runtime\\python.exe -s app\\helene_setup.py --root . --restore-upstream --confirmed

The same emergency action is available next to Update inside Hélène.

Further updates run in this folder from the application. Preserve this folder,
including profile and authored code. Whisper and XTTS are optional in Settings;
praxis-relay is an independent optional MIT product. No build toolchain is bundled.
'''
    for name in ['START-HERE.md','FIRST-RUN.md','ПЕРВЫЙ-ЗАПУСК.md']:put(name,text)
