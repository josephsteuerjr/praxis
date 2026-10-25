#![cfg_attr(windows, windows_subsystem = "windows")]
use std::{
    path::PathBuf,
    process::{Command, ExitCode},
};
fn main() -> ExitCode {
    let root = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(PathBuf::from))
        .expect("launcher directory");
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
            startup_error(&format!("Python application did not start: {e}"));
            ExitCode::FAILURE
        }
    }
}

fn startup_error(message: &str) {
    eprintln!("{message}");
    #[cfg(windows)]
    {
        #[link(name = "user32")]
        unsafe extern "system" {
            fn MessageBoxW(
                window: *mut std::ffi::c_void,
                text: *const u16,
                title: *const u16,
                kind: u32,
            ) -> i32;
        }
        let text: Vec<u16> = message.encode_utf16().chain(Some(0)).collect();
        let title: Vec<u16> = "Hélène — startup failed"
            .encode_utf16()
            .chain(Some(0))
            .collect();
        unsafe {
            MessageBoxW(std::ptr::null_mut(), text.as_ptr(), title.as_ptr(), 0x10);
        }
    }
}
