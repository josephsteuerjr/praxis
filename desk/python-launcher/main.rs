#![cfg_attr(windows, windows_subsystem = "windows")]
use helene_python_launcher::{language, present, text};
use std::{
    path::PathBuf,
    process::{Command, ExitCode},
};
fn main() -> ExitCode {
    let root = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(PathBuf::from));
    let lang = language(root.as_deref());
    let Some(root) = root else {
        startup_error(&text("native.bootstrap.root_missing", lang, &[]), lang);
        return ExitCode::FAILURE;
    };
    let python = root.join(if cfg!(windows) {
        "runtime/pythonw.exe"
    } else {
        "runtime/bin/python3"
    });
    match Command::new(python)
        .arg("-s")
        .arg(root.join("app/python_app.py"))
        .arg("--root")
        .arg(&root)
        .args(std::env::args_os().skip(1))
        .current_dir(&root)
        .status()
    {
        Ok(s) => ExitCode::from(s.code().unwrap_or(1) as u8),
        Err(e) => {
            startup_error(
                &text(
                    "native.bootstrap.python_error",
                    lang,
                    &[("detail", &e.to_string())],
                ),
                lang,
            );
            ExitCode::FAILURE
        }
    }
}

fn startup_error(message: &str, lang: usize) {
    present(message, &text("native.app.startup_title", lang, &[]));
}
