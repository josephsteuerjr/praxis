use std::{collections::BTreeSet, env, fs, path::PathBuf, process::Command};

fn legacy_icon() {
    println!("cargo:rerun-if-changed=../shell/icons/icon.ico");
    // The archive bootstrap receives its icon through the existing Win32
    // resource writer. Preserve the original windres hook for legacy bins.
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows")
        || env::var_os("CARGO_FEATURE_WINDOWS_BOOTSTRAP").is_some()
    {
        return;
    }
    let icon = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap())
        .join("../shell/icons/icon.ico")
        .canonicalize()
        .unwrap();
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    let rc = out.join("icon.rc");
    let object = out.join("icon.o");
    fs::write(
        &rc,
        format!(
            "1 ICON \"{}\"\n",
            icon.display().to_string().replace('\\', "/")
        ),
    )
    .unwrap();
    let compiler = env::var_os("HELENE_WINDRES")
        .expect("Set HELENE_WINDRES to the bundled GNU windres executable");
    assert!(
        Command::new(compiler)
            .arg(&rc)
            .arg("-O")
            .arg("coff")
            .arg("-o")
            .arg(&object)
            .status()
            .unwrap()
            .success(),
        "Icon resource compilation failed"
    );
    println!("cargo:rustc-link-arg={}", object.display());
}

fn parameters(text: &str) -> BTreeSet<&str> {
    let mut result = BTreeSet::new();
    let mut rest = text;
    while let Some(start) = rest.find('{') {
        rest = &rest[start + 1..];
        let end = rest
            .find('}')
            .expect("bootstrap caption parameter is not closed");
        let key = &rest[..end];
        assert!(!key.is_empty() && key.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_'));
        result.insert(key);
        rest = &rest[end + 1..];
    }
    result
}

fn main() {
    let home = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let keys = [
        "native.app.startup_title",
        "native.bootstrap.root_missing",
        "native.bootstrap.identity",
        "native.bootstrap.exited",
        "native.bootstrap.unconfirmed",
        "native.bootstrap.windows_error",
        "native.bootstrap.python_error",
        "native.bootstrap.migration_error",
    ];
    let catalogs: Vec<serde_json::Value> = ["ru", "en", "fr", "zh"]
        .iter()
        .map(|lang| {
            let path = home.join("../lang").join(format!("{lang}.json"));
            println!("cargo:rerun-if-changed={}", path.display());
            serde_json::from_slice(&fs::read(path).expect("canonical interface catalog"))
                .expect("valid interface JSON")
        })
        .collect();
    let mut generated = String::from("// Generated from canonical desk/lang; do not edit.\npub fn template(key: &str, language: usize) -> Option<&'static str> {\n let code=if language<4 {language} else {1};\n match key {\n");
    for key in keys {
        let texts: Vec<&str> = catalogs
            .iter()
            .map(|catalog| catalog[key].as_str().expect("bootstrap caption missing"))
            .collect();
        let expected = parameters(texts[0]);
        assert!(
            texts.iter().all(|text| parameters(text) == expected),
            "bootstrap caption parameters differ: {key}"
        );
        generated.push_str(&format!(" {key:?} => Some({texts:?}[code]),\n"));
    }
    generated.push_str(" _ => None,\n }\n}\n");
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap()).join("bootstrap_text.rs");
    fs::write(out, generated).expect("write bootstrap captions");
    legacy_icon();
}
