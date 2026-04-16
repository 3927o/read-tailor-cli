use reqwest::Client;
use serde::{Deserialize, Serialize};

use crate::config::ResolvedAiConfig;

#[derive(Debug, Serialize)]
struct ChatRequest<'a> {
    model: &'a str,
    temperature: f32,
    messages: Vec<ChatMessage<'a>>,
}

#[derive(Debug, Serialize)]
struct ChatMessage<'a> {
    role: &'a str,
    content: &'a str,
}

#[derive(Debug, Deserialize)]
struct ChatResponse {
    choices: Vec<Choice>,
}

#[derive(Debug, Deserialize)]
struct Choice {
    message: ChoiceMessage,
}

#[derive(Debug, Deserialize)]
struct ChoiceMessage {
    content: Content,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum Content {
    Text(String),
    Parts(Vec<ContentPart>),
}

#[derive(Debug, Deserialize)]
struct ContentPart {
    #[serde(rename = "type")]
    kind: Option<String>,
    text: Option<String>,
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
    };
    let endpoint = config.endpoint();
    let request_body = serde_json::to_string_pretty(&request)
        .unwrap_or_else(|error| format!("{{\"serialization_error\":\"{error}\"}}"));

    let response = client
        .post(&endpoint)
        .bearer_auth(&config.api_key)
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
    let response_body = response.text().await.unwrap_or_default();

    if !status.is_success() {
        return AiCallRecord {
            endpoint,
            model: config.model.clone(),
            request_body,
            response_status: Some(status.as_u16()),
            response_body: Some(response_body.clone()),
            extracted_content: None,
            error: Some(format!("AI request failed with {status}: {response_body}")),
        };
    }

    let response: ChatResponse = match serde_json::from_str(&response_body) {
        Ok(response) => response,
        Err(error) => {
            return AiCallRecord {
                endpoint,
                model: config.model.clone(),
                request_body,
                response_status: Some(status.as_u16()),
                response_body: Some(response_body),
                extracted_content: None,
                error: Some(format!("failed to deserialize AI response: {error}")),
            };
        }
    };

    let choice = match response.choices.into_iter().next() {
        Some(choice) => choice,
        None => {
            return AiCallRecord {
                endpoint,
                model: config.model.clone(),
                request_body,
                response_status: Some(status.as_u16()),
                response_body: Some(response_body),
                extracted_content: None,
                error: Some("AI response did not contain any choices".to_string()),
            };
        }
    };

    let content = match choice.message.content {
        Content::Text(text) => text,
        Content::Parts(parts) => parts
            .into_iter()
            .filter(|part| part.kind.as_deref() == Some("text"))
            .filter_map(|part| part.text)
            .collect::<Vec<_>>()
            .join(""),
    };

    if content.trim().is_empty() {
        return AiCallRecord {
            endpoint,
            model: config.model.clone(),
            request_body,
            response_status: Some(status.as_u16()),
            response_body: Some(response_body),
            extracted_content: None,
            error: Some("AI response content was empty".to_string()),
        };
    }

    AiCallRecord {
        endpoint,
        model: config.model.clone(),
        request_body,
        response_status: Some(status.as_u16()),
        response_body: Some(response_body),
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
