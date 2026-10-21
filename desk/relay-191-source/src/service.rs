//! Shared product controls. Both the native app and the headless CLI use this
//! service; neither owns a second implementation of account state.
use crate::core::{
    account_router::{AccountRouter, ACCOUNT_SLOTS},
    catalog::ModelCatalog,
    chat_completions,
    config::Config,
    limits::LimitsCache,
};
use crate::settings::Settings;
use crate::{app_router, AppState};
use anyhow::{anyhow, bail, Context, Result};
use axum::{
    extract::State,
    http::{HeaderMap, StatusCode},
    routing::post,
    Json, Router,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::VecDeque,
    fs,
    path::{Path, PathBuf},
    sync::Arc,
};
use tokio::sync::Mutex;

#[derive(Clone, Serialize, Deserialize)]
struct ControlAddress {
    port: u16,
    token: String,
}
struct Running {
    shutdown: tokio::sync::oneshot::Sender<()>,
    task: tokio::task::JoinHandle<()>,
    address: ControlAddress,
    config: Arc<Config>,
}
#[derive(Default, Clone, Serialize)]
struct LoginState {
    pending: bool,
    slot: Option<String>,
    error: Option<String>,
}

pub struct Relay {
    state: AppState,
    running: Mutex<Option<Running>>,
    management: Mutex<Option<Running>>,
    login_task: Mutex<Option<tokio::task::JoinHandle<()>>>,
    login_state: Arc<Mutex<LoginState>>,
    events: Arc<Mutex<VecDeque<Value>>>,
    settings: Mutex<Settings>,
    _lock: fs::File,
}

fn descriptor(root: &Path) -> PathBuf {
    root.join("control.json")
}
fn private_json(path: &Path, value: &impl Serialize) -> Result<()> {
    let mut file = tempfile::NamedTempFile::new_in(path.parent().context("missing parent")?)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.as_file()
            .set_permissions(fs::Permissions::from_mode(0o600))?;
    }
    serde_json::to_writer_pretty(file.as_file_mut(), value)?;
    file.as_file_mut().sync_all()?;
    file.persist(path).map_err(|e| e.error)?;
    Ok(())
}

impl Relay {
    pub async fn new() -> Result<Arc<Self>> {
        let config = Arc::new(Config::load()?);
        fs::create_dir_all(&config.codex_home)
            .context("Relay data directory is not writable; set RELAY_AUTH_DIR")?;
        let lock = fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(config.codex_home.join("runtime.lock"))?;
        lock.try_lock().context("This relay installation is already open; use its window or the headless control commands")?;
        let api_key = crate::local_key::load(
            &config.codex_home,
            std::env::var("RELAY_API_KEY").ok().as_deref(),
        )?;
        let settings = Settings::load(&config.codex_home)?;
        settings.effective_port()?;
        let accounts = AccountRouter::open(&config.codex_home).await?;
        let client = crate::core::network::client()?;
        chat_completions::resolve_codex_cli_version(&client).await;
        Ok(Arc::new(Self {
            state: AppState {
                live_config: Some(Arc::new(std::sync::RwLock::new(config.clone()))),
                api_key: Some(Arc::from(api_key)),
                config,
                accounts,
                client,
                limits: LimitsCache::default(),
                catalog: ModelCatalog::from_env(),
            },
            running: Mutex::new(None),
            login_task: Mutex::new(None),
            login_state: Arc::new(Mutex::new(LoginState::default())),
            events: Arc::new(Mutex::new(VecDeque::new())),
            settings: Mutex::new(settings),
            management: Mutex::new(None),
            _lock: lock,
        }))
    }

    async fn event(&self, message: impl Into<String>) {
        let mut events = self.events.lock().await;
        events.push_front(json!({"time": chrono::Local::now().format("%H:%M:%S").to_string(), "message": message.into()}));
        events.truncate(100);
    }

    pub async fn start_management(self: &Arc<Self>) -> Result<()> {
        let mut management = self.management.lock().await;
        if management.as_ref().is_some_and(|server| !server.task.is_finished()) {
            return Ok(());
        }
        let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0)).await?;
        let address = ControlAddress {
            port: listener.local_addr()?.port(),
            token: uuid::Uuid::new_v4().to_string(),
        };
        let app = Router::new()
            .route("/_relay/control", post(control_handler))
            .layer(axum::extract::DefaultBodyLimit::max(32768))
            .with_state((self.clone(), address.token.clone()));
        private_json(&descriptor(&self.state.config.codex_home), &address)?;
        let (shutdown, receive) = tokio::sync::oneshot::channel();
        let task = tokio::spawn(async move {
            let _ = axum::serve(listener, app)
                .with_graceful_shutdown(async { let _ = receive.await; })
                .await;
        });
        *management = Some(Running { shutdown, task, address, config: self.state.config.clone() });
        Ok(())
    }

    pub async fn shutdown_management(&self) {
        if let Some(mut server) = self.management.lock().await.take() {
            let _ = server.shutdown.send(());
            if tokio::time::timeout(std::time::Duration::from_secs(3), &mut server.task).await.is_err() {
                server.task.abort();
            }
            if let Ok(bytes) = fs::read(descriptor(&self.state.config.codex_home)) {
                if serde_json::from_slice::<ControlAddress>(&bytes).is_ok_and(|address| address.token == server.address.token) {
                    let _ = fs::remove_file(descriptor(&self.state.config.codex_home));
                }
            }
        }
    }

    pub fn start(self: &Arc<Self>) -> futures_util::future::BoxFuture<'_, Result<()>> {
        Box::pin(async move {
            let mut running = self.running.lock().await;
            if running
                .as_ref()
                .is_some_and(|server| !server.task.is_finished())
            {
                return Ok(());
            }
            *running = None;
            let settings = self.settings.lock().await.clone();
            let port = settings.effective_port()?;
            let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST,port)).await
            .with_context(|| format!("Port {port} is already in use or unavailable. Stop its owner or choose another port."))?;
            let port = listener.local_addr()?.port();
            let address = ControlAddress {
                port,
                token: uuid::Uuid::new_v4().to_string(),
            };
            let control = Router::new()
                .route("/_relay/control", post(control_handler))
                .layer(axum::extract::DefaultBodyLimit::max(32768))
                .with_state((self.clone(), address.token.clone()));
            let config = Arc::new(settings.config(&self.state.config));
            if let Some(live) = &self.state.live_config {
                *live.write().unwrap_or_else(|error| error.into_inner()) = config.clone();
            }
            let mut state = self.state.clone();
            state.config = config.clone();
            let app = app_router(state).merge(control);
            if self.management.lock().await.is_none() {
                private_json(&descriptor(&self.state.config.codex_home), &address)?;
            }
            let (shutdown, receive) = tokio::sync::oneshot::channel();
            let task = tokio::spawn(async move {
                let _ = axum::serve(listener, app)
                    .with_graceful_shutdown(async {
                        let _ = receive.await;
                    })
                    .await;
            });
            *running = Some(Running {
                shutdown,
                task,
                address,
                config,
            });
            self.event(format!("API started on 127.0.0.1:{port}")).await;
            Ok(())
        })
    }

    pub async fn stop(&self) -> Result<()> {
        let mut running = self.running.lock().await;
        if let Some(mut server) = running.take() {
            let _ = server.shutdown.send(());
            if tokio::time::timeout(std::time::Duration::from_secs(3), &mut server.task)
                .await
                .is_err()
            {
                server.task.abort();
            }
            if let Ok(bytes) = fs::read(descriptor(&self.state.config.codex_home)) {
                if serde_json::from_slice::<ControlAddress>(&bytes)
                    .is_ok_and(|a| a.token == server.address.token)
                {
                    let _ = fs::remove_file(descriptor(&self.state.config.codex_home));
                }
            }
            self.event("API stopped").await;
        }
        Ok(())
    }

    pub async fn status(&self) -> Result<Value> {
        let active = self.state.accounts.active_slot().await;
        let mut accounts = vec![];
        for slot in ACCOUNT_SLOTS {
            let file = self.state.accounts.auth_home(slot)?.join("auth.json");
            let auth = crate::login::lib::try_read_auth_json(&file);
            let (email, plan, error) = match auth {
                Ok(auth) => match auth.tokens {
                    Some(tokens) => (
                        tokens.id_token.email.clone(),
                        tokens.id_token.get_chatgpt_plan_type(),
                        None,
                    ),
                    None => (None, None, Some("Subscription credentials required")),
                },
                Err(_) if file.exists() => (
                    None,
                    None,
                    Some("Credentials cannot be read; sign in again"),
                ),
                Err(_) => (None, None, None),
            };
            accounts.push(json!({"slot":slot,"configured":file.is_file(),"active":slot==active && file.is_file(),
                "email":email,"plan":plan,"error":error}));
        }
        let login = self.login_state.lock().await.clone();
        let settings = self.settings.lock().await.clone();
        let desired_port = settings.effective_port()?;
        let (running, port, restart_required) = {
            let server = self.running.lock().await;
            match server.as_ref().filter(|r| !r.task.is_finished()) {
                Some(r) => (
                    true,
                    r.address.port,
                    r.address.port != desired_port,
                ),
                None => (false, desired_port, false),
            }
        };
        let overrides: Vec<&str> = [
            "RELAY_PORT",
            "RELAY_DEFAULT_MODEL",
            "RELAY_REASONING_EFFORT",
            "RELAY_INSTRUCTIONS",
            "RELAY_PARALLEL_TOOL_CALLS",
            "RELAY_API_KEY",
        ]
        .into_iter()
        .filter(|name| std::env::var_os(name).is_some())
        .collect();
        Ok(
            json!({"product":"Praxis Relay","version":env!("CARGO_PKG_VERSION"),"running":running,
            "port":port,"base_url":format!("http://127.0.0.1:{port}/v1"),
            "api_key":self.state.api_key.as_deref(),"auth_directory":self.state.config.codex_home,
            "protocol_version":chat_completions::codex_cli_version(),"accounts":accounts,
            "settings":settings,"restart_required":restart_required,"environment_overrides":overrides,
            "instructions_file":crate::core::config::custom_instructions().map(|_| crate::core::config::instructions_file_path()),
            "login":login,"login_url":if login.pending { crate::login::lib::login_url() } else { None },
            "events":self.events.lock().await.clone()}),
        )
    }

    pub async fn preferences(&self) -> Settings {
        self.settings.lock().await.clone()
    }

    pub async fn configure(&self, patch: Value) -> Result<Value> {
        {
            let mut settings = self.settings.lock().await;
            let next = settings.patched(patch)?;
            private_json(&self.state.config.codex_home.join("settings.json"), &next)?;
            *settings = next;
        }
        if let Some(live) = &self.state.live_config {
            let config = Arc::new(self.settings.lock().await.config(&self.state.config));
            *live.write().unwrap_or_else(|error| error.into_inner()) = config;
        }
        self.status().await
    }

    pub async fn startup_error(&self, error: String) {
        self.event(format!("API startup failed: {error}")).await;
    }

    pub async fn begin_login(self: &Arc<Self>, slot: &str) -> Result<()> {
        self.state.accounts.auth_home(slot)?;
        let mut task = self.login_task.lock().await;
        if task.as_ref().is_some_and(|t| !t.is_finished()) {
            bail!("A login is already in progress; finish or cancel it first");
        }
        crate::login::lib::clear_login_url();
        *self.login_state.lock().await = LoginState {
            pending: true,
            slot: Some(slot.to_string()),
            error: None,
        };
        self.event(format!("Signing in to {slot}; finish in your browser"))
            .await;
        let relay = self.clone();
        let slot = slot.to_string();
        *task = Some(tokio::spawn(async move {
            let result = tokio::time::timeout(
                std::time::Duration::from_secs(300),
                relay.state.accounts.login(&slot),
            )
            .await;
            let error = match result {
                Ok(Ok(())) => None,
                Ok(Err(e)) => Some(format!("{e:#}")),
                Err(_) => Some("Login timed out after five minutes".into()),
            };
            relay.state.limits.invalidate(&slot).await;
            relay
                .event(if error.is_none() {
                    format!("Signed in to {slot}")
                } else {
                    format!("Sign-in failed for {slot}")
                })
                .await;
            *relay.login_state.lock().await = LoginState {
                pending: false,
                slot: Some(slot),
                error,
            };
            crate::login::lib::clear_login_url();
        }));
        Ok(())
    }

    pub async fn cancel_login(&self) {
        if let Some(task) = self.login_task.lock().await.take() {
            task.abort();
            let _ = task.await;
        }
        *self.login_state.lock().await = LoginState::default();
        crate::login::lib::clear_login_url();
    }

    /// Small command vocabulary is shared by Tauri IPC and authenticated local
    /// headless control. Subscription tokens never cross this boundary.
    pub async fn command(
        self: &Arc<Self>,
        op: &str,
        slot: Option<&str>,
        port: Option<u16>,
    ) -> Result<Value> {
        let wanted = || slot.ok_or_else(|| anyhow!("Choose primary or secondary"));
        match op {
            "status" => return self.status().await,
            "start" => self.start().await?,
            "stop" => self.stop().await?,
            "login" => self.begin_login(wanted()?).await?,
            "cancel_login" => self.cancel_login().await,
            "open_login" => {
                let url = crate::login::lib::login_url().context("No login is in progress")?;
                if !url.starts_with("https://auth.openai.com/") {
                    bail!("Unexpected login address");
                }
                #[cfg(windows)]
                {
                    use std::os::windows::process::CommandExt;
                    std::process::Command::new("rundll32.exe")
                        .args(["url.dll,FileProtocolHandler", &url])
                        .creation_flags(0x08000000)
                        .spawn()?;
                }
                #[cfg(target_os = "macos")]
                {
                    std::process::Command::new("open").arg(&url).spawn()?;
                }
                #[cfg(all(unix, not(target_os = "macos")))]
                {
                    std::process::Command::new("xdg-open").arg(&url).spawn()?;
                }
            }
            "logout" => {
                self.cancel_login().await;
                let slots = if slot == Some("all") {
                    ACCOUNT_SLOTS.to_vec()
                } else {
                    vec![wanted()?]
                };
                for slot in slots {
                    self.state.accounts.logout(slot).await?;
                    self.state.limits.invalidate(slot).await;
                    self.event(format!("Signed out of {slot}")).await;
                }
            }
            "switch" => {
                let slot = wanted()?;
                self.state.accounts.switch_to(slot).await?;
                self.event(format!("Active subscription: {slot}")).await;
            }
            "limits" => {
                let slot = wanted()?;
                let account = self.state.accounts.lease_slot(slot).await?;
                return self.state.limits.get_for_account(&account).await;
            }
            "port" => {
                if self.running.lock().await.is_some() {
                    bail!("Stop the API before changing its port");
                }
                let port = port.filter(|p| *p > 0).context("Port must be 1..65535")?;
                return self.configure(json!({"port":port})).await;
            }
            "network" => {
                return Ok(json!({"mode":std::env::var("RELAY_NETWORK_MODE").unwrap_or_else(|_|"system".into()),
                    "proxy_configured":std::env::var("RELAY_PROXY_URL").is_ok_and(|value|!value.is_empty()),"generation_probe":false}));
            }
            "network_check" => {
                let response = self.state.client.get(format!("{}/models", self.state.clone().snapshot().config.chatgpt_base_url.trim_end_matches('/')))
                    .timeout(std::time::Duration::from_secs(15)).send().await?;
                return Ok(json!({"reachable":true,"status":response.status().as_u16(),"generation_probe":false}));
            }
            "models" => {
                let snapshot = self
                    .state
                    .catalog
                    .snapshot(&self.state.accounts, &self.state.client)
                    .await;
                return Ok(
                    json!({"source":snapshot.source.as_str(),"models":snapshot.listed_slugs()}),
                );
            }
            _ => bail!("Unknown command: {op}"),
        }
        self.status().await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn isolated(root: &Path, port: u16) -> Arc<Relay> {
        let config = Arc::new(Config {
            codex_home: root.to_path_buf(),
            chatgpt_base_url: "https://invalid.example".into(),
            model: "test".into(),
            user_instructions: None,
            reasoning_effort: None,
            instructions_mode: None,
            parallel_tool_calls: true,
        });
        let lock = fs::File::create(root.join("lock")).unwrap();
        Arc::new(Relay {
            state: AppState {
                live_config: Some(Arc::new(std::sync::RwLock::new(config.clone()))),
                api_key: Some(Arc::from("test-relay-client-key")),
                config,
                accounts: AccountRouter::open(root).await.unwrap(),
                client: reqwest::Client::new(),
                limits: LimitsCache::default(),
                catalog: ModelCatalog::from_env(),
            },
            running: Mutex::new(None),
            login_task: Mutex::new(None),
            login_state: Arc::new(Mutex::new(LoginState::default())),
            events: Arc::new(Mutex::new(VecDeque::new())),
            settings: Mutex::new(Settings {
                port,
                ..Settings::default()
            }),
            management: Mutex::new(None),
            _lock: lock,
        })
    }

    #[tokio::test]
    async fn settings_persist_and_update_next_request_without_restart() {
        let root = tempfile::tempdir().unwrap();
        let relay = isolated(root.path(), 0).await;
        relay.start().await.unwrap();
        let port = relay.status().await.unwrap()["port"].as_u64().unwrap() as u16;
        let result = relay.configure(json!({"port":port,"defaults":{"model":"configured-model","reasoning":"high","instructions_mode":"minimal","instructions":"Owner instructions"},"labels":{"primary":"Work"}})).await.unwrap();
        assert_eq!(result["restart_required"], false);
        assert_eq!(relay.state.clone().snapshot().config.model, "configured-model");
        assert_eq!(relay.state.clone().snapshot().config.reasoning_effort.as_deref(), Some("high"));
        assert_ne!(
            relay.running.lock().await.as_ref().unwrap().config.model,
            "configured-model"
        );
        let persisted = fs::read(root.path().join("settings.json")).unwrap();
        assert!(relay.configure(json!({"port":0})).await.is_err());
        assert_eq!(
            fs::read(root.path().join("settings.json")).unwrap(),
            persisted
        );
        relay.stop().await.unwrap();
        relay.start().await.unwrap();
        assert_eq!(relay.status().await.unwrap()["restart_required"], false);
        {
            let running = relay.running.lock().await;
            let config = &running.as_ref().unwrap().config;
            assert_eq!(config.model, "configured-model");
            assert_eq!(config.reasoning_effort.as_deref(), Some("high"));
            assert!(config.minimal_instructions());
            assert!(config
                .user_instructions
                .as_deref()
                .unwrap()
                .contains("Owner instructions"));
        }
        let value = relay
            .configure(json!({"appearance":{"theme":"dark"}}))
            .await
            .unwrap();
        assert_eq!(value["restart_required"], false);
        let saved = Settings::load(root.path()).unwrap();
        assert_eq!(saved.labels.primary, "Work");
        assert_eq!(saved.defaults.model, "configured-model");
        relay.stop().await.unwrap();
        assert!(!root.path().join("auth.json").exists());
    }

    #[tokio::test]
    async fn model_and_image_requests_require_the_installation_key() {
        let root = tempfile::tempdir().unwrap();
        let relay = isolated(root.path(), 0).await;
        relay.start().await.unwrap();
        let address: ControlAddress =
            serde_json::from_slice(&fs::read(descriptor(root.path())).unwrap()).unwrap();
        let base = format!("http://127.0.0.1:{}", address.port);
        let client = reqwest::Client::new();
        for route in [
            "/v1/chat/completions",
            "/v1/images/generations",
            "/v1/images/edits",
        ] {
            for key in [None, Some("relay"), Some("wrong-key")] {
                let mut request = client.post(format!("{base}{route}")).json(&json!({}));
                if let Some(key) = key {
                    request = request.bearer_auth(key);
                }
                assert_eq!(request.send().await.unwrap().status(), 401, "{route}");
            }
        }
        let image_catalog = format!("{base}/v1/images/models");
        assert_eq!(
            client.get(&image_catalog).send().await.unwrap().status(),
            401
        );
        assert_eq!(
            client
                .get(&image_catalog)
                .bearer_auth("test-relay-client-key")
                .send()
                .await
                .unwrap()
                .status(),
            200
        );
        relay.stop().await.unwrap();
    }

    #[tokio::test]
    async fn headless_controls_require_local_secret_and_reject_browser_origins() {
        let root = tempfile::tempdir().unwrap();
        let relay = isolated(root.path(), 0).await;
        relay.start().await.unwrap();
        let address: ControlAddress =
            serde_json::from_slice(&fs::read(descriptor(root.path())).unwrap()).unwrap();
        let url = format!("http://127.0.0.1:{}/_relay/control", address.port);
        let client = reqwest::Client::new();
        let body = json!({"op":"status","slot":null,"port":null});
        assert_eq!(
            client.post(&url).json(&body).send().await.unwrap().status(),
            403
        );
        assert_eq!(
            client
                .post(&url)
                .header("x-relay-control", "wrong")
                .json(&body)
                .send()
                .await
                .unwrap()
                .status(),
            403
        );
        assert_eq!(
            client
                .post(&url)
                .header("x-relay-control", &address.token)
                .header("origin", "https://untrusted.example")
                .json(&body)
                .send()
                .await
                .unwrap()
                .status(),
            403
        );
        let response = client
            .post(&url)
            .header("x-relay-control", &address.token)
            .json(&body)
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), 200);
        let value: Value = response.json().await.unwrap();
        assert_eq!(value["running"], true);
        assert!(value["accounts"]
            .as_array()
            .unwrap()
            .iter()
            .all(|a| a["configured"] == false));
        assert!(value.get("tokens").is_none());
        relay.stop().await.unwrap();
        assert!(!descriptor(root.path()).exists());
        assert!(
            tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, address.port))
                .await
                .is_ok()
        );
    }

    #[tokio::test]
    async fn occupied_port_is_an_actionable_error_and_does_not_claim_running() {
        let root = tempfile::tempdir().unwrap();
        let busy = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let relay = isolated(root.path(), busy.local_addr().unwrap().port()).await;
        let error = relay.start().await.unwrap_err().to_string();
        assert!(error.contains("already in use"));
        assert_eq!(relay.status().await.unwrap()["running"], false);
        assert!(!descriptor(root.path()).exists());
    }

    #[tokio::test]
    async fn management_survives_api_stop_and_restarts_same_instance() {
        let root = tempfile::tempdir().unwrap();
        let relay = isolated(root.path(), 0).await;
        relay.start_management().await.unwrap();
        let before = fs::read(descriptor(root.path())).unwrap();
        relay.start().await.unwrap();
        relay.stop().await.unwrap();
        assert_eq!(fs::read(descriptor(root.path())).unwrap(), before);
        let address: ControlAddress = serde_json::from_slice(&before).unwrap();
        let response = reqwest::Client::new()
            .post(format!("http://127.0.0.1:{}/_relay/control", address.port))
            .header("x-relay-control", address.token)
            .json(&json!({"op":"start"}))
            .send().await.unwrap();
        assert!(response.status().is_success());
        assert_eq!(response.json::<Value>().await.unwrap()["running"], true);
        relay.stop().await.unwrap();
        relay.shutdown_management().await;
        assert!(!descriptor(root.path()).exists());
    }
}

#[derive(Deserialize, Serialize)]
struct Command {
    op: String,
    slot: Option<String>,
    port: Option<u16>,
    settings: Option<Value>,
}
async fn control_handler(
    State((relay, token)): State<(Arc<Relay>, String)>,
    headers: HeaderMap,
    Json(command): Json<Command>,
) -> (StatusCode, Json<Value>) {
    if headers.contains_key("origin")
        || headers.get("x-relay-control").and_then(|v| v.to_str().ok()) != Some(&token)
    {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"error":"Local control authorization required"})),
        );
    }
    if command.op == "stop" {
        tokio::spawn(async move {
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
            let _ = relay.stop().await;
        });
        return (StatusCode::OK, Json(json!({"stopping":true})));
    }
    let result = if command.op == "configure" {
        relay
            .configure(command.settings.unwrap_or(Value::Null))
            .await
    } else {
        relay
            .command(&command.op, command.slot.as_deref(), command.port)
            .await
    };
    match result {
        Ok(value) => (StatusCode::OK, Json(value)),
        Err(e) => (
            StatusCode::CONFLICT,
            Json(json!({"error":format!("{e:#}")})),
        ),
    }
}

pub async fn cli() -> Result<()> {
    let _ = tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "praxis_relay=info".into()),
        )
        .with_writer(std::io::stderr)
        .try_init();
    let args: Vec<String> = std::env::args().skip(1).collect();
    let action = args.first().map(String::as_str).unwrap_or("serve");
    if matches!(action, "version" | "--version" | "-V") {
        println!(
            "Praxis Relay {} (independent relay; Codex-compatible upstream protocol)",
            env!("CARGO_PKG_VERSION")
        );
        return Ok(());
    }
    if matches!(action, "help" | "--help" | "-h") {
        println!("Praxis Relay {}\nHeadless: serve [--headless]\nControls: status | login [primary|secondary] | cancel_login | logout primary|secondary|all | switch primary|secondary | limits primary|secondary | models | stop\nSettings: configure <patch.json> (works offline and against a running process)\nConfig: RELAY_AUTH_DIR, RELAY_PORT, RELAY_API_KEY, RELAY_PYTHON, RELAY_CODEX_VERSION\nNative desktop: praxis-relay-desktop",env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    if !matches!(
        action,
        "serve"
            | "server"
            | "run"
            | "--headless"
            | "status"
            | "login"
            | "cancel_login"
            | "logout"
            | "switch"
            | "limits"
            | "stop"
            | "configure"
            | "models"
    ) {
        bail!("Unknown command {action}; use --help");
    }
    let serve = matches!(action, "serve" | "server" | "run" | "--headless");
    let settings = if action == "configure" {
        Some(serde_json::from_str::<Value>(&fs::read_to_string(
            args.get(1).context("Use configure <patch.json>")?,
        )?)?)
    } else {
        None
    };
    let slot = args
        .get(1)
        .filter(|a| !a.starts_with('-'))
        .cloned()
        .or_else(|| (action == "login").then(|| "primary".into()));
    if !serve {
        let root = crate::core::config::auth_root()?;
        if let Ok(bytes) = fs::read(descriptor(&root)) {
            let address: ControlAddress = serde_json::from_slice(&bytes)?;
            let response = reqwest::Client::new()
                .post(format!("http://127.0.0.1:{}/_relay/control", address.port))
                .header("x-relay-control", address.token)
                .json(&Command {
                    op: action.into(),
                    slot,
                    port: None,
                    settings,
                })
                .timeout(std::time::Duration::from_secs(30))
                .send()
                .await?;
            let status = response.status();
            let value: Value = response.json().await?;
            println!("{}", serde_json::to_string_pretty(&value)?);
            if !status.is_success() {
                bail!("Control command failed");
            }
            return Ok(());
        }
    }
    let relay = Relay::new().await?;
    if serve {
        relay.start().await?;
        println!("Praxis Relay {} · {}\nFully headless. Use a second terminal for controls; Ctrl+C stops this process.",env!("CARGO_PKG_VERSION"),relay.status().await?["base_url"].as_str().unwrap_or(""));
        tokio::select! {
            _=tokio::signal::ctrl_c()=>{},
            _=async { while relay.running.lock().await.is_some() { tokio::time::sleep(std::time::Duration::from_millis(250)).await; } }=>{}
        }
        relay.cancel_login().await;
        relay.stop().await?;
    } else {
        let value = if action == "configure" {
            relay.configure(settings.unwrap()).await?
        } else {
            relay.command(action, slot.as_deref(), None).await?
        };
        if action == "login" {
            while relay.login_state.lock().await.pending {
                tokio::time::sleep(std::time::Duration::from_millis(100)).await;
            }
            let login = relay.login_state.lock().await.clone();
            if let Some(error) = login.error {
                bail!("{error}");
            }
            println!("Signed in to {}", slot.as_deref().unwrap_or("primary"));
        } else {
            println!("{}", serde_json::to_string_pretty(&value)?);
        }
    }
    Ok(())
}
