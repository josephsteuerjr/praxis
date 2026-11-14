//! Private-safe fingerprints of the final wire shape, immediately before dispatch.
//! No prompts, tokens, account ids or proxy credentials enter this record.
use serde_json::{json, Value};
use ring::digest::{Context, SHA256};
fn hash(bytes: &[u8]) -> String {
    ring::digest::digest(&SHA256, bytes).as_ref().iter().map(|b| format!("{b:02x}")).collect()
}
fn field(value: &Value) -> Value {
    let bytes = serde_json::to_vec(value).unwrap_or_default();
    json!({"bytes":bytes.len(),"sha256":hash(&bytes)})
}
pub fn record(payload: &Value, account: &str, conservative: bool) -> Value {
    let mut prefixes = Vec::new();
    let mut digest = Context::new(&SHA256);
    let mut bytes = 0usize;
    if let Some(items) = payload.get("input").and_then(Value::as_array) {
        for item in items {
            let encoded = serde_json::to_vec(item).unwrap_or_default();
            digest.update(&(encoded.len() as u64).to_be_bytes());
            digest.update(&encoded);
            bytes += encoded.len();
            let sha = digest.clone().finish().as_ref().iter().map(|b| format!("{b:02x}")).collect::<String>();
            prefixes.push(json!({"bytes":bytes,"sha256":sha}));
        }
    }
    json!({"kind":"relay_wire_prefix", "version":1,
        "account_sha256":hash(account.as_bytes()),"conservative_retry":conservative,
        "model":payload.get("model"),"reasoning":payload.get("reasoning"),
        "cache_key":field(&payload["prompt_cache_key"]),
        "instructions":field(&payload["instructions"]),"tools":field(&payload["tools"]),
        "input_prefixes":prefixes})
}
#[cfg(test)] mod tests {
    use super::*;
    #[test] fn observes_exact_prefix_without_text() {
        let a=json!({"input":[{"content":"private-a"},{"content":"private-b"}],"instructions":"secret","tools":[]});
        let mut b=a.clone(); b["input"][1]["content"]=json!("private-c");
        let x=record(&a,"account",false);let y=record(&b,"account",false);
        assert_eq!(x["input_prefixes"][0],y["input_prefixes"][0]);
        assert_ne!(x["input_prefixes"][1],y["input_prefixes"][1]);
        assert!(!x.to_string().contains("private"));assert!(!x.to_string().contains("secret"));
    }
}

// Passive result receipts. Only counts and hashes are retained.
pub fn record_dispatch(payload: &Value, account: &str, session_id: &str, conservative: bool) -> Value {
    let mut observation = record(payload, account, conservative);
    observation["version"] = json!(2);
    observation["payload"] = field(payload);
    observation["session_id"] = field(&json!(session_id));
    observation
}

fn numeric_fields(source: &Value, names: &[&str]) -> Value {
    let mut selected = serde_json::Map::new();
    for name in names {
        if let Some(number) = source.get(*name).and_then(Value::as_u64) {
            selected.insert((*name).to_string(), json!(number));
        }
    }
    Value::Object(selected)
}

pub fn record_completed(event: &Value, dispatch: &Value, request_id: Option<&str>) -> Option<Value> {
    if event.get("type").and_then(Value::as_str) != Some("response.completed") {
        return None;
    }
    let response = event.get("response")?.as_object()?;
    let mut receipt = json!({
        "kind": "relay_cache_result", "version": 1,
        "dispatch": field(dispatch),
    });
    for name in ["id", "model", "service_tier"] {
        if let Some(value) = response.get(name) {
            receipt[name] = field(value);
        }
    }
    if let Some(request_id) = request_id {
        receipt["upstream_request_id"] = field(&json!(request_id));
    }
    if let Some(usage) = response.get("usage").filter(|v| v.is_object()) {
        let mut selected = numeric_fields(usage, &["input_tokens", "output_tokens", "total_tokens"]);
        for name in ["input_tokens_details", "prompt_tokens_details"] {
            if let Some(details) = usage.get(name).filter(|v| v.is_object()) {
                let mut counts = numeric_fields(details, &["cached_tokens", "cache_write_tokens"]);
                if let Some(writes) = details.get("cache_write_tokens_details").filter(|v| v.is_object()) {
                    counts["cache_write_tokens_details"] = numeric_fields(writes, &["ttl_30m_tokens", "ttl_12h_tokens"]);
                }
                selected[name] = counts;
            }
        }
        receipt["usage"] = selected;
    }
    if let Some(diagnostics) = response.get("prompt_cache_diagnostics").filter(|v| v.is_object()) {
        let mut selected = numeric_fields(diagnostics, &["comparison_reusable_tokens", "cache_missed_tokens"]);
        // Hash categorical metadata too: an unexpected backend string must not
        // introduce private text into this log.
        for name in ["type", "reason"] {
            if let Some(value) = diagnostics.get(name) {
                selected[name] = field(value);
            }
        }
        receipt["prompt_cache_diagnostics"] = selected;
    }
    Some(receipt)
}

#[cfg(test)]
mod receipt_tests {
    use super::*;

    #[test]
    fn completed_only_not_usage_shaped_partial() {
        let partial = json!({"type":"response.in_progress", "response":{"usage":{"input_tokens":123}}});
        assert!(record_completed(&partial, &json!({}), None).is_none());
        assert!(record_completed(&json!({"type":"response.completed"}), &json!({}), None).is_none());
        assert!(record_completed(&json!({"type":"response.completed","response":{}}), &json!({}), None).is_some());
    }

    #[test]
    fn distinguishes_missing_counters_from_reported_zero() {
        let absent = record_completed(&json!({"type":"response.completed","response":{"usage":{"input_tokens":5}}}), &json!({}), None).unwrap();
        assert!(absent["usage"].get("input_tokens_details").is_none());
        let zero = record_completed(&json!({"type":"response.completed","response":{"usage":{"input_tokens":5,"input_tokens_details":{"cached_tokens":0,"cache_write_tokens":0}}}}), &json!({}), None).unwrap();
        assert_eq!(zero["usage"]["input_tokens_details"]["cached_tokens"], 0);
        assert_eq!(zero["usage"]["input_tokens_details"]["cache_write_tokens"], 0);
        let no_usage = record_completed(&json!({"type":"response.completed","response":{}}), &json!({}), None).unwrap();
        assert!(no_usage.get("usage").is_none());
    }

    #[test]
    fn preserves_provider_write_and_lifetime_counts() {
        let event = json!({"type":"response.completed","response":{"usage":{"input_tokens":10000,"input_tokens_details":{"cached_tokens":2000,"cache_write_tokens":6000,"cache_write_tokens_details":{"ttl_30m_tokens":4000,"ttl_12h_tokens":2000}}},"prompt_cache_diagnostics":{"type":"cache_miss","reason":"tools_changed","comparison_reusable_tokens":8000,"cache_missed_tokens":6000}}});
        let result=record_completed(&event,&json!({}),None).unwrap();
        assert_eq!(result["usage"]["input_tokens_details"]["cache_write_tokens"],6000);
        assert_eq!(result["usage"]["input_tokens_details"]["cache_write_tokens_details"]["ttl_12h_tokens"],2000);
        assert_eq!(result["prompt_cache_diagnostics"]["cache_missed_tokens"],6000);
        assert_eq!(result["prompt_cache_diagnostics"]["reason"],field(&json!("tools_changed")));
    }

    #[test]
    fn cannot_log_private_response_text_or_opaque_ids() {
        let event=json!({"type":"response.completed","response":{"id":"secret-response","model":"secret-model","service_tier":"secret-tier","output":[{"text":"secret-output"}],"usage":{"input_tokens":10,"secret-usage-key":"secret-usage","input_tokens_details":{"cached_tokens":"secret-count","cache_write_tokens":-2,"cache_write_tokens_details":{"ttl_12h_tokens":1.5,"other":"secret-write"}}},"prompt_cache_diagnostics":{"type":"secret-type","reason":"secret-reason","secret-key":"secret-value"}}});
        let receipt=record_completed(&event,&json!({}),Some("secret-request")).unwrap();
        assert!(!receipt.to_string().contains("secret"));
        assert!(receipt["usage"]["input_tokens_details"].get("cached_tokens").is_none());
        assert!(receipt["usage"]["input_tokens_details"].get("cache_write_tokens").is_none());
    }

    #[test]
    fn dispatch_covers_hidden_option_and_session_changes() {
        let mut payload=json!({"model":"gpt-6.1-sol","input":[{"role":"user","content":"private-prompt"}]});
        let first=record_dispatch(&payload,"private-account","private-session",false);
        payload["parallel_tool_calls"]=json!(false);
        let changed=record_dispatch(&payload,"private-account","private-session",false);
        assert_eq!(first["input_prefixes"],changed["input_prefixes"]);
        assert_ne!(first["payload"],changed["payload"]);
        assert_ne!(first["session_id"],record_dispatch(&payload,"private-account","other-session",false)["session_id"]);
        assert!(!first.to_string().contains("private"));
    }

    #[test]
    fn result_links_to_actual_retry_account_and_shape() {
        let event=json!({"type":"response.completed","response":{"usage":{"input_tokens":10}}});
        let first=record_dispatch(&json!({"prompt_cache_key":"a"}),"account-a","session",false);
        let retry=record_dispatch(&json!({}),"account-b","session",true);
        let a=record_completed(&event,&first,None).unwrap();
        let b=record_completed(&event,&retry,None).unwrap();
        assert_eq!(a["dispatch"],field(&first));
        assert_eq!(b["dispatch"],field(&retry));
        assert_ne!(a["dispatch"],b["dispatch"]);
    }
}
