//! Linux-only deterministic executor. The existing body owns identity, journal,
//! request deduplication and artifacts; this module introduces no supervisor.
use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail};
use serde_json::Value;

const EXECUTOR: &str = include_str!("linux_desktop.py");

struct ExecutorChild(std::process::Child);
impl Drop for ExecutorChild {
    fn drop(&mut self) { let _ = self.0.kill(); let _ = self.0.wait(); }
}

fn nonblocking(pipe: &impl AsRawFd) -> Result<()> {
    let fd=pipe.as_raw_fd();
    let flags=unsafe { libc::fcntl(fd,libc::F_GETFL) };
    if flags<0 || unsafe { libc::fcntl(fd,libc::F_SETFL,flags|libc::O_NONBLOCK) }<0 {
        return Err(std::io::Error::last_os_error()).context("executor pipe configuration failed")
    }
    Ok(())
}

fn drain(pipe: &mut impl Read, bytes: &mut Vec<u8>, limit: usize, strict: bool) -> Result<bool> {
    let mut block=[0u8;16384];
    // Bound a single pass too: continuously writable stderr must not starve
    // the parent watchdog or delivery of the rest of the request.
    for _ in 0..64 {
        match pipe.read(&mut block) {
            Ok(0) => return Ok(true),
            Ok(n) => {
                if strict && bytes.len().saturating_add(n)>limit { bail!("Linux desktop response exceeds its byte limit; input may have occurred") }
                bytes.extend_from_slice(&block[..n.min(limit.saturating_sub(bytes.len()))]);
            }
            Err(e) if e.kind()==std::io::ErrorKind::WouldBlock => break,
            Err(e) if e.kind()==std::io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e.into()),
        }
    }
    Ok(false)
}

pub fn dispatch(capability: &str, args: Value, state: &Path) -> Result<Value> {
    let payload = serde_json::to_vec(&args)?;
    if payload.len() > 4 * 1024 * 1024 { bail!("Linux desktop request exceeds 4 MiB") }
    let executable = std::env::current_exe()?.canonicalize()?;
    let bundled = executable.parent().context("body executable has no parent")?.join("runtime/bin/python3");
    // A direct source/native test can use the distro interpreter. A package
    // always contains its own interpreter next to helene-body.
    let python = if bundled.is_file() { bundled } else { PathBuf::from("/usr/bin/python3") };
    if !python.is_file() { bail!("Linux desktop executor requires the package Python runtime") }
    let parent = std::process::id() as libc::pid_t;
    let mut command = Command::new(python);
    command.args(["-I", "-c", EXECUTOR, capability]).arg(state)
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    unsafe {
        command.pre_exec(move || {
            // Python's TERM handler releases input. KILL here would race the
            // process-group grace and leave keys pressed on normal owner stop.
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            // pre_exec runs after fork in a multi-threaded body: do not allocate
            // an Error::other string on the parent-death race path.
            if libc::getppid() != parent { return Err(std::io::Error::from_raw_os_error(libc::ESRCH)); }
            Ok(())
        });
    }
    let started=Instant::now();
    let deadline=if matches!(capability,"desktop.window.read"|"desktop.element.find"|"desktop.element.act") {
        let requested=args.get("timeout_ms").and_then(Value::as_u64).filter(|n| *n>0).unwrap_or(5000).min(30000);
        Duration::from_millis(requested+3000)
    } else if capability=="desktop.window.list" {
        Duration::from_millis(args.get("timeout_ms").and_then(Value::as_u64).unwrap_or(20000).min(30000)+3000)
    } else { Duration::from_secs(55) };
    let mut child = ExecutorChild(command.spawn().context("Linux desktop executor did not start")?);
    let mut stdin=Some(child.0.stdin.take().context("executor stdin unavailable")?);
    let mut stdout=child.0.stdout.take().context("executor stdout unavailable")?;
    let mut stderr=child.0.stderr.take().context("executor stderr unavailable")?;
    nonblocking(stdin.as_ref().unwrap())?; nonblocking(&stdout)?; nonblocking(&stderr)?;
    let (mut received,mut errors,mut written)=(Vec::new(),Vec::new(),0);
    let (mut stdout_done,mut stderr_done)=(false,false);
    let status=loop {
        if started.elapsed()>deadline { bail!("Linux desktop executor exceeded its parent deadline; input may have occurred, do not repeat it with a new key") }
        if let Some(pipe)=stdin.as_mut() {
            match pipe.write(&payload[written..]) {
                Ok(0) => bail!("Linux desktop request delivery stopped; input may have occurred"),
                Ok(n) => written+=n,
                Err(e) if matches!(e.kind(),std::io::ErrorKind::WouldBlock|std::io::ErrorKind::Interrupted) => {},
                Err(e) => return Err(e).context("desktop request was not delivered"),
            }
            if written==payload.len() { stdin=None; }
        }
        if !stdout_done { stdout_done=drain(&mut stdout,&mut received,16*1024*1024,true)?; }
        if !stderr_done { stderr_done=drain(&mut stderr,&mut errors,2000,false)?; }
        if let Some(status)=child.0.try_wait()?
            && stdout_done && stderr_done {
            break status;
        }
        std::thread::sleep(Duration::from_millis(5));
    };
    if !status.success() {
        bail!("Linux desktop executor failed ({status}); input may have occurred, do not repeat it with a new key")
    }
    let value: Value = serde_json::from_slice(&received).context("invalid Linux desktop receipt; input may have occurred")?;
    if value["ok"] != true {
        bail!("{}", value["error"].as_str().unwrap_or("Linux desktop action was not confirmed"))
    }
    Ok(value)
}

pub fn read(capability: &str, args: Value) -> Result<Value> {
    dispatch(capability, args, Path::new("."))
}
