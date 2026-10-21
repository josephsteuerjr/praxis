//! Downstream wire choice over one upstream producer. The lazy JSON body owns
//! the receiver just like SSE does: a disconnected client cancels upstream.
use crate::core::models::ResponseEvent;
use axum::{
    body::Body,
    http::header,
    response::{IntoResponse, Response},
};
use futures::{Stream, StreamExt};
use serde_json::{json, Value};
use std::collections::BTreeMap;

pub fn json_response<S>(events: S, model: String) -> Response
where
    S: Stream<Item = anyhow::Result<ResponseEvent>> + Unpin + Send + 'static,
{
    let body = async_stream::stream! {
        let document = collect(events, &model).await;
        yield Ok::<_, std::convert::Infallible>(serde_json::to_vec(&document)
            .expect("a JSON Value always serializes"));
    };
    (
        [(header::CONTENT_TYPE, "application/json")],
        Body::from_stream(body),
    )
        .into_response()
}

async fn collect<S>(mut events: S, model: &str) -> Value
where
    S: Stream<Item = anyhow::Result<ResponseEvent>> + Unpin,
{
    let mut content = String::new();
    let mut tools: BTreeMap<u64, Value> = BTreeMap::new();
    let mut finish = None;
    let mut usage = None;
    let mut observation = None;
    let mut terminal = None;
    let mut failure = None;
    let mut id = None;
    let mut created = None;
    while let Some(result) = events.next().await {
        let event = match result {
            Ok(event) => event,
            Err(error) => {
                failure = Some(
                    json!({"code":"stream_error", "message":error.to_string(), "type":"stream_error"}),
                );
                break;
            }
        };
        id = Some(event.id.clone());
        created = Some(event.created);
        if event.usage.is_some() {
            usage = event.usage.clone();
        }
        // Used-source editions may carry provider observations. Preserve the
        // receipt without interpreting it as assistant text or billing proof.
        let serialized = serde_json::to_value(&event).expect("event serializes");
        if let Some(value) = serialized.get("relay_provider_observation") {
            observation = Some(value.clone());
        }
        if let Some(value) = event.relay_terminal {
            failure = Some(json!({"code":value.code, "message":value.message,
                                  "type":"relay_error"}));
            terminal = Some(value);
            break;
        }
        for choice in event.choices {
            if let Some(reason) = choice.finish_reason.as_deref() {
                if !matches!(reason, "stop" | "tool_calls" | "length" | "content_filter") {
                    failure = Some(json!({"code":reason,
                            "message":choice.delta.content.as_deref().unwrap_or("Upstream completion failed"),
                            "type":"relay_error"}));
                    break;
                }
                finish = Some(reason.to_string());
            }
            if let Some(text) = choice.delta.content {
                content.push_str(&text);
            }
            if let Some(Value::Array(calls)) = choice.delta.tool_calls {
                for call in calls {
                    let index = call["index"].as_u64().unwrap_or(0);
                    let target = tools.entry(index).or_insert_with(|| {
                        json!({
                            "type":"function", "function":{"name":"", "arguments":""}
                        })
                    });
                    for key in ["id", "type"] {
                        if let Some(value) = call.get(key) {
                            target[key] = value.clone();
                        }
                    }
                    for key in ["name", "arguments"] {
                        if let Some(fragment) = call["function"][key].as_str() {
                            let mut text =
                                target["function"][key].as_str().unwrap_or("").to_string();
                            text.push_str(fragment);
                            target["function"][key] = Value::String(text);
                        }
                    }
                }
            }
        }
        if failure.is_some() {
            break;
        }
    }
    if finish.is_none() && failure.is_none() {
        failure = Some(
            json!({"code":"upstream_torn", "message":"Upstream ended without a completion terminal", "type":"relay_error"}),
        );
    }
    let mut message = json!({"role":"assistant", "content":if content.is_empty() { Value::Null } else { Value::String(content) }});
    if !tools.is_empty() {
        message["tool_calls"] = Value::Array(tools.into_values().collect());
    }
    let mut document = json!({
        "id":id.unwrap_or_else(|| format!("chatcmpl-{}", uuid::Uuid::new_v4())),
        "object":"chat.completion", "created":created.unwrap_or_else(|| chrono::Utc::now().timestamp()),
        "model":model, "choices":[{"index":0,"message":message,"finish_reason":finish}]
    });
    if let Some(value) = usage {
        document["usage"] = serde_json::to_value(value).expect("usage serializes");
    }
    if let Some(value) = observation {
        document["relay_provider_observation"] = value;
    }
    if let Some(value) = terminal {
        document["relay_terminal"] = serde_json::to_value(value).expect("terminal serializes");
    }
    if let Some(error) = failure {
        // Partial output and transport prose must not become an executable model
        // answer. Keep the failure (and measured usage) in the same response.
        document["error"] = error;
        document["choices"][0]["message"] = json!({"role":"assistant", "content":null});
        document["choices"][0]["finish_reason"] = json!("error");
    }
    document
}
