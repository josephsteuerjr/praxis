// Exact installed code proof, independent of the agent's acceptance message.
pub(crate) fn verify_code_manifest(root: &std::path::Path, expected: &std::collections::BTreeMap<String, String>) -> Result<(), String> {
    use sha2::{Digest, Sha256};
    use std::io::Read;
    if expected.is_empty() { return Err("release has no code hashes; installation is unverified".into()); }
    let mut actual = std::collections::BTreeMap::new();
    fn visit(root: &std::path::Path, dir: &std::path::Path, actual: &mut std::collections::BTreeMap<String, String>) -> Result<(), String> {
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
    if &actual != expected {
        let changed: Vec<_> = actual.keys().chain(expected.keys()).filter(|p| actual.get(*p) != expected.get(*p)).take(10).cloned().collect();
        return Err(format!("installed code differs from release: {}", changed.join(", ")));
    }
    Ok(())
}

#[cfg(test)]
mod code_manifest_tests {
    use super::verify_code_manifest;
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
}
