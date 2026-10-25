//! Shared Windows admission for the two native desktop process owners.
//!
//! Python uses the same `.desktop-owner.lock` record. A share-exclusive OS
//! handle excludes another process while an owner is live. Persisted words are
//! teardown diagnostics, not a permanent launch ban after a crash. Child Jobs
//! retain kill-on-close custody; live-owner retries still measure them empty.
#![cfg(windows)]

use std::collections::BTreeMap;
use std::fs::{File, OpenOptions};
use std::io::{Seek, SeekFrom, Write};
use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

const NAME: &str = ".desktop-owner.lock";
const ACTIVE: &[u8] = b"active\n";
const CLEAN: &[u8] = b"clean\n";
const OPEN_REPARSE_POINT: u32 = 0x0020_0000;
static STATE: OnceLock<Mutex<OwnerState>> = OnceLock::new();

#[derive(Default)]
struct OwnerState {
    leases: BTreeMap<PathBuf, File>,
    in_flight: usize,
    closing: bool,
}

fn state() -> &'static Mutex<OwnerState> {
    STATE.get_or_init(|| Mutex::new(OwnerState::default()))
}

fn record(file: &mut File, word: &[u8]) -> Result<(), String> {
    file.set_len(0)
        .map_err(|e| format!("desktop owner record truncate: {e}"))?;
    file.sync_all()
        .map_err(|e| format!("desktop owner record flush: {e}"))?;
    file.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    file.write_all(word)
        .map_err(|e| format!("desktop owner record write: {e}"))?;
    file.sync_all()
        .map_err(|e| format!("desktop owner record flush: {e}"))
}

/// Launch ticket lives through CreateProcess and Job admission. A teardown
/// that observes an empty Job cannot publish clean while a suspended child is
/// still between the tree record and its process/Job receipt.
pub struct LaunchTicket;
impl Drop for LaunchTicket {
    fn drop(&mut self) {
        if let Ok(mut owner) = state().lock() {
            owner.in_flight -= 1;
        } // poisoned state remains fail-closed to the next admission
    }
}

/// Obtain once per tree per native owner process before *any* child spawn.
/// Distinct trees can run independently. The kernel lease owns exclusion.
pub fn begin(tree: &Path) -> Result<LaunchTicket, String> {
    std::fs::create_dir_all(tree).map_err(|e| format!("desktop tree unavailable: {e}"))?;
    let tree = tree
        .canonicalize()
        .map_err(|e| format!("desktop tree identity unavailable: {e}"))?;
    let mut owner = state()
        .lock()
        .map_err(|_| "desktop owner map poisoned".to_string())?;
    if owner.closing {
        return Err("desktop owner is closing; no new child launch".into());
    }
    if !owner.leases.contains_key(&tree) {
        let path = tree.join(NAME);
        let mut file = match OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .share_mode(0)
            .custom_flags(OPEN_REPARSE_POINT)
            .open(&path)
        {
            Ok(file) => file,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => OpenOptions::new()
                .read(true)
                .write(true)
                .share_mode(0)
                .custom_flags(OPEN_REPARSE_POINT)
                .open(&path)
                .map_err(|e| format!("desktop tree owned or lock unavailable: {e}"))?,
            Err(e) => return Err(format!("desktop tree lock unavailable: {e}")),
        };
        if file
            .metadata()
            .map_err(|e| e.to_string())?
            .file_attributes()
            & 0x400
            != 0
        {
            return Err("desktop tree lock is a reparse point".into());
        }
        record(&mut file, ACTIVE)?;
        owner.leases.insert(tree, file);
    }
    owner.in_flight += 1;
    Ok(LaunchTicket)
}

/// Caller measured all process Jobs empty and retired its children. The
/// closing bit prevents a late launch even after handles are dropped. A failed
/// write keeps the handle and the active marker; it never licenses a peer.
pub fn mark_clean_after_teardown() -> Result<(), String> {
    let mut owner = state()
        .lock()
        .map_err(|_| "desktop owner map poisoned".to_string())?;
    owner.closing = true;
    if owner.in_flight != 0 {
        return Err("desktop child launch still in flight".into());
    }
    for file in owner.leases.values_mut() {
        record(file, CLEAN)?;
    }
    owner.leases.clear();
    Ok(())
}
