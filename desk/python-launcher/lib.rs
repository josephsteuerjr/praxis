//! Early captions share canonical UI catalogs; no Python or runtime files required.
use std::{fs::File, io::Read, path::Path};
include!(concat!(env!("OUT_DIR"), "/bootstrap_text.rs"));

pub const LANGUAGES: [&str; 4] = ["ru", "en", "fr", "zh"];
const CONFIG_LIMIT: u64 = 8 * 1024 * 1024;

fn from_tag(tag: &str) -> Option<usize> {
    let normalized = tag.to_lowercase().replace('_', "-");
    let parts: Vec<&str> = normalized.split('-').collect();
    match parts[0] {
        "ru" => Some(0),
        "en" => Some(1),
        "fr" => Some(2),
        "zh" if !parts.iter().any(|p| ["hant", "tw", "hk", "mo"].contains(p))
            && (parts.len() == 1 || parts.iter().any(|p| ["hans", "cn", "sg"].contains(p))) =>
        {
            Some(3)
        }
        _ => None,
    }
}

pub fn select(config: Option<&[u8]>, tags: &[String]) -> usize {
    let selected = config.and_then(|bytes| {
        if bytes.len() as u64 > CONFIG_LIMIT {
            return None;
        }
        let body = bytes.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(bytes);
        let value: serde_json::Value = serde_json::from_slice(body).ok()?;
        let lang = value.as_object()?.get("lang")?.as_str()?;
        LANGUAGES.iter().position(|candidate| *candidate == lang)
    });
    selected
        .or_else(|| tags.iter().find_map(|tag| from_tag(tag)))
        .unwrap_or(1)
}

#[cfg(windows)]
fn preferred_tags() -> Vec<String> {
    #[link(name = "kernel32")]
    extern "system" {
        fn GetUserPreferredUILanguages(
            flags: u32,
            count: *mut u32,
            buffer: *mut u16,
            size: *mut u32,
        ) -> i32;
    }
    let (mut count, mut size) = (0, 0);
    unsafe {
        if GetUserPreferredUILanguages(8, &mut count, std::ptr::null_mut(), &mut size) == 0
            || size == 0
            || size > 32768
        {
            return vec![];
        }
        let mut buffer = vec![0u16; size as usize];
        if GetUserPreferredUILanguages(8, &mut count, buffer.as_mut_ptr(), &mut size) == 0 {
            return vec![];
        }
        if size as usize > buffer.len() {
            return vec![];
        }
        buffer[..size as usize]
            .split(|c| *c == 0)
            .filter(|s| !s.is_empty())
            .map(String::from_utf16_lossy)
            .collect()
    }
}

#[cfg(target_os = "macos")]
fn preferred_tags() -> Vec<String> {
    use std::ffi::{c_char, c_void, CStr};
    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        fn CFLocaleCopyPreferredLanguages() -> *const c_void;
        fn CFArrayGetCount(array: *const c_void) -> isize;
        fn CFArrayGetValueAtIndex(array: *const c_void, index: isize) -> *const c_void;
        fn CFStringGetCString(
            value: *const c_void,
            buffer: *mut c_char,
            size: isize,
            encoding: u32,
        ) -> bool;
        fn CFRelease(value: *const c_void);
    }
    let mut result = vec![];
    unsafe {
        let array = CFLocaleCopyPreferredLanguages();
        if !array.is_null() {
            for index in 0..CFArrayGetCount(array).clamp(0, 64) {
                let value = CFArrayGetValueAtIndex(array, index);
                let mut buffer = [0 as c_char; 256];
                if !value.is_null()
                    && CFStringGetCString(value, buffer.as_mut_ptr(), 256, 0x08000100)
                {
                    result.push(
                        CStr::from_ptr(buffer.as_ptr())
                            .to_string_lossy()
                            .into_owned(),
                    );
                }
            }
            CFRelease(array);
        }
    }
    result
}

#[cfg(not(any(windows, target_os = "macos")))]
fn preferred_tags() -> Vec<String> {
    vec![]
}

pub fn language(root: Option<&Path>) -> usize {
    let config = root.and_then(|root| {
        let file = File::open(root.join("helene.json")).ok()?;
        if file.metadata().ok()?.len() > CONFIG_LIMIT {
            return None;
        }
        let mut bytes = Vec::new();
        file.take(CONFIG_LIMIT + 1).read_to_end(&mut bytes).ok()?;
        Some(bytes)
    });
    let mut tags = preferred_tags();
    for key in ["LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"] {
        if let Ok(value) = std::env::var(key) {
            tags.extend(
                value
                    .split(':')
                    .map(|tag| tag.split(['.', '@']).next().unwrap_or("").to_owned()),
            );
        }
    }
    select(config.as_deref(), &tags)
}

/// Substitute the owned template once; inserted paths/diagnostics stay literal.
pub fn text(key: &str, language: usize, values: &[(&str, &str)]) -> String {
    let mut rest = template(key, language).unwrap_or(key);
    let mut result = String::new();
    while let Some(start) = rest.find('{') {
        result.push_str(&rest[..start]);
        let tail = &rest[start + 1..];
        let Some(end) = tail.find('}') else {
            result.push_str(&rest[start..]);
            return result;
        };
        let name = &tail[..end];
        if let Some((_, value)) = values.iter().find(|(key, _)| *key == name) {
            result.push_str(value);
        } else {
            result.push_str(&rest[start..start + end + 2]);
        }
        rest = &tail[end + 1..];
    }
    result.push_str(rest);
    result
}

pub fn present(message: &str, title: &str) {
    eprintln!("{message}");
    #[cfg(windows)]
    {
        #[link(name = "user32")]
        extern "system" {
            fn MessageBoxW(window: isize, text: *const u16, title: *const u16, flags: u32) -> i32;
        }
        let body: Vec<u16> = message.encode_utf16().chain(Some(0)).collect();
        let heading: Vec<u16> = title.encode_utf16().chain(Some(0)).collect();
        unsafe {
            MessageBoxW(0, body.as_ptr(), heading.as_ptr(), 0x10);
        }
    }
    #[cfg(not(windows))]
    let _ = title;
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn config_file_selects_ui_without_runtime_catalogs_or_mutation() {
        let suffix = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "helene-bootstrap-lang-{}-{suffix}",
            std::process::id()
        ));
        std::fs::create_dir(&root).unwrap();
        struct Cleanup(std::path::PathBuf);
        impl Drop for Cleanup {
            fn drop(&mut self) {
                let _ = std::fs::remove_file(self.0.join("helene.json"));
                let _ = std::fs::remove_dir(&self.0);
            }
        }
        let _cleanup = Cleanup(root.clone());
        for (index, code) in LANGUAGES.iter().enumerate() {
            let body=serde_json::json!({"lang":code,"agent":{"frame_language":"zh"},"literal":"中文 {lang}"}).to_string().into_bytes();
            std::fs::write(root.join("helene.json"), &body).unwrap();
            assert_eq!(language(Some(&root)), index);
            assert_eq!(std::fs::read(root.join("helene.json")).unwrap(), body);
            assert!(template("native.bootstrap.identity", index).is_some());
        }
        assert!(!root.join("app").exists());
    }
    #[test]
    fn ui_axis_does_not_read_frame_axis() {
        for (ui, name) in LANGUAGES.iter().enumerate() {
            for frame in LANGUAGES {
                let value = serde_json::json!({"lang": name, "agent": {"frame_language": frame}})
                    .to_string();
                assert_eq!(select(Some(value.as_bytes()), &["en-US".into()]), ui);
            }
        }
    }
    #[test]
    fn parser_rejects_lookalikes_and_honors_bom_and_escaped_json() {
        for value in [
            r#"{"agent":{"lang":"fr"}}"#,
            r#"{"note":"\"lang\":\"fr\""}"#,
            "{broken",
            "[]",
        ] {
            assert_eq!(select(Some(value.as_bytes()), &["zh-Hans".into()]), 3);
        }
        assert_eq!(select(Some(b"\xef\xbb\xbf{\"lang\":\"f\\u0072\"}"), &[]), 2);
        assert_eq!(select(Some(br#"{"lang":"zh-Hant"}"#), &["fr-CA".into()]), 2);
        assert_eq!(select(Some(&vec![b' '; CONFIG_LIMIT as usize + 1]), &[]), 1);
    }
    #[test]
    fn ordered_tags_follow_the_interface_mapping() {
        assert_eq!(select(None, &["zh-Hant-TW".into(), "ru-RU".into()]), 0);
        assert_eq!(select(None, &["zh-Hans-CN".into()]), 3);
        assert_eq!(select(None, &["zh-HK".into()]), 1);
        assert_eq!(select(None, &["de-DE".into(), "fr_CA".into()]), 2);
    }
    #[test]
    fn embedded_captions_exist_and_do_not_rewrite_inserted_values() {
        let raw = "literal 中文 {pid} <script> C:\\folder\\name";
        for lang in 0..4 {
            let rendered = text(
                "native.bootstrap.unconfirmed",
                lang,
                &[("pid", "42"), ("path", raw)],
            );
            assert!(rendered.contains(raw));
            assert!(rendered.contains("42"));
            assert!(!text("native.app.startup_title", lang, &[]).is_empty());
        }
        assert_ne!(
            text("native.bootstrap.identity", 0, &[]),
            text("native.bootstrap.identity", 2, &[])
        );
    }
}
