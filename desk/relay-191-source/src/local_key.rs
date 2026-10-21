//! Persistent client credential shared by the desktop and headless modes.
use anyhow::{bail, Context, Result};
use std::{fs, io::Write, path::Path};

pub(crate) const KEY_FILE: &str = "api-key.txt";
const SUFFIX_LEN: usize = 44;

pub(crate) fn load(root: &Path, explicit: Option<&str>) -> Result<String> {
    if let Some(key) = explicit.map(str::trim).filter(|key| !key.is_empty()) {
        validate_header(key)?;
        return Ok(key.to_owned());
    }
    let path = root.join(KEY_FILE);
    if path.exists() {
        return read(&path);
    }
    // UUID v4 uses the OS CSPRNG. Two independent draws provide enough random
    // hex digits for legacy clients that validate the sk- key shape locally.
    let random = format!("{}{}", uuid::Uuid::new_v4().simple(), uuid::Uuid::new_v4().simple());
    let key = format!("sk-{}", &random[..SUFFIX_LEN]);
    let mut file = tempfile::NamedTempFile::new_in(root)
        .context("Cannot create the relay client-key file")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.as_file().set_permissions(fs::Permissions::from_mode(0o600))?;
    }
    writeln!(file, "{key}")?;
    file.as_file_mut().sync_all()?;
    match file.persist_noclobber(&path) {
        Ok(_) => Ok(key),
        Err(error) if error.error.kind() == std::io::ErrorKind::AlreadyExists => read(&path),
        Err(error) => Err(error.error).context("Cannot save the relay client-key file"),
    }
}

fn validate_header(key: &str) -> Result<()> {
    if key.is_empty() || !key.bytes().all(|byte| byte.is_ascii_graphic()) {
        bail!("The relay client key must contain printable ASCII characters without spaces");
    }
    Ok(())
}

fn read(path: &Path) -> Result<String> {
    let key = fs::read_to_string(path).context("Cannot read the relay client-key file")?;
    let key = key.trim();
    validate_header(key)?;
    if !key.starts_with("sk-") || key.len() != SUFFIX_LEN + 3
        || !key[3..].bytes().all(|byte| byte.is_ascii_alphanumeric())
    {
        bail!("Invalid relay client-key file; expected sk- followed by 44 letters or digits. The existing file was preserved");
    }
    Ok(key.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_key_has_client_shape_and_survives_reload() {
        let root = tempfile::tempdir().unwrap();
        let key = load(root.path(), None).unwrap();
        assert_eq!(key.len(), 47);
        assert!(key.starts_with("sk-"));
        assert!(key[3..].bytes().all(|byte| byte.is_ascii_alphanumeric()));
        assert_eq!(load(root.path(), None).unwrap(), key);
        let other = tempfile::tempdir().unwrap();
        assert_ne!(load(other.path(), None).unwrap(), key);
    }

    #[test]
    fn explicit_host_key_does_not_rotate_the_persistent_default() {
        let root = tempfile::tempdir().unwrap();
        let original = load(root.path(), None).unwrap();
        assert_eq!(load(root.path(), Some(" host-configured-key ")).unwrap(), "host-configured-key");
        assert_eq!(load(root.path(), Some(" ")).unwrap(), original);
        assert!(load(root.path(), Some("invalid key")).is_err());
    }

    #[test]
    fn corrupt_key_fails_without_silently_replacing_it() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join(KEY_FILE);
        fs::write(&path, "broken\n").unwrap();
        assert!(load(root.path(), None).is_err());
        assert_eq!(fs::read_to_string(path).unwrap(), "broken\n");
    }
}
