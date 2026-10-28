//! Optional Windows CU executor. The body keeps admission, identity and its journal.
//!
//! A child is suspended until its kill-on-close Job owns it. After dispatch, a
//! broken pipe, timeout or dead child cannot prove whether a filesystem effect
//! occurred; the body records an error against the admitted request, never
//! invokes the native implementation or sends this request again.
use std::ffi::c_void;
use std::io::{Read, Write};
use std::os::windows::{io::AsRawHandle, process::CommandExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
    SetInformationJobObject, TerminateJobObject,
};

const CREATE_SUSPENDED: u32 = 0x4;
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const MAX_READ_REQUEST: usize = 65_536;
const MAX_READ_REPLY: usize = 8 * 1024 * 1024;
// 8 MiB of control characters can expand sixfold as JSON Unicode escapes.
const MAX_FS_REQUEST: usize = 64 * 1024 * 1024;
const MAX_FS_REPLY: usize = 64 * 1024 * 1024;

#[derive(Debug)]
pub struct EffectUncertain(pub String);
impl std::fmt::Display for EffectUncertain {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "{}; the filesystem effect may have occurred: inspect the path before retrying with a new request id",
            self.0
        )
    }
}
impl std::error::Error for EffectUncertain {}

pub fn selected_read(capability: &str) -> bool {
    std::env::var_os("HELENE_CU_PYTHON_READS").as_deref() == Some(std::ffi::OsStr::new("1"))
        && matches!(
            capability,
            "desktop.status" | "desktop.window.list" | "os.process.list"
        )
}

pub fn selected_fs(capability: &str) -> bool {
    std::env::var_os("HELENE_CU_PYTHON_FS").as_deref() == Some(std::ffi::OsStr::new("1"))
        && matches!(
            capability,
            "fs.stat"
                | "fs.list"
                | "fs.read"
                | "fs.hash"
                | "fs.write_atomic"
                | "fs.replace"
                | "fs.mkdir"
        )
}

struct Job(HANDLE);
impl Job {
    fn new() -> Result<Self> {
        let job = Self(unsafe { CreateJobObjectW(None, None)? });
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        unsafe {
            SetInformationJobObject(
                job.0,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const c_void,
                std::mem::size_of_val(&limits) as u32,
            )?
        };
        Ok(job)
    }
    fn adopt(&self, child: &std::process::Child) -> Result<()> {
        unsafe { AssignProcessToJobObject(self.0, HANDLE(child.as_raw_handle()))? };
        Ok(())
    }
    fn terminate(&self) {
        let _ = unsafe { TerminateJobObject(self.0, 1) };
    }
}
impl Drop for Job {
    fn drop(&mut self) {
        let _ = unsafe { CloseHandle(self.0) };
    }
}

#[link(name = "ntdll")]
unsafe extern "system" {
    fn NtResumeProcess(handle: *mut c_void) -> i32;
}

fn drain(mut pipe: impl Read, limit: usize) -> std::io::Result<Vec<u8>> {
    let mut retained = Vec::new();
    let mut buffer = [0u8; 16_384];
    loop {
        let count = pipe.read(&mut buffer)?;
        if count == 0 {
            return Ok(retained);
        }
        let remaining = (limit + 1).saturating_sub(retained.len());
        retained.extend_from_slice(&buffer[..remaining.min(count)]);
        // Keep draining; a truncated JSON prefix cannot become a positive receipt.
    }
}

pub fn dispatch_read(capability: &str, args: Value) -> Result<Value> {
    let python = std::env::var_os("HELENE_CU_PYTHON_EXE")
        .map(PathBuf::from)
        .context("Python CU interpreter not selected")?;
    let script = std::env::var_os("HELENE_CU_PYTHON_SCRIPT")
        .map(PathBuf::from)
        .context("Python CU script not selected")?;
    run(
        &python,
        &script,
        capability,
        args,
        Duration::from_secs(20),
        MAX_READ_REQUEST,
        MAX_READ_REPLY,
        false,
    )
}

pub fn dispatch_fs(capability: &str, args: Value) -> Result<Value> {
    let python = std::env::var_os("HELENE_CU_PYTHON_EXE")
        .map(PathBuf::from)
        .context("Python CU interpreter not selected")?;
    let script = std::env::var_os("HELENE_CU_PYTHON_FS_SCRIPT")
        .map(PathBuf::from)
        .context("Python CU filesystem script not selected")?;
    run(
        &python,
        &script,
        capability,
        args,
        Duration::from_secs(52),
        MAX_FS_REQUEST,
        MAX_FS_REPLY,
        matches!(capability, "fs.write_atomic" | "fs.replace" | "fs.mkdir"),
    )
}

fn run(
    python: &Path,
    script: &Path,
    capability: &str,
    args: Value,
    timeout: Duration,
    max_request: usize,
    max_reply: usize,
    effectful: bool,
) -> Result<Value> {
    if !python.is_absolute() || !python.is_file() || !script.is_absolute() || !script.is_file() {
        bail!("Python CU interpreter or script unavailable in selected package")
    }
    let input = serde_json::to_vec(&json!({"capability": capability, "args": args}))?;
    if input.len() > max_request {
        bail!("Python CU request exceeds {max_request} bytes")
    }
    let system_root = std::env::var_os("SystemRoot").context("Windows SystemRoot unavailable")?;
    let mut cmd = Command::new(python);
    cmd.args(["-I", "-B", "-u"])
        .arg(script)
        .env_clear()
        .env("SystemRoot", &system_root)
        .env("WINDIR", &system_root)
        .env("PATH", PathBuf::from(&system_root).join("System32"))
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW | CREATE_SUSPENDED);
    let mut child = cmd.spawn().context("start suspended Python CU executor")?;
    let mut could_execute = false;
    let mut explicit_refusal = false;
    let result = (|| -> Result<Value> {
        let job = Job::new().context("create CU child Job")?;
        job.adopt(&child)
            .context("admit CU child to Job before execution")?;
        if unsafe { NtResumeProcess(child.as_raw_handle()) } < 0 {
            bail!("resume of admitted Python CU child failed")
        }
        could_execute = true;
        let mut stdin = child.stdin.take().context("CU child stdin missing")?;
        let stdout = child.stdout.take().context("CU child stdout missing")?;
        let writer = std::thread::spawn(move || stdin.write_all(&input));
        let reader = std::thread::spawn(move || drain(stdout, max_reply));
        let deadline = Instant::now() + timeout;
        let status = loop {
            match child.try_wait() {
                Ok(Some(status)) => break Ok(status),
                Ok(None) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(20))
                }
                Ok(None) => break Err(anyhow::anyhow!("Python CU executor timed out")),
                Err(error) => {
                    break Err(anyhow::anyhow!(
                        "Python CU process outcome unknown: {error}"
                    ));
                }
            }
        };
        if status.is_err() {
            job.terminate();
            let _ = child.kill();
            let _ = child.wait();
        }
        let sent = writer.join().ok().and_then(Result::ok).is_some();
        let output = reader.join().ok().and_then(Result::ok);
        let status = status?;
        if !sent {
            bail!("Python CU request pipe failed")
        }
        if !status.success() {
            bail!("Python CU executor exited with {status}")
        }
        let output = output.context("Python CU result pipe failed")?;
        if output.len() > max_reply {
            bail!("Python CU result exceeded {max_reply} bytes")
        }
        let response: Value =
            serde_json::from_slice(&output).context("Python CU result invalid JSON")?;
        match (
            response.get("ok").and_then(Value::as_bool),
            response.get("result"),
        ) {
            (Some(true), Some(value))
                if value.get("ok") == Some(&Value::Bool(true))
                    || (capability == "fs.mkdir"
                        && value.get("ok") == Some(&Value::Bool(false))
                        && value.get("complete") == Some(&Value::Bool(false))) =>
            {
                Ok(value.clone())
            }
            (Some(false), _) => {
                let effect = response
                    .get("effect")
                    .and_then(Value::as_str)
                    .unwrap_or("unknown");
                explicit_refusal = matches!(effect, "none" | "precommit");
                bail!(
                    "Python CU refused (effect={effect}): {}",
                    response
                        .get("error")
                        .and_then(Value::as_str)
                        .unwrap_or("unknown error")
                )
            }
            _ => bail!("Python CU result missing positive receipt"),
        }
    })();
    if result.is_err() && child.try_wait().ok().flatten().is_none() {
        let _ = child.kill();
        let _ = child.wait();
    }
    if effectful && could_execute && !explicit_refusal {
        result.map_err(|error| anyhow::Error::new(EffectUncertain(format!("{error:#}"))))
    } else {
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn selected_verbs_are_exact() {
        assert!(!selected_fs("fs.delete"));
        assert!(!selected_read("desktop.input.perform"));
    }
}
