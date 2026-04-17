use futures_util::StreamExt;
use reqwest::Client;
use serde::{Deserialize, Serialize};

use crate::config::ResolvedAiConfig;

#[derive(Debug, Serialize)]
struct ChatRequest<'a> {
    model: &'a str,
    temperature: f32,
    messages: Vec<ChatMessage<'a>>,
    stream: bool,
}

#[derive(Debug, Serialize)]
struct ChatMessage<'a> {
    role: &'a str,
    content: &'a str,
}

#[derive(Debug, Deserialize)]
struct StreamChunk {
    #[serde(default)]
    choices: Vec<StreamChoice>,
}

#[derive(Debug, Deserialize)]
struct StreamChoice {
    #[serde(default)]
    delta: StreamDelta,
    #[serde(default)]
    finish_reason: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
struct StreamDelta {
    #[serde(default)]
    content: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct AiCallRecord {
    pub endpoint: String,
    pub model: String,
    pub request_body: String,
    pub response_status: Option<u16>,
    pub response_body: Option<String>,
    pub extracted_content: Option<String>,
    pub error: Option<String>,
}

pub async fn complete_with_debug(
    config: &ResolvedAiConfig,
    system: &str,
    user: &str,
) -> AiCallRecord {
    let client = Client::new();
    let request = ChatRequest {
        model: &config.model,
        temperature: 0.2,
        messages: vec![
            ChatMessage {
                role: "system",
                content: system,
            },
            ChatMessage {
                role: "user",
                content: user,
            },
        ],
        stream: true,
    };
    let endpoint = config.endpoint();
    let request_body = serde_json::to_string_pretty(&request)
        .unwrap_or_else(|error| format!("{{\"serialization_error\":\"{error}\"}}"));

    let response = client
        .post(&endpoint)
        .bearer_auth(&config.api_key)
        .header("Accept", "text/event-stream")
        .json(&request)
        .send()
        .await;

    let response = match response {
        Ok(response) => response,
        Err(error) => {
            return AiCallRecord {
                endpoint,
                model: config.model.clone(),
                request_body,
                response_status: None,
                response_body: None,
                extracted_content: None,
                error: Some(format!("failed to call AI endpoint: {error}")),
            };
        }
    };

    let status = response.status();

    if !status.is_success() {
        let error_body = response.text().await.unwrap_or_default();
        return AiCallRecord {
            endpoint,
            model: config.model.clone(),
            request_body,
            response_status: Some(status.as_u16()),
            response_body: Some(error_body.clone()),
            extracted_content: None,
            error: Some(format!("AI request failed with {status}: {error_body}")),
        };
    }

    let mut stream = response.bytes_stream();
    let mut raw_buffer = String::new();
    let mut line_buffer = String::new();
    let mut content_parts: Vec<String> = Vec::new();
    let mut stream_error: Option<String> = None;
    let mut finish_reason: Option<String> = None;

    while let Some(chunk) = stream.next().await {
        let chunk = match chunk {
            Ok(bytes) => bytes,
            Err(error) => {
                stream_error = Some(format!("stream read error: {error}"));
                break;
            }
        };
        let text = match std::str::from_utf8(&chunk) {
            Ok(text) => text,
            Err(error) => {
                stream_error = Some(format!("utf-8 decode error in stream chunk: {error}"));
                break;
            }
        };
        raw_buffer.push_str(text);
        line_buffer.push_str(text);

        while let Some(idx) = line_buffer.find('\n') {
            let mut line: String = line_buffer.drain(..=idx).collect();
            // Strip trailing \n and an optional preceding \r.
            if line.ends_with('\n') {
                line.pop();
            }
            if line.ends_with('\r') {
                line.pop();
            }
            let line = line.trim();
            if line.is_empty() || line.starts_with(':') {
                continue;
            }
            let payload = match line.strip_prefix("data:") {
                Some(rest) => rest.trim(),
                None => continue,
            };
            if payload == "[DONE]" {
                finish_reason.get_or_insert_with(|| "done".to_string());
                continue;
            }
            let parsed: StreamChunk = match serde_json::from_str(payload) {
                Ok(chunk) => chunk,
                Err(_) => continue,
            };
            for choice in parsed.choices {
                if let Some(text) = choice.delta.content {
                    if !text.is_empty() {
                        content_parts.push(text);
                    }
                }
                if let Some(reason) = choice.finish_reason {
                    finish_reason = Some(reason);
                }
            }
        }
    }

    if let Some(error) = stream_error {
        return AiCallRecord {
            endpoint,
            model: config.model.clone(),
            request_body,
            response_status: Some(status.as_u16()),
            response_body: Some(raw_buffer),
            extracted_content: None,
            error: Some(error),
        };
    }

    if finish_reason.is_none() {
        return AiCallRecord {
            endpoint,
            model: config.model.clone(),
            request_body,
            response_status: Some(status.as_u16()),
            response_body: Some(raw_buffer),
            extracted_content: None,
            error: Some("AI stream ended without a finish signal".to_string()),
        };
    }

    let content: String = content_parts.concat();
    if content.trim().is_empty() {
        return AiCallRecord {
            endpoint,
            model: config.model.clone(),
            request_body,
            response_status: Some(status.as_u16()),
            response_body: Some(raw_buffer),
            extracted_content: None,
            error: Some("AI response content was empty".to_string()),
        };
    }

    AiCallRecord {
        endpoint,
        model: config.model.clone(),
        request_body,
        response_status: Some(status.as_u16()),
        response_body: Some(raw_buffer),
        extracted_content: Some(strip_code_fences(&content)),
        error: None,
    }
}

fn strip_code_fences(value: &str) -> String {
    let trimmed = value.trim();
    if let Some(stripped) = trimmed.strip_prefix("```") {
        let stripped = stripped
            .split_once('\n')
            .map(|(_, rest)| rest)
            .unwrap_or_default();
        return stripped.trim_end_matches("```").trim().to_string();
    }
    trimmed.to_string()
}
