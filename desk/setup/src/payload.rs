//! Поставка мастера: откуда берутся файлы, которые он ставит (1.2, 27.09).
//!
//! Источников два:
//! * **хвост** — с 1.2 установщик для людей один файл `Helene-<v>-setup.exe`: этот же
//!   exe, за ним архив поставки (tar, сжатый zstd) и 32 байта хвоста — `HLNPAYLD`,
//!   смещение архива (u64 LE), его длина (u64 LE), первые 8 байт sha256 архива.
//!   Мастер читает собственный файл и раскладывает архив прямо рядом с целевой
//!   папкой (`<папка>.new`, см. `tx.rs`), без распаковки в %TEMP%, как делал NSIS;
//! * **папка** — распакованный архив рядом с exe (кнопка «Обновить» в окне качает
//!   zip, распаковывает и зовёт оттуда `helene-setup.exe --update`) и установленная
//!   программа (мастер «на месте»).
//!
//! В начале архива сборка кладёт опись `.helene-payload.json` (версия, число файлов и
//! байт, «набор входа» — реле и ядро питона для входа в ChatGPT до установки), затем
//! паспорт сборки и манифест статики окна: всё, что мастеру нужно ДО раскладки,
//! читается с первых килобайт, без разжатия 650 МБ.
//!
//! Сборка — `installer/build_dist.py::build_setup_exe`. Формат хвоста там и здесь
//! один; стенд `tail_round_trip` держит его.
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

use serde::Deserialize;

pub const MAGIC: [u8; 8] = *b"HLNPAYLD";
pub const TAIL_LEN: usize = 32;
/// Опись поставки — первым файлом архива и в корне распакованной поставки.
pub const MANIFEST: &str = ".helene-payload.json";

/// Хвост установщика: где в собственном файле лежит архив и чем его сверить.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Tail {
    pub offset: u64,
    pub len: u64,
    pub sha8: [u8; 8],
}

/// Разбор последних 32 байт файла длиной `file_len`. None — это не наш хвост:
/// обычный exe (мастер в установленной программе), обрезанная закачка или
/// посторонние байты. Архив обязан лежать ровно между exe и хвостом.
pub fn parse_tail(raw: &[u8], file_len: u64) -> Option<Tail> {
    if raw.len() != TAIL_LEN || raw[..8] != MAGIC {
        return None;
    }
    let offset = u64::from_le_bytes(raw[8..16].try_into().ok()?);
    let len = u64::from_le_bytes(raw[16..24].try_into().ok()?);
    let mut sha8 = [0u8; 8];
    sha8.copy_from_slice(&raw[24..32]);
    if len == 0 || offset == 0 {
        return None;
    }
    if offset.checked_add(len)?.checked_add(TAIL_LEN as u64)? != file_len {
        return None;
    }
    Some(Tail { offset, len, sha8 })
}

/// Байты хвоста — для стендов (сборка пишет их питоном, `build_dist.py`).
#[cfg_attr(not(test), allow(dead_code))]
pub fn encode_tail(t: &Tail) -> [u8; TAIL_LEN] {
    let mut out = [0u8; TAIL_LEN];
    out[..8].copy_from_slice(&MAGIC);
    out[8..16].copy_from_slice(&t.offset.to_le_bytes());
    out[16..24].copy_from_slice(&t.len.to_le_bytes());
    out[24..32].copy_from_slice(&t.sha8);
    out
}

/// Хвост файла `exe`, если он есть.
pub fn read_tail(exe: &Path) -> Option<Tail> {
    use std::io::{Read, Seek, SeekFrom};
    let mut f = std::fs::File::open(exe).ok()?;
    let len = f.metadata().ok()?.len();
    if len < TAIL_LEN as u64 {
        return None;
    }
    f.seek(SeekFrom::End(-(TAIL_LEN as i64))).ok()?;
    let mut raw = [0u8; TAIL_LEN];
    f.read_exact(&mut raw).ok()?;
    parse_tail(&raw, len)
}

/// Опись поставки (`.helene-payload.json`). Поля — необязательные: у поставок до 1.2
/// описи нет вовсе, и мастер тогда считает сам.
#[derive(Deserialize, Default, Clone, Debug)]
#[allow(dead_code)] // product и files — для журнала и будущих проверок описи
pub struct Manifest {
    #[serde(default)]
    pub product: String,
    #[serde(default)]
    pub version: String,
    /// Файлов и байт в поставке — для хода раскладки.
    #[serde(default)]
    pub files: u64,
    #[serde(default)]
    pub bytes: u64,
    /// Набор входа в ChatGPT до установки: реле и ядро питона рантайма (без пакетов).
    #[serde(default)]
    pub kit: Vec<String>,
    /// Имена верхнего уровня поставки: по ним обновление отличает файлы прежней
    /// поставки от того, что владелец положил в папку сам.
    #[serde(default)]
    pub top: Vec<String>,
}

#[derive(Clone, Debug)]
pub enum Source {
    /// Архив в хвосте собственного exe.
    Tail { exe: PathBuf, tail: Tail },
    /// Распакованная поставка (или сама установка) в папке.
    Dir(PathBuf),
}

/// Что разложено.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Stats {
    pub files: u64,
    pub bytes: u64,
}

/// Отказ раскладки: отменили или сломалось. Отмена — не ошибка владельца, и мастер
/// говорит о ней другими словами.
#[derive(Debug, PartialEq, Eq)]
pub enum Stop {
    Cancelled,
    Failed(String),
}

impl From<String> for Stop {
    fn from(s: String) -> Self {
        Stop::Failed(s)
    }
}

impl std::fmt::Display for Stop {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Stop::Cancelled => write!(f, "отменено"),
            Stop::Failed(s) => write!(f, "{s}"),
        }
    }
}

/// Что не раскладывать: имена верхнего уровня и относительные пути (`runtime`,
/// `app/static`) — обновление оставляет их такими, какие стоят.
pub struct Skip<'a> {
    pub top: &'a [&'a str],
    pub rel: &'a [String],
}

/// Относительный путь записи архива или файла папки — только обычными частями.
/// `..`, корень, буква диска — чужой путь: раскладка его не пишет никогда.
pub fn safe_rel(p: &Path) -> Option<String> {
    let mut parts: Vec<String> = Vec::new();
    for c in p.components() {
        match c {
            Component::Normal(s) => {
                let s = s.to_str()?;
                // Двоеточие в имени — поток NTFS (`файл:поток`) или буква диска.
                if s.contains(':') || s.is_empty() {
                    return None;
                }
                parts.push(s.to_string());
            }
            Component::CurDir => {}
            _ => return None,
        }
    }
    if parts.is_empty() {
        None
    } else {
        Some(parts.join("/"))
    }
}

pub fn skipped(rel: &str, skip: &Skip) -> bool {
    let top = rel.split('/').next().unwrap_or("");
    if skip.top.iter().any(|t| *t == top) {
        return true;
    }
    skip.rel.iter().any(|s| rel == s.as_str() || rel.starts_with(&format!("{s}/")))
}

fn join_rel(root: &Path, rel: &str) -> PathBuf {
    let mut p = root.to_path_buf();
    for part in rel.split('/') {
        p.push(part);
    }
    p
}

/// Папки, уже созданные этой раскладкой: 17 тысяч файлов лежат в паре тысяч папок, и
/// спрашивать систему о каждой родительской папке на каждый файл незачем.
#[derive(Default)]
struct Made(std::collections::HashSet<PathBuf>);

impl Made {
    fn parent_of(&mut self, to: &Path) -> Result<(), Stop> {
        if let Some(parent) = to.parent() {
            if !self.0.contains(parent) {
                std::fs::create_dir_all(parent).map_err(|e| Stop::Failed(format!("{}: {e}", parent.display())))?;
                self.0.insert(parent.to_path_buf());
            }
        }
        Ok(())
    }
}

/// Копирование потока в файл кусками — с проверкой отмены между кусками: файл в
/// сотню мегабайт (onnxruntime, av) иначе держал бы «Отмену» секунды. Буфер — один
/// на всю раскладку (живая проба 27.09: по два мегабайта на каждый из 17 830 файлов
/// растягивали раскладку на минуты).
fn pour(
    reader: &mut dyn std::io::Read,
    to: &Path,
    cancel: &AtomicBool,
    buf: &mut [u8],
    made: &mut Made,
    on_bytes: &mut dyn FnMut(u64),
) -> Result<u64, Stop> {
    use std::io::Write;
    made.parent_of(to)?;
    let mut file = std::fs::File::create(to).map_err(|e| Stop::Failed(format!("{}: {e}", to.display())))?;
    let mut total = 0u64;
    loop {
        if cancel.load(Ordering::Relaxed) {
            return Err(Stop::Cancelled);
        }
        let n = reader.read(buf).map_err(|e| Stop::Failed(format!("чтение поставки: {e}")))?;
        if n == 0 {
            break;
        }
        file.write_all(&buf[..n]).map_err(|e| Stop::Failed(format!("{}: {e}", to.display())))?;
        total += n as u64;
        on_bytes(n as u64);
    }
    Ok(total)
}

impl Source {
    /// Где искать поставку: сначала хвост собственного exe, потом папка рядом.
    pub fn locate(exe: &Path, dir_markers_ok: impl Fn(&Path) -> bool) -> Option<Source> {
        if let Some(tail) = read_tail(exe) {
            return Some(Source::Tail { exe: exe.to_path_buf(), tail });
        }
        let here = exe.parent()?.to_path_buf();
        dir_markers_ok(&here).then_some(Source::Dir(here))
    }

    pub fn is_tail(&self) -> bool {
        matches!(self, Source::Tail { .. })
    }

    /// Путь для людей и журнала: exe установщика или папка поставки.
    pub fn path(&self) -> &Path {
        match self {
            Source::Tail { exe, .. } => exe,
            Source::Dir(d) => d,
        }
    }

    /// Папка поставки, если она распакована (для старых путей: реле, репетиция).
    pub fn dir(&self) -> Option<&Path> {
        match self {
            Source::Dir(d) => Some(d),
            Source::Tail { .. } => None,
        }
    }

    /// Опись поставки. У папки без описи (поставки до 1.2) — пустая.
    pub fn manifest(&self) -> Manifest {
        self.read(MANIFEST)
            .and_then(|b| serde_json::from_slice::<Manifest>(&b).ok())
            .unwrap_or_default()
    }

    /// Один маленький файл поставки (паспорт, манифест статики, опись). У хвоста
    /// смотрим только первые записи: сборка кладёт их вперёд; не нашлось — None,
    /// и мастер решает так, как решал бы без них (заменить, а не оставить).
    pub fn read(&self, rel: &str) -> Option<Vec<u8>> {
        match self {
            Source::Dir(d) => std::fs::read(join_rel(d, rel)).ok(),
            #[cfg(windows)]
            Source::Tail { exe, tail } => tar_read_head(exe, tail, rel, 96),
            #[cfg(not(windows))]
            Source::Tail { .. } => None,
        }
    }

    /// Сверить архив в хвосте с суммой, записанной сборкой: обрезанная или битая
    /// закачка должна остановить установку ДО первого файла, а не посреди.
    pub fn verify(&self) -> Result<(), String> {
        match self {
            Source::Dir(_) => Ok(()),
            Source::Tail { exe, tail } => {
                use sha2::Digest;
                use std::io::{Read, Seek, SeekFrom};
                let mut f = std::fs::File::open(exe).map_err(|e| format!("{}: {e}", exe.display()))?;
                f.seek(SeekFrom::Start(tail.offset)).map_err(|e| e.to_string())?;
                let mut take = f.take(tail.len);
                let mut hasher = sha2::Sha256::new();
                let mut buf = vec![0u8; 1 << 20];
                loop {
                    let n = take.read(&mut buf).map_err(|e| format!("установщик не читается: {e}"))?;
                    if n == 0 {
                        break;
                    }
                    hasher.update(&buf[..n]);
                }
                let sum = hasher.finalize();
                if sum[..8] != tail.sha8 {
                    return Err("установщик повреждён: сумма архива внутри не сходится — скачай его заново".into());
                }
                Ok(())
            }
        }
    }

    /// Разложить поставку в `dst` (обычно `<папка>.new`). `progress(байт, всего, файлов)`.
    pub fn extract(
        &self,
        dst: &Path,
        skip: &Skip,
        cancel: &AtomicBool,
        progress: &mut dyn FnMut(u64, u64, u64),
    ) -> Result<Stats, Stop> {
        match self {
            Source::Dir(src) => copy_dir(src, dst, skip, None, cancel, progress),
            #[cfg(windows)]
            Source::Tail { exe, tail } => {
                let total = self.manifest().bytes;
                tar_extract(exe, tail, dst, skip, None, total, cancel, progress)
            }
            #[cfg(not(windows))]
            Source::Tail { .. } => Err(Stop::Failed("архив в хвосте установщика — только Windows".into())),
        }
    }

    /// Набор входа в ChatGPT (реле и ядро питона) — в `dst`, до установки.
    pub fn extract_kit(&self, dst: &Path) -> Result<Stats, String> {
        let manifest = self.manifest();
        let never = AtomicBool::new(false);
        let skip = Skip { top: &[], rel: &[] };
        let mut quiet = |_: u64, _: u64, _: u64| {};
        let r = match self {
            Source::Dir(src) => copy_dir(src, dst, &skip, Some(&manifest.kit), &never, &mut quiet),
            #[cfg(windows)]
            Source::Tail { exe, tail } => {
                if manifest.kit.is_empty() {
                    return Err("в описи поставки нет набора входа".into());
                }
                tar_extract(exe, tail, dst, &skip, Some(&manifest.kit), 0, &never, &mut quiet)
            }
            #[cfg(not(windows))]
            Source::Tail { .. } => Err(Stop::Failed("архив в хвосте установщика — только Windows".into())),
        };
        r.map_err(|e| e.to_string())
    }
}

/// Копия распакованной поставки с ходом и отменой. `only` — только эти пути (набор входа).
fn copy_dir(
    src: &Path,
    dst: &Path,
    skip: &Skip,
    only: Option<&Vec<String>>,
    cancel: &AtomicBool,
    progress: &mut dyn FnMut(u64, u64, u64),
) -> Result<Stats, Stop> {
    // Сначала опись: сколько всего — для хода; заодно пути, которые не пишем.
    let mut files: Vec<(String, u64)> = Vec::new();
    fn walk(root: &Path, here: &Path, out: &mut Vec<(String, u64)>) -> Result<(), String> {
        for entry in std::fs::read_dir(here).map_err(|e| format!("{}: {e}", here.display()))? {
            let entry = entry.map_err(|e| e.to_string())?;
            let path = entry.path();
            let meta = entry.metadata().map_err(|e| format!("{}: {e}", path.display()))?;
            if meta.is_dir() {
                walk(root, &path, out)?;
            } else if meta.is_file() {
                let rel = path.strip_prefix(root).map_err(|e| e.to_string())?;
                if let Some(rel) = safe_rel(rel) {
                    out.push((rel, meta.len()));
                }
            }
        }
        Ok(())
    }
    walk(src, src, &mut files)?;
    files.retain(|(rel, _)| match only {
        Some(list) => list.iter().any(|k| k == rel),
        None => !skipped(rel, skip),
    });
    let total: u64 = files.iter().map(|(_, n)| n).sum();
    let mut done = 0u64;
    let mut count = 0u64;
    std::fs::create_dir_all(dst).map_err(|e| Stop::Failed(format!("{}: {e}", dst.display())))?;
    let mut made = Made::default();
    // Распакованная поставка лежит на диске — копирует система (CopyFileEx), отмена —
    // между файлами: самый большой файл поставки копируется за доли секунды.
    for (rel, size) in &files {
        if cancel.load(Ordering::Relaxed) {
            return Err(Stop::Cancelled);
        }
        let from = join_rel(src, rel);
        let to = join_rel(dst, rel);
        made.parent_of(&to)?;
        std::fs::copy(&from, &to).map_err(|e| Stop::Failed(format!("{}: {e}", to.display())))?;
        done += size;
        count += 1;
        progress(done, total, count);
    }
    Ok(Stats { files: count, bytes: done })
}

/// Поток архива из хвоста: File → срез [offset, offset+len) → zstd.
#[cfg(windows)]
fn tar_stream(
    exe: &Path,
    tail: &Tail,
) -> Result<tar::Archive<zstd::stream::read::Decoder<'static, std::io::BufReader<std::io::Take<std::fs::File>>>>, String> {
    use std::io::{Read, Seek, SeekFrom};
    let mut f = std::fs::File::open(exe).map_err(|e| format!("{}: {e}", exe.display()))?;
    f.seek(SeekFrom::Start(tail.offset)).map_err(|e| e.to_string())?;
    let limited = f.take(tail.len);
    let mut decoder = zstd::stream::read::Decoder::new(limited).map_err(|e| format!("архив поставки не читается: {e}"))?;
    // Сборка жмёт с окном 2^27 (дальние совпадения) — разрешить декодеру столько же.
    let _ = decoder.window_log_max(27);
    Ok(tar::Archive::new(decoder))
}

#[cfg(windows)]
fn tar_read_head(exe: &Path, tail: &Tail, want: &str, max_entries: usize) -> Option<Vec<u8>> {
    use std::io::Read;
    let mut archive = tar_stream(exe, tail).ok()?;
    for (i, entry) in archive.entries().ok()?.enumerate() {
        if i >= max_entries {
            return None;
        }
        let mut entry = entry.ok()?;
        let rel = safe_rel(&entry.path().ok()?)?;
        if rel == want {
            let mut out = Vec::new();
            entry.read_to_end(&mut out).ok()?;
            return Some(out);
        }
    }
    None
}

#[cfg(windows)]
#[allow(clippy::too_many_arguments)]
fn tar_extract(
    exe: &Path,
    tail: &Tail,
    dst: &Path,
    skip: &Skip,
    only: Option<&Vec<String>>,
    total: u64,
    cancel: &AtomicBool,
    progress: &mut dyn FnMut(u64, u64, u64),
) -> Result<Stats, Stop> {
    let mut archive = tar_stream(exe, tail)?;
    std::fs::create_dir_all(dst).map_err(|e| Stop::Failed(format!("{}: {e}", dst.display())))?;
    let mut done = 0u64;
    let mut count = 0u64;
    let mut buf = vec![0u8; 1 << 20];
    let mut made = Made::default();
    let mut wanted_left = only.map(|l| l.len()).unwrap_or(usize::MAX);
    let entries = archive.entries().map_err(|e| Stop::Failed(format!("архив поставки не читается: {e}")))?;
    for entry in entries {
        if cancel.load(Ordering::Relaxed) {
            return Err(Stop::Cancelled);
        }
        let mut entry = entry.map_err(|e| Stop::Failed(format!("архив поставки не читается: {e}")))?;
        let raw = entry.path().map_err(|e| Stop::Failed(format!("имя в архиве: {e}")))?.into_owned();
        let Some(rel) = safe_rel(&raw) else {
            return Err(Stop::Failed(format!("в архиве чужой путь: {}", raw.display())));
        };
        let kind = entry.header().entry_type();
        let wanted = match only {
            Some(list) => list.iter().any(|k| *k == rel),
            None => !skipped(&rel, skip),
        };
        if kind.is_dir() {
            if wanted && only.is_none() {
                let to = join_rel(dst, &rel);
                std::fs::create_dir_all(&to).map_err(|e| Stop::Failed(format!("{}: {e}", to.display())))?;
                made.0.insert(to);
            }
            continue;
        }
        if !kind.is_file() {
            // Ссылок в поставке Windows нет по построению; незнакомое — не писать.
            continue;
        }
        if !wanted {
            // Разжать всё равно придётся (tar читается подряд), но на диск не пишем;
            // ход считаем и по пропущенному — иначе полоса стояла бы на рантайме.
            let size = entry.header().size().unwrap_or(0);
            let mut sink = std::io::sink();
            std::io::copy(&mut entry, &mut sink).map_err(|e| Stop::Failed(format!("архив поставки: {e}")))?;
            done += size;
            progress(done, total, count);
            continue;
        }
        let to = join_rel(dst, &rel);
        pour(&mut entry, &to, cancel, &mut buf, &mut made, &mut |n| {
            done += n;
        })?;
        count += 1;
        progress(done, total, count);
        if only.is_some() {
            wanted_left = wanted_left.saturating_sub(1);
            if wanted_left == 0 {
                break;
            }
        }
    }
    Ok(Stats { files: count, bytes: done })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tail_round_trip() {
        let t = Tail { offset: 7_340_032, len: 172_000_000, sha8: *b"\x01\x02\x03\x04\x05\x06\x07\x08" };
        let raw = encode_tail(&t);
        assert_eq!(&raw[..8], b"HLNPAYLD");
        assert_eq!(parse_tail(&raw, t.offset + t.len + 32), Some(t));
        // Обрезанная закачка или лишний байт — уже не хвост.
        assert_eq!(parse_tail(&raw, t.offset + t.len + 31), None);
        assert_eq!(parse_tail(&raw, t.offset + t.len + 33), None);
        let mut wrong = raw;
        wrong[0] = b'X';
        assert_eq!(parse_tail(&wrong, t.offset + t.len + 32), None);
    }

    #[test]
    fn read_tail_finds_the_archive_behind_the_exe() {
        let dir = std::env::temp_dir().join(format!("helene-tail-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let exe = dir.join("setup.exe");
        let body = b"MZ-this-is-an-exe".to_vec();
        let archive = b"ARCHIVE-BYTES".to_vec();
        use sha2::Digest;
        let sum = sha2::Sha256::digest(&archive);
        let mut sha8 = [0u8; 8];
        sha8.copy_from_slice(&sum[..8]);
        let tail = Tail { offset: body.len() as u64, len: archive.len() as u64, sha8 };
        let mut all = body.clone();
        all.extend_from_slice(&archive);
        all.extend_from_slice(&encode_tail(&tail));
        std::fs::write(&exe, &all).unwrap();
        assert_eq!(read_tail(&exe), Some(tail));
        let src = Source::Tail { exe: exe.clone(), tail };
        assert!(src.verify().is_ok());
        // Испорченный байт архива — сумма не сходится.
        let mut broken = all.clone();
        broken[body.len() + 2] ^= 0xff;
        std::fs::write(&exe, &broken).unwrap();
        assert!(src.verify().is_err());
        // Обычный exe без хвоста.
        std::fs::write(&exe, &body).unwrap();
        assert_eq!(read_tail(&exe), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn foreign_paths_are_refused() {
        assert_eq!(safe_rel(Path::new("app/deskapp.py")).as_deref(), Some("app/deskapp.py"));
        assert_eq!(safe_rel(Path::new("./helene.exe")).as_deref(), Some("helene.exe"));
        assert_eq!(safe_rel(Path::new("../evil.exe")), None);
        assert_eq!(safe_rel(Path::new("app/../../evil.exe")), None);
        assert_eq!(safe_rel(Path::new("/etc/passwd")), None);
        #[cfg(windows)]
        {
            assert_eq!(safe_rel(Path::new("C:\\Windows\\evil.exe")), None);
            assert_eq!(safe_rel(Path::new("file.txt:stream")), None);
        }
        assert_eq!(safe_rel(Path::new("")), None);
    }

    #[test]
    fn skip_rules_cover_top_names_and_subtrees() {
        let rel = vec!["runtime".to_string(), "app/static".to_string()];
        let skip = Skip { top: &["helene.json", "data"], rel: &rel };
        assert!(skipped("helene.json", &skip));
        assert!(skipped("data/soul/SOUL.md", &skip));
        assert!(skipped("runtime/python.exe", &skip));
        assert!(skipped("app/static/index.html", &skip));
        assert!(skipped("app/static", &skip));
        assert!(!skipped("app/staticky.txt", &skip));
        assert!(!skipped("app/deskapp.py", &skip));
        assert!(!skipped("helene.exe", &skip));
    }

    #[test]
    fn dir_source_copies_with_progress_and_honours_cancel() {
        let root = std::env::temp_dir().join(format!("helene-dirsrc-{}", std::process::id()));
        let src = root.join("src");
        let dst = root.join("dst");
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(src.join("app")).unwrap();
        std::fs::create_dir_all(src.join("data")).unwrap();
        std::fs::write(src.join("helene.exe"), b"exe").unwrap();
        std::fs::write(src.join("helene.json"), b"{}").unwrap();
        std::fs::write(src.join("app").join("deskapp.py"), b"print(1)").unwrap();
        std::fs::write(src.join("data").join("x"), b"x").unwrap();
        let source = Source::Dir(src.clone());
        let never = AtomicBool::new(false);
        let skip = Skip { top: &["helene.json", "data"], rel: &[] };
        let mut last = (0, 0, 0);
        let stats = source
            .extract(&dst, &skip, &never, &mut |d, t, n| last = (d, t, n))
            .unwrap();
        assert_eq!(stats.files, 2);
        assert_eq!(last, (11, 11, 2));
        assert!(dst.join("helene.exe").is_file());
        assert!(dst.join("app").join("deskapp.py").is_file());
        assert!(!dst.join("helene.json").exists());
        assert!(!dst.join("data").exists());
        let cancelled = AtomicBool::new(true);
        let r = source.extract(&root.join("dst2"), &skip, &cancelled, &mut |_, _, _| {});
        assert_eq!(r.unwrap_err(), Stop::Cancelled);
        let _ = std::fs::remove_dir_all(&root);
    }

    /// Хвост целиком: tar+zstd, как собирает build_dist.py, — опись первой, набор входа,
    /// пропуски и отмена посреди.
    #[cfg(windows)]
    #[test]
    fn tail_archive_extracts_reads_head_and_kit() {
        use sha2::Digest;
        let root = std::env::temp_dir().join(format!("helene-tailx-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let mut tar_bytes: Vec<u8> = Vec::new();
        {
            let mut b = tar::Builder::new(&mut tar_bytes);
            let mut add = |name: &str, data: &[u8]| {
                let mut h = tar::Header::new_gnu();
                h.set_size(data.len() as u64);
                h.set_mode(0o644);
                h.set_entry_type(tar::EntryType::Regular);
                h.set_cksum();
                b.append_data(&mut h, name, data).unwrap();
            };
            add(MANIFEST, br#"{"product":"Helene","version":"9.9.9","files":4,"bytes":20,"kit":["helene-relay.exe","runtime/python.exe"],"top":["helene.exe","runtime","helene-relay.exe","helene.json"]}"#);
            add("helene-relay.exe", b"relay");
            add("runtime/python.exe", b"py");
            add("runtime/Lib/site.py", b"site-pack");
            add("helene.exe", b"exe");
            add("helene.json", b"{}");
            b.finish().unwrap();
        }
        let packed = zstd::encode_all(&tar_bytes[..], 3).unwrap();
        let body = b"MZ-exe".to_vec();
        let sum = sha2::Sha256::digest(&packed);
        let mut sha8 = [0u8; 8];
        sha8.copy_from_slice(&sum[..8]);
        let tail = Tail { offset: body.len() as u64, len: packed.len() as u64, sha8 };
        let exe = root.join("Helene-9.9.9-setup.exe");
        let mut all = body.clone();
        all.extend_from_slice(&packed);
        all.extend_from_slice(&encode_tail(&tail));
        std::fs::write(&exe, &all).unwrap();

        let src = Source::locate(&exe, |_| false).expect("хвост найден");
        assert!(src.is_tail());
        src.verify().unwrap();
        let m = src.manifest();
        assert_eq!(m.version, "9.9.9");
        assert_eq!(m.kit.len(), 2);

        let kit = root.join("kit");
        let got = src.extract_kit(&kit).unwrap();
        assert_eq!(got.files, 2);
        assert!(kit.join("helene-relay.exe").is_file());
        assert!(kit.join("runtime").join("python.exe").is_file());
        assert!(!kit.join("helene.exe").exists());

        let dst = root.join("Helene.new");
        let never = AtomicBool::new(false);
        let rel = vec!["runtime".to_string()];
        let skip = Skip { top: &["helene.json", "data"], rel: &rel };
        let stats = src.extract(&dst, &skip, &never, &mut |_, _, _| {}).unwrap();
        assert!(dst.join("helene.exe").is_file());
        assert!(dst.join(MANIFEST).is_file());
        assert!(!dst.join("helene.json").exists());
        assert!(!dst.join("runtime").exists());
        assert_eq!(stats.files, 3);

        let cancelled = AtomicBool::new(true);
        let r = src.extract(&root.join("x.new"), &skip, &cancelled, &mut |_, _, _| {});
        assert_eq!(r.unwrap_err(), Stop::Cancelled);
        let _ = std::fs::remove_dir_all(&root);
    }
}
