// Текст, который сказала родная утилита Windows — на ЛЮБОЙ Windows.
//
// Один код на оболочку, службу и установщик: `sc.exe`, `netsh`, `schtasks`,
// `icacls` и Windows PowerShell 5.1 отвечают не в UTF-8, а в кодовой странице
// консоли этой системы. Сначала пробуем UTF-8 (так говорят PowerShell 7 и
// большинство современных утилит), при неудаче — спрашиваем систему, в какой
// странице она говорит, и переводим ей же.
//
// ⚠ 17.09. До этого во всех трёх местах была ВШИТАЯ таблица cp866 — русская
// страница DOS. На русской Windows она права, на любой другой — нет: немец,
// швед и китаец видели в причине отказа службы кириллическую абракадабру,
// потому что их система отвечает в cp437, cp850 или cp936. Это ломало людей
// уже сегодня, до всякой локализации: ошибка установки — ровно тот текст,
// который человек копирует и присылает, и он приезжал нечитаемым.
//
// Файл включается через `include!`. Включающему крейту нужны фичи windows-sys
// `Win32_Globalization` (MultiByteToWideChar, GetOEMCP) и `Win32_System_Console`
// (GetConsoleOutputCP).

/// Вывод консольной программы как строка. Пустой хвост и перевод строки
/// снимаются: это подпись для человека, а не значение.
fn console_text(bytes: &[u8]) -> String {
    match std::str::from_utf8(bytes) {
        Ok(s) => s.trim().to_string(),
        Err(_) => decode_console(bytes).trim().to_string(),
    }
}

/// В какой кодовой странице говорят консольные программы этой системы.
///
/// У окна, службы и установщика своей консоли нет (дети запускаются с
/// `CREATE_NO_WINDOW`), и `GetConsoleOutputCP` тогда отвечает нулём — в этом
/// случае утилита берёт OEM-страницу системы, её и спрашиваем.
#[cfg(windows)]
fn console_codepage() -> u32 {
    let cp = unsafe { windows_sys::Win32::System::Console::GetConsoleOutputCP() };
    if cp != 0 {
        cp
    } else {
        unsafe { windows_sys::Win32::Globalization::GetOEMCP() }
    }
}

#[cfg(windows)]
fn decode_console(bytes: &[u8]) -> String {
    decode_codepage(bytes, console_codepage())
}

#[cfg(not(windows))]
fn decode_console(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

/// Байты одной кодовой страницы → строка.
///
/// Отдельно от `console_text` ради стенда: он должен спрашивать НАЗВАННУЮ
/// страницу, а не ту, что стоит у собирающего, — иначе проверка «читаем не
/// только по-русски» зеленела бы на русской машине и краснела на английской.
#[cfg(windows)]
fn decode_codepage(bytes: &[u8], cp: u32) -> String {
    use windows_sys::Win32::Globalization::MultiByteToWideChar;
    if bytes.is_empty() {
        return String::new();
    }
    let need = unsafe {
        MultiByteToWideChar(cp, 0, bytes.as_ptr(), bytes.len() as i32, std::ptr::null_mut(), 0)
    };
    if need <= 0 {
        // Неизвестная странице система — лучше показать испорченный текст,
        // чем промолчать: причина отказа нужна человеку целиком.
        return String::from_utf8_lossy(bytes).into_owned();
    }
    let mut wide = vec![0u16; need as usize];
    let got = unsafe {
        MultiByteToWideChar(cp, 0, bytes.as_ptr(), bytes.len() as i32, wide.as_mut_ptr(), need)
    };
    if got <= 0 {
        return String::from_utf8_lossy(bytes).into_owned();
    }
    wide.truncate(got as usize);
    String::from_utf16_lossy(&wide)
}
