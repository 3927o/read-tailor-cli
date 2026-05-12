#!/usr/bin/env python3
"""Plan 1: AI generates normalize.py + full tag-tree outline."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _shared import (
    parse_ir_response,  # noqa: F401  (kept for symmetry; unused)
    read_raw,
    require_ai_env,
    write_normalized,
    write_structure_json,
    write_trace,
)
from ai_utils import call_ai, extract_code_block
from common.outline_full import build_full_outline
from common.prompts import SCRIPT_SYSTEM, script_user_prompt
from common.script_runner import run_generated_script


def run(raw_html_path: str, output_html_path: str, output_structure_path: str) -> int:
    require_ai_env()
    raw = read_raw(raw_html_path)
    outline = build_full_outline(raw)
    user = script_user_prompt("full_tag_outline", outline)
    response, tokens = call_ai(user, SCRIPT_SYSTEM, max_tokens=32000)
    write_trace(output_html_path, f"## SYSTEM\n{SCRIPT_SYSTEM}\n\n## USER\n{user}", response)
    script = extract_code_block(response, "python")
    result = run_generated_script(script, raw_html_path, output_html_path)
    if result["returncode"] != 0:
        raise RuntimeError(
            f"normalize.py exited with {result['returncode']}; "
            f"stderr: {result['stderr'][:500]}"
        )
    write_structure_json(output_html_path, output_structure_path)
    return tokens
