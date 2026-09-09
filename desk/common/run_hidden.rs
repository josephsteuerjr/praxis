// Внешняя программа без окна и С ДЕДЛАЙНОМ — один код на оболочку и установщик.
//
// Голый `cmd.output()` ждёт вечно: повисший netsh, sc или tailscaled вешал
// вызвавшую команду навсегда, а вместе с ней (у синхронных команд Tauri) и всё
// окно. Установщик до 07.09 спрашивал SCM без дедлайна (ревью 06.09, §4
// п. 13). Файл включается через `include!`; ему нужны `CREATE_NO_WINDOW` и
// (на Windows) `CommandExt` из включающего файла.

fn run_hidden_for(cmd: &mut std::process::Command, limit: std::time::Duration) -> Result<std::process::Output, String> {
    use std::io::Read;
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd.stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    let mut child = cmd.spawn().map_err(|e| e.to_string())?;
    let out_pipe = child.stdout.take();
    let err_pipe = child.stderr.take();
    // Трубы читаем в отдельных потоках: иначе полный буфер вывода
    // заблокировал бы ребёнка, и дедлайн ловил бы собственный тупик.
    let out_thread = std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(mut p) = out_pipe {
            let _ = p.read_to_end(&mut buf);
        }
        buf
    });
    let err_thread = std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(mut p) = err_pipe {
            let _ = p.read_to_end(&mut buf);
        }
        buf
    });
    let deadline = std::time::Instant::now() + limit;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let stdout = out_thread.join().unwrap_or_default();
                let stderr = err_thread.join().unwrap_or_default();
                return Ok(std::process::Output { status, stdout, stderr });
            }
            Ok(None) => {}
            Err(err) => return Err(err.to_string()),
        }
        if std::time::Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("не ответил за {} с", limit.as_secs()));
        }
        std::thread::sleep(std::time::Duration::from_millis(80));
    }
}

#[cfg(test)]
mod run_hidden_tests {
    use super::*;

    /// Дедлайн действительно срабатывает, а не ждёт конца процесса.
    #[test]
    #[cfg(windows)]
    fn deadline_kills_a_sleeper() {
        let mut cmd = std::process::Command::new("cmd.exe");
        cmd.args(["/C", "ping 127.0.0.1 -n 6 >nul"]);
        let started = std::time::Instant::now();
        let err = run_hidden_for(&mut cmd, std::time::Duration::from_millis(600)).unwrap_err();
        assert!(err.contains("не ответил"), "{err}");
        assert!(started.elapsed() < std::time::Duration::from_secs(4));
    }

    #[test]
    #[cfg(windows)]
    fn output_is_collected() {
        let mut cmd = std::process::Command::new("cmd.exe");
        cmd.args(["/C", "echo hi"]);
        let out = run_hidden_for(&mut cmd, std::time::Duration::from_secs(10)).unwrap();
        assert!(out.status.success());
        assert!(String::from_utf8_lossy(&out.stdout).contains("hi"));
    }
}
