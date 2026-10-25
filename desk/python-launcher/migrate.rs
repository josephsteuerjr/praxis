#![cfg_attr(windows, windows_subsystem = "windows")]
use std::{path::PathBuf,process::{Command,ExitCode}};
fn main()->ExitCode {
    let root=std::env::current_exe().ok().and_then(|p|p.parent().map(PathBuf::from)).expect("update asset directory");
    let python=root.join(if cfg!(windows){"runtime/python.exe"}else{"runtime/bin/python3"});
    match Command::new(python).arg("-s").arg(root.join("app/python_migrate.py"))
        .args(std::env::args_os().skip(1)).current_dir(&root).status(){
        Ok(s)=>ExitCode::from(s.code().unwrap_or(1) as u8),Err(e)=>{eprintln!("Migration did not start: {e}");ExitCode::FAILURE}
    }
}
