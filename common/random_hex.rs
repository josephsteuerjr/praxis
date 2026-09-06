// Случайные байты для секретов продукта — из системного CSPRNG, один код на
// оболочку, службу и установщик.
//
// До 07.09 установщик генерировал ключ реле (`sk-frame-…`) через
// `RandomState` + время — не криптографический источник (ревью 06.09, §4
// п. 6, решение 5); оболочка и служба при этом уже ходили в BCryptGenRandom.
// Ключ реле — Bearer на локальном порту, но локальный порт видят все процессы
// учётки, и угадываемый ключ там ничем не лучше пустого.

/// `bytes` случайных байт шестнадцатеричной строкой. None — системный
/// генератор отказал; звать что-то похуже вместо него нельзя, пусть решает
/// вызывающий (падать словами или обходиться без секрета).
#[cfg(windows)]
fn random_hex(bytes: usize) -> Option<String> {
    use windows_sys::Win32::Security::Cryptography::{BCryptGenRandom, BCRYPT_USE_SYSTEM_PREFERRED_RNG};
    let mut buf = vec![0u8; bytes];
    let status = unsafe { BCryptGenRandom(std::ptr::null_mut(), buf.as_mut_ptr(), buf.len() as u32, BCRYPT_USE_SYSTEM_PREFERRED_RNG) };
    if status != 0 {
        return None;
    }
    Some(buf.iter().map(|b| format!("{b:02x}")).collect())
}

/// Вне Windows — /dev/urandom; нет и его — None.
#[cfg(not(windows))]
fn random_hex(bytes: usize) -> Option<String> {
    use std::io::Read;
    let mut buf = vec![0u8; bytes];
    std::fs::File::open("/dev/urandom").ok()?.read_exact(&mut buf).ok()?;
    Some(buf.iter().map(|b| format!("{b:02x}")).collect())
}

#[cfg(test)]
mod random_hex_tests {
    use super::*;

    #[test]
    fn hex_of_requested_length_and_not_repeating() {
        let a = random_hex(24).expect("системный генератор");
        let b = random_hex(24).expect("системный генератор");
        assert_eq!(a.len(), 48);
        assert!(a.bytes().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
    }
}
