#![windows_subsystem = "windows"]

// Bootstrap only. Setup, recovery, UI and lifecycle stay in the bundled Python.
use std::{env, process::Command};
use std::os::windows::process::CommandExt;

#[link(name = "user32")]
extern "system" { fn MessageBoxW(window: isize, text: *const u16, title: *const u16, flags: u32) -> i32; }

fn launch() -> std::io::Result<()> {
    let executable = env::current_exe()?;
    let root = executable.parent().ok_or_else(|| std::io::Error::other("Missing application folder"))?;
    Command::new(root.join("runtime/pythonw.exe"))
        .arg("-s").arg(root.join("app/python_app.py"))
        .arg("--root").arg(root).args(env::args_os().skip(1))
        .current_dir(root).creation_flags(0x0800_0000).spawn()?;
    Ok(())
}

fn main() {
    if let Err(error) = launch() {
        let text: Vec<u16> = format!("Could not start Hélène. Keep helene.exe, app and runtime in the same folder.\n\n{error}")
            .encode_utf16().chain(Some(0)).collect();
        let title: Vec<u16> = "Hélène".encode_utf16().chain(Some(0)).collect();
        unsafe { MessageBoxW(0, text.as_ptr(), title.as_ptr(), 0x10); }
        std::process::exit(1);
    }
}
