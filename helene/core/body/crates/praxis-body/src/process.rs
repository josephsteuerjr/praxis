use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use praxis_body_protocol::{ExecutionIdentity, OperationStatus};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::{fsops, identity};

/// Оболочка. Имена в JSON — snake_case: дерево шлёт `power_shell` всегда
/// (`helene/core/agent.py`, `tool_computer` → `process_start(shell="power_shell")`),
/// и на macOS это принимается и исполняется zsh — см. `shell_plan`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ShellKind {
    Direct,
    PowerShell,
    Cmd,
    Wsl,
    /// POSIX-оболочки (порт на macOS, 19.09): `/bin/sh -c`, `/bin/zsh -lc`, `/bin/bash -lc`.
    /// На Windows их нет — отказ словами, а не поиск git-bash по диску.
    Sh,
    Zsh,
    Bash,
}

/// Что на ЭТОЙ платформе будет исполнять просьбу с такой оболочкой. Считается до захвата
/// каталога операции, чтобы невозможная просьба не оставила после себя «стартующую»
/// операцию, и едет в квитанцию `process.start` полем `shell_used` (+ `note`, если тело
/// подменило просьбу: PowerShell на macOS — это zsh, и молчать об этом нельзя).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ShellPlan {
    pub used: &'static str,
    pub note: Option<&'static str>,
}

pub fn shell_plan(shell: ShellKind) -> Result<ShellPlan> {
    let plain = |used| Ok(ShellPlan { used, note: None });
    match shell {
        ShellKind::Direct => plain("direct"),
        ShellKind::PowerShell if cfg!(target_os = "macos") => Ok(ShellPlan {
            used: "zsh",
            note: Some(
                "PowerShell requested; macOS ran the command with /bin/zsh -lc (login shell, \
                 UTF-8): PowerShell syntax (cmdlets, $env:, Get-*) will not work here",
            ),
        }),
        ShellKind::PowerShell => plain("powershell"),
        ShellKind::Cmd if cfg!(target_os = "macos") => {
            anyhow::bail!("shell cmd is not available on macOS: use zsh, sh, bash or direct")
        }
        ShellKind::Cmd => plain("cmd"),
        ShellKind::Wsl if cfg!(target_os = "macos") => {
            anyhow::bail!("shell wsl is not available on macOS: use zsh, sh, bash or direct")
        }
        ShellKind::Wsl => plain("wsl"),
        ShellKind::Sh | ShellKind::Zsh | ShellKind::Bash if cfg!(windows) => anyhow::bail!(
            "shell {} is not available on Windows: use power_shell, cmd or wsl",
            shell_name(shell)
        ),
        ShellKind::Sh => plain("sh"),
        ShellKind::Zsh => plain("zsh"),
        ShellKind::Bash => plain("bash"),
    }
}

fn shell_name(shell: ShellKind) -> &'static str {
    match shell {
        ShellKind::Direct => "direct",
        ShellKind::PowerShell => "power_shell",
        ShellKind::Cmd => "cmd",
        ShellKind::Wsl => "wsl",
        ShellKind::Sh => "sh",
        ShellKind::Zsh => "zsh",
        ShellKind::Bash => "bash",
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TerminalMode {
    Pipes,
    ConPty,
}

fn default_shell() -> ShellKind {
    ShellKind::Direct
}

fn default_mode() -> TerminalMode {
    TerminalMode::Pipes
}

const POWERSHELL_UTF8_PREAMBLE: &str = concat!(
    "$__PraxisUtf8 = New-Object System.Text.UTF8Encoding($false)\r\n",
    "[Console]::OutputEncoding = $__PraxisUtf8\r\n",
    "$OutputEncoding = $__PraxisUtf8\r\n",
    "Remove-Variable __PraxisUtf8 -ErrorAction SilentlyContinue\r\n",
);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProcessStartArgs {
    #[serde(default)]
    pub program: Option<String>,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub command: Option<String>,
    #[serde(default = "default_shell")]
    pub shell: ShellKind,
    #[serde(default = "default_mode")]
    pub terminal: TerminalMode,
    #[serde(default)]
    pub cwd: Option<PathBuf>,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    #[serde(default)]
    pub timeout_s: u64,
    #[serde(default)]
    pub name: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct OperationState {
    operation_id: String,
    status: OperationStatus,
    supervisor_pid: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    child_pid: Option<u32>,
    identity: ExecutionIdentity,
    created_at: DateTime<Utc>,
    updated_at: DateTime<Utc>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    started_at: Option<DateTime<Utc>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    finished_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct OperationResult {
    operation_id: String,
    status: OperationStatus,
    exit_code: Option<i32>,
    duration_ms: u128,
    stdout_log: PathBuf,
    stderr_log: PathBuf,
    finished_at: DateTime<Utc>,
}

fn safe_operation_id(value: &str) -> Result<&str> {
    if value.is_empty()
        || value.len() > 96
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    {
        anyhow::bail!("invalid operation id");
    }
    Ok(value)
}

fn operation_dir(state_dir: &Path, operation_id: &str) -> Result<PathBuf> {
    Ok(state_dir
        .join("operations")
        .join(safe_operation_id(operation_id)?))
}

fn write_json(path: &Path, value: &impl Serialize) -> Result<()> {
    let raw = serde_json::to_vec_pretty(value)?;
    fsops::atomic_write(path, &raw)
}

pub fn start(state_dir: &Path, operation_id: &str, args: ProcessStartArgs) -> Result<Value> {
    if matches!(args.terminal, TerminalMode::ConPty) {
        anyhow::bail!("ConPTY is reserved by the v1 contract but not enabled in this build");
    }
    // Оболочка, которой на этой платформе нет (cmd/wsl на macOS, sh/zsh/bash на Windows),
    // отклоняется ЗДЕСЬ, до захвата каталога операции: иначе отказ случился бы в супервизоре
    // и снаружи выглядел бы как «стартовала и упала», а не как «так нельзя».
    let shell = shell_plan(args.shell)?;
    // Resolve the supervisor binary before claiming the operation id. A failure here must not
    // leave behind a directory that later callers would mistake for an in-flight operation.
    let executable = std::env::current_exe().context("locate praxis-body executable")?;
    let dir = operation_dir(state_dir, operation_id)?;
    let operations = dir.parent().context("operation directory has no parent")?;
    fs::create_dir_all(operations)?;
    match fs::create_dir(&dir) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            return replay_existing_start(state_dir, operation_id, &dir, &args);
        }
        Err(error) => return Err(error).context("claim process operation directory"),
    }
    let state = OperationState {
        operation_id: operation_id.into(),
        status: OperationStatus::Starting,
        supervisor_pid: 0,
        child_pid: None,
        identity: identity::current(),
        created_at: Utc::now(),
        updated_at: Utc::now(),
        started_at: None,
        finished_at: None,
    };
    if let Err(error) = write_json(&dir.join("request.json"), &args)
        .and_then(|_| write_json(&dir.join("state.json"), &state))
    {
        let _ = fs::remove_dir_all(&dir);
        return Err(error).context("prepare process operation");
    }

    let mut command = Command::new(executable);
    command
        .arg("supervise")
        .arg("--state-dir")
        .arg(state_dir)
        .arg("--operation-id")
        .arg(operation_id)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .env("PRAXIS_BODY_SUPERVISOR", "1");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        command.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP);
    }
    let supervisor = match command.spawn() {
        Ok(value) => value,
        Err(error) => {
            let _ = fs::remove_dir_all(&dir);
            return Err(error).context("spawn detached operation supervisor");
        }
    };
    write_json(
        &dir.join("launcher.json"),
        &json!({"supervisor_pid": supervisor.id(), "launched_at": Utc::now()}),
    )?;
    let mut receipt = json!({
        "ok": true,
        "operation_id": operation_id,
        "status": OperationStatus::Starting,
        "supervisor_pid": supervisor.id(),
        "operation_dir": dir,
    });
    // Windows-квитанция остаётся прежней; на macOS/Linux в ней названа оболочка, которая
    // исполняет просьбу на самом деле, и заметка, если тело её подменило.
    if !cfg!(windows) {
        receipt["shell_used"] = Value::String(shell.used.into());
        if let Some(note) = shell.note {
            receipt["note"] = Value::String(note.into());
        }
    }
    Ok(receipt)
}

fn replay_existing_start(
    state_dir: &Path,
    operation_id: &str,
    dir: &Path,
    requested: &ProcessStartArgs,
) -> Result<Value> {
    // Another Runtime/process can win the directory claim a few instructions before its atomic
    // request/state files appear. Wait for that small preparation window, then prove that this
    // operation id names the same command. An operation id is never an alias for different work.
    let deadline = Instant::now() + Duration::from_secs(2);
    let existing = loop {
        match fs::read(dir.join("request.json"))
            .context("read existing operation request")
            .and_then(|raw| serde_json::from_slice::<ProcessStartArgs>(&raw).map_err(Into::into))
        {
            Ok(value) => break value,
            Err(error) if Instant::now() < deadline => {
                let _ = error;
                thread::sleep(Duration::from_millis(10));
            }
            Err(error) => return Err(error),
        }
    };
    if &existing != requested {
        anyhow::bail!(
            "operation_id {operation_id} is already bound to different process arguments"
        );
    }
    loop {
        match status(state_dir, operation_id, 16_384) {
            Ok(value)
                if value.get("status").and_then(Value::as_str) == Some("starting")
                    && value.get("supervisor_pid").is_none_or(Value::is_null)
                    && Instant::now() < deadline =>
            {
                // The winner has committed the request/state pair but has not yet committed its
                // launcher record. Keep the replay response useful instead of exposing a
                // transient `starting` operation with no process identity.
                thread::sleep(Duration::from_millis(10));
            }
            Ok(value) => return Ok(value),
            Err(error) if Instant::now() < deadline => {
                let _ = error;
                thread::sleep(Duration::from_millis(10));
            }
            Err(error) => return Err(error),
        }
    }
}

/// Тонкая обёртка: любой ранний отказ супервизора обязан стать ЗАПИСАННЫМ исходом.
///
/// ⚠ Раньше отказ до запуска ребёнка (не прочитался запрос, не создался лог, не собралась
/// команда, не стартовал процесс, не привязался Job) просто ронял супервизор с ошибкой —
/// и `result.json` не появлялся НИКОГДА. Снаружи это выглядело как «стартует», а через
/// 15 секунд превращалось в `in_doubt`: то есть «я не знаю, что случилось» — при том что
/// причина была известна ровно в момент отказа и умирала вместе с процессом.
/// Незнание, которого можно было избежать, — это та же ложь, что и выдумка.
pub fn supervise(state_dir: &Path, operation_id: &str) -> Result<()> {
    let outcome = supervise_inner(state_dir, operation_id);
    if let Err(error) = &outcome
        && let Ok(dir) = operation_dir(state_dir, operation_id)
        && dir.is_dir()
        && !dir.join("result.json").exists()
    {
        // Пишем и состояние, и исход: карточка читает первое, список — второе, и
        // расходиться им нельзя (см. effective_status).
        if let Ok(raw) = fs::read(dir.join("state.json"))
            && let Ok(mut state) = serde_json::from_slice::<OperationState>(&raw)
        {
            state.status = OperationStatus::Failed;
            state.updated_at = Utc::now();
            state.finished_at = Some(Utc::now());
            let _ = write_json(&dir.join("state.json"), &state);
        }
        let _ = write_json(
            &dir.join("result.json"),
            &OperationResult {
                operation_id: operation_id.into(),
                status: OperationStatus::Failed,
                exit_code: None,
                duration_ms: 0,
                stdout_log: dir.join("stdout.log"),
                stderr_log: dir.join("stderr.log"),
                finished_at: Utc::now(),
            },
        );
        // Причина отдельным файлом: в OperationResult поля под неё нет, а терять её нельзя —
        // это единственное, что отличает «упало вот из-за этого» от «неизвестно».
        let _ = fsops::atomic_write(
            &dir.join("supervisor_error.txt"),
            format!("{error:#}").as_bytes(),
        );
    }
    outcome
}

fn supervise_inner(state_dir: &Path, operation_id: &str) -> Result<()> {
    let dir = operation_dir(state_dir, operation_id)?;
    let args: ProcessStartArgs = serde_json::from_slice(
        &fs::read(dir.join("request.json")).context("read operation request")?,
    )?;
    let mut state: OperationState =
        serde_json::from_slice(&fs::read(dir.join("state.json")).context("read operation state")?)?;
    state.supervisor_pid = std::process::id();
    state.identity = identity::current();
    state.status = OperationStatus::Running;
    state.started_at = Some(Utc::now());
    state.updated_at = Utc::now();

    let stdout_path = dir.join("stdout.log");
    let stderr_path = dir.join("stderr.log");
    let stdout = File::create(&stdout_path)?;
    let stderr = File::create(&stderr_path)?;
    let mut command = build_command(&dir, &args)?;
    command
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    if let Some(cwd) = &args.cwd {
        command.current_dir(cwd);
    }
    command.envs(&args.env);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        use windows::Win32::System::Threading::CREATE_NO_WINDOW;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        command.creation_flags(CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW.0);
    }
    #[cfg(unix)]
    {
        // Ребёнок — в своей группе процессов: отмена и таймаут бьют `killpg` по всей
        // группе (`zsh -lc "a | b"` — это несколько процессов), а не по одному zsh,
        // после которого сироты жили бы дальше. Замена job-объекту Windows.
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    let mut child = command.spawn().context("spawn supervised process")?;
    state.child_pid = Some(child.id());
    write_json(&dir.join("state.json"), &state)?;
    let job = JobGuard::attach(&child, operation_id)?;

    let started = Instant::now();
    let cancel_path = dir.join("cancel.requested");
    let (status, exit_code) = loop {
        if let Some(exit) = child.try_wait()? {
            break (
                if exit.success() {
                    OperationStatus::Succeeded
                } else {
                    OperationStatus::Failed
                },
                exit.code(),
            );
        }
        if cancel_path.exists() {
            job.terminate(0xC000_013A_u32 as i32)?;
            let exit = child.wait()?;
            break (OperationStatus::Cancelled, exit.code());
        }
        if args.timeout_s > 0 && started.elapsed() >= Duration::from_secs(args.timeout_s) {
            job.terminate(1460)?;
            let exit = child.wait()?;
            break (OperationStatus::TimedOut, exit.code());
        }
        thread::sleep(Duration::from_millis(100));
    };
    state.status = status;
    state.updated_at = Utc::now();
    state.finished_at = Some(Utc::now());
    write_json(&dir.join("state.json"), &state)?;
    write_json(
        &dir.join("result.json"),
        &OperationResult {
            operation_id: operation_id.into(),
            status,
            exit_code,
            duration_ms: started.elapsed().as_millis(),
            stdout_log: stdout_path,
            stderr_log: stderr_path,
            finished_at: Utc::now(),
        },
    )?;
    Ok(())
}

fn build_command(dir: &Path, args: &ProcessStartArgs) -> Result<Command> {
    match args.shell {
        ShellKind::Direct => {
            let program = args
                .program
                .as_deref()
                .context("direct process requires program")?;
            let mut command = Command::new(program);
            command.args(&args.args);
            Ok(command)
        }
        // macOS: PowerShell-просьба дерева исполняется zsh (см. `shell_plan`). Скрипт
        // уходит аргументом `-lc`, без BOM и без PowerShell-преамбулы UTF-8: zsh их не
        // поймёт, а кодировка вывода здесь и так UTF-8 — `LANG` подставляется, если под
        // launchd его нет вовсе (иначе `ls` с русскими именами печатает знаки вопроса).
        // `-l` — login shell: PATH из ~/.zprofile (Homebrew и прочее), как в Терминале.
        ShellKind::PowerShell if cfg!(target_os = "macos") => {
            let script = args
                .command
                .as_deref()
                .context("PowerShell (run as zsh on macOS) requires command")?;
            Ok(posix_shell("/bin/zsh", "-lc", script))
        }
        ShellKind::Sh => {
            let script = args.command.as_deref().context("sh requires command")?;
            if cfg!(windows) {
                anyhow::bail!("shell sh is not available on Windows");
            }
            Ok(posix_shell("/bin/sh", "-c", script))
        }
        ShellKind::Zsh => {
            let script = args.command.as_deref().context("zsh requires command")?;
            if cfg!(windows) {
                anyhow::bail!("shell zsh is not available on Windows");
            }
            Ok(posix_shell("/bin/zsh", "-lc", script))
        }
        ShellKind::Bash => {
            let script = args.command.as_deref().context("bash requires command")?;
            if cfg!(windows) {
                anyhow::bail!("shell bash is not available on Windows");
            }
            Ok(posix_shell("/bin/bash", "-lc", script))
        }
        ShellKind::Cmd | ShellKind::Wsl if cfg!(target_os = "macos") => {
            // Сюда не доходит: `start()` отказал раньше. Оставлено на случай, если запрос
            // положили в каталог операции мимо `start()`.
            anyhow::bail!("shell {} is not available on macOS", shell_name(args.shell))
        }
        ShellKind::PowerShell => {
            let script = args
                .command
                .as_deref()
                .context("PowerShell requires command")?;
            let path = dir.join("command.ps1");
            let mut raw = vec![0xEF, 0xBB, 0xBF];
            raw.extend_from_slice(POWERSHELL_UTF8_PREAMBLE.as_bytes());
            raw.extend_from_slice(script.as_bytes());
            fs::write(&path, raw)?;
            let mut command = Command::new("powershell.exe");
            command.args([
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
            ]);
            command.arg(path);
            Ok(command)
        }
        ShellKind::Cmd => {
            let script = args.command.as_deref().context("cmd requires command")?;
            let mut command = Command::new("cmd.exe");
            command.args(["/D", "/S", "/C", script]);
            Ok(command)
        }
        ShellKind::Wsl => {
            let script = args.command.as_deref().context("WSL requires command")?;
            let mut command = Command::new("wsl.exe");
            command.args(["--", "bash", "-lc", script]);
            Ok(command)
        }
    }
}

/// POSIX-оболочка со скриптом аргументом. `LANG` — только если его нет в окружении
/// (под launchd так и есть): выбор пользователя не перебивается, а без него вывод
/// с не-ASCII именами и текстом идёт не в UTF-8.
fn posix_shell(shell: &str, flag: &str, script: &str) -> Command {
    let mut command = Command::new(shell);
    command.args([flag, script]);
    if std::env::var_os("LANG").is_none() && std::env::var_os("LC_ALL").is_none() {
        command.env("LANG", "en_US.UTF-8");
    }
    command
}

/// Порог, после которого «стартует, но ребёнка так и нет» перестаёт быть стартом.
/// Живёт здесь одним именем, а не двумя литералами в двух функциях.
const STARTING_WITHOUT_CHILD_SEC: i64 = 15;

/// Фактическое состояние операции — с оглядкой на `result.json` и на жизнь супервизора.
///
/// ⚠ Раньше эта логика жила ВНУТРИ `status()`, а `list()` отдавал сырое `state.status`
/// и даже не открывал `result.json`. Две соседние функции одного файла отвечали на один
/// вопрос по-разному, и расхождение было невидимым: снаружи оба выглядят как «статус».
/// Ценой были незакрываемые задачи — сервер спрашивает список, видит «running» у операции,
/// которая завершилась ещё 13 июля, и отказывается закрывать задачу — та числится живой.
/// Теперь ответ один, потому что считает его одна функция.
fn effective_status(dir: &Path, state: &OperationState) -> (OperationStatus, Option<OperationResult>) {
    let result: Option<OperationResult> = fs::read(dir.join("result.json"))
        .ok()
        .and_then(|raw| serde_json::from_slice(&raw).ok());
    let mut status = result.as_ref().map(|x| x.status).unwrap_or(state.status);
    if status == OperationStatus::Starting
        && state.child_pid.is_none()
        && Utc::now()
            .signed_duration_since(state.updated_at)
            .num_seconds()
            > STARTING_WITHOUT_CHILD_SEC
    {
        status = OperationStatus::InDoubt;
    }
    if status == OperationStatus::Running
        && !process_instance_matches(state.supervisor_pid, state.started_at)
    {
        status = OperationStatus::InDoubt;
    }
    (status, result)
}

pub fn status(state_dir: &Path, operation_id: &str, tail: u64) -> Result<Value> {
    let dir = operation_dir(state_dir, operation_id)?;
    let state: OperationState =
        serde_json::from_slice(&fs::read(dir.join("state.json")).context("read operation state")?)?;
    let launcher: Option<Value> = fs::read(dir.join("launcher.json"))
        .ok()
        .and_then(|raw| serde_json::from_slice(&raw).ok());
    let (effective_status, result) = effective_status(&dir, &state);
    Ok(json!({
        "ok": true,
        "operation_id": operation_id,
        "status": effective_status,
        "identity": state.identity,
        "supervisor_pid": if state.supervisor_pid > 0 { Value::from(state.supervisor_pid) } else { launcher.as_ref().and_then(|x| x.get("supervisor_pid")).cloned().unwrap_or(Value::Null) },
        "child_pid": state.child_pid,
        "result": result,
        "stdout_tail": tail_file(&dir.join("stdout.log"), tail)?,
        "stderr_tail": tail_file(&dir.join("stderr.log"), tail)?,
        "operation_dir": dir,
    }))
}

#[cfg(windows)]
fn process_instance_matches(pid: u32, started_at: Option<DateTime<Utc>>) -> bool {
    use windows::Win32::Foundation::{CloseHandle, FILETIME};
    use windows::Win32::System::Threading::{
        GetProcessTimes, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };

    if pid == 0 {
        return false;
    }
    let Ok(handle) = (unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) }) else {
        return false;
    };
    let mut created = FILETIME::default();
    let mut exited = FILETIME::default();
    let mut kernel = FILETIME::default();
    let mut user = FILETIME::default();
    let read =
        unsafe { GetProcessTimes(handle, &mut created, &mut exited, &mut kernel, &mut user) };
    let _ = unsafe { CloseHandle(handle) };
    if read.is_err() {
        return false;
    }
    let Some(expected) = started_at else {
        return true;
    };
    filetime_datetime(created).is_some_and(|actual| {
        actual
            .signed_duration_since(expected)
            .num_seconds()
            .unsigned_abs()
            <= 60
    })
}

#[cfg(windows)]
fn filetime_datetime(value: windows::Win32::Foundation::FILETIME) -> Option<DateTime<Utc>> {
    const UNIX_EPOCH_FILETIME: u64 = 116_444_736_000_000_000;
    const TICKS_PER_SECOND: u64 = 10_000_000;
    let ticks = (u64::from(value.dwHighDateTime) << 32) | u64::from(value.dwLowDateTime);
    let unix = ticks.checked_sub(UNIX_EPOCH_FILETIME)?;
    DateTime::from_timestamp(
        i64::try_from(unix / TICKS_PER_SECOND).ok()?,
        u32::try_from((unix % TICKS_PER_SECOND) * 100).ok()?,
    )
}

#[cfg(not(windows))]
fn process_instance_matches(pid: u32, _started_at: Option<DateTime<Utc>>) -> bool {
    pid > 0 && unsafe { libc::kill(pid as i32, 0) } == 0
}

pub fn cancel(state_dir: &Path, operation_id: &str) -> Result<Value> {
    let dir = operation_dir(state_dir, operation_id)?;
    if dir.join("result.json").exists() {
        return status(state_dir, operation_id, 16_384);
    }
    fs::write(dir.join("cancel.requested"), Utc::now().to_rfc3339())?;
    Ok(json!({"ok": true, "operation_id": operation_id, "status": "cancelling"}))
}

pub fn list(state_dir: &Path, root_filter: Option<&str>) -> Result<Value> {
    let root = state_dir.join("operations");
    let mut rows = Vec::new();
    let root_filter = root_filter.map(|value| value.replace('/', "\\").to_ascii_lowercase());
    if root.is_dir() {
        for entry in fs::read_dir(root)? {
            let entry = entry?;
            if !entry.path().is_dir() {
                continue;
            }
            if let Ok(raw) = fs::read(entry.path().join("state.json"))
                && let Ok(state) = serde_json::from_slice::<OperationState>(&raw)
            {
                let request = fs::read(entry.path().join("request.json"))
                    .ok()
                    .and_then(|raw| serde_json::from_slice::<ProcessStartArgs>(&raw).ok());
                if let Some(filter) = &root_filter {
                    let matches = request
                        .as_ref()
                        .and_then(|value| value.cwd.as_ref())
                        .map(|cwd| {
                            cwd.to_string_lossy()
                                .replace('/', "\\")
                                .to_ascii_lowercase()
                                .starts_with(filter)
                        })
                        .unwrap_or(false);
                    if !matches {
                        continue;
                    }
                }
                // Тот же ответ, что у status(): сервер спрашивает список чаще, чем карточку,
                // и решает по нему, жива ли задача. Сырое state.status здесь означало, что
                // завершившаяся операция вечно числится running и задачу нельзя закрыть.
                let (live_status, _) = effective_status(&entry.path(), &state);
                rows.push(json!({
                    "operation_id": state.operation_id,
                    "status": live_status,
                    "state_status": state.status,
                    "supervisor_pid": state.supervisor_pid,
                    "child_pid": state.child_pid,
                    "identity": state.identity,
                    "created_at": state.created_at,
                    "updated_at": state.updated_at,
                    "name": request.as_ref().and_then(|value| value.name.clone()),
                    "command": request.as_ref().and_then(|value| value.command.clone()),
                    "program": request.as_ref().and_then(|value| value.program.clone()),
                    "cwd": request.as_ref().and_then(|value| value.cwd.clone()),
                }));
            }
        }
    }
    rows.sort_by_key(|row| {
        row.get("created_at")
            .and_then(Value::as_str)
            .map(str::to_string)
    });
    Ok(json!({"ok": true, "operations": rows}))
}

fn tail_file(path: &Path, limit: u64) -> Result<String> {
    let mut file = match File::open(path) {
        Ok(value) => value,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(String::new()),
        Err(error) => return Err(error.into()),
    };
    let size = file.metadata()?.len();
    let take = size.min(limit.clamp(1, 4 * 1024 * 1024));
    file.seek(SeekFrom::Start(size - take))?;
    let mut bytes = vec![0u8; take as usize];
    file.read_exact(&mut bytes)?;
    Ok(String::from_utf8_lossy(&bytes).to_string())
}

#[cfg(windows)]
struct JobGuard(windows::Win32::Foundation::HANDLE);

#[cfg(windows)]
impl JobGuard {
    fn attach(child: &std::process::Child, operation_id: &str) -> Result<Self> {
        use std::ffi::c_void;
        use std::os::windows::io::AsRawHandle;
        use windows::Win32::Foundation::HANDLE;
        use windows::Win32::System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
            SetInformationJobObject,
        };

        let job = unsafe { CreateJobObjectW(None, None)? };
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        unsafe {
            SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const c_void,
                std::mem::size_of_val(&limits) as u32,
            )?;
            let process = HANDLE(child.as_raw_handle());
            AssignProcessToJobObject(job, process)
                .with_context(|| format!("assign {operation_id} to Windows Job Object"))?;
        }
        Ok(Self(job))
    }

    fn terminate(&self, exit_code: i32) -> Result<()> {
        use windows::Win32::System::JobObjects::TerminateJobObject;
        unsafe { TerminateJobObject(self.0, exit_code as u32)? };
        Ok(())
    }
}

#[cfg(windows)]
impl Drop for JobGuard {
    fn drop(&mut self) {
        unsafe {
            let _ = windows::Win32::Foundation::CloseHandle(self.0);
        }
    }
}

#[cfg(not(windows))]
struct JobGuard {
    pid: u32,
}

#[cfg(not(windows))]
impl JobGuard {
    fn attach(child: &std::process::Child, _operation_id: &str) -> Result<Self> {
        Ok(Self { pid: child.id() })
    }

    /// Ребёнок посажен в свою группу (`process_group(0)` при спавне), поэтому сигнал
    /// идёт всей группе — и конвейеру `a | b`, и тому, что оболочка породила.
    /// TERM, полсекунды на уборку, потом KILL без права на отказ: сразу за нами стоит
    /// `child.wait()`, и процесс, который игнорирует TERM, держал бы супервизор вечно —
    /// «отменяется» без конца. Job-объект Windows тоже убивает, а не просит.
    fn terminate(&self, _exit_code: i32) -> Result<()> {
        let group = self.pid as libc::pid_t;
        if group <= 0 {
            anyhow::bail!("supervised child has no pid to terminate");
        }
        unsafe {
            libc::killpg(group, libc::SIGTERM);
        }
        thread::sleep(Duration::from_millis(500));
        unsafe {
            libc::killpg(group, libc::SIGKILL);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use uuid::Uuid;

    /// Дерево шлёт `shell: "power_shell"` всегда — имя обязано разбираться, и на macOS
    /// оно обязано означать zsh с заметкой, а не отказ и не тихую подмену.
    #[test]
    fn power_shell_is_the_wire_name_and_maps_to_zsh_on_macos() {
        assert_eq!(
            serde_json::from_str::<ShellKind>("\"power_shell\"").unwrap(),
            ShellKind::PowerShell
        );
        for (wire, kind) in [
            ("\"sh\"", ShellKind::Sh),
            ("\"zsh\"", ShellKind::Zsh),
            ("\"bash\"", ShellKind::Bash),
            ("\"direct\"", ShellKind::Direct),
        ] {
            assert_eq!(serde_json::from_str::<ShellKind>(wire).unwrap(), kind);
        }
        let plan = shell_plan(ShellKind::PowerShell).unwrap();
        if cfg!(target_os = "macos") {
            assert_eq!(plan.used, "zsh");
            assert!(plan.note.unwrap().contains("macOS ran"), "{plan:?}");
            assert!(shell_plan(ShellKind::Cmd).unwrap_err().to_string().contains("macOS"));
            assert!(shell_plan(ShellKind::Wsl).unwrap_err().to_string().contains("macOS"));
            assert_eq!(shell_plan(ShellKind::Zsh).unwrap().used, "zsh");
        } else {
            assert_eq!(plan.used, "powershell");
            assert!(plan.note.is_none());
        }
        if cfg!(windows) {
            assert!(shell_plan(ShellKind::Zsh).unwrap_err().to_string().contains("Windows"));
        }
    }

    /// macOS: zsh получает скрипт как есть — без BOM, без PowerShell-преамбулы, и
    /// с UTF-8 в окружении, если `LANG` не задан.
    #[cfg(target_os = "macos")]
    #[test]
    fn on_macos_power_shell_runs_zsh_without_bom_or_preamble() {
        let root = std::env::temp_dir().join(format!("praxis-process-zsh-{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        let args = ProcessStartArgs {
            program: None,
            args: Vec::new(),
            command: Some("echo 'Привет'".into()),
            shell: ShellKind::PowerShell,
            terminal: TerminalMode::Pipes,
            cwd: None,
            env: BTreeMap::new(),
            timeout_s: 0,
            name: None,
        };
        let mut command = build_command(&root, &args).unwrap();
        assert_eq!(command.get_program(), "/bin/zsh");
        let argv: Vec<String> = command
            .get_args()
            .map(|value| value.to_string_lossy().into_owned())
            .collect();
        assert_eq!(argv, vec!["-lc".to_string(), "echo 'Привет'".to_string()]);
        assert!(!root.join("command.ps1").exists(), "PowerShell-файл на macOS не пишется");
        let output = command.output().expect("zsh есть на любом macOS");
        assert_eq!(String::from_utf8(output.stdout).unwrap().trim(), "Привет");
        let _ = fs::remove_dir_all(root);
    }

    #[cfg(not(target_os = "macos"))]
    #[test]
    fn powershell_script_forces_utf8_process_logs() {
        let root = std::env::temp_dir().join(format!("praxis-process-{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        let args = ProcessStartArgs {
            program: None,
            args: Vec::new(),
            command: Some("Write-Output 'Привет'".into()),
            shell: ShellKind::PowerShell,
            terminal: TerminalMode::Pipes,
            cwd: None,
            env: BTreeMap::new(),
            timeout_s: 0,
            name: None,
        };
        let _command = build_command(&root, &args).unwrap();
        let raw = fs::read(root.join("command.ps1")).unwrap();
        assert_eq!(&raw[..3], &[0xEF, 0xBB, 0xBF]);
        let script = std::str::from_utf8(&raw[3..]).unwrap();
        assert!(script.starts_with(POWERSHELL_UTF8_PREAMBLE));
        assert!(script.ends_with("Write-Output 'Привет'"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn existing_operation_id_cannot_alias_different_process_arguments() {
        let root = std::env::temp_dir().join(format!("praxis-process-alias-{}", Uuid::new_v4()));
        let operation_id = "op-stable";
        let dir = operation_dir(&root, operation_id).unwrap();
        fs::create_dir_all(&dir).unwrap();
        let first = ProcessStartArgs {
            program: Some("first.exe".into()),
            args: vec!["one".into()],
            command: None,
            shell: ShellKind::Direct,
            terminal: TerminalMode::Pipes,
            cwd: None,
            env: BTreeMap::new(),
            timeout_s: 0,
            name: None,
        };
        write_json(&dir.join("request.json"), &first).unwrap();
        let mut changed = first.clone();
        changed.program = Some("second.exe".into());

        let error = replay_existing_start(&root, operation_id, &dir, &changed).unwrap_err();
        assert!(error.to_string().contains("different process arguments"));
        let _ = fs::remove_dir_all(root);
    }

    /// Список и карточка обязаны отвечать на «жива ли операция» ОДИНАКОВО.
    ///
    /// До 28.07 они расходились: `status()` считал фактическое состояние (читал
    /// `result.json`, ловил «стартует без ребёнка» и мёртвого супервизора), а `list()`
    /// отдавал сырое `state.status` и `result.json` даже не открывал. Снаружи оба поля
    /// называются «status», поэтому расхождение было невидимым — а сервер решает по
    /// СПИСКУ, жива ли задача. Из-за этого на проде две завершённые 13-14 июля операции
    /// вечно числились живыми, и задачу нельзя было закрыть штатно.
    #[test]
    fn list_and_status_agree_on_a_finished_operation() {
        let root = std::env::temp_dir().join(format!("praxis-process-agree-{}", Uuid::new_v4()));
        let operation_id = "op-finished";
        let dir = operation_dir(&root, operation_id).unwrap();
        fs::create_dir_all(&dir).unwrap();

        // Состояние осталось «running» — так и бывает, когда супервизор умер, не переписав его.
        let state = OperationState {
            operation_id: operation_id.to_string(),
            status: OperationStatus::Running,
            supervisor_pid: 0,
            child_pid: None,
            identity: identity::current(),
            created_at: Utc::now(),
            updated_at: Utc::now(),
            started_at: None,
            finished_at: None,
        };
        write_json(&dir.join("state.json"), &state).unwrap();
        write_json(&dir.join("request.json"), &ProcessStartArgs {
            program: Some("done.exe".into()),
            args: vec![],
            command: None,
            shell: ShellKind::Direct,
            terminal: TerminalMode::Pipes,
            cwd: None,
            env: BTreeMap::new(),
            timeout_s: 0,
            name: None,
        }).unwrap();

        let card = status(&root, operation_id, 0).unwrap();
        let listing = list(&root, None).unwrap();
        let row = listing["operations"].as_array()
            .or_else(|| listing.as_array())
            .and_then(|rows| rows.iter().find(|r| r["operation_id"] == operation_id))
            .expect("операция обязана быть в списке");

        assert_eq!(
            row["status"], card["status"],
            "список и карточка разошлись: список {}, карточка {}", row["status"], card["status"]
        );
        assert_eq!(row["status"], serde_json::json!("in_doubt"),
                   "супервизора нет — «running» здесь неправда");
        // Сырое поле не выбрасываем: она вправе видеть, что записано на диске, и чем это
        // отличается от вывода. Иначе «почему у меня in_doubt» станет вопросом без ответа.
        assert_eq!(row["state_status"], serde_json::json!("running"));

        let _ = fs::remove_dir_all(root);
    }


    /// Ранний отказ супервизора обязан оставить ПРИЧИНУ, а не «не знаю».
    ///
    /// Здесь запрос заведомо несобираемый (shell=Direct без program), то есть build_command
    /// падает ДО спавна ребёнка. Раньше на этом супервизор просто умирал, result.json не
    /// появлялся, и через 15 секунд операция становилась in_doubt — «неизвестно» вместо
    /// «не собралась команда: direct process requires program».
    #[test]
    fn an_early_supervisor_failure_is_a_recorded_outcome_not_a_shrug() {
        let root = std::env::temp_dir().join(format!("praxis-process-early-{}", Uuid::new_v4()));
        let operation_id = "op-early-fail";
        let dir = operation_dir(&root, operation_id).unwrap();
        fs::create_dir_all(&dir).unwrap();
        write_json(&dir.join("request.json"), &ProcessStartArgs {
            program: None,               // ← direct без program: команда не соберётся
            args: vec![],
            command: None,
            shell: ShellKind::Direct,
            terminal: TerminalMode::Pipes,
            cwd: None,
            env: BTreeMap::new(),
            timeout_s: 0,
            name: None,
        }).unwrap();
        write_json(&dir.join("state.json"), &OperationState {
            operation_id: operation_id.to_string(),
            status: OperationStatus::Starting,
            supervisor_pid: 0,
            child_pid: None,
            identity: identity::current(),
            created_at: Utc::now(),
            updated_at: Utc::now(),
            started_at: None,
            finished_at: None,
        }).unwrap();

        let outcome = supervise(&root, operation_id);
        assert!(outcome.is_err(), "команда не собирается — отказ обязан быть");

        let card = status(&root, operation_id, 0).unwrap();
        assert_eq!(card["status"], serde_json::json!("failed"),
                   "это не «не знаю», это известный провал");
        let reason = fs::read_to_string(dir.join("supervisor_error.txt")).unwrap();
        assert!(reason.contains("program"), "причина обязана дожить до неё, а не умереть с процессом: {reason}");

        let listing = list(&root, None).unwrap();
        let rows = listing["operations"].as_array().or_else(|| listing.as_array()).unwrap();
        let row = rows.iter().find(|r| r["operation_id"] == operation_id).unwrap();
        assert_eq!(row["status"], card["status"], "список и карточка снова обязаны сойтись");

        let _ = fs::remove_dir_all(root);
    }
}
