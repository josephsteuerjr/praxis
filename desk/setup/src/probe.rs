//! Что уже стоит на машине и какая память агентов на ней лежит (1.2, 27.09).
//!
//! Требование Егора к 1.2: «если при установке найдена память — мастер предлагает
//! продолжить с <имя> / выбрать из найденных / начать заново — предложение выбрать
//! агента существующего (или агентов, лол)». До 1.2 мастер смотрел в одно место —
//! запись в «Приложениях» (только HKCU и 64-битный вид HKLM) или папку по умолчанию, —
//! и пропускал установки «для всех» от NSIS (они в `WOW6432Node`), папки, где после
//! снятия осталась память, и копии, которые владелец сделал сам.
//!
//! Ищем: записи об удалении (HKCU, HKLM обоих видов) → папки по умолчанию («для меня»
//! и «для всех») → копии `%LocalAppData%\Helene-backup-*`. Для каждой папки — имя
//! агента и владельца, версия, есть ли программа, сколько памяти, когда жил, соседи
//! (`agents/<id>/`). Ничего не меняет.
use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::install::{default_dir, read_json_pub as read_json, setup_from_dir, PRODUCT};

#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Found {
    /// `installed` — программа стоит и настроена; `leftover` — программы нет, память
    /// осталась (снятие с «оставить данные»); `backup` — копия, сделанная руками.
    pub kind: String,
    pub dir: String,
    pub agent: String,
    pub owner: String,
    pub version: String,
    /// `user` | `machine` | "" — по записи в реестре или по папке.
    pub scope: String,
    /// Рядом лежит программа (helene.exe).
    pub program: bool,
    /// helene.json с `setup_complete`.
    pub complete: bool,
    /// Решений хватает, чтобы продолжить без вопросов (имена, конституция).
    pub decisions: bool,
    pub data_mb: u64,
    /// День последней жизни агента (`ГГГГ-ММ-ДД`), по файлам памяти и журналам.
    pub last: String,
    /// Соседние агенты этой установки (`agents/<id>/`) — именами.
    pub agents: Vec<String>,
}

fn norm(p: &Path) -> String {
    let full = std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
    let s = full.display().to_string();
    s.strip_prefix(r"\\?\").unwrap_or(&s).trim_end_matches(['\\', '/']).to_lowercase()
}

fn text_at(cfg: &serde_json::Value, a: &str, b: &str) -> String {
    cfg.get(a).and_then(|o| o.get(b)).and_then(|v| v.as_str()).unwrap_or("").trim().to_string()
}

/// Размер папки в МБ — с потолком по числу файлов: память агента на десятки тысяч
/// файлов не должна держать экран.
fn size_mb(dir: &Path) -> u64 {
    fn walk(p: &Path, acc: &mut u64, left: &mut u32) {
        let Ok(rd) = std::fs::read_dir(p) else { return };
        for e in rd.flatten() {
            if *left == 0 {
                return;
            }
            *left -= 1;
            match e.metadata() {
                Ok(m) if m.is_dir() => walk(&e.path(), acc, left),
                Ok(m) => *acc += m.len(),
                Err(_) => {}
            }
        }
    }
    let mut bytes = 0u64;
    let mut left = 200_000u32;
    walk(dir, &mut bytes, &mut left);
    bytes / (1024 * 1024)
}

fn day_of(t: std::time::SystemTime) -> String {
    let secs = t.duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let d = crate::install::civil_yyyymmdd_pub(secs);
    if d.len() == 8 {
        format!("{}-{}-{}", &d[..4], &d[4..6], &d[6..])
    } else {
        d
    }
}

fn last_alive(root: &Path) -> String {
    let data = root.join("data");
    let mut newest: Option<std::time::SystemTime> = None;
    for p in [
        data.join("memory"),
        data.join("soul").join("SOUL.md"),
        data.join("runner.log"),
        data.join("deskapp.log"),
        data.join("workspace"),
        root.join("helene.json"),
    ] {
        if let Ok(t) = std::fs::metadata(&p).and_then(|m| m.modified()) {
            if newest.map(|n| t > n).unwrap_or(true) {
                newest = Some(t);
            }
        }
    }
    newest.map(day_of).unwrap_or_default()
}

/// Соседние агенты установки: `agents/<id>/helene.json` → имя агента (или id).
fn neighbours(root: &Path) -> Vec<String> {
    let Ok(rd) = std::fs::read_dir(root.join("agents")) else { return Vec::new() };
    let mut out: Vec<String> = rd
        .flatten()
        .filter(|e| e.path().join("helene.json").is_file())
        .map(|e| {
            let id = e.file_name().to_string_lossy().into_owned();
            read_json(&e.path().join("helene.json"))
                .map(|c| text_at(&c, "agent", "name"))
                .filter(|n| !n.is_empty())
                .unwrap_or(id)
        })
        .collect();
    out.sort();
    out
}

/// Разобрать одну папку. None — ни программы, ни памяти, ни настроек: не находка.
pub fn look(root: &Path, kind_hint: &str, scope: &str) -> Option<Found> {
    let cfg = read_json(&root.join("helene.json"));
    let has_soul = root.join("data").join("soul").join("SOUL.md").is_file();
    let has_memory = root.join("data").join("memory").is_dir();
    let program = crate::install::shell_exe(root).exists();
    let complete = cfg
        .as_ref()
        .and_then(|c| c.get("setup_complete").and_then(|v| v.as_bool()))
        .unwrap_or(false);
    if !program && !has_soul && !has_memory && !complete {
        return None;
    }
    // Praxis (окно к серверу) пишет свой helene.json с `mode: remote` — для мастера
    // Hélène это не агент; мастеру Praxis — как раз его установка.
    let remote = cfg.as_ref().and_then(|c| c.get("mode").and_then(|v| v.as_str())) == Some("remote");
    if remote != cfg!(feature = "praxis") {
        return None;
    }
    let agent = cfg.as_ref().map(|c| text_at(c, "agent", "name")).unwrap_or_default();
    let owner = cfg.as_ref().map(|c| text_at(c, "owner", "name")).unwrap_or_default();
    let version = cfg
        .as_ref()
        .map(|c| text_at(c, "installed", "version"))
        .filter(|v| !v.is_empty())
        .or_else(|| {
            read_json(&root.join("helene-build.json"))
                .and_then(|p| p.get("version").and_then(|v| v.as_str()).map(str::to_string))
        })
        .unwrap_or_default();
    let kind = if kind_hint == "backup" {
        "backup"
    } else if program && complete {
        "installed"
    } else if program {
        // Программа есть, настройки нет: мастер доведёт её до конца на месте.
        "unconfigured"
    } else {
        "leftover"
    };
    Some(Found {
        kind: kind.to_string(),
        dir: root.display().to_string(),
        agent,
        owner,
        version,
        scope: scope.to_string(),
        program,
        complete,
        decisions: setup_from_dir(root).is_some(),
        data_mb: if root.join("data").is_dir() { size_mb(&root.join("data")) } else { 0 },
        last: last_alive(root),
        agents: neighbours(root),
    })
}

/// Записи об удалении: (папка, `user`|`machine`). Оба вида HKLM — установщик NSIS
/// 1.1.x был 32-битным, и его запись «для всех» лежит в `WOW6432Node`.
#[cfg(windows)]
pub fn registered() -> Vec<(PathBuf, String)> {
    use winreg::enums::{HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, KEY_READ, KEY_WOW64_32KEY, KEY_WOW64_64KEY};
    use winreg::RegKey;
    let path = format!("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{PRODUCT}");
    let mut out: Vec<(PathBuf, String)> = Vec::new();
    for (hive, view, scope) in [
        (HKEY_CURRENT_USER, 0, "user"),
        (HKEY_LOCAL_MACHINE, KEY_WOW64_64KEY, "machine"),
        (HKEY_LOCAL_MACHINE, KEY_WOW64_32KEY, "machine"),
    ] {
        let Ok(key) = RegKey::predef(hive).open_subkey_with_flags(&path, KEY_READ | view) else { continue };
        let Ok(loc) = key.get_value::<String, _>("InstallLocation") else { continue };
        let loc = loc.trim().trim_matches('"').to_string();
        if loc.is_empty() {
            continue;
        }
        let p = PathBuf::from(loc);
        if !out.iter().any(|(q, _)| norm(q) == norm(&p)) {
            out.push((p, scope.to_string()));
        }
    }
    out
}

#[cfg(not(windows))]
pub fn registered() -> Vec<(PathBuf, String)> {
    Vec::new()
}

/// Папка «для всех» по умолчанию.
#[cfg(windows)]
pub fn machine_dir() -> PathBuf {
    crate::win::program_files().join(PRODUCT)
}

#[cfg(not(windows))]
pub fn machine_dir() -> PathBuf {
    PathBuf::from("/Applications").join(PRODUCT)
}

/// Копии, которые владелец сделал сам: `%LocalAppData%\Helene-backup-*`.
fn manual_backups() -> Vec<PathBuf> {
    let Some(base) = std::env::var_os("LOCALAPPDATA").map(PathBuf::from) else { return Vec::new() };
    let Ok(rd) = std::fs::read_dir(&base) else { return Vec::new() };
    let mut out: Vec<PathBuf> = rd
        .flatten()
        .filter(|e| {
            e.file_name().to_string_lossy().to_lowercase().starts_with(&format!("{}-backup", PRODUCT.to_lowercase()))
                && e.path().is_dir()
        })
        .map(|e| e.path())
        .collect();
    out.sort();
    out.reverse(); // свежие — первыми
    out
}

/// Всё найденное — без повторов, установленные первыми.
pub fn probe() -> Vec<Found> {
    let mut seen: Vec<String> = Vec::new();
    let mut out: Vec<Found> = Vec::new();
    let mut add = |root: PathBuf, kind: &str, scope: &str, out: &mut Vec<Found>| {
        let key = norm(&root);
        if seen.contains(&key) {
            return;
        }
        seen.push(key);
        if let Some(f) = look(&root, kind, scope) {
            out.push(f);
        }
    };
    for (dir, scope) in registered() {
        add(dir, "", &scope, &mut out);
    }
    if let Some(d) = default_dir() {
        add(d, "", "user", &mut out);
    }
    add(machine_dir(), "", "machine", &mut out);
    for b in manual_backups() {
        add(b, "backup", "", &mut out);
    }
    let rank = |k: &str| match k {
        "installed" => 0,
        "unconfigured" => 1,
        "leftover" => 2,
        _ => 3,
    };
    out.sort_by_key(|f| rank(&f.kind));
    out
}

/// Режим установки по папке, если ни реестр, ни метка не сказали: внутри Program
/// Files — «для всех».
pub fn scope_of_dir(dir: &Path) -> String {
    let marker = read_json(&dir.join(crate::install::INSTALL_MARKER))
        .and_then(|m| m.get("scope").and_then(|v| v.as_str()).map(str::to_string));
    if let Some(s) = marker.filter(|s| s == "user" || s == "machine") {
        return s;
    }
    let nd = norm(dir);
    for (reg, scope) in registered() {
        if norm(&reg) == nd {
            return scope;
        }
    }
    #[cfg(windows)]
    {
        let pf = norm(&crate::win::program_files());
        if nd.starts_with(&format!("{pf}\\")) {
            return "machine".into();
        }
        if let Some(pf86) = std::env::var_os("ProgramFiles(x86)") {
            if nd.starts_with(&format!("{}\\", norm(Path::new(&pf86)))) {
                return "machine".into();
            }
        }
    }
    "user".into()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn root(tag: &str) -> PathBuf {
        let r = std::env::temp_dir().join(format!("helene-probe-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&r);
        std::fs::create_dir_all(&r).unwrap();
        r
    }

    #[test]
    fn a_leftover_memory_is_found_with_its_names() {
        let r = root("left");
        std::fs::create_dir_all(r.join("data").join("soul")).unwrap();
        std::fs::write(r.join("data").join("soul").join("SOUL.md"), "Я — Мира.").unwrap();
        std::fs::write(
            r.join("helene.json"),
            r#"{"setup_complete":true,"agent":{"name":"Мира"},"owner":{"name":"Егор"},"installed":{"version":"1.1.1"}}"#,
        )
        .unwrap();
        std::fs::create_dir_all(r.join("agents").join("jarvis")).unwrap();
        std::fs::write(r.join("agents").join("jarvis").join("helene.json"), r#"{"agent":{"name":"Джарвис"}}"#).unwrap();
        let f = look(&r, "", "user").expect("находка");
        assert_eq!(f.kind, "leftover");
        assert_eq!(f.agent, "Мира");
        assert_eq!(f.owner, "Егор");
        assert_eq!(f.version, "1.1.1");
        assert!(!f.program);
        assert_eq!(f.agents, vec!["Джарвис".to_string()]);
        assert!(!f.last.is_empty());
        let _ = std::fs::remove_dir_all(&r);
    }

    #[test]
    fn empty_folders_and_praxis_windows_are_not_agents() {
        let r = root("empty");
        assert!(look(&r, "", "user").is_none());
        std::fs::write(r.join("helene.json"), r#"{"mode":"remote","setup_complete":true}"#).unwrap();
        std::fs::write(r.join("helene.exe"), "x").unwrap();
        assert!(look(&r, "", "user").is_none(), "Praxis — окно к серверу, не агент");
        let _ = std::fs::remove_dir_all(&r);
    }

    /// Живой поиск на машине разработчика — руками: `cargo test probe_live -- --ignored --nocapture`.
    #[test]
    #[ignore]
    fn probe_live() {
        for f in probe() {
            println!("{} | {} | {} | {} MB | {}", f.kind, f.agent, f.dir, f.data_mb, f.last);
        }
        for b in manual_backups() {
            println!("backup dir: {}", b.display());
        }
    }

    #[test]
    fn scope_by_marker_wins() {
        let r = root("scope");
        std::fs::write(r.join(crate::install::INSTALL_MARKER), r#"{"scope":"machine"}"#).unwrap();
        assert_eq!(scope_of_dir(&r), "machine");
        std::fs::write(r.join(crate::install::INSTALL_MARKER), r#"{"scope":"user"}"#).unwrap();
        assert_eq!(scope_of_dir(&r), "user");
        let _ = std::fs::remove_dir_all(&r);
    }
}
