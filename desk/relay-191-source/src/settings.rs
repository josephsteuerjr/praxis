//! Durable settings shared by desktop and headless; credentials are never part
//! of this document. Unknown fields and invalid patches fail before persistence.
use crate::core::config::Config;
use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::Path;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Appearance {
    pub theme: String,
    pub paper_light: String,
    pub paper_dark: String,
    pub accent: String,
    pub scale: u16,
    pub density: String,
    pub handwritten: bool,
    pub grain: bool,
    pub reduced_motion: bool,
}
impl Default for Appearance {
    fn default() -> Self {
        Self {
            theme: "system".into(),
            paper_light: "cream".into(),
            paper_dark: "coal".into(),
            accent: "terra".into(),
            scale: 100,
            density: "comfortable".into(),
            handwritten: true,
            grain: true,
            reduced_motion: false,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Defaults {
    pub model: String,
    pub reasoning: String,
    pub instructions_mode: String,
    pub instructions: String,
    pub parallel_tools: bool,
}
impl Default for Defaults {
    fn default() -> Self {
        Self {
            model: String::new(),
            reasoning: "none".into(),
            instructions_mode: "codex".into(),
            instructions: String::new(),
            parallel_tools: true,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Labels {
    pub primary: String,
    pub secondary: String,
}
impl Default for Labels {
    fn default() -> Self {
        Self {
            primary: "Первая подписка".into(),
            secondary: "Вторая подписка".into(),
        }
    }
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub port: u16,
    pub auto_start_api: bool,
    pub start_in_tray: bool,
    pub quota_refresh_seconds: u16,
    pub appearance: Appearance,
    pub defaults: Defaults,
    pub labels: Labels,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            port: 5011,
            auto_start_api: false,
            start_in_tray: false,
            quota_refresh_seconds: 0,
            appearance: Appearance::default(),
            defaults: Defaults::default(),
            labels: Labels::default(),
        }
    }
}
fn choice(value: &str, choices: &[&str], name: &str) -> Result<()> {
    if !choices.contains(&value) {
        bail!("Invalid {name}");
    }
    Ok(())
}
pub fn launch_defaults(arguments: &[String]) -> Result<serde_json::Map<String, Value>> {
    let mut defaults = serde_json::Map::new();
    let mut overrides = serde_json::Map::new();
    let mut index = 0;
    while index < arguments.len() {
        let argument = &arguments[index];
        if matches!(argument.as_str(), "--minimized" | "--start-api") {
            index += 1;
            continue;
        }
        if !matches!(argument.as_str(), "--config" | "--model" | "--reasoning") {
            bail!("Unknown relay launch argument");
        }
        let value = arguments.get(index + 1).filter(|value| !value.starts_with("--"))
            .context("Relay launch argument requires a value")?;
        if argument == "--config" {
            let config: Value = serde_json::from_slice(&std::fs::read(value)?)
                .context("Cannot read application configuration")?;
            let model = config.get("model").context("Application model configuration missing")?;
            defaults.insert("model".into(), Value::String(model.get("model").and_then(Value::as_str).unwrap_or("").into()));
            defaults.insert("reasoning".into(), Value::String(model.get("reasoning_effort").and_then(Value::as_str).filter(|value| !value.is_empty()).unwrap_or("none").into()));
        } else {
            overrides.insert(argument.trim_start_matches("--").into(), Value::String(value.clone()));
        }
        index += 2;
    }
    defaults.extend(overrides);
    Settings::default().patched(serde_json::json!({"defaults":defaults}))?;
    Ok(defaults)
}
impl Settings {
    pub fn load(root: &Path) -> Result<Self> {
        let path = root.join("settings.json");
        let settings: Self = match std::fs::read(&path) {
            Ok(bytes) => serde_json::from_slice(&bytes)
                .context("settings.json is invalid; the existing file was preserved")?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Self::default(),
            Err(e) => return Err(e.into()),
        };
        settings.validate()?;
        Ok(settings)
    }
    pub fn validate(&self) -> Result<()> {
        if self.port == 0 {
            bail!("Port must be 1..65535");
        }
        let a = &self.appearance;
        choice(&a.theme, &["system", "light", "dark"], "theme")?;
        choice(
            &a.paper_light,
            &["cream", "white", "kraft", "sage", "sky"],
            "light paper",
        )?;
        choice(
            &a.paper_dark,
            &["coal", "cocoa", "graphite", "navy", "pine"],
            "dark paper",
        )?;
        choice(
            &a.accent,
            &["terra", "ochre", "sage", "plum", "sea", "cinnabar"],
            "accent",
        )?;
        choice(&a.density, &["comfortable", "compact"], "density")?;
        if ![85, 100, 115, 130].contains(&a.scale) {
            bail!("Invalid interface scale");
        }
        if ![0, 60, 300, 900].contains(&self.quota_refresh_seconds) {
            bail!("Invalid quota refresh interval");
        }
        for label in [&self.labels.primary, &self.labels.secondary] {
            if label.trim().is_empty()
                || label.chars().count() > 48
                || label.chars().any(char::is_control)
            {
                bail!("Account names must contain 1..48 printable characters");
            }
        }
        let d = &self.defaults;
        if d.model.len() > 160
            || d.model
                .chars()
                .any(|c| !c.is_ascii_alphanumeric() && !"-._/".contains(c))
        {
            bail!("Invalid default model identifier");
        }
        choice(
            &d.reasoning,
            &[
                "default", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
            ],
            "reasoning effort",
        )?;
        choice(
            &d.instructions_mode,
            &["codex", "minimal"],
            "instruction mode",
        )?;
        if d.instructions.len() > 16000 || d.instructions.contains('\0') {
            bail!("Instructions must be at most 16000 UTF-8 bytes without NUL");
        }
        Ok(())
    }
    pub fn patched(&self, patch: Value) -> Result<Self> {
        if !patch.is_object() {
            bail!("Settings patch must be an object");
        }
        fn merge(target: &mut Value, patch: Value) {
            if let (Some(object), Value::Object(changes)) = (target.as_object_mut(), &patch) {
                for (key, value) in changes {
                    merge(object.entry(key).or_insert(Value::Null), value.clone());
                }
            } else {
                *target = patch;
            }
        }
        let mut value = serde_json::to_value(self)?;
        merge(&mut value, patch);
        let next: Self = serde_json::from_value(value)?;
        next.validate()?;
        Ok(next)
    }
    pub fn effective_port(&self) -> Result<u16> {
        match std::env::var("RELAY_PORT") {
            Ok(raw) => raw
                .parse::<u16>()
                .ok()
                .filter(|p| *p > 0)
                .context("RELAY_PORT must be 1..65535"),
            Err(_) => Ok(self.port),
        }
    }
    pub fn config(&self, base: &Config) -> Config {
        let mut config = base.clone();
        config.model =
            std::env::var("RELAY_DEFAULT_MODEL").unwrap_or_else(|_| self.defaults.model.clone());
        if std::env::var_os("RELAY_REASONING_EFFORT").is_none() {
            config.reasoning_effort =
                (self.defaults.reasoning != "default").then(|| self.defaults.reasoning.clone());
        }
        if std::env::var_os("RELAY_INSTRUCTIONS").is_none() {
            config.instructions_mode = Some(self.defaults.instructions_mode.clone());
        }
        if std::env::var_os("RELAY_PARALLEL_TOOL_CALLS").is_none() {
            config.parallel_tool_calls = self.defaults.parallel_tools;
        }
        let extra = self.defaults.instructions.trim();
        if !extra.is_empty() {
            config.user_instructions = Some(match config.user_instructions.take() {
                Some(text) => format!("{text}\n\n{extra}"),
                None => extra.to_string(),
            });
        }
        config
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn old_port_only_settings_migrate_and_nested_patches_preserve_siblings() {
        let root = tempfile::tempdir().unwrap();
        std::fs::write(root.path().join("settings.json"), r#"{"port":19509}"#).unwrap();
        let original = Settings::load(root.path()).unwrap();
        let next = original
            .patched(
                serde_json::json!({"appearance":{"theme":"dark"},"labels":{"primary":"Работа"}}),
            )
            .unwrap();
        assert_eq!(next.port, 19509);
        assert_eq!(next.appearance.theme, "dark");
        assert_eq!(next.appearance.accent, original.appearance.accent);
        assert_eq!(next.labels.secondary, original.labels.secondary);
        assert!(original.patched(serde_json::json!({"port":0})).is_err());
        assert!(original
            .patched(serde_json::json!({"appearance":{"made_up":true}}))
            .is_err());
        assert!(original
            .patched(serde_json::json!({"defaults":{"instructions":"\0"}}))
            .is_err());
    }
}
