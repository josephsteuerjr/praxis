//! Снимок памяти агента перед обновлением (1.2, 27.09).
//!
//! Требование Егора: «перед любым обновлением — снимок; прежние не перезатирать».
//! Снимок — один zip (Проводник открывает его сам) в `<установка>\backups\`, имя —
//! штамп времени и повод: `2026-09-27_0412-before-1.2.0.zip`. В нём то, без чего агента
//! не вернуть: `data/` целиком, кроме журналов, кэшей, голосовых моделей и входа в
//! ChatGPT, плюс `helene.json` и настройки соседних агентов. Пишется во `.tmp` и
//! переименовывается в конце: оборванный снимок не выглядит целым. Старше `keep`
//! снимков одного вида (`before-*`) — удаляются, свежие не трогаются никогда.
//!
//! Периодические снимки (раз в неделю по умолчанию) делает сама программа тем же
//! правилом — `common/backup.rs` в оболочке и службе.
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

/// Что в `data/` не снимается: журналы, кэши и тяжёлое, что восстанавливается само
/// (голосовые модели, снимки экрана тела), и вход в ChatGPT (живой refresh_token в
/// копиях множить незачем — войти заново можно в настройках).
pub const SKIP_IN_DATA: [&str; 8] = ["relay", "body", "voice", "cache", ".cache", "tmp", "backups", "__pycache__"];

pub fn skip_rel(rel: &str) -> bool {
    let first = rel.split('/').next().unwrap_or("");
    if SKIP_IN_DATA.iter().any(|s| s.eq_ignore_ascii_case(first)) {
        return true;
    }
    // Журналы верхнего уровня data/ (runner.log, deskapp.log, service.log…) и их ротации.
    !rel.contains('/') && (rel.ends_with(".log") || rel.contains(".log."))
}

/// Штамп `ГГГГ-ММ-ДД_ЧЧММ` по UTC — без зависимостей: снимки сортируются по имени.
pub fn stamp_now() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let day = crate::install::civil_yyyymmdd_pub(secs);
    let hm = (secs % 86_400) / 60;
    format!("{}-{}-{}_{:02}{:02}", &day[..4], &day[4..6], &day[6..8], hm / 60, hm % 60)
}

fn add_dir(
    zip: &mut zip::ZipWriter<std::fs::File>,
    root: &Path,
    here: &Path,
    prefix: &str,
    skip: &dyn Fn(&str) -> bool,
    cancel: &AtomicBool,
    count: &mut u64,
) -> Result<(), String> {
    let Ok(rd) = std::fs::read_dir(here) else { return Ok(()) };
    let mut entries: Vec<_> = rd.flatten().collect();
    entries.sort_by_key(|e| e.file_name());
    for e in entries {
        if cancel.load(Ordering::Relaxed) {
            return Err("отменено".into());
        }
        let path = e.path();
        let Ok(rel) = path.strip_prefix(root) else { continue };
        let rel = rel.to_string_lossy().replace('\\', "/");
        if skip(&rel) {
            continue;
        }
        let Ok(meta) = std::fs::symlink_metadata(&path) else { continue };
        if meta.file_type().is_symlink() {
            continue;
        }
        let name = format!("{prefix}{rel}");
        if meta.is_dir() {
            add_dir(zip, root, &path, prefix, skip, cancel, count)?;
        } else {
            // Файл, занятый живым агентом на запись, читается как есть; не прочёлся —
            // пропускаем: снимок из 9999 файлов лучше, чем никакого.
            let Ok(bytes) = std::fs::read(&path) else { continue };
            let opts = zip::write::SimpleFileOptions::default()
                .compression_method(zip::CompressionMethod::Deflated)
                .large_file(bytes.len() as u64 >= 0xFFFF_FFFF);
            zip.start_file(name, opts).map_err(|e| e.to_string())?;
            zip.write_all(&bytes).map_err(|e| e.to_string())?;
            *count += 1;
        }
    }
    Ok(())
}

/// Снять снимок установки `root` в `into` (обычно `<root>\backups`). -> путь и число файлов.
pub fn snapshot(root: &Path, into: &Path, reason: &str, cancel: &AtomicBool) -> Result<(PathBuf, u64), String> {
    std::fs::create_dir_all(into).map_err(|e| format!("{}: {e}", into.display()))?;
    let safe_reason: String = reason
        .chars()
        .map(|c| if c.is_alphanumeric() || c == '.' || c == '-' { c } else { '-' })
        .collect();
    let mut name = format!("{}-{safe_reason}", stamp_now());
    // Два снимка в одну минуту — не перезаписать первый.
    let mut n = 1;
    while into.join(format!("{name}.zip")).exists() {
        n += 1;
        name = format!("{}-{safe_reason}-{n}", stamp_now());
    }
    let final_path = into.join(format!("{name}.zip"));
    let tmp = into.join(format!("{name}.zip.tmp"));
    let file = std::fs::File::create(&tmp).map_err(|e| format!("{}: {e}", tmp.display()))?;
    let mut zip = zip::ZipWriter::new(file);
    let mut count = 0u64;
    let result = (|| -> Result<(), String> {
        for cfg in ["helene.json", "helene.json.bak"] {
            if let Ok(bytes) = std::fs::read(root.join(cfg)) {
                zip.start_file(cfg, zip::write::SimpleFileOptions::default()).map_err(|e| e.to_string())?;
                zip.write_all(&bytes).map_err(|e| e.to_string())?;
                count += 1;
            }
        }
        let data = root.join("data");
        add_dir(&mut zip, &data, &data, "data/", &skip_rel, cancel, &mut count)?;
        // Соседние агенты: их настройки и память — тем же правилом.
        let agents = root.join("agents");
        if agents.is_dir() {
            add_dir(&mut zip, &agents, &agents, "agents/", &|rel: &str| {
                let parts: Vec<&str> = rel.splitn(3, '/').collect();
                match parts.as_slice() {
                    [_, "data", rest] => skip_rel(rest),
                    _ => false,
                }
            }, cancel, &mut count)?;
        }
        Ok(())
    })();
    let finished = zip.finish().map_err(|e| e.to_string());
    if let Err(e) = result.and(finished.map(|_| ())) {
        let _ = std::fs::remove_file(&tmp);
        return Err(e);
    }
    std::fs::rename(&tmp, &final_path).map_err(|e| format!("{}: {e}", final_path.display()))?;
    Ok((final_path, count))
}

/// Оставить `keep` свежих снимков с этим окончанием имени (`-before-…` или `-weekly`),
/// остальные — удалить. Чужие файлы в папке не трогаются.
pub fn prune(into: &Path, kind: &str, keep: usize) -> Vec<PathBuf> {
    let Ok(rd) = std::fs::read_dir(into) else { return Vec::new() };
    let mut ours: Vec<PathBuf> = rd
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            let n = p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            n.ends_with(".zip") && n.contains(kind) && n.as_bytes().first().map(|b| b.is_ascii_digit()).unwrap_or(false)
        })
        .collect();
    ours.sort();
    let mut gone = Vec::new();
    while ours.len() > keep.max(1) {
        let p = ours.remove(0);
        if std::fs::remove_file(&p).is_ok() {
            gone.push(p);
        }
    }
    gone
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_takes_memory_and_settings_but_not_logs_or_login() {
        let r = std::env::temp_dir().join(format!("helene-backup-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&r);
        let put = |rel: &str, text: &str| {
            let p = r.join(rel);
            std::fs::create_dir_all(p.parent().unwrap()).unwrap();
            std::fs::write(p, text).unwrap();
        };
        put("helene.json", "{}");
        put("data/soul/SOUL.md", "душа");
        put("data/memory/2026-09-27.md", "день");
        put("data/workspace/notes.md", "заметки");
        put("data/runner.log", "журнал");
        put("data/relay/local_auth/auth.json", "секрет");
        put("data/voice/model.bin", "тяжёлое");
        put("agents/mira/helene.json", "{}");
        put("agents/mira/data/soul/SOUL.md", "душа соседа");
        put("agents/mira/data/runner.log", "журнал соседа");
        let never = AtomicBool::new(false);
        let (path, n) = snapshot(&r, &r.join("backups"), "before-1.2.0", &never).unwrap();
        assert!(path.file_name().unwrap().to_string_lossy().ends_with("-before-1.2.0.zip"));
        let file = std::fs::File::open(&path).unwrap();
        let mut z = zip::ZipArchive::new(file).unwrap();
        let names: Vec<String> = (0..z.len()).map(|i| z.by_index(i).unwrap().name().to_string()).collect();
        for want in ["helene.json", "data/soul/SOUL.md", "data/memory/2026-09-27.md", "data/workspace/notes.md",
                     "agents/mira/helene.json", "agents/mira/data/soul/SOUL.md"] {
            assert!(names.iter().any(|n| n == want), "нет {want}: {names:?}");
        }
        for never_in in ["data/runner.log", "data/relay/local_auth/auth.json", "data/voice/model.bin",
                         "agents/mira/data/runner.log"] {
            assert!(!names.iter().any(|n| n == never_in), "лишнее {never_in}: {names:?}");
        }
        assert_eq!(n as usize, names.len());
        // Второй снимок в ту же минуту — рядом, а не поверх.
        let (path2, _) = snapshot(&r, &r.join("backups"), "before-1.2.0", &never).unwrap();
        assert_ne!(path, path2);
        assert!(path.is_file() && path2.is_file());
        let gone = prune(&r.join("backups"), "before-", 1);
        assert_eq!(gone.len(), 1);
        let _ = std::fs::remove_dir_all(&r);
    }
}
