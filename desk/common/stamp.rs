// Штамп времени в журналах — ОДИН формат на helene.log, service.log и broker.log.
//
// До 07.09 форматов было три: оболочка писала секунды эпохи (`[1788716066]`),
// служба — местное время с секундами, брокер в харнессе — местное без секунд
// (ревью 06.09, §4 п. 17, решение 11). Читает журналы человек, и сравнивать
// «когда упало окно» с «когда служба подняла реле» в двух системах счисления
// он не должен. Формат — как в службе: `[дд.мм.гггг ЧЧ:ММ:СС]`, местное время.
// Python-сторона (`localharness/broker.py::_stamp`) держит тот же формат.

#[cfg(windows)]
fn now_stamp() -> String {
    use windows_sys::Win32::Foundation::SYSTEMTIME;
    use windows_sys::Win32::System::SystemInformation::GetLocalTime;
    let mut t: SYSTEMTIME = unsafe { std::mem::zeroed() };
    unsafe { GetLocalTime(&mut t) };
    format!(
        "[{:02}.{:02}.{} {:02}:{:02}:{:02}]",
        t.wDay, t.wMonth, t.wYear, t.wHour, t.wMinute, t.wSecond
    )
}

/// Вне Windows местного времени без внешних крейтов не взять — секунды эпохи,
/// но в тех же скобках.
#[cfg(not(windows))]
fn now_stamp() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("[{secs}]")
}

#[cfg(test)]
mod stamp_tests {
    use super::*;

    #[test]
    fn stamp_is_bracketed_and_has_seconds() {
        let s = now_stamp();
        assert!(s.starts_with('[') && s.ends_with(']'), "{s}");
        #[cfg(windows)]
        {
            // [07.09.2026 01:26:55]
            assert_eq!(s.len(), 21, "{s}");
            assert_eq!(&s[3..4], ".");
            assert_eq!(&s[11..12], " ");
            assert_eq!(&s[14..15], ":");
            assert_eq!(&s[17..18], ":");
        }
    }
}
