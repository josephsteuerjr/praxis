//! Windows native file-dialog IO is Python-owned; other platforms retain their
//! prior native owner until their process custody can be qualified separately.
//!
//! No Windows file operation or fallback lives here. The bundled Python and the
//! reviewed source package are siblings of the native binary; the owner-only
//! HTTP route uses the same deskd.local_files implementation. A dispatched
//! save with a missing result is UNKNOWN, never a reason to retry blindly.
#[cfg(not(windows))]
#[path = "local_files_legacy.rs"]
mod native;
#[cfg(not(windows))]
pub use native::{list, read, save};

#[cfg(windows)]
use std::{io::{Read, Write}, path::Path, process::{Command, Stdio}, time::{Duration, Instant}};
#[cfg(windows)]
use serde_json::{json, Value};

#[cfg(windows)]
const MAX_CONTENT: usize = 64 * 1024 * 1024;
#[cfg(windows)]
const MAX_FRAME: usize = ((MAX_CONTENT + 2) / 3) * 4 + 65536;
#[cfg(windows)]
const FILE_CALL_TIMEOUT: Duration = Duration::from_secs(120);

#[cfg(windows)]
fn drain(mut pipe: impl Read, keep: usize) -> std::io::Result<Vec<u8>> {
    let mut result = Vec::new();
    let mut buffer = [0; 16384];
    loop {
        let count = pipe.read(&mut buffer)?;
        if count == 0 { break; }
        let room = keep.saturating_sub(result.len());
        result.extend_from_slice(&buffer[..room.min(count)]);
        // Continue draining even after a cut so the child cannot deadlock on
        // a full pipe. A cut is refused rather than parsed as a valid receipt.
    }
    Ok(result)
}

#[cfg(windows)]
pub fn list(path: &str) -> Result<Value, String> {
    invoke("list", json!({"path": path}))
}

#[cfg(windows)]
pub fn read(path: &str) -> Result<Value, String> {
    invoke("read", json!({"path": path}))
}

#[cfg(windows)]
pub fn save(folder: &str, name: &str, data: &str, overwrite: bool) -> Result<Value, String> {
    if data.len() > MAX_FRAME {
        return Err("Файл больше 64 МБ".into());
    }
    invoke("save", json!({"folder":folder,"name":name,"data":data,"overwrite":overwrite}))
}

#[cfg(windows)]
fn invoke(action: &str, args: Value) -> Result<Value, String> {
    let base = super::install_root();
    let script = base.join("app").join("localharness").join("local_files_cli.py");
    let python = super::bundled_python(&base);
    if !script.is_file() || !python.is_file() {
        return Err("Файловый диалог Python в этой установке недоступен".into());
    }
    run(&python, &script, action, args)
}

#[cfg(windows)]
fn run(python: &Path, script: &Path, action: &str, mut args: Value) -> Result<Value, String> {
    args["action"] = Value::String(action.into());
    let input = serde_json::to_vec(&args).map_err(|e| e.to_string())?;
    if input.len() > MAX_FRAME {
        return Err("Файл больше 64 МБ".into());
    }
    let mut cmd = Command::new(python);
    cmd.arg("-u").arg(script).env("PYTHONUTF8", "1")
        .env("HELENE_PARENT_PID", std::process::id().to_string())
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // Admission to the window's kill-on-close Job must happen BEFORE the
        // script sees any input, including a save it could commit after Stop.
        cmd.creation_flags(super::CREATE_NO_WINDOW | 0x4);
    }
    let mut child = cmd.spawn().map_err(|e| format!("Python файлового диалога не запустился: {e}"))?;
    if let Err(error) = super::adopt_suspended(&mut child) {
        return Err(format!("Python файлового диалога не получил Job: {error}"));
    }
    let pipes = (child.stdin.take(), child.stdout.take(), child.stderr.take());
    let (Some(input_pipe), Some(output_pipe), Some(error_pipe)) = pipes else {
        let _ = child.kill();
        let _ = child.wait();
        return Err("Python не открыл канал; исход файловой операции неизвестен".into());
    };
    let writer = std::thread::spawn(move || {
        let mut input_pipe = input_pipe;
        input_pipe.write_all(&input)
    });
    let reader = std::thread::spawn(move || drain(output_pipe, MAX_FRAME + 1));
    let errors = std::thread::spawn(move || drain(error_pipe, 0));
    let until = Instant::now() + FILE_CALL_TIMEOUT;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) if Instant::now() < until => std::thread::sleep(Duration::from_millis(25)),
            Ok(None) => break Err("Файловая операция превысила 120 с; исход неизвестен".to_string()),
            Err(e) => break Err(format!("Исход файловой операции неизвестен: {e}")),
        }
    };
    if status.is_err() { let _ = child.kill(); let _ = child.wait(); }
    // Join even on a failed write or timeout: Python may already have
    // committed a save. Never re-dispatch this invocation automatically.
    let sent = writer.join().ok().and_then(Result::ok).is_some();
    let output = reader.join().ok().and_then(Result::ok);
    let _ = errors.join();
    let status = status?;
    let output = output.ok_or("Исход файловой операции неизвестен; перечитай файл")?;
    if !sent || !status.success() || output.len() > MAX_FRAME {
        return Err("Исход файловой операции неизвестен; перечитай файл перед повтором".into());
    }
    let receipt: Value = serde_json::from_slice(&output)
        .map_err(|_| "Python не вернул квитанцию; перечитай файл перед повтором".to_string())?;
    match (receipt.get("ok").and_then(Value::as_bool), receipt.get("result")) {
        (Some(true), Some(value)) if value.is_object() => Ok(value.clone()),
        (Some(false), _) => Err(receipt.get("error").and_then(Value::as_str)
                                .unwrap_or("Python отказал в файловой операции").to_string()),
        _ => Err("Python вернул неполную квитанцию; перечитай файл перед повтором".into()),
    }
}

#[cfg(all(test, windows))] mod tests {
    use super::*;
    #[test] fn missing_python_refuses_without_rust_file_io() {
        assert!(run(Path::new("missing-python"), Path::new("missing-script"),
                    "list", json!({"path":"C:/"})).is_err());
    }
}
