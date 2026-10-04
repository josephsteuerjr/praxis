// Linux window integration. The engine, config and service remain shared code.
fn linux_owner_name() -> String {
    Command::new("id").arg("-un").output().ok().filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).unwrap_or_default()
}
fn linux_service_op(op: &str) -> Result<String, String> {
    let user = linux_owner_name();
    linux_svc_owner_ok(&user, Some(unsafe { libc::geteuid() }))?;
    let svc = install_root().join("helene-svc");
    let mut cmd = Command::new(&svc);
    cmd.args(["service", op]);
    let out = run_hidden_for(&mut cmd, Duration::from_secs(LINUX_SVC_DEADLINE_SEC + 20))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
    }
    let after = linux_svc_state(&user);
    if (op == "install" && after != "running") || (op == "remove" && after != "absent") {
        return Err(format!("Операция службы завершилась, состояние: {after}. Успех не подтверждён."));
    }
    if op == "start" {
        return if after == "running" { Ok("Служба запущена; жду связи с движком".into()) }
            else { Err(format!("Запуск службы не подтверждён: {after}")) };
    }
    let path = install_root().join(CONFIG_NAME);
    let cfg = match read_config(&path) { ConfigRead::Ok(mut v) => {
        if !v["installed"].is_object() { v["installed"] = serde_json::json!({}); }
        v["installed"]["service"] = serde_json::Value::Bool(op == "install"); v
    }, _ => return Err("Служба изменена, но helene.json не читается; состояние записать не удалось.".into()) };
    let _ = config_save_at(&path, &cfg.to_string(), None)?;
    Ok(if op == "install" { "Служба работает. Перезапусти окно, чтобы передать ей движок." }
       else { "Служба снята. Перезапусти окно, чтобы запускать движок вместе с ним." }.into())
}

// /proc is read only. Zombies and a PID reused after the reader receipt are not the runner.
fn linux_owner_pid_alive(pid: u32, observed_at: Option<f64>) -> Option<bool> {
    let stat = match std::fs::read_to_string(format!("/proc/{pid}/stat")) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Some(false),
        Err(_) => return None,
    };
    let fields: Vec<&str> = stat.rsplit_once(')')?.1.split_whitespace().collect();
    if matches!(fields.first().copied(), Some("Z" | "X")) { return Some(false); }
    let ticks = fields.get(19)?.parse::<f64>().ok()?;
    let hz = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
    if hz <= 0 { return None; }
    let boot = std::fs::read_to_string("/proc/stat").ok()?.lines()
        .find_map(|line| line.strip_prefix("btime ").and_then(|v| v.parse::<f64>().ok()))?;
    let seen = observed_at.filter(|v| v.is_finite() && *v > 0.)?;
    Some(boot + ticks / hz as f64 <= seen + 1.)
}

fn linux_broker_run(prepared: &MacPrepared) -> BrokerRun {
    let wish = &prepared.wish;
    let started = Instant::now();
    let reply = if wish.op == BrokerOp::Ping {
        serde_json::json!({"ok": true, "note": "интерактивный брокер — окно; exec требует пароль администратора"})
    } else {
        let argv: Vec<&str> = std::iter::once(wish.cmd.as_str()).chain(wish.args.iter().map(String::as_str)).collect();
        let request = serde_json::json!({"op": "exec", "argv": argv, "why": wish.why,
            "timeout_sec": wish.timeout_sec, "digest": prepared.digest});
        // Match the installed polkit exec.path, also when the owner home is symlinked.
        let svc = match install_root().join("helene-svc").canonicalize() {
            Ok(path) => path,
            Err(e) => return BrokerRun::Failed(format!("Нет программы брокера: {e}")),
        };
        let args = vec![svc.to_string_lossy().into_owned(),
            "broker-root".into(), request.to_string()];
        match linux_svc_run_admin_for(&args, LINUX_SVC_DEADLINE_SEC + wish.timeout_sec) {
            Ok(out) => match out.lines().rev().find_map(|line| serde_json::from_str::<serde_json::Value>(line).ok()) {
                Some(reply) => reply,
                None => return BrokerRun::Failed("квитанция брокера не разобралась".into()),
            },
            Err(e) => return BrokerRun::Refused(e),
        }
    };
    BrokerRun::Done(BrokerReceipt {
        id: wish.id.clone(), op: wish.op.as_str().into(), ok: reply["ok"].as_bool().unwrap_or(false),
        code: reply["code"].as_i64().map(|n| n as i32), pid: None,
        out: broker_cut(reply["out"].as_str().unwrap_or("")),
        err: broker_cut(reply["err"].as_str().unwrap_or("")),
        note: reply["error"].as_str().or_else(|| reply["note"].as_str()).unwrap_or("").into(),
        ms: started.elapsed().as_millis() as u64, at: local_stamp(), why: wish.why.clone(),
    })
}
fn linux_autostart_path() -> Option<PathBuf> {
    let base = std::env::var_os("XDG_CONFIG_HOME").map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))?;
    Some(base.join("autostart").join("helene.desktop"))
}
fn linux_launcher() -> PathBuf {
    // The packaged .desktop launches the Electron window, never the headless host.
    std::env::var_os("HELENE_LAUNCHER").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/usr/bin/helene"))
}
fn linux_autostart_set(on: bool) -> Result<(), String> {
    let path = linux_autostart_path().ok_or("Не найден дом владельца")?;
    if !on { return match std::fs::remove_file(path) {
        Ok(()) => Ok(()), Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e.to_string()),
    }; }
    let launcher = linux_launcher();
    if !launcher.is_file() { return Err(format!("Нет программы запуска: {}", launcher.display())); }
    let raw = launcher.to_string_lossy();
    if raw.contains(['\n', '\r', '\0']) { return Err("Недопустимый путь программы запуска".into()); }
    let quoted = raw.replace('\\', "\\\\").replace('"', "\\\"").replace('`', "\\`").replace('$', "\\$").replace('%', "%%");
    std::fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    std::fs::write(path, format!("[Desktop Entry]\nType=Application\nName=Hélène\nExec=\"{quoted}\"\nTerminal=false\n")).map_err(|e| e.to_string())
}
