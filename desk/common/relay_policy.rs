//! Shared relay demand and loop-key policy for text and auxiliary image use.
pub fn needed(cfg: &serde_json::Value) -> bool {
    cfg.get("relay").and_then(|v| v.get("enabled")).and_then(|v| v.as_bool()).unwrap_or(false)
        || cfg.get("images").and_then(|v| v.get("enabled")).and_then(|v| v.as_bool()).unwrap_or(false)
}

pub fn key(cfg: &serde_json::Value) -> &str {
    cfg.get("relay").and_then(|v| v.get("key")).and_then(|v| v.as_str()).filter(|v| !v.trim().is_empty())
        .or_else(|| {
            if cfg.get("relay").and_then(|v| v.get("enabled")).and_then(|v| v.as_bool()).unwrap_or(false) {
                cfg.get("model").and_then(|v| v.get("key")).and_then(|v| v.as_str())
            } else { None }
        }).unwrap_or("")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn auxiliary_images_start_relay_and_keep_provider_credentials_separate() {
        let cfg=json!({"model":{"framework":"anthropic","key":"provider-secret"},"relay":{"enabled":false,"key":"loop-key"},"images":{"enabled":true}});
        assert!(needed(&cfg));assert_eq!(key(&cfg),"loop-key");
        let cfg=json!({"model":{"key":"provider-secret"},"images":{"enabled":true}});
        assert!(needed(&cfg));assert_eq!(key(&cfg),"");
    }
    #[test]
    fn text_relay_legacy_key_and_disabled_state_survive() {
        assert_eq!(key(&json!({"relay":{"enabled":true},"model":{"key":"legacy-loop"}})),"legacy-loop");
        assert!(needed(&json!({"relay":{"enabled":true}})));
        assert!(!needed(&json!({"relay":{"enabled":false},"images":{"enabled":false}})));
    }
}
