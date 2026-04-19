#!/usr/bin/env python3
"""
共用 AI 调用工具（流式版本）。
"""
import json
import os
import re
import subprocess
import tempfile

from config import AI_BASE_URL, AI_API_KEY, AI_MODEL


def call_ai(prompt: str, system: str = "", max_tokens: int = 8000) -> tuple[str, int]:
    """
    调用 AI 模型（流式），返回 (response_text, total_tokens)。
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "stream": True,
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        payload_file = f.name

    try:
        base = AI_BASE_URL.rstrip("/")
        if not base.endswith("/chat/completions"):
            url = f"{base}/chat/completions"
        else:
            url = base

        result = subprocess.run(
            [
                "curl", "-s", "-N",
                "-X", "POST",
                url,
                "-H", "Content-Type: application/json",
                "-H", f"Authorization: Bearer {AI_API_KEY}",
                "-d", f"@{payload_file}",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )

        if result.returncode != 0:
            raise RuntimeError(f"curl failed: {result.stderr}")

        raw = result.stdout.strip()
        if not raw:
            raise RuntimeError(f"curl returned empty response (stderr: {result.stderr[:300]})")

        # 解析 SSE 流：data: {...}\n\n 格式
        content_parts = []
        total_tokens = 0

        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith(":"):
                # OpenRouter 等 provider 的注释行，跳过
                continue
            if line == "data: [DONE]":
                break
            if line.startswith("data: "):
                json_str = line[6:]  # 去掉 "data: " 前缀
                try:
                    chunk = json.loads(json_str)
                    # 提取 content delta
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            content_parts.append(delta["content"])
                    # 提取 usage（通常在最后一个 chunk）
                    usage = chunk.get("usage", {})
                    if usage:
                        total_tokens = usage.get("total_tokens", 0)
                except json.JSONDecodeError:
                    continue

        full_content = "".join(content_parts)

        if not full_content:
            # 可能不是流式响应，尝试直接解析整个响应
            try:
                resp = json.loads(raw)
                if "choices" in resp:
                    full_content = resp["choices"][0]["message"]["content"]
                    total_tokens = resp.get("usage", {}).get("total_tokens", 0)
                elif "error" in resp:
                    raise RuntimeError(f"AI error: {resp['error']}")
            except json.JSONDecodeError:
                raise RuntimeError(f"无法解析 AI 响应: {raw[:500]}")

        return full_content, total_tokens

    finally:
        os.unlink(payload_file)


def extract_code_block(text: str, lang: str = "python") -> str:
    """从 AI 响应中提取代码块"""
    if not text:
        return ""
    pattern = rf"```{lang}\s*\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def extract_yaml_block(text: str) -> str:
    """从 AI 响应中提取 YAML 块"""
    if not text:
        return ""
    result = extract_code_block(text, "yaml")
    if result == text.strip():
        match = re.search(r"```\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
    return result
