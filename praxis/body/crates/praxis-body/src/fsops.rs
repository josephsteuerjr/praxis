use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use std::time::Instant;

use anyhow::{Context, Result};
use serde::Deserialize;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

#[derive(Debug, Deserialize)]
struct PathArgs {
    path: PathBuf,
}

#[derive(Debug, Deserialize)]
struct ListArgs {
    path: PathBuf,
    #[serde(default)]
    offset: usize,
    #[serde(default = "default_list_limit")]
    limit: usize,
}

#[derive(Debug, Deserialize)]
struct ReadArgs {
    path: PathBuf,
    #[serde(default)]
    offset: u64,
    #[serde(default = "default_read_limit")]
    limit: u64,
}

#[derive(Debug, Deserialize)]
struct WriteArgs {
    path: PathBuf,
    content: String,
    #[serde(default)]
    expected_sha256: Option<String>,
    #[serde(default)]
    backup: bool,
}

#[derive(Debug, Deserialize)]
struct ReplaceArgs {
    path: PathBuf,
    old: String,
    new: String,
    #[serde(default)]
    expected_sha256: Option<String>,
    #[serde(default)]
    backup: bool,
}

#[derive(Debug, Deserialize)]
struct PatchArgs {
    root: PathBuf,
    patch: String,
}

#[derive(Debug, Deserialize)]
struct DeleteArgs {
    path: PathBuf,
    #[serde(default)]
    recursive: bool,
    #[serde(flatten)]
    extra: Map<String, Value>,
}

#[derive(Debug, Deserialize)]
struct MoveArgs {
    #[serde(alias = "source", alias = "src")]
    from: PathBuf,
    #[serde(alias = "destination", alias = "dst")]
    to: PathBuf,
    #[serde(default)]
    overwrite: bool,
    #[serde(flatten)]
    extra: Map<String, Value>,
}

#[derive(Debug, Deserialize)]
struct CopyArgs {
    #[serde(alias = "source", alias = "src")]
    from: PathBuf,
    #[serde(alias = "destination", alias = "dst")]
    to: PathBuf,
    #[serde(default)]
    recursive: bool,
    #[serde(default)]
    overwrite: bool,
    #[serde(flatten)]
    extra: Map<String, Value>,
}

#[derive(Debug, Deserialize)]
struct MkdirArgs {
    path: PathBuf,
    #[serde(default = "default_parents")]
    parents: bool,
    #[serde(flatten)]
    extra: Map<String, Value>,
}

fn default_read_limit() -> u64 {
    1024 * 1024
}

fn default_list_limit() -> usize {
    1_000
}

fn default_parents() -> bool {
    true
}

pub fn dispatch(capability: &str, args: Value) -> Result<Value> {
    match capability {
        "fs.stat" => stat(serde_json::from_value(args)?),
        "fs.list" => list(serde_json::from_value(args)?),
        "fs.read" => read(serde_json::from_value(args)?),
        "fs.hash" => hash_value(serde_json::from_value(args)?),
        "fs.write_atomic" => write_atomic(serde_json::from_value(args)?),
        "fs.replace" => replace(serde_json::from_value(args)?),
        "fs.apply_patch" => apply_patch(serde_json::from_value(args)?),
        "fs.delete" => delete(serde_json::from_value(args)?),
        "fs.move" => move_path(serde_json::from_value(args)?),
        "fs.copy" => copy(serde_json::from_value(args)?),
        "fs.mkdir" => mkdir(serde_json::from_value(args)?),
        _ => anyhow::bail!("unknown filesystem capability {capability}"),
    }
}

fn apply_patch(args: PatchArgs) -> Result<Value> {
    if args.patch.trim().is_empty() {
        anyhow::bail!("unified patch is empty");
    }
    let stage = std::env::temp_dir().join(format!("praxis-{}.patch", Uuid::new_v4()));
    fs::write(&stage, args.patch.as_bytes())?;
    let mut check_command = std::process::Command::new("git.exe");
    check_command
        .args(["-C"])
        .arg(&args.root)
        .args(["apply", "--check", "--whitespace=nowarn"])
        .arg(&stage);
    let check = command_output(&mut check_command).context("run git apply --check")?;
    if !check.status.success() {
        let _ = fs::remove_file(&stage);
        anyhow::bail!(
            "patch check failed: {}",
            String::from_utf8_lossy(&check.stderr)
        );
    }
    let mut apply_command = std::process::Command::new("git.exe");
    apply_command
        .args(["-C"])
        .arg(&args.root)
        .args(["apply", "--whitespace=nowarn"])
        .arg(&stage);
    let applied = command_output(&mut apply_command).context("run git apply")?;
    let _ = fs::remove_file(&stage);
    if !applied.status.success() {
        anyhow::bail!(
            "patch apply failed after check: {}",
            String::from_utf8_lossy(&applied.stderr)
        );
    }
    Ok(json!({
        "ok": true,
        "root": args.root,
        "patch_sha256": hex::encode(Sha256::digest(args.patch.as_bytes())),
    }))
}

fn command_output(command: &mut std::process::Command) -> std::io::Result<std::process::Output> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        use windows::Win32::System::Threading::CREATE_NO_WINDOW;

        command.creation_flags(CREATE_NO_WINDOW.0);
    }
    command.output()
}

fn stat(args: PathArgs) -> Result<Value> {
    let metadata = fs::symlink_metadata(&args.path)
        .with_context(|| format!("stat {}", args.path.display()))?;
    Ok(json!({
        "ok": true,
        "path": args.path,
        "kind": if metadata.file_type().is_symlink() { "reparse" } else if metadata.is_dir() { "directory" } else if metadata.is_file() { "file" } else { "other" },
        "size": metadata.len(),
        "readonly": metadata.permissions().readonly(),
        "modified_unix_ms": metadata.modified().ok().and_then(|x| x.duration_since(std::time::UNIX_EPOCH).ok()).map(|x| x.as_millis()),
    }))
}

fn list(args: ListArgs) -> Result<Value> {
    const MAX_SCAN: usize = 100_000;
    let limit = args.limit.clamp(1, 4_096);
    let mut rows = Vec::new();
    for entry in
        fs::read_dir(&args.path).with_context(|| format!("list {}", args.path.display()))?
    {
        if rows.len() >= MAX_SCAN {
            anyhow::bail!(
                "directory contains more than {MAX_SCAN} entries; use rg or a narrower path"
            );
        }
        let entry = entry?;
        let metadata = entry.metadata()?;
        rows.push(json!({
            "name": entry.file_name().to_string_lossy(),
            "path": entry.path(),
            "kind": if metadata.is_dir() { "directory" } else if metadata.is_file() { "file" } else { "other" },
            "size": metadata.len(),
        }));
    }
    rows.sort_by(|a, b| a["name"].as_str().cmp(&b["name"].as_str()));
    let total = rows.len();
    let start = args.offset.min(total);
    let end = start.saturating_add(limit).min(total);
    let entries = rows.drain(start..end).collect::<Vec<_>>();
    Ok(json!({
        "ok": true,
        "path": args.path,
        "offset": start,
        "limit": limit,
        "total": total,
        "next_offset": end,
        "eof": end == total,
        "entries": entries,
    }))
}

fn read(args: ReadArgs) -> Result<Value> {
    let limit = args.limit.clamp(1, 8 * 1024 * 1024);
    let mut file =
        File::open(&args.path).with_context(|| format!("read {}", args.path.display()))?;
    let size = file.metadata()?.len();
    if args.offset > size {
        anyhow::bail!("offset {} is beyond file size {}", args.offset, size);
    }
    file.seek(SeekFrom::Start(args.offset))?;
    let mut bytes = vec![0u8; limit.min(size - args.offset) as usize];
    file.read_exact(&mut bytes)?;
    Ok(json!({
        "ok": true,
        "path": args.path,
        "offset": args.offset,
        "size": size,
        "next_offset": args.offset + bytes.len() as u64,
        "eof": args.offset + bytes.len() as u64 == size,
        "text": String::from_utf8_lossy(&bytes),
        "lossy": std::str::from_utf8(&bytes).is_err(),
    }))
}

fn hash_value(args: PathArgs) -> Result<Value> {
    let (sha256, size) = sha256_file(&args.path)?;
    Ok(json!({"ok": true, "path": args.path, "sha256": sha256, "size": size}))
}

fn write_atomic(args: WriteArgs) -> Result<Value> {
    let before = existing_hash(&args.path)?;
    check_expected(before.as_deref(), args.expected_sha256.as_deref())?;
    let backup = if args.backup && args.path.exists() {
        Some(create_backup(&args.path)?)
    } else {
        None
    };
    atomic_write(&args.path, args.content.as_bytes())?;
    let (after, size) = sha256_file(&args.path)?;
    Ok(json!({
        "ok": true,
        "path": args.path,
        "before_sha256": before,
        "after_sha256": after,
        "size": size,
        "backup": backup,
    }))
}

fn replace(args: ReplaceArgs) -> Result<Value> {
    let before = existing_hash(&args.path)?;
    check_expected(before.as_deref(), args.expected_sha256.as_deref())?;
    let text = fs::read_to_string(&args.path)
        .with_context(|| format!("read text {}", args.path.display()))?;
    let count = text.matches(&args.old).count();
    if count != 1 {
        anyhow::bail!("exact replace requires one match, found {count}");
    }
    let backup = if args.backup {
        Some(create_backup(&args.path)?)
    } else {
        None
    };
    atomic_write(
        &args.path,
        text.replacen(&args.old, &args.new, 1).as_bytes(),
    )?;
    let (after, size) = sha256_file(&args.path)?;
    Ok(json!({
        "ok": true,
        "path": args.path,
        "before_sha256": before,
        "after_sha256": after,
        "size": size,
        "backup": backup,
    }))
}

// ── Дерево: удалить, переместить, скопировать, создать ───────────────────────────────
//
// ⚠ Десять файловых глаголов умели читать файл и писать файл — и ни один не менял дерево.
// Каждое «удали это» она делала запуском PowerShell: отдельная отслеживаемая операция со
// своим каталогом, которая остаётся в реестре навсегда (на этой машине таких записей уже
// 243), секунды вместо миллисекунд и мусор в учёте. Четыре глагола ниже закрывают дыру.
//
// Три правила, общие для всех четырёх:
//
//  1. Рекурсия — её явный выбор, а не наша догадка. «Удали пустой каталог» и «снеси
//     дерево» — разные намерения. Это не забор: отказ называет ровно то слово, которое
//     надо дописать, и повтор с ним проходит без всякого подтверждения.
//  2. Все пределы едут в ответе полем `limits` — даже когда до них не дошло. Молчаливый
//     предел хуже отсутствующего: об отсутствующий не спотыкаются вслепую.
//  3. Частичный результат называется частичным: `complete:false`, `failed[]` и `summary`,
//     который начинается со слова PARTIAL. Полагаться на внешнее поле `ok` здесь нельзя —
//     `body_client.call` собирает ответ как `{**frame, **result, "ok": frame["ok"]}`, то
//     есть наш `ok` перетирается статусом кадра. Поэтому правда живёт в `complete`,
//     `failed_count` и `summary`, которые никто не перетирает.

/// Сколько узлов дерева обходим за один вызов, прежде чем ответить частично.
const MAX_TREE_ENTRIES: usize = 200_000;
/// Сколько секунд работаем, прежде чем ответить частично.
///
/// ⚠ Число выбрано не с потолка: сервер зовёт тело с `timeout=60` (`body_client.call`).
/// Ответ, который не успел вернуться, для неё неотличим от «тело умерло» — а честное
/// «снесла 40 тысяч файлов из 300 тысяч, кончилось время» она может продолжить повтором.
const MAX_TREE_SECONDS: u64 = 45;
/// Сколько отказов перечисляем поимённо (счётчик `failed_count` — полный, без капа).
const MAX_FAILURES_REPORTED: usize = 20;
/// Сколько созданных каталогов перечисляем поимённо в `fs.mkdir`.
const MAX_CREATED_DIRS_REPORTED: usize = 64;

fn tree_limits() -> Value {
    json!({
        "max_entries": MAX_TREE_ENTRIES,
        "max_seconds": MAX_TREE_SECONDS,
        "max_failures_reported": MAX_FAILURES_REPORTED,
    })
}

/// Неизвестные аргументы не проглатываем и не роняем из-за них вызов: называем.
///
/// ⚠ Опечатка `recursiv: true` при молчаливом serde означала бы «рекурсия выключена» —
/// ровно тот молчаливый предел, которого в этом проекте быть не должно. Отвергать вызов
/// целиком тоже неправильно: тогда одно лишнее поле от диспетчера ломает глагол.
fn ignored_args(extra: &Map<String, Value>) -> Vec<String> {
    let mut names: Vec<String> = extra.keys().cloned().collect();
    names.sort();
    names
}

/// Бюджет обхода: узлы и время. Кончился — не идём дальше, но и не врём.
struct Budget {
    started: Instant,
    entries: usize,
    stopped: Option<String>,
}

impl Budget {
    fn new() -> Self {
        Self {
            started: Instant::now(),
            entries: 0,
            stopped: None,
        }
    }

    /// Оплатить один узел. `false` — бюджет исчерпан, причина уже записана.
    fn spend(&mut self) -> bool {
        if !self.time_available() {
            return false;
        }
        if self.entries >= MAX_TREE_ENTRIES {
            self.stopped = Some(format!(
                "entry limit reached: {MAX_TREE_ENTRIES} nodes visited"
            ));
            return false;
        }
        self.entries += 1;
        true
    }

    fn time_available(&mut self) -> bool {
        if self.stopped.is_some() {
            return false;
        }
        let elapsed = self.started.elapsed().as_secs();
        if elapsed >= MAX_TREE_SECONDS {
            self.stopped = Some(format!(
                "time limit reached: {elapsed}s of {MAX_TREE_SECONDS}s allowed per call"
            ));
            return false;
        }
        true
    }

    fn elapsed_ms(&self) -> u128 {
        self.started.elapsed().as_millis()
    }
}

/// Что именно случилось с деревом — считаем по ходу, чтобы ответ говорил правду числами.
#[derive(Default)]
struct Tally {
    files: u64,
    dirs: u64,
    links: u64,
    bytes: u64,
    cleared_readonly: u64,
    overwritten: u64,
    skipped_existing: u64,
    skipped_reparse: u64,
    failed_total: u64,
    failed: Vec<Value>,
}

impl Tally {
    fn fail(&mut self, path: &Path, error: impl std::fmt::Display) {
        self.failed_total += 1;
        if self.failed.len() < MAX_FAILURES_REPORTED {
            self.failed
                .push(json!({"path": path, "error": error.to_string()}));
        }
    }

    fn failure_report(&self) -> Value {
        json!({
            "failed_count": self.failed_total,
            "failed": self.failed,
            "failed_truncated": self.failed_total as usize > self.failed.len(),
        })
    }
}

fn kind_of(metadata: &fs::Metadata) -> &'static str {
    let file_type = metadata.file_type();
    if file_type.is_symlink() {
        "reparse"
    } else if file_type.is_dir() {
        "directory"
    } else if file_type.is_file() {
        "file"
    } else {
        "other"
    }
}

/// Точка повторной обработки, которая ведёт в каталог (junction или symlink на папку).
///
/// ⚠ `FileType::is_dir()` для связи всегда false, поэтому без этой проверки junction
/// пришлось бы удалять как файл — а Windows такое не делает и вернула бы отказ в доступе.
fn is_directory_like(file_type: &fs::FileType) -> bool {
    if file_type.is_dir() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::FileTypeExt;
        file_type.is_symlink_dir()
    }
    #[cfg(not(windows))]
    {
        false
    }
}

/// Windows junctions and mount points are reparse points even when FileType::is_symlink
/// does not classify them as symbolic links.
fn is_reparse(metadata: &fs::Metadata) -> bool {
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0 // FILE_ATTRIBUTE_REPARSE_POINT
    }
    #[cfg(not(windows))]
    {
        false
    }
}

/// Inspect every existing component: create_dir_all and fs::copy otherwise follow
/// a pre-existing junction inside the destination tree.
fn check_copy_destination(path: &Path) -> std::io::Result<()> {
    for component in path.ancestors() {
        match fs::symlink_metadata(component) {
            Ok(metadata) if is_reparse(&metadata) => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidInput,
                    format!(
                        "destination reparse point is not followed: {}",
                        component.display()
                    ),
                ));
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

/// Снять «только чтение» и повторить. Возвращает true, если атрибут действительно сняли.
///
/// ⚠ Живой случай: любой клон гита — файлы в `.git/objects` на Windows лежат read-only.
/// Проверено на этой машине (rustc 1.94): `fs::remove_file` такой файл сносит сам, молча,
/// а `fs::copy` ПОВЕРХ такого файла возвращает «отказано в доступе» (код 5). То есть
/// счётчик `cleared_readonly` почти всегда набивает копирование, а не удаление — и в
/// ответе он значит ровно «сколько раз атрибут снимали МЫ», а не «сколько было read-only».
/// Спрашивать её «снять атрибут?» было бы забором на ровном месте: она сказала «скопируй»,
/// права у неё полные. Наше дело — не спрятать, что атрибут пришлось снять.
fn clear_readonly(path: &Path) -> bool {
    let Ok(metadata) = fs::symlink_metadata(path) else {
        return false;
    };
    let mut permissions = metadata.permissions();
    if !permissions.readonly() {
        return false;
    }
    permissions.set_readonly(false);
    fs::set_permissions(path, permissions).is_ok()
}

fn remove_entry(path: &Path, as_directory: bool, tally: &mut Tally) -> std::io::Result<()> {
    let once = |path: &Path| {
        if as_directory {
            fs::remove_dir(path)
        } else {
            fs::remove_file(path)
        }
    };
    match once(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::PermissionDenied => {
            if clear_readonly(path) {
                let retried = once(path);
                if retried.is_ok() {
                    tally.cleared_readonly += 1;
                }
                retried
            } else {
                Err(error)
            }
        }
        Err(error) => Err(error),
    }
}

fn copy_file_forgiving(source: &Path, target: &Path, tally: &mut Tally) -> std::io::Result<u64> {
    match fs::copy(source, target) {
        Ok(bytes) => Ok(bytes),
        Err(error) if error.kind() == std::io::ErrorKind::PermissionDenied => {
            if clear_readonly(target) {
                let retried = fs::copy(source, target);
                if retried.is_ok() {
                    tally.cleared_readonly += 1;
                }
                retried
            } else {
                Err(error)
            }
        }
        Err(error) => Err(error),
    }
}

fn delete(args: DeleteArgs) -> Result<Value> {
    let mut budget = Budget::new();
    let mut tally = Tally::default();
    let metadata = match fs::symlink_metadata(&args.path) {
        Ok(metadata) => metadata,
        // ⚠ «Нечего удалять» имеет право сказать ТОЛЬКО NotFound. Отказ в доступе или
        // сбойный том — это «не смогла посмотреть», и выдать его за «уже пусто» значит
        // соврать ровно тем способом, ради которого затевалась эта волна.
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(json!({
                "ok": true,
                "path": args.path,
                "existed": false,
                "recursive": args.recursive,
                "removed_files": 0,
                "removed_dirs": 0,
                "removed_links": 0,
                "removed_bytes": 0,
                "cleared_readonly": 0,
                "complete": true,
                "stopped_because": Value::Null,
                "failed_count": 0,
                "failed": [],
                "failed_truncated": false,
                "elapsed_ms": budget.elapsed_ms(),
                // Живой случай: две операции 13–14 июля били по `C:\Users\Egor` и
                // `C:\Users\Егор`, а на диске только `yegor`. Тихое «ок» на такую
                // опечатку — это ложь, из которой не выбраться.
                "summary": format!("nothing deleted: {} does not exist", args.path.display()),
                "ignored_args": ignored_args(&args.extra),
                "limits": tree_limits(),
            }));
        }
        Err(error) => {
            return Err(anyhow::Error::new(error))
                .with_context(|| format!("delete {}", args.path.display()));
        }
    };
    let kind = kind_of(&metadata);
    let file_type = metadata.file_type();
    if file_type.is_symlink() {
        // Саму связь снимаем, в цель не заходим: «удали эту папку» про junction никогда
        // не значит «вычисти чужой каталог на том конце».
        match remove_entry(&args.path, is_directory_like(&file_type), &mut tally) {
            Ok(()) => tally.links += 1,
            Err(error) => tally.fail(&args.path, error),
        }
    } else if file_type.is_dir() {
        if args.recursive {
            delete_tree(&args.path, &mut budget, &mut tally);
        } else {
            let mut entries = fs::read_dir(&args.path)
                .with_context(|| format!("delete {}", args.path.display()))?;
            if entries.next().is_some() {
                anyhow::bail!(
                    "{} is a directory and it is not empty: this call deletes an empty \
                     directory only. Repeat with recursive=true to delete the whole tree.",
                    args.path.display()
                );
            }
            drop(entries);
            match remove_entry(&args.path, true, &mut tally) {
                Ok(()) => tally.dirs += 1,
                Err(error) => tally.fail(&args.path, error),
            }
        }
    } else {
        let size = metadata.len();
        match remove_entry(&args.path, false, &mut tally) {
            Ok(()) => {
                tally.files += 1;
                tally.bytes += size;
            }
            Err(error) => tally.fail(&args.path, error),
        }
    }
    let complete = budget.stopped.is_none() && tally.failed_total == 0;
    let mut summary = format!(
        "deleted {} file(s), {} directory(ies), {} link(s), {} byte(s) under {}",
        tally.files,
        tally.dirs,
        tally.links,
        tally.bytes,
        args.path.display()
    );
    if tally.cleared_readonly > 0 {
        summary.push_str(&format!(
            "; cleared the read-only attribute on {} entry(ies) to do it",
            tally.cleared_readonly
        ));
    }
    if !complete {
        summary = format!(
            "PARTIAL: {summary}; {} entry(ies) could not be removed{}",
            tally.failed_total,
            budget
                .stopped
                .as_deref()
                .map(|reason| format!("; stopped early: {reason}"))
                .unwrap_or_default()
        );
    }
    Ok(json!({
        "ok": complete,
        "path": args.path,
        "existed": true,
        "kind": kind,
        "recursive": args.recursive,
        "removed_files": tally.files,
        "removed_dirs": tally.dirs,
        "removed_links": tally.links,
        "removed_bytes": tally.bytes,
        "cleared_readonly": tally.cleared_readonly,
        "complete": complete,
        "stopped_because": budget.stopped,
        "failed_count": tally.failed_total,
        "failed": tally.failed,
        "failed_truncated": tally.failed_total as usize > tally.failed.len(),
        "elapsed_ms": budget.elapsed_ms(),
        "summary": summary,
        "ignored_args": ignored_args(&args.extra),
        "limits": tree_limits(),
    }))
}

/// Обход снизу вверх, итеративно.
///
/// ⚠ Не рекурсией: в её рабочих деревьях лежат `node_modules` и `target`, на которых
/// рекурсивный обход съедает стек — а падение тела здесь означало бы наполовину снесённое
/// дерево вообще без ответа о том, что успело исчезнуть.
fn delete_tree(root: &Path, budget: &mut Budget, tally: &mut Tally) {
    let mut stack: Vec<(PathBuf, bool)> = vec![(root.to_path_buf(), false)];
    while let Some((path, children_visited)) = stack.pop() {
        if children_visited {
            match remove_entry(&path, true, tally) {
                Ok(()) => tally.dirs += 1,
                Err(error) => tally.fail(&path, error),
            }
            continue;
        }
        if !budget.spend() {
            return;
        }
        let metadata = match fs::symlink_metadata(&path) {
            Ok(metadata) => metadata,
            Err(error) => {
                tally.fail(&path, error);
                continue;
            }
        };
        let file_type = metadata.file_type();
        if file_type.is_symlink() {
            match remove_entry(&path, is_directory_like(&file_type), tally) {
                Ok(()) => tally.links += 1,
                Err(error) => tally.fail(&path, error),
            }
        } else if file_type.is_dir() {
            match fs::read_dir(&path) {
                Ok(entries) => {
                    stack.push((path.clone(), true));
                    for entry in entries {
                        match entry {
                            Ok(entry) => stack.push((entry.path(), false)),
                            Err(error) => tally.fail(&path, error),
                        }
                    }
                }
                Err(error) => tally.fail(&path, error),
            }
        } else {
            let size = metadata.len();
            match remove_entry(&path, false, tally) {
                Ok(()) => {
                    tally.files += 1;
                    tally.bytes += size;
                }
                Err(error) => tally.fail(&path, error),
            }
        }
    }
}

/// Сравнимый ключ пути: канонизируем ближайшего существующего предка и досыпаем хвост.
///
/// Нужен ровно для одного вопроса — «а не внутрь ли себя мы копируем». `canonicalize`
/// целиком не годится: назначения обычно ещё нет на диске.
fn compare_key(path: &Path) -> PathBuf {
    let mut existing = path.to_path_buf();
    let mut tail: Vec<std::ffi::OsString> = Vec::new();
    loop {
        if let Ok(canonical) = fs::canonicalize(&existing) {
            let mut key = canonical;
            for part in tail.iter().rev() {
                key.push(part);
            }
            return normalized_case(&key);
        }
        match existing.file_name() {
            Some(name) => {
                tail.push(name.to_os_string());
                if !existing.pop() {
                    break;
                }
            }
            None => break,
        }
    }
    normalized_case(path)
}

fn normalized_case(path: &Path) -> PathBuf {
    #[cfg(windows)]
    {
        // Windows не различает регистр: `C:\Work` и `c:\work` — один каталог, и копия
        // «внутрь себя» через разный регистр закрутилась бы бесконечно.
        PathBuf::from(path.to_string_lossy().to_lowercase())
    }
    #[cfg(not(windows))]
    {
        path.to_path_buf()
    }
}

fn ensure_distinct(from: &Path, to: &Path) -> Result<()> {
    let from_key = compare_key(from);
    let to_key = compare_key(to);
    if from_key == to_key {
        anyhow::bail!(
            "source and destination are the same path: {}",
            from.display()
        );
    }
    if to_key.starts_with(&from_key) {
        anyhow::bail!(
            "destination {} is inside the source {}: copying a tree into itself never ends",
            to.display(),
            from.display()
        );
    }
    Ok(())
}

/// `mv a.txt C:\dir` — привычка оболочки: назначение-каталог значит «положи внутрь».
/// Мы так и делаем, но обязаны вернуть ИТОГОВЫЙ путь, иначе она не знает, куда легло.
fn resolve_destination(from: &Path, to: &Path) -> Result<(PathBuf, bool)> {
    let into_directory = fs::metadata(to).map(|x| x.is_dir()).unwrap_or(false);
    if into_directory {
        let name = from
            .file_name()
            .with_context(|| format!("source {} has no file name to keep", from.display()))?;
        Ok((to.join(name), true))
    } else {
        Ok((to.to_path_buf(), false))
    }
}

/// Разные тома. Windows отвечает ERROR_NOT_SAME_DEVICE(17), unix — EXDEV(18).
fn is_cross_volume(error: &std::io::Error) -> bool {
    #[cfg(windows)]
    {
        matches!(error.raw_os_error(), Some(17))
    }
    #[cfg(not(windows))]
    {
        matches!(error.raw_os_error(), Some(18))
    }
}

fn move_path(args: MoveArgs) -> Result<Value> {
    move_path_with_rename(args, |source, destination| fs::rename(source, destination))
}

fn move_path_with_rename(
    args: MoveArgs,
    rename: impl FnOnce(&Path, &Path) -> std::io::Result<()>,
) -> Result<Value> {
    let mut budget = Budget::new();
    let mut tally = Tally::default();
    let source = fs::symlink_metadata(&args.from)
        .with_context(|| format!("move source {}", args.from.display()))?;
    let source_kind = kind_of(&source);
    let (destination, resolved_into_directory) = resolve_destination(&args.from, &args.to)?;
    ensure_distinct(&args.from, &destination)?;
    let existing = fs::symlink_metadata(&destination).ok();
    let mut overwritten = false;
    if let Some(existing) = &existing {
        if !args.overwrite {
            anyhow::bail!(
                "destination {} already exists ({}). Repeat with overwrite=true to replace it.",
                destination.display(),
                kind_of(existing)
            );
        }
        if is_directory_like(&existing.file_type()) {
            // Не забор, а отказ угадывать: `overwrite` — про замену файла. Снести дерево
            // под назначением — отдельное намерение, у него есть своё имя (fs.delete
            // recursive=true), и подразумевать его флагом было бы подменой смысла.
            anyhow::bail!(
                "destination {} is an existing directory: overwrite=true replaces a file, \
                 it will not delete a directory tree for you. Delete it first with \
                 fs.delete recursive=true, or move to a path that does not exist.",
                destination.display()
            );
        }
        overwritten = true;
        if source.file_type().is_dir() {
            // rename каталога поверх существующего файла не умеет ни один API: убираем
            // файл заранее. Отдельным шагом — чтобы отказ на нём был назван отказом.
            remove_entry(&destination, false, &mut tally)
                .with_context(|| format!("replace {}", destination.display()))?;
        }
    }
    let bytes_hint = if source.file_type().is_dir() {
        0
    } else {
        source.len()
    };
    let strategy;
    let mut cross_volume = false;
    let mut source_removed = true;
    match rename(&args.from, &destination) {
        Ok(()) => {
            strategy = "rename";
            budget.spend();
            if source.file_type().is_dir() {
                tally.dirs += 1;
            } else {
                tally.files += 1;
                tally.bytes += bytes_hint;
            }
        }
        Err(error) if is_cross_volume(&error) => {
            // ⚠ Тут «мгновенно» превращается в минуты. Молчать об этом нельзя: она
            // планирует по времени, и «переместила» без пояснения означало бы, что
            // копирование 40 ГБ выглядит как сбой связи, а не как честная работа.
            strategy = "copy_and_delete";
            cross_volume = true;
            source_removed = copy_then_remove_source(
                &args.from,
                &destination,
                &source,
                args.overwrite,
                &mut budget,
                &mut tally,
            );
        }
        Err(error) => {
            return Err(anyhow::Error::new(error)).with_context(|| {
                format!("move {} -> {}", args.from.display(), destination.display())
            });
        }
    }
    let complete = budget.stopped.is_none()
        && tally.failed_total == 0
        && tally.skipped_reparse == 0
        && tally.skipped_existing == 0
        && source_removed;
    let mut summary = if cross_volume {
        format!(
            "move {} to {} ACROSS VOLUMES: copied {} file(s) ({} byte(s)); {}",
            args.from.display(),
            destination.display(),
            tally.files,
            tally.bytes,
            if source_removed {
                "source removed after complete copy"
            } else {
                "source removal not confirmed (copy incomplete or removal failed)"
            }
        )
    } else {
        format!(
            "renamed {} to {} in place ({} byte(s))",
            args.from.display(),
            destination.display(),
            tally.bytes
        )
    };
    if overwritten {
        summary.push_str("; an existing file at the destination was replaced");
    }
    if resolved_into_directory {
        summary.push_str("; the destination was an existing directory, so the name was kept");
    }
    if tally.skipped_reparse > 0 {
        summary.push_str(&format!(
            "; {} reparse point(s) (junction/symlink) were not copied",
            tally.skipped_reparse
        ));
    }
    if tally.skipped_existing > 0 {
        summary.push_str(&format!(
            "; {} existing destination file(s) were not replaced",
            tally.skipped_existing
        ));
    }
    if !complete {
        summary = format!(
            "PARTIAL: {summary}; {} failure(s){}{}",
            tally.failed_total,
            if source_removed {
                ""
            } else {
                "; source removal NOT CONFIRMED"
            },
            budget
                .stopped
                .as_deref()
                .map(|reason| format!("; stopped early: {reason}"))
                .unwrap_or_default()
        );
    }
    let failures = tally.failure_report();
    Ok(json!({
        "ok": complete,
        "from": args.from,
        "to": destination,
        "requested_to": args.to,
        "destination_resolved_into_directory": resolved_into_directory,
        "kind": source_kind,
        "strategy": strategy,
        "cross_volume": cross_volume,
        "overwritten": overwritten,
        "moved_files": tally.files,
        "moved_dirs": tally.dirs,
        "moved_links": tally.links,
        "bytes": tally.bytes,
        "skipped_reparse": tally.skipped_reparse,
        "skipped_existing": tally.skipped_existing,
        "cleared_readonly": tally.cleared_readonly,
        "source_removed": source_removed,
        "complete": complete,
        "stopped_because": budget.stopped,
        "failed_count": failures["failed_count"],
        "failed": failures["failed"],
        "failed_truncated": failures["failed_truncated"],
        "elapsed_ms": budget.elapsed_ms(),
        "summary": summary,
        "ignored_args": ignored_args(&args.extra),
        "limits": tree_limits(),
    }))
}

/// Tests exercise the actual cross-volume decision with a same-volume fixture.
fn copy_then_remove_source(
    from: &Path,
    to: &Path,
    source: &fs::Metadata,
    overwrite: bool,
    budget: &mut Budget,
    tally: &mut Tally,
) -> bool {
    copy_then_remove_source_with_hook(from, to, source, overwrite, budget, tally, || {})
}

fn copy_then_remove_source_with_hook(
    from: &Path,
    to: &Path,
    source: &fs::Metadata,
    overwrite: bool,
    budget: &mut Budget,
    tally: &mut Tally,
    after_copy: impl FnOnce(),
) -> bool {
    copy_tree(from, to, overwrite, budget, tally);
    after_copy();
    // A deliberate skip during fs.copy is NOT permission to delete the original
    // during fs.move. This includes overwrite=false as well as links/junctions.
    if tally.failed_total != 0
        || tally.skipped_reparse != 0
        || tally.skipped_existing != 0
        || budget.stopped.is_some()
    {
        return false;
    }
    let mut removal = Tally::default();
    // A live source is not a snapshot. Never give delete_tree a source whose
    // children can change after traversal: it would delete un-copied files.
    let mut manifest = Vec::new();
    if let Err(error) = copied_source_manifest(from, to, source, budget, &mut manifest) {
        tally.fail(from, error);
        return false;
    }
    delete_checked_source(from, &manifest, budget, &mut removal);
    tally.cleared_readonly += removal.cleared_readonly;
    tally.failed_total += removal.failed_total;
    for failure in removal.failed {
        if tally.failed.len() < MAX_FAILURES_REPORTED {
            tally.failed.push(failure);
        }
    }
    budget.stopped.is_none()
        && tally.failed_total == 0
        && fs::symlink_metadata(from).is_err_and(|e| e.kind() == std::io::ErrorKind::NotFound)
}

/// Bottom-up manifest of exact source/destination pairs. A late source child
/// missing from the destination aborts before removing any original bytes.
fn copied_source_manifest(
    from: &Path,
    to: &Path,
    original: &fs::Metadata,
    budget: &mut Budget,
    manifest: &mut Vec<(PathBuf, PathBuf, fs::Metadata)>,
) -> Result<()> {
    let mut stack = vec![(from.to_path_buf(), to.to_path_buf(), false)];
    while let Some((source, target, visited)) = stack.pop() {
        if !budget.spend() {
            anyhow::bail!("cross-volume source comparison exceeded traversal budget");
        }
        if visited {
            let meta = fs::symlink_metadata(&source)?;
            if !meta.is_dir() || is_reparse(&meta) {
                anyhow::bail!("source directory changed during move: {}", source.display());
            }
            manifest.push((source, target, meta));
            continue;
        }
        let meta = fs::symlink_metadata(&source)?;
        let dest = fs::symlink_metadata(&target)?;
        if is_reparse(&meta) || is_reparse(&dest) {
            anyhow::bail!(
                "reparse point appeared during cross-volume move: {}",
                source.display()
            );
        }
        if source == from
            && (!same_source_identity(original, &meta)
                || meta.is_dir() != original.is_dir()
                || meta.len() != original.len())
        {
            anyhow::bail!(
                "source changed during cross-volume move: {}",
                source.display()
            );
        }
        if meta.is_dir() && dest.is_dir() {
            stack.push((source.clone(), target.clone(), true));
            for entry in fs::read_dir(&source)? {
                let entry = entry?;
                stack.push((entry.path(), target.join(entry.file_name()), false));
            }
        } else if meta.is_file() && dest.is_file() {
            if !files_equal(&source, &target, budget)? {
                anyhow::bail!(
                    "source differs from copied destination: {}",
                    source.display()
                );
            }
            manifest.push((source, target, meta));
        } else {
            anyhow::bail!(
                "source/destination kind changed during move: {}",
                source.display()
            );
        }
    }
    Ok(())
}

/// Detect a changed source identity before unlink. Unix supplies inode+device;
/// stable Windows MetadataExt only supplies creation time, not file index, so
/// this is a weaker signal there. An unavailable timestamp declines removal.
fn same_source_identity(expected: &fs::Metadata, current: &fs::Metadata) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        expected.dev() == current.dev() && expected.ino() == current.ino()
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        expected.creation_time() != 0 && expected.creation_time() == current.creation_time()
    }
}

fn files_equal(source: &Path, target: &Path, budget: &mut Budget) -> Result<bool> {
    let before = fs::symlink_metadata(source)?;
    let dest = fs::symlink_metadata(target)?;
    if is_reparse(&before)
        || is_reparse(&dest)
        || !before.is_file()
        || !dest.is_file()
        || before.len() != dest.len()
    {
        return Ok(false);
    }
    let mut left = File::open(source)?;
    let mut right = File::open(target)?;
    let mut a = [0u8; 64 * 1024];
    let mut b = [0u8; 64 * 1024];
    loop {
        if !budget.time_available() {
            anyhow::bail!("cross-volume source comparison exceeded time budget");
        }
        let n = left.read(&mut a)?;
        right.read_exact(&mut b[..n])?;
        if a[..n] != b[..n] {
            return Ok(false);
        }
        if n == 0 {
            break;
        }
    }
    let after = fs::symlink_metadata(source)?;
    let dest_after = fs::symlink_metadata(target)?;
    Ok(before.len() == after.len()
        && before.modified().ok() == after.modified().ok()
        && !is_reparse(&after)
        && same_source_identity(&before, &after)
        && dest.len() == dest_after.len()
        && dest.modified().ok() == dest_after.modified().ok()
        && !is_reparse(&dest_after)
        && same_source_identity(&dest, &dest_after))
}

fn delete_checked_source(
    root: &Path,
    manifest: &[(PathBuf, PathBuf, fs::Metadata)],
    budget: &mut Budget,
    tally: &mut Tally,
) {
    let directories: std::collections::HashMap<&Path, &fs::Metadata> = manifest
        .iter()
        .filter(|(_, _, meta)| meta.is_dir())
        .map(|(path, _, meta)| (path.as_path(), meta))
        .collect();
    for (source, target, expected) in manifest {
        if !budget.spend() {
            return;
        }
        // Never traverse a parent replaced by a junction after manifesting.
        let mut ancestor = source.parent();
        let mut unsafe_parent = false;
        while let Some(path) = ancestor {
            if !path.starts_with(root) {
                break;
            }
            match (directories.get(path), fs::symlink_metadata(path)) {
                (Some(expected), Ok(meta))
                    if meta.is_dir()
                        && !is_reparse(&meta)
                        && same_source_identity(expected, &meta) => {}
                _ => unsafe_parent = true,
            }
            ancestor = path.parent();
        }
        if unsafe_parent {
            tally.fail(
                source,
                "source parent changed to reparse/non-directory before removal",
            );
            continue;
        }
        if expected.is_dir() {
            // remove_dir refuses a new, unlisted child; never remove_dir_all.
            match fs::symlink_metadata(source) {
                Ok(meta)
                    if meta.is_dir()
                        && !is_reparse(&meta)
                        && same_source_identity(expected, &meta) =>
                {
                    match fs::remove_dir(source) {
                        Ok(()) => tally.dirs += 1,
                        Err(error) => tally.fail(source, error),
                    }
                }
                _ => tally.fail(source, "source directory changed before removal"),
            }
        } else if files_equal(source, target, budget).unwrap_or(false) {
            match fs::symlink_metadata(source) {
                Ok(meta)
                    if meta.is_file()
                        && !is_reparse(&meta)
                        && same_source_identity(expected, &meta) =>
                {
                    match remove_entry(source, false, tally) {
                        Ok(()) => {
                            tally.files += 1;
                            tally.bytes += meta.len();
                        }
                        Err(error) => tally.fail(source, error),
                    }
                }
                _ => tally.fail(source, "source kind changed before removal"),
            }
        } else {
            tally.fail(
                source,
                "source changed after copy; retained instead of removing",
            );
        }
    }
}

fn copy(args: CopyArgs) -> Result<Value> {
    let mut budget = Budget::new();
    let mut tally = Tally::default();
    let source = fs::symlink_metadata(&args.from)
        .with_context(|| format!("copy source {}", args.from.display()))?;
    let source_kind = kind_of(&source);
    if source.file_type().is_dir() && !args.recursive {
        anyhow::bail!(
            "{} is a directory: this call copies one file. Repeat with recursive=true to \
             copy the whole tree.",
            args.from.display()
        );
    }
    let (destination, resolved_into_directory) = resolve_destination(&args.from, &args.to)?;
    ensure_distinct(&args.from, &destination)?;
    let existed = fs::symlink_metadata(&destination).is_ok();
    if existed && !args.overwrite && !source.file_type().is_dir() {
        anyhow::bail!(
            "destination {} already exists. Repeat with overwrite=true to replace it.",
            destination.display()
        );
    }
    copy_tree(
        &args.from,
        &destination,
        args.overwrite,
        &mut budget,
        &mut tally,
    );
    let complete =
        budget.stopped.is_none() && tally.failed_total == 0 && tally.skipped_reparse == 0;
    let mut summary = format!(
        "copied {} file(s), {} new directory(ies), {} byte(s) from {} to {}",
        tally.files,
        tally.dirs,
        tally.bytes,
        args.from.display(),
        destination.display()
    );
    if tally.overwritten > 0 {
        summary.push_str(&format!(
            "; {} existing file(s) were overwritten",
            tally.overwritten
        ));
    }
    if tally.skipped_existing > 0 {
        summary.push_str(&format!(
            "; {} file(s) already existed and were LEFT AS THEY WERE because overwrite=false",
            tally.skipped_existing
        ));
    }
    if tally.skipped_reparse > 0 {
        summary.push_str(&format!(
            "; {} reparse point(s) (junction/symlink) were not followed and were NOT copied",
            tally.skipped_reparse
        ));
    }
    if resolved_into_directory {
        summary.push_str("; the destination was an existing directory, so the name was kept");
    }
    if !complete {
        summary = format!(
            "PARTIAL: {summary}; {} failure(s){}",
            tally.failed_total,
            budget
                .stopped
                .as_deref()
                .map(|reason| format!("; stopped early: {reason}"))
                .unwrap_or_default()
        );
    }
    let failures = tally.failure_report();
    Ok(json!({
        "ok": complete,
        "from": args.from,
        "to": destination,
        "requested_to": args.to,
        "destination_resolved_into_directory": resolved_into_directory,
        "kind": source_kind,
        "recursive": args.recursive,
        "overwrite": args.overwrite,
        "copied_files": tally.files,
        "created_dirs": tally.dirs,
        "bytes": tally.bytes,
        "overwritten_files": tally.overwritten,
        "skipped_existing": tally.skipped_existing,
        "skipped_reparse": tally.skipped_reparse,
        "cleared_readonly": tally.cleared_readonly,
        "complete": complete,
        "stopped_because": budget.stopped,
        "failed_count": failures["failed_count"],
        "failed": failures["failed"],
        "failed_truncated": failures["failed_truncated"],
        "elapsed_ms": budget.elapsed_ms(),
        "summary": summary,
        "ignored_args": ignored_args(&args.extra),
        "limits": tree_limits(),
    }))
}

fn copy_tree(from: &Path, to: &Path, overwrite: bool, budget: &mut Budget, tally: &mut Tally) {
    let mut stack: Vec<(PathBuf, PathBuf)> = vec![(from.to_path_buf(), to.to_path_buf())];
    while let Some((source, target)) = stack.pop() {
        if !budget.spend() {
            return;
        }
        if let Err(error) = check_copy_destination(&target) {
            tally.fail(&target, error);
            continue;
        }
        let metadata = match fs::symlink_metadata(&source) {
            Ok(metadata) => metadata,
            Err(error) => {
                tally.fail(&source, error);
                continue;
            }
        };
        let file_type = metadata.file_type();
        if is_reparse(&metadata) {
            // ⚠ Связь не разворачиваем. Скопировать ЦЕЛЬ вместо связи — подмена смысла:
            // на месте ярлыка окажется его содержимое, и она об этом не узнает. Считаем
            // и называем в ответе (`skipped_reparse`), а не делаем вид, что скопировали.
            tally.skipped_reparse += 1;
            tally.fail(&source, "source reparse point was not copied");
            continue;
        }
        if file_type.is_dir() {
            let target_existed = match fs::symlink_metadata(&target) {
                Ok(existing) if existing.is_dir() => true,
                Ok(_) => {
                    tally.fail(&target, "destination is not a directory");
                    continue;
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => false,
                Err(error) => {
                    tally.fail(&target, error);
                    continue;
                }
            };
            if let Err(error) = fs::create_dir_all(&target) {
                tally.fail(&target, error);
                continue;
            }
            if !target_existed {
                tally.dirs += 1;
            }
            match fs::read_dir(&source) {
                Ok(entries) => {
                    for entry in entries {
                        match entry {
                            Ok(entry) => stack.push((entry.path(), target.join(entry.file_name()))),
                            Err(error) => tally.fail(&source, error),
                        }
                    }
                }
                Err(error) => tally.fail(&source, error),
            }
        } else {
            let target_existed = fs::symlink_metadata(&target).is_ok();
            if target_existed && !overwrite {
                tally.skipped_existing += 1;
                continue;
            }
            if let Some(parent) = target.parent()
                && !parent.as_os_str().is_empty()
                && let Err(error) = fs::create_dir_all(parent)
            {
                tally.fail(parent, error);
                continue;
            }
            match copy_file_forgiving(&source, &target, tally) {
                Ok(bytes) => {
                    tally.files += 1;
                    tally.bytes += bytes;
                    if target_existed {
                        tally.overwritten += 1;
                    }
                }
                Err(error) => tally.fail(&target, error),
            }
        }
    }
}

fn mkdir(args: MkdirArgs) -> Result<Value> {
    let started = Instant::now();
    if let Ok(metadata) = fs::symlink_metadata(&args.path) {
        if is_directory_like(&metadata.file_type()) {
            return Ok(json!({
                "ok": true,
                "path": args.path,
                "existed": true,
                "parents": args.parents,
                "created": [],
                "created_count": 0,
                "created_truncated": false,
                "complete": true,
                "failed_count": 0,
                "failed": [],
                "elapsed_ms": started.elapsed().as_millis(),
                "summary": format!("{} already exists as a directory; nothing was created", args.path.display()),
                "ignored_args": ignored_args(&args.extra),
                "limits": json!({"max_created_reported": MAX_CREATED_DIRS_REPORTED}),
            }));
        }
        anyhow::bail!(
            "{} already exists and it is a {}, not a directory",
            args.path.display(),
            kind_of(&metadata)
        );
    }
    // Считаем недостающую цепочку САМИ, а не зовём create_dir_all вслепую: только так
    // ответ может сказать, какие именно каталоги родились на диске этим вызовом.
    let mut missing: Vec<PathBuf> = Vec::new();
    let mut cursor = args.path.clone();
    loop {
        if cursor.exists() {
            break;
        }
        missing.push(cursor.clone());
        match cursor.parent() {
            Some(parent) if !parent.as_os_str().is_empty() => cursor = parent.to_path_buf(),
            _ => break,
        }
    }
    missing.reverse();
    if !args.parents && missing.len() > 1 {
        anyhow::bail!(
            "parent directory {} does not exist: this call creates one level. Repeat with \
             parents=true to create the whole chain ({} missing level(s)).",
            missing[missing.len() - 2].display(),
            missing.len() - 1
        );
    }
    let mut created: Vec<PathBuf> = Vec::new();
    let mut tally = Tally::default();
    for path in &missing {
        match fs::create_dir(path) {
            Ok(()) => created.push(path.clone()),
            Err(error) => {
                tally.fail(path, error);
                break;
            }
        }
    }
    let complete = tally.failed_total == 0;
    let reported: Vec<&PathBuf> = created.iter().take(MAX_CREATED_DIRS_REPORTED).collect();
    let summary = if complete {
        format!(
            "created {} directory(ies), deepest is {}",
            created.len(),
            args.path.display()
        )
    } else {
        format!(
            "PARTIAL: created {} directory(ies) and then failed before reaching {}",
            created.len(),
            args.path.display()
        )
    };
    Ok(json!({
        "ok": complete,
        "path": args.path,
        "existed": false,
        "parents": args.parents,
        "created": reported,
        "created_count": created.len(),
        "created_truncated": created.len() > reported.len(),
        "complete": complete,
        "failed_count": tally.failed_total,
        "failed": tally.failed,
        "elapsed_ms": started.elapsed().as_millis(),
        "summary": summary,
        "ignored_args": ignored_args(&args.extra),
        "limits": json!({"max_created_reported": MAX_CREATED_DIRS_REPORTED}),
    }))
}

pub fn sha256_file(path: &Path) -> Result<(String, u64)> {
    let mut file = File::open(path).with_context(|| format!("hash {}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut size = 0u64;
    let mut buffer = vec![0u8; 1024 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
        size += read as u64;
    }
    Ok((hex::encode(hasher.finalize()), size))
}

pub fn existing_hash(path: &Path) -> Result<Option<String>> {
    if path.exists() {
        Ok(Some(sha256_file(path)?.0))
    } else {
        Ok(None)
    }
}

fn check_expected(actual: Option<&str>, expected: Option<&str>) -> Result<()> {
    if let Some(expected) = expected
        && actual != Some(expected)
    {
        anyhow::bail!(
            "hash conflict: expected {}, actual {}",
            expected,
            actual.unwrap_or("<missing>")
        );
    }
    Ok(())
}

fn create_backup(path: &Path) -> Result<PathBuf> {
    let parent = path.parent().context("file has no parent")?;
    let name = path
        .file_name()
        .map(|x| x.to_string_lossy())
        .unwrap_or_default();
    let backup = parent.join(format!(".{name}.praxis-backup-{}", Uuid::new_v4()));
    fs::copy(path, &backup)?;
    Ok(backup)
}

pub fn atomic_write(path: &Path, content: &[u8]) -> Result<()> {
    let parent = path.parent().context("destination has no parent")?;
    fs::create_dir_all(parent)?;
    let name = path
        .file_name()
        .map(|x| x.to_string_lossy())
        .unwrap_or_default();
    let stage = parent.join(format!(".{name}.praxis-part-{}", Uuid::new_v4()));
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&stage)?;
    file.write_all(content)?;
    file.sync_all()?;
    drop(file);
    replace_path(&stage, path).with_context(|| {
        format!(
            "commit atomic write {} -> {}",
            stage.display(),
            path.display()
        )
    })?;
    Ok(())
}

#[cfg(windows)]
pub fn replace_path(stage: &Path, destination: &Path) -> Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows::Win32::Storage::FileSystem::{
        MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH, MoveFileExW,
    };
    use windows::core::PCWSTR;

    let wide = |value: &Path| {
        value
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect::<Vec<u16>>()
    };
    let source = wide(stage);
    let target = wide(destination);
    unsafe {
        MoveFileExW(
            PCWSTR(source.as_ptr()),
            PCWSTR(target.as_ptr()),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )?;
    }
    Ok(())
}

#[cfg(not(windows))]
pub fn replace_path(stage: &Path, destination: &Path) -> Result<()> {
    fs::rename(stage, destination)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn expected_hash_prevents_stale_write() {
        let dir = std::env::temp_dir().join(format!("praxis-fs-{}", Uuid::new_v4()));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("value.txt");
        fs::write(&path, "old").unwrap();
        let error = dispatch(
            "fs.write_atomic",
            json!({"path": path, "content": "new", "expected_sha256": "deadbeef"}),
        )
        .unwrap_err();
        assert!(error.to_string().contains("hash conflict"));
        assert_eq!(fs::read_to_string(dir.join("value.txt")).unwrap(), "old");
        let _ = fs::remove_dir_all(dir);
    }

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("praxis-{tag}-{}", Uuid::new_v4()));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn text(value: &Value, key: &str) -> String {
        value[key].as_str().unwrap_or_default().to_string()
    }

    /// Живой случай 13–14 июля: две операции били по `C:\Users\Egor` и `C:\Users\Егор`,
    /// а на диске только `yegor`. Тихое «удалила» на такой промах — ложь, из которой
    /// не выбраться: она считает файл снесённым и идёт дальше.
    #[test]
    fn deleting_a_missing_path_says_it_was_missing_and_not_that_it_removed_something() {
        let dir = scratch("delete-missing");
        let value = dispatch(
            "fs.delete",
            json!({"path": dir.join("Egor").join("note.txt")}),
        )
        .unwrap();
        assert_eq!(value["existed"], false);
        assert_eq!(value["removed_files"], 0);
        assert_eq!(value["removed_dirs"], 0);
        assert!(
            text(&value, "summary").contains("does not exist"),
            "{value}"
        );
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn deleting_a_full_directory_without_recursive_names_the_word_that_unlocks_it() {
        let dir = scratch("delete-tree-guard");
        fs::write(dir.join("a.txt"), "a").unwrap();
        let error = dispatch("fs.delete", json!({"path": &dir}))
            .unwrap_err()
            .to_string();
        assert!(error.contains("recursive=true"), "{error}");
        assert!(dir.exists(), "отказ не должен ничего сносить");
        // Граница: пустой каталог уходит и без слова recursive — различение, не забор.
        let empty = dir.join("empty");
        fs::create_dir(&empty).unwrap();
        let value = dispatch("fs.delete", json!({"path": &empty})).unwrap();
        assert_eq!(value["removed_dirs"], 1);
        assert!(!empty.exists());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn a_recursive_delete_counts_exactly_what_it_removed() {
        let dir = scratch("delete-tree");
        let root = dir.join("root");
        fs::create_dir_all(root.join("sub")).unwrap();
        fs::write(root.join("a.txt"), "abc").unwrap();
        fs::write(root.join("sub").join("b.txt"), "de").unwrap();
        let value = dispatch("fs.delete", json!({"path": &root, "recursive": true})).unwrap();
        assert_eq!(value["complete"], true, "{value}");
        assert_eq!(value["removed_files"], 2, "{value}");
        assert_eq!(value["removed_dirs"], 2, "{value}");
        assert_eq!(value["removed_bytes"], 5, "{value}");
        assert_eq!(value["failed_count"], 0);
        assert!(!root.exists());
        let _ = fs::remove_dir_all(dir);
    }

    /// Любой клон гита: `.git/objects` лежит read-only. Удаление такого файла Windows
    /// делает молча сама, а вот копирование ПОВЕРХ него отвечает «отказано в доступе» —
    /// и вот там мы снимаем атрибут и обязаны об этом сказать. Спрашивать «снять?» —
    /// забор; снять и промолчать — вранье.
    #[cfg(windows)]
    #[test]
    fn a_read_only_destination_is_overwritten_and_the_answer_admits_the_attribute_was_cleared() {
        let dir = scratch("copy-readonly");
        let from = dir.join("s.txt");
        let to = dir.join("object");
        fs::write(&from, "new").unwrap();
        fs::write(&to, "old").unwrap();
        let mut permissions = fs::metadata(&to).unwrap().permissions();
        permissions.set_readonly(true);
        fs::set_permissions(&to, permissions).unwrap();

        let value = dispatch(
            "fs.copy",
            json!({"from": &from, "to": &to, "overwrite": true}),
        )
        .unwrap();
        assert_eq!(value["copied_files"], 1, "{value}");
        assert_eq!(value["overwritten_files"], 1, "{value}");
        assert_eq!(value["cleared_readonly"], 1, "{value}");
        assert_eq!(fs::read_to_string(&to).unwrap(), "new");

        // Удаление read-only файла на Windows проходит без нашей помощи — тогда и
        // счётчик обязан остаться нулём, а не приписывать нам чужую работу.
        let value = dispatch("fs.delete", json!({"path": &to})).unwrap();
        assert_eq!(value["removed_files"], 1, "{value}");
        assert_eq!(value["cleared_readonly"], 0, "{value}");
        assert!(!to.exists());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn unknown_arguments_come_back_by_name_instead_of_silently_defaulting() {
        let dir = scratch("delete-typo");
        let path = dir.join("a.txt");
        fs::write(&path, "a").unwrap();
        let value = dispatch("fs.delete", json!({"path": &path, "recursiv": true})).unwrap();
        assert_eq!(value["ignored_args"], json!(["recursiv"]), "{value}");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn every_tree_verb_carries_its_limits_in_the_answer() {
        let dir = scratch("limits");
        fs::write(dir.join("a.txt"), "a").unwrap();
        let answers = vec![
            dispatch("fs.mkdir", json!({"path": dir.join("made")})).unwrap(),
            dispatch(
                "fs.copy",
                json!({"from": dir.join("a.txt"), "to": dir.join("b.txt")}),
            )
            .unwrap(),
            dispatch(
                "fs.move",
                json!({"from": dir.join("b.txt"), "to": dir.join("c.txt")}),
            )
            .unwrap(),
            dispatch("fs.delete", json!({"path": dir.join("c.txt")})).unwrap(),
        ];
        for answer in answers {
            let limits = answer["limits"].as_object().expect("нет блока limits");
            assert!(!limits.is_empty(), "пустой блок пределов: {answer}");
            assert!(
                answer["summary"].as_str().is_some(),
                "нет человеческого итога: {answer}"
            );
        }
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn a_move_refuses_to_replace_silently_and_replaces_when_told() {
        let dir = scratch("move-overwrite");
        let from = dir.join("a.txt");
        let to = dir.join("b.txt");
        fs::write(&from, "new").unwrap();
        fs::write(&to, "old").unwrap();
        let error = dispatch("fs.move", json!({"from": &from, "to": &to}))
            .unwrap_err()
            .to_string();
        assert!(error.contains("overwrite=true"), "{error}");
        assert_eq!(fs::read_to_string(&to).unwrap(), "old");
        let value = dispatch(
            "fs.move",
            json!({"from": &from, "to": &to, "overwrite": true}),
        )
        .unwrap();
        assert_eq!(value["overwritten"], true, "{value}");
        assert_eq!(value["strategy"], "rename", "{value}");
        assert_eq!(value["cross_volume"], false, "{value}");
        assert_eq!(value["source_removed"], true, "{value}");
        assert_eq!(fs::read_to_string(&to).unwrap(), "new");
        assert!(!from.exists());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn a_move_into_an_existing_directory_reports_where_it_actually_landed() {
        let dir = scratch("move-into-dir");
        let from = dir.join("a.txt");
        let box_dir = dir.join("box");
        fs::write(&from, "a").unwrap();
        fs::create_dir(&box_dir).unwrap();
        let value = dispatch("fs.move", json!({"from": &from, "to": &box_dir})).unwrap();
        assert_eq!(
            value["destination_resolved_into_directory"], true,
            "{value}"
        );
        assert_eq!(value["to"], json!(box_dir.join("a.txt")), "{value}");
        assert!(box_dir.join("a.txt").exists());
        let _ = fs::remove_dir_all(dir);
    }

    /// Перенос через границу тома — это копирование со сносом источника, а не мгновенное
    /// переименование. Второй том в тесте не поднять, поэтому проверяем оба звена, из
    /// которых собран этот путь: распознавание кода ошибки и саму подмену.
    #[test]
    fn the_cross_volume_path_is_recognised_by_its_error_code_and_moves_the_whole_tree() {
        let same_device = if cfg!(windows) { 17 } else { 18 };
        assert!(is_cross_volume(&std::io::Error::from_raw_os_error(
            same_device
        )));
        assert!(!is_cross_volume(&std::io::Error::from_raw_os_error(5)));

        let dir = scratch("move-xvol");
        let from = dir.join("src");
        let to = dir.join("dst");
        fs::create_dir_all(from.join("sub")).unwrap();
        fs::write(from.join("a.txt"), "abc").unwrap();
        fs::write(from.join("sub").join("b.txt"), "de").unwrap();
        let mut budget = Budget::new();
        let mut tally = Tally::default();
        let metadata = fs::symlink_metadata(&from).unwrap();
        let removed =
            copy_then_remove_source(&from, &to, &metadata, false, &mut budget, &mut tally);
        assert!(removed, "{:?}", tally.failure_report());
        assert_eq!(tally.files, 2);
        assert_eq!(tally.bytes, 5);
        assert_eq!(tally.failed_total, 0);
        assert!(!from.exists(), "источник обязан исчезнуть после копии");
        assert_eq!(
            fs::read_to_string(to.join("sub").join("b.txt")).unwrap(),
            "de"
        );
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn complete_cross_volume_copy_reports_source_removed_only_after_copy() {
        let dir = scratch("move-xvol-complete");
        let from = dir.join("source");
        let to = dir.join("target");
        fs::create_dir(&from).unwrap();
        fs::write(from.join("important"), b"original").unwrap();
        let result = move_path_with_rename(
            MoveArgs {
                from: from.clone(),
                to: to.clone(),
                overwrite: false,
                extra: Map::new(),
            },
            |_, _| {
                Err(std::io::Error::from_raw_os_error(if cfg!(windows) {
                    17
                } else {
                    18
                }))
            },
        )
        .unwrap();
        assert_eq!(result["complete"], true, "{result}");
        assert_eq!(result["source_removed"], true, "{result}");
        assert!(!from.exists());
        assert_eq!(fs::read(to.join("important")).unwrap(), b"original");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn cross_volume_move_keeps_new_source_child_created_after_copy() {
        let dir = scratch("move-xvol-late-child");
        let from = dir.join("source");
        let to = dir.join("target");
        fs::create_dir(&from).unwrap();
        fs::write(from.join("original"), b"before").unwrap();
        let meta = fs::symlink_metadata(&from).unwrap();
        let mut budget = Budget::new();
        let mut tally = Tally::default();
        let removed = copy_then_remove_source_with_hook(
            &from,
            &to,
            &meta,
            false,
            &mut budget,
            &mut tally,
            || fs::write(from.join("late"), b"must survive").unwrap(),
        );
        assert!(!removed);
        assert!(tally.failed_total > 0);
        assert_eq!(fs::read(from.join("late")).unwrap(), b"must survive");
        assert_eq!(fs::read(from.join("original")).unwrap(), b"before");
        assert_eq!(fs::read(to.join("original")).unwrap(), b"before");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn cross_volume_move_keeps_source_file_rewritten_after_copy() {
        let dir = scratch("move-xvol-late-edit");
        let from = dir.join("source");
        let to = dir.join("target");
        fs::write(&from, b"before").unwrap();
        let meta = fs::symlink_metadata(&from).unwrap();
        let mut budget = Budget::new();
        let mut tally = Tally::default();
        let removed = copy_then_remove_source_with_hook(
            &from,
            &to,
            &meta,
            false,
            &mut budget,
            &mut tally,
            || fs::write(&from, b"changed").unwrap(),
        );
        assert!(!removed);
        assert!(tally.failed_total > 0);
        assert_eq!(fs::read(&from).unwrap(), b"changed");
        assert_eq!(fs::read(&to).unwrap(), b"before");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn cross_volume_move_retains_source_when_destination_keeps_an_existing_file() {
        let dir = scratch("move-xvol-existing");
        let from = dir.join("source");
        let to = dir.join("target");
        fs::create_dir(&from).unwrap();
        fs::write(from.join("important"), b"original").unwrap();
        let result = move_path_with_rename(
            MoveArgs {
                from: from.clone(),
                to: to.clone(),
                overwrite: false,
                extra: Map::new(),
            },
            |_, target| {
                // Destination changes after initial admission but before the EXDEV fallback.
                fs::create_dir(target)?;
                fs::write(target.join("important"), b"older")?;
                Err(std::io::Error::from_raw_os_error(if cfg!(windows) {
                    17
                } else {
                    18
                }))
            },
        )
        .unwrap();
        assert_eq!(result["complete"], false, "{result}");
        assert_eq!(result["source_removed"], false, "{result}");
        assert_eq!(result["skipped_existing"], 1, "{result}");
        assert!(text(&result, "summary").starts_with("PARTIAL:"), "{result}");
        assert_eq!(fs::read(from.join("important")).unwrap(), b"original");
        assert_eq!(fs::read(to.join("important")).unwrap(), b"older");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn cross_volume_move_keeps_late_link_and_its_external_target() {
        let dir = scratch("move-xvol-late-link");
        let from = dir.join("source");
        let to = dir.join("target");
        let outside = dir.join("outside");
        fs::create_dir(&from).unwrap();
        fs::create_dir(&outside).unwrap();
        fs::write(outside.join("sentinel"), b"untouched").unwrap();
        let meta = fs::symlink_metadata(&from).unwrap();
        let mut budget = Budget::new();
        let mut tally = Tally::default();
        let removed = copy_then_remove_source_with_hook(
            &from,
            &to,
            &meta,
            false,
            &mut budget,
            &mut tally,
            || {
                #[cfg(unix)]
                std::os::unix::fs::symlink(&outside, from.join("late")).unwrap();
                #[cfg(windows)]
                junction(&from.join("late"), &outside);
            },
        );
        assert!(!removed);
        assert!(tally.failed_total > 0);
        assert_eq!(fs::read(outside.join("sentinel")).unwrap(), b"untouched");
        assert!(fs::symlink_metadata(from.join("late")).is_ok());
        #[cfg(windows)]
        fs::remove_dir(from.join("late")).unwrap();
        let _ = fs::remove_dir_all(dir);
    }

    #[cfg(windows)]
    fn junction(link: &Path, target: &Path) {
        // mklink /J needs no developer-mode symlink privilege. Both paths are
        // under this test's unique disposable temporary fixture.
        let output = std::process::Command::new("cmd.exe")
            .args(["/C", "mklink", "/J"])
            .arg(link)
            .arg(target)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "junction creation failed: {:?}",
            output.status
        );
    }

    #[cfg(windows)]
    #[test]
    fn cross_volume_copy_with_a_source_junction_never_deletes_the_original() {
        let dir = scratch("move-xvol-junction");
        let outside = dir.join("outside");
        let from = dir.join("source");
        let to = dir.join("target");
        fs::create_dir_all(&outside).unwrap();
        fs::create_dir_all(&from).unwrap();
        fs::write(outside.join("sentinel"), b"untouched").unwrap();
        fs::write(from.join("ordinary"), b"retained").unwrap();
        junction(&from.join("link"), &outside);
        let result = move_path_with_rename(
            MoveArgs {
                from: from.clone(),
                to: to.clone(),
                overwrite: false,
                extra: Map::new(),
            },
            |_, _| Err(std::io::Error::from_raw_os_error(17)),
        )
        .unwrap();
        assert_eq!(result["complete"], false, "{result}");
        assert_eq!(result["source_removed"], false, "{result}");
        assert_eq!(result["skipped_reparse"], 1, "{result}");
        assert_eq!(result["failed_count"], 1, "{result}");
        assert!(text(&result, "summary").starts_with("PARTIAL:"), "{result}");
        assert_eq!(fs::read(from.join("ordinary")).unwrap(), b"retained");
        assert!(fs::symlink_metadata(from.join("link")).is_ok());
        assert_eq!(fs::read(outside.join("sentinel")).unwrap(), b"untouched");
        fs::remove_dir(from.join("link")).unwrap();
        let _ = fs::remove_dir_all(dir);
    }

    #[cfg(windows)]
    #[test]
    fn cross_volume_move_of_a_junction_itself_retains_the_link() {
        let dir = scratch("move-xvol-root-junction");
        let outside = dir.join("outside");
        fs::create_dir(&outside).unwrap();
        fs::write(outside.join("sentinel"), b"untouched").unwrap();
        let from = dir.join("link");
        junction(&from, &outside);
        let result = move_path_with_rename(
            MoveArgs {
                from: from.clone(),
                to: dir.join("target"),
                overwrite: false,
                extra: Map::new(),
            },
            |_, _| Err(std::io::Error::from_raw_os_error(17)),
        )
        .unwrap();
        assert_eq!(result["complete"], false, "{result}");
        assert_eq!(result["source_removed"], false, "{result}");
        assert_eq!(result["skipped_reparse"], 1, "{result}");
        assert_eq!(result["failed_count"], 1, "{result}");
        assert!(fs::symlink_metadata(&from).is_ok());
        assert_eq!(fs::read(outside.join("sentinel")).unwrap(), b"untouched");
        fs::remove_dir(from).unwrap();
        let _ = fs::remove_dir_all(dir);
    }

    #[cfg(windows)]
    #[test]
    fn recursive_copy_refuses_destination_child_junction_without_touching_external_sentinel() {
        let dir = scratch("copy-destination-junction");
        let outside = dir.join("outside");
        let from = dir.join("source");
        let into = dir.join("destination");
        fs::create_dir_all(from.join("nested")).unwrap();
        fs::create_dir_all(into.join("source")).unwrap();
        fs::create_dir_all(&outside).unwrap();
        fs::write(from.join("nested").join("sentinel"), b"replacement").unwrap();
        fs::write(from.join("nested").join("fresh"), b"new").unwrap();
        fs::write(outside.join("sentinel"), b"untouched").unwrap();
        let link = into.join("source").join("nested");
        junction(&link, &outside);
        let result = dispatch(
            "fs.copy",
            json!({"from": from, "to": into, "recursive": true, "overwrite": true}),
        )
        .unwrap();
        assert_eq!(result["complete"], false, "{result}");
        assert_eq!(result["failed_count"], 1, "{result}");
        assert!(text(&result, "summary").starts_with("PARTIAL:"), "{result}");
        assert!(
            text(&result["failed"][0], "error").contains("reparse"),
            "{result}"
        );
        assert_eq!(fs::read(outside.join("sentinel")).unwrap(), b"untouched");
        assert!(!outside.join("fresh").exists());
        fs::remove_dir(link).unwrap();
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn copying_a_directory_without_recursive_names_the_word_that_unlocks_it() {
        let dir = scratch("copy-guard");
        let from = dir.join("src");
        fs::create_dir(&from).unwrap();
        let error = dispatch("fs.copy", json!({"from": &from, "to": dir.join("dst")}))
            .unwrap_err()
            .to_string();
        assert!(error.contains("recursive=true"), "{error}");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn a_recursive_copy_says_what_it_kept_and_what_it_overwrote() {
        let dir = scratch("copy-tree");
        let from = dir.join("src");
        let into = dir.join("dst");
        fs::create_dir_all(from.join("sub")).unwrap();
        fs::write(from.join("a.txt"), "one").unwrap();
        fs::write(from.join("sub").join("b.txt"), "two").unwrap();
        fs::create_dir_all(into.join("src")).unwrap();
        fs::write(into.join("src").join("a.txt"), "old").unwrap();

        let kept = dispatch(
            "fs.copy",
            json!({"from": &from, "to": &into, "recursive": true}),
        )
        .unwrap();
        assert_eq!(kept["destination_resolved_into_directory"], true, "{kept}");
        assert_eq!(kept["copied_files"], 1, "{kept}");
        assert_eq!(kept["skipped_existing"], 1, "{kept}");
        assert_eq!(kept["overwritten_files"], 0, "{kept}");
        assert!(
            text(&kept, "summary").contains("LEFT AS THEY WERE"),
            "{kept}"
        );
        assert_eq!(
            fs::read_to_string(into.join("src").join("a.txt")).unwrap(),
            "old"
        );

        let replaced = dispatch(
            "fs.copy",
            json!({"from": &from, "to": &into, "recursive": true, "overwrite": true}),
        )
        .unwrap();
        assert_eq!(replaced["copied_files"], 2, "{replaced}");
        assert_eq!(replaced["overwritten_files"], 2, "{replaced}");
        assert_eq!(replaced["skipped_existing"], 0, "{replaced}");
        assert_eq!(
            fs::read_to_string(into.join("src").join("a.txt")).unwrap(),
            "one"
        );
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn copying_a_tree_into_itself_is_named_instead_of_looping_forever() {
        let dir = scratch("copy-self");
        let from = dir.join("src");
        fs::create_dir(&from).unwrap();
        fs::write(from.join("a.txt"), "a").unwrap();
        let error = dispatch(
            "fs.copy",
            json!({"from": &from, "to": from.join("inner"), "recursive": true}),
        )
        .unwrap_err()
        .to_string();
        assert!(error.contains("inside the source"), "{error}");
        // Файл сам на себя: назначение не каталог, значит путь совпадает буквально —
        // и это отдельная фраза, чтобы она не гадала, что за «внутри себя».
        let same = dispatch(
            "fs.copy",
            json!({"from": from.join("a.txt"), "to": from.join("a.txt")}),
        )
        .unwrap_err()
        .to_string();
        assert!(same.contains("same path"), "{same}");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn mkdir_lists_exactly_the_directories_it_created_and_repeats_without_lying() {
        let dir = scratch("mkdir");
        let deep = dir.join("a").join("b").join("c");
        let value = dispatch("fs.mkdir", json!({"path": &deep})).unwrap();
        assert_eq!(value["existed"], false, "{value}");
        assert_eq!(value["created_count"], 3, "{value}");
        assert_eq!(
            value["created"],
            json!([dir.join("a"), dir.join("a").join("b"), &deep]),
            "{value}"
        );
        assert!(deep.is_dir());

        let again = dispatch("fs.mkdir", json!({"path": &deep})).unwrap();
        assert_eq!(again["existed"], true, "{again}");
        assert_eq!(again["created_count"], 0, "{again}");

        let occupied = dir.join("file.txt");
        fs::write(&occupied, "x").unwrap();
        let error = dispatch("fs.mkdir", json!({"path": &occupied}))
            .unwrap_err()
            .to_string();
        assert!(error.contains("not a directory"), "{error}");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn mkdir_without_parents_names_the_missing_level_and_still_makes_one() {
        let dir = scratch("mkdir-parents");
        let deep = dir.join("a").join("b");
        let error = dispatch("fs.mkdir", json!({"path": &deep, "parents": false}))
            .unwrap_err()
            .to_string();
        assert!(error.contains("parents=true"), "{error}");
        assert!(!dir.join("a").exists(), "отказ не должен ничего создавать");
        // Граница: один уровень без parents проходит.
        let value = dispatch("fs.mkdir", json!({"path": dir.join("a"), "parents": false})).unwrap();
        assert_eq!(value["created_count"], 1, "{value}");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn exact_replace_refuses_ambiguous_text() {
        let dir = std::env::temp_dir().join(format!("praxis-replace-{}", Uuid::new_v4()));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("value.txt");
        fs::write(&path, "x x").unwrap();
        let error =
            dispatch("fs.replace", json!({"path": path, "old": "x", "new": "y"})).unwrap_err();
        assert!(error.to_string().contains("found 2"));
        let _ = fs::remove_dir_all(dir);
    }
}
