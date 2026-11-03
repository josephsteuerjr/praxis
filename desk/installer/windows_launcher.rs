#![windows_subsystem = "windows"]

// Bootstrap only. Setup, recovery, UI and lifecycle stay in the bundled Python.
use std::{env, io, path::Path, process::Command, ptr};
use std::os::windows::process::CommandExt;
use std::os::windows::{ffi::OsStrExt, io::AsRawHandle};

#[link(name = "kernel32")]
extern "system" {
    fn CreateEventW(attributes: *mut std::ffi::c_void, manual: i32, initial: i32, name: *const u16) -> isize;
    fn CloseHandle(handle: isize) -> i32;
    fn WaitForMultipleObjects(count: u32, handles: *const isize, all: i32, timeout: u32) -> u32;
}
#[link(name = "ole32")]
extern "system" { fn CoCreateGuid(guid: *mut [u8; 16]) -> i32; }

struct Event(isize);
impl Drop for Event { fn drop(&mut self) { unsafe { CloseHandle(self.0); } } }
fn event(name: &str) -> io::Result<Event> {
    let wide: Vec<u16> = std::ffi::OsStr::new(name).encode_wide().chain(Some(0)).collect();
    let handle = unsafe { CreateEventW(ptr::null_mut(), 1, 0, wide.as_ptr()) };
    if handle == 0 { Err(io::Error::last_os_error()) } else { Ok(Event(handle)) }
}

#[link(name = "user32")]
extern "system" { fn MessageBoxW(window: isize, text: *const u16, title: *const u16, flags: u32) -> i32; }

fn launch() -> io::Result<bool> {
    let executable = env::current_exe()?;
    let root = executable.parent().ok_or_else(|| std::io::Error::other("Missing application folder"))?;
    let mut guid = [0u8; 16];
    if unsafe { CoCreateGuid(&mut guid) } < 0 { return Err(io::Error::other("Could not create startup identity")); }
    let name = format!("Local\\HeleneBootstrap-{}", guid.iter().map(|byte| format!("{byte:02x}")).collect::<String>());
    let ready = event(&name)?;
    let failed = event(&format!("{name}-failed"))?;
    let mut child = Command::new(root.join("runtime/pythonw.exe"))
        .arg("-s").arg(root.join("app/windows_start.py"))
        .arg("--root").arg(root).args(env::args_os().skip(1))
        .env("HELENE_BOOTSTRAP_EVENT", &name)
        .current_dir(root).creation_flags(0x0800_0000).spawn()?;
    // Readiness belongs to the real renderer/wizard. No retained files, Python
    // imports or second lifecycle owner are needed in this tiny entry point.
    let handles = [ready.0, failed.0, child.as_raw_handle() as isize];
    match unsafe { WaitForMultipleObjects(3, handles.as_ptr(), 0, 90_000) } {
        0 => Ok(true),
        1 => Ok(false), // Python has already presented the concrete error.
        2 => Err(io::Error::other(format!("Python exited before confirming its window ({}). Details: {}", child.wait()?, startup_log(root).display()))),
        258 => Err(io::Error::other(format!("Window startup is still unconfirmed (process {}). Details: {}", child.id(), startup_log(root).display()))),
        _ => Err(io::Error::last_os_error()),
    }
}

fn startup_log(root: &Path) -> std::path::PathBuf { root.join(".helene/logs/startup-error.log") }

fn main() {
    let result = launch();
    if matches!(result, Ok(false)) { std::process::exit(1); }
    if let Err(error) = result {
        let text: Vec<u16> = format!("Could not start Hélène. Keep helene.exe, app and runtime in the same folder.\n\n{error}")
            .encode_utf16().chain(Some(0)).collect();
        let title: Vec<u16> = "Hélène".encode_utf16().chain(Some(0)).collect();
        unsafe { MessageBoxW(0, text.as_ptr(), title.as_ptr(), 0x10); }
        std::process::exit(1);
    }
}
