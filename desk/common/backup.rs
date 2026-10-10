// Копии памяти агента — один текст на мастер, окно и службу (1.2, 27.09).
//
// Требование Егора: «перед любым обновлением — снимок; периодически (по умолчанию
// раз в неделю; настраиваемо: период, сколько хранить, папка) — memory/, soul/,
// workspace/, helene.json, настройки расширений → …\backups\<штамп>\; прежние не
// перезатирать; старше N — удалять».
//
// Снимок — один zip (его открывает Проводник и Finder) с именем
// `ГГГГ-ММ-ДД_ЧЧММ-<повод>.zip`: `before-1.2.0` (мастер перед обновлением), `auto`
// (по расписанию), `manual` (кнопка в настройках). Пишется во `.tmp` и
// переименовывается в конце: оборванный снимок не выглядит целым. Уборка — только
// своего вида и только сверх `keep`; ручные снимки не удаляются никогда.
//
// Что в снимке: `data/` целиком, кроме журналов верхнего уровня, кэшей, голосовых
// моделей, снимков экрана тела и входа в ChatGPT (живой refresh_token в копиях
// множить незачем — войти заново можно в настройках); `helene.json`; соседние агенты
// (`agents/<id>/`) тем же правилом.
//
// Настройки — блок `backup` в helene.json: `every_days` (0 — выключено, по умолчанию
// 7), `keep` (сколько снимков по расписанию хранить, по умолчанию 8), `dir` (папка;
// пусто — `backups` рядом с программой, ВНЕ дерева агента: агент не должен уметь
// переписать собственную страховку).
#[allow(unused_imports)]
use std::io::{Read as _, Write as _};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

/// Что в `data/` не снимается.
pub const SKIP_IN_DATA: [&str; 8] = ["relay", "body", "voice", "cache", ".cache", "tmp", "backups", "__pycache__"];

pub const DEFAULT_EVERY_DAYS: u64 = 7;
pub const DEFAULT_KEEP: usize = 8;
/// Снимков перед обновлением хранится столько — их не настраивают.
pub const KEEP_BEFORE_UPDATE: usize = 10;

pub fn skip_rel(rel: &str) -> bool {
    let parts: Vec<&str> = rel.split('/').collect();
    let first = parts.first().copied().unwrap_or("");
    if SKIP_IN_DATA.iter().any(|s| s.eq_ignore_ascii_case(first)) {
        return true;
    }
    // Состояние телефонного подключения принадлежит этому компьютеру, как
    // в carry: tunnel binary, адрес/lease и runtime.lock пересоздаются при
    // старте. Живой byte-range lock нельзя читать на Windows даже из мастера.
    // Остальная .state (история, ретенция, настройки агента) остаётся в снимке.
    if first.eq_ignore_ascii_case("memory")
        && parts.get(1).is_some_and(|p| p.eq_ignore_ascii_case(".state"))
        && parts.get(2).is_some_and(|p| p.eq_ignore_ascii_case("phone-access"))
    {
        return true;
    }
    // Скачанные голосовые модели живут здесь, а не в data/voice. Другие
    // модели (в том числе созданные владельцем) остаются в снимке.
    if first.eq_ignore_ascii_case("models")
        && parts.get(1).map(|p| p.eq_ignore_ascii_case("whisper") || p.eq_ignore_ascii_case("piper")).unwrap_or(false)
    {
        return true;
    }
    // 01.10, слово владельца после живого замера (5,5 ГБ материалов, бэкап
    // 772 МБ): рабочие проекты и их история — МАТЕРИАЛЫ, не память. Байты
    // проектов в снимке не ездят — рядом в том же снимке лежит манифест
    // леджера ретенции (memory/.state/retention.json); полный снимок рабочих
    // папок делается отдельно. Исключение — workspace/inbox: необработанный
    // ввод владельца обязан переживать обновление. Сам каталог workspace
    // (один компонент) не срезается — иначе пропадёт и inbox.
    if first.eq_ignore_ascii_case("workspace") && parts.len() > 1
        && !parts[1].eq_ignore_ascii_case("inbox")
    {
        return true;
    }
    // Вложенные зависимости и кэши пересоздаются. История .git сама по себе
    // тяжелее рабочих копий (замер 01.10: 367 МБ из 545) — не её объём
    // переживать в КАЖДОМ снимке.
    if parts.iter().any(|part| ["node_modules", ".venv", "__pycache__", ".cache",
        ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache"].iter().any(|p| part.eq_ignore_ascii_case(p))) {
        return true;
    }
    // Журналы верхнего уровня data/ (runner.log, deskapp.log, service.log…) и их ротации.
    !rel.contains('/') && (rel.ends_with(".log") || rel.contains(".log."))
}

/// Секунды от эпохи → `ГГГГММДД` по UTC (алгоритм Хиннанта), без зависимостей.
fn backup_civil(secs: u64) -> String {
    let days = (secs / 86_400) as i64;
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}{m:02}{d:02}")
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Штамп `ГГГГ-ММ-ДД_ЧЧММ` по UTC: снимки сортируются по имени.
pub fn stamp_at(secs: u64) -> String {
    let day = backup_civil(secs);
    let hm = (secs % 86_400) / 60;
    format!("{}-{}-{}_{:02}{:02}", &day[..4], &day[4..6], &day[6..8], hm / 60, hm % 60)
}

#[cfg(windows)]
pub fn stamp_now() -> String {
    use windows_sys::Win32::Foundation::SYSTEMTIME;
    use windows_sys::Win32::System::SystemInformation::GetLocalTime;
    let mut t: SYSTEMTIME = unsafe { std::mem::zeroed() };
    unsafe { GetLocalTime(&mut t) };
    format!("{:04}-{:02}-{:02}_{:02}{:02}", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute)
}

/// Вне Windows — UTC: снимков перед обновлением там не снимают (install.sh), а
/// расписанию хватает порядка имён.
#[cfg(not(windows))]
pub fn stamp_now() -> String {
    stamp_at(now_secs())
}

struct BackupEntry {
    path: PathBuf,
    name: String,
    bytes: u64,
}

#[derive(Clone, Copy, Default)]
pub struct BackupProgress {
    pub files: u64,
    pub total_files: u64,
    pub bytes: u64,
    pub total_bytes: u64,
}

pub const BACKUP_CHUNK: usize = 64 * 1024;

fn backup_cancelled(cancel: &AtomicBool) -> Result<(), String> {
    if cancel.load(Ordering::Relaxed) { Err("отменено".into()) } else { Ok(()) }
}

fn collect_dir(
    entries: &mut Vec<BackupEntry>,
    root: &Path,
    here: &Path,
    prefix: &str,
    skip: &dyn Fn(&str) -> bool,
    cancel: &AtomicBool,
) -> Result<(), String> {
    backup_cancelled(cancel)?;
    let Ok(rd) = std::fs::read_dir(here) else { return Ok(()) };
    let mut children: Vec<_> = rd.flatten().collect();
    children.sort_by_key(|e| e.file_name());
    for e in children {
        backup_cancelled(cancel)?;
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
            collect_dir(entries, root, &path, prefix, skip, cancel)?;
        } else if meta.is_file() {
            entries.push(BackupEntry { path, name, bytes: meta.len() });
        }
    }
    Ok(())
}

/// Снять снимок установки `root` в `into`. -> путь и число файлов.
pub fn snapshot(root: &Path, into: &Path, reason: &str, cancel: &AtomicBool) -> Result<(PathBuf, u64), String> {
    snapshot_with_progress(root, into, reason, cancel, &mut |_| {})
}

/// Сначала считаем отобранные файлы по metadata; данные читаются блоками,
/// отмена проверяется до/после каждого блока. Progress не включает модели/кэши.
pub fn snapshot_with_progress(
    root: &Path, into: &Path, reason: &str, cancel: &AtomicBool,
    progress: &mut dyn FnMut(BackupProgress),
) -> Result<(PathBuf, u64), String> {
    backup_cancelled(cancel)?;
    let mut entries = Vec::new();
    for cfg in ["helene.json", "helene.json.bak"] {
        let path = root.join(cfg);
        if let Ok(meta) = std::fs::symlink_metadata(&path) {
            if meta.is_file() && !meta.file_type().is_symlink() {
                entries.push(BackupEntry { path, name: cfg.into(), bytes: meta.len() });
            }
        }
    }
    let data = root.join("data");
    collect_dir(&mut entries, &data, &data, "data/", &skip_rel, cancel)?;
    let agents = root.join("agents");
    collect_dir(&mut entries, &agents, &agents, "agents/", &|rel| {
        let parts: Vec<&str> = rel.splitn(3, '/').collect();
        match parts.as_slice() {
            [_, data, rest] if data.eq_ignore_ascii_case("data") => skip_rel(rest),
            _ => false,
        }
    }, cancel)?;
    let mut status = BackupProgress { total_files: entries.len() as u64,
        total_bytes: entries.iter().map(|e| e.bytes).sum(), ..Default::default() };
    progress(status);
    backup_cancelled(cancel)?;
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
    let mut buffer = [0u8; BACKUP_CHUNK];
    let result = (|| -> Result<(), String> {
        for entry in entries {
            backup_cancelled(cancel)?;
            // Не объявляем успешным снимок с потерянными файлами.
            let mut file = std::fs::File::open(&entry.path)
                .map_err(|e| format!("{}: {e}", entry.path.display()))?;
            let opts = zip::write::SimpleFileOptions::default()
                .compression_method(zip::CompressionMethod::Deflated)
                .large_file(entry.bytes >= 0xFFFF_FFFF);
            zip.start_file(entry.name, opts).map_err(|e| e.to_string())?;
            loop {
                backup_cancelled(cancel)?;
                let size = file.read(&mut buffer).map_err(|e| format!("{}: {e}", entry.path.display()))?;
                backup_cancelled(cancel)?;
                if size == 0 { break; }
                zip.write_all(&buffer[..size]).map_err(|e| e.to_string())?;
                status.bytes += size as u64;
                progress(status);
            }
            status.files += 1;
            progress(status);
        }
        backup_cancelled(cancel)
    })();
    if let Err(e) = result {
        // Drop у ZipWriter закрывает metadata. Убираем текущую запись прежде,
        // чтобы он не завершал её сжатие; буфер ограничен 64 КБ.
        let _ = zip.abort_file();
        drop(zip);
        let _ = std::fs::remove_file(&tmp);
        return Err(e);
    }
    if let Err(e) = zip.finish().map_err(|e| e.to_string()).and_then(|_| backup_cancelled(cancel)) {
        let _ = std::fs::remove_file(&tmp);
        return Err(e);
    }
    if let Err(e) = std::fs::rename(&tmp, &final_path) {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("{}: {e}", final_path.display()));
    }
    Ok((final_path, status.files))
}

/// Снимки одного вида (`-auto`, `-before-`, `-manual`) в папке — по имени, старые первыми.
fn ours(into: &Path, kind: &str) -> Vec<PathBuf> {
    let Ok(rd) = std::fs::read_dir(into) else { return Vec::new() };
    let mut out: Vec<PathBuf> = rd
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            let n = p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            n.ends_with(".zip") && n.contains(kind) && n.as_bytes().first().map(|b| b.is_ascii_digit()).unwrap_or(false)
        })
        .collect();
    out.sort();
    out
}

/// Оставить `keep` свежих снимков этого вида, остальные — удалить. Чужие файлы в
/// папке не трогаются.
pub fn prune(into: &Path, kind: &str, keep: usize) -> Vec<PathBuf> {
    let mut list = ours(into, kind);
    let mut gone = Vec::new();
    while list.len() > keep.max(1) {
        let p = list.remove(0);
        if std::fs::remove_file(&p).is_ok() {
            gone.push(p);
        }
    }
    gone
}

/// Настройки копий из helene.json установки `root`.
pub struct Policy {
    pub every_days: u64,
    pub keep: usize,
    pub dir: PathBuf,
}

pub fn policy(root: &Path, cfg: Option<&serde_json::Value>) -> Policy {
    let block = cfg.and_then(|c| c.get("backup"));
    let every_days = block
        .and_then(|b| b.get("every_days"))
        .and_then(|v| v.as_u64())
        .unwrap_or(DEFAULT_EVERY_DAYS);
    let keep = block
        .and_then(|b| b.get("keep"))
        .and_then(|v| v.as_u64())
        .map(|n| n.clamp(1, 1000) as usize)
        .unwrap_or(DEFAULT_KEEP);
    let dir = block
        .and_then(|b| b.get("dir"))
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .map(|p| if p.is_absolute() { p } else { root.join(p) })
        .unwrap_or_else(|| root.join("backups"));
    Policy { every_days, keep, dir }
}

/// Время (секунды от эпохи) последнего снимка по расписанию — по mtime файла.
fn last_auto(into: &Path) -> Option<u64> {
    ours(into, "-auto")
        .iter()
        .filter_map(|p| std::fs::metadata(p).and_then(|m| m.modified()).ok())
        .filter_map(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .max()
}

/// Пора ли снимать по расписанию. Чистая: время приходит снаружи.
pub fn due(every_days: u64, last: Option<u64>, now: u64) -> bool {
    if every_days == 0 {
        return false;
    }
    match last {
        None => true,
        Some(t) => now.saturating_sub(t) >= every_days * 86_400,
    }
}

/// Тик расписания: снять, если пора, и убрать лишние снимки этого вида. Памяти ещё
/// нет (первые минуты после установки) — снимать нечего. -> строка для журнала.
pub fn tick(root: &Path, cfg: Option<&serde_json::Value>) -> Option<String> {
    let p = policy(root, cfg);
    if !due(p.every_days, last_auto(&p.dir), now_secs()) {
        return None;
    }
    if !root.join("data").join("soul").exists() && !root.join("data").join("memory").exists() {
        return None;
    }
    let never = AtomicBool::new(false);
    Some(match snapshot(root, &p.dir, "auto", &never) {
        Ok((path, files)) => {
            let gone = prune(&p.dir, "-auto", p.keep);
            format!(
                "копия памяти по расписанию: {} ({files} файлов){}",
                path.display(),
                if gone.is_empty() { String::new() } else { format!("; старых убрано: {}", gone.len()) }
            )
        }
        Err(e) => format!("копия памяти по расписанию не снялась: {e}"),
    })
}

/// Снимки в папке — для окна: имя, байты, время (секунды от эпохи); свежие первыми.
pub fn list(into: &Path) -> Vec<(String, u64, u64)> {
    let Ok(rd) = std::fs::read_dir(into) else { return Vec::new() };
    let mut out: Vec<(String, u64, u64)> = rd
        .flatten()
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            if !name.ends_with(".zip") {
                return None;
            }
            let meta = e.metadata().ok()?;
            let when = meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs())
                .unwrap_or(0);
            Some((name, meta.len(), when))
        })
        .collect();
    out.sort_by(|a, b| b.0.cmp(&a.0));
    out
}
