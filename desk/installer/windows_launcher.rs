#![windows_subsystem = "windows"]

// Bootstrap only. Setup, recovery, UI and lifecycle stay in the bundled Python.
use helene_python_launcher::{language, present, text};
use std::os::windows::process::CommandExt;
use std::os::windows::{ffi::OsStrExt, io::AsRawHandle};
use std::{
    env, io,
    path::{Path, PathBuf},
    process::Command,
    ptr,
};

#[link(name = "kernel32")]
extern "system" {
    fn CreateEventW(
        attributes: *mut std::ffi::c_void,
        manual: i32,
        initial: i32,
        name: *const u16,
    ) -> isize;
    fn CloseHandle(handle: isize) -> i32;
    fn WaitForMultipleObjects(count: u32, handles: *const isize, all: i32, timeout: u32) -> u32;
}
#[link(name = "ole32")]
extern "system" {
    fn CoCreateGuid(guid: *mut [u8; 16]) -> i32;
}

struct Event(isize);
impl Drop for Event {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0);
        }
    }
}
fn event(name: &str) -> io::Result<Event> {
    let wide: Vec<u16> = std::ffi::OsStr::new(name)
        .encode_wide()
        .chain(Some(0))
        .collect();
    let handle = unsafe { CreateEventW(ptr::null_mut(), 1, 0, wide.as_ptr()) };
    if handle == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(Event(handle))
    }
}

enum LaunchError {
    Io(io::Error),
    Root,
    Identity,
    Exited(String, PathBuf),
    Unconfirmed(u32, PathBuf),
}
impl From<io::Error> for LaunchError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}
impl LaunchError {
    fn message(&self, lang: usize) -> String {
        match self {
            Self::Io(error) => error.to_string(),
            Self::Root => text("native.bootstrap.root_missing", lang, &[]),
            Self::Identity => text("native.bootstrap.identity", lang, &[]),
            Self::Exited(status, path) => text(
                "native.bootstrap.exited",
                lang,
                &[("status", status), ("path", &path.display().to_string())],
            ),
            Self::Unconfirmed(pid, path) => text(
                "native.bootstrap.unconfirmed",
                lang,
                &[
                    ("pid", &pid.to_string()),
                    ("path", &path.display().to_string()),
                ],
            ),
        }
    }
}

fn launch() -> Result<bool, LaunchError> {
    let executable = env::current_exe()?;
    let root = executable.parent().ok_or(LaunchError::Root)?;
    let mut guid = [0u8; 16];
    if unsafe { CoCreateGuid(&mut guid) } < 0 {
        return Err(LaunchError::Identity);
    }
    let name = format!(
        "Local\\HeleneBootstrap-{}",
        guid.iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>()
    );
    let ready = event(&name)?;
    let failed = event(&format!("{name}-failed"))?;
    let mut child = Command::new(root.join("runtime/pythonw.exe"))
        .arg("-s")
        .arg(root.join("app/windows_start.py"))
        .arg("--root")
        .arg(root)
        .args(env::args_os().skip(1))
        .env("HELENE_BOOTSTRAP_EVENT", &name)
        .current_dir(root)
        .creation_flags(0x0800_0000)
        .spawn()?;
    // Readiness belongs to the real renderer/wizard. No retained files, Python
    // imports or second lifecycle owner are needed in this tiny entry point.
    let handles = [ready.0, failed.0, child.as_raw_handle() as isize];
    match unsafe { WaitForMultipleObjects(3, handles.as_ptr(), 0, 300_000) } {
        0 => Ok(true),
        1 => Ok(false), // Python has already presented the concrete error.
        2 => Err(LaunchError::Exited(
            child.wait()?.to_string(),
            startup_log(root),
        )),
        258 => Err(LaunchError::Unconfirmed(child.id(), startup_log(root))),
        _ => Err(LaunchError::Io(io::Error::last_os_error())),
    }
}

fn startup_log(root: &Path) -> std::path::PathBuf {
    root.join(".helene/logs/startup-error.log")
}

fn main() {
    let root = env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(Path::to_path_buf));
    let lang = language(root.as_deref());
    let result = launch();
    if matches!(result, Ok(false)) {
        std::process::exit(1);
    }
    if let Err(error) = result {
        present(
            &text(
                "native.bootstrap.windows_error",
                lang,
                &[("detail", &error.message(lang))],
            ),
            &text("native.app.startup_title", lang, &[]),
        );
        std::process::exit(1);
    }
}
