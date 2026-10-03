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
    let out = run_hidden_for(&mut cmd, Duration::from_secs(120))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
    }
    let after = linux_svc_state(&user);
    if (op == "install" && after != "running") || (op == "remove" && after != "absent") {
        return Err(format!("Операция службы завершилась, состояние: {after}. Успех не подтверждён."));
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
