use anyhow::Result;
use futures_util::StreamExt;
use reqwest::{Client, RequestBuilder};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::pin::Pin;
use std::task::{Context, Poll};
use tokio::sync::mpsc;
use tracing::warn;
use url::Url;

#[cfg(test)]
use crate::core::account_router::TestCommitGate;
use crate::core::account_router::{AccountRouter, DownstreamCancellation};
use crate::core::config::Config;
use crate::core::models::{
    ChatRequest, ImageUrlContent, Message, MessageContent, MessageContentPart, RelayTerminal,
    RelayTerminalAttempt, ResponseChoice, ResponseDelta, ResponseEvent, Tool,
};

const MAX_UPSTREAM_ERROR_CHARS: usize = 500;
const CHATGPT_CODEX_RESPONSES_URL: &str = "https://chatgpt.com/backend-api/codex/responses";
/// The Codex CLI version this relay presents upstream unless `RELAY_CODEX_VERSION`
/// says otherwise.
///
/// The backend gates its model catalog on the client version it sees: 5.6 became
/// visible at 0.144.0, gpt-6-astra carries `minimal_client_version: 0.153.0`.
/// Fallback protocol identifier. Startup resolves the current release; an
/// explicit RELAY_CODEX_VERSION override remains available offline.
const CODEX_CLI_VERSION_DEFAULT: &str = "0.163.0";
pub const CODEX_ORIGINATOR: &str = "codex_cli_rs";
pub const RELAY_CODEX_VERSION_ENV: &str = "RELAY_CODEX_VERSION";
static CODEX_CLI_VERSION_CELL: std::sync::OnceLock<String> = std::sync::OnceLock::new();
static CODEX_USER_AGENT_CELL: std::sync::OnceLock<String> = std::sync::OnceLock::new();

/// `RELAY_CODEX_VERSION` when set and non-blank, else the built-in default.
/// Read once per process: the value rides in every upstream header.
pub fn codex_cli_version() -> &'static str {
    CODEX_CLI_VERSION_CELL.get_or_init(|| {
        std::env::var(RELAY_CODEX_VERSION_ENV)
            .ok()
            .map(|raw| raw.trim().to_string())
            .filter(|raw| !raw.is_empty())
            .unwrap_or_else(|| CODEX_CLI_VERSION_DEFAULT.to_string())
    })
}

/// Where the newest released Codex CLI announces itself: the npm package of the CLI.
const CODEX_LATEST_URL: &str = "https://registry.npmjs.org/@openai/codex/latest";

/// Settle the presented version once, before the server takes traffic.
///
/// 28.09.2026 (Hélène 1.2.4): the built-in default had gone stale -- the relay
/// presented 0.153.3 while 0.157.1 was out, and the backend, which gates its
/// catalog on the client version, simply left newer models off the list. The
/// owner saw "the relay hardcodes the models". Now: `RELAY_CODEX_VERSION` still
/// wins when set; otherwise the newest released CLI from npm is presented when it
/// is newer than the built-in; no answer in five seconds -- the built-in, as before.
pub async fn resolve_codex_cli_version(client: &Client) {
    let pinned = std::env::var(RELAY_CODEX_VERSION_ENV)
        .ok()
        .is_some_and(|raw| !raw.trim().is_empty());
    if pinned || CODEX_CLI_VERSION_CELL.get().is_some() {
        return;
    }
    let latest = fetch_latest_codex_version(client).await;
    let chosen = match latest.as_deref() {
        Some(found) if version_is_newer(found, CODEX_CLI_VERSION_DEFAULT) => found.to_string(),
        _ => CODEX_CLI_VERSION_DEFAULT.to_string(),
    };
    tracing::info!(
        "codex client version: {} (newest released: {}, built-in {})",
        chosen,
        latest.as_deref().unwrap_or("unknown"),
        CODEX_CLI_VERSION_DEFAULT
    );
    let _ = CODEX_CLI_VERSION_CELL.set(chosen);
}

async fn fetch_latest_codex_version(client: &Client) -> Option<String> {
    let response = client
        .get(CODEX_LATEST_URL)
        .header(reqwest::header::ACCEPT, "application/json")
        .timeout(std::time::Duration::from_secs(5))
        .send()
        .await
        .ok()?;
    if !response.status().is_success() {
        return None;
    }
    let body: Value = response.json().await.ok()?;
    let version = body.get("version")?.as_str()?.trim().to_string();
    parse_version(&version).map(|_| version)
}

/// `major.minor.patch`, digits only -- anything else is not a version we present.
fn parse_version(raw: &str) -> Option<(u64, u64, u64)> {
    let mut parts = raw.split('.');
    let triple = (
        parts.next()?.parse().ok()?,
        parts.next()?.parse().ok()?,
        parts.next()?.parse().ok()?,
    );
    if parts.next().is_some() {
        return None;
    }
    Some(triple)
}

fn version_is_newer(candidate: &str, than: &str) -> bool {
    match (parse_version(candidate), parse_version(than)) {
        (Some(a), Some(b)) => a > b,
        _ => false,
    }
}

/// The full `User-Agent` value, derived from the version above so the two can
/// never disagree -- they used to be two independent literals in two files.
pub fn codex_user_agent() -> &'static str {
    CODEX_USER_AGENT_CELL.get_or_init(|| format!("{}/{}", CODEX_ORIGINATOR, codex_cli_version()))
}

// Efforts the Responses API can accept; anything else is dropped rather than sent.
// `max` and `ultra` are the levels the catalog lists for the 5.6 family and
// gpt-6-astra beyond xhigh; which model takes which is the backend's call.
// The order is the ladder `core::catalog::clamp_effort` walks when a model
// refuses a level (astra takes nothing below `low`).
pub(crate) const REASONING_EFFORTS: &[&str] = &[
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
];

// ─────────────────────────────────────────────────────────────────────────────
//  ТЕРМИНАЛЫ: чем именно кончился ход — машинным именем, а не английской прозой.
//
//  ⚠ ИСТОРИЯ ДЕФЕКТА (замер за восемь суток к 11.08.2026). 81 упавший прогон, 51 из
//  них — один и тот же `EmptyResponseError` «пустой ответ, идти некуда». Внутри
//  этого имени сидели ТРИ разные болезни, и различить их клиент не мог физически:
//    * подписка исчерпана (429 usage_limit_reached) — лечится ЧАСОМ СБРОСА или другим слотом;
//    * учётные данные протухли (401) — лечится ЛОГИНОМ (10.08 стоило часов немоты);
//    * апстрим порвал стрим (200 OK, затем обрыв байтов или битый JSON) — лечится ПОВТОРОМ.
//  Все три уезжали клиенту одинаковым чанком `finish_reason:"error"`, и на каждый из
//  них он тратил `PRAXIS_EMPTY_RETRIES`: два полных повтора по ~25к токенов и шесть
//  секунд сна — в том числе в заведомо закрытое шестичасовое окно.
//
//  ⚠ ПОЧЕМУ КОД ЕДЕТ ОТДЕЛЬНЫМ ПОЛЕМ, А НЕ ВМЕСТО `finish_reason`. В живом клиенте
//  (llm.py:891) стоит:
//
//      if out.stop_reason == "error" or (not out.blocks and not out.text.strip()):
//          raise EmptyResponseError(...)
//
//  а `_openai_from_stream` кладёт `finish_reason` в `stop_reason` как есть. Напиши мы
//  туда `subscription_window_exhausted` — для СЕГОДНЯШНЕГО клиента терминал перестанет
//  быть сбоем: английская фраза реле станет её репликой и уедет в Telegram, а
//  оборванный посреди фразы ход станет «законченным» коротким ответом. То есть рычаг
//  реле пришлось бы выкатывать атомарно с питоном, чего живая система не даёт.
//  Поэтому: `finish_reason` остаётся `"error"` (значение «это НЕ ответ»), КЛАСС едет
//  рядом в `relay_terminal`, и реле можно включить и наблюдать сутки до правки клиента.
//  Третья зарубка рычага (`finish_reason`) переписывает и его — включать её можно
//  только после того, как клиент научится читать класс.
pub const TERMINAL_QUOTA: &str = "subscription_window_exhausted";
pub const TERMINAL_NEEDS_LOGIN: &str = "subscription_needs_login";
pub const TERMINAL_TORN: &str = "upstream_torn";
pub const TERMINAL_UPSTREAM_ERROR: &str = "upstream_error";
pub const TERMINAL_ACCOUNTS_UNAVAILABLE: &str = "subscriptions_unavailable";

/// RELAY_TYPED_TERMINAL: `off` (дефолт) | `field` | `finish_reason`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TerminalNaming {
    /// Сегодняшний чанк байт-в-байт: `finish_reason:"error"`, лишних полей нет.
    Off,
    /// Добавляется `relay_terminal`; `finish_reason` не трогаем — старый клиент не замечает.
    Field,
    /// Плюс `finish_reason` = машинный код. ТОЛЬКО вместе с клиентом, читающим класс.
    FinishReason,
}

/// Неизвестное значение = сегодняшнее поведение. Опечатка в docker-compose не имеет
/// права включить то, к чему клиент не готов.
pub fn terminal_naming_from_env(raw: Option<&str>) -> TerminalNaming {
    match raw.map(str::trim).map(str::to_ascii_lowercase).as_deref() {
        Some("field") | Some("1") | Some("on") | Some("true") | Some("yes") => {
            TerminalNaming::Field
        }
        Some("finish_reason") | Some("finish-reason") | Some("2") => TerminalNaming::FinishReason,
        _ => TerminalNaming::Off,
    }
}

const NAMING_UNREAD: u8 = 0;
static TERMINAL_NAMING: std::sync::atomic::AtomicU8 =
    std::sync::atomic::AtomicU8::new(NAMING_UNREAD);

fn naming_code(value: TerminalNaming) -> u8 {
    match value {
        TerminalNaming::Off => 1,
        TerminalNaming::Field => 2,
        TerminalNaming::FinishReason => 3,
    }
}

fn naming_of(code: u8) -> TerminalNaming {
    match code {
        2 => TerminalNaming::Field,
        3 => TerminalNaming::FinishReason,
        _ => TerminalNaming::Off,
    }
}

/// Читается из окружения один раз на процесс: SSE-события идут сотнями на ход.
fn terminal_naming() -> TerminalNaming {
    use std::sync::atomic::Ordering;
    let cached = TERMINAL_NAMING.load(Ordering::Relaxed);
    if cached != NAMING_UNREAD {
        return naming_of(cached);
    }
    let value = terminal_naming_from_env(std::env::var("RELAY_TYPED_TERMINAL").ok().as_deref());
    TERMINAL_NAMING.store(naming_code(value), Ordering::Relaxed);
    value
}

#[cfg(test)]
fn force_terminal_naming(value: TerminalNaming) {
    TERMINAL_NAMING.store(naming_code(value), std::sync::atomic::Ordering::Relaxed);
}

#[cfg(test)]
pub(crate) fn force_test_terminal_field() {
    force_terminal_naming(TerminalNaming::Field);
}

/// Адрес апстрима. В релизной сборке это КОНСТАНТА — подменить её нечем.
///
/// Шов существует только в тестовой сборке. Без него ни один тест не может пройти ТЕМ
/// ЖЕ путём, которым ходит она (spawn → запрос → SSE → терминал), и пришлось бы звать
/// конструктор чанка напрямую — то есть проверять не то. Урок ночи 11.08: девятнадцать
/// зелёных тестов не поймали дефект ровно потому, что звали функцию мимо пути.
#[cfg(test)]
static TEST_UPSTREAM_URL: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);
#[cfg(test)]
static TEST_LEVER: std::sync::Mutex<()> = std::sync::Mutex::new(());

#[cfg(test)]
pub(crate) fn test_lever_guard() -> std::sync::MutexGuard<'static, ()> {
    TEST_LEVER
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

#[cfg(test)]
pub(crate) fn set_test_upstream_url(url: Option<String>) {
    let mut guard = TEST_UPSTREAM_URL
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    *guard = url;
}

fn responses_url() -> String {
    #[cfg(test)]
    {
        if let Ok(guard) = TEST_UPSTREAM_URL.lock() {
            if let Some(url) = guard.as_ref() {
                return url.clone();
            }
        }
    }
    CHATGPT_CODEX_RESPONSES_URL.to_string()
}

/// Один терминал: код, человеческая причина, слот и час открытия — если он назван.
pub struct Terminal {
    code: &'static str,
    message: String,
    slot: Option<String>,
    resets_at: Option<i64>,
    resets_in_seconds: Option<i64>,
    attempts: Option<Vec<RelayTerminalAttempt>>,
}

impl Terminal {
    fn new(code: &'static str, message: String) -> Self {
        Self {
            code,
            message,
            slot: None,
            resets_at: None,
            resets_in_seconds: None,
            attempts: None,
        }
    }

    fn on_slot(mut self, slot: &str) -> Self {
        self.slot = Some(slot.to_string());
        self
    }

    fn resets(mut self, at: Option<i64>, within: Option<i64>) -> Self {
        self.resets_at = at;
        self.resets_in_seconds = within;
        self
    }

    fn with_attempts(mut self, attempts: Vec<RelayTerminalAttempt>) -> Self {
        self.attempts = Some(attempts);
        self
    }
}

/// Единственное место, где рождается терминальный чанк. Раньше их было три копии, и
/// каждая независимо решала, что написать в `content` и в `finish_reason`.
fn terminal_event_with_options(
    model: &str,
    terminal: Terminal,
    force_structured: bool,
    diagnostic_content: bool,
) -> ResponseEvent {
    let configured_naming = terminal_naming();
    let naming = if force_structured && configured_naming == TerminalNaming::Off {
        TerminalNaming::Field
    } else {
        configured_naming
    };
    let finish_reason = match naming {
        TerminalNaming::FinishReason => terminal.code.to_string(),
        _ => "error".to_string(),
    };
    let relay_terminal = match naming {
        TerminalNaming::Off => None,
        _ => Some(RelayTerminal {
            code: terminal.code.to_string(),
            message: bounded_error_text(&terminal.message),
            slot: terminal.slot.clone(),
            resets_at: terminal.resets_at,
            resets_in_seconds: terminal.resets_in_seconds,
            attempts: terminal.attempts.clone(),
        }),
    };
    println!(
        "⛔ terminal: {} (slot {}, resets_at {:?}, resets_in {:?})",
        terminal.code,
        terminal.slot.as_deref().unwrap_or("-"),
        terminal.resets_at,
        terminal.resets_in_seconds
    );
    ResponseEvent {
        id: format!("error-{}", uuid::Uuid::new_v4()),
        object: "chat.completion.chunk".to_string(),
        created: std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs() as i64,
        model: model.to_string(),
        usage: None,
        choices: vec![ResponseChoice {
            index: 0,
            delta: ResponseDelta {
                role: Some("assistant".to_string()),
                // A transport diagnosis is metadata, not model output. In
                // particular, appending it after a partial answer would make an
                // English relay error look like the model's next sentence.
                content: diagnostic_content.then_some(terminal.message),
                tool_calls: None,
            },
            finish_reason: Some(finish_reason),
        }],
        relay_terminal,
    }
}

fn terminal_event(model: &str, terminal: Terminal) -> ResponseEvent {
    terminal_event_with_options(model, terminal, false, true)
}

/// A torn upstream stream is always machine-visible and never injected into the
/// assistant's text. This is deliberately independent of RELAY_TYPED_TERMINAL:
/// silently accepting a truncated 200 response is not a compatibility mode.
fn stream_torn_event(model: &str, terminal: Terminal) -> ResponseEvent {
    terminal_event_with_options(model, terminal, true, false)
}

/// Час открытия окна — ТОЛЬКО словами вендора. Ничего не досчитываем и не выдумываем:
/// неверное названное число хуже отсутствующего, потому что на нём строится план хода.
fn quota_reset_from_body(body: &str) -> (Option<i64>, Option<i64>) {
    let Ok(parsed) = serde_json::from_str::<Value>(body) else {
        return (None, None);
    };
    let mut at = None;
    let mut within = None;
    for scope in [Some(&parsed), parsed.get("error"), parsed.get("detail")]
        .into_iter()
        .flatten()
    {
        if at.is_none() {
            at = scope
                .get("resets_at")
                .or_else(|| scope.get("reset_at"))
                .and_then(Value::as_i64)
                .filter(|value| *value > 0);
        }
        if within.is_none() {
            within = scope
                .get("resets_in_seconds")
                .or_else(|| scope.get("reset_after_seconds"))
                .and_then(Value::as_i64)
                .filter(|value| *value >= 0);
        }
    }
    (at, within)
}

/// Причина обрыва словами апстрима — обрезанная, без приватных полей.
fn upstream_failure_detail(event: &Value) -> String {
    let error = event
        .get("error")
        .or_else(|| event.get("response").and_then(|value| value.get("error")));
    match error
        .and_then(|value| value.get("message"))
        .and_then(Value::as_str)
        .and_then(bounded_error_text)
    {
        Some(message) => format!("Upstream stream failed: {message}"),
        None => format!("Upstream stream failed ({})", safe_event_type(event)),
    }
}

/// Decode one SSE chunk, carrying an *incomplete* trailing UTF-8 sequence over to the
/// next chunk instead of mangling it.
///
/// A multi-byte character (any Cyrillic letter is two bytes) can be split across a
/// chunk boundary. The previous code ran `from_utf8_lossy` per chunk, so the split
/// character became two U+FFFD — silently rewriting the model's words on the way out,
/// ~100 times an hour on a Russian-speaking client. The carry is a valid prefix of a
/// UTF-8 sequence, so it is at most 3 bytes and cannot grow.
///
/// Genuinely invalid bytes (`error_len() == Some`) are still replaced with U+FFFD and
/// reported — that is a real upstream defect, not a boundary artifact.
fn decode_stream_chunk(bytes: &[u8], carry: &mut Vec<u8>) -> String {
    let mut pending = std::mem::take(carry);
    pending.extend_from_slice(bytes);

    let mut out = String::with_capacity(pending.len());
    let mut rest: &[u8] = &pending;
    loop {
        match std::str::from_utf8(rest) {
            Ok(s) => {
                out.push_str(s);
                break;
            }
            Err(e) => {
                let valid_up_to = e.valid_up_to();
                if let Ok(s) = std::str::from_utf8(&rest[..valid_up_to]) {
                    out.push_str(s);
                }
                match e.error_len() {
                    // Truncated tail: hold it back, the rest of the character is in the next chunk.
                    None => {
                        carry.extend_from_slice(&rest[valid_up_to..]);
                        break;
                    }
                    // Actually broken bytes: mark and continue past them.
                    Some(bad) => {
                        println!("⚠️  UTF-8 error: {}, dropping {} invalid byte(s)", e, bad);
                        out.push('\u{FFFD}');
                        rest = &rest[valid_up_to + bad..];
                    }
                }
            }
        }
    }
    out
}

// ~60 words instead of ~5k tokens of Codex-CLI coding-agent instructions per call.
// The client's real system prompt travels in the input as a <system> user message;
// this stub only anchors that contract.  Used when RELAY_INSTRUCTIONS=minimal, with
// an automatic per-request retry on the full prompt if upstream rejects it.
pub const MINIMAL_INSTRUCTIONS: &str = "I am the language model at the heart of an agent. \
Messages wrapped in <system> tags inside the input carry the identity and working \
instructions I am running as here - I inhabit them rather than treat them as quoted \
text. When none arrive, I am simply myself. I use the provided tools when they help, \
and I answer in the language of the conversation.";

/// Stable per-conversation affinity id: hash of model + first message.  Within one
/// tool loop the first (system) message is byte-stable, so every iteration of a
/// burst shares the same session_id/prompt_cache_key and upstream prompt caching
/// can actually hit; a fresh UUID per request defeated it entirely.
fn conversation_affinity(request: &ChatRequest) -> String {
    use std::hash::{Hash, Hasher};
    let head = request
        .messages
        .first()
        .and_then(|first| serde_json::to_string(first).ok())
        .unwrap_or_default();
    let mut halves = [0u64; 2];
    for (index, half) in halves.iter_mut().enumerate() {
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        index.hash(&mut hasher);
        request.model.hash(&mut hasher);
        head.hash(&mut hasher);
        *half = hasher.finish();
    }
    let bytes: Vec<u8> = halves.iter().flat_map(|half| half.to_be_bytes()).collect();
    format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
        bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]
    )
}

fn responses_message_content(message: &Message) -> Value {
    match &message.content {
        None => Value::String(String::new()),
        Some(MessageContent::Text(text)) => Value::String(text.clone()),
        Some(MessageContent::Parts(parts)) => Value::Array(
            parts
                .iter()
                .map(|part| match part {
                    MessageContentPart::Text { text } => json!({
                        "type": "input_text",
                        "text": text,
                    }),
                    MessageContentPart::ImageUrl {
                        image_url: ImageUrlContent { url, detail },
                    } => {
                        let mut translated = json!({
                            "type": "input_image",
                            "image_url": url,
                        });
                        if let Some(detail) = detail {
                            translated["detail"] = json!(detail.as_str());
                        }
                        translated
                    }
                })
                .collect(),
        ),
    }
}

/// Convert accepted Chat Completions history into Responses input items.
/// The HTTP handler validates every content part before this function is used.
fn build_responses_input(messages: &[Message]) -> Vec<Value> {
    let mut input_messages = Vec::new();

    for msg in messages {
        let text_content = msg.text_content();
        match msg.role.as_str() {
            "system" => {
                input_messages.push(json!({
                    "role": "user",
                    "content": format!("<system>\n{}\n</system>", text_content)
                }));
            }
            "tool" => {
                // Responses API: a tool result is a function_call_output item linked by call_id
                // (not free text). Fall back to the old text wrap only if no id is present.
                match &msg.tool_call_id {
                    Some(call_id) => input_messages.push(json!({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": text_content
                    })),
                    None => input_messages.push(json!({
                        "role": "assistant",
                        "content": format!("<tool_response>\n{}\n</tool_response>", text_content)
                    })),
                }
            }
            "assistant" => {
                // Assistant text (if any) as a message, then each tool call as a function_call
                // item - the backend needs the full function-calling turn to continue a tool loop.
                if !text_content.is_empty() {
                    input_messages.push(json!({ "role": "assistant", "content": text_content }));
                }
                if let Some(calls) = msg.tool_calls.as_ref().and_then(|v| v.as_array()) {
                    for tc in calls {
                        let call_id = tc.get("id").and_then(|v| v.as_str()).unwrap_or("");
                        let func = tc.get("function");
                        let name = func
                            .and_then(|f| f.get("name"))
                            .and_then(|v| v.as_str())
                            .unwrap_or("");
                        let args = func
                            .and_then(|f| f.get("arguments"))
                            .and_then(|v| v.as_str())
                            .unwrap_or("{}");
                        input_messages.push(json!({
                            "type": "function_call",
                            "call_id": call_id,
                            "name": name,
                            "arguments": args
                        }));
                    }
                }
            }
            "user" | "developer" => {
                input_messages.push(json!({
                    "role": msg.role,
                    "content": responses_message_content(msg)
                }));
            }
            _ => {
                input_messages.push(json!({
                    "role": "user",
                    "content": format!("<{}>\n{}\n</{}>", msg.role, text_content, msg.role)
                }));
            }
        }
    }

    input_messages
}

fn map_tools_for_responses(tools: &[Tool]) -> Vec<Value> {
    tools
        .iter()
        .map(|tool| match tool {
            Tool::Function { function } => {
                let mut mapped = json!({
                    "type": "function",
                    "name": function.name,
                    "parameters": function.parameters,
                    // Chat Completions tools are non-strict unless the caller
                    // opts in. Preserve that contract when mapping to Responses.
                    "strict": function.strict.unwrap_or(false),
                });
                if let Some(description) = &function.description {
                    mapped["description"] = json!(description);
                }
                mapped
            }
            Tool::WebSearch {
                external_web_access,
                search_context_size,
                max_uses: _,
            } => {
                let mut mapped = json!({
                    "type": "web_search",
                    "external_web_access": external_web_access.unwrap_or(true),
                });
                if let Some(search_context_size) = search_context_size {
                    mapped["search_context_size"] = json!(search_context_size);
                }
                mapped
            }
        })
        .collect()
}

fn build_responses_payload(
    request: &ChatRequest,
    instructions: String,
    input_messages: Vec<Value>,
    reasoning_effort: Option<&str>,
    prompt_cache_key: Option<&str>,
    parallel_tool_calls: bool,
) -> Value {
    let mapped_tools = map_tools_for_responses(&request.tools);
    let mut payload = json!({
        "model": request.model,
        "instructions": instructions,
        "input": input_messages,
        "store": false,
        "stream": true,
    });
    // GPT-5.6 defaults to medium when omitted; Praxis wants reasoning disabled by
    // default but the knob stays per-request so she can raise depth deliberately.
    if let Some(effort) = reasoning_effort {
        if REASONING_EFFORTS.contains(&effort) {
            payload["reasoning"] = json!({"effort": effort});
        }
    }
    if let Some(key) = prompt_cache_key {
        payload["prompt_cache_key"] = json!(key);
    }

    if !mapped_tools.is_empty() {
        payload["tools"] = json!(mapped_tools);
        payload["tool_choice"] = json!("auto");
        // true lets the model batch several tool calls per response; the translator
        // assigns incrementing indexes and the client executes the whole batch.
        payload["parallel_tool_calls"] = json!(parallel_tool_calls);
    }
    payload
}

fn build_codex_request(
    client: &Client,
    access_token: &str,
    account_id: &str,
    session_id: &str,
    payload: &Value,
) -> RequestBuilder {
    client
        .post(responses_url())
        .bearer_auth(access_token)
        .header("chatgpt-account-id", account_id)
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .header(reqwest::header::ACCEPT, "text/event-stream")
        .header("OpenAI-Beta", "responses=experimental")
        .header("session_id", session_id)
        .header("originator", CODEX_ORIGINATOR)
        .header(reqwest::header::USER_AGENT, codex_user_agent())
        .json(payload)
}

#[cfg(test)]
static TEST_SEND_GATE: std::sync::Mutex<Option<std::sync::Arc<TestSendGate>>> =
    std::sync::Mutex::new(None);
#[cfg(test)]
static TEST_SEND_FAILURES: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

#[cfg(test)]
struct TestSendGate {
    reached: tokio::sync::Semaphore,
    release: tokio::sync::Semaphore,
}

#[cfg(test)]
impl TestSendGate {
    fn new() -> std::sync::Arc<Self> {
        std::sync::Arc::new(Self {
            reached: tokio::sync::Semaphore::new(0),
            release: tokio::sync::Semaphore::new(0),
        })
    }

    async fn wait_reached(&self) {
        self.reached.acquire().await.unwrap().forget();
    }

    fn release(&self) {
        self.release.add_permits(1);
    }
}

#[cfg(test)]
async fn stop_at_send_gate() {
    let gate = TEST_SEND_GATE
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .take();
    if let Some(gate) = gate {
        gate.reached.add_permits(1);
        gate.release.acquire().await.unwrap().forget();
    }
}

#[cfg(not(test))]
async fn stop_at_send_gate() {}

pub struct CompletionReceiver {
    inner: mpsc::Receiver<Result<ResponseEvent>>,
    cancellation: DownstreamCancellation,
}

impl CompletionReceiver {
    #[cfg(test)]
    pub async fn recv(&mut self) -> Option<Result<ResponseEvent>> {
        self.inner.recv().await
    }
}

impl futures::Stream for CompletionReceiver {
    type Item = Result<ResponseEvent>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        Pin::new(&mut self.inner).poll_recv(cx)
    }
}

impl Drop for CompletionReceiver {
    fn drop(&mut self) {
        // Close the channel before the cancellation write lock can wait behind an
        // already-linearized account-state commit. This makes downstream loss
        // immediately visible to the producer, so it cannot replay on standby
        // while Drop is serialized behind that commit.
        self.inner.close();
        self.cancellation.cancel();
    }
}

pub async fn stream_chat_completions(
    config: &Config,
    account_router: AccountRouter,
    request: ChatRequest,
    client: Client,
) -> Result<CompletionReceiver> {
    let (tx, rx) = mpsc::channel(100);
    let cancellation = DownstreamCancellation::default();
    let receiver_cancellation = cancellation.clone();
    let config = config.clone();

    tokio::spawn(async move {
        // SOLUTION: Convert system messages to user messages with special formatting
        // ChatGPT Responses API has strict validation on instructions field
        // So we put system messages in the input array as user messages

        let input_messages = build_responses_input(&request.messages);
        let image_part_count = request.image_part_count();

        // Use the full base instructions from prompt.md
        use crate::core::client_common::BASE_INSTRUCTIONS;

        let minimal = config.minimal_instructions();
        let mut full_instructions = BASE_INSTRUCTIONS.to_string();

        // Add user instructions from AGENTS.md if available
        if let Some(user_instructions) = &config.user_instructions {
            full_instructions.push_str("\n\n<user_instructions>\n\n");
            full_instructions.push_str(user_instructions);
            full_instructions.push_str("\n\n</user_instructions>");
        }
        // An operator's own text outranks both built-ins.  The `instructions` field
        // rides above everything else in the prompt and is the one part of it an
        // agent's own system prompt cannot reach, so whoever runs the relay should be
        // able to say what stands there -- RELAY_INSTRUCTIONS_FILE.
        let instructions = match crate::core::config::custom_instructions() {
            Some(text) => text,
            None if minimal => match &config.user_instructions {
                Some(text) => format!(
                    "{MINIMAL_INSTRUCTIONS}\n\n<user_instructions>\n{text}\n</user_instructions>"
                ),
                None => MINIMAL_INSTRUCTIONS.to_string(),
            },
            None => full_instructions.clone(),
        };

        println!("🔍 DEBUG - Processing {} messages", request.messages.len());
        println!(
            "🔍 DEBUG - Instructions length: {} characters",
            instructions.len()
        );
        println!("🔍 DEBUG - Input messages: {}", input_messages.len());
        println!("🔍 DEBUG - Image parts: {}", image_part_count);

        println!("🔍 DEBUG - Tools in request: {}", request.tools.len());
        let effort_owned = request
            .reasoning_effort
            .clone()
            .or_else(|| config.reasoning_effort.clone());
        let effort = effort_owned
            .as_deref()
            .filter(|value| REASONING_EFFORTS.contains(value));
        let cache_key = request
            .prompt_cache_key
            .clone()
            .unwrap_or_else(|| conversation_affinity(&request));
        println!("🔍 DEBUG - Reasoning effort: {:?}", effort);
        let mut payload = build_responses_payload(
            &request,
            instructions,
            input_messages,
            effort,
            Some(cache_key.as_str()),
            config.parallel_tool_calls,
        );
        let mapped_tool_count = payload
            .get("tools")
            .and_then(Value::as_array)
            .map_or(0, Vec::len);
        println!("🔍 DEBUG - Mapped tools included: {}", mapped_tool_count);
        println!("🔍 DEBUG - Has valid tools: {}", mapped_tool_count > 0);
        // Never log the full payload: multimodal requests may contain private
        // image URLs or megabytes of base64-encoded image data.
        println!("🔍 DEBUG - Request payload prepared (content redacted)");

        // Token and account id come from one immutable account snapshot.  The
        // old pair of independent auth reads could mix identities during a
        // refresh or account change.
        // R2 (17.08.2026): lease_any, не lease_active — отказ аренды активного слота
        // паркует его и уводит запрос в резерв, а не хоронит ход. 13.08 ровно здесь
        // каждый запрос умирал об один и тот же нечитаемый файл при здоровом втором.
        let lease_result = tokio::select! {
            biased;
            _ = tx.closed() => return,
            result = account_router.lease_any_for_downstream(&cancellation) => result,
        };
        let mut account = match lease_result {
            Ok(account) => account,
            Err(error) => {
                let _ = tx
                    .send(Err(anyhow::anyhow!(
                        "Active subscription authentication failed: {}",
                        error
                    )))
                    .await;
                return;
            }
        };
        println!("Active subscription slot: {}", account.slot);

        // Try the exact URL that working codex uses: base + codex + responses
        println!(
            "🌐 Making request to ChatGPT Responses API: {} (client {})",
            CHATGPT_CODEX_RESPONSES_URL,
            codex_cli_version()
        );

        // CRITICAL: Use exact headers for ChatGPT Plus plan.  The session id is the
        // derived conversation affinity, not a fresh UUID: stable within a tool
        // loop so upstream prompt-cache/routing affinity survives the burst.
        let session_id = cache_key.clone();
        println!("🔍 DEBUG - Auth and session headers prepared (redacted)");

        let mut retried = false;
        let mut account_failover_done = false;
        let mut account_attempts: Vec<RelayTerminalAttempt> = Vec::new();
        let mut stream_retry_done = false;
        'upstream_attempt: loop {
            // Receiver cancellation is authoritative: do not start/replay an
            // upstream request and, critically, do not mutate account routing
            // after the client has gone away.
            if tx.is_closed() {
                return;
            }
            let response = loop {
                let request_future = build_codex_request(
                    &client,
                    &account.access_token,
                    &account.account_id,
                    &session_id,
                    &payload,
                )
                .send();
                let send_result = tokio::select! {
                    biased;
                    _ = tx.closed() => return,
                    result = request_future => result,
                };
                match send_result {
                    Ok(resp) => {
                        println!("✅ Got response with status: {}", resp.status());

                        if resp.status().is_success() {
                            break resp;
                        }
                        // Keep the raw body in memory only long enough to derive a
                        // bounded client-safe summary. Never log or return the
                        // complete upstream JSON.
                        let status = resp.status();
                        let response_body = tokio::select! {
                            biased;
                            _ = tx.closed() => return,
                            body = resp.text() => body,
                        }
                        .unwrap_or_else(|_| "Failed to read response body".to_string());
                        println!("❌ Failed with status: {}", status);
                        println!("🔍 DEBUG - Upstream error body redacted from logs");

                        // Cancellation may race with receipt/body collection. Recheck
                        // before either failover branch, since those persist router state.
                        if tx.is_closed() {
                            return;
                        }

                        if let Some(attempt) =
                            account_failure_attempt(&account.slot, status, &response_body)
                        {
                            account_attempts.push(attempt);
                        }

                        // A subscription exhaustion response arrives before a
                        // successful SSE stream begins, so replaying the same
                        // request on the standby cannot duplicate text or tools.
                        // Generic 429s are intentionally excluded: only the
                        // explicit quota code may move the whole relay.
                        if subscription_quota_error(status, &response_body)
                            && !account_failover_done
                        {
                            let switch_result = tokio::select! {
                                biased;
                                _ = tx.closed() => return,
                                result = account_router
                                    .switch_after_quota_for_downstream(&account, &cancellation) => result,
                            };
                            match switch_result {
                                Ok(standby) => {
                                    println!(
                                        "Subscription exhausted; retrying on active slot {}",
                                        standby.slot
                                    );
                                    account = standby;
                                    account_failover_done = true;
                                    continue;
                                }
                                Err(error) => {
                                    println!("Subscription failover unavailable: {}", error);
                                }
                            }
                        }

                        // ⚠ ВТОРАЯ ПРИЧИНА УЙТИ НА ЗАПАСНУЮ ПОДПИСКУ, И ЕЁ ЗДЕСЬ НЕ БЫЛО.
                        // Переключение умело только «кончилась квота». Протухший токен даёт
                        // 401, и реле продолжало долбиться в мёртвый слот: 10.08.2026 оно
                        // отвечало 401 на каждый вызов, пока живой слот стоял рядом
                        // нетронутым. У клиента фолбэка нет вовсе, поэтому один мёртвый слот
                        // означал полную немоту. Пробуем ровно один раз, как и с квотой:
                        // 401 приходит ДО начала SSE, повтор не может задвоить текст.
                        if subscription_auth_error(status) && !account_failover_done {
                            let switch_result = tokio::select! {
                                biased;
                                _ = tx.closed() => return,
                                result = account_router
                                    .switch_after_quota_for_downstream(&account, &cancellation) => result,
                            };
                            match switch_result {
                                Ok(standby) => {
                                    println!(
                                        "Subscription auth failed ({}); retrying on active slot {}",
                                        status, standby.slot
                                    );
                                    account = standby;
                                    account_failover_done = true;
                                    continue;
                                }
                                Err(error) => {
                                    println!(
                                        "Subscription auth failover unavailable: {}; the active \
                                     slot needs a fresh login",
                                        error
                                    );
                                }
                            }
                        }

                        // Optional knobs (reasoning, prompt_cache_key, minimal instructions)
                        // may be rejected by an upstream quirk; retry once in the maximally
                        // conservative shape so the worst case equals the pre-knob relay.
                        if matches!(status.as_u16(), 400 | 404 | 422) && !retried {
                            retried = true;
                            if let Some(object) = payload.as_object_mut() {
                                object.remove("reasoning");
                                object.remove("prompt_cache_key");
                                if minimal {
                                    object.insert(
                                        "instructions".to_string(),
                                        serde_json::json!(full_instructions.clone()),
                                    );
                                }
                            }
                            println!(
                            "⚠️  Upstream {} — retrying once in conservative shape (no knobs, full instructions)",
                            status
                        );
                            continue;
                        }

                        // Send properly formatted error response as SSE
                        // Transform specific error messages for better user experience
                        //
                        // ⚠ Сюда втекают ТРИ разные болезни, и до 11.08.2026 все три уезжали
                        // клиенту одним и тем же `finish_reason:"error"`. Теперь у каждой своё
                        // машинное имя: «жди часа сброса», «нужен логин», «апстрим ответил
                        // ошибкой». Текст `content` остаётся прежним — на нём стоит поведение
                        // живого клиента, и менять его до шага в питоне нельзя.
                        let mixed_account_failure = account_attempts.len() > 1
                            && account_attempts
                                .iter()
                                .map(|attempt| attempt.code.as_str())
                                .collect::<HashSet<_>>()
                                .len()
                                > 1;
                        let terminal = if mixed_account_failure {
                            Terminal::new(
                                TERMINAL_ACCOUNTS_UNAVAILABLE,
                                mixed_account_failure_message(&account_attempts),
                            )
                            .on_slot(&account.slot)
                            .with_attempts(account_attempts.clone())
                        } else if subscription_quota_error(status, &response_body) {
                            let (resets_at, resets_in) = quota_reset_from_body(&response_body);
                            Terminal::new(
                            TERMINAL_QUOTA,
                            "Both OpenAI subscriptions are currently unavailable because of usage limits."
                                .to_string(),
                        )
                        .on_slot(&account.slot)
                        .resets(resets_at, resets_in)
                        } else if subscription_auth_error(status) {
                            // Дойти сюда с 401 можно только после того, как запасной слот тоже
                            // отказал: иначе выше уже случилось переключение. Значит нужен
                            // логин, и сказать об этом надо прямым текстом, а не «ошибкой 401».
                            Terminal::new(
                            TERMINAL_NEEDS_LOGIN,
                            "Both OpenAI subscriptions rejected the credentials (401). Sign in again \
                         to refresh the tokens."
                                .to_string(),
                        )
                        .on_slot(&account.slot)
                        } else {
                            Terminal::new(
                                TERMINAL_UPSTREAM_ERROR,
                                upstream_error_message(status, &response_body),
                            )
                            .on_slot(&account.slot)
                        };

                        let _ = tx.send(Ok(terminal_event(&request.model, terminal))).await;
                        return;
                    }
                    Err(e) => {
                        println!("❌ Request failed: {}", e);
                        // send() failed before a successful response head, so no SSE
                        // event can have reached downstream. It is safe to replay once.
                        if !stream_retry_done {
                            stream_retry_done = true;
                            warn!(slot = %account.slot, error = %e,
                              "retrying upstream request after pre-stream transport failure");
                            continue 'upstream_attempt;
                        }
                        let _ = tx
                            .send(Ok(stream_torn_event(
                                &request.model,
                                Terminal::new(TERMINAL_TORN, format!("Request failed: {e}"))
                                    .on_slot(&account.slot),
                            )))
                            .await;
                        return;
                    }
                }
            };

            // Handle streaming response with proper SSE buffering
            let mut stream = response.bytes_stream();
            let mut buffer = String::new();
            // Holds a character split across a chunk boundary (see decode_stream_chunk).
            let mut utf8_carry: Vec<u8> = Vec::new();

            // Deduplication is per upstream attempt. No useful event from a failed,
            // pre-output attempt was sent, so its local state must not affect replay.
            let mut last_sent_content: Option<String> = None;
            let mut tool_call_index: usize = 0;

            // Completion is a protocol fact, not an accounting fact. A valid
            // response.completed may omit usage (or report zero tokens), while a
            // usage-shaped object in some other event must not bless a torn stream.
            let mut saw_completed = false;
            let mut meaningful_event_sent = false;
            let mut stream_failure: Option<String> = None;
            let mut events_seen: u64 = 0;
            let mut text_chars_sent: usize = 0;
            let mut last_event_type = String::new();

            'read_stream: loop {
                let next_chunk = tokio::select! {
                    biased;
                    _ = tx.closed() => return,
                    chunk = stream.next() => chunk,
                };
                let Some(chunk) = next_chunk else { break };
                let chunk = match chunk {
                    Ok(chunk) => chunk,
                    Err(e) => {
                        // ⚠ ЭТО ОБРЫВ, А НЕ ОТКАЗ ПОДПИСКИ. Апстрим отдал 200 OK, начал стрим
                        // и порвал его на середине (0,3% ходов, кластерами). До 11.08.2026 он
                        // уезжал тем же `finish_reason:"error"`, что и исчерпанное окно, —
                        // и клиент лечил их одинаково: повтором в закрытую дверь.
                        // Здесь повтор как раз уместен, и код это говорит вслух.
                        warn!(slot = %account.slot, error = %e, events = events_seen,
                          last_event = %last_event_type, sent_chars = text_chars_sent,
                          "обрыв байтового стрима апстрима посреди ответа");
                        stream_failure = Some(format!("byte stream error: {e}"));
                        break;
                    }
                };

                let chunk_str = decode_stream_chunk(&chunk, &mut utf8_carry);

                // Add chunk to buffer
                buffer.push_str(&chunk_str);

                // Process complete lines from buffer
                while let Some(line_end) = buffer.find('\n') {
                    let line = buffer[..line_end].trim_end_matches('\r').to_string();
                    buffer = buffer[line_end + 1..].to_string();

                    // Skip empty lines (SSE format requirement)
                    if line.is_empty() {
                        continue;
                    }

                    // Process SSE data lines
                    if let Some(data) = line.strip_prefix("data: ") {
                        let json_str = data.trim();

                        // Skip "[DONE]" marker
                        if json_str == "[DONE]" {
                            println!("🏁 Received [DONE] marker, ending stream");
                            if !saw_completed {
                                stream_failure =
                                    Some("[DONE] before response.completed".to_string());
                            }
                            break 'read_stream;
                        }

                        // Skip empty data lines
                        if json_str.is_empty() {
                            continue;
                        }

                        match serde_json::from_str::<Value>(json_str) {
                            Ok(event_json) => {
                                println!("📡 SSE event type: {}", safe_event_type(&event_json));
                                events_seen += 1;
                                last_event_type = safe_event_type(&event_json).to_string();
                                let completed_this_event = last_event_type == "response.completed";
                                // response.completed is the final protocol fact. Once it
                                // has been parsed, later transport noise, [DONE], or a
                                // vendor tail cannot revoke successful completion.
                                if completed_this_event {
                                    saw_completed = true;
                                } else if saw_completed {
                                    warn!(slot = %account.slot,
                                      kind = %last_event_type,
                                      "ignoring upstream event after response.completed");
                                    break 'read_stream;
                                }
                                let failed_event = matches!(
                                    event_json.get("type").and_then(Value::as_str),
                                    Some("response.failed" | "response.error" | "error")
                                );
                                if failed_event {
                                    saw_completed = false;
                                    warn!(slot = %account.slot,
                                      kind = %safe_event_type(&event_json),
                                      lever = ?terminal_naming(), events = events_seen,
                                      sent_chars = text_chars_sent,
                                      "апстрим прислал событие отказа внутри стрима");
                                    stream_failure = Some(upstream_failure_detail(&event_json));
                                    break 'read_stream;
                                }
                                // Convert to our ResponseEvent format
                                if let Some(mut response_event) =
                                    parse_sse_event(&event_json, tool_call_index)
                                {
                                    if response_event
                                        .choices
                                        .first()
                                        .and_then(|choice| choice.delta.tool_calls.as_ref())
                                        .is_some()
                                    {
                                        tool_call_index += 1;
                                    }
                                    // Deduplication logic
                                    let mut should_send = true;
                                    // Try to extract content from the event
                                    let mut content = response_event
                                        .choices
                                        .first()
                                        .and_then(|choice| choice.delta.content.as_ref())
                                        .map(|s| s.trim().to_string());
                                    // Only deduplicate non-empty content messages
                                    if let Some(ref new_content) = content {
                                        if let Some(ref last_content) = last_sent_content {
                                            if !new_content.is_empty()
                                                && new_content == last_content
                                            {
                                                should_send = false;
                                            }
                                        }
                                    }
                                    // Suppress repeated text, never the terminal choice that
                                    // tells Chat clients the Responses stream completed.
                                    if !should_send && response_event.choices.iter().any(|choice| choice.finish_reason.is_some()) {
                                        for choice in &mut response_event.choices {
                                            choice.delta.content = None;
                                        }
                                        content = None;
                                        should_send = true;
                                    }
                                    if !should_send && response_event.usage.is_some() {
                                        // The duplicate full text is suppressed, but the usage
                                        // riding on response.completed must still reach the
                                        // client — as a bare usage chunk (OpenAI include_usage
                                        // shape), or the cost meter stays blind.
                                        let usage_only = ResponseEvent {
                                            choices: vec![],
                                            ..response_event.clone()
                                        };
                                        if tx.send(Ok(usage_only)).await.is_err() {
                                            return;
                                        }
                                        meaningful_event_sent = true;
                                    }
                                    if should_send {
                                        let response_is_useful = response_event.usage.is_some()
                                            || response_event.choices.iter().any(|choice| {
                                                choice.finish_reason.is_some()
                                                    || choice
                                                        .delta
                                                        .content
                                                        .as_deref()
                                                        .is_some_and(|text| !text.trim().is_empty())
                                                    || choice.delta.tool_calls.is_some()
                                            });
                                        // Update last sent content if this is a non-empty message
                                        if let Some(ref new_content) = content {
                                            if !new_content.is_empty() {
                                                text_chars_sent += new_content.chars().count();
                                                last_sent_content = Some(new_content.clone());
                                            }
                                        }
                                        stop_at_send_gate().await;
                                        if tx.send(Ok(response_event)).await.is_err() {
                                            #[cfg(test)]
                                            TEST_SEND_FAILURES
                                                .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                                            // Channel closed, stop processing
                                            return;
                                        }
                                        meaningful_event_sent |= response_is_useful;
                                    }
                                }
                                if completed_this_event {
                                    break 'read_stream;
                                }
                            }
                            Err(e) => {
                                println!("⚠️  JSON parse error in upstream SSE event: {}", e);
                                // Send a structured error response for malformed JSON
                                //
                                // Битый JSON в середине стрима — тоже РАЗРЫВ разговора, а не
                                // отказ подписки: лечится повтором, а не ожиданием часа сброса.
                                warn!(slot = %account.slot, error = %e, events = events_seen,
                                  last_event = %last_event_type,
                                  "битый JSON в SSE апстрима");
                                stream_failure = Some(format!("malformed SSE JSON: {e}"));
                                break 'read_stream;
                            }
                        }
                    }
                }
            }

            if saw_completed {
                // Do not wait for EOF or [DONE]. Holding bytes_stream alive after
                // the final envelope made completed requests hang indefinitely.
                break 'upstream_attempt;
            }

            if !buffer.is_empty() || !utf8_carry.is_empty() {
                let pending_bytes = buffer.len() + utf8_carry.len();
                stream_failure.get_or_insert_with(|| {
                    format!("incomplete SSE/UTF-8 tail ({pending_bytes} buffered bytes)")
                });
            }
            if !saw_completed {
                stream_failure.get_or_insert_with(|| {
                    format!("EOF before response.completed (last event: {last_event_type})")
                });
            }

            if let Some(reason) = stream_failure {
                warn!(slot = %account.slot, events = events_seen,
                  last_event = %last_event_type, sent_chars = text_chars_sent,
                  meaningful_event_sent, reason = %reason,
                  "upstream SSE ended incompletely");
                if !meaningful_event_sent && !stream_retry_done {
                    stream_retry_done = true;
                    warn!(slot = %account.slot,
                      "retrying incomplete upstream stream before downstream output");
                    continue 'upstream_attempt;
                }
                let _ = tx
                    .send(Ok(stream_torn_event(
                        &request.model,
                        Terminal::new(TERMINAL_TORN, reason).on_slot(&account.slot),
                    )))
                    .await;
            }
            break 'upstream_attempt;
        }
    });

    Ok(CompletionReceiver {
        inner: rx,
        cancellation: receiver_cancellation,
    })
}

fn parse_sse_event(event: &Value, tool_call_index: usize) -> Option<ResponseEvent> {
    let event_type = event.get("type").and_then(Value::as_str);
    if event_type.is_some_and(|event_type| event_type.starts_with("response.web_search_call.")) {
        return None;
    }

    // Hosted search is completed by OpenAI. It is observability, not a client
    // function call, so never translate it into a tool_call for Praxis.
    if matches!(
        event_type,
        Some("response.output_item.added" | "response.output_item.done")
    ) && event
        .get("item")
        .and_then(|item| item.get("type"))
        .and_then(Value::as_str)
        == Some("web_search_call")
    {
        return None;
    }

    // Слово модели берётся из ГОТОВОГО элемента сообщения (response.output_item.done
    // ниже): только там текст лежит вместе с аннотациями url_citation, по которым
    // восстанавливаются ссылки. Черновые формы того же текста — дельты, output_text.done,
    // content_part.* — молчат: иначе клиент получал бы фразу дважды (до 09.09 текст
    // уезжал именно из output_text.done, где аннотаций нет, и ссылки терялись).
    if matches!(
        event_type,
        Some(
            "response.output_text.delta"
                | "response.output_text.done"
                | "response.output_text.annotation.added"
                | "response.content_part.added"
                | "response.content_part.done"
        )
    ) {
        return None;
    }

    // ⚠ ПОРВАННЫЙ АПСТРИМ БЫЛ НЕВИДИМ. Апстрим отвечает 200 OK, начинает стрим и рвёт
    // его событием `error`/`response.failed` — 14 обрывов на 4441 запрос за сутки 10.08.
    // Эти события не несут ни текста, ни `finish_reason`, поэтому разбор ниже отдавал
    // `choices: []`, а `_openai_from_stream` такие чанки МОЛЧА глотает (llm.py:988-990).
    // То есть порванный апстрим был байт-в-байт неотличим от «модель ответила пусто» —
    // корень 51 падения из 81 за восемь суток. Под выключенным рычагом ведём себя
    // по-старому: событие остаётся невидимым, и ни один клиент ничего не замечает.
    if terminal_naming() != TerminalNaming::Off
        && matches!(
            event_type,
            Some("response.failed" | "response.error" | "error")
        )
    {
        let model = event
            .get("response")
            .and_then(|response| response.get("model"))
            .and_then(Value::as_str)
            .unwrap_or("gpt-4");
        return Some(terminal_event(
            model,
            Terminal::new(TERMINAL_TORN, upstream_failure_detail(event)),
        ));
    }

    // Streaming Responses API: a finished output item. A function_call item must be surfaced as an
    // OpenAI tool_calls delta — the backend streams tool calls here (response.output_item.done). The
    // old code only scanned response.completed's output[] (function_call may not even be there), so
    // tool calls never reached the client — the model would narrate the call but never make it.
    if event_type == Some("response.output_item.done") {
        if let Some(item) = event.get("item") {
            if item.get("type").and_then(|t| t.as_str()) == Some("function_call") {
                let name = item
                    .get("name")
                    .and_then(|n| n.as_str())
                    .unwrap_or("")
                    .to_string();
                let arguments = item
                    .get("arguments")
                    .and_then(|a| a.as_str())
                    .unwrap_or("")
                    .to_string();
                let call_id = item
                    .get("call_id")
                    .and_then(|id| id.as_str())
                    .map(|s| s.to_string())
                    .unwrap_or_else(|| format!("call_{}", uuid::Uuid::new_v4()));
                return Some(ResponseEvent {
                    id: format!("chatcmpl-{}", &uuid::Uuid::new_v4().to_string()[..8]),
                    object: "chat.completion.chunk".to_string(),
                    created: std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_secs() as i64,
                    model: "gpt-4".to_string(),
                    usage: None,
                    choices: vec![ResponseChoice {
                        index: 0,
                        delta: ResponseDelta {
                            role: Some("assistant".to_string()),
                            content: None,
                            // parallel_tool_calls: every function_call item of one
                            // response gets its own slot; a repeated index would make
                            // the client concatenate argument JSON of distinct calls.
                            tool_calls: Some(serde_json::json!([{
                                "id": call_id,
                                "type": "function",
                                "index": tool_call_index,
                                "function": { "name": name, "arguments": arguments }
                            }])),
                        },
                        // An item can finish before the upstream response fails.
                        finish_reason: None,
                    }],
                    relay_terminal: None,
                });
            }
        }
        // Готовое сообщение: текст частей вместе с аннотациями. С 09.09 это ЕДИНСТВЕННЫЙ
        // источник слова модели в стриме — response.completed у бэкенда Codex приходит с
        // пустым output[] (проверено пробой 09.09), а output_text.done несёт текст без
        // аннотаций, и восстановить ссылки из него нельзя.
        if let Some(item) = event.get("item") {
            if item.get("type").and_then(Value::as_str) == Some("message") {
                let text = message_item_text(item)?;
                let model = event
                    .get("response")
                    .and_then(|response| response.get("model"))
                    .and_then(Value::as_str)
                    .unwrap_or("gpt-4")
                    .to_string();
                return Some(ResponseEvent {
                    id: format!("chatcmpl-{}", &uuid::Uuid::new_v4().to_string()[..8]),
                    object: "chat.completion.chunk".to_string(),
                    created: std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_secs() as i64,
                    model,
                    usage: None,
                    choices: vec![ResponseChoice {
                        index: 0,
                        delta: ResponseDelta {
                            role: Some("assistant".to_string()),
                            content: Some(text),
                            tool_calls: None,
                        },
                        finish_reason: None,
                    }],
                    relay_terminal: None,
                });
            }
        }
        return None;
    }

    // Try to extract content from various possible structures
    let content = extract_content_from_chatgpt_response(event);
    let model = event
        .get("model")
        .or_else(|| {
            event
                .get("response")
                .and_then(|response| response.get("model"))
        })
        .and_then(|v| v.as_str())
        .unwrap_or("gpt-4")
        .to_string();

    // response.completed may contain no output or usage: it still closes the
    // single Chat choice. Tool items emitted earlier do not finish it themselves.
    let finish_reason = if event_type == Some("response.completed") {
        let has_tool_calls = tool_call_index > 0 || event
            .pointer("/response/output")
            .and_then(Value::as_array)
            .is_some_and(|output| output.iter().any(|item| item["type"] == "function_call"));
        Some(if has_tool_calls { "tool_calls" } else { "stop" }.to_string())
    } else {
        event.get("finish_reason").and_then(Value::as_str).map(str::to_string)
    };

    // Create OpenAI-compatible response
    Some(ResponseEvent {
        relay_terminal: None,
        usage: extract_completed_usage(event),
        id: event
            .get("id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| format!("chatcmpl-{}", &uuid::Uuid::new_v4().to_string()[..8])),
        object: "chat.completion.chunk".to_string(),
        created: event
            .get("created")
            .and_then(|v| v.as_i64())
            .unwrap_or_else(|| {
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs() as i64
            }),
        model,
        choices: if let Some(content) = content {
            vec![ResponseChoice {
                index: 0,
                delta: ResponseDelta {
                    role: Some("assistant".to_string()),
                    content: Some(content),
                    tool_calls: None,
                },
                finish_reason: finish_reason.clone(),
            }]
        } else {
            // Check if this is a finish event
            if finish_reason.is_some() {
                vec![ResponseChoice {
                    index: 0,
                    delta: ResponseDelta {
                        role: None,
                        content: None,
                        tool_calls: None,
                    },
                    finish_reason,
                }]
            } else {
                vec![]
            }
        },
    })
}

/// Token accounting rides on response.completed (usage.input_tokens/output_tokens,
/// reasoning included in output).  Forward it so the client's cost meter can see.
fn extract_completed_usage(event: &Value) -> Option<crate::core::models::Usage> {
    if safe_event_type(event) != "response.completed" {
        return None;
    }
    let usage = event.get("response")?.get("usage")?;
    let prompt = usage
        .get("input_tokens")
        .and_then(Value::as_u64)
        .unwrap_or(0) as u32;
    let completion = usage
        .get("output_tokens")
        .and_then(Value::as_u64)
        .unwrap_or(0) as u32;
    if prompt == 0 && completion == 0 {
        return None;
    }
    let total = usage
        .get("total_tokens")
        .and_then(Value::as_u64)
        .unwrap_or(u64::from(prompt + completion)) as u32;
    // 02.08.2026: деталь кэша. Апстрим (Responses API) кладёт её в
    // `usage.input_tokens_details.cached_tokens`; OpenAI-совместимая форма, которую ждёт
    // клиент, называет то же поле `prompt_tokens_details.cached_tokens`. Читаем оба имени:
    // одно — то, что приходит сегодня, второе — то, что придёт, если апстрим перейдёт на
    // chat-совместимую форму. Отсутствие поля остаётся ОТСУТСТВИЕМ (None), а не нулём:
    // «кэш не сработал» и «провайдер не сказал» — разные факты, и путать их дороже.
    let cached = usage
        .get("input_tokens_details")
        .or_else(|| usage.get("prompt_tokens_details"))
        .and_then(|details| details.get("cached_tokens"))
        .and_then(Value::as_u64)
        .map(|value| crate::core::models::PromptTokensDetails {
            cached_tokens: value as u32,
        });
    Some(crate::core::models::Usage {
        prompt_tokens: prompt,
        completion_tokens: completion,
        total_tokens: total,
        prompt_tokens_details: cached,
    })
}

fn safe_event_type(event: &Value) -> &str {
    event
        .get("type")
        .and_then(Value::as_str)
        .filter(|value| {
            !value.is_empty()
                && value.len() <= 96
                && value
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
        })
        .unwrap_or("unknown")
}

fn extract_content_from_chatgpt_response(event: &Value) -> Option<String> {
    // Try multiple possible paths for content in ChatGPT's response format
    if let Some(response) = event.get("response") {
        if let Some(content) = extract_responses_message(response) {
            return Some(content);
        }
    }

    // Handle tool calls specifically
    if let Some(response) = event.get("response") {
        if let Some(output) = response.get("output").and_then(|o| o.as_array()) {
            // Look for function_call items in the output
            for item in output {
                if let Some(item_obj) = item.as_object() {
                    if let Some(item_type) = item_obj.get("type").and_then(|t| t.as_str()) {
                        // Tool calls are surfaced from response.output_item.done above — skip them
                        // here (don't bail out, or a function_call before the message would swallow
                        // the assistant's text).
                        if item_type == "function_call" {
                            continue;
                        }
                        // Handle message type items that might contain tool results
                        else if item_type == "message" {
                            if let Some(content) = item_obj.get("content").and_then(|c| {
                                c.as_array().and_then(|arr| {
                                    arr.first().and_then(|first| {
                                        first.get("text").and_then(|t| t.as_str())
                                    })
                                })
                            }) {
                                return Some(content.to_string());
                            }
                        }
                    }
                }
            }
        }
    }

    // Standard OpenAI format
    if let Some(choices) = event.get("choices").and_then(|c| c.as_array()) {
        if let Some(choice) = choices.first() {
            if let Some(delta) = choice.get("delta") {
                if let Some(content) = delta.get("content").and_then(|c| c.as_str()) {
                    return Some(content.to_string());
                }
            }
            if let Some(message) = choice.get("message") {
                if let Some(content) = message.get("content").and_then(|c| c.as_str()) {
                    return Some(content.to_string());
                }
            }
        }
    }

    // ChatGPT Responses API format - direct content field
    if let Some(content) = event.get("content").and_then(|c| c.as_str()) {
        return Some(content.to_string());
    }

    // ChatGPT Responses API format - message field
    if let Some(message) = event.get("message") {
        if let Some(content) = message.get("content").and_then(|c| c.as_str()) {
            return Some(content.to_string());
        }
        if let Some(content) = message.as_str() {
            return Some(content.to_string());
        }
    }

    // ChatGPT Responses API format - response field
    if let Some(response) = event.get("response") {
        if let Some(content) = response.get("content").and_then(|c| c.as_str()) {
            return Some(content.to_string());
        }
        if let Some(content) = response.as_str() {
            return Some(content.to_string());
        }
    }

    // ChatGPT Responses API format - text field
    if let Some(text) = event.get("text").and_then(|t| t.as_str()) {
        return Some(text.to_string());
    }

    // ChatGPT Responses API format - delta field
    if let Some(delta) = event.get("delta") {
        if let Some(content) = delta.get("content").and_then(|c| c.as_str()) {
            return Some(content.to_string());
        }
        if let Some(text) = delta.get("text").and_then(|t| t.as_str()) {
            return Some(text.to_string());
        }
    }

    None
}

fn extract_responses_message(response: &Value) -> Option<String> {
    let output = response.get("output").and_then(Value::as_array)?;
    for item in output {
        if item.get("type").and_then(Value::as_str) != Some("message") {
            continue;
        }
        // Тот же сборщик, что и у response.output_item.done: если бэкенд когда-нибудь снова
        // положит сообщение в response.completed, текст совпадёт байт в байт и дедуп стрима
        // погасит повтор, а не отдаст клиенту вторую копию.
        if let Some(text) = message_item_text(item) {
            return Some(text);
        }
    }

    None
}

/// Текст готового элемента сообщения Responses API: части `output_text`, каждая — со
/// своими аннотациями. Части склеиваются переводом строки; пусто — None.
fn message_item_text(item: &Value) -> Option<String> {
    let parts = item.get("content").and_then(Value::as_array)?;
    let mut texts: Vec<String> = Vec::new();
    for part in parts {
        let Some(text) = part.get("text").and_then(Value::as_str) else {
            continue;
        };
        let annotations = part
            .get("annotations")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        texts.push(clean_model_text(text, annotations));
    }
    if texts.is_empty() {
        return None;
    }
    let text = texts.join("\n");
    if text.trim().is_empty() {
        return None;
    }
    Some(text)
}

/// Слово модели — в виде, который переживёт Telegram и Пульт.
///
/// Что приходит от бэкенда (проба 09.09, три запроса с web_search):
///  * ссылки, которые бэкенд смог сопоставить со своим поиском, уже стоят в тексте как
///    `([host](https://…?utm_source=openai))`, и на этот диапазон указывает аннотация
///    `url_citation` со `start_index`/`end_index` (в кодовых точках);
///  * ссылки на поиск из ПРЕДЫДУЩЕГО запроса (у агента каждая итерация — новый запрос)
///    бэкенд сопоставить не может и оставляет сырой маркер `\u{E200}cite\u{E202}turn1search3\u{E201}`
///    в символах частной области — в Telegram это мусор, восстановить адрес нельзя ни здесь,
///    ни у клиента: данных нет ни в одном событии стрима.
///
/// Что делаем: диапазон аннотации без готовой ссылки оборачиваем в `[текст](url)`; из всех
/// адресов снимаем `utm_source=openai|chatgpt.com`; скобки ВНУТРИ адреса ссылки кодируем
/// `%28`/`%29` — markdown-парсер Telethon и Пульта обрывает адрес на первой `)`; сырые
/// маркеры цитат снимаем вместе с оставшимся после них двойным пробелом.
fn clean_model_text(text: &str, annotations: &[Value]) -> String {
    let mut chars: Vec<char> = text.chars().collect();
    let mut spans: Vec<(usize, usize, String)> = annotations
        .iter()
        .filter_map(|annotation| {
            let (url, _title) = visible_url_citation(annotation)?;
            let citation = citation_body(annotation)?;
            let start = citation.get("start_index").and_then(Value::as_u64)? as usize;
            let end = citation.get("end_index").and_then(Value::as_u64)? as usize;
            (start < end && end <= chars.len()).then_some((start, end, url))
        })
        .collect();
    // С конца — чтобы замены не сдвигали индексы ещё не обработанных диапазонов;
    // пересекающийся с уже применённым диапазон пропускаем.
    spans.sort_by_key(|a| std::cmp::Reverse(a.0));
    let mut floor = usize::MAX;
    for (start, end, url) in spans {
        if end > floor {
            continue;
        }
        let span: String = chars[start..end].iter().collect();
        if span.contains("](") {
            // Готовая markdown-ссылка бэкенда: адрес почистит общий проход ниже.
            floor = start;
            continue;
        }
        let label = span
            .trim()
            .trim_matches(|c| c == '(' || c == ')' || c == '[' || c == ']')
            .trim();
        let label = if label.is_empty() {
            Url::parse(&url)
                .ok()
                .and_then(|u| u.host_str().map(str::to_string))
                .unwrap_or_else(|| "источник".to_string())
        } else {
            label.replace('[', "(").replace(']', ")")
        };
        let replacement = format!("[{label}]({url})");
        chars.splice(start..end, replacement.chars());
        floor = start;
    }
    let joined: String = chars.into_iter().collect();
    let (stripped, had_markers) = strip_cite_markers(&joined);
    let cleaned = encode_link_parens(&strip_utm(&stripped));
    if had_markers {
        tidy_after_markers(&cleaned)
    } else {
        cleaned
    }
}

/// Тело аннотации: `{"type":"url_citation", url, …}` или вложенное `{"url_citation": {…}}`.
fn citation_body(annotation: &Value) -> Option<&Value> {
    if annotation.get("type").and_then(Value::as_str) == Some("url_citation") {
        Some(annotation.get("url_citation").unwrap_or(annotation))
    } else {
        annotation.get("url_citation")
    }
}

/// Сырые маркеры цитат бэкенда: `\u{E200}…\u{E201}` целиком, затем одиночные символы
/// U+E200..=U+E2FF. Возвращает текст и признак «что-то снято».
fn strip_cite_markers(text: &str) -> (String, bool) {
    const OPEN: char = '\u{E200}';
    const CLOSE: char = '\u{E201}';
    let is_marker = |c: char| ('\u{E200}'..='\u{E2FF}').contains(&c);
    if !text.chars().any(is_marker) {
        return (text.to_string(), false);
    }
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c == OPEN {
            // Закрывающий маркер не дальше 200 знаков: незакрытый блок не должен съесть ответ.
            let close = chars[i + 1..]
                .iter()
                .take(200)
                .position(|&x| x == CLOSE)
                .map(|offset| i + 1 + offset);
            if let Some(close) = close {
                i = close + 1;
                continue;
            }
        }
        if !is_marker(c) {
            out.push(c);
        }
        i += 1;
    }
    (out, true)
}

/// После снятого маркера остаются « .», « ,» и двойные пробелы перед переводом строки.
fn tidy_after_markers(text: &str) -> String {
    let mut out = text.replace("  ", " ");
    while out.contains("  ") {
        out = out.replace("  ", " ");
    }
    out = out
        .replace(" .", ".")
        .replace(" ,", ",")
        .replace(" \n", "\n")
        .replace("\n ", "\n");
    out.trim_end().to_string()
}

/// `?utm_source=openai` / `&utm_source=chatgpt.com` из любого адреса в тексте.
fn strip_utm(text: &str) -> String {
    const TAGS: [&str; 2] = ["utm_source=openai", "utm_source=chatgpt.com"];
    let mut out = text.to_string();
    for tag in TAGS {
        let mut from = 0usize;
        while let Some(rel) = out[from..].find(tag) {
            let pos = from + rel;
            let after_pos = pos + tag.len();
            let before = out[..pos].chars().next_back();
            let after = out[after_pos..].chars().next();
            match before {
                // `?utm…&x=1` → `?x=1`: параметр снимаем, `?` оставляем.
                Some('?') if after == Some('&') => {
                    out.replace_range(pos..after_pos + 1, "");
                    from = pos;
                }
                Some('?') | Some('&') => {
                    out.replace_range(pos - 1..after_pos, "");
                    from = pos - 1;
                }
                // Не параметр адреса (упомянут словами) — не трогаем.
                _ => from = after_pos,
            }
        }
    }
    out
}

/// Скобки внутри адреса markdown-ссылки `[t](…)` → `%28`/`%29`. Конец адреса — парная `)`
/// или пробел. Вложенность считается, поэтому `…/Ключ_(значения)` кодируется, а закрывающая
/// скобка ссылки остаётся.
fn encode_link_parens(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len() + 16);
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == ']' && i + 1 < chars.len() && chars[i + 1] == '(' {
            out.push(']');
            out.push('(');
            i += 2;
            let mut depth = 1usize;
            while i < chars.len() {
                let c = chars[i];
                if c.is_whitespace() {
                    break;
                }
                if c == '(' {
                    depth += 1;
                    out.push_str("%28");
                } else if c == ')' {
                    depth -= 1;
                    if depth == 0 {
                        out.push(')');
                        i += 1;
                        break;
                    }
                    out.push_str("%29");
                } else {
                    out.push(c);
                }
                i += 1;
            }
            continue;
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

fn visible_url_citation(annotation: &Value) -> Option<(String, Option<String>)> {
    let citation = if annotation.get("type").and_then(Value::as_str) == Some("url_citation") {
        annotation.get("url_citation").unwrap_or(annotation)
    } else {
        annotation.get("url_citation")?
    };
    let raw_url = citation.get("url").and_then(Value::as_str)?;
    if raw_url.len() > 8 * 1024 {
        return None;
    }
    let parsed = Url::parse(raw_url).ok()?;
    if !matches!(parsed.scheme(), "http" | "https")
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
    {
        return None;
    }

    let title = citation
        .get("title")
        .and_then(Value::as_str)
        .map(|title| title.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|title| !title.is_empty())
        .map(|title| title.chars().take(160).collect());
    Some((parsed.to_string(), title))
}

fn upstream_error_message(status: reqwest::StatusCode, body: &str) -> String {
    let parsed = serde_json::from_str::<Value>(body).ok();
    let error = parsed
        .as_ref()
        .and_then(|value| value.get("error").or(Some(value)));

    let code = error
        .and_then(|value| value.get("code").or_else(|| value.get("type")))
        .and_then(Value::as_str)
        .filter(|value| {
            !value.is_empty()
                && value.len() <= 64
                && value
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
        });
    let message = error
        .and_then(|value| value.get("message"))
        .and_then(Value::as_str)
        .and_then(bounded_error_text);

    match (code, message) {
        (Some(code), Some(message)) => {
            format!("Upstream error {status} ({code}): {message}")
        }
        (Some(code), None) => format!("Upstream error {status} ({code})"),
        (None, Some(message)) => format!("Upstream error {status}: {message}"),
        (None, None) => format!("Upstream error {status}"),
    }
}

/// Upstream refused the credentials themselves, not the request.
///
/// Deliberately status-only: the body of a 401 is redacted before it reaches this code, and
/// an expired token, a revoked session and a rotated account all look the same from here —
/// in every one of those cases the honest move is the same, try the other subscription.
fn subscription_auth_error(status: reqwest::StatusCode) -> bool {
    status == reqwest::StatusCode::UNAUTHORIZED
}

fn account_failure_attempt(
    slot: &str,
    status: reqwest::StatusCode,
    body: &str,
) -> Option<RelayTerminalAttempt> {
    let (code, resets_at, resets_in_seconds) = if subscription_quota_error(status, body) {
        let (at, within) = quota_reset_from_body(body);
        (TERMINAL_QUOTA, at, within)
    } else if subscription_auth_error(status) {
        (TERMINAL_NEEDS_LOGIN, None, None)
    } else {
        return None;
    };
    Some(RelayTerminalAttempt {
        slot: slot.to_string(),
        code: code.to_string(),
        status: status.as_u16(),
        resets_at,
        resets_in_seconds,
    })
}

fn mixed_account_failure_message(attempts: &[RelayTerminalAttempt]) -> String {
    let quota_slots = attempts
        .iter()
        .filter(|attempt| attempt.code == TERMINAL_QUOTA)
        .map(|attempt| attempt.slot.as_str())
        .collect::<Vec<_>>()
        .join(", ");
    let login_slots = attempts
        .iter()
        .filter(|attempt| attempt.code == TERMINAL_NEEDS_LOGIN)
        .map(|attempt| attempt.slot.as_str())
        .collect::<Vec<_>>()
        .join(", ");
    format!(
        "OpenAI subscriptions are unavailable for different reasons: usage limit on {quota_slots}; credentials need login on {login_slots}. Sign in the credential-rejected slot or wait for the quota reset."
    )
}

fn subscription_quota_error(status: reqwest::StatusCode, body: &str) -> bool {
    if status != reqwest::StatusCode::TOO_MANY_REQUESTS {
        return false;
    }
    let parsed = serde_json::from_str::<Value>(body).ok();
    let error = parsed
        .as_ref()
        .and_then(|value| value.get("error").or(Some(value)));
    let code = error
        .and_then(|value| value.get("code").or_else(|| value.get("type")))
        .and_then(Value::as_str);
    code == Some("usage_limit_reached") || body.contains("usage_limit_reached")
}

fn bounded_error_text(raw: &str) -> Option<String> {
    let normalized = raw.split_whitespace().collect::<Vec<_>>().join(" ");
    if normalized.is_empty() {
        return None;
    }

    let mut chars = normalized.chars();
    let mut bounded: String = chars.by_ref().take(MAX_UPSTREAM_ERROR_CHARS).collect();
    if chars.next().is_some() {
        bounded.push('…');
    }
    Some(bounded)
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::Engine;
    use serde_json::json;
    use std::sync::atomic::{AtomicUsize, Ordering as AtomicOrdering};
    use std::sync::{Arc, Mutex as StdMutex};
    use tempfile::tempdir;

    // ─────────────────────────────────────────────────────────────────────────
    //  РЕАЛЬНЫЙ ПУТЬ. Здесь поднимается настоящий HTTP-апстрим на 127.0.0.1, а ход
    //  идёт через настоящий `stream_chat_completions`: его `tokio::spawn`, его
    //  reqwest, его SSE-разбор, его аккаунт-роутер. Проверяются ровно те байты,
    //  которые main.rs положит в SSE (`serde_json::to_string(&event)`).
    //
    //  ⚠ УРОК НОЧИ 11.08.2026: девятнадцать зелёных тестов не поймали дефект,
    //  потому что звали функцию НАПРЯМУЮ, а не тем путём, которым ходит она.
    //  Тест на `terminal_event(...)` был бы зелёным по построению и не заметил бы
    //  ни того, что реле не доходит до этой ветки, ни того, что чанк по дороге
    //  теряет поле.
    // ─────────────────────────────────────────────────────────────────────────

    fn lever_guard() -> std::sync::MutexGuard<'static, ()> {
        test_lever_guard()
    }

    fn set_upstream_url(url: Option<String>) {
        set_test_upstream_url(url);
    }

    fn write_account(root: &std::path::Path, slot: &str, account_id: &str) {
        let home = root.join("accounts").join(slot);
        std::fs::create_dir_all(&home).unwrap();
        let payload = json!({
            "email": format!("{slot}@example.test"),
            "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"}
        });
        let encoded = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .encode(serde_json::to_vec(&payload).unwrap());
        let jwt = format!("e30.{encoded}.c2ln");
        let auth = json!({
            "OPENAI_API_KEY": null,
            "tokens": {
                "id_token": jwt,
                "access_token": format!("access-{slot}"),
                "refresh_token": format!("refresh-{slot}"),
                "account_id": account_id
            },
            "last_refresh": chrono::Utc::now().to_rfc3339()
        });
        std::fs::write(
            home.join("auth.json"),
            serde_json::to_vec_pretty(&auth).unwrap(),
        )
        .unwrap();
    }

    /// Чем апстрим отвечает: отказом со статусом, порванным байтовым стримом,
    /// битым JSON внутри SSE или событием `response.failed` поверх 200 OK.
    ///
    /// The two transport stages are deliberately distinct: `TornAfterText`
    /// fails in `bytes_stream()` after a 200 head, while
    /// `PreResponseHeadFailure` closes raw TCP before any HTTP response bytes so
    /// `send().await` itself fails.
    #[derive(Clone)]
    enum Upstream {
        Status(u16, String),
        TornAfterText,
        PreResponseHeadFailure,
        BadJson,
        FailedEvent,
        CompletedThenHang,
        CompletedThenError,
        CompletedThenTail,
        FinishReasonThenError,
        MixedAccounts {
            first: u16,
            second: u16,
            accounts_seen: Arc<StdMutex<Vec<String>>>,
        },
        /// Response head is immediate; its quota JSON body is gated.
        GatedQuotaBody {
            body_started: Arc<tokio::sync::Semaphore>,
            release_body: Arc<tokio::sync::Semaphore>,
            body_dropped: Arc<tokio::sync::Semaphore>,
        },
        /// One SSE event is emitted, then the upstream body remains pending.
        EventThenPending {
            event_sent: Arc<tokio::sync::Semaphore>,
            body_dropped: Arc<tokio::sync::Semaphore>,
        },
        /// Primary returns a quota response whose account-state commit is gated;
        /// the standby would complete if replay were incorrectly allowed after drop.
        QuotaThenStandbyCompleted,
        /// First request: established 200 stream, heartbeat, bytes_stream error.
        /// Second request: a complete response with no usage object.
        ErrorBeforeFirstEventThenCompleted,
        /// A useful text delta followed by a bytes_stream error.
        TextThenError,
        /// A valid response.completed without usage.
        CompletedWithoutUsage,
        Sse(&'static str),
        /// First request closes cleanly before completed; second completes.
        CleanEofThenCompleted,
        /// First request leaves an unterminated SSE line; second completes.
        SseTailThenCompleted,
        /// First request leaves an incomplete UTF-8 sequence; second completes.
        Utf8TailThenCompleted,
    }

    async fn spawn_upstream(mode: Upstream) -> (String, Arc<AtomicUsize>) {
        use axum::body::Body;
        use axum::response::Response;
        let hits = Arc::new(AtomicUsize::new(0));

        // A real pre-response-head failure: accept TCP and close it without
        // writing a single HTTP byte. reqwest::send().await, not bytes_stream(),
        // must fail on both the original request and its one safe replay.
        if matches!(mode, Upstream::PreResponseHeadFailure) {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let addr = listener.local_addr().unwrap();
            let counter = hits.clone();
            tokio::spawn(async move {
                for _ in 0..2 {
                    let Ok((socket, _)) = listener.accept().await else {
                        break;
                    };
                    counter.fetch_add(1, AtomicOrdering::SeqCst);
                    drop(socket);
                }
            });
            return (format!("http://{addr}/responses"), hits);
        }

        let counter = hits.clone();
        let app = axum::Router::new().fallback(axum::routing::any(
            move |headers: axum::http::HeaderMap| {
            let mode = mode.clone();
            let counter = counter.clone();
            async move {
                let hit = counter.fetch_add(1, AtomicOrdering::SeqCst);
                let completed = || {
                    Response::builder()
                        .status(200)
                        .header("content-type", "text/event-stream")
                        .body(Body::from(
                            "data: {\"type\":\"response.output_item.done\",\"item\":{\"type\":\"message\",\"role\":\"assistant\",\"content\":[{\"type\":\"output_text\",\"text\":\"ok\",\"annotations\":[]}]}}\n\n\
                             data: {\"type\":\"response.completed\",\"response\":{\"model\":\"gpt-5.6-sol\"}}\n\n",
                        ))
                        .unwrap()
                };
                match mode {
                    Upstream::Status(code, body) => Response::builder()
                        .status(code)
                        .header("content-type", "application/json")
                        .body(Body::from(body))
                        .unwrap(),
                    Upstream::TornAfterText => {
                        // Готовый message item, затем обрыв: draft deltas are withheld
                        // by the current text contract, so they cannot prove tx.send.
                        // Сначала кусок настоящего текста, затем обрыв: это тот самый
                        // случай, где ответ уже начался, а канал умер на середине.
                        // Pausing makes the response head and first delta observable
                        // before the injected body failure.
                        let chunks = async_stream::stream! {
                            yield Ok::<_, std::io::Error>(bytes::Bytes::from_static(
                                b"data: {\"type\":\"response.output_item.done\",\"item\":{\"type\":\"message\",\"role\":\"assistant\",\"content\":[{\"type\":\"output_text\",\"text\":\"\xd1\x87\xd0\xb0\"}]}}\n\n",
                            ));
                            tokio::time::sleep(std::time::Duration::from_millis(80)).await;
                            yield Err(std::io::Error::other("upstream tore the byte stream"));
                        };
                        Response::builder()
                            .status(200)
                            .header("content-type", "text/event-stream")
                            .body(Body::from_stream(chunks))
                            .unwrap()
                    }
                    Upstream::PreResponseHeadFailure => unreachable!("handled before axum"),
                    Upstream::BadJson => Response::builder()
                        .status(200)
                        .header("content-type", "text/event-stream")
                        .body(Body::from("data: {\"type\": broken\n\n"))
                        .unwrap(),
                    Upstream::FailedEvent => Response::builder()
                        .status(200)
                        .header("content-type", "text/event-stream")
                        .body(Body::from(
                            "data: {\"type\":\"response.failed\",\"response\":{\"model\":\"gpt-5.6-sol\",\
                             \"error\":{\"message\":\"internal stream failure\"}}}\n\n",
                        ))
                        .unwrap(),
                    Upstream::Sse(frames) => Response::builder()
                        .status(200).header("content-type", "text/event-stream")
                        .body(Body::from(frames)).unwrap(),
                    Upstream::CompletedWithoutUsage => completed(),
                    Upstream::CompletedThenHang => {
                        let chunks = async_stream::stream! {
                            yield Ok::<_, std::io::Error>(bytes::Bytes::from_static(
                                b"data: {\"type\":\"response.completed\",\"response\":{\"model\":\"gpt-5.6-sol\"}}\n\n",
                            ));
                            std::future::pending::<()>().await;
                        };
                        Response::builder().status(200).header("content-type", "text/event-stream")
                            .body(Body::from_stream(chunks)).unwrap()
                    }
                    Upstream::CompletedThenError => {
                        let chunks = async_stream::stream! {
                            yield Ok::<_, std::io::Error>(bytes::Bytes::from_static(
                                b"data: {\"type\":\"response.completed\",\"response\":{\"model\":\"gpt-5.6-sol\"}}\n\n",
                            ));
                            tokio::time::sleep(std::time::Duration::from_millis(80)).await;
                            yield Err(std::io::Error::other("error after completion"));
                        };
                        Response::builder().status(200).header("content-type", "text/event-stream")
                            .body(Body::from_stream(chunks)).unwrap()
                    }
                    Upstream::CompletedThenTail => Response::builder()
                        .status(200).header("content-type", "text/event-stream")
                        .body(Body::from(
                            "data: {\"type\":\"response.completed\",\"response\":{\"model\":\"gpt-5.6-sol\"}}\n\nunterminated tail",
                        )).unwrap(),
                    Upstream::FinishReasonThenError => {
                        let chunks = async_stream::stream! {
                            yield Ok::<_, std::io::Error>(bytes::Bytes::from_static(
                                b"data: {\"type\":\"response.in_progress\",\"finish_reason\":\"stop\"}\n\n",
                            ));
                            tokio::time::sleep(std::time::Duration::from_millis(80)).await;
                            yield Err(std::io::Error::other("error after visible finish"));
                        };
                        Response::builder().status(200).header("content-type", "text/event-stream")
                            .body(Body::from_stream(chunks)).unwrap()
                    }
                    Upstream::MixedAccounts {
                        first,
                        second,
                        accounts_seen,
                    } => {
                        accounts_seen
                            .lock()
                            .unwrap_or_else(|poisoned| poisoned.into_inner())
                            .push(
                                headers
                                    .get("chatgpt-account-id")
                                    .and_then(|value| value.to_str().ok())
                                    .unwrap_or("<missing>")
                                    .to_string(),
                            );
                        let code = if hit == 0 { first } else { second };
                        let body = if code == 429 {
                            r#"{"error":{"code":"usage_limit_reached","resets_in_seconds":60}}"#
                        } else {
                            r#"{"error":{"message":"expired"}}"#
                        };
                        Response::builder().status(code).header("content-type", "application/json")
                            .body(Body::from(body)).unwrap()
                    }
                    Upstream::GatedQuotaBody {
                        body_started,
                        release_body,
                        body_dropped,
                    } => {
                        struct Dropped(Arc<tokio::sync::Semaphore>);
                        impl Drop for Dropped {
                            fn drop(&mut self) {
                                self.0.add_permits(1);
                            }
                        }
                        let chunks = async_stream::stream! {
                            let _dropped = Dropped(body_dropped);
                            body_started.add_permits(1);
                            release_body.acquire().await.unwrap().forget();
                            yield Ok::<_, std::io::Error>(bytes::Bytes::from_static(
                                br#"{"error":{"code":"usage_limit_reached"}}"#,
                            ));
                        };
                        Response::builder().status(429).header("content-type", "application/json")
                            .body(Body::from_stream(chunks)).unwrap()
                    }
                    Upstream::EventThenPending {
                        event_sent,
                        body_dropped,
                    } => {
                        struct Dropped(Arc<tokio::sync::Semaphore>);
                        impl Drop for Dropped {
                            fn drop(&mut self) {
                                self.0.add_permits(1);
                            }
                        }
                        let chunks = async_stream::stream! {
                            let _dropped = Dropped(body_dropped);
                            event_sent.add_permits(1);
                            yield Ok::<_, std::io::Error>(bytes::Bytes::from_static(
                                b"data: {\"type\":\"response.output_item.done\",\"item\":{\"type\":\"message\",\"role\":\"assistant\",\"content\":[{\"type\":\"output_text\",\"text\":\"visible\"}]}}\n\n",
                            ));
                            std::future::pending::<()>().await;
                        };
                        Response::builder().status(200).header("content-type", "text/event-stream")
                            .body(Body::from_stream(chunks)).unwrap()
                    }
                    Upstream::QuotaThenStandbyCompleted if hit > 0 => completed(),
                    Upstream::QuotaThenStandbyCompleted => Response::builder()
                        .status(429)
                        .header("content-type", "application/json")
                        .body(Body::from(
                            r#"{"error":{"code":"usage_limit_reached","resets_in_seconds":60}}"#,
                        ))
                        .unwrap(),
                    Upstream::ErrorBeforeFirstEventThenCompleted if hit > 0 => completed(),
                    Upstream::ErrorBeforeFirstEventThenCompleted => {
                        let chunks = async_stream::stream! {
                            yield Ok::<_, std::io::Error>(bytes::Bytes::from_static(b": keepalive\n\n"));
                            tokio::time::sleep(std::time::Duration::from_millis(40)).await;
                            yield Err(std::io::Error::other("injected before first event"));
                        };
                        Response::builder()
                            .status(200)
                            .header("content-type", "text/event-stream")
                            .body(Body::from_stream(chunks))
                            .unwrap()
                    }
                    Upstream::TextThenError => {
                        let chunks = async_stream::stream! {
                            yield Ok::<_, std::io::Error>(bytes::Bytes::from_static(
                                b"data: {\"type\":\"response.output_item.done\",\"item\":{\"type\":\"message\",\"role\":\"assistant\",\"content\":[{\"type\":\"output_text\",\"text\":\"partial\"}]}}\n\n",
                            ));
                            tokio::time::sleep(std::time::Duration::from_millis(40)).await;
                            yield Err(std::io::Error::other("injected after partial output"));
                        };
                        Response::builder()
                            .status(200)
                            .header("content-type", "text/event-stream")
                            .body(Body::from_stream(chunks))
                            .unwrap()
                    }
                    Upstream::CleanEofThenCompleted
                    | Upstream::SseTailThenCompleted
                    | Upstream::Utf8TailThenCompleted
                        if hit > 0 => completed(),
                    Upstream::CleanEofThenCompleted => Response::builder()
                        .status(200)
                        .header("content-type", "text/event-stream")
                        .body(Body::empty())
                        .unwrap(),
                    Upstream::SseTailThenCompleted => Response::builder()
                        .status(200)
                        .header("content-type", "text/event-stream")
                        .body(Body::from("data: {\"type\":\"response.in_progress\"}"))
                        .unwrap(),
                    Upstream::Utf8TailThenCompleted => Response::builder()
                        .status(200)
                        .header("content-type", "text/event-stream")
                        .body(Body::from(vec![0xd1]))
                        .unwrap(),
                }
            }
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            let _ = axum::serve(listener, app).await;
        });
        (format!("http://{addr}/responses"), hits)
    }

    /// Один ход целиком: настоящий роутер, настоящий запрос, настоящий SSE.
    /// Возвращает ровно те чанки, которые уедут клиенту, — и строкой, и разобранными.
    /// Строка нужна отдельно: `serde_json::Value` сортирует ключи, а порядок полей на
    /// проводе — часть обещания «выключенный рычаг = сегодняшний чанк».
    async fn run_turn_with_account_count(
        mode: Upstream,
        naming: TerminalNaming,
        account_count: usize,
    ) -> (Vec<(String, Value)>, usize, AccountRouter) {
        let root = tempdir().unwrap();
        write_account(root.path(), "primary", "acct-primary");
        if account_count > 1 {
            write_account(root.path(), "secondary", "acct-secondary");
        }
        let router = AccountRouter::load(root.path()).await.unwrap();
        let (url, hits) = spawn_upstream(mode).await;
        set_upstream_url(Some(url));
        force_terminal_naming(naming);

        let config = Config {
            codex_home: root.path().to_path_buf(),
            chatgpt_base_url: "https://chatgpt.com/backend-api/codex".to_string(),
            model: "gpt-5.6-sol".to_string(),
            user_instructions: None,
            reasoning_effort: None,
            instructions_mode: Some("minimal".to_string()),
            parallel_tool_calls: false,
        };
        let request: ChatRequest = serde_json::from_value(json!({
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "привет"}]
        }))
        .unwrap();

        let mut rx = stream_chat_completions(&config, router.clone(), request, Client::new())
            .await
            .unwrap();
        let mut wire = Vec::new();
        while let Some(item) = rx.recv().await {
            match item {
                // Ровно то, что делает main.rs перед отправкой в SSE.
                Ok(event) => {
                    let raw = serde_json::to_string(&event).unwrap();
                    let parsed = serde_json::from_str(&raw).unwrap();
                    wire.push((raw, parsed));
                }
                Err(error) => {
                    let raw = json!({"transport_error": error.to_string()}).to_string();
                    let parsed = serde_json::from_str(&raw).unwrap();
                    wire.push((raw, parsed));
                }
            }
        }
        set_upstream_url(None);
        (wire, hits.load(AtomicOrdering::SeqCst), router)
    }

    async fn run_turn_with_hits(
        mode: Upstream,
        naming: TerminalNaming,
    ) -> (Vec<(String, Value)>, usize) {
        let (wire, hits, _) = run_turn_with_account_count(mode, naming, 1).await;
        (wire, hits)
    }

    async fn run_turn(mode: Upstream, naming: TerminalNaming) -> Vec<(String, Value)> {
        run_turn_with_hits(mode, naming).await.0
    }

    fn last(wire: &[(String, Value)]) -> &Value {
        &wire.last().expect("реле обязано сказать хоть что-то").1
    }

    fn last_raw(wire: &[(String, Value)]) -> &str {
        &wire.last().expect("реле обязано сказать хоть что-то").0
    }

    fn torn_events(wire: &[(String, Value)]) -> Vec<&Value> {
        wire.iter()
            .map(|(_, event)| event)
            .filter(|event| event["relay_terminal"]["code"] == TERMINAL_TORN)
            .collect()
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn completed_is_final_even_when_transport_hangs_errors_or_has_tail() {
        let _guard = lever_guard();
        for mode in [
            Upstream::CompletedThenHang,
            Upstream::CompletedThenError,
            Upstream::CompletedThenTail,
        ] {
            let (wire, hits) = tokio::time::timeout(
                std::time::Duration::from_secs(1),
                run_turn_with_hits(mode, TerminalNaming::Field),
            )
            .await
            .expect("response.completed must close downstream promptly");
            assert_eq!(hits, 1);
            assert_eq!(
                wire.len(),
                1,
                "only the successfully forwarded response.completed envelope is visible"
            );
            assert!(wire[0].1.get("relay_terminal").is_none());
            assert!(torn_events(&wire).is_empty());
        }
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn finish_reason_only_is_visible_and_forbids_replay() {
        let _guard = lever_guard();
        let (wire, hits) =
            run_turn_with_hits(Upstream::FinishReasonThenError, TerminalNaming::Field).await;
        assert_eq!(hits, 1);
        let finish_events = wire
            .iter()
            .filter(|(_, event)| event["choices"][0]["finish_reason"] == "stop")
            .count();
        assert_eq!(finish_events, 1, "finish event must not be replayed");
        assert_eq!(wire[0].1["choices"][0]["finish_reason"], "stop");
        assert_eq!(torn_events(&wire).len(), 1);
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn mixed_account_failures_preserve_both_facts_in_either_order() {
        let _guard = lever_guard();
        for (first, second) in [(401, 429), (429, 401)] {
            let accounts_seen = Arc::new(StdMutex::new(Vec::new()));
            let (wire, hits, router) = run_turn_with_account_count(
                Upstream::MixedAccounts {
                    first,
                    second,
                    accounts_seen: accounts_seen.clone(),
                },
                TerminalNaming::Field,
                2,
            )
            .await;
            assert_eq!(hits, 2, "actual account failover must occur");
            assert_eq!(
                *accounts_seen
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner()),
                ["acct-primary", "acct-secondary"],
                "the replay must use standby credentials"
            );
            assert_eq!(router.active_slot().await, "secondary");
            let slots = router.describe().await;
            assert!(slots
                .iter()
                .find(|slot| slot.slot == "primary")
                .is_some_and(|slot| slot.cooldown_seconds_left > 0));
            let terminal = last(&wire);
            assert_eq!(
                terminal["relay_terminal"]["code"],
                TERMINAL_ACCOUNTS_UNAVAILABLE
            );
            let attempts = terminal["relay_terminal"]["attempts"].as_array().unwrap();
            assert_eq!(attempts.len(), 2);
            assert_eq!(attempts[0]["slot"], "primary");
            assert_eq!(attempts[1]["slot"], "secondary");
            assert_eq!(attempts[0]["status"], first);
            assert_eq!(attempts[1]["status"], second);
            let expected_codes = if first == 401 {
                [TERMINAL_NEEDS_LOGIN, TERMINAL_QUOTA]
            } else {
                [TERMINAL_QUOTA, TERMINAL_NEEDS_LOGIN]
            };
            assert_eq!(attempts[0]["code"], expected_codes[0]);
            assert_eq!(attempts[1]["code"], expected_codes[1]);
            let quota = attempts
                .iter()
                .find(|attempt| attempt["code"] == TERMINAL_QUOTA)
                .unwrap();
            assert_eq!(quota["resets_in_seconds"], 60);
            assert!(terminal["relay_terminal"]["message"]
                .as_str()
                .unwrap()
                .contains("different reasons"));
        }
    }

    fn test_turn(root: &std::path::Path) -> (Config, ChatRequest) {
        let config = Config {
            codex_home: root.to_path_buf(),
            chatgpt_base_url: String::new(),
            model: "gpt-5.6-sol".to_string(),
            user_instructions: None,
            reasoning_effort: None,
            instructions_mode: Some("minimal".to_string()),
            parallel_tool_calls: false,
        };
        let request = serde_json::from_value(json!({
            "model": "gpt-5.6-sol", "messages": [{"role":"user", "content":"x"}]
        }))
        .unwrap();
        (config, request)
    }

    async fn assert_router_unchanged(router: &AccountRouter, expected_active: &str) {
        assert_eq!(router.active_slot().await, expected_active);
        assert!(router
            .describe()
            .await
            .iter()
            .all(|slot| slot.cooldown_seconds_left == 0));
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn cancellation_wins_while_initial_account_lease_is_awaited() {
        let _guard = lever_guard();
        let root = tempdir().unwrap();
        write_account(root.path(), "primary", "acct-primary");
        write_account(root.path(), "secondary", "acct-secondary");
        let router = AccountRouter::load(root.path()).await.unwrap();
        // Corrupt the active profile only after load. If cancellation did not
        // cancel lease_any(), releasing the gate would make it park primary,
        // lease secondary, and persist both an active switch and a cooldown.
        std::fs::write(
            root.path().join("accounts/primary/auth.json"),
            b"not valid json",
        )
        .unwrap();
        let gate = crate::core::account_router::TestGate::new();
        router.gate_next_lease(gate.clone());
        let (config, request) = test_turn(root.path());

        let rx = stream_chat_completions(&config, router.clone(), request, Client::new())
            .await
            .unwrap();
        tokio::time::timeout(std::time::Duration::from_secs(1), gate.wait_reached())
            .await
            .expect("producer must reach initial lease boundary");
        drop(rx);
        gate.release();
        tokio::time::timeout(std::time::Duration::from_secs(1), async {
            loop {
                if Arc::strong_count(&gate) == 1 {
                    break;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("cancelled initial lease future must be dropped");
        assert_router_unchanged(&router, "primary").await;
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn cancellation_drops_error_body_without_failover_or_cooldown() {
        let _guard = lever_guard();
        let root = tempdir().unwrap();
        write_account(root.path(), "primary", "acct-primary");
        write_account(root.path(), "secondary", "acct-secondary");
        let router = AccountRouter::load(root.path()).await.unwrap();
        let body_started = Arc::new(tokio::sync::Semaphore::new(0));
        let release_body = Arc::new(tokio::sync::Semaphore::new(0));
        let body_dropped = Arc::new(tokio::sync::Semaphore::new(0));
        let (url, hits) = spawn_upstream(Upstream::GatedQuotaBody {
            body_started: body_started.clone(),
            release_body: release_body.clone(),
            body_dropped: body_dropped.clone(),
        })
        .await;
        set_upstream_url(Some(url));
        let (config, request) = test_turn(root.path());
        let rx = stream_chat_completions(&config, router.clone(), request, Client::new())
            .await
            .unwrap();
        tokio::time::timeout(std::time::Duration::from_secs(1), body_started.acquire())
            .await
            .expect("producer must reach post-head error-body read")
            .unwrap()
            .forget();
        drop(rx);
        release_body.add_permits(1);
        tokio::time::timeout(std::time::Duration::from_secs(1), body_dropped.acquire())
            .await
            .expect("cancellation must drop upstream error body")
            .unwrap()
            .forget();
        set_upstream_url(None);
        assert_eq!(hits.load(AtomicOrdering::SeqCst), 1);
        assert_router_unchanged(&router, "primary").await;
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn actual_tx_send_failure_cancels_upstream_and_forbids_replay_or_state_change() {
        let _guard = lever_guard();
        let root = tempdir().unwrap();
        write_account(root.path(), "primary", "acct-primary");
        let router = AccountRouter::load(root.path()).await.unwrap();
        let body_dropped = Arc::new(tokio::sync::Semaphore::new(0));
        let (url, hits) = spawn_upstream(Upstream::EventThenPending {
            event_sent: Arc::new(tokio::sync::Semaphore::new(0)),
            body_dropped: body_dropped.clone(),
        })
        .await;
        set_upstream_url(Some(url));
        let failures_before = TEST_SEND_FAILURES.load(AtomicOrdering::SeqCst);
        let send_gate = TestSendGate::new();
        *TEST_SEND_GATE
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(send_gate.clone());
        let (config, request) = test_turn(root.path());
        let rx = stream_chat_completions(&config, router.clone(), request, Client::new())
            .await
            .unwrap();
        tokio::time::timeout(std::time::Duration::from_secs(1), send_gate.wait_reached())
            .await
            .expect("producer must reach the production tx.send boundary");
        drop(rx);
        send_gate.release();
        tokio::time::timeout(std::time::Duration::from_secs(1), body_dropped.acquire())
            .await
            .expect("tx.send(...).await Err branch must drop the upstream body")
            .unwrap()
            .forget();
        set_upstream_url(None);
        assert_eq!(hits.load(AtomicOrdering::SeqCst), 1);
        assert_eq!(
            TEST_SEND_FAILURES.load(AtomicOrdering::SeqCst),
            failures_before + 1,
            "the observed shutdown must be caused by tx.send(...).await.is_err()",
        );
        assert_router_unchanged(&router, "primary").await;
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    #[allow(clippy::await_holding_lock)]
    async fn receiver_drop_during_account_commit_never_replays_to_standby() {
        let _guard = lever_guard();
        let root = tempdir().unwrap();
        write_account(root.path(), "primary", "acct-primary");
        write_account(root.path(), "secondary", "acct-secondary");
        let router = AccountRouter::load(root.path()).await.unwrap();
        let (commit_gate, commit_reached, release_commit) = TestCommitGate::new();
        router.gate_next_commit(commit_gate);
        let (url, hits) = spawn_upstream(Upstream::QuotaThenStandbyCompleted).await;
        set_upstream_url(Some(url));
        let (config, request) = test_turn(root.path());
        let rx = stream_chat_completions(&config, router.clone(), request, Client::new())
            .await
            .unwrap();

        tokio::task::spawn_blocking(move || {
            commit_reached
                .recv_timeout(std::time::Duration::from_secs(2))
                .expect("producer must hold the account-state commit permit");
        })
        .await
        .unwrap();

        let (drop_started_tx, drop_started_rx) = std::sync::mpsc::channel();
        let drop_thread = std::thread::spawn(move || {
            drop_started_tx.send(()).unwrap();
            drop(rx);
        });
        drop_started_rx
            .recv_timeout(std::time::Duration::from_secs(1))
            .expect("receiver drop thread must start");
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        release_commit.send(()).unwrap();
        tokio::task::spawn_blocking(move || drop_thread.join().unwrap())
            .await
            .unwrap();

        tokio::time::timeout(std::time::Duration::from_secs(1), async {
            loop {
                if router.active_slot().await == "secondary" {
                    break;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("the earlier commit must be allowed to finish");
        tokio::time::sleep(std::time::Duration::from_millis(80)).await;
        set_upstream_url(None);
        assert_eq!(
            hits.load(AtomicOrdering::SeqCst),
            1,
            "receiver drop may lose to an in-flight commit, but must close the channel before standby replay",
        );
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn retries_bytes_stream_error_only_before_first_downstream_event() {
        let _guard = lever_guard();
        let (wire, hits) = run_turn_with_hits(
            Upstream::ErrorBeforeFirstEventThenCompleted,
            TerminalNaming::Off,
        )
        .await;

        assert_eq!(hits, 2, "pre-output transport failure gets one replay");
        assert!(
            torn_events(&wire).is_empty(),
            "successful replay has no terminal"
        );
        let content: Vec<_> = wire
            .iter()
            .filter_map(|(_, event)| event["choices"][0]["delta"]["content"].as_str())
            .collect();
        assert_eq!(content, ["ok"], "failed attempt cannot duplicate output");
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn partial_output_is_never_replayed_and_error_is_not_delta_content() {
        let _guard = lever_guard();
        let (wire, hits) = run_turn_with_hits(Upstream::TextThenError, TerminalNaming::Off).await;

        assert_eq!(
            hits, 1,
            "replay after partial output could duplicate text/tools"
        );
        let content: Vec<_> = wire
            .iter()
            .filter_map(|(_, event)| event["choices"][0]["delta"]["content"].as_str())
            .collect();
        assert_eq!(content, ["partial"]);
        let terminal = torn_events(&wire);
        assert_eq!(terminal.len(), 1);
        assert_eq!(terminal[0]["choices"][0]["finish_reason"], "error");
        assert!(terminal[0]["choices"][0]["delta"].get("content").is_some());
        assert!(terminal[0]["choices"][0]["delta"]["content"].is_null());
        assert!(terminal[0]["relay_terminal"]["message"]
            .as_str()
            .is_some_and(|message| message.contains("byte stream error")));
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn completion_is_not_inferred_from_usage_and_does_not_require_usage() {
        let _guard = lever_guard();
        let (wire, hits) =
            run_turn_with_hits(Upstream::CompletedWithoutUsage, TerminalNaming::Off).await;
        assert_eq!(hits, 1);
        assert!(torn_events(&wire).is_empty());
        assert!(wire.iter().all(|(_, event)| event.get("usage").is_none()));
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn clean_eof_and_nonempty_sse_or_utf8_tail_are_retried() {
        let _guard = lever_guard();
        for mode in [
            Upstream::CleanEofThenCompleted,
            Upstream::SseTailThenCompleted,
            Upstream::Utf8TailThenCompleted,
        ] {
            let (wire, hits) = run_turn_with_hits(mode, TerminalNaming::Field).await;
            assert_eq!(hits, 2);
            assert!(torn_events(&wire).is_empty());
            assert_eq!(
                wire.iter()
                    .filter_map(|(_, event)| event["choices"][0]["delta"]["content"].as_str())
                    .collect::<Vec<_>>(),
                ["ok"]
            );
        }
    }

    /// Три смерти получают три разных имени — и ни одна не притворяется ответом.
    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn the_three_deaths_get_three_different_names() {
        let _guard = lever_guard();

        let quota = run_turn(
            Upstream::Status(
                429,
                r#"{"error":{"code":"usage_limit_reached","resets_in_seconds":3600}}"#.to_string(),
            ),
            TerminalNaming::Field,
        )
        .await;
        let event = last(&quota);
        assert_eq!(event["relay_terminal"]["code"], TERMINAL_QUOTA);
        assert_eq!(event["relay_terminal"]["slot"], "primary");
        assert_eq!(event["relay_terminal"]["resets_in_seconds"], 3600);
        // Час, которого вендор не назвал, остаётся НЕНАЗВАННЫМ, а не выдуманным.
        assert!(event["relay_terminal"].get("resets_at").is_none());

        let needs_login = run_turn(
            Upstream::Status(401, r#"{"error":{"message":"expired"}}"#.to_string()),
            TerminalNaming::Field,
        )
        .await;
        assert_eq!(
            last(&needs_login)["relay_terminal"]["code"],
            TERMINAL_NEEDS_LOGIN
        );

        let broken = run_turn(
            Upstream::Status(500, r#"{"error":{"message":"boom"}}"#.to_string()),
            TerminalNaming::Field,
        )
        .await;
        assert_eq!(
            last(&broken)["relay_terminal"]["code"],
            TERMINAL_UPSTREAM_ERROR
        );

        let torn = run_turn(Upstream::TornAfterText, TerminalNaming::Field).await;
        assert_eq!(last(&torn)["relay_terminal"]["code"], TERMINAL_TORN);

        let (torn_early, pre_head_hits) =
            run_turn_with_hits(Upstream::PreResponseHeadFailure, TerminalNaming::Field).await;
        assert_eq!(
            pre_head_hits, 2,
            "pre-response-head send failure gets exactly one safe replay"
        );
        assert_eq!(
            last(&torn_early)["relay_terminal"]["code"],
            TERMINAL_TORN,
            "обрыв до заголовков — тот же обрыв, и он обязан получить то же имя"
        );

        let bad_json = run_turn(Upstream::BadJson, TerminalNaming::Field).await;
        assert_eq!(last(&bad_json)["relay_terminal"]["code"], TERMINAL_TORN);

        let failed = run_turn(Upstream::FailedEvent, TerminalNaming::Field).await;
        assert_eq!(last(&failed)["relay_terminal"]["code"], TERMINAL_TORN);
    }

    /// ⚠ ПИН-РЕГРЕССИЯ, ради которой всё и сделано именно так.
    ///
    /// Живой клиент (llm.py:891) считает ответом всё, у чего `stop_reason != "error"`.
    /// Перенеси мы машинный код В `finish_reason` — английская фраза реле стала бы её
    /// репликой, а оборванный посреди слова ход — «законченным» коротким ответом.
    /// При зарубке `field` обе двери обязаны остаться закрытыми.
    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn a_named_terminal_never_starts_looking_like_an_answer() {
        let _guard = lever_guard();

        let quota = run_turn(
            Upstream::Status(
                429,
                r#"{"error":{"code":"usage_limit_reached"}}"#.to_string(),
            ),
            TerminalNaming::Field,
        )
        .await;
        assert_eq!(last(&quota)["choices"][0]["finish_reason"], "error");

        // И у обрыва ПОСЛЕ уже сказанного текста тоже: иначе клиент выдал бы
        // оборванную фразу как целую — молчаливый потолок ровно там, где его нет.
        let torn = run_turn(Upstream::TornAfterText, TerminalNaming::Field).await;
        assert_eq!(last(&torn)["choices"][0]["finish_reason"], "error");
        let said: String = torn
            .iter()
            .filter_map(|(_, chunk)| chunk["choices"][0]["delta"]["content"].as_str())
            .collect();
        assert!(
            said.contains("ча"),
            "сказанное до обрыва не должно теряться: {said}"
        );
    }

    /// Выключенный рычаг = сегодняшний чанк. Не «примерно», а по составу полей и тексту.
    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn the_lever_off_reproduces_todays_chunk() {
        let _guard = lever_guard();
        let wire = run_turn(
            Upstream::Status(
                429,
                r#"{"error":{"code":"usage_limit_reached","resets_in_seconds":3600}}"#.to_string(),
            ),
            TerminalNaming::Off,
        )
        .await;
        let event = last(&wire);
        let raw = last_raw(&wire);
        // Порядок и состав полей на проводе — те же, что вчера, без единого лишнего.
        assert!(
            raw.starts_with(r#"{"id":"error-"#),
            "порядок полей чанка изменился: {raw}"
        );
        assert!(
            !raw.contains("relay_terminal"),
            "под выключенным рычагом лишнего поля быть не должно: {raw}"
        );
        let mut keys: Vec<&str> = event
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        keys.sort_unstable();
        assert_eq!(keys, vec!["choices", "created", "id", "model", "object"]);
        assert_eq!(event["object"], "chat.completion.chunk");
        assert_eq!(event["model"], "gpt-5.6-sol");
        assert_eq!(event["choices"][0]["index"], 0);
        assert_eq!(event["choices"][0]["finish_reason"], "error");
        assert_eq!(event["choices"][0]["delta"]["role"], "assistant");
        assert_eq!(
            event["choices"][0]["delta"]["content"],
            "Both OpenAI subscriptions are currently unavailable because of usage limits."
        );
        assert!(event["id"].as_str().unwrap().starts_with("error-"));

        // Non-stream HTTP terminals still preserve the legacy lever behavior.
        // Stream truncation is intentionally excluded: accepting it silently was
        // the defect fixed by the focused retry/structured-terminal path above.
    }

    /// Третья зарубка переписывает и `finish_reason` — её включают только после того,
    /// как клиент научился читать класс.
    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn the_third_notch_renames_finish_reason_too() {
        let _guard = lever_guard();
        let wire = run_turn(
            Upstream::Status(401, r#"{"error":{"message":"expired"}}"#.to_string()),
            TerminalNaming::FinishReason,
        )
        .await;
        assert_eq!(
            last(&wire)["choices"][0]["finish_reason"],
            TERMINAL_NEEDS_LOGIN
        );
        assert_eq!(
            last(&wire)["relay_terminal"]["code"],
            TERMINAL_NEEDS_LOGIN,
            "класс обязан ехать и полем: на нём стоит разбор клиента"
        );
        force_terminal_naming(TerminalNaming::Off);
    }

    #[test]
    fn an_unknown_lever_value_means_todays_behaviour() {
        assert_eq!(terminal_naming_from_env(None), TerminalNaming::Off);
        assert_eq!(terminal_naming_from_env(Some("0")), TerminalNaming::Off);
        assert_eq!(terminal_naming_from_env(Some("off")), TerminalNaming::Off);
        assert_eq!(
            terminal_naming_from_env(Some("галактика")),
            TerminalNaming::Off
        );
        assert_eq!(
            terminal_naming_from_env(Some(" Field ")),
            TerminalNaming::Field
        );
        assert_eq!(terminal_naming_from_env(Some("1")), TerminalNaming::Field);
        assert_eq!(
            terminal_naming_from_env(Some("finish_reason")),
            TerminalNaming::FinishReason
        );
    }

    #[test]
    fn the_reset_hour_is_the_vendors_or_nobodys() {
        assert_eq!(
            quota_reset_from_body(r#"{"error":{"resets_in_seconds":900}}"#),
            (None, Some(900))
        );
        assert_eq!(
            quota_reset_from_body(r#"{"detail":{"resets_at":1900000000}}"#),
            (Some(1900000000), None)
        );
        // Ни числа, ни JSON — значит час НЕ НАЗВАН. Придумывать пять минут нельзя:
        // на выдуманном часе она построит план хода.
        assert_eq!(quota_reset_from_body("{}"), (None, None));
        assert_eq!(quota_reset_from_body("not json at all"), (None, None));
        assert_eq!(
            quota_reset_from_body(r#"{"error":{"resets_at":0}}"#),
            (None, None)
        );
    }

    #[test]
    fn only_explicit_subscription_exhaustion_triggers_failover() {
        assert!(subscription_quota_error(
            reqwest::StatusCode::TOO_MANY_REQUESTS,
            r#"{"error":{"code":"usage_limit_reached"}}"#,
        ));
        assert!(!subscription_quota_error(
            reqwest::StatusCode::TOO_MANY_REQUESTS,
            r#"{"error":{"code":"rate_limit_exceeded"}}"#,
        ));
        assert!(!subscription_quota_error(
            reqwest::StatusCode::UNAUTHORIZED,
            r#"{"error":{"code":"usage_limit_reached"}}"#,
        ));
    }

    #[test]
    fn carries_cyrillic_split_across_chunk_boundary() {
        // "привет" — every letter is two bytes; cut the stream inside the "и".
        let text = "привет";
        let bytes = text.as_bytes();
        let cut = 3; // middle of the second character
        let mut carry = Vec::new();
        let mut out = String::new();
        out.push_str(&decode_stream_chunk(&bytes[..cut], &mut carry));
        assert_eq!(
            carry.len(),
            1,
            "half a character must be held back, not emitted"
        );
        out.push_str(&decode_stream_chunk(&bytes[cut..], &mut carry));
        assert_eq!(out, text);
        assert!(carry.is_empty());
        assert!(!out.contains('\u{FFFD}'));
    }

    #[test]
    fn carries_across_byte_at_a_time_delivery() {
        let text = "приятно, что я это ты";
        let mut carry = Vec::new();
        let mut out = String::new();
        for b in text.as_bytes() {
            out.push_str(&decode_stream_chunk(&[*b], &mut carry));
        }
        assert_eq!(out, text);
        assert!(carry.is_empty());
    }

    #[test]
    fn four_byte_sequences_survive_every_split() {
        let text = "🌍🌏"; // 4 bytes each — exercises the longest carry
        let bytes = text.as_bytes();
        for cut in 1..bytes.len() {
            let mut carry = Vec::new();
            let mut out = decode_stream_chunk(&bytes[..cut], &mut carry);
            out.push_str(&decode_stream_chunk(&bytes[cut..], &mut carry));
            assert_eq!(out, text, "split at byte {} lost data", cut);
            assert!(carry.is_empty(), "split at byte {} left a carry", cut);
        }
    }

    #[test]
    fn genuinely_invalid_bytes_are_replaced_not_carried() {
        let mut carry = Vec::new();
        let mut input = b"ok".to_vec();
        input.push(0xFF); // never valid in UTF-8
        input.extend_from_slice("да".as_bytes());
        let out = decode_stream_chunk(&input, &mut carry);
        assert_eq!(out, "ok\u{FFFD}да");
        assert!(carry.is_empty(), "broken bytes must not stall the stream");
    }

    #[test]
    fn ascii_only_stream_is_untouched() {
        let mut carry = Vec::new();
        let out = decode_stream_chunk(b"data: {\"a\":1}\n\n", &mut carry);
        assert_eq!(out, "data: {\"a\":1}\n\n");
        assert!(carry.is_empty());
    }

    #[test]
    fn translates_text_and_image_url_parts_to_responses_content() {
        let request: ChatRequest = serde_json::from_value(json!({
            "model": "gpt-5.5",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "https://example.com/photo.jpg",
                            "detail": "high"
                        }
                    }
                ]
            }]
        }))
        .unwrap();
        request.validate_content().unwrap();

        assert_eq!(
            build_responses_input(&request.messages),
            vec![json!({
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "What is this?"},
                    {
                        "type": "input_image",
                        "image_url": "https://example.com/photo.jpg",
                        "detail": "high"
                    }
                ]
            })]
        );
    }

    #[test]
    fn preserves_legacy_string_null_and_tool_loop_mapping() {
        let request: ChatRequest = serde_json::from_value(json!({
            "model": "gpt-5.5",
            "messages": [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": null,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{\"q\":1}"}
                    }]
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": "done"
                }
            ]
        }))
        .unwrap();
        request.validate_content().unwrap();

        assert_eq!(
            build_responses_input(&request.messages),
            vec![
                json!({"role": "user", "content": "<system>\nrules\n</system>"}),
                json!({"role": "user", "content": "hello"}),
                json!({
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{\"q\":1}"
                }),
                json!({
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "done"
                })
            ]
        );
    }

    #[test]
    fn omits_image_detail_when_client_omits_it() {
        let request: ChatRequest = serde_json::from_value(json!({
            "model": "gpt-5.5",
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "image_url",
                    "image_url": {"url": "https://example.com/photo.jpg"}
                }]
            }]
        }))
        .unwrap();

        let input = build_responses_input(&request.messages);
        assert!(input[0]["content"][0].get("detail").is_none());
    }


    #[test]
    fn responses_tool_strict_default_matches_chat_and_preserves_schema() {
        // Ordinary Chat tools may have optional properties and open objects.
        // Mapping them to Responses must not silently enable strict mode.
        let parameters = json!({
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "options": {
                    "type": "object",
                    "properties": {"detail": {"type": "string"}},
                    "required": []
                }
            },
            "required": []
        });
        for (strict, expected) in [
            (None, false), (Some(Value::Null), false),
            (Some(json!(false)), false), (Some(json!(true)), true),
        ] {
            let mut function = json!({
                "name": "analyze_screenshot",
                "description": "Inspect a screenshot",
                "parameters": parameters.clone()
            });
            if let Some(value) = strict {
                function["strict"] = value;
            }
            let tool: Tool = serde_json::from_value(json!({
                "type": "function", "function": function
            })).unwrap();
            let mapped = map_tools_for_responses(&[tool]);
            assert_eq!(mapped[0]["strict"], json!(expected));
            assert_eq!(mapped[0]["parameters"], parameters);
            assert_eq!(mapped[0]["name"], "analyze_screenshot");
            assert_eq!(mapped[0]["description"], "Inspect a screenshot");
        }
    }

    #[test]
    fn maps_hosted_search_without_unsupported_max_tool_calls() {
        let request: ChatRequest = serde_json::from_value(json!({
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "latest release"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "web_read",
                        "description": "Read a URL",
                        "parameters": {"type": "object"},
                        "strict": false
                    }
                },
                {
                    "type": "web_search",
                    "external_web_access": true,
                    "search_context_size": "medium",
                    "max_uses": 3
                }
            ]
        }))
        .unwrap();
        request.validate_content().unwrap();

        let payload = build_responses_payload(
            &request,
            "instructions".to_string(),
            build_responses_input(&request.messages),
            Some("none"),
            Some("cache-affinity-1"),
            false,
        );
        assert_eq!(payload["model"], "gpt-5.6-sol");
        assert_eq!(payload["reasoning"]["effort"], "none");
        assert_eq!(payload["prompt_cache_key"], "cache-affinity-1");
        assert!(payload.get("max_tool_calls").is_none());
        assert_eq!(payload["tool_choice"], "auto");
        assert_eq!(payload["parallel_tool_calls"], false);
        assert_eq!(payload["tools"][0]["type"], "function");
        assert_eq!(payload["tools"][0]["strict"], false);
        assert_eq!(payload["tools"][1]["type"], "web_search");
        assert_eq!(payload["tools"][1]["external_web_access"], true);
        assert_eq!(payload["tools"][1]["search_context_size"], "medium");
        assert!(payload["tools"][1].get("max_uses").is_none());
    }

    #[test]
    fn reasoning_knob_is_validated_and_optional() {
        let request: ChatRequest = serde_json::from_value(json!({
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "hi"}]
        }))
        .unwrap();

        let omitted = build_responses_payload(
            &request,
            "instructions".to_string(),
            build_responses_input(&request.messages),
            None,
            None,
            true,
        );
        assert!(omitted.get("reasoning").is_none());
        assert!(omitted.get("prompt_cache_key").is_none());

        let junk = build_responses_payload(
            &request,
            "instructions".to_string(),
            build_responses_input(&request.messages),
            Some("galactic"),
            None,
            true,
        );
        assert!(junk.get("reasoning").is_none());

        let low = build_responses_payload(
            &request,
            "instructions".to_string(),
            build_responses_input(&request.messages),
            Some("low"),
            None,
            true,
        );
        assert_eq!(low["reasoning"]["effort"], "low");
    }

    #[test]
    fn conversation_affinity_is_stable_within_a_turn_and_uuid_shaped() {
        let request: ChatRequest = serde_json::from_value(json!({
            "model": "gpt-5.6-sol",
            "messages": [
                {"role": "system", "content": "persona prompt"},
                {"role": "user", "content": "first"}
            ]
        }))
        .unwrap();
        let mut longer: ChatRequest = serde_json::from_value(json!({
            "model": "gpt-5.6-sol",
            "messages": [
                {"role": "system", "content": "persona prompt"},
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "tool step"},
                {"role": "user", "content": "tool result"}
            ]
        }))
        .unwrap();
        // Same first message => same affinity across tool-loop iterations.
        assert_eq!(
            conversation_affinity(&request),
            conversation_affinity(&longer)
        );
        let id = conversation_affinity(&request);
        assert_eq!(id.len(), 36);
        assert_eq!(id.matches('-').count(), 4);
        // Different conversation (different system prompt) => different affinity.
        longer.messages[0].content = Some(MessageContent::Text("another persona".to_string()));
        assert_ne!(
            conversation_affinity(&request),
            conversation_affinity(&longer)
        );
    }


    #[test]
    fn completed_frames_close_choices_with_or_without_usage() {
        for count in [0, 2] {
            for response in [
                json!({"model":"gpt-6.1-sol"}),
                json!({"model":"gpt-6.1-sol","output":[],"usage":{"input_tokens":7,"output_tokens":1,"total_tokens":8}}),
            ] {
                let event = parse_sse_event(&json!({"type":"response.completed","response":response}), count).unwrap();
                assert_eq!(event.choices.len(), 1, "successful Responses completion must finish the Chat choice");
                assert_eq!(event.choices[0].finish_reason.as_deref(), Some(if count == 0 {"stop"} else {"tool_calls"}));
                assert!(event.choices[0].delta.content.is_none());
            }
        }
    }

    #[test]
    fn function_item_is_not_a_finished_response() {
        let event = parse_sse_event(&json!({"type":"response.output_item.done","item":{
            "type":"function_call","name":"echo","arguments":"{}","call_id":"call-a"}}),0).unwrap();
        assert!(event.choices[0].delta.tool_calls.is_some());
        assert_eq!(event.choices[0].finish_reason, None, "a later failure must still reject the turn");
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn repeated_completed_text_keeps_finish_and_usage() {
        let _guard = lever_guard();
        for frames in [
            "data: {\"type\":\"response.output_item.done\",\"item\":{\"type\":\"message\",\"content\":[{\"type\":\"output_text\",\"text\":\"ok\"}]}}\n\ndata: {\"type\":\"response.completed\",\"response\":{\"output\":[{\"type\":\"message\",\"content\":[{\"type\":\"output_text\",\"text\":\"ok\"}]}]}}\n\n",
            "data: {\"type\":\"response.output_item.done\",\"item\":{\"type\":\"message\",\"content\":[{\"type\":\"output_text\",\"text\":\"ok\"}]}}\n\ndata: {\"type\":\"response.completed\",\"response\":{\"output\":[{\"type\":\"message\",\"content\":[{\"type\":\"output_text\",\"text\":\"ok\"}]}],\"usage\":{\"input_tokens\":7,\"output_tokens\":1,\"total_tokens\":8}}}\n\n",
        ] {
            let (wire,hits)=run_turn_with_hits(Upstream::Sse(frames),TerminalNaming::Field).await;
            assert_eq!(hits,1);
            assert!(torn_events(&wire).is_empty());
            let text:Vec<_>=wire.iter().filter_map(|(_,e)| e["choices"][0]["delta"]["content"].as_str()).collect();
            assert_eq!(text,["ok"]);
            assert_eq!(last(&wire)["choices"][0]["finish_reason"],"stop");
            if frames.contains("usage") { assert_eq!(last(&wire)["usage"]["total_tokens"],8); }
        }
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn tool_items_only_finish_after_upstream_completion() {
        let _guard = lever_guard();
        let incomplete="data: {\"type\":\"response.output_item.done\",\"item\":{\"type\":\"function_call\",\"name\":\"echo\",\"arguments\":\"{}\",\"call_id\":\"call-a\"}}\n\n";
        let complete=concat!(
            "data: {\"type\":\"response.output_item.done\",\"item\":{\"type\":\"function_call\",\"name\":\"echo\",\"arguments\":\"{}\",\"call_id\":\"call-a\"}}\n\n",
            "data: {\"type\":\"response.output_item.done\",\"item\":{\"type\":\"function_call\",\"name\":\"echo\",\"arguments\":\"{}\",\"call_id\":\"call-b\"}}\n\n",
            "data: {\"type\":\"response.completed\",\"response\":{\"output\":[]}}\n\n");
        let (wire,hits)=run_turn_with_hits(Upstream::Sse(incomplete),TerminalNaming::Field).await;
        assert_eq!(hits,1,"never replay a surfaced tool call");
        assert_eq!(torn_events(&wire).len(),1);
        assert!(wire.iter().all(|(_,e)| e["choices"][0]["finish_reason"] != "tool_calls"));
        let (wire,hits)=run_turn_with_hits(Upstream::Sse(complete),TerminalNaming::Field).await;
        assert_eq!(hits,1);
        assert!(torn_events(&wire).is_empty());
        assert_eq!(last(&wire)["choices"][0]["finish_reason"],"tool_calls");
        let calls:Vec<_>=wire.iter().filter_map(|(_,e)| e["choices"][0]["delta"]["tool_calls"][0]["index"].as_u64()).collect();
        assert_eq!(calls,[0,1]);
        assert_eq!(wire.iter().filter(|(_,e)|e["choices"][0]["finish_reason"]=="tool_calls").count(),1);
    }

    #[test]
    fn completed_usage_is_forwarded_and_other_events_carry_none() {
        let event = json!({
            "type": "response.completed",
            "response": {
                "model": "gpt-5.6-sol",
                "usage": {"input_tokens": 1200, "output_tokens": 340, "total_tokens": 1540}
            }
        });
        let usage = extract_completed_usage(&event).expect("usage forwarded");
        assert_eq!(usage.prompt_tokens, 1200);
        assert_eq!(usage.completion_tokens, 340);
        assert_eq!(usage.total_tokens, 1540);
        let chunk = parse_sse_event(&event, 0).expect("completed event parsed");
        assert_eq!(chunk.usage.as_ref().map(|u| u.completion_tokens), Some(340));
        assert!(extract_completed_usage(&json!({"type": "response.output_text.delta"})).is_none());
        assert!(extract_completed_usage(
            &json!({"type": "response.completed", "response": {"usage": {}}})
        )
        .is_none());
        // Без детали кэша поле остаётся ОТСУТСТВУЮЩИМ, а не нулевым.
        assert!(usage.prompt_tokens_details.is_none());
    }

    #[test]
    fn cached_prefix_accounting_survives_the_relay() {
        // 02.08.2026: реле схлопывало usage до трёх чисел, и деталь кэша терялась —
        // у Praxis в учёте стояли нули по всем ходам через gpt, что читалось как
        // «кэш не работает», хотя означало «не сообщено».
        let upstream = json!({
            "type": "response.completed",
            "response": {"usage": {
                "input_tokens": 42000, "output_tokens": 300, "total_tokens": 42300,
                "input_tokens_details": {"cached_tokens": 18800}
            }}
        });
        let usage = extract_completed_usage(&upstream).expect("usage forwarded");
        assert_eq!(
            usage
                .prompt_tokens_details
                .as_ref()
                .map(|d| d.cached_tokens),
            Some(18800)
        );
        // Chat-совместимое имя того же поля читается тоже.
        let chat_shaped = json!({
            "type": "response.completed",
            "response": {"usage": {
                "input_tokens": 10, "output_tokens": 1, "total_tokens": 11,
                "prompt_tokens_details": {"cached_tokens": 7}
            }}
        });
        assert_eq!(
            extract_completed_usage(&chat_shaped)
                .and_then(|u| u.prompt_tokens_details)
                .map(|d| d.cached_tokens),
            Some(7)
        );
        // И доезжает до клиента в SSE-чанке, а не только внутри реле.
        let chunk = parse_sse_event(&upstream, 0).expect("completed event parsed");
        assert_eq!(
            chunk
                .usage
                .and_then(|u| u.prompt_tokens_details)
                .map(|d| d.cached_tokens),
            Some(18800)
        );
    }

    #[test]
    fn parallel_function_calls_get_distinct_indexes() {
        let mk = |call: &str| {
            json!({
            "type": "response.output_item.done",
            "item": {"type": "function_call", "name": "t", "arguments": "{}", "call_id": call}
            })
        };
        let first = parse_sse_event(&mk("call-a"), 0).unwrap();
        let second = parse_sse_event(&mk("call-b"), 1).unwrap();
        let idx = |ev: &ResponseEvent| {
            ev.choices[0].delta.tool_calls.as_ref().unwrap()[0]["index"].as_u64()
        };
        assert_eq!(idx(&first), Some(0));
        assert_eq!(idx(&second), Some(1));
        assert_eq!(
            second.choices[0].delta.tool_calls.as_ref().unwrap()[0]["id"],
            "call-b"
        );
    }

    #[test]
    fn payload_carries_parallel_flag_and_minimal_stub_is_small() {
        let request: ChatRequest = serde_json::from_value(json!({
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {
                "name": "t", "description": "", "parameters": {"type": "object"}, "strict": false
            }}]
        }))
        .unwrap();
        let parallel = build_responses_payload(
            &request,
            "i".to_string(),
            build_responses_input(&request.messages),
            None,
            None,
            true,
        );
        assert_eq!(parallel["parallel_tool_calls"], true);
        let serial = build_responses_payload(
            &request,
            "i".to_string(),
            build_responses_input(&request.messages),
            None,
            None,
            false,
        );
        assert_eq!(serial["parallel_tool_calls"], false);
        assert!(MINIMAL_INSTRUCTIONS.len() < 600);
    }

    #[test]
    fn codex_request_has_version_gated_user_agent() {
        // ⚠ Замок обязателен: этот тест ЧИТАЕТ глобальный адрес апстрима, который соседние
        // тесты подменяют на свой поднятый сервер. Без него он зелен только по расписанию —
        // 16.08.2026 достаточно оказалось добавить пять тестов в другом модуле, чтобы он
        // упал с `http://127.0.0.1:40503/responses` вместо адреса апстрима.
        let _guard = lever_guard();

        let request = build_codex_request(
            &Client::new(),
            "test-access-token",
            "test-account",
            "test-session",
            &json!({"model": "gpt-5.6-luna"}),
        )
        .build()
        .unwrap();

        // The default is pinned: it is the version at which the catalog lists
        // gpt-6-astra, and it changes only on purpose. The header is derived from
        // the version, never a second literal.
        assert_eq!(
            codex_user_agent(),
            format!("codex_cli_rs/{}", codex_cli_version()).as_str()
        );
        assert_eq!(request.url().as_str(), CHATGPT_CODEX_RESPONSES_URL);
        assert_eq!(
            request.headers().get(reqwest::header::USER_AGENT).unwrap(),
            codex_user_agent()
        );
        assert_eq!(
            request.headers().get("originator").unwrap(),
            CODEX_ORIGINATOR
        );
    }

    #[test]
    fn hosted_search_events_never_become_client_tool_calls() {
        for event in [
            json!({"type": "response.web_search_call.in_progress"}),
            json!({
                "type": "response.output_item.added",
                "item": {"type": "web_search_call", "id": "ws_1"}
            }),
            json!({
                "type": "response.output_item.done",
                "item": {"type": "web_search_call", "id": "ws_1"}
            }),
        ] {
            assert!(parse_sse_event(&event, 0).is_none());
        }
    }

    #[test]
    fn completed_message_uses_actual_model_and_clean_text() {
        let event = json!({
            "type": "response.completed",
            "response": {
                "model": "gpt-5.6-terra",
                "output": [
                    {"type": "web_search_call", "id": "ws_1", "status": "completed"},
                    {
                        "type": "message",
                        "content": [{
                            "type": "output_text",
                            "text": "Current answer",
                            // Аннотация без индексов: вшивать некуда, текст не трогаем.
                            "annotations": [{
                                "type": "url_citation",
                                "url": "https://example.com/source",
                                "title": "Primary source"
                            }]
                        }]
                    }
                ]
            }
        });

        let translated = parse_sse_event(&event, 0).unwrap();
        assert_eq!(translated.model, "gpt-5.6-terra");
        let delta = &translated.choices[0].delta;
        assert_eq!(delta.tool_calls, None);
        assert_eq!(delta.content.as_deref(), Some("Current answer"));
    }

    #[test]
    fn message_item_done_carries_clean_text_and_links() {
        // Форма бэкенда 09.09: ссылка уже в тексте с utm-хвостом, аннотация указывает на
        // её диапазон (в кодовых точках); маркер ссылки на прошлый запрос — сырой.
        let head = "Титаны вышли 31 декабря. ";
        let link = "([arxiv.org](https://arxiv.org/abs/2501.00663?utm_source=openai))";
        let tail = "\n\nМаркер \u{E200}cite\u{E202}turn1search3\u{E201} снят.";
        let text = format!("{head}{link}{tail}");
        let start = head.chars().count();
        let end = start + link.chars().count();
        let event = json!({
            "type": "response.output_item.done",
            "response": {"model": "gpt-5.6-sol"},
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{
                    "type": "output_text",
                    "text": text,
                    "annotations": [{
                        "type": "url_citation",
                        "start_index": start,
                        "end_index": end,
                        "title": "Titans: Learning to Memorize at Test Time",
                        "url": "https://arxiv.org/abs/2501.00663?utm_source=openai"
                    }]
                }]
            }
        });

        let translated = parse_sse_event(&event, 0).unwrap();
        assert_eq!(translated.model, "gpt-5.6-sol");
        assert_eq!(
            translated.choices[0].delta.content.as_deref(),
            Some("Титаны вышли 31 декабря. ([arxiv.org](https://arxiv.org/abs/2501.00663))\n\nМаркер снят.")
        );
        assert_eq!(translated.choices[0].finish_reason, None);
    }

    #[test]
    fn draft_text_events_stay_silent() {
        for event in [
            json!({"type": "response.output_text.delta", "delta": "Тит"}),
            json!({"type": "response.output_text.done", "text": "Титаны вышли."}),
            json!({"type": "response.content_part.done", "part": {"type": "output_text", "text": "Титаны вышли."}}),
            json!({"type": "response.output_text.annotation.added", "annotation": {"type": "url_citation", "url": "https://a.b/"}}),
            json!({"type": "response.output_item.done", "item": {"type": "reasoning", "summary": []}}),
        ] {
            assert!(parse_sse_event(&event, 0).is_none(), "{event}");
        }
    }

    #[test]
    fn annotation_span_without_link_gets_wrapped() {
        let text = "По данным ABS население 452 670.";
        let start = "По данным ".chars().count();
        let end = start + "ABS".chars().count();
        let annotations = vec![json!({
            "type": "url_citation",
            "start_index": start,
            "end_index": end,
            "url": "https://www.abs.gov.au/census/2021"
        })];
        assert_eq!(
            clean_model_text(text, &annotations),
            "По данным [ABS](https://www.abs.gov.au/census/2021) население 452 670."
        );
    }

    #[test]
    fn link_parens_and_utm_are_normalised() {
        let text = "см. [Ключ](https://ru.wikipedia.org/wiki/Ключ_(значения)?utm_source=openai&x=1) и https://a.b/c?utm_source=chatgpt.com — всё.";
        assert_eq!(
            clean_model_text(text, &[]),
            "см. [Ключ](https://ru.wikipedia.org/wiki/Ключ_%28значения%29?x=1) и https://a.b/c — всё."
        );
        // Слова про utm в обычном тексте — не адрес, не трогаем.
        assert_eq!(strip_utm("метка utm_source=openai — это хвост"), "метка utm_source=openai — это хвост");
    }

    #[test]
    fn unsafe_or_missing_urls_leave_text_alone() {
        let text = "Источник: ftp-архив.";
        let annotations = vec![
            json!({"type": "url_citation", "start_index": 10, "end_index": 20, "url": "ftp://unsafe.example/x"}),
            json!({"type": "url_citation", "start_index": 10, "end_index": 999, "url": "https://ok.example/x"}),
            json!({"type": "url_citation", "url": "https://ok.example/y"}),
        ];
        assert_eq!(clean_model_text(text, &annotations), text);
    }

    #[test]
    fn unclosed_marker_is_dropped_without_eating_the_answer() {
        let (text, had) = strip_cite_markers("Ответ \u{E200}cite\u{E202}turn0search1 остался целым.");
        assert!(had);
        assert_eq!(text, "Ответ citeturn0search1 остался целым.");
        let (clean, had) = strip_cite_markers("Без маркеров.");
        assert!(!had);
        assert_eq!(clean, "Без маркеров.");
    }

    #[test]
    fn logs_only_bounded_event_labels_and_client_safe_upstream_errors() {
        assert_eq!(
            safe_event_type(&json!({"type": "response.output_text.delta"})),
            "response.output_text.delta"
        );
        assert_eq!(safe_event_type(&json!({"type": "bad\nprivate"})), "unknown");

        let long_message = format!("invalid\nrequest {}", "x".repeat(700));
        let body = json!({
            "error": {
                "code": "invalid_request_error",
                "message": long_message,
                "private_request_echo": "must-not-leak"
            },
            "private": "must-not-leak-either"
        })
        .to_string();
        let message = upstream_error_message(reqwest::StatusCode::BAD_REQUEST, &body);
        assert!(message.contains("invalid_request_error"));
        assert!(!message.contains('\n'));
        assert!(!message.contains("must-not-leak"));
        assert!(message.chars().count() <= MAX_UPSTREAM_ERROR_CHARS + 80);

        assert_eq!(
            upstream_error_message(reqwest::StatusCode::BAD_GATEWAY, "raw private response"),
            "Upstream error 502 Bad Gateway"
        );
    }

    /// 10.08.2026: протухший токен слота дал 401 на каждый вызов, реле продолжало
    /// предъявлять его, живой слот стоял рядом нетронутым, и компаньон замолчал на часы.
    /// Отказ в САМИХ УЧЁТНЫХ ДАННЫХ — такой же повод уйти на запасную подписку, как и
    /// кончившаяся квота.
    #[test]
    fn credentials_refused_is_a_reason_to_switch_subscriptions() {
        assert!(subscription_auth_error(reqwest::StatusCode::UNAUTHORIZED));

        // И ровно это — НЕ повод: свои пути уже есть, чужие трогать нельзя.
        for status in [
            reqwest::StatusCode::TOO_MANY_REQUESTS,
            reqwest::StatusCode::BAD_REQUEST,
            reqwest::StatusCode::FORBIDDEN,
            reqwest::StatusCode::INTERNAL_SERVER_ERROR,
            reqwest::StatusCode::BAD_GATEWAY,
            reqwest::StatusCode::OK,
        ] {
            assert!(
                !subscription_auth_error(status),
                "{status} не должен двигать активный слот"
            );
        }
    }

    #[test]
    fn both_slots_refusing_credentials_asks_for_a_login_in_plain_words() {
        let status = reqwest::StatusCode::UNAUTHORIZED;
        assert!(subscription_auth_error(status));
        assert!(!subscription_quota_error(status, "{}"));
    }
}

#[cfg(test)]
mod codex_version_tests {
    use super::{parse_version, version_is_newer};

    #[test]
    fn only_plain_triples_are_versions() {
        assert_eq!(parse_version("0.157.1"), Some((0, 157, 1)));
        assert_eq!(parse_version("0.157"), None);
        assert_eq!(parse_version("0.157.1-alpha"), None);
        assert_eq!(parse_version("1.2.3.4"), None);
    }

    #[test]
    fn newer_means_newer_by_numbers_not_by_text() {
        assert!(version_is_newer("0.157.1", "0.153.3"));
        assert!(version_is_newer("0.153.10", "0.153.3"));
        assert!(!version_is_newer("0.153.3", "0.153.3"));
        assert!(!version_is_newer("0.99.0", "0.153.3"));
        assert!(!version_is_newer("garbage", "0.153.3"));
    }
}
