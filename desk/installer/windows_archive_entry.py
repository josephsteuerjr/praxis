"""Clickable portable Windows entries; all product lifecycle logic stays Python."""
from pathlib import Path

def write(root):
    root=Path(root)
    def put(name,text):
        p=root/name;p.unlink(missing_ok=True);p.write_text(text,encoding='utf-8')
    # VBScript is only a zero-console entry into the bundled interpreter. It
    # performs no install/copy/update and never needs a native product rebuild.
    for name,entry in [('Start Helene.vbs','python_app.py'),('Migrate Helene.vbs','python_migration_window.py')]:
        put(name,'Option Explicit\nDim fs, shell, root, command\nSet fs = CreateObject("Scripting.FileSystemObject")\nSet shell = CreateObject("WScript.Shell")\nroot = fs.GetParentFolderName(WScript.ScriptFullName)\nshell.CurrentDirectory = root\ncommand = Chr(34) & root & "\\runtime\\pythonw.exe" & Chr(34) & " -s " & Chr(34) & root & "\\app\\'+entry+'" & Chr(34) & " --root " & Chr(34) & root & Chr(34)\nshell.Run command, 0, False\n')
    text='''# Hélène 2.1.0 for Windows

Extract the complete archive into its permanent folder. Open **Start Helene.vbs**.
Python and the ready runtime are already inside; no installer or compiler is needed.

For an older installation, open **Migrate Helene.vbs** in this same folder.
The Python migrator preserves profiles, authored source and Git history, verifies
the new running app, then retires only the identified old installation.
The program itself is not downloaded, unpacked or copied again.

Further updates run in this folder from the application. Preserve this folder,
including profile and authored code. Whisper and XTTS are optional in Settings;
praxis-relay is an independent optional MIT product. No build toolchain is bundled.
'''
    for name in ['START-HERE.md','FIRST-RUN.md','ПЕРВЫЙ-ЗАПУСК.md']:put(name,text)
