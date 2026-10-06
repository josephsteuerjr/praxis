// Exact installed code proof, independent of the agent's acceptance message.
// Импорты — внутри функций, пути — полностью квалифицированные: файл include!-ится
// в setup/src/trial.rs и не должен конфликтовать с импортами включая­го модуля.

/// Собрать фактические хэши установленного кода (`tree/` + `app/`, без `__pycache__`).
/// Установщик снимает этот слепок ПОСЛЕ переноса правок агента (шаг 7б) и передаёт
/// в испытание: перенесённые установщиком правки — часть установки, а всё, что
/// изменилось ВО ВРЕМЯ испытания, по-прежнему видно сверке.
pub(crate) fn collect_code_hashes(root: &std::path::Path) -> Result<std::collections::BTreeMap<String, String>, String> {
    use sha2::{Digest, Sha256};
    use std::io::Read;
    let mut actual = std::collections::BTreeMap::new();
    fn visit(root: &std::path::Path, dir: &std::path::Path, actual: &mut std::collections::BTreeMap<String, String>) -> Result<(), String> {
        use sha2::{Digest, Sha256};
        use std::io::Read;
        for entry in std::fs::read_dir(dir).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            let path = entry.path();
            let kind = entry.file_type().map_err(|e| e.to_string())?;
            if kind.is_symlink() { return Err(format!("symlink in installed code: {}", path.display())); }
            if kind.is_dir() {
                if entry.file_name() != "__pycache__" && path != root.join("app/static.prev") { visit(root, &path, actual)?; }
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
    for dir in ["tree", "app"] { visit(root, &root.join(dir), &mut actual)?; }
    Ok(actual)
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
        let changed: Vec<String> = actual.keys().chain(expected.keys())
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
        let changed: Vec<String> = actual.keys().chain(expected.keys())
            .filter(|p| actual.get(*p) != expected.get(*p))
            .take(10).map(|p| p.to_string()).collect();
        return Err(format!("installed code differs from the checked snapshot: {}", changed.join(", ")));
    }
    Ok(())
}

#[cfg(test)]
mod code_manifest_tests {
    use super::{collect_code_hashes, verify_code_manifest, verify_code_manifest_except};
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
}
