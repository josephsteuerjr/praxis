// Стоп-кран владельца (1.2.7, слово Егора 30.09: «сначала стоп-кран и дерево процессов»).
//
// 29.09 агента нельзя было остановить штатно: его поднимали шесть независимых хозяев
// (SCM, цикл службы, задача при входе, запасной ход окна, лестница надзора окна, сторож
// испытания), и ни один не спрашивал «не остановлен ли он владельцем» — такого флага не было
// вовсе (`desk-notes/ИНЦИДЕНТ-29.09-Джарвис/02-РЕВЬЮ.md`, S-1/H-1/W-1).
//
// Флаг один на машину: `%ProgramData%\Helene\stop.json`. Пишет его `helene-svc panic`
// (служба правами СИСТЕМЫ, а без службы — сам правами владельца). Пока он лежит, агента не
// поднимает никто, и каждый, кто поднимает, читает его ПЕРВЫМ делом. Снять —
// `helene-svc resume`: под службой папка закрыта на запись всем, кроме СИСТЕМЫ и
// администраторов, поэтому при включённом UAC снятие идёт через согласие UAC.
//
// ⚠ Граница честности: если рука агента — администратор (UAC выключен, как на ПК Егора),
// агент может удалить флаг сам. Флаг защищает от нечаянных воскресителей, а не от агента,
// который решил его обойти. Граница против самого агента — 1.2.8 (агент не администратор).
//
// Файл включается через `include!` в службу, окно и установщик: трое читателей одного
// флага не имеют права понимать его по-разному.

/// Код выхода «остановлен владельцем»: надзор не поднимает такого ребёнка снова. Тот же
/// номер — `localharness/owner_stop.py::OWNER_STOP_EXIT`.
#[allow(dead_code)]
const OWNER_STOP_EXIT: i32 = 5;

#[allow(dead_code)]
const OWNER_STOP_NAME: &str = "stop.json";

/// Папка флага. Путь `ProgramData` берётся у оболочки Windows, а не из переменной среды:
/// переменную пользователь (и агент) переопределяет в `HKCU\Environment`, и следующий
/// поднятый процесс искал бы флаг не там.
#[allow(dead_code)]
fn owner_stop_dir() -> Option<std::path::PathBuf> {
    #[cfg(windows)]
    {
        use windows_sys::Win32::System::Com::CoTaskMemFree;
        use windows_sys::Win32::UI::Shell::{FOLDERID_ProgramData, SHGetKnownFolderPath};
        let mut raw: windows_sys::core::PWSTR = std::ptr::null_mut();
        let hr = unsafe { SHGetKnownFolderPath(&FOLDERID_ProgramData, 0, std::ptr::null_mut(), &mut raw) };
        let mut found = None;
        if hr >= 0 && !raw.is_null() {
            let mut n = 0usize;
            while unsafe { *raw.add(n) } != 0 {
                n += 1;
            }
            let text = String::from_utf16_lossy(unsafe { std::slice::from_raw_parts(raw, n) });
            if !text.trim().is_empty() {
                found = Some(std::path::PathBuf::from(text));
            }
        }
        if !raw.is_null() {
            unsafe { CoTaskMemFree(raw as *const std::ffi::c_void) };
        }
        let base = found.unwrap_or_else(|| std::path::PathBuf::from(r"C:\ProgramData"));
        Some(base.join("Helene"))
    }
    // 03.10, слово Егора («остановить/запустить — можно и нужно»): на Linux движок
    // всегда идёт от имени владельца (окно или helene@<имя>.service с User=%i), поэтому
    // флагу не нужно ничего защищать от него — место в XDG-state владельца, ВНЕ дерева
    // данных: снос дерева агента не должен снимать стоп. HOME, а не $XDG_STATE_HOME:
    // у службы из юнита есть HOME, а XDG-переменных сеанса нет — два читателя обязаны
    // смотреть в одну точку. macOS остаётся без флага (как в 1.2.7).
    #[cfg(not(windows))]
    {
        linux_owner_stop_dir(std::env::var_os("HOME"))
    }
}

/// Чистая половина пути Linux: без среды — стенд проверяет разбор, не трогая
/// переменные процесса (в edition 2024 set_var небезопасен, гонок не заводим).
#[allow(dead_code)]
#[cfg(not(windows))]
fn linux_owner_stop_dir(home: Option<std::ffi::OsString>) -> Option<std::path::PathBuf> {
    let home = home.filter(|h| !h.is_empty())?;
    Some(std::path::PathBuf::from(home).join(".local").join("state").join("helene"))
}

#[allow(dead_code)]
fn owner_stop_file() -> Option<std::path::PathBuf> {
    owner_stop_dir().map(|d| d.join(OWNER_STOP_NAME))
}

/// Записка стоп-крана, если агент остановлен владельцем. Файл есть, но не читается или
/// битый — это всё равно «остановлен»: сомнение здесь решается в сторону стопа.
#[allow(dead_code)]
fn owner_stop_note() -> Option<serde_json::Value> {
    let path = owner_stop_file()?;
    match std::fs::read(&path) {
        Ok(bytes) => Some(
            serde_json::from_slice::<serde_json::Value>(&bytes)
                .ok()
                .filter(|v| v.is_object())
                .unwrap_or_else(|| serde_json::json!({"broken": true})),
        ),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => None,
        // Нет прав прочитать — файл есть. Остановлен.
        Err(_) => Some(serde_json::json!({"broken": true})),
    }
}

#[allow(dead_code)]
fn owner_stopped() -> bool {
    owner_stop_note().is_some()
}

/// Откуда нажали — словами владельца.
#[allow(dead_code)]
fn owner_stop_via_said(via: &str) -> &'static str {
    match via {
        "tray" => "из трея",
        "start" => "ярлыком «Пуска»",
        "window" => "кнопкой в окне",
        "telegram" => "из Telegram",
        "agent" => "самим агентом",
        "cli" => "командой helene-svc panic",
        _ => "",
    }
}

/// Одна строка для окна и журналов: «остановлен владельцем 30.09 23:33, из трея».
#[allow(dead_code)]
fn owner_stop_said(note: &serde_json::Value) -> String {
    let when = note.get("at_local").and_then(|v| v.as_str()).unwrap_or("").trim_matches(|c| c == '[' || c == ']');
    let via = owner_stop_via_said(note.get("via").and_then(|v| v.as_str()).unwrap_or(""));
    let by = if note.get("by").and_then(|v| v.as_str()) == Some("agent") { "самим агентом" } else { "владельцем" };
    let mut out = format!("агент остановлен {by}");
    if !when.is_empty() {
        out.push(' ');
        out.push_str(when);
    }
    if !via.is_empty() && by == "владельцем" {
        out.push_str(", ");
        out.push_str(via);
    }
    out
}

/// Creating even an incomplete marker stops readers. Never truncate an existing stop.
/// This command requires the owner/admin context; it is not an agent security boundary.
#[allow(dead_code)]
fn request_owner_stop(via: &str) -> Result<(), String> {
    use std::io::Write;
    let dir = owner_stop_dir().ok_or("owner stop is not supported on this platform")?;
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let result = std::process::Command::new("icacls.exe")
            .arg(&dir).args(["/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-32-545:(OI)(CI)RX"])
            .creation_flags(0x08000000).output().map_err(|e| e.to_string())?;
        if !result.status.success() { return Err("cannot protect owner-stop directory; use an elevated owner command".into()); }
    }
    let path = dir.join(OWNER_STOP_NAME);
    match std::fs::OpenOptions::new().write(true).create_new(true).open(path) {
        Ok(mut f) => {
            let at = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
                .map_err(|e| e.to_string())?.as_secs();
            let bytes = serde_json::to_vec(&serde_json::json!({"at": at, "via": via, "by": "owner"})).map_err(|e| e.to_string())?;
            f.write_all(&bytes).and_then(|_| f.sync_all()).map_err(|e| e.to_string())
        }
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}

#[allow(dead_code)]
fn resume_owner_stop() -> Result<(), String> {
    let path = owner_stop_file().ok_or("owner stop is not supported on this platform")?;
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(format!("cannot clear owner stop; use an elevated owner command: {e}")),
    }
}

#[cfg(all(test, not(windows)))]
mod owner_stop_tests {
    use super::*;

    #[test]
    fn linux_dir_is_owner_state_outside_the_data_tree() {
        // XDG-state, не дерево данных: снос data/ не должен снимать стоп владельца.
        assert_eq!(
            linux_owner_stop_dir(Some("/home/egor".into())),
            Some(std::path::PathBuf::from("/home/egor/.local/state/helene"))
        );
        assert_eq!(linux_owner_stop_dir(Some("".into())), None);
        assert_eq!(linux_owner_stop_dir(None), None);
    }
}
