//! Standalone Codex Images API. One request means one upstream attempt: an
//! interrupted generation may already be paid for, so it must never be replayed.
use axum::http::{HeaderMap, StatusCode};
use base64::Engine;
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::time::Duration;

use super::account_router::{AccountRouter, DownstreamCancellation};
use super::chat_completions::{codex_user_agent, CODEX_ORIGINATOR};

pub const MAX_IMAGE_REQUEST_BYTES: usize = 144 * 1024 * 1024;
const MAX_IMAGE_RESPONSE_BYTES: usize = 64 * 1024 * 1024;
const MAX_IMAGE_BYTES: usize = 32 * 1024 * 1024;
pub const DEFAULT_IMAGE_MODEL: &str = "gpt-image-2";

#[derive(Clone, Deserialize, Serialize)]
#[serde(untagged, deny_unknown_fields)]
pub enum ImageReference {
    Inline { image_url: String },
    File { file_id: String },
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ImageRequest {
    pub model: String,
    pub prompt: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub n: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quality: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub size: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub background: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub images: Option<Vec<ImageReference>>,
}

impl ImageRequest {
    pub fn validate(&self, edit: bool) -> Result<(), &'static str> {
        if self.model.trim().is_empty() || self.model.len() > 200 {
            return Err("model must be a nonempty image-model identifier");
        }
        if self.prompt.trim().is_empty() || self.prompt.chars().count() > 32000 {
            return Err("prompt must contain 1-32000 characters");
        }
        if self.n == Some(0) {
            return Err("n must be positive");
        }
        if self
            .quality
            .as_deref()
            .is_some_and(|v| !["auto", "low", "medium", "high"].contains(&v))
        {
            return Err("quality must be auto, low, medium or high");
        }
        if self
            .background
            .as_deref()
            .is_some_and(|v| !["auto", "opaque", "transparent"].contains(&v))
        {
            return Err("background must be auto, opaque or transparent");
        }
        if self.size.as_deref().is_some_and(|v| {
            v.len() > 32
                || !(v == "auto"
                    || v.split_once('x').is_some_and(|(w, h)| {
                        w.parse::<u32>().is_ok_and(|n| n > 0)
                            && h.parse::<u32>().is_ok_and(|n| n > 0)
                    }))
        }) {
            return Err("size must be auto or WIDTHxHEIGHT");
        }
        if edit {
            let images = self.images.as_ref().ok_or("edits require images")?;
            if images.is_empty() || images.len() > 5 {
                return Err("edits require 1-5 images");
            }
            for image in images {
                match image {
                    ImageReference::Inline { image_url } => {
                        super::models::validate_image_url(image_url)
                            .map_err(|_| "invalid edit image_url")?;
                    }
                    ImageReference::File { file_id }
                        if file_id.is_empty() || file_id.len() > 200 =>
                    {
                        return Err("invalid edit file_id");
                    }
                    ImageReference::File { .. } => {}
                }
            }
        } else if self.images.is_some() {
            return Err("reference images belong in /images/edits");
        }
        Ok(())
    }
}

pub struct ImageReply {
    pub status: StatusCode,
    pub body: Value,
    pub headers: HeaderMap,
}

pub fn error(status: StatusCode, code: &str, message: &str) -> ImageReply {
    ImageReply {
        status,
        body: json!({"error":{"type":"image_generation_error","code":code,"message":message}}),
        headers: HeaderMap::new(),
    }
}

struct CancelOnDrop(DownstreamCancellation);
impl Drop for CancelOnDrop {
    fn drop(&mut self) {
        self.0.cancel();
    }
}

pub async fn forward(
    router: &AccountRouter,
    client: &reqwest::Client,
    request: &ImageRequest,
    base_url: &str,
    edit: bool,
    turn_id: &str,
) -> ImageReply {
    let cancellation = DownstreamCancellation::default();
    let _guard = CancelOnDrop(cancellation.clone());
    let account = match router.lease_any_for_downstream(&cancellation).await {
        Ok(account) => account,
        Err(_) => {
            return error(
                StatusCode::SERVICE_UNAVAILABLE,
                "subscription_needs_login",
                "No authenticated subscription is available",
            )
        }
    };
    let operation = if edit { "edits" } else { "generations" };
    let url = format!("{}/images/{operation}", base_url.trim_end_matches('/'));
    let response = match client
        .post(url)
        .bearer_auth(&account.access_token)
        .header("chatgpt-account-id", &account.account_id)
        .header("originator", CODEX_ORIGINATOR)
        .header("user-agent", codex_user_agent())
        .header("x-codex-image-turn-id", turn_id)
        .timeout(Duration::from_secs(240))
        .json(request)
        .send()
        .await
    {
        Ok(response) => response,
        Err(_) => {
            return error(
                StatusCode::BAD_GATEWAY,
                "image_outcome_unknown",
                "Image request interrupted; outcome unknown. No automatic retry was made",
            )
        }
    };
    let status =
        StatusCode::from_u16(response.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
    let mut headers = HeaderMap::new();
    for name in ["x-codex-imagegen-request-id", "x-request-id", "retry-after"] {
        if let Some(value) = response.headers().get(name) {
            if let Ok(value) = axum::http::HeaderValue::from_bytes(value.as_bytes()) {
                headers.insert(name, value);
            }
        }
    }
    let mut raw = Vec::new();
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = match chunk {
            Ok(chunk) => chunk,
            Err(_) => {
                return error(
                    StatusCode::BAD_GATEWAY,
                    "image_outcome_unknown",
                    "Image response interrupted; no automatic retry was made",
                )
            }
        };
        if raw.len().saturating_add(chunk.len()) > MAX_IMAGE_RESPONSE_BYTES {
            return error(
                StatusCode::BAD_GATEWAY,
                "image_response_too_large",
                "Image response exceeds the relay transport limit",
            );
        }
        raw.extend_from_slice(&chunk);
    }
    let body: Value = match serde_json::from_slice(&raw) {
        Ok(body) => body,
        Err(_) => {
            return error(
                StatusCode::BAD_GATEWAY,
                "invalid_image_response",
                "Upstream returned an invalid JSON image response",
            )
        }
    };
    if !status.is_success() {
        // In particular, an image-specific 429 is NOT proof that text allowance
        // is exhausted. Preserve its code and leave the account router unchanged.
        let body = if body.get("error").is_some() {
            body
        } else {
            json!({"error":{"code":"image_upstream_error","message":"Image provider rejected the request"}})
        };
        return ImageReply {
            status,
            body,
            headers,
        };
    }
    if !valid_image_response(&body) {
        return error(
            StatusCode::BAD_GATEWAY,
            "invalid_image_response",
            "Upstream did not return a valid encoded image",
        );
    }
    ImageReply {
        status,
        body,
        headers,
    }
}

fn valid_image_response(body: &Value) -> bool {
    let Some(images) = body.get("data").and_then(Value::as_array) else {
        return false;
    };
    !images.is_empty()
        && images.iter().all(|image| {
            let Some(encoded) = image.get("b64_json").and_then(Value::as_str) else {
                return false;
            };
            if encoded.len() > MAX_IMAGE_BYTES.div_ceil(3) * 4 {
                return false;
            }
            let Ok(raw) = base64::engine::general_purpose::STANDARD.decode(encoded) else {
                return false;
            };
            !raw.is_empty()
                && raw.len() <= MAX_IMAGE_BYTES
                && (raw.starts_with(b"\x89PNG\r\n\x1a\n")
                    || raw.starts_with(b"\xff\xd8\xff")
                    || (raw.starts_with(b"RIFF") && raw.get(8..12) == Some(b"WEBP")))
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn request_and_artifact_validation() {
        let request: ImageRequest = serde_json::from_value(json!({"model":"gpt-image-2","prompt":"draw a square","quality":"low","size":"1024x1024"})).unwrap();
        assert!(request.validate(false).is_ok());
        assert!(request.validate(true).is_err());
        assert!(serde_json::from_value::<ImageRequest>(
            json!({"model":"gpt-image-2","prompt":"x","stream":true})
        )
        .is_err());
        assert!(!valid_image_response(&json!({"data":[]})));
        assert!(!valid_image_response(
            &json!({"data":[{"b64_json":"not base64"}]})
        ));
        assert!(!valid_image_response(
            &json!({"data":[{"b64_json":"aGVsbG8="}]})
        ));
        let png =
            base64::engine::general_purpose::STANDARD.encode(b"\x89PNG\r\n\x1a\nsynthetic fixture");
        assert!(valid_image_response(&json!({"data":[{"b64_json":png}]})));
    }
}
