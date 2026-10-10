// Exact installed release + retained edit proof, independent of acceptance.
// Included by setup/src/update_backup.rs; imports stay inside functions.

/// Собрать фактические хэши установленного кода (`tree/` + `app/`, без `__pycache__`).
/// Перенесённые и слитые правки — часть установки. Их точные байты подтверждает
/// переносчик; сверка выполняется перед запуском новой службы.
pub(crate) fn collect_code_hashes(root: &std::path::Path) -> Result<std::collections::BTreeMap<String, String>, String> {
    let mut actual = std::collections::BTreeMap::new();
    for dir in ["tree", "app"] { visit_code(root, &root.join(dir), &mut actual)?; }
    Ok(actual)
}

fn visit_code(root: &std::path::Path, dir: &std::path::Path, actual: &mut std::collections::BTreeMap<String, String>) -> Result<(), String> {
        use sha2::{Digest, Sha256};
        use std::io::Read;
        for entry in std::fs::read_dir(dir).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            let path = entry.path();
            let kind = entry.file_type().map_err(|e| e.to_string())?;
            if kind.is_symlink() { return Err(format!("symlink in installed code: {}", path.display())); }
            if kind.is_dir() {
                if entry.file_name() != "__pycache__" && path != root.join("app/static.prev") { visit_code(root, &path, actual)?; }
            } else if kind.is_file() {
                let rel = path.strip_prefix(root).map_err(|e| e.to_string())?.to_string_lossy().replace('\\', "/");
                // Bytecode is interpreter-generated; source and every shipped asset are proof material.
                if rel.ends_with(".pyc") { continue; }
                let mut file = std::fs::File::open(&path).map_err(|e| e.to_string())?;
                let mut hash = Sha256::new();
                let mut buf = [0u8; 65536];
                loop { let n = file.read(&mut buf).map_err(|e| e.to_string())?; if n == 0 { break; } hash.update(&buf[..n]); }
                actual.insert(rel, format!("{:x}", hash.finalize()));
            } else { return Err(format!("not a regular code file: {}", path.display())); }
        }
        Ok(())
}

pub(crate) fn collect_static_hashes(root: &std::path::Path) -> Result<std::collections::BTreeMap<String, String>, String> {
    let mut actual = std::collections::BTreeMap::new();
    visit_code(root, &root.join("app/static"), &mut actual)?;
    Ok(actual)
}

/// The new release with the exact edits produced by the transfer helper.
/// Both unchanged-file carries and successful three-way merges are part of
/// this installation. Deleted files are absent; conflicts keep release bytes.
pub(crate) fn expected_code_with_carry(
    release: &std::collections::BTreeMap<String, String>, report: &serde_json::Value,
) -> Result<std::collections::BTreeMap<String, String>, String> {
    use std::collections::BTreeSet;
    if release.is_empty() { return Err("в поставке нет отпечатков кода".into()); }
    let mut paths = BTreeSet::new();
    for key in ["carried", "merged"] {
        if let Some(value) = report.get(key) {
            let rows = value.as_array().ok_or("не читается отчёт переноса правок")?;
            for row in rows {
                let path = row.as_str().ok_or("не читается путь перенесённой правки")?;
                let mut parts = path.split('/');
                if !matches!(parts.next(), Some("tree" | "app")) || path.contains('\\')
                    || !path.contains('/') || parts.any(|p| matches!(p, "" | "." | "..")) {
                    return Err("неверный путь в отчёте переноса правок".into());
                }
                paths.insert(path.to_string());
            }
        }
    }
    let proof = report.get("applied_code_sha256").and_then(|v| v.as_object());
    if paths.is_empty() && proof.is_none() { return Ok(release.clone()); }
    let proof = proof.ok_or("переносчик не подтвердил сохранённые правки отпечатками кода")?;
    if paths != proof.keys().cloned().collect() { return Err("отпечатки переноса не совпали со списком сохранённых правок".into()); }
    let mut expected = release.clone();
    for (path, value) in proof {
        if value.is_null() { expected.remove(path); }
        else {
            let hash = value.as_str().filter(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()))
                .ok_or("не читается отпечаток сохранённой правки")?;
            expected.insert(path.clone(), hash.to_ascii_lowercase());
        }
    }
    Ok(expected)
}

pub(crate) fn verify_code_manifest_with_carry(
    root: &std::path::Path, release: &std::collections::BTreeMap<String, String>, report: &serde_json::Value,
    retained_static: Option<&std::collections::BTreeMap<String, String>>,
) -> Result<(), String> {
    let mut expected = expected_code_with_carry(release, report)?;
    if let Some(static_hashes) = retained_static {
        if static_hashes.keys().any(|path| !path.starts_with("app/static/")) {
            return Err("отпечатки сохранённого интерфейса содержат чужие пути".into());
        }
        // The installer promised to retain the owner's UI when the release
        // did not change it. Verify the exact pre-swap snapshot of that UI.
        expected.retain(|path, _| !path.starts_with("app/static/"));
        expected.extend(static_hashes.clone());
    }
    verify_code_manifest(root, &expected)
}

/// Сверка с манифестом выпуска, кроме путей, которые установщик сам перенёс (правки агента).
/// Перенесённое — сознательная запись установщика, а не подмена; всё остальное сверяется строго.
pub(crate) fn verify_code_manifest_except(root: &std::path::Path, expected: &std::collections::BTreeMap<String, String>, skip_rel: &[String]) -> Result<(), String> {
    if expected.is_empty() { return Err("release has no code hashes; installation is unverified".into()); }
    let skip: std::collections::BTreeSet<String> = skip_rel.iter().cloned().collect();
    let actual = collect_code_hashes(root)?;
    let actual: std::collections::BTreeMap<&str, &str> = actual.iter().filter(|(k, _)| !skip.contains(*k)).map(|(k, v)| (k.as_str(), v.as_str())).collect();
    let expected: std::collections::BTreeMap<&str, &str> = expected.iter().filter(|(k, _)| !skip.contains(*k)).map(|(k, v)| (k.as_str(), v.as_str())).collect();
    if actual != expected {
        let changed: Vec<String> = actual.keys().chain(expected.keys()).collect::<std::collections::BTreeSet<_>>().into_iter()
            .filter(|p| actual.get(*p).copied() != expected.get(*p).copied())
            .take(10).map(|p| p.to_string()).collect();
        return Err(format!("installed code differs from release (outside carried edits): {}", changed.join(", ")));
    }
    Ok(())
}

pub(crate) fn verify_code_manifest(root: &std::path::Path, expected: &std::collections::BTreeMap<String, String>) -> Result<(), String> {
    if expected.is_empty() { return Err("release has no code hashes; installation is unverified".into()); }
    let actual = collect_code_hashes(root)?;
    if &actual != expected {
        let changed: Vec<String> = actual.keys().chain(expected.keys()).collect::<std::collections::BTreeSet<_>>().into_iter()
            .filter(|p| actual.get(*p) != expected.get(*p))
            .take(10).map(|p| p.to_string()).collect();
        return Err(format!("код после переноса правок не совпал с проверенной копией: {}", changed.join(", ")));
    }
    Ok(())
}

#[cfg(test)]
mod code_manifest_tests {
    use super::{collect_code_hashes, collect_static_hashes, verify_code_manifest, verify_code_manifest_except, verify_code_manifest_with_carry, expected_code_with_carry};
    #[test]
    fn exact_changed_extra_missing() {
        use sha2::{Digest, Sha256};
        let root = std::env::temp_dir().join(format!("helene-manifest-{}-{}", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        std::fs::create_dir_all(root.join("tree")).unwrap();
        std::fs::create_dir_all(root.join("app")).unwrap();
        std::fs::write(root.join("tree/a.py"), b"print(1)").unwrap();
        let mut expected = std::collections::BTreeMap::new();
        expected.insert("tree/a.py".into(), format!("{:x}", Sha256::digest(b"print(1)")));
        assert!(verify_code_manifest(&root, &expected).is_ok());
        std::fs::write(root.join("tree/a.py"), b"print(2)").unwrap();
        assert!(verify_code_manifest(&root, &expected).is_err());
        std::fs::write(root.join("tree/a.py"), b"print(1)").unwrap();
        std::fs::write(root.join("app/extra.py"), b"pass").unwrap();
        assert!(verify_code_manifest(&root, &expected).is_err());
        std::fs::remove_file(root.join("app/extra.py")).unwrap();
        std::fs::remove_file(root.join("tree/a.py")).unwrap();
        assert!(verify_code_manifest(&root, &expected).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn carried_edits_are_allowed_by_the_except_check() {
        // Правка агента в перенесённом файле не считается расхождением в ремонтной
        // проверке; расхождение в НЕперенесённом файле ловится по-прежнему.
        let root = std::env::temp_dir().join(format!("helene-manifest-x-{}-{}", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        std::fs::create_dir_all(root.join("tree")).unwrap();
        std::fs::create_dir_all(root.join("app")).unwrap();
        std::fs::write(root.join("tree/a.py"), b"print(1)").unwrap();
        std::fs::write(root.join("app/b.py"), b"pass").unwrap();
        let mut expected = std::collections::BTreeMap::new();
        expected.insert("tree/a.py".into(), "carried-edit-hash".into());
        expected.insert("app/b.py".into(), collect_code_hashes(&root).unwrap()["app/b.py"].clone());
        // a.py на диске отличается от манифеста — но он в списке перенесённых.
        assert!(verify_code_manifest_except(&root, &expected, &["tree/a.py".into()]).is_ok());
        // b.py НЕ перенесён — его расхождение ловится.
        assert!(verify_code_manifest_except(&root, &expected, &[]).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn exact_carry_proof_protects_merged_added_deleted_and_unchanged_code() {
        use sha2::{Digest, Sha256};
        let root = std::env::temp_dir().join(format!("helene-carry-proof-{}", std::process::id()));
        std::fs::create_dir_all(root.join("tree")).unwrap();
        std::fs::create_dir_all(root.join("app/deskd")).unwrap();
        for (name, text) in [("app/deskapp.py", "new release"), ("app/deskd/readers.py", "new readers"),
            ("tree/deleted.py", "old"), ("tree/carried.py", "base"), ("tree/conflict.py", "release kept")] {
            std::fs::write(root.join(name), text).unwrap();
        }
        let release = collect_code_hashes(&root).unwrap();
        for (name, text) in [("app/deskapp.py", "merged desktop"), ("app/deskd/readers.py", "merged readers"),
            ("tree/carried.py", "agent edit"), ("tree/added.py", "agent addition")] {
            std::fs::write(root.join(name), text).unwrap();
        }
        std::fs::remove_file(root.join("tree/deleted.py")).unwrap();
        let mut proof = serde_json::Map::new();
        for path in ["app/deskapp.py", "app/deskd/readers.py", "tree/carried.py", "tree/added.py"] {
            proof.insert(path.into(), format!("{:x}", Sha256::digest(std::fs::read(root.join(path)).unwrap())).into());
        }
        proof.insert("tree/deleted.py".into(), serde_json::Value::Null);
        let report = serde_json::json!({"carried":["tree/carried.py","tree/added.py","tree/deleted.py"],
            "merged":["app/deskapp.py","app/deskd/readers.py"],"conflicts":[{"path":"tree/conflict.py"}],
            "applied_code_sha256":proof});
        assert!(verify_code_manifest_with_carry(&root, &release, &report, None).is_ok());
        // A retained edit is protected by its OWN exact hash, not a blanket skip.
        std::fs::write(root.join("app/deskapp.py"), "different later bytes").unwrap();
        let error = verify_code_manifest_with_carry(&root, &release, &report, None).unwrap_err();
        assert_eq!(error.matches("app/deskapp.py").count(), 1, "diagnostic must not duplicate paths");
        std::fs::write(root.join("app/deskapp.py"), "merged desktop").unwrap();
        std::fs::write(root.join("tree/conflict.py"), "unexpected code").unwrap();
        assert!(verify_code_manifest_with_carry(&root, &release, &report, None).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn carry_proof_requires_exact_path_set_and_hashes() {
        let release = std::collections::BTreeMap::from([("tree/a.py".into(), "a".repeat(64))]);
        for report in [
            serde_json::json!({"merged":["tree/a.py"]}),
            serde_json::json!({"carried":["tree/a.py"],"applied_code_sha256":{}}),
            serde_json::json!({"merged":[],"applied_code_sha256":{"tree/a.py":"b".repeat(64)}}),
            serde_json::json!({"merged":["app/../outside.py"],"applied_code_sha256":{"app/../outside.py":"b".repeat(64)}}),
            serde_json::json!({"carried":["tree/a.py"],"applied_code_sha256":{"tree/a.py":"invalid"}}),
        ] { assert!(expected_code_with_carry(&release, &report).is_err(), "{report}"); }
        assert_eq!(expected_code_with_carry(&release, &serde_json::Value::Null).unwrap(), release);
    }

    #[test]
    fn owner_interface_kept_by_installer_is_verified_against_its_own_snapshot() {
        let root = std::env::temp_dir().join(format!("helene-static-proof-{}", std::process::id()));
        std::fs::create_dir_all(root.join("tree")).unwrap();
        std::fs::create_dir_all(root.join("app/static")).unwrap();
        std::fs::write(root.join("app/static/index.html"), "release UI").unwrap();
        std::fs::write(root.join("app/deskapp.py"), "release app").unwrap();
        let release = collect_code_hashes(&root).unwrap();
        std::fs::write(root.join("app/static/index.html"), "owner UI").unwrap();
        std::fs::write(root.join("app/static/owner.css"), "owner style").unwrap();
        let retained = collect_static_hashes(&root).unwrap();
        assert!(verify_code_manifest_with_carry(&root, &release, &serde_json::Value::Null, Some(&retained)).is_ok());
        assert!(verify_code_manifest_with_carry(&root, &release, &serde_json::Value::Null, None).is_err());
        std::fs::write(root.join("app/static/owner.css"), "different subsequent style").unwrap();
        assert!(verify_code_manifest_with_carry(&root, &release, &serde_json::Value::Null, Some(&retained)).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }
}
