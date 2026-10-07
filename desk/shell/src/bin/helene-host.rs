// Compile the SAME commands and supervisor as the desktop shell, without Tauri.
#[path = "../main.rs"]
mod runtime;
fn main() { runtime::run_host(); }
