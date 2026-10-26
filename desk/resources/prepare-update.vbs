Option Explicit
Dim fso, shell, root, selected, folder, command
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("Shell.Application")
root = fso.GetParentFolderName(WScript.ScriptFullName)
Set selected = shell.BrowseForFolder(0, "Выберите папку установленной Hélène (с helene.json)", 0, CreateObject("WScript.Shell").ExpandEnvironmentStrings("%LOCALAPPDATA%\Helene"))
If selected Is Nothing Then WScript.Quit
folder = selected.Self.Path
If Not fso.FileExists(folder & "\helene.json") Then
    MsgBox "В выбранной папке нет helene.json. Установка не изменена.", 48, "Hélène"
    WScript.Quit 1
End If
command = Chr(34) & root & "\runtime\pythonw.exe" & Chr(34) & " -s " & Chr(34) & root & "\app\python_update_prepare.py" & Chr(34) & " --old " & Chr(34) & folder & Chr(34)
CreateObject("WScript.Shell").Run command, 0, False
