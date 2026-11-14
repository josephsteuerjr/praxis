//! One outbound route for generation, discovery, limits and token refresh.
//! Settings are private; status never returns proxy credentials.
use anyhow::{bail, Result};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{path::PathBuf, sync::{OnceLock, RwLock}, time::Duration};

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Settings { pub proxy_url: Option<String> }
#[derive(Clone)]
struct Snapshot { settings: Settings, client: Client, path: PathBuf }
static CURRENT: OnceLock<RwLock<Snapshot>> = OnceLock::new();

fn build(settings: &Settings) -> Result<Client> {
    let mut builder = Client::builder().tcp_keepalive(Duration::from_secs(30))
        .pool_idle_timeout(Duration::from_secs(90)).redirect(reqwest::redirect::Policy::none());
    if let Some(raw) = &settings.proxy_url {
        let url = reqwest::Url::parse(raw).map_err(|_| anyhow::anyhow!("Invalid proxy URL"))?;
        if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none()
            || url.query().is_some() || url.fragment().is_some() {
            bail!("Proxy must be an HTTP(S) URL without query or fragment");
        }
        builder = builder.no_proxy().proxy(reqwest::Proxy::all(raw)
            .map_err(|_| anyhow::anyhow!("Invalid proxy configuration"))?);
    }
    builder.build().map_err(|_| anyhow::anyhow!("Cannot initialize outbound route"))
}

pub fn init(home: &std::path::Path) -> Result<()> {
    let path = std::env::var_os("RELAY_NETWORK_FILE").map(PathBuf::from)
        .unwrap_or_else(|| home.join("network.json"));
    let settings = match std::fs::read(&path) {
        Ok(bytes) => serde_json::from_slice(&bytes).map_err(|_| anyhow::anyhow!("Invalid network settings file"))?,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Settings::default(),
        Err(_) => bail!("Cannot read network settings file"),
    };
    let client = build(&settings)?;
    let snapshot = Snapshot {settings, client, path};
    if let Some(lock) = CURRENT.get() { *lock.write().map_err(|_| anyhow::anyhow!("Network state unavailable"))? = snapshot; }
    else { let _ = CURRENT.set(RwLock::new(snapshot)); }
    Ok(())
}

pub fn client() -> Result<Client> {
    if let Some(lock) = CURRENT.get() {
        return Ok(lock.read().map_err(|_| anyhow::anyhow!("Network state unavailable"))?.client.clone());
    }
    // Library/test callers without server startup retain the platform route.
    build(&Settings::default())
}

pub fn status() -> Result<Value> {
    let state = CURRENT.get().ok_or_else(|| anyhow::anyhow!("Network state unavailable"))?
        .read().map_err(|_| anyhow::anyhow!("Network state unavailable"))?;
    Ok(json!({"proxy_configured":state.settings.proxy_url.is_some(),
        "route":if state.settings.proxy_url.is_some() {"configured_proxy"} else {"platform_default"},
        "preflight":"disabled_server_deployment", "generation_probe":false}))
}

pub fn update(mut settings: Settings) -> Result<Value> {
    settings.proxy_url = settings.proxy_url.map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
    let client = build(&settings)?;
    let lock = CURRENT.get().ok_or_else(|| anyhow::anyhow!("Network state unavailable"))?;
    let mut state = lock.write().map_err(|_| anyhow::anyhow!("Network state unavailable"))?;
    let parent = state.path.parent().ok_or_else(|| anyhow::anyhow!("Invalid network settings path"))?;
    std::fs::create_dir_all(parent)?;
    let mut file = tempfile::NamedTempFile::new_in(parent)?;
    #[cfg(unix)] {
        use std::os::unix::fs::PermissionsExt;
        file.as_file().set_permissions(std::fs::Permissions::from_mode(0o600))?;
    }
    use std::io::Write;
    file.write_all(&serde_json::to_vec(&settings)?)?;
    file.as_file().sync_all()?;
    file.persist(&state.path).map_err(|_| anyhow::anyhow!("Cannot save network settings"))?;
    state.settings = settings; state.client = client;
    drop(state);
    status()
}

/// Fresh, non-generating request, using the SAME client/account as the next POST.
/// This proves endpoint reachability, not geographic eligibility or future immunity from bans.
#[derive(Debug, thiserror::Error)]
#[error("backend_unavailable: preflight HTTP {0}; generation not sent")]
// Retained compatibility/test helper: this server deployment deliberately does
// not probe before generation. Do not re-enable it to satisfy dead-code lint.
#[cfg_attr(not(test), allow(dead_code))]
pub struct ProbeStatus(pub u16);
#[cfg_attr(not(test), allow(dead_code))]
pub async fn preflight(client: &Client, token: &str, account: &str) -> Result<()> {
    #[cfg(test)]
    { if TEST_PROBE_URL.lock().unwrap().is_none() { return Ok(()); } }
    let response = client.get(probe_url())
        .query(&[("client_version", crate::core::chat_completions::codex_cli_version())])
        .bearer_auth(token).header("ChatGPT-Account-ID",account)
        .header("User-Agent",crate::core::chat_completions::codex_user_agent())
        .header("originator",crate::core::chat_completions::CODEX_ORIGINATOR)
        .timeout(Duration::from_secs(15)).send().await
        .map_err(|_| anyhow::anyhow!("backend_unavailable: preflight transport failed; generation not sent"))?;
    if !response.status().is_success() {
        return Err(ProbeStatus(response.status().as_u16()).into());
    }
    Ok(())
}
#[cfg(test)]
pub(crate) static TEST_PROBE_URL: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);
#[cfg_attr(not(test), allow(dead_code))]
fn probe_url() -> String {
    #[cfg(test)] { return TEST_PROBE_URL.lock().unwrap().clone().unwrap_or_default(); }
    #[cfg(not(test))] { "https://chatgpt.com/backend-api/codex/models".to_string() }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn invalid_proxy_does_not_build_or_echo_secret() {
        let err=build(&Settings{proxy_url:Some("garbage://user:secret@host".into())}).unwrap_err();
        assert!(!err.to_string().contains("secret"));
    }
    #[test] fn explicit_proxy_builds_without_connecting() {
        assert!(build(&Settings{proxy_url:Some("http://user:secret@127.0.0.1:9".into())}).is_ok());
    }
    #[tokio::test] async fn unreachable_preflight_is_not_a_generation() {
        *TEST_PROBE_URL.lock().unwrap() = Some("http://127.0.0.1:9/preflight".into());
        let result=preflight(&build(&Settings::default()).unwrap(),"fixture","fixture").await;
        *TEST_PROBE_URL.lock().unwrap() = None;
        assert!(result.unwrap_err().to_string().contains("generation not sent"));
    }
}
