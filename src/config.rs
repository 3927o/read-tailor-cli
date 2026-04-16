use std::{env, fs, path::Path};

use anyhow::{Context, Result, bail};
use serde::Deserialize;

use crate::cli::Step;

#[derive(Debug, Default, Deserialize)]
pub struct FileConfig {
    #[serde(default)]
    pub ai: AiFileConfig,
}

#[derive(Debug, Default, Deserialize)]
pub struct AiFileConfig {
    pub base_url: Option<String>,
    pub api_key: Option<String>,
    pub model: Option<String>,
    #[serde(default)]
    pub step2: AiOverrideConfig,
    #[serde(default)]
    pub step4: AiOverrideConfig,
    #[serde(default)]
    pub step5: AiOverrideConfig,
}

#[derive(Debug, Default, Deserialize)]
pub struct AiOverrideConfig {
    pub base_url: Option<String>,
    pub api_key: Option<String>,
    pub model: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ResolvedAiConfig {
    pub base_url: String,
    pub api_key: String,
    pub model: String,
}

impl ResolvedAiConfig {
    pub fn endpoint(&self) -> String {
        let base = self.base_url.trim_end_matches('/');
        if base.ends_with("/chat/completions") {
            base.to_string()
        } else if base.ends_with("/v1") {
            format!("{base}/chat/completions")
        } else {
            format!("{base}/v1/chat/completions")
        }
    }
}

pub fn load_config(root: &Path) -> Result<FileConfig> {
    let path = root.join("bookcli.toml");
    if !path.exists() {
        return Ok(FileConfig::default());
    }

    let raw = fs::read_to_string(&path)
        .with_context(|| format!("failed to read config file {}", path.display()))?;
    toml::from_str(&raw).with_context(|| format!("failed to parse config file {}", path.display()))
}

pub fn resolve_ai_config(config: &FileConfig, step: Step) -> Result<Option<ResolvedAiConfig>> {
    let step_key = match step {
        Step::Step2 => Some("STEP2"),
        Step::Step4 => Some("STEP4"),
        Step::Step5 => Some("STEP5"),
        _ => None,
    };

    let file_step = match step {
        Step::Step2 => Some(&config.ai.step2),
        Step::Step4 => Some(&config.ai.step4),
        Step::Step5 => Some(&config.ai.step5),
        _ => None,
    };

    let base_url = pick_value(
        "AI_BASE_URL",
        step_key.map(|value| format!("AI_BASE_URL_{value}")),
        file_step.and_then(|item| item.base_url.clone()),
        config.ai.base_url.clone(),
    );
    let api_key = pick_value(
        "AI_API_KEY",
        step_key.map(|value| format!("AI_API_KEY_{value}")),
        file_step.and_then(|item| item.api_key.clone()),
        config.ai.api_key.clone(),
    );
    let model = pick_value(
        "AI_MODEL",
        step_key.map(|value| format!("AI_MODEL_{value}")),
        file_step.and_then(|item| item.model.clone()),
        config.ai.model.clone(),
    );

    let provided = [base_url.as_ref(), api_key.as_ref(), model.as_ref()]
        .into_iter()
        .filter(|value| value.is_some())
        .count();

    if provided == 0 {
        return Ok(None);
    }

    if provided != 3 {
        bail!(
            "AI configuration for {} is incomplete; base_url, api_key and model must all be provided",
            step.label()
        );
    }

    Ok(Some(ResolvedAiConfig {
        base_url: base_url.unwrap(),
        api_key: api_key.unwrap(),
        model: model.unwrap(),
    }))
}

fn pick_value(
    global_env_key: &str,
    step_env_key: Option<String>,
    file_step_value: Option<String>,
    file_global_value: Option<String>,
) -> Option<String> {
    if let Some(key) = step_env_key {
        if let Some(value) = read_env(&key) {
            return Some(value);
        }
    }

    if let Some(value) = file_step_value.filter(|value| !value.trim().is_empty()) {
        return Some(value);
    }

    read_env(global_env_key).or_else(|| file_global_value.filter(|value| !value.trim().is_empty()))
}

fn read_env(key: &str) -> Option<String> {
    env::var(key).ok().filter(|value| !value.trim().is_empty())
}
