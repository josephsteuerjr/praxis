// The dispatcher calls the shared desktop command implementations.
fn argument<T: serde::de::DeserializeOwned>(args: &serde_json::Value, snake: &str, camel: &str) -> Result<T, String> {
    serde_json::from_value(args.get(camel).or_else(|| args.get(snake)).cloned().unwrap_or(serde_json::Value::Null))
        .map_err(|e| format!("invalid argument {camel}: {e}"))
}

async fn host_command(app: &ShellHandle, name: &str, args: &serde_json::Value) -> Result<serde_json::Value, String> {
    match name {
        "local_files_list" => local_files_list(argument::<String>(args, "path", "path")?).await,
        "local_files_read" => local_files_read(argument::<String>(args, "path", "path")?).await,
        "local_files_save" => local_files_save(argument::<String>(args, "folder", "folder")?, argument::<String>(args, "name", "name")?, argument::<String>(args, "data", "data")?, argument::<bool>(args, "overwrite", "overwrite")?).await,
        "owner_control" => serde_json::to_value(owner_control(argument::<String>(args, "action", "action")?)?).map_err(|e| e.to_string()),
        "owner_state" => serde_json::to_value(owner_state()).map_err(|e| e.to_string()),
        "engine_restart" => serde_json::to_value(engine_restart()?).map_err(|e| e.to_string()),
        "config_save" => serde_json::to_value(config_save(app.clone(), argument::<String>(args, "config", "config")?, argument::<Option<String>>(args, "mtime_ns", "mtimeNs")?)?).map_err(|e| e.to_string()),
        "restart_self" => serde_json::to_value(restart_self(app.clone())).map_err(|e| e.to_string()),
        "install_service" => serde_json::to_value(install_service().await?).map_err(|e| e.to_string()),
        "remove_service" => serde_json::to_value(remove_service().await?).map_err(|e| e.to_string()),
        "service_state" => serde_json::to_value(service_state().await).map_err(|e| e.to_string()),
        "config_load" => serde_json::to_value(config_load()?).map_err(|e| e.to_string()),
        "open_path" => serde_json::to_value(open_path(argument::<String>(args, "path", "path")?)?).map_err(|e| e.to_string()),
        "autostart_get" => serde_json::to_value(autostart_get()).map_err(|e| e.to_string()),
        "autostart_set" => serde_json::to_value(autostart_set(argument::<bool>(args, "on", "on")?).await?).map_err(|e| e.to_string()),
        "probe_model" => serde_json::to_value(probe_model(argument::<String>(args, "base_url", "baseUrl")?, argument::<String>(args, "key", "key")?, argument::<Option<String>>(args, "framework", "framework")?).await).map_err(|e| e.to_string()),
        "lan_ip" => serde_json::to_value(lan_ip()).map_err(|e| e.to_string()),
        "tailscale_ip" => serde_json::to_value(tailscale_ip().await).map_err(|e| e.to_string()),
        "firewall_allow" => serde_json::to_value(firewall_allow(argument::<u16>(args, "port", "port")?).await?).map_err(|e| e.to_string()),
        "firewall_clear" => serde_json::to_value(firewall_clear(argument::<u16>(args, "port", "port")?).await?).map_err(|e| e.to_string()),
        "admin_state" => serde_json::to_value(admin_state()).map_err(|e| e.to_string()),
        "relay_login" => serde_json::to_value(relay_login().await?).map_err(|e| e.to_string()),
        "relay_login_url" => serde_json::to_value(relay_login_url()).map_err(|e| e.to_string()),
        "open_login_page" => serde_json::to_value(open_login_page()?).map_err(|e| e.to_string()),
        "relay_status" => serde_json::to_value(relay_status(app.clone())).map_err(|e| e.to_string()),
        "relay_account" => serde_json::to_value(relay_account().await?).map_err(|e| e.to_string()),
        "relay_auth_transfer" => relay_auth_transfer(argument::<bool>(args, "replace", "replace")?).await,
        "notify" => serde_json::to_value(notify(argument::<String>(args, "title", "title")?, argument::<String>(args, "body", "body")?)).map_err(|e| e.to_string()),
        "app_info" => serde_json::to_value(app_info()).map_err(|e| e.to_string()),
        "update_check" => serde_json::to_value(update_check(argument::<String>(args, "url", "url")?).await?).map_err(|e| e.to_string()),
        "update_download" => serde_json::to_value(update_download(argument::<String>(args, "url", "url")?, argument::<Option<String>>(args, "sha256", "sha256")?).await?).map_err(|e| e.to_string()),
        "update_install" => serde_json::to_value(update_install(app.clone(), argument::<String>(args, "path", "path")?, argument::<Option<bool>>(args, "force_extensions", "forceExtensions")?).await?).map_err(|e| e.to_string()),
        "backup_now" => serde_json::to_value(backup_now().await?).map_err(|e| e.to_string()),
        "backup_list" => serde_json::to_value(backup_list()).map_err(|e| e.to_string()),
        "logs_bundle" => serde_json::to_value(logs_bundle().await?).map_err(|e| e.to_string()),
        "reveal_path" => serde_json::to_value(reveal_path(argument::<String>(args, "path", "path")?)?).map_err(|e| e.to_string()),
        "open_privacy_pane" => serde_json::to_value(open_privacy_pane(argument::<String>(args, "kind", "kind")?)?).map_err(|e| e.to_string()),
        "telegram_account" => serde_json::to_value(telegram_account(argument::<String>(args, "step", "step")?, argument::<String>(args, "api_id", "apiId")?, argument::<String>(args, "api_hash", "apiHash")?, argument::<String>(args, "phone", "phone")?, argument::<String>(args, "code", "code")?, argument::<String>(args, "password", "password")?).await?).map_err(|e| e.to_string()),
        "voice_fetch" => serde_json::to_value(voice_fetch(argument::<String>(args, "model", "model")?, argument::<Option<String>>(args, "kind", "kind")?)?).map_err(|e| e.to_string()),
        "carry_export" => serde_json::to_value(carry_export().await?).map_err(|e| e.to_string()),
        "agents_list" => serde_json::to_value(agents_list(app.state::<LocalHarness>())).map_err(|e| e.to_string()),
        "switch_agent" => serde_json::to_value(switch_agent(app.clone(), argument::<String>(args, "id", "id")?)?).map_err(|e| e.to_string()),
        "agent_add" => serde_json::to_value(agent_add(app.clone(), argument::<String>(args, "name", "name")?, argument::<Option<serde_json::Value>>(args, "soul", "soul")?, argument::<Option<String>>(args, "soul_kind", "soulKind")?, argument::<Option<String>>(args, "soul_text", "soulText")?, argument::<Option<String>>(args, "soul_from", "soulFrom")?).await?).map_err(|e| e.to_string()),
        "agent_default_set" => serde_json::to_value(agent_default_set(argument::<String>(args, "id", "id")?)?).map_err(|e| e.to_string()),
        "agent_enabled_set" => serde_json::to_value(agent_enabled_set(app.clone(), argument::<String>(args, "id", "id")?, argument::<bool>(args, "enabled", "enabled")?).await?).map_err(|e| e.to_string()),
        "agent_remove" => serde_json::to_value(agent_remove(app.clone(), argument::<String>(args, "id", "id")?).await?).map_err(|e| e.to_string()),
        _ => Err(format!("unknown host command: {name}")),
    }
}

#[cfg(unix)]
static HOST_EXIT: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
#[cfg(unix)]
extern "C" fn host_signal(_: libc::c_int) {
    HOST_EXIT.store(true, std::sync::atomic::Ordering::Relaxed);
}

pub fn run_host() {
    use std::io::BufRead;
    let Some(raw) = arg_after("--root") else { eprintln!("helene-host requires --root"); std::process::exit(2) };
    let root = PathBuf::from(raw);
    if !root.is_absolute() || !root.is_dir() { eprintln!("host root must be an existing absolute directory"); std::process::exit(2) }
    #[cfg(unix)]
    let parent = unsafe { libc::getppid() };
    #[cfg(unix)]
    unsafe {
        libc::signal(libc::SIGTERM, host_signal as *const () as libc::sighandler_t);
        libc::signal(libc::SIGINT, host_signal as *const () as libc::sighandler_t);
    }
    let Some(boot) = bootstrap() else { return };
    let app = ShellHandle::new(LocalHarness {
        children: Mutex::new(boot.children), plans: Mutex::new(boot.plans),
        stopping: std::sync::atomic::AtomicBool::new(false),
    });
    #[cfg(target_os = "linux")]
    if service_owns_harness() {
        if let Some(tree) = boot.tree.clone() {
            let body_app = app.clone(); let config = current_config_path();
            std::thread::spawn(move || watch_service_body(body_app, tree, config));
        }
    }
    // A crashed/killed Electron parent must not leave its engine/relay behind.
    #[cfg(unix)]
    {
        // Keep the original parent across bootstrap. Signals only set a flag;
        // cleanup runs on a normal Rust thread after startup owns its children.
        let guardian = app.clone();
        std::thread::spawn(move || loop {
            std::thread::sleep(Duration::from_secs(1));
            if unsafe { libc::getppid() } != parent || HOST_EXIT.load(std::sync::atomic::Ordering::Relaxed) {
                kill_children(&guardian.state::<LocalHarness>()); relay_abort(); std::process::exit(0);
            }
        });
    }
    let worker = app.clone();
    std::thread::spawn(move || watch_children(worker));
    std::thread::spawn(update_autocheck);
    std::thread::spawn(backup_ticker);
    if boot.tree.is_some() {
        for agent in raisable(&boot.base) {
            watch_outbound(app.clone(), agent.tree.clone(), agent.name.clone(), boot.notify_text);
            watch_broker_wishes(agent.tree.clone());
        }
    }
    shell_adapter::event("ready", serde_json::json!({"script": boot.init_script, "version": env!("CARGO_PKG_VERSION")}));
    let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build().expect("host async runtime");
    let mut input = std::io::stdin().lock();
    loop {
        let mut line = String::new();
        // Bound frames before allocating arbitrary input from the renderer.
        const FILE_FRAME: u64 = (64 * 1024 * 1024_u64).div_ceil(3) * 4 + 65536;
        match (&mut input).take(FILE_FRAME + 1).read_line(&mut line) {
            Ok(0) | Err(_) => break,
            Ok(_) if line.len() as u64 > FILE_FRAME || !line.ends_with('\n') => break,
            _ => {}
        }
        let Ok(request) = serde_json::from_str::<serde_json::Value>(&line) else { continue };
        let Some(id) = request.get("id").and_then(|v| v.as_u64()) else { continue };
        let name = request.get("command").and_then(|v| v.as_str()).unwrap_or("");
        if name != "local_files_save" && line.len() > 16 * 1024 * 1024 {
            shell_adapter::send(&serde_json::json!({"id":id,"error":"Слишком большой запрос к оболочке"}));
            continue;
        }
        let args = request.get("args").cloned().unwrap_or_else(|| serde_json::json!({}));
        if name == "host_visibility" {
            app.visibility(args["visible"].as_bool().unwrap_or(false), args["focused"].as_bool().unwrap_or(false));
            shell_adapter::send(&serde_json::json!({"id":id,"result":null}));
            continue;
        }
        if name == "host_shutdown" {
            shell_adapter::send(&serde_json::json!({"id":id,"result":null}));
            break;
        }
        let command = name.to_string(); let handle = app.clone();
        runtime.spawn(async move {
            let result = host_command(&handle, &command, &args).await;
            shell_adapter::send(&match result {
                Ok(value) => serde_json::json!({"id":id,"result":value}),
                Err(error) => serde_json::json!({"id":id,"error":error}),
            });
        });
    }
    kill_children(&app.state::<LocalHarness>());
    relay_abort();
    runtime.shutdown_background();
}
