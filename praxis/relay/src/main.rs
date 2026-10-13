use axum::{
    extract::{rejection::JsonRejection, DefaultBodyLimit, State},
    http::{HeaderMap, StatusCode},
    response::{sse::Event, IntoResponse, Json, Response},
    routing::{get, post},
    Router,
};
use std::net::SocketAddr;
use std::sync::Arc;
use tokio_stream::StreamExt;
use tower_http::cors::CorsLayer;
use tracing::{error, info, warn};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

// Modules
mod core;
mod login;
#[cfg(target_family = "windows")]
mod tray;

use core::account_router::AccountRouter;
use core::catalog::ModelCatalog;
use core::chat_completions;
use core::config::Config;
use core::limits::LimitsCache;
use core::models::{ChatRequest, ModelList, MAX_CHAT_REQUEST_BYTES};
use login::lib::CodexAuth;

// For CLI menu
use std::io::{self, IsTerminal, Write};

#[derive(Clone)]
struct AppState {
    config: Arc<Config>,
    accounts: AccountRouter,
    limits: LimitsCache,
    // What the backend serves today, refreshed on a TTL; /v1/models and the
    // request validator both read it (core::catalog).
    catalog: ModelCatalog,
    // One pooled client for every upstream call: Client::new() per request cost a
    // fresh DNS+TCP+TLS handshake to chatgpt.com on every single LLM call.
    client: reqwest::Client,
}

#[tokio::main]
async fn main() {
    // Declare per-monitor DPI awareness before any window exists: without it
    // Windows bitmap-scales the process UI on high-DPI displays and the tray
    // context menu renders blurry.  The console window itself belongs to
    // conhost and manages its own DPI either way.
    #[cfg(target_family = "windows")]
    unsafe {
        windows_sys::Win32::UI::HiDpi::SetProcessDpiAwarenessContext(
            windows_sys::Win32::UI::HiDpi::DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
        );
    }
    // The container runs from /app, whose logs directory is the persisted
    // compose mount. RELAY_LOG_DIR keeps non-container deployments explicit.
    let logs_dir = std::env::var_os("RELAY_LOG_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| {
            std::env::current_dir()
                .unwrap_or_else(|_| std::path::PathBuf::from("."))
                .join("logs")
        });
    if let Err(e) = std::fs::create_dir_all(&logs_dir) {
        eprintln!("Failed to create logs directory: {}", e);
    }
    // Initialize tracing with both console and file output
    let file_appender = tracing_appender::rolling::daily(logs_dir.clone(), "relay.log");
    let (non_blocking, _guard) = tracing_appender::non_blocking(file_appender);

    tracing_subscriber::registry()
        .with(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "codex_proxy=info,tower_http=info".into()),
        )
        .with(
            tracing_subscriber::fmt::layer()
                .with_writer(std::io::stdout)
                .with_ansi(true)
                .with_target(false)
                .with_thread_ids(false)
                .with_thread_names(false),
        )
        .with(
            tracing_subscriber::fmt::layer()
                .with_writer(non_blocking)
                .with_ansi(false)
                .with_target(true)
                .with_thread_ids(true)
                .with_thread_names(true),
        )
        .init();

    info!("=== Starting Codex Proxy Server ==="); // Codex Proxy Server
    info!("Log directory: {}", logs_dir.display());
    info!("Timestamp: {}", chrono::Utc::now().to_rfc3339());

    // Запуск без человека. До 10.09 argv игнорировался целиком: оболочка
    // Hélène зовёт `helene-relay serve`, реле печатало меню, читало из
    // закрытого stdin конец файла, отвечало «Invalid choice» — и крутилось так,
    // не заняв порта ни разу. Живая проба 10.09: 70 секунд процессора за
    // минуту, `/v1/models` молчит; на машине владельца это было незаметно,
    // потому что порт 5011 держало реле прежнего поколения. Контейнер её прода
    // обходил ту же дыру снаружи (`echo 1 | relay` в Dockerfile).
    //
    // Правило простое: назван аргумент — делаем названное и выходим; аргумента
    // нет и stdin не терминал (служба, контейнер, ребёнок оболочки) — поднимаем
    // сервер; человеку за терминалом остаётся прежнее меню.
    let action = std::env::args().nth(1).unwrap_or_default();
    match action.trim() {
        "serve" | "server" | "run" => {
            if let Err(e) = run_server().await {
                error!("Failed to start server: {}", e);
                std::process::exit(1);
            }
            return;
        }
        "login" => {
            if let Err(e) = run_login().await {
                error!("Login failed: {}", e);
                std::process::exit(1);
            }
            return;
        }
        "refresh" => {
            if let Err(e) = refresh_token().await {
                error!("Token refresh failed: {}", e);
                std::process::exit(1);
            }
            return;
        }
        "" => {
            if !io::stdin().is_terminal() {
                info!("stdin не терминал — поднимаю сервер без меню");
                if let Err(e) = run_server().await {
                    error!("Failed to start server: {}", e);
                    std::process::exit(1);
                }
                return;
            }
        }
        other => {
            eprintln!("Unknown argument: {other}");
            eprintln!("Usage: relay [serve|login|refresh]  (no argument shows the menu)");
            std::process::exit(2);
        }
    }

    // Display CLI menu
    loop {
        display_menu();
        let choice = get_user_choice();

        match choice.as_str() {
            "1" => {
                if let Err(e) = run_server().await {
                    error!("Failed to start server: {}", e);
                }
            }
            "2" => {
                // Close all servers functionality
                if let Err(e) = close_all_servers().await {
                    error!("Failed to close servers: {}", e);
                }
            }
            "3" => {
                if let Err(e) = run_login().await {
                    error!("Login failed: {}", e);
                }
            }
            "4" => {
                if let Err(e) = refresh_token().await {
                    error!("Token refresh failed: {}", e);
                }
            }
            "5" => {
                println!("Exiting...");
                break;
            }
            "6" => {
                if let Err(e) = list_running_servers().await {
                    error!("Failed to list running servers: {}", e);
                }
            }
            _ => {
                println!("Invalid choice. Please try again.");
            }
        }
    }
}

async fn run_login() -> anyhow::Result<()> {
    info!("Starting login process");
    // RELAY_LOCAL=1: the host (Hélène) owns these credentials — they go to
    // ./local_auth next to the working directory, never to ~/.codex. The host
    // polls exactly that file; writing anywhere else looks to it like a login
    // that never completed.
    if login::lib::relay_local() {
        let local_auth_dir = std::env::current_dir()?.join("local_auth");
        std::fs::create_dir_all(&local_auth_dir)?;
        let local_auth_path = local_auth_dir.join("auth.json");
        println!("RELAY_LOCAL is set: auth file path: {:?}", local_auth_path);
        login::lib::login_with_chatgpt(&local_auth_dir, false).await?;
        if !local_auth_path.exists() {
            return Err(anyhow::anyhow!(
                "Login failed: auth.json was not created in {:?}",
                local_auth_dir
            ));
        }
        println!("Auth file created successfully at: {:?}", local_auth_path);
        return Ok(());
    }
    let home_dir =
        dirs::home_dir().ok_or_else(|| anyhow::anyhow!("Could not determine home directory"))?;
    let codex_home = home_dir.join(".codex");
    let opencode_home = home_dir.join(".opencode");
    let codex_auth_path = codex_home.join("auth.json");
    let opencode_auth_path = opencode_home.join("auth.json");

    // Try to read or create in .codex first
    std::fs::create_dir_all(&codex_home)?;
    println!("Codex home directory: {:?}", codex_home);
    println!("Expected auth file path: {:?}", codex_auth_path);

    let mut used_opencode = false;
    let login_result = login::lib::login_with_chatgpt(&codex_home, false).await;
    if login_result.is_err() || !codex_auth_path.exists() {
        // If failed or file not created, try .opencode
        println!("Could not create or find auth.json in .codex, switching to .opencode directory (Opencode integration)...");
        std::fs::create_dir_all(&opencode_home)?;
        let login_result2 = login::lib::login_with_chatgpt(&opencode_home, false).await;
        if login_result2.is_err() || !opencode_auth_path.exists() {
            // Third fallback: create ./local_auth directory in current working directory
            let local_auth_dir = std::env::current_dir()?.join("local_auth");
            std::fs::create_dir_all(&local_auth_dir)?;
            let local_auth_path = local_auth_dir.join("auth.json");
            println!("Could not create or find auth.json in .codex or .opencode, switching to ./local_auth directory (local fallback)...");
            let login_result3 = login::lib::login_with_chatgpt(&local_auth_dir, false).await;
            if login_result3.is_err() || !local_auth_path.exists() {
                return Err(anyhow::anyhow!("Login failed: Could not create auth.json in .codex, .opencode, or ./local_auth directory."));
            }
            println!("Auth file created successfully at: {:?} (local fallback, move to ~/.codex or ~/.opencode for best compatibility)", local_auth_path);
            println!("WARNING: Using local fallback directory for authentication. Move auth.json to ~/.codex or ~/.opencode for best compatibility and Opencode integration.");
            return Ok(());
        }
        used_opencode = true;
    }

    if used_opencode {
        println!(
            "Auth file created successfully at: {:?} (Opencode integration)",
            opencode_auth_path
        );
    } else {
        println!(
            "Auth file created successfully at: {:?} (Codex Proxy Server)",
            codex_auth_path
        );
    }

    info!("Login successful");
    println!("Login completed!");
    Ok(())
}

fn display_menu() {
    println!("\n=== Codex Proxy Server===");
    println!("1. Run server");
    println!("2. Close all servers");
    println!("3. Login");
    println!("4. Refresh token");
    println!("5. Exit");
    println!("6. List running servers");
    print!("Please select an option (1-6): ");
    io::stdout().flush().unwrap();
}

fn get_user_choice() -> String {
    let mut choice = String::new();
    io::stdin()
        .read_line(&mut choice)
        .expect("Failed to read input");
    choice.trim().to_string()
}

fn app_router(app_state: AppState) -> Router {
    Router::new()
        .route(
            "/chat/completions",
            post(chat_completions_handler).layer(DefaultBodyLimit::max(MAX_CHAT_REQUEST_BYTES)),
        )
        .route(
            "/v1/chat/completions",
            post(chat_completions_handler).layer(DefaultBodyLimit::max(MAX_CHAT_REQUEST_BYTES)),
        )
        .route("/v1/models", get(models_handler))
        .route(
            "/v1/images/generations",
            post(image_generation_handler)
                .layer(DefaultBodyLimit::max(core::images::MAX_IMAGE_REQUEST_BYTES)),
        )
        .route(
            "/images/generations",
            post(image_generation_handler)
                .layer(DefaultBodyLimit::max(core::images::MAX_IMAGE_REQUEST_BYTES)),
        )
        .route(
            "/v1/images/edits",
            post(image_edit_handler)
                .layer(DefaultBodyLimit::max(core::images::MAX_IMAGE_REQUEST_BYTES)),
        )
        .route(
            "/images/edits",
            post(image_edit_handler)
                .layer(DefaultBodyLimit::max(core::images::MAX_IMAGE_REQUEST_BYTES)),
        )
        .route("/v1/images/models", get(image_models_handler))
        .route("/images/models", get(image_models_handler))
        .route("/v1/limits", get(limits_handler))
        .route("/v1/account", get(account_handler))
        .route("/v1/account/switch", post(account_switch_handler))
        .route("/health", get(health_handler))
        .layer(CorsLayer::permissive())
        .with_state(app_state)
}

async fn run_server() -> anyhow::Result<()> {
    info!("Starting Codex Proxy Server");

    // Load configuration
    let config = match Config::load() {
        Ok(config) => {
            info!("Configuration loaded successfully");
            Arc::new(config)
        }
        Err(e) => {
            error!("Failed to load configuration: {}", e);
            return Err(e);
        }
    };

    // Load the active subscription and any standby before accepting traffic.
    let accounts = AccountRouter::load(&config.codex_home).await?;

    // Create app state
    let client = reqwest::Client::builder()
        .tcp_keepalive(std::time::Duration::from_secs(30))
        .pool_idle_timeout(std::time::Duration::from_secs(90))
        .build()
        .unwrap_or_else(|error| {
            warn!(
                "shared client builder failed ({}), falling back to default",
                error
            );
            reqwest::Client::new()
        });
    // Before any upstream call: the presented Codex version gates the model catalog.
    chat_completions::resolve_codex_cli_version(&client).await;
    let app_state = AppState {
        config,
        accounts,
        limits: LimitsCache::default(),
        catalog: ModelCatalog::from_env(),
        client,
    };

    // Create router
    let app = app_router(app_state);

    // Configure server.  Loopback only, deliberately; RELAY_PORT rescues the
    // rare machine where 5011 is already taken (и это ручка `relay.port`
    // в helene.json: оболочка Hélène поднимает реле именно так).
    let port = std::env::var("RELAY_PORT")
        .ok()
        .and_then(|value| value.trim().parse::<u16>().ok())
        .unwrap_or(5011);
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    info!("Server listening on {}", addr);

    // Start server and block until it exits
    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    println!("Server is running. Press Ctrl+C to stop.");
    #[cfg(target_family = "windows")]
    {
        println!("The relay minimizes to the system tray (icon by the clock).");
        println!("Double-click the tray icon to show this window again; quit from the tray menu.");
        tray::spawn(port);
    }
    axum::serve(listener, app).await.unwrap();
    Ok(())
}

async fn refresh_token() -> anyhow::Result<()> {
    println!("Refreshing token...");

    // Load configuration
    let config = Config::load()?;

    // Get the codex auth
    let codex_auth = match CodexAuth::from_codex_home(&config.codex_home) {
        Ok(Some(auth)) => auth,
        _ => {
            return Err(anyhow::anyhow!("No authentication found. Please use the 'Login' option in the CLI menu. This enables Opencode and other integrations."));
        }
    };

    // Get token data which will automatically refresh if needed
    let token_data = match codex_auth.get_token_data().await {
        Ok(data) => data,
        Err(_) => {
            return Err(anyhow::anyhow!("No authentication found. Please use the 'Login' option in the CLI menu. This enables Opencode and other integrations."));
        }
    };

    println!("Token refreshed successfully!");
    match &token_data.account_id {
        Some(account_id) => println!("Account ID: {}", account_id),
        None => println!("Account ID: None"),
    }

    Ok(())
}

async fn close_all_servers() -> anyhow::Result<()> {
    println!("Closing all servers (system-wide)...");
    let mut closed = 0;
    for port in 5011..=5020 {
        let pids = get_pids_for_port(port);
        for pid in pids {
            if kill_pid(pid) {
                println!("Killed server on port {} (PID {})", port, pid);
                closed += 1;
            }
        }
    }
    println!("Closed {} running server(s) on ports 5011-5020.", closed);
    Ok(())
}

// Get PIDs listening on a port
fn get_pids_for_port(port: u16) -> Vec<u32> {
    #[cfg(target_family = "unix")]
    {
        use std::process::Command;
        let output = Command::new("lsof")
            .arg("-ti")
            .arg(format!(":{}", port))
            .output();
        if let Ok(out) = output {
            let stdout = String::from_utf8_lossy(&out.stdout);
            stdout
                .lines()
                .filter_map(|line| line.trim().parse::<u32>().ok())
                .collect()
        } else {
            vec![]
        }
    }
    #[cfg(target_family = "windows")]
    {
        use std::process::Command;
        let output = Command::new("netstat").arg("-ano").output();
        if let Ok(out) = output {
            let stdout = String::from_utf8_lossy(&out.stdout);
            stdout
                .lines()
                .filter_map(|line| {
                    if line.contains(&format!(":{}", port)) {
                        line.split_whitespace().last()?.parse::<u32>().ok()
                    } else {
                        None
                    }
                })
                .collect()
        } else {
            vec![]
        }
    }
}

// Kill a process by PID
fn kill_pid(pid: u32) -> bool {
    #[cfg(target_family = "unix")]
    {
        use std::process::Command;
        let status = Command::new("kill").arg("-9").arg(pid.to_string()).status();
        status.map(|s| s.success()).unwrap_or(false)
    }
    #[cfg(target_family = "windows")]
    {
        use std::process::Command;
        let status = Command::new("taskkill")
            .arg("/PID")
            .arg(pid.to_string())
            .arg("/F")
            .status();
        status.map(|s| s.success()).unwrap_or(false)
    }
}

// Utility: Check if a port is in use (cross-platform)
fn is_port_in_use(port: u16) -> bool {
    #[cfg(target_family = "unix")]
    {
        use std::process::Command;
        // Try lsof first
        let lsof_output = Command::new("lsof")
            .arg("-i")
            .arg(format!(":{}", port))
            .output();
        if let Ok(out) = lsof_output {
            let stdout = String::from_utf8_lossy(&out.stdout);
            if stdout.contains(&format!(":{}", port)) {
                return true;
            }
        } else {
            eprintln!("lsof failed for port {}", port);
        }
        // Fallback to netstat
        let netstat_output = Command::new("netstat").arg("-an").output();
        if let Ok(out) = netstat_output {
            let stdout = String::from_utf8_lossy(&out.stdout);
            if stdout.contains(&format!(":{}", port)) {
                return true;
            }
        } else {
            eprintln!("netstat failed for port {}", port);
        }
        false
    }
    #[cfg(target_family = "windows")]
    {
        use std::process::Command;
        let output = Command::new("netstat").arg("-ano").output();
        if let Ok(out) = output {
            let stdout = String::from_utf8_lossy(&out.stdout);
            if stdout.contains(&format!(":{}", port)) {
                return true;
            }
        } else {
            eprintln!("netstat failed for port {}", port);
        }
        false
    }
}

async fn list_running_servers() -> anyhow::Result<()> {
    println!("Checking ports 5011-5020 for running servers...");
    let mut found = false;
    for port in 5011..=5020 {
        if is_port_in_use(port) {
            println!("Port {}: RUNNING", port);
            found = true;
        }
    }
    if !found {
        println!("No running servers found on ports 5011-5020.");
    }
    Ok(())
}

#[allow(dead_code)]
async fn check_authentication(config: &Config) -> anyhow::Result<()> {
    info!(
        "Checking authentication in directory: {:?}",
        &config.codex_home
    );
    let auth_file_path = config.codex_home.join("auth.json");
    info!("Looking for auth file at: {:?}", auth_file_path);

    if auth_file_path.exists() {
        info!("Auth file found!");
        // Try to read the file to check if it's valid
        match std::fs::read_to_string(&auth_file_path) {
            Ok(_content) => {
                // Auth file content preview removed for security
            }
            Err(e) => {
                error!("Error reading auth file: {}", e);
                return Err(anyhow::anyhow!("Failed to read auth file: {}", e));
            }
        }
    } else {
        warn!("Auth file not found!");
        // List files in the directory to see what's there
        if let Ok(entries) = std::fs::read_dir(&config.codex_home) {
            info!("Files in codex home directory:");
            for entry in entries.flatten() {
                info!("  - {}", entry.file_name().to_string_lossy());
            }
        }

        // Check if we're in .codex or .opencode and provide specific guidance
        if let Some(home_dir) = dirs::home_dir() {
            let codex_path = home_dir.join(".codex");
            let opencode_path = home_dir.join(".opencode");

            if config.codex_home == codex_path {
                info!("Looking in .codex directory. Checking if auth file exists in .opencode...");
                let opencode_auth = opencode_path.join("auth.json");
                if opencode_auth.exists() {
                    info!("Found auth file in .opencode directory. Consider moving it to .codex for better compatibility.");
                }
            } else if config.codex_home == opencode_path {
                info!("Looking in .opencode directory. Checking if auth file exists in .codex...");
                let codex_auth = codex_path.join("auth.json");
                if codex_auth.exists() {
                    info!("Found auth file in .codex directory. Using that instead.");
                }
            }
        }
    }

    let codex_auth = match CodexAuth::from_codex_home(&config.codex_home) {
        Ok(Some(auth)) => auth,
        _ => {
            return Err(anyhow::anyhow!("No authentication found. Please use the 'Login' option in the CLI menu. This enables Opencode and other integrations."));
        }
    };

    let token_data = match codex_auth.get_token_data().await {
        Ok(data) => data,
        Err(_) => {
            return Err(anyhow::anyhow!("No authentication found. Please use the 'Login' option in the CLI menu. This enables Opencode and other integrations."));
        }
    };

    if token_data.access_token.is_empty() {
        return Err(anyhow::anyhow!("No authentication found. Please use the 'Login' option in the CLI menu. This enables Opencode and other integrations."));
    }

    if token_data.account_id.is_none() {
        return Err(anyhow::anyhow!("No authentication found. Please use the 'Login' option in the CLI menu. This enables Opencode and other integrations."));
    }

    // Log token information for debugging
    info!("Authentication successful");
    info!(
        "Plan type: {}",
        codex_auth.get_plan_type().as_deref().unwrap_or("None")
    );

    Ok(())
}

async fn health_handler(State(state): State<AppState>) -> Json<serde_json::Value> {
    info!("💓 Health check endpoint requested");
    let response = serde_json::json!({
        "status": "healthy",
        "timestamp": chrono::Utc::now().to_rfc3339(),
        "service": "relay",
        "version": env!("CARGO_PKG_VERSION"),
        "account_router": {
            "active_slot": state.accounts.active_slot().await,
            "configured_slots": state.accounts.account_count(),
        },
        "model_catalog": state.catalog.status().await,
        "codex_client_version": chat_completions::codex_cli_version(),
    });
    info!("✅ Health check response: {}", response);
    Json(response)
}

async fn models_handler(State(state): State<AppState>) -> Json<ModelList> {
    info!("📋 Models endpoint requested");
    // The backend's own catalog (core::catalog), the same snapshot the request
    // validator below reads, so the advertised set cannot drift from the
    // accepted set. Static FALLBACK_MODELS only when the backend cannot be asked.
    let list = state
        .catalog
        .model_list(&state.accounts, &state.client)
        .await;
    info!("✅ Returning {} available models", list.data.len());
    Json(list)
}

async fn limits_handler(State(state): State<AppState>) -> Response {
    match state.limits.get(&state.accounts).await {
        Ok(value) => {
            let mut response = Json(value.clone()).into_response();
            core::limits::apply_response_headers(response.headers_mut(), &value);
            response
        }
        Err(error) => {
            warn!("Limits request failed: {}", error);
            (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({
                    "error": {
                        "message": "OpenAI usage limits are temporarily unavailable",
                        "type": "upstream_error",
                        "code": "limits_unavailable"
                    }
                })),
            )
                .into_response()
        }
    }
}

/// Which subscriptions exist and which one is live right now.
///
/// The active slot lives in the router's memory, so editing auth files on a
/// running relay changes nothing until a restart.  Without this pair of
/// endpoints "switch me to the other subscription" had no executor at all —
/// only an instruction telling a human to do it by hand.
async fn account_handler(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "active_slot": state.accounts.active_slot().await,
        "configured_slots": state.accounts.account_count(),
        "slots": state.accounts.describe().await,
    }))
}

#[derive(serde::Deserialize)]
struct AccountSwitchRequest {
    slot: String,
}

async fn account_switch_handler(
    State(state): State<AppState>,
    payload: Result<Json<AccountSwitchRequest>, JsonRejection>,
) -> Response {
    let requested = match payload {
        Ok(Json(request)) => request.slot,
        Err(rejection) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": {
                        "message": format!("expected {{\"slot\": \"...\"}}: {rejection}"),
                        "type": "invalid_request_error",
                        "code": "invalid_json"
                    }
                })),
            )
                .into_response();
        }
    };
    let previous = state.accounts.active_slot().await;
    match state.accounts.switch_to(&requested).await {
        Ok(lease) => {
            info!("account switch requested: {} -> {}", previous, lease.slot);
            // No cache to clear: LimitsCache is keyed by account, and `get`
            // leases the active slot first — so "how much is left?" already
            // answers about the subscription that is now live.
            Json(serde_json::json!({
                "previous_slot": previous,
                "active_slot": lease.slot,
                "slots": state.accounts.describe().await,
            }))
            .into_response()
        }
        Err(error) => {
            warn!("account switch refused: {}", error);
            (
                StatusCode::CONFLICT,
                Json(serde_json::json!({
                    "error": {
                        "message": error.to_string(),
                        "type": "account_switch_refused",
                        "code": "switch_refused"
                    },
                    "active_slot": state.accounts.active_slot().await,
                    "slots": state.accounts.describe().await,
                })),
            )
                .into_response()
        }
    }
}

/// Ключ петли к реле: `RELAY_API_KEY`. Задан — вызов модели обязан принести его
/// Bearer-ом; пусто — проверки нет вовсе (так живёт её серверное реле, у
/// которого порт не выходит за контейнер).
///
/// Обещание было старше проверки. Оболочка Hélène передаёт ключ этой
/// переменной с 0.4.x, установщик пишет в helene.json `sk-frame-…` и объясняет
/// в коде: «открытый локальный порт позволял бы любому процессу на машине жечь
/// подписку владельца». Реле переменную не читало, и любой процесс мог.
fn loop_key() -> Option<&'static str> {
    static KEY: std::sync::OnceLock<Option<String>> = std::sync::OnceLock::new();
    KEY.get_or_init(|| {
        std::env::var("RELAY_API_KEY")
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
    })
    .as_deref()
}

/// Принёс ли запрос ключ петли. Нет ключа в среде — пускаем всех, как раньше.
fn key_ok(headers: &HeaderMap) -> bool {
    let Some(key) = loop_key() else { return true };
    headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .map(str::trim)
        .and_then(|value| {
            value
                .strip_prefix("Bearer ")
                .or_else(|| value.strip_prefix("bearer "))
        })
        .map(|got| got.trim() == key)
        .unwrap_or(false)
}

async fn image_models_handler(headers: HeaderMap) -> Response {
    if !key_ok(&headers) {
        return (StatusCode::UNAUTHORIZED, Json(serde_json::json!({"error":{"code":"invalid_api_key","message":"Missing or wrong relay key"}}))).into_response();
    }
    Json(serde_json::json!({"object":"list","data":[{"id":core::images::DEFAULT_IMAGE_MODEL,"object":"model","owned_by":"chatgpt","modalities":["image"],"operations":["generate","edit"]}],"source":"codex_builtin","entitlement_verified":false})).into_response()
}

async fn image_generation_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    payload: Result<Json<core::images::ImageRequest>, JsonRejection>,
) -> Response {
    image_handler(state, headers, payload, false).await
}

async fn image_edit_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    payload: Result<Json<core::images::ImageRequest>, JsonRejection>,
) -> Response {
    image_handler(state, headers, payload, true).await
}

async fn image_handler(
    state: AppState,
    headers: HeaderMap,
    payload: Result<Json<core::images::ImageRequest>, JsonRejection>,
    edit: bool,
) -> Response {
    if !key_ok(&headers) {
        return (StatusCode::UNAUTHORIZED, Json(serde_json::json!({"error":{"code":"invalid_api_key","message":"Missing or wrong relay key"}}))).into_response();
    }
    let request = match payload {
        Ok(Json(request)) => request,
        Err(rejection) => return (rejection.status(), Json(serde_json::json!({"error":{"code":"invalid_image_request","message":rejection.body_text()}}))).into_response(),
    };
    if let Err(message) = request.validate(edit) {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error":{"code":"invalid_image_request","message":message}})),
        )
            .into_response();
    }
    let turn_id = headers
        .get("x-codex-image-turn-id")
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.is_empty() && value.len() <= 200)
        .map(str::to_string)
        .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
    let reply = core::images::forward(
        &state.accounts,
        &state.client,
        &request,
        &state.config.chatgpt_base_url,
        edit,
        &turn_id,
    )
    .await;
    (reply.status, reply.headers, Json(reply.body)).into_response()
}

async fn chat_completions_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    payload: Result<Json<ChatRequest>, JsonRejection>,
) -> Result<Response, StatusCode> {
    if !key_ok(&headers) {
        warn!("chat/completions без ключа петли (RELAY_API_KEY задан) — отказ");
        return Ok((
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({
                "error": {
                    "message": "missing or wrong Authorization: Bearer <RELAY_API_KEY>",
                    "type": "invalid_request_error",
                    "param": null,
                    "code": "invalid_api_key"
                }
            })),
        )
            .into_response());
    }
    let mut request = match payload {
        Ok(Json(request)) => request,
        Err(rejection) => {
            let status = rejection.status();
            let code = if status == StatusCode::PAYLOAD_TOO_LARGE {
                "request_too_large"
            } else {
                "invalid_json"
            };
            return Ok((
                status,
                Json(serde_json::json!({
                    "error": {
                        "message": rejection.body_text(),
                        "type": "invalid_request_error",
                        "param": null,
                        "code": code
                    }
                })),
            )
                .into_response());
        }
    };

    info!("🚀 CHAT COMPLETIONS REQUEST RECEIVED!");
    info!("Request messages count: {}", request.messages.len());
    info!("Request tools count: {}", request.tools.len());
    info!("Request image parts count: {}", request.image_part_count());

    // Validate the model against the backend's live catalog (core::catalog), not
    // a constant and not a prefix: a slug the cached catalog does not know
    // triggers one early refresh, so a model released after the last fetch is
    // accepted on first ask; a typo is still refused loudly here, and the 404
    // lists what is actually on offer right now.
    let model_known = state
        .catalog
        .is_known(&request.model, &state.accounts, &state.client)
        .await;
    info!("Request model supported: {}", model_known);
    if !model_known {
        warn!("Invalid model requested (value redacted)");
        let snapshot = state.catalog.snapshot(&state.accounts, &state.client).await;
        return Ok((
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "error": {
                    "message": format!(
                        "Model not found. Available models ({}): {}. Requested: {}",
                        snapshot.source.as_str(),
                        snapshot.listed_slugs().join(", "),
                        request.model
                    ),
                    "type": "model_not_found",
                    "code": "model_not_found"
                }
            })),
        )
            .into_response());
    }

    // Reasoning effort: the catalog says which levels this model takes.
    // gpt-6-astra refuses "none"/"minimal" with a 400, which used to cost a
    // failed call plus a conservative retry carrying the full Codex preamble
    // (~5k prompt tokens per turn). Clamp to the nearest level the model
    // supports before anything goes upstream; models the catalog knows no
    // levels for are left exactly as before.
    let snapshot = state.catalog.snapshot(&state.accounts, &state.client).await;
    if let Some(entry) = snapshot.find(&request.model) {
        let wanted = request
            .reasoning_effort
            .clone()
            .or_else(|| state.config.reasoning_effort.clone());
        let clamped = core::catalog::clamp_effort(
            wanted.as_deref(),
            &entry.reasoning_efforts,
            entry.default_reasoning_effort.as_deref(),
        );
        if clamped != wanted {
            info!(
                "reasoning effort {:?} -> {:?} for {} (model takes {:?})",
                wanted, clamped, request.model, entry.reasoning_efforts
            );
        }
        if wanted.is_some() {
            request.reasoning_effort = clamped;
        }
    }

    if let Err(validation_error) = request.validate_content() {
        warn!(
            "Invalid message content at {}: {}",
            validation_error.param, validation_error.message
        );
        return Ok((
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": {
                    "message": validation_error.to_string(),
                    "type": "invalid_request_error",
                    "param": validation_error.param,
                    "code": "invalid_message_content"
                }
            })),
        )
            .into_response());
    }

    // Process the chat completion
    match chat_completions::stream_chat_completions(
        &state.config,
        state.accounts.clone(),
        request,
        state.client.clone(),
    )
    .await
    {
        Ok(response_stream) => {
            info!("✅ Chat completion stream started successfully");
            // Convert the response stream to SSE
            let sse_stream = response_stream.map(|result| {
                    match result {
                        Ok(event) => {
                            let json = serde_json::to_string(&event).unwrap_or_else(|e| {
                                error!("Failed to serialize event: {}", e);
                                r#"{"error": "Failed to serialize event"}"#.to_string()
                            });
                            Ok::<Event, Box<dyn std::error::Error + Send + Sync>>(Event::default().data(json))
                        }
                        Err(e) => {
                            error!("Stream error: {}", e);
                            let error_json = serde_json::to_string(&serde_json::json!({
                                "error": {
                                    "message": format!("Stream error: {}", e),
                                    "type": "stream_error",
                                    "code": "stream_error"
                                }
                            })).unwrap_or_else(|_| r#"{"error":{"message":"Failed to format error","type":"format_error","code":"format_error"}}"#.to_string());
                            Ok::<Event, Box<dyn std::error::Error + Send + Sync>>(Event::default().data(error_json))
                        }
                    }
                });

            Ok(axum::response::Sse::new(sse_stream).into_response())
        }
        Err(e) => {
            error!("❌ Chat completions error: {}", e);
            error!("Error details: {:?}", e);
            Ok((
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({
                    "error": {
                        "message": format!("Failed to process chat completion: {}", e),
                        "type": "server_error",
                        "code": "internal_error"
                    }
                })),
            )
                .into_response())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use base64::Engine;
    use futures::StreamExt;
    use serde_json::json;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use tempfile::tempdir;
    use tower::ServiceExt;

    fn write_test_account(root: &std::path::Path, slot: &str, account_id: &str) {
        let home = root.join("accounts").join(slot);
        std::fs::create_dir_all(&home).unwrap();
        let claims = json!({"email":format!("{slot}@example.test"),"https://api.openai.com/auth":{"chatgpt_plan_type":"pro"}});
        let encoded = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .encode(serde_json::to_vec(&claims).unwrap());
        std::fs::write(home.join("auth.json"), json!({
            "OPENAI_API_KEY": null,
            "tokens": {"id_token":format!("e30.{encoded}.c2ln"),"access_token":format!("synthetic-{slot}"),"refresh_token":format!("refresh-{slot}"),"account_id":account_id},
            "last_refresh": chrono::Utc::now().to_rfc3339()
        }).to_string()).unwrap();
    }

    #[tokio::test]
    async fn image_routes_forward_exact_contract_and_preserve_quota_error() {
        let seen = Arc::new(std::sync::Mutex::new(Vec::<serde_json::Value>::new()));
        let records = seen.clone();
        let png = base64::engine::general_purpose::STANDARD.encode(b"\x89PNG\r\n\x1a\nfixture");
        let upstream = Router::new().fallback(axum::routing::post(move |uri: axum::http::Uri, headers: HeaderMap, Json(body): Json<serde_json::Value>| {
            let records = records.clone(); let png = png.clone();
            async move {
                records.lock().unwrap().push(json!({"path":uri.path(),"body":body,"account":headers.get("chatgpt-account-id").unwrap().to_str().unwrap(),"authorization":headers.get("authorization").unwrap().to_str().unwrap(),"turn":headers.get("x-codex-image-turn-id").unwrap().to_str().unwrap()}));
                let mut response = if body["prompt"] == "quota" {
                    (StatusCode::TOO_MANY_REQUESTS, Json(json!({"error":{"code":"image_generation_limit_reached","message":"image allowance"}}))).into_response()
                } else {
                    Json(json!({"data":[{"b64_json":png,"generation_id":"generation-fixture"}],"size":"1254x1254","usage":{"input_tokens":21,"output_tokens":515}})).into_response()
                };
                response.headers_mut().insert("x-codex-imagegen-request-id", "request-fixture".parse().unwrap());
                response
            }
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let task = tokio::spawn(async move {
            axum::serve(listener, upstream).await.unwrap();
        });
        let root = tempdir().unwrap();
        write_test_account(root.path(), "primary", "acct-primary");
        write_test_account(root.path(), "secondary", "acct-secondary");
        let accounts = AccountRouter::load(root.path()).await.unwrap();
        let app = app_router(AppState {
            config: Arc::new(Config {
                codex_home: root.path().into(),
                chatgpt_base_url: format!("http://{address}"),
                model: String::new(),
                user_instructions: None,
                reasoning_effort: None,
                instructions_mode: None,
                parallel_tool_calls: false,
            }),
            accounts: accounts.clone(),
            limits: LimitsCache::default(),
            catalog: ModelCatalog::static_only(),
            client: reqwest::Client::new(),
        });
        for (uri, edit, prompt, status) in [
            ("/v1/images/generations", false, "draw", 200),
            ("/images/edits", true, "edit", 200),
            ("/v1/images/generations", false, "quota", 429),
        ] {
            let mut payload = json!({"model":"gpt-image-2","prompt":prompt,"quality":"low","size":"1024x1024","background":"opaque","n":1});
            if edit {
                payload["images"] = json!([{"file_id":"file-fixture"}]);
            }
            let mut builder = axum::http::Request::builder()
                .method("POST")
                .uri(uri)
                .header("content-type", "application/json")
                .header("x-codex-image-turn-id", "turn-fixture");
            if let Some(key) = loop_key() {
                builder = builder.header("authorization", format!("Bearer {key}"));
            }
            let response = app
                .clone()
                .oneshot(builder.body(Body::from(payload.to_string())).unwrap())
                .await
                .unwrap();
            assert_eq!(response.status().as_u16(), status);
            assert_eq!(
                response.headers()["x-codex-imagegen-request-id"],
                "request-fixture"
            );
            let bytes = axum::body::to_bytes(response.into_body(), 1024 * 1024)
                .await
                .unwrap();
            let body: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
            if status == 200 {
                assert_eq!(body["size"], "1254x1254");
                assert_eq!(body["usage"]["output_tokens"], 515);
            } else {
                assert_eq!(body["error"]["code"], "image_generation_limit_reached");
            }
            let last = seen.lock().unwrap().last().unwrap().clone();
            assert_eq!(last["body"], payload);
            assert_eq!(last["account"], "acct-primary");
            assert_eq!(last["authorization"], "Bearer synthetic-primary");
            assert_eq!(last["turn"], "turn-fixture");
            assert_eq!(
                last["path"],
                if edit {
                    "/images/edits"
                } else {
                    "/images/generations"
                }
            );
        }
        assert_eq!(
            seen.lock().unwrap().len(),
            3,
            "no paid request may be replayed"
        );
        assert_eq!(
            accounts.active_slot().await,
            "primary",
            "image quota does not park the text account"
        );
        let invalid = axum::http::Request::builder()
            .method("POST")
            .uri("/v1/images/generations")
            .header("content-type", "application/json")
            .body(Body::from(
                json!({"model":"gpt-image-2","prompt":"","stream":true}).to_string(),
            ))
            .unwrap();
        assert!(app
            .oneshot(invalid)
            .await
            .unwrap()
            .status()
            .is_client_error());
        assert_eq!(seen.lock().unwrap().len(), 3);
        task.abort();
    }

    async fn http_post(root: &std::path::Path, upstream_url: String) -> (Response, AccountRouter) {
        chat_completions::set_test_upstream_url(Some(upstream_url));
        let accounts = AccountRouter::load(root).await.unwrap();
        let app = app_router(AppState {
            config: Arc::new(Config {
                codex_home: root.to_path_buf(),
                chatgpt_base_url: String::new(),
                model: "gpt-5.6-sol".to_string(),
                user_instructions: None,
                reasoning_effort: None,
                instructions_mode: Some("minimal".to_string()),
                parallel_tool_calls: false,
            }),
            accounts: accounts.clone(),
            limits: LimitsCache::default(),
            catalog: ModelCatalog::static_only(),
            client: reqwest::Client::new(),
        });
        let request = axum::http::Request::builder()
            .method("POST")
            .uri("/chat/completions")
            .header("content-type", "application/json")
            .body(Body::from(
                json!({"model":"gpt-5.6-sol","messages":[{"role":"user","content":"hello"}]})
                    .to_string(),
            ))
            .unwrap();
        (app.oneshot(request).await.unwrap(), accounts)
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn http_status_matrix_uses_distinct_accounts_and_preserves_mixed_facts() {
        let _lever = chat_completions::test_lever_guard();
        chat_completions::force_test_terminal_field();
        for (primary_status, secondary_status) in [(401u16, 429u16), (429, 401)] {
            let seen = Arc::new(std::sync::Mutex::new(Vec::<(String, String)>::new()));
            let seen_server = seen.clone();
            let hit = Arc::new(AtomicUsize::new(0));
            let hit_server = hit.clone();
            let upstream = Router::new().fallback(axum::routing::any(move |headers: HeaderMap| {
                let seen = seen_server.clone();
                let hit = hit_server.clone();
                async move {
                    let account_id = headers
                        .get("chatgpt-account-id")
                        .unwrap()
                        .to_str()
                        .unwrap()
                        .to_string();
                    let authorization = headers
                        .get("authorization")
                        .unwrap()
                        .to_str()
                        .unwrap()
                        .to_string();
                    seen.lock()
                        .unwrap()
                        .push((account_id.clone(), authorization.clone()));
                    hit.fetch_add(1, Ordering::SeqCst);

                    // Select the fault from the actual account identity. A replay
                    // with a stale bearer or account id must not accidentally pass
                    // merely because it happened to be the second HTTP request.
                    let code = match (account_id.as_str(), authorization.as_str()) {
                        ("acct-primary", "Bearer synthetic-primary") => primary_status,
                        ("acct-secondary", "Bearer synthetic-secondary") => secondary_status,
                        _ => StatusCode::BAD_REQUEST.as_u16(),
                    };
                    let body = if code == 429 {
                        r#"{"error":{"code":"usage_limit_reached","resets_in_seconds":73}}"#
                    } else {
                        r#"{"error":{"message":"expired"}}"#
                    };
                    Response::builder()
                        .status(code)
                        .body(Body::from(body))
                        .unwrap()
                }
            }));
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let addr = listener.local_addr().unwrap();
            tokio::spawn(async move {
                let _ = axum::serve(listener, upstream).await;
            });
            let root = tempdir().unwrap();
            write_test_account(root.path(), "primary", "acct-primary");
            write_test_account(root.path(), "secondary", "acct-secondary");
            let (response, accounts) =
                http_post(root.path(), format!("http://{addr}/responses")).await;
            assert_eq!(response.status(), StatusCode::OK);
            let body = axum::body::to_bytes(response.into_body(), 1024 * 1024)
                .await
                .unwrap();
            let wire = String::from_utf8(body.to_vec()).unwrap();
            let data_line = wire
                .lines()
                .filter(|line| line.starts_with("data: "))
                .find(|line| line.contains("subscriptions_unavailable"))
                .expect("structured mixed-account terminal SSE data line");
            let terminal: serde_json::Value =
                serde_json::from_str(data_line.trim_start_matches("data: ")).unwrap();
            let terminal = &terminal["relay_terminal"];
            assert_eq!(terminal["code"], "subscriptions_unavailable");
            let attempts = terminal["attempts"].as_array().unwrap();
            assert_eq!(attempts.len(), 2);
            let expected_attempts = if primary_status == 401 {
                [
                    ("primary", "subscription_needs_login", 401u64, None),
                    (
                        "secondary",
                        "subscription_window_exhausted",
                        429u64,
                        Some(73u64),
                    ),
                ]
            } else {
                [
                    (
                        "primary",
                        "subscription_window_exhausted",
                        429u64,
                        Some(73u64),
                    ),
                    ("secondary", "subscription_needs_login", 401u64, None),
                ]
            };
            for (attempt, (slot, code, status, reset)) in attempts.iter().zip(expected_attempts) {
                assert_eq!(attempt["slot"], slot);
                assert_eq!(attempt["code"], code);
                assert_eq!(attempt["status"], status);
                if let Some(reset) = reset {
                    assert_eq!(attempt["resets_in_seconds"], reset);
                } else {
                    assert!(attempt.get("resets_in_seconds").is_none());
                }
            }
            assert_eq!(hit.load(Ordering::SeqCst), 2);
            assert_eq!(
                *seen.lock().unwrap(),
                [
                    (
                        "acct-primary".to_string(),
                        "Bearer synthetic-primary".to_string()
                    ),
                    (
                        "acct-secondary".to_string(),
                        "Bearer synthetic-secondary".to_string()
                    )
                ]
            );
            assert_eq!(accounts.active_slot().await, "secondary");
            let slots = accounts.describe().await;
            let primary = slots.iter().find(|slot| slot.slot == "primary").unwrap();
            let secondary = slots.iter().find(|slot| slot.slot == "secondary").unwrap();
            assert!(!primary.active);
            assert!(secondary.active);
            assert!(primary.cooldown_seconds_left > 0);
            assert_eq!(secondary.cooldown_seconds_left, 0);
            chat_completions::set_test_upstream_url(None);
        }
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn dropping_http_sse_body_cancels_pending_upstream_read() {
        let _lever = chat_completions::test_lever_guard();
        struct Dropped(Arc<tokio::sync::Semaphore>);
        impl Drop for Dropped {
            fn drop(&mut self) {
                self.0.add_permits(1);
            }
        }

        let hits = Arc::new(AtomicUsize::new(0));
        let upstream_dropped = Arc::new(tokio::sync::Semaphore::new(0));
        let counter = hits.clone();
        let dropped = upstream_dropped.clone();
        let upstream = Router::new().fallback(axum::routing::any(move || {
            let counter = counter.clone();
            let dropped = dropped.clone();
            async move {
                counter.fetch_add(1, Ordering::SeqCst);
                let stream = async_stream::stream! {
                    let _guard = Dropped(dropped);
                    yield Ok::<_, std::io::Error>(bytes::Bytes::from_static(
                    b"data: {\"type\":\"response.output_item.done\",\"item\":{\"type\":\"message\",\"role\":\"assistant\",\"content\":[{\"type\":\"output_text\",\"text\":\"visible\"}]}}\n\n",
                    ));
                    std::future::pending::<()>().await;
                };
                Response::builder().status(200).header("content-type", "text/event-stream")
                    .body(Body::from_stream(stream)).unwrap()
            }
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            let _ = axum::serve(listener, upstream).await;
        });
        chat_completions::set_test_upstream_url(Some(format!("http://{addr}/responses")));

        let root = tempdir().unwrap();
        write_test_account(root.path(), "primary", "acct-primary");
        let app = app_router(AppState {
            config: Arc::new(Config {
                codex_home: root.path().to_path_buf(),
                chatgpt_base_url: String::new(),
                model: "gpt-5.6-sol".to_string(),
                user_instructions: None,
                reasoning_effort: None,
                instructions_mode: Some("minimal".to_string()),
                parallel_tool_calls: false,
            }),
            accounts: AccountRouter::load(root.path()).await.unwrap(),
            limits: LimitsCache::default(),
            catalog: ModelCatalog::static_only(),
            client: reqwest::Client::new(),
        });
        let request = axum::http::Request::builder()
            .method("POST")
            .uri("/chat/completions")
            .header("content-type", "application/json")
            .body(Body::from(
                json!({"model":"gpt-5.6-sol","messages":[{"role":"user","content":"hello"}]})
                    .to_string(),
            ))
            .unwrap();
        let response = app.oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let mut body = response.into_body().into_data_stream();
        let frame = tokio::time::timeout(std::time::Duration::from_secs(1), body.next())
            .await
            .expect("HTTP SSE must expose first event")
            .expect("SSE body has data")
            .expect("valid SSE data");
        assert!(frame
            .windows(b"visible".len())
            .any(|window| window == b"visible"));
        drop(body);
        tokio::time::timeout(
            std::time::Duration::from_secs(1),
            upstream_dropped.acquire(),
        )
        .await
        .expect("dropping HTTP SSE body must cancel upstream")
        .unwrap()
        .forget();
        assert_eq!(hits.load(Ordering::SeqCst), 1);
        chat_completions::set_test_upstream_url(None);
    }
}
