#!/usr/bin/env python3
"""Shared plumbing used by all plan_N scripts.

Each plan_N exposes:

    def run(raw_html_path, output_html_path, output_structure_path) -> int

This module centralizes:
  - reading the raw HTML
  - writing the AI prompt + response trace files alongside the outputs
  - writing structure.json from the produced normalized HTML
"""
from __future__ import annotations

import json
import os
from typing import Any

from common.structure_summary import summarize


def read_raw(raw_html_path: str) -> str:
    with open(raw_html_path, "r", encoding="utf-8") as fh:
        return fh.read()


def write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def write_normalized(path: str, html: str) -> None:
    write_text(path, html)


def write_structure_json(html_path: str, structure_path: str) -> None:
    with open(html_path, "r", encoding="utf-8") as fh:
        html = fh.read()
    summary = summarize(html)
    write_text(structure_path, json.dumps(summary, ensure_ascii=False, indent=2))


def trace_paths(output_html_path: str) -> tuple[str, str]:
    base, _ = os.path.splitext(output_html_path)
    if base.endswith(".normalized"):
        base = base[: -len(".normalized")]
    return f"{base}.ai.prompt.txt", f"{base}.ai.response.txt"


def write_trace(output_html_path: str, prompt: str, response: str) -> None:
    prompt_path, response_path = trace_paths(output_html_path)
    write_text(prompt_path, prompt)
    write_text(response_path, response)


def require_ai_env() -> None:
    from config import AI_API_KEY, AI_BASE_URL, AI_MODEL

    missing = [
        name
        for name, value in (
            ("AI_BASE_URL", AI_BASE_URL),
            ("AI_API_KEY", AI_API_KEY),
            ("AI_MODEL", AI_MODEL),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing required env vars for AI calls: " + ", ".join(missing)
        )


def parse_ir_response(text: str) -> dict[str, Any]:
    """Extract a single JSON object from the AI response."""
    import re

    if not text or not text.strip():
        raise RuntimeError("AI returned empty IR response")
    # Try fenced ```json ... ``` first
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1).strip())
    # Otherwise assume bare JSON object
    stripped = text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("AI response contains no JSON object")
    return json.loads(stripped[start : end + 1])
