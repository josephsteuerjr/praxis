# Ярлык Hélène в меню «Пуск» с AppUserModel.ID.
#
# Зачем: Windows показывает уведомления только от приложений с известным ей
# AUMID. Для непакованных exe единственный надёжный способ его объявить —
# ярлык в Start Menu с этим свойством (запись в HKCU одна не работает: toast
# «отправляется» без ошибки и молча выбрасывается). Установщик создаёт этот
# ярлык сам; оболочка чинит его при запуске, если ярлыка нет.
#
# Запуск: powershell.exe -NoProfile -ExecutionPolicy Bypass -File <этот файл>
#         -Exe <путь к helene.exe> -Aumid app.helene.desk [-Name Hélène]
param(
    [Parameter(Mandatory = $true)][string] $Exe,
    [Parameter(Mandatory = $true)][string] $Aumid,
    [string] $Name = "Helene",   # имя файла ярлыка — латиницей
    [string] $Icon = ""
)
$ErrorActionPreference = "Stop"

$code = @"
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

namespace FrameShortcut {
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    public class CShellLink { }

    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
    public interface IShellLinkW {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cch, IntPtr pfd, uint fFlags);
        void GetIDList(out IntPtr ppidl);
        void SetIDList(IntPtr pidl);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cch);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cch);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cch);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
        void GetHotkey(out short pwHotkey);
        void SetHotkey(short wHotkey);
        void GetShowCmd(out int piShowCmd);
        void SetShowCmd(int iShowCmd);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cch, out int piIcon);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
        void Resolve(IntPtr hwnd, uint fFlags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PropertyKey {
        public Guid fmtid;
        public uint pid;
        public PropertyKey(Guid f, uint p) { fmtid = f; pid = p; }
    }

    [StructLayout(LayoutKind.Explicit)]
    public struct PropVariant {
        [FieldOffset(0)] public ushort vt;
        [FieldOffset(8)] public IntPtr ptr;
    }

    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
    public interface IPropertyStore {
        void GetCount(out uint cProps);
        void GetAt(uint iProp, out PropertyKey pkey);
        void GetValue(ref PropertyKey key, out PropVariant pv);
        void SetValue(ref PropertyKey key, ref PropVariant pv);
        void Commit();
    }

    public static class Maker {
        [DllImport("ole32.dll")] static extern int PropVariantClear(ref PropVariant pvar);

        public static void Create(string lnkPath, string exe, string aumid, string name, string icon) {
            var link = (IShellLinkW)new CShellLink();
            link.SetPath(exe);
            link.SetWorkingDirectory(System.IO.Path.GetDirectoryName(exe));
            link.SetDescription(name);
            link.SetIconLocation(string.IsNullOrEmpty(icon) ? exe : icon, 0);
            var store = (IPropertyStore)link;
            // System.AppUserModel.ID = {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}, 5
            var key = new PropertyKey(new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5);
            var pv = new PropVariant();
            pv.vt = 31; // VT_LPWSTR
            pv.ptr = Marshal.StringToCoTaskMemUni(aumid);
            store.SetValue(ref key, ref pv);
            store.Commit();
            PropVariantClear(ref pv);
            ((IPersistFile)link).Save(lnkPath, true);
        }
    }
}
"@
Add-Type -TypeDefinition $code -Language CSharp

# Папку меню «Пуск» спрашиваем у Windows: групповая политика «Перенаправление
# папок» уводит её с %APPDATA% (в домене — на сетевой диск), и склейка руками
# молча создавала ярлык не там, где Windows ищет AUMID, — уведомления при
# этом пропадали без единой ошибки.
$dir = [Environment]::GetFolderPath('Programs')
if ([string]::IsNullOrWhiteSpace($dir)) {
    $dir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
}
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$lnk = Join-Path $dir ($Name + ".lnk")
[FrameShortcut.Maker]::Create($lnk, $Exe, $Aumid, $Name, $Icon)
Write-Output "ok $lnk -> $Exe [$Aumid]"
