// Ярлык .lnk напрямую через COM — без PowerShell и без компилятора C#.
//
// 27.09 (1.2.3): у Егора (установка двойным кликом, «для всех», UAC выключен) ярлык
// «Пуска» не создался ни установщиком, ни окном: start-menu-shortcut.ps1 собирал свой
// C# через Add-Type, а компилятор csc.exe из этого дерева процессов не запустился
// («Клиент не обладает требуемыми правами», 1314). Установщик при этом написал
// «Ярлыки: ok». Без ярлыка с AppUserModel.ID Windows молча выбрасывает уведомления, а в
// «Пуске» программы просто нет. Здесь те же три интерфейса, что звал тот C#
// (IShellLinkW, IPropertyStore, IPersistFile), только из самого процесса: ни дочерних
// программ, ни компиляции.
//
// Отдельный поток — свой COM-апартамент: вызывающий поток может уже жить в чужом
// (главный поток Tauri — STA окна), и CoInitializeEx там ответил бы RPC_E_CHANGED_MODE.
//
// Файл включается через `include!` (установщик и оболочка).

/// Где лежат ярлыки: «Пуск» и Рабочий стол — свои или общие. Спрашиваем Windows, как её
/// видит Проводник (перенаправление папок, OneDrive), а не склеиваем из %APPDATA%.
#[cfg(windows)]
#[derive(Clone, Copy)]
#[allow(dead_code)] // окну нужен только «Пуск», мастеру — всё
enum ShortcutPlace {
    Start,
    Desktop,
    CommonStart,
    CommonDesktop,
}

#[cfg(windows)]
fn shortcut_folder(place: ShortcutPlace) -> Option<std::path::PathBuf> {
    use windows::Win32::System::Com::CoTaskMemFree;
    use windows::Win32::UI::Shell::{
        SHGetKnownFolderPath, FOLDERID_CommonPrograms, FOLDERID_Desktop, FOLDERID_Programs, FOLDERID_PublicDesktop,
        KF_FLAG_DEFAULT,
    };
    let id = match place {
        ShortcutPlace::Start => FOLDERID_Programs,
        ShortcutPlace::Desktop => FOLDERID_Desktop,
        ShortcutPlace::CommonStart => FOLDERID_CommonPrograms,
        ShortcutPlace::CommonDesktop => FOLDERID_PublicDesktop,
    };
    unsafe {
        let path = SHGetKnownFolderPath(&id, KF_FLAG_DEFAULT, None).ok()?;
        let text = path.to_string().ok();
        CoTaskMemFree(Some(path.0 as *const core::ffi::c_void));
        text.filter(|t| !t.trim().is_empty()).map(std::path::PathBuf::from)
    }
}

/// Создать (или переписать) ярлык `lnk` на `exe`. `aumid` — System.AppUserModel.ID:
/// без него уведомления Windows от непакованного exe не показываются.
#[cfg(windows)]
fn create_shortcut(
    lnk: &std::path::Path,
    exe: &std::path::Path,
    aumid: Option<&str>,
    description: &str,
    icon: Option<&std::path::Path>,
) -> Result<(), String> {
    create_shortcut_with_args(lnk, exe, "", aumid, description, icon)
}

#[cfg(windows)]
fn create_shortcut_with_args(
    lnk: &std::path::Path, exe: &std::path::Path, arguments: &str,
    aumid: Option<&str>, description: &str, icon: Option<&std::path::Path>,
) -> Result<(), String> {
    if let Some(dir) = lnk.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("папка ярлыка {}: {e}", dir.display()))?;
    }
    let lnk = lnk.to_path_buf();
    let exe = exe.to_path_buf();
    let aumid = aumid.map(str::to_string);
    let description = description.to_string();
    let icon = icon.map(std::path::Path::to_path_buf);
    let arguments = arguments.to_string();
    std::thread::spawn(move || unsafe {
        shortcut_in_own_apartment(&lnk, &exe, &arguments, aumid.as_deref(), &description, icon.as_deref())
    })
    .join()
    .unwrap_or_else(|_| Err("создание ярлыка упало".into()))?;
    Ok(())
}

#[cfg(windows)]
unsafe fn shortcut_in_own_apartment(
    lnk: &std::path::Path,
    exe: &std::path::Path,
    arguments: &str,
    aumid: Option<&str>,
    description: &str,
    icon: Option<&std::path::Path>,
) -> Result<(), String> {
    use windows::core::{Interface, GUID, HSTRING, PWSTR};
    use windows::Win32::Foundation::{E_OUTOFMEMORY, PROPERTYKEY};
    use windows::Win32::System::Com::StructuredStorage::{PropVariantClear, PROPVARIANT};
    use windows::Win32::System::Com::{
        CoCreateInstance, CoInitializeEx, CoTaskMemAlloc, CoUninitialize, IPersistFile, CLSCTX_INPROC_SERVER,
        COINIT_APARTMENTTHREADED,
    };
    use windows::Win32::System::Variant::VT_LPWSTR;
    use windows::Win32::UI::Shell::PropertiesSystem::IPropertyStore;
    use windows::Win32::UI::Shell::{IShellLinkW, ShellLink};

    let init = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
    if init.is_err() {
        return Err(format!("COM не поднялся (0x{:08X})", init.0 as u32));
    }
    let made = (|| -> windows::core::Result<()> {
        let link: IShellLinkW = CoCreateInstance(&ShellLink, None, CLSCTX_INPROC_SERVER)?;
        link.SetPath(&HSTRING::from(exe))?;
        link.SetArguments(&HSTRING::from(arguments))?;
        if let Some(dir) = exe.parent() {
            link.SetWorkingDirectory(&HSTRING::from(dir))?;
        }
        link.SetDescription(&HSTRING::from(description))?;
        link.SetIconLocation(&HSTRING::from(icon.unwrap_or(exe)), 0)?;
        if let Some(id) = aumid {
            let store: IPropertyStore = link.cast()?;
            // System.AppUserModel.ID = {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}, 5
            let key = PROPERTYKEY { fmtid: GUID::from_u128(0x9f4c2855_9f79_4b39_a8d0_e1d42de1d5f3), pid: 5 };
            // VT_LPWSTR, строка — в памяти COM: её освободит PropVariantClear.
            let wide: Vec<u16> = id.encode_utf16().chain(std::iter::once(0)).collect();
            let mem = CoTaskMemAlloc(wide.len() * 2) as *mut u16;
            if mem.is_null() {
                return Err(E_OUTOFMEMORY.into());
            }
            std::ptr::copy_nonoverlapping(wide.as_ptr(), mem, wide.len());
            let mut value = PROPVARIANT::default();
            {
                let inner = &mut *value.Anonymous.Anonymous;
                inner.vt = VT_LPWSTR;
                inner.Anonymous.pwszVal = PWSTR(mem);
            }
            let set = store.SetValue(&key, &value).and_then(|_| store.Commit());
            let _ = PropVariantClear(&mut value);
            set?;
        }
        let file: IPersistFile = link.cast()?;
        file.Save(&HSTRING::from(lnk), true)?;
        Ok(())
    })();
    CoUninitialize();
    made.map_err(|e| format!("{} (0x{:08X})", e.message().trim(), e.code().0 as u32))?;
    if !lnk.is_file() {
        // Расписка — только по файлу: «сохранено» без файла уже было в журнале (02.09).
        return Err(format!("Windows ответила «сохранено», а файла {} нет", lnk.display()));
    }
    Ok(())
}

/// Сказать Проводнику, что значки сменились.
///
/// ⚠ 29.09.2026 (Егор, 1.2.5): после обновления в панели задач и в «Пуске» осталась
/// СТАРАЯ иконка, хотя в exe, в `helene.ico` и в ярлыках уже лежала новая. Путь к значку
/// у ярлыка прежний (`…\Helene\helene.ico,0`), а Проводник держит картинки в кэше по
/// пути: файл подменили — кэш об этом не знает, пока ему не сказать. Здесь — ровно то,
/// что для этого предусмотрено: «этот ярлык обновлён» по каждому и «ассоциации сменились»
/// (Проводник сбрасывает кэш значков). Ничего не возвращает: неудача тут — старая
/// картинка до перезагрузки, а не повод ронять установку.
#[cfg(windows)]
#[allow(dead_code)] // окну — только при своём ярлыке, мастеру — всегда
fn refresh_shell_icons(lnks: &[std::path::PathBuf]) {
    use std::os::windows::ffi::OsStrExt;
    use windows::Win32::UI::Shell::{
        SHChangeNotify, SHCNE_ASSOCCHANGED, SHCNE_UPDATEITEM, SHCNF_FLUSHNOWAIT, SHCNF_IDLIST, SHCNF_PATHW,
    };
    for lnk in lnks {
        let wide: Vec<u16> = lnk.as_os_str().encode_wide().chain(std::iter::once(0)).collect();
        unsafe {
            SHChangeNotify(SHCNE_UPDATEITEM, SHCNF_PATHW | SHCNF_FLUSHNOWAIT, Some(wide.as_ptr() as _), None)
        };
    }
    unsafe { SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST | SHCNF_FLUSHNOWAIT, None, None) };
}

/// AUMID, записанный в ярлыке, — для стенда и для проверки после установки.
#[cfg(windows)]
#[allow(dead_code)]
fn shortcut_aumid(lnk: &std::path::Path) -> Option<String> {
    let lnk = lnk.to_path_buf();
    std::thread::spawn(move || unsafe {
        use windows::core::{Interface, GUID, HSTRING};
        use windows::Win32::Foundation::PROPERTYKEY;
        use windows::Win32::System::Com::StructuredStorage::PropVariantClear;
        use windows::Win32::System::Com::{
            CoCreateInstance, CoInitializeEx, CoUninitialize, IPersistFile, CLSCTX_INPROC_SERVER,
            COINIT_APARTMENTTHREADED, STGM_READ,
        };
        use windows::Win32::System::Variant::VT_LPWSTR;
        use windows::Win32::UI::Shell::PropertiesSystem::IPropertyStore;
        use windows::Win32::UI::Shell::{IShellLinkW, ShellLink};
        if CoInitializeEx(None, COINIT_APARTMENTTHREADED).is_err() {
            return None;
        }
        let read = (|| -> windows::core::Result<Option<String>> {
            let link: IShellLinkW = CoCreateInstance(&ShellLink, None, CLSCTX_INPROC_SERVER)?;
            link.cast::<IPersistFile>()?.Load(&HSTRING::from(lnk.as_path()), STGM_READ)?;
            let store: IPropertyStore = link.cast()?;
            let key = PROPERTYKEY { fmtid: GUID::from_u128(0x9f4c2855_9f79_4b39_a8d0_e1d42de1d5f3), pid: 5 };
            let mut value = store.GetValue(&key)?;
            let inner = &*value.Anonymous.Anonymous;
            let text = if inner.vt == VT_LPWSTR { inner.Anonymous.pwszVal.to_string().ok() } else { None };
            let _ = PropVariantClear(&mut value);
            Ok(text)
        })();
        CoUninitialize();
        read.ok().flatten()
    })
    .join()
    .ok()
    .flatten()
}

#[cfg(all(test, windows))]
mod shortcut_win_tests {
    use super::*;

    #[test]
    fn shortcut_carries_its_aumid_without_powershell() {
        let dir = std::env::temp_dir().join(format!("helene-lnk-{}", std::process::id()));
        let lnk = dir.join("Hélène test.lnk");
        let exe = std::env::current_exe().unwrap();
        create_shortcut(&lnk, &exe, Some("app.helene.test"), "Hélène", None).unwrap();
        assert!(lnk.is_file());
        assert_eq!(shortcut_aumid(&lnk).as_deref(), Some("app.helene.test"));
        // Без AUMID — обычный ярлык (рабочий стол).
        let plain = dir.join("plain.lnk");
        create_shortcut(&plain, &exe, None, "plain", None).unwrap();
        assert_eq!(shortcut_aumid(&plain), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn known_folders_answer() {
        assert!(shortcut_folder(ShortcutPlace::Start).is_some_and(|p| p.is_absolute()));
        assert!(shortcut_folder(ShortcutPlace::CommonStart).is_some_and(|p| p.is_absolute()));
    }
}
