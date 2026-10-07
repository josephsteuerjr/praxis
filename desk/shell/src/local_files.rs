//! File operations belong to the interactive shell, including remote-agent windows.
//! They never delegate a local path to the server or elevate its filesystem rights.
use std::{fs, io::{Read, Write}, path::{Path, PathBuf}, sync::atomic::{AtomicU64, Ordering}};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
const MAX: u64 = 64 * 1024 * 1024;
static SEQ: AtomicU64 = AtomicU64::new(0);

fn home() -> Result<PathBuf, String> {
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from)
        .filter(|p| p.is_absolute() && p.is_dir()).ok_or_else(|| "Не удалось найти домашнюю папку".into())
}
fn visible_path(path: &Path) -> String {
    let said = path.to_string_lossy();
    if let Some(unc) = said.strip_prefix("\\\\?\\UNC\\") { return format!("\\\\{unc}"); }
    said.strip_prefix("\\\\?\\").unwrap_or(&said).to_string()
}
#[cfg(windows)]
fn known(id: windows::core::GUID) -> Option<PathBuf> {
    use windows::Win32::{UI::Shell::{SHGetKnownFolderPath, KF_FLAG_DEFAULT}, System::Com::CoTaskMemFree};
    unsafe {
        let raw = SHGetKnownFolderPath(&id, KF_FLAG_DEFAULT, None).ok()?;
        let value = raw.to_string().ok().map(PathBuf::from);
        CoTaskMemFree(Some(raw.0.cast())); value
    }
}
fn places() -> Result<Vec<(String, PathBuf)>, String> {
    let h = home()?;
    #[cfg(windows)]
    let dirs = {
        use windows::Win32::UI::Shell::{FOLDERID_Downloads, FOLDERID_Documents, FOLDERID_Desktop};
        vec![("Загрузки", known(FOLDERID_Downloads).unwrap_or_else(|| h.join("Downloads"))),
             ("Документы", known(FOLDERID_Documents).unwrap_or_else(|| h.join("Documents"))),
             ("Рабочий стол", known(FOLDERID_Desktop).unwrap_or_else(|| h.join("Desktop")))]
    };
    #[cfg(not(windows))]
    let dirs = vec![("Загрузки", h.join("Downloads")), ("Документы", h.join("Documents")), ("Рабочий стол", h.join("Desktop"))];
    let mut out: Vec<_> = dirs.into_iter().filter(|(_, p)| p.is_dir()).map(|(n,p)| (n.to_string(),p)).collect();
    out.push(("Домашняя папка".into(), h));
    #[cfg(windows)]
    for c in b'A'..=b'Z' { let p = PathBuf::from(format!("{}:\\", c as char)); if p.is_dir() { out.push((format!("{}:", c as char), p)); } }
    #[cfg(not(windows))]
    out.push(("Файловая система".into(), PathBuf::from("/")));
    Ok(out)
}
pub fn list(said: &str) -> Result<Value, String> {
    let locations = places()?;
    let p = if said.is_empty() { locations[0].1.clone() } else { PathBuf::from(said) };
    if !p.is_absolute() { return Err("Выбери абсолютный путь к папке".into()); }
    let path = fs::canonicalize(p).map_err(|e| format!("Не удалось открыть папку: {e}"))?;
    let mut rows: Vec<(bool, String, Value)> = Vec::new();
    for entry in fs::read_dir(&path).map_err(|e| format!("Не удалось прочитать папку: {e}"))?.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        let Ok(meta) = fs::metadata(entry.path()) else { continue; };
        if !meta.is_dir() && !meta.is_file() { continue; }
        let folder = meta.is_dir();
        rows.push((!folder, name.to_lowercase(), json!({"name": name, "path": visible_path(&entry.path()), "folder": folder, "size": if folder {0} else {meta.len()}})));
    }
    rows.sort_by(|a,b| (&a.0,&a.1).cmp(&(&b.0,&b.1)));
    let total = rows.len();
    Ok(json!({"path": visible_path(&path), "parent": path.parent().map(visible_path).unwrap_or_default(),
        "locations": locations.into_iter().map(|(name,path)| json!({"name":name,"path":path})).collect::<Vec<_>>(),
        "entries": rows.into_iter().map(|(_,_,v)|v).collect::<Vec<_>>(), "total":total}))
}
pub fn read(said: &str) -> Result<Value, String> {
    let p = Path::new(said); if !p.is_absolute() { return Err("Выбери абсолютный путь к файлу".into()); }
    let file = fs::File::open(p).map_err(|e| format!("Не удалось открыть файл: {e}"))?;
    let meta = file.metadata().map_err(|e| e.to_string())?;
    if !meta.is_file() || meta.len() > MAX { return Err("Выбери файл до 64 МБ".into()); }
    let mut data = Vec::new(); file.take(MAX + 1).read_to_end(&mut data).map_err(|e| format!("Не удалось прочитать файл: {e}"))?;
    if data.len() as u64 > MAX { return Err("Файл больше 64 МБ".into()); }
    let ext = p.extension().and_then(|x|x.to_str()).unwrap_or("").to_lowercase();
    let mime = match ext.as_str() { "png"=>"image/png", "jpg"|"jpeg"=>"image/jpeg", "webp"=>"image/webp", "gif"=>"image/gif", "mp3"=>"audio/mpeg", "wav"=>"audio/wav", "ogg"=>"audio/ogg", "webm"=>"audio/webm", "m4a"=>"audio/mp4", "txt"|"md"=>"text/plain", _=>"application/octet-stream" };
    Ok(json!({"name":p.file_name().unwrap_or_default().to_string_lossy(), "mime":mime, "size":data.len(), "data":STANDARD.encode(data)}))
}
fn valid_name(name: &str) -> bool {
    if name.is_empty() || name == "." || name == ".." || name.ends_with(['.', ' ']) || name.chars().any(|c| c.is_control() || "/\\:*?\"<>|".contains(c)) { return false; }
    let stem = name.split('.').next().unwrap_or("").to_ascii_uppercase();
    !matches!(stem.as_str(), "CON"|"PRN"|"AUX"|"NUL"|"COM1"|"COM2"|"COM3"|"COM4"|"COM5"|"COM6"|"COM7"|"COM8"|"COM9"|"LPT1"|"LPT2"|"LPT3"|"LPT4"|"LPT5"|"LPT6"|"LPT7"|"LPT8"|"LPT9")
}
#[cfg(windows)]
fn move_file(from: &Path, to: &Path, overwrite: bool) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH};
    let a: Vec<u16> = from.as_os_str().encode_wide().chain(Some(0)).collect();
    let b: Vec<u16> = to.as_os_str().encode_wide().chain(Some(0)).collect();
    let flags = MOVEFILE_WRITE_THROUGH | if overwrite { MOVEFILE_REPLACE_EXISTING } else { 0 };
    if unsafe { MoveFileExW(a.as_ptr(), b.as_ptr(), flags) } == 0 { Err(std::io::Error::last_os_error()) } else { Ok(()) }
}
#[cfg(not(windows))]
fn move_file(from: &Path, to: &Path, overwrite: bool) -> std::io::Result<()> {
    if overwrite { fs::rename(from, to) } else { fs::hard_link(from, to)?; fs::remove_file(from) }
}
pub fn save(folder: &str, name: &str, data: &str, overwrite: bool) -> Result<Value, String> {
    if !valid_name(name) { return Err("Имя файла содержит путь или служебные символы".into()); }
    let parent = Path::new(folder);
    if !parent.is_absolute() || !parent.is_dir() { return Err("Выбери существующую папку".into()); }
    if data.len() as u64 > MAX.div_ceil(3) * 4 { return Err("Файл больше 64 МБ".into()); }
    let bytes = STANDARD.decode(data).map_err(|_| "Не удалось прочитать содержимое файла")?;
    if bytes.len() as u64 > MAX { return Err("Файл больше 64 МБ".into()); }
    let target = parent.join(name);
    if !overwrite && target.symlink_metadata().is_ok() { return Err("file_exists: файл с этим именем уже существует".into()); }
    let tmp = parent.join(format!(".helene-save-{}-{}", std::process::id(), SEQ.fetch_add(1,Ordering::Relaxed)));
    let mut created = false;
    let result = (|| -> std::io::Result<()> {
        let mut sink = fs::OpenOptions::new().write(true).create_new(true).open(&tmp)?;
        created = true;
        sink.write_all(&bytes)?; sink.sync_all()?; drop(sink);
        move_file(&tmp, &target, overwrite)?;
        Ok(())
    })();
    if created { let _ = fs::remove_file(&tmp); }
    if let Err(e) = result {
        if e.kind() == std::io::ErrorKind::AlreadyExists { return Err("file_exists: файл с этим именем уже существует".into()); }
        return Err(format!("Не удалось сохранить файл: {e}"));
    }
    Ok(json!({"path":target,"bytes":bytes.len(),"saved":true}))
}

#[cfg(test)] mod tests {
    use super::*;
    #[test] fn atomic_save_conflict_replace_and_local_read() {
        let p = std::env::temp_dir().join(format!("helene-paper-files-{}-{}",std::process::id(),SEQ.fetch_add(1,Ordering::Relaxed))); fs::create_dir(&p).unwrap();
        let folder = p.to_str().unwrap();
        save(folder,"отчёт.txt", &STANDARD.encode(b"first"), false).unwrap();
        assert!(save(folder,"отчёт.txt", &STANDARD.encode(b"lost"), false).unwrap_err().contains("file_exists"));
        assert_eq!(fs::read(p.join("отчёт.txt")).unwrap(),b"first");
        save(folder,"отчёт.txt", &STANDARD.encode(b"second"), true).unwrap();
        assert_eq!(read(p.join("отчёт.txt").to_str().unwrap()).unwrap()["data"], STANDARD.encode(b"second"));
        assert_eq!(list(folder).unwrap()["entries"].as_array().unwrap().len(),1);
        assert!(save(folder,"../bad","",false).is_err()); assert!(save(folder,"CON.txt","",false).is_err());
        fs::remove_dir_all(p).unwrap();
    }
}
