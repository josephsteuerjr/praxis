//! Previous program snapshots and retirement of the removed automatic trial.
use std::path::{Path, PathBuf};
use serde_json::Value;
pub const KEPT_PREFIX: &str = "program-";
pub fn kept_path(dir: &Path, version: &str) -> PathBuf {
    dir.join("backups").join(format!("{KEPT_PREFIX}{version}"))
}
include!("../../common/code_manifest.rs");

/// Keep the original evidence; never claim that an agent accepted the update.
pub fn retire_legacy(dir: &Path) -> Result<bool, String> {
    let path = dir.join("backups/update-trial.json");
    if !path.is_file() { return Ok(false); }
    let raw = std::fs::read(&path).map_err(|e| e.to_string())?;
    let parsed: Value = serde_json::from_slice(&raw).unwrap_or(Value::Null);
    let mut row = if parsed.is_object() { parsed } else { serde_json::json!({"legacy_unreadable":true}) };
    let already=row.get("phase").and_then(Value::as_str) == Some("retired");
    let suffix=crate::install::random_hex_pub(8).ok_or("не удалось создать имя архива прежнего испытания")?;
    let archive = dir.join("backups").join(format!("update-trial-before-retirement-{suffix}.json"));
    if !already {
        std::fs::write(&archive, &raw).map_err(|e| e.to_string())?;
        row["phase"] = "retired".into();
        row["retirement"] = serde_json::json!({"reason":"Автоматическое испытание приложения отменено. Обновление не требует местного агента.", "evidence":archive.display().to_string(), "agent_acceptance":false});
        crate::install::write_atomic_pub(&path, &serde_json::to_string_pretty(&row).map_err(|e| e.to_string())?)?;
    }
    let receipt_path=dir.join("data/memory/.control/update-plan.receipt.json");
    if let Ok(bytes)=std::fs::read(&receipt_path) {
        if let Ok(mut receipt)=serde_json::from_slice::<Value>(&bytes) {
            let same=receipt.get("id").and_then(Value::as_str).is_some()
                && receipt.get("id")==row.get("id") && receipt.get("desktop").and_then(Value::as_bool)==Some(true);
            if same && ["starting","trial","accepting","rollback"].contains(&receipt.get("state").and_then(Value::as_str).unwrap_or("")) {
                std::fs::write(receipt_path.with_extension(format!("before-retirement-{suffix}.json")),&bytes).map_err(|e|e.to_string())?;
                receipt["retired_trial"]=receipt.get("trial").cloned().unwrap_or(Value::Null);
                if let Some(map)=receipt.as_object_mut() { map.remove("trial"); }
                receipt["state"]="done".into();
                receipt["note"]="Прежнее автоматическое испытание отменено. Обновления больше не ждут приёмки местным агентом.".into();
                receipt["summary"]=receipt["note"].clone();
                receipt["finished_epoch"]=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs().into();
                crate::install::write_atomic_pub(&receipt_path,&serde_json::to_string_pretty(&receipt).map_err(|e|e.to_string())?)?;
            }
        }
    }
    Ok(!already)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn remote_only_legacy_trial_retires_without_touching_previous_program() {
        let root=std::env::temp_dir().join(format!("helene-retire-{}",crate::install::random_hex_pub(8).unwrap()));
        let kept=kept_path(&root,"1.2.6"); std::fs::create_dir_all(&kept).unwrap();
        std::fs::write(kept.join("owner-code.py"),b"owner code").unwrap();
        let original=br#"{"phase":"trial","id":"actual-old-update","from_version":"1.2.6","to_version":"1.3.7"}"#;
        std::fs::write(root.join("backups/update-trial.json"),original).unwrap();
        assert!(retire_legacy(&root).unwrap()); assert!(!retire_legacy(&root).unwrap());
        let row:Value=serde_json::from_slice(&std::fs::read(root.join("backups/update-trial.json")).unwrap()).unwrap();
        assert_eq!(row["phase"],"retired"); assert_eq!(row["id"],"actual-old-update");
        assert_eq!(row["retirement"]["agent_acceptance"],false);
        assert_eq!(std::fs::read(row["retirement"]["evidence"].as_str().unwrap()).unwrap(),original);
        assert_eq!(std::fs::read(kept.join("owner-code.py")).unwrap(),b"owner code");
        std::fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn broken_legacy_marker_does_not_lock_out_future_updates() {
        for original in [b"broken".as_slice(),b"[]".as_slice()] {
            let root=std::env::temp_dir().join(format!("helene-retire-{}",crate::install::random_hex_pub(8).unwrap()));
            std::fs::create_dir_all(root.join("backups")).unwrap();
            std::fs::write(root.join("backups/update-trial.json"),original).unwrap();
            assert!(retire_legacy(&root).unwrap());
            let row:Value=serde_json::from_slice(&std::fs::read(root.join("backups/update-trial.json")).unwrap()).unwrap();
            assert_eq!(row["legacy_unreadable"],true);
            assert_eq!(std::fs::read(row["retirement"]["evidence"].as_str().unwrap()).unwrap(),original);
            std::fs::remove_dir_all(root).unwrap();
        }
    }
    #[test]
    fn only_matching_desktop_receipt_is_retired_including_interrupted_retirement() {
        for (id,desktop,phase,expected) in [("same",true,"trial","done"),("other",true,"trial","trial"),("same",false,"trial","trial"),("same",true,"retired","done")] {
            let root=std::env::temp_dir().join(format!("helene-retire-{}",crate::install::random_hex_pub(8).unwrap()));
            std::fs::create_dir_all(root.join("backups")).unwrap();
            let receipt=root.join("data/memory/.control/update-plan.receipt.json");std::fs::create_dir_all(receipt.parent().unwrap()).unwrap();
            std::fs::write(root.join("backups/update-trial.json"),serde_json::json!({"id":"same","phase":phase}).to_string()).unwrap();
            let old=serde_json::json!({"id":id,"desktop":desktop,"state":"trial","trial":{"key":"old-key"}});
            std::fs::write(&receipt,old.to_string()).unwrap();retire_legacy(&root).unwrap();
            let row:Value=serde_json::from_slice(&std::fs::read(&receipt).unwrap()).unwrap();
            assert_eq!(row["state"],expected);
            if expected=="done" { assert_eq!(row["retired_trial"],old["trial"]);assert!(row.get("trial").is_none()); }
            else { assert_eq!(row,old); }
            std::fs::remove_dir_all(root).unwrap();
        }
    }
}
