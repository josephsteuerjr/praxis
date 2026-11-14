//! Content-free argument-stream evidence. All optional ceilings default to zero (off).
use serde_json::{json, Value};
use std::collections::HashMap;
use std::time::{Duration, Instant};

#[derive(Default, Debug, Clone, Copy)]
pub struct Limits {
    pub argument_bytes: u64,
    pub whitespace_run: u64,
    pub attempt_ms: u64,
}

impl Limits {
    pub fn from_env() -> Self {
        fn number(key: &str) -> u64 {
            std::env::var(key).ok().and_then(|s| s.parse().ok()).unwrap_or(0)
        }
        Self {
            argument_bytes: number("RELAY_ARGUMENT_BYTES_LIMIT"),
            whitespace_run: number("RELAY_ARGUMENT_WHITESPACE_RUN_LIMIT"),
            attempt_ms: number("RELAY_MODEL_ATTEMPT_MS_LIMIT"),
        }
    }
}

#[derive(Default)]
struct Call {
    bytes: u64,
    in_string: bool,
    escaped: bool,
    run: u64,
}

pub struct Attempt {
    pub limits: Limits,
    pub started: Instant,
    id: String,
    calls: HashMap<String, Call>,
    deltas: u64,
    bytes: u64,
    chars: u64,
    whitespace: u64,
    longest: u64,
    max_call_bytes: u64,
    first_delta: Option<Instant>,
    last_delta: Option<Instant>,
    last_progress: Option<Instant>,
    usage: Option<Value>,
    outcome: &'static str,
}

impl Attempt {
    pub fn new(limits: Limits) -> Self {
        Self { limits, started: Instant::now(), id: uuid::Uuid::new_v4().simple().to_string(),
            calls: HashMap::new(), deltas: 0, bytes: 0, chars: 0, whitespace: 0,
            longest: 0, max_call_bytes: 0, first_delta: None, last_delta: None,
            last_progress: None, usage: None, outcome: "streaming" }
    }

    pub fn observe(&mut self, event: &Value) {
        match event["type"].as_str() {
            Some("response.function_call_arguments.delta") => {
                if let Some(delta) = event["delta"].as_str() {
                    let now = Instant::now();
                    self.first_delta.get_or_insert(now);self.last_delta = Some(now);
                    self.deltas += 1;self.bytes += delta.len() as u64;
                    let key = event["item_id"].as_str().map(str::to_owned)
                        .unwrap_or_else(|| event["output_index"].to_string());
                    let call = self.calls.entry(key).or_default();
                    call.bytes += delta.len() as u64;
                    self.max_call_bytes = self.max_call_bytes.max(call.bytes);
                    for ch in delta.chars() {
                        self.chars += 1;
                        if call.in_string {
                            call.run = 0;
                            if call.escaped { call.escaped = false; }
                            else if ch == '\\' { call.escaped = true; }
                            else if ch == '"' { call.in_string = false; }
                        } else if matches!(ch, ' ' | '\t' | '\r' | '\n') {
                            self.whitespace += 1;call.run += 1;
                            self.longest = self.longest.max(call.run);
                        } else {
                            call.run = 0;
                            if ch == '"' { call.in_string = true; }
                        }
                    }
                }
            }
            Some("response.completed") => {
                // Only known numeric accounting fields, never arbitrary vendor JSON.
                let usage = &event["response"]["usage"];
                let mut safe = serde_json::Map::new();
                for key in ["input_tokens", "output_tokens", "total_tokens"] {
                    if let Some(n) = usage[key].as_u64() { safe.insert(key.into(), json!(n)); }
                }
                for (parent, key) in [("input_tokens_details", "cached_tokens"), ("output_tokens_details", "reasoning_tokens")] {
                    if let Some(n) = usage[parent][key].as_u64() { safe.insert(key.into(), json!(n)); }
                }
                if !safe.is_empty() { self.usage = Some(Value::Object(safe)); }
                self.outcome = "completed";
            }
            Some("response.failed" | "response.error" | "error") => self.outcome = "upstream_failed",
            _ => {}
        }
    }

    pub fn exceeded(&self) -> Option<&'static str> {
        if self.limits.argument_bytes > 0 && self.max_call_bytes > self.limits.argument_bytes {
            Some("argument_bytes")
        } else if self.limits.whitespace_run > 0 && self.longest > self.limits.whitespace_run {
            Some("whitespace_run")
        } else if self.limits.attempt_ms > 0 && self.started.elapsed().as_millis() >= self.limits.attempt_ms as u128 {
            Some("attempt_ms")
        } else { None }
    }

    pub fn cancel(&mut self) { self.outcome = "runaway_generation"; }

    pub fn finish_stream(&mut self) {
        if self.outcome == "streaming" { self.outcome = "interrupted"; }
    }

    pub fn has_arguments(&self) -> bool { self.deltas > 0 }

    pub fn progress_due(&mut self) -> bool {
        if self.deltas == 0 { return false; }
        let now = Instant::now();
        if self.last_progress.is_none_or(|last| now.duration_since(last) >= Duration::from_secs(5)) {
            self.last_progress = Some(now);true
        } else { false }
    }

    pub fn snapshot(&self) -> Value {
        let span = self.first_delta.zip(self.last_delta).map(|(a,b)| b.duration_since(a).as_millis() as u64).unwrap_or(0);
        json!({"schema":"relay.generation.v1", "attempt_id":self.id,
            "argument_deltas":self.deltas,"argument_bytes":self.bytes,"argument_chars":self.chars,
            "outside_string_whitespace_chars":self.whitespace,
            "outside_string_whitespace_ratio": if self.chars == 0 {0.0} else {self.whitespace as f64 / self.chars as f64},
            "longest_whitespace_run":self.longest,"max_call_argument_bytes":self.max_call_bytes,
            "delta_span_ms":span,"attempt_ms":self.started.elapsed().as_millis() as u64,
            "usage":self.usage,"outcome":self.outcome,
            "limits":{"argument_bytes":self.limits.argument_bytes,"whitespace_run":self.limits.whitespace_run,"attempt_ms":self.limits.attempt_ms}})
    }
}

impl Drop for Attempt {
    fn drop(&mut self) {
        self.finish_stream();
        tracing::info!(generation = %self.snapshot(), "relay generation summary");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn delta(a: &mut Attempt, id: &str, text: &str) {a.observe(&json!({"type":"response.function_call_arguments.delta","item_id":id,"delta":text}));}

    #[test]
    fn honest_large_edit_and_chunked_quotes_are_not_outside_whitespace() {
        let mut a=Attempt::new(Limits::default());
        delta(&mut a,"one","{\"text\":\"π\\");delta(&mut a,"one","\" ");
        delta(&mut a,"one",&" ".repeat(300_000));delta(&mut a,"one","\"}");
        assert_eq!(a.whitespace,0);assert!(a.bytes>300_000);assert_eq!(a.exceeded(),None);
        assert!(!a.snapshot().to_string().contains('π'));
    }

    #[test]
    fn interleaved_calls_have_independent_json_and_run_state() {
        let mut a=Attempt::new(Limits{whitespace_run:8,..Limits::default()});
        delta(&mut a,"a","{    ");delta(&mut a,"b","{\"text\":\"     ");
        delta(&mut a,"a","     ");assert_eq!(a.longest,9);assert_eq!(a.whitespace,9);
        assert_eq!(a.exceeded(),Some("whitespace_run"));
    }

    #[test]
    fn byte_ceiling_is_per_call_and_zero_means_off() {
        let mut a=Attempt::new(Limits{argument_bytes:8,..Limits::default()});
        delta(&mut a,"a","{\"π\":1}");delta(&mut a,"b","12345678");
        assert_eq!(a.max_call_bytes,8);assert_eq!(a.exceeded(),None);
        delta(&mut a,"a"," ");assert_eq!(a.exceeded(),Some("argument_bytes"));
        a.limits=Limits::default();assert_eq!(a.exceeded(),None);
    }

    #[test]
    fn completed_usage_is_numeric_and_failures_remain_distinct() {
        let mut a=Attempt::new(Limits::default());
        a.observe(&json!({"type":"response.completed","response":{"usage":{"input_tokens":12,"output_tokens":4,"secret":"NO","output_tokens_details":{"reasoning_tokens":3}}}}));
        assert_eq!(a.snapshot()["usage"]["reasoning_tokens"],3);assert!(!a.snapshot().to_string().contains("NO"));
        assert_eq!(a.outcome,"completed");
        let mut failed=Attempt::new(Limits::default());failed.observe(&json!({"type":"response.failed"}));assert_eq!(failed.outcome,"upstream_failed");
        let mut torn=Attempt::new(Limits::default());assert_eq!(torn.outcome,"streaming");
        torn.finish_stream();assert_eq!(torn.outcome,"interrupted");
    }
}
