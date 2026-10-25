use std::{env, path::PathBuf, process::Command};
fn main() {
    println!("cargo:rerun-if-changed=../shell/icons/icon.ico");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") { return; }
    let icon=PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap()).join("../shell/icons/icon.ico").canonicalize().unwrap();
    let out=PathBuf::from(env::var_os("OUT_DIR").unwrap());
    let rc=out.join("icon.rc"); let object=out.join("icon.o");
    std::fs::write(&rc,format!("1 ICON \"{}\"\n",icon.display().to_string().replace('\\',"/"))).unwrap();
    let compiler=env::var_os("HELENE_WINDRES").expect("Set HELENE_WINDRES to the bundled GNU windres executable");
    assert!(Command::new(compiler).arg(&rc).arg("-O").arg("coff").arg("-o").arg(&object).status().unwrap().success(),"Icon resource compilation failed");
    println!("cargo:rustc-link-arg={}",object.display());
}
