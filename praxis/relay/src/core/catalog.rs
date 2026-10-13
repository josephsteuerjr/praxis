//! Live model catalog: what the Codex backend says it serves today, not what a
//! constant said it served on the day someone last edited it.
//!
//! `GET https://chatgpt.com/backend-api/codex/models?client_version=X` answers
//! with every model the account can use: slug, visibility (`list` / `hide`),
//! context window, input modalities, reasoning levels, priority. The relay asks
//! once per `RELAY_MODELS_TTL` seconds (default 600), serves `/v1/models` from
//! the answer and validates chat requests against it. A request for a slug the
//! cached answer does not know forces one early refresh, so a model released an
//! hour ago is usable the moment someone asks for it.
//!
//! Degradation is explicit, never silent: when the backend cannot be asked (no
//! subscription, network, 5xx) the last good answer keeps serving; with no
//! answer ever, the static [`FALLBACK_MODELS`] list stands in. `/health`
//! reports which of the three is live.
//!
//! Why this exists: the static list was edited for 5.6 (three times), for
//! Spark, and would have been edited again for gpt-6-astra -- a rebuild and a
//! deploy each time for a fact the backend already publishes. Note the backend
//! gates the catalog on the client version it sees: astra carries
//! `minimal_client_version: 0.153.0`, so the version the relay presents
//! (`RELAY_CODEX_VERSION`) decides what the catalog lists.

use anyhow::{anyhow, bail, Context, Result};
use reqwest::Client;
use serde_json::{json, Value};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;
use tracing::{info, warn};

use crate::core::account_router::AccountRouter;
use crate::core::chat_completions::{codex_cli_version, codex_user_agent, CODEX_ORIGINATOR};
use crate::core::models::{Model, ModelList, FALLBACK_MODELS};

const CODEX_MODELS_URL: &str = "https://chatgpt.com/backend-api/codex/models";
pub const RELAY_MODELS_TTL_ENV: &str = "RELAY_MODELS_TTL";
pub const RELAY_MODEL_DISCOVERY_ENV: &str = "RELAY_MODEL_DISCOVERY";
pub const RELAY_EXTRA_MODELS_ENV: &str = "RELAY_EXTRA_MODELS";
const DEFAULT_TTL: Duration = Duration::from_secs(600);
/// After a refresh attempt (failed or not) upstream is left alone at least this long,
/// so a burst of requests for a bogus slug cannot become a burst of catalog fetches.
const RETRY_COOLDOWN: Duration = Duration::from_secs(30);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(20);
/// The live answer is ~370 KiB (each model carries its full base instructions).
const MAX_CATALOG_BODY_BYTES: usize = 8 * 1024 * 1024;

/// One model as the backend describes it, reduced to what the relay uses.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CatalogModel {
    pub slug: String,
    pub display_name: Option<String>,
    /// `visibility == "list"`: advertised on `/v1/models`. Hidden slugs are
    /// still accepted when asked for by name -- the backend serves them.
    pub listed: bool,
    pub priority: i64,
    pub context_window: Option<u64>,
    pub max_context_window: Option<u64>,
    pub input_modalities: Vec<String>,
    pub reasoning_efforts: Vec<String>,
    pub default_reasoning_effort: Option<String>,
    pub minimal_client_version: Option<String>,
    pub upgrade: Option<String>,
}

impl CatalogModel {
    /// A slug with no metadata: what the fallback list and RELAY_EXTRA_MODELS produce.
    fn bare(slug: &str, priority: i64) -> Self {
        CatalogModel {
            slug: slug.to_string(),
            display_name: None,
            listed: true,
            priority,
            context_window: None,
            max_context_window: None,
            input_modalities: Vec::new(),
            reasoning_efforts: Vec::new(),
            default_reasoning_effort: None,
            minimal_client_version: None,
            upgrade: None,
        }
    }
}

/// Where the models being served came from.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CatalogSource {
    /// A backend answer younger than the TTL.
    Live,
    /// A backend answer older than the TTL that could not be refreshed.
    Stale,
    /// No backend answer at all: [`FALLBACK_MODELS`] (+ extras).
    Fallback,
    /// Discovery switched off by `RELAY_MODEL_DISCOVERY=off`.
    Static,
}

impl CatalogSource {
    pub fn as_str(self) -> &'static str {
        match self {
            CatalogSource::Live => "live",
            CatalogSource::Stale => "stale",
            CatalogSource::Fallback => "fallback",
            CatalogSource::Static => "static",
        }
    }
}

/// The catalog as of one moment: models plus their provenance.
#[derive(Clone, Debug)]
pub struct CatalogSnapshot {
    pub models: Vec<CatalogModel>,
    pub source: CatalogSource,
}

impl CatalogSnapshot {
    pub fn find(&self, slug: &str) -> Option<&CatalogModel> {
        self.models.iter().find(|m| m.slug == slug)
    }

    pub fn listed_slugs(&self) -> Vec<String> {
        self.models
            .iter()
            .filter(|m| m.listed)
            .map(|m| m.slug.clone())
            .collect()
    }
}

struct CatalogState {
    /// Empty until the first successful fetch.
    models: Vec<CatalogModel>,
    fetched: Option<Instant>,
    last_attempt: Option<Instant>,
    last_error: Option<String>,
}

#[derive(Clone)]
pub struct ModelCatalog {
    inner: Arc<Mutex<CatalogState>>,
    ttl: Duration,
    discovery: bool,
    extra: Vec<String>,
}

impl ModelCatalog {
    /// Configure from the environment:
    /// * `RELAY_MODELS_TTL` -- seconds between catalog refreshes (default 600);
    /// * `RELAY_MODEL_DISCOVERY` -- `off`/`0`/`false` serves the static list only;
    /// * `RELAY_EXTRA_MODELS` -- comma-separated slugs always accepted and advertised
    ///   on top of whatever the catalog says (a slug the backend hides, or one it
    ///   lists only for a client version the relay does not claim).
    pub fn from_env() -> Self {
        let ttl = std::env::var(RELAY_MODELS_TTL_ENV)
            .ok()
            .and_then(|raw| raw.trim().parse::<u64>().ok())
            .map(Duration::from_secs)
            .unwrap_or(DEFAULT_TTL);
        let discovery = !matches!(
            std::env::var(RELAY_MODEL_DISCOVERY_ENV)
                .ok()
                .map(|v| v.trim().to_ascii_lowercase())
                .as_deref(),
            Some("off") | Some("0") | Some("false") | Some("no")
        );
        let extra = parse_extra_models(&std::env::var(RELAY_EXTRA_MODELS_ENV).unwrap_or_default());
        Self::with(Vec::new(), ttl, discovery, extra)
    }

    /// The static list only, never a network call: what tests and
    /// `RELAY_MODEL_DISCOVERY=off` deployments run on.
    #[cfg_attr(not(test), allow(dead_code))]
    pub fn static_only() -> Self {
        Self::with(Vec::new(), DEFAULT_TTL, false, Vec::new())
    }

    fn with(models: Vec<CatalogModel>, ttl: Duration, discovery: bool, extra: Vec<String>) -> Self {
        let fetched = if models.is_empty() {
            None
        } else {
            Some(Instant::now())
        };
        ModelCatalog {
            inner: Arc::new(Mutex::new(CatalogState {
                models,
                fetched,
                last_attempt: None,
                last_error: None,
            })),
            ttl,
            discovery,
            extra,
        }
    }

    /// The current catalog, refreshed first when older than the TTL.
    pub async fn snapshot(&self, router: &AccountRouter, client: &Client) -> CatalogSnapshot {
        self.snapshot_inner(router, client, false).await
    }

    /// Is `model` something this relay will forward? Listed and hidden catalog
    /// slugs and RELAY_EXTRA_MODELS all count. A miss forces one early refresh
    /// (rate-limited) so a slug released after the last fetch is not refused
    /// for the rest of the TTL.
    pub async fn is_known(&self, model: &str, router: &AccountRouter, client: &Client) -> bool {
        if self.snapshot(router, client).await.find(model).is_some() {
            return true;
        }
        if !self.discovery {
            return false;
        }
        self.snapshot_inner(router, client, true)
            .await
            .find(model)
            .is_some()
    }

    /// The `/v1/models` document: listed models in backend priority order,
    /// each carrying the metadata the backend published for it.
    pub async fn model_list(&self, router: &AccountRouter, client: &Client) -> ModelList {
        let snapshot = self.snapshot(router, client).await;
        model_list_from(&snapshot)
    }

    /// For `/health`: which source is serving and how old it is. No slugs -- the
    /// list has its own endpoint.
    pub async fn status(&self) -> Value {
        let guard = self.inner.lock().await;
        let source = self.source_of(&guard);
        json!({
            "source": source.as_str(),
            "models": if guard.models.is_empty() { FALLBACK_MODELS.len() } else { guard.models.len() },
            "extra_models": self.extra.len(),
            "fetched_seconds_ago": guard.fetched.map(|at| at.elapsed().as_secs()),
            "ttl_seconds": self.ttl.as_secs(),
            "last_error": guard.last_error,
        })
    }

    fn source_of(&self, state: &CatalogState) -> CatalogSource {
        if !self.discovery {
            CatalogSource::Static
        } else if state.models.is_empty() {
            CatalogSource::Fallback
        } else if state.fetched.is_some_and(|at| at.elapsed() < self.ttl) {
            CatalogSource::Live
        } else {
            CatalogSource::Stale
        }
    }

    async fn snapshot_inner(
        &self,
        router: &AccountRouter,
        client: &Client,
        force: bool,
    ) -> CatalogSnapshot {
        // The lock is held across the fetch on purpose: a burst of callers
        // becomes one upstream request, the rest read the answer.
        let mut guard = self.inner.lock().await;
        if self.discovery {
            let expired = guard.fetched.is_none_or(|at| at.elapsed() >= self.ttl);
            let cooling = guard
                .last_attempt
                .is_some_and(|at| at.elapsed() < RETRY_COOLDOWN);
            if (expired || force) && !cooling {
                guard.last_attempt = Some(Instant::now());
                match fetch_catalog(router, client).await {
                    Ok(models) => {
                        info!(
                            "model catalog refreshed: {} models ({} listed) for client {}",
                            models.len(),
                            models.iter().filter(|m| m.listed).count(),
                            codex_cli_version()
                        );
                        guard.models = models;
                        guard.fetched = Some(Instant::now());
                        guard.last_error = None;
                    }
                    Err(error) => {
                        warn!("model catalog refresh failed: {}", error);
                        guard.last_error = Some(error.to_string());
                    }
                }
            }
        }
        let source = self.source_of(&guard);
        let base: Vec<CatalogModel> = if guard.models.is_empty() {
            fallback_models()
        } else {
            guard.models.clone()
        };
        drop(guard);
        CatalogSnapshot {
            models: with_extras(base, &self.extra),
            source,
        }
    }
}

/// The static safety net as catalog entries, in the constant's order.
fn fallback_models() -> Vec<CatalogModel> {
    FALLBACK_MODELS
        .iter()
        .enumerate()
        .map(|(index, slug)| CatalogModel::bare(slug, index as i64))
        .collect()
}

/// Append RELAY_EXTRA_MODELS that the catalog does not already carry.
fn with_extras(mut models: Vec<CatalogModel>, extra: &[String]) -> Vec<CatalogModel> {
    let mut next_priority = models.iter().map(|m| m.priority).max().unwrap_or(0) + 1;
    for slug in extra {
        if models.iter().any(|m| &m.slug == slug) {
            continue;
        }
        models.push(CatalogModel::bare(slug, next_priority));
        next_priority += 1;
    }
    models
}

/// Trim, drop empties, drop repeats, keep order -- the whole RELAY_EXTRA_MODELS contract.
pub fn parse_extra_models(raw: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for slug in raw.split(',') {
        let slug = slug.trim();
        if slug.is_empty() || out.iter().any(|seen| seen == slug) {
            continue;
        }
        out.push(slug.to_string());
    }
    out
}

/// The reasoning effort to send for a model whose supported levels the catalog
/// lists.
///
/// Measured 2026-09-04: gpt-6-astra answers `400 unsupported_value` to
/// `reasoning.effort: "none"` (and `"minimal"`); its levels are low..max. The
/// relay's default effort is `none`, so every astra call failed once and was
/// rescued by the conservative retry -- which drops the knobs *and* swaps the
/// 350-character instructions for the full Codex preamble: 5016 prompt tokens
/// for a "pong" against 88 through terra. Clamping here, before anything goes
/// upstream, keeps both the call count and the instructions honest.
///
/// Rules: no known levels -> pass through untouched; a supported value stays;
/// a value on the ladder but unsupported -> the nearest supported level above
/// it, else the highest supported; a value not on the ladder at all -> the
/// model's own default, else its lowest level. `None` (no effort asked
/// anywhere) stays `None` -- the backend default is the model's own.
pub fn clamp_effort(
    requested: Option<&str>,
    supported: &[String],
    model_default: Option<&str>,
) -> Option<String> {
    let requested = requested?;
    if supported.is_empty() || supported.iter().any(|s| s == requested) {
        return Some(requested.to_string());
    }
    let ladder = crate::core::chat_completions::REASONING_EFFORTS;
    let rank = |effort: &str| ladder.iter().position(|e| *e == effort);
    let mut ranked: Vec<(usize, &str)> = supported
        .iter()
        .filter_map(|s| rank(s).map(|r| (r, s.as_str())))
        .collect();
    ranked.sort_unstable();
    match rank(requested) {
        Some(want) => ranked
            .iter()
            .find(|(r, _)| *r >= want)
            .or_else(|| ranked.last())
            .map(|(_, s)| (*s).to_string()),
        None => model_default
            .filter(|d| supported.iter().any(|s| s == d))
            .map(str::to_string)
            .or_else(|| ranked.first().map(|(_, s)| (*s).to_string())),
    }
    .or_else(|| Some(requested.to_string()))
}

async fn fetch_catalog(router: &AccountRouter, client: &Client) -> Result<Vec<CatalogModel>> {
    // lease_any, as limits does: a parked active slot must not blind discovery
    // while a healthy standby could answer.
    let account = router
        .lease_any()
        .await
        .context("no subscription available to read the model catalog")?;
    let response = client
        .get(CODEX_MODELS_URL)
        .query(&[("client_version", codex_cli_version())])
        .bearer_auth(&account.access_token)
        .header("ChatGPT-Account-ID", &account.account_id)
        .header(reqwest::header::USER_AGENT, codex_user_agent())
        .header("originator", CODEX_ORIGINATOR)
        .header(reqwest::header::ACCEPT, "application/json")
        .timeout(REQUEST_TIMEOUT)
        .send()
        .await
        .context("model catalog request failed")?;
    let status = response.status();
    if !status.is_success() {
        // The body can carry account details.  Never reflect it.
        bail!("model catalog endpoint returned {status}");
    }
    if response
        .content_length()
        .is_some_and(|length| length > MAX_CATALOG_BODY_BYTES as u64)
    {
        bail!("model catalog response is unexpectedly large");
    }
    let body = response
        .bytes()
        .await
        .context("failed to read model catalog response")?;
    if body.len() > MAX_CATALOG_BODY_BYTES {
        bail!("model catalog response is unexpectedly large");
    }
    let raw: Value = serde_json::from_slice(&body).context("invalid model catalog JSON")?;
    parse_catalog(&raw)
}

fn string_list(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

/// Reduce the backend document to [`CatalogModel`]s, sorted by the backend's
/// own priority (ties keep document order). Entries without a slug are skipped;
/// an answer with no usable entry is an error, not an empty catalog -- an empty
/// catalog would refuse every request while looking healthy.
pub fn parse_catalog(raw: &Value) -> Result<Vec<CatalogModel>> {
    let items = raw
        .get("models")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow!("model catalog payload has no `models` array"))?;
    let mut out: Vec<CatalogModel> = Vec::new();
    for item in items {
        let Some(slug) = item
            .get("slug")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|slug| !slug.is_empty())
        else {
            continue;
        };
        if out.iter().any(|m| m.slug == slug) {
            continue;
        }
        let listed = item
            .get("visibility")
            .and_then(Value::as_str)
            .is_none_or(|v| v == "list");
        out.push(CatalogModel {
            slug: slug.to_string(),
            display_name: item
                .get("display_name")
                .and_then(Value::as_str)
                .map(str::to_string),
            listed,
            priority: item
                .get("priority")
                .and_then(Value::as_i64)
                .unwrap_or(i64::MAX),
            context_window: item.get("context_window").and_then(Value::as_u64),
            max_context_window: item.get("max_context_window").and_then(Value::as_u64),
            input_modalities: string_list(item.get("input_modalities")),
            reasoning_efforts: item
                .get("supported_reasoning_levels")
                .and_then(Value::as_array)
                .map(|levels| {
                    levels
                        .iter()
                        .filter_map(|level| level.get("effort").and_then(Value::as_str))
                        .map(str::to_string)
                        .collect()
                })
                .unwrap_or_default(),
            default_reasoning_effort: item
                .get("default_reasoning_level")
                .and_then(Value::as_str)
                .map(str::to_string),
            minimal_client_version: item
                .get("minimal_client_version")
                .and_then(Value::as_str)
                .map(str::to_string),
            upgrade: item
                .get("upgrade")
                .and_then(|u| u.get("model"))
                .and_then(Value::as_str)
                .map(str::to_string),
        });
    }
    if out.is_empty() {
        bail!("model catalog payload lists no models");
    }
    out.sort_by_key(|m| m.priority);
    Ok(out)
}

/// Build the OpenAI-shaped `/v1/models` document from a snapshot: listed models
/// only, in priority order, with the backend's metadata beside the four
/// standard fields. `meta.n_ctx_train` / `context_length` are the llama.cpp
/// spellings that local-model client lanes read.
pub fn model_list_from(snapshot: &CatalogSnapshot) -> ModelList {
    let created = chrono::Utc::now().timestamp();
    ModelList {
        object: "list".to_string(),
        data: snapshot
            .models
            .iter()
            .filter(|m| m.listed)
            .map(|m| Model {
                id: m.slug.clone(),
                object: "model".to_string(),
                created,
                owned_by: "chatgpt".to_string(),
                display_name: m.display_name.clone(),
                context_window: m.context_window,
                context_length: m.context_window,
                max_context_window: m.max_context_window,
                meta: m
                    .context_window
                    .map(|window| json!({ "n_ctx_train": window })),
                input_modalities: m.input_modalities.clone(),
                reasoning_efforts: m.reasoning_efforts.clone(),
                default_reasoning_effort: m.default_reasoning_effort.clone(),
                upgrade: m.upgrade.clone(),
            })
            .collect(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Value {
        json!({
            "models": [
                {"slug": "gpt-reserve", "display_name": "GPT-Reserve", "visibility": "hide", "priority": 3,
                 "context_window": 272000, "input_modalities": ["text", "image"],
                 "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}]},
                {"slug": "gpt-5.6-sol", "display_name": "GPT-5.6-Sol", "visibility": "list", "priority": 6,
                 "context_window": 272000, "max_context_window": 872000,
                 "input_modalities": ["text", "image"], "default_reasoning_level": "medium",
                 "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}, {"effort": "high"}, {"effort": "ultra"}]},
                {"slug": "gpt-6-astra", "display_name": "GPT-6-Astra", "visibility": "list", "priority": 1,
                 "context_window": 272000, "minimal_client_version": "0.153.0",
                 "input_modalities": ["text", "image"], "upgrade": null,
                 "supported_reasoning_levels": [{"effort": "low"}, {"effort": "max"}]},
                {"slug": "gpt-5.4", "visibility": "list", "priority": 16, "upgrade": {"model": "gpt-5.6-terra"}},
                {"visibility": "list", "priority": 0},
                {"slug": "   ", "visibility": "list"},
                {"slug": "gpt-5.4", "visibility": "list", "priority": 99}
            ]
        })
    }

    #[test]
    fn parses_the_backend_document_in_priority_order() {
        let models = parse_catalog(&fixture()).unwrap();
        let slugs: Vec<&str> = models.iter().map(|m| m.slug.as_str()).collect();
        // Sorted by priority; the slug-less and blank entries are skipped; the
        // duplicate gpt-5.4 keeps its first occurrence.
        assert_eq!(
            slugs,
            ["gpt-6-astra", "gpt-reserve", "gpt-5.6-sol", "gpt-5.4"]
        );
        let astra = &models[0];
        assert!(astra.listed);
        assert_eq!(astra.display_name.as_deref(), Some("GPT-6-Astra"));
        assert_eq!(astra.context_window, Some(272000));
        assert_eq!(astra.minimal_client_version.as_deref(), Some("0.153.0"));
        assert_eq!(astra.reasoning_efforts, ["low", "max"]);
        assert_eq!(astra.upgrade, None);
        assert!(!models[1].listed, "hidden stays hidden");
        assert_eq!(models[2].max_context_window, Some(872000));
        assert_eq!(
            models[2].default_reasoning_effort.as_deref(),
            Some("medium")
        );
        assert_eq!(models[3].upgrade.as_deref(), Some("gpt-5.6-terra"));
        assert_eq!(models[3].priority, 16);
    }

    #[test]
    fn an_answer_without_models_is_an_error_not_an_empty_catalog() {
        assert!(parse_catalog(&json!({"models": []})).is_err());
        assert!(parse_catalog(&json!({"models": [{"visibility": "list"}]})).is_err());
        assert!(parse_catalog(&json!({"error": "nope"})).is_err());
        assert!(parse_catalog(&json!([])).is_err());
    }

    #[test]
    fn models_document_lists_only_visible_models_with_metadata() {
        let snapshot = CatalogSnapshot {
            models: parse_catalog(&fixture()).unwrap(),
            source: CatalogSource::Live,
        };
        let list = model_list_from(&snapshot);
        assert_eq!(list.object, "list");
        let ids: Vec<&str> = list.data.iter().map(|m| m.id.as_str()).collect();
        assert_eq!(ids, ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.4"]);
        let astra = &list.data[0];
        assert_eq!(astra.object, "model");
        assert_eq!(astra.owned_by, "chatgpt");
        assert_eq!(astra.context_window, Some(272000));
        assert_eq!(astra.context_length, Some(272000));
        assert_eq!(astra.meta.as_ref().unwrap()["n_ctx_train"], 272000);
        assert_eq!(astra.input_modalities, ["text", "image"]);
        // Serialized shape: the four OpenAI fields always, metadata only when known.
        let value = serde_json::to_value(&list).unwrap();
        assert_eq!(value["data"][0]["id"], "gpt-6-astra");
        assert_eq!(value["data"][0]["display_name"], "GPT-6-Astra");
        assert!(value["data"][2].get("display_name").is_none());
        assert!(value["data"][2].get("meta").is_none());
        assert!(value["data"][2].get("input_modalities").is_none());
        assert_eq!(value["data"][2]["upgrade"], "gpt-5.6-terra");
        // Hidden slugs are still findable for validation.
        assert!(snapshot.find("gpt-reserve").is_some());
        assert!(snapshot.find("gpt-5").is_none());
        assert_eq!(
            snapshot.listed_slugs(),
            ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.4"]
        );
    }

    #[test]
    fn extra_models_parse_cleanly_and_join_the_catalog_once() {
        assert_eq!(
            parse_extra_models("  gpt-7 , ,gpt-5.6-sol, gpt-7 ,gpt-8-mini"),
            vec![
                "gpt-7".to_string(),
                "gpt-5.6-sol".to_string(),
                "gpt-8-mini".to_string()
            ]
        );
        assert!(parse_extra_models("").is_empty());
        assert!(parse_extra_models(" , , ").is_empty());

        let base = parse_catalog(&fixture()).unwrap();
        let joined = with_extras(base, &parse_extra_models("gpt-5.6-sol,gpt-7"));
        let slugs: Vec<&str> = joined.iter().map(|m| m.slug.as_str()).collect();
        // sol already there (once); gpt-7 appended last, listed, bare.
        assert_eq!(
            slugs,
            [
                "gpt-6-astra",
                "gpt-reserve",
                "gpt-5.6-sol",
                "gpt-5.4",
                "gpt-7"
            ]
        );
        let seven = joined.last().unwrap();
        assert!(seven.listed);
        assert!(seven.priority > 16);
        assert_eq!(seven.context_window, None);
    }

    #[test]
    fn fallback_is_the_static_list_in_its_own_order() {
        let models = with_extras(fallback_models(), &[]);
        let slugs: Vec<&str> = models.iter().map(|m| m.slug.as_str()).collect();
        assert_eq!(slugs, FALLBACK_MODELS.to_vec());
        assert!(models.iter().all(|m| m.listed));
        assert!(slugs.contains(&"gpt-6-astra"));
    }

    #[tokio::test]
    async fn discovery_off_reports_static_and_extras() {
        let catalog =
            ModelCatalog::with(Vec::new(), DEFAULT_TTL, false, parse_extra_models("gpt-x"));
        let status = catalog.status().await;
        assert_eq!(status["source"], "static");
        assert_eq!(status["extra_models"], 1);
        assert_eq!(status["models"], FALLBACK_MODELS.len());
        assert!(status["fetched_seconds_ago"].is_null());
    }

    #[tokio::test]
    async fn a_preloaded_catalog_reports_live_until_the_ttl_passes() {
        let catalog = ModelCatalog::with(
            parse_catalog(&fixture()).unwrap(),
            DEFAULT_TTL,
            true,
            Vec::new(),
        );
        assert_eq!(catalog.status().await["source"], "live");
        let expired = ModelCatalog::with(
            parse_catalog(&fixture()).unwrap(),
            Duration::ZERO,
            true,
            Vec::new(),
        );
        assert_eq!(expired.status().await["source"], "stale");
        let never = ModelCatalog::with(Vec::new(), DEFAULT_TTL, true, Vec::new());
        assert_eq!(never.status().await["source"], "fallback");
    }

    #[test]
    fn effort_is_clamped_to_what_the_model_takes() {
        let astra: Vec<String> = ["low", "medium", "high", "xhigh", "max"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let sol: Vec<String> = ["low", "medium", "high", "xhigh", "max", "ultra"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let none: Vec<String> = Vec::new();
        // The live failure: relay default "none" on astra -> its lowest level.
        assert_eq!(
            clamp_effort(Some("none"), &astra, Some("medium")).as_deref(),
            Some("low")
        );
        assert_eq!(
            clamp_effort(Some("minimal"), &astra, None).as_deref(),
            Some("low")
        );
        // Supported values pass untouched; too high folds to the top.
        assert_eq!(
            clamp_effort(Some("high"), &astra, None).as_deref(),
            Some("high")
        );
        assert_eq!(
            clamp_effort(Some("ultra"), &astra, None).as_deref(),
            Some("max")
        );
        assert_eq!(
            clamp_effort(Some("ultra"), &sol, None).as_deref(),
            Some("ultra")
        );
        // Junk: the model's default when it is real, else the lowest level.
        assert_eq!(
            clamp_effort(Some("turbo"), &astra, Some("medium")).as_deref(),
            Some("medium")
        );
        assert_eq!(
            clamp_effort(Some("turbo"), &astra, Some("nope")).as_deref(),
            Some("low")
        );
        // No knowledge -> no opinion (terra takes "none" today; the fallback list
        // carries no levels, so nothing is rewritten there).
        assert_eq!(
            clamp_effort(Some("none"), &none, None).as_deref(),
            Some("none")
        );
        assert_eq!(
            clamp_effort(Some("turbo"), &none, None).as_deref(),
            Some("turbo")
        );
        assert_eq!(clamp_effort(None, &astra, Some("medium")), None);
    }

    #[test]
    fn source_names_are_stable_strings() {
        assert_eq!(CatalogSource::Live.as_str(), "live");
        assert_eq!(CatalogSource::Stale.as_str(), "stale");
        assert_eq!(CatalogSource::Fallback.as_str(), "fallback");
        assert_eq!(CatalogSource::Static.as_str(), "static");
    }
}
