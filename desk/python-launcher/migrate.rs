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
        present(
            &text("native.bootstrap.root_missing", lang, &[]),
            &text("native.app.startup_title", lang, &[]),
        );
        return ExitCode::FAILURE;
    };
    let python = root.join(if cfg!(windows) {
        "runtime/python.exe"
    } else {
        "runtime/bin/python3"
    });
    match Command::new(python)
        .arg("-s")
        .arg(root.join("app/python_migrate.py"))
        .args(std::env::args_os().skip(1))
        .current_dir(&root)
        .status()
    {
        Ok(s) => ExitCode::from(s.code().unwrap_or(1) as u8),
        Err(e) => {
            present(
                &text(
                    "native.bootstrap.migration_error",
                    lang,
                    &[("detail", &e.to_string())],
                ),
                &text("native.app.startup_title", lang, &[]),
            );
            ExitCode::FAILURE
        }
    }
}
