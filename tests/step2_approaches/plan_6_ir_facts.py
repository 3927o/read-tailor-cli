#!/usr/bin/env python3
"""Plan 6: AI emits IR (ir-1.0) + structural facts; engine renders."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _shared import (
    parse_ir_response,
    read_raw,
    require_ai_env,
    write_normalized,
    write_structure_json,
    write_trace,
)
from ai_utils import call_ai
from common.facts_extractor import extract_facts
from common.ir_engine import apply_ir
from common.ir_schema import validate as ir_validate
from common.prompts import IR_SYSTEM, ir_user_prompt


def run(raw_html_path: str, output_html_path: str, output_structure_path: str) -> int:
    require_ai_env()
    raw = read_raw(raw_html_path)
    facts = extract_facts(raw)
    payload = json.dumps(facts, ensure_ascii=False, indent=2)
    user = ir_user_prompt("structural_facts_json", payload)
    response, tokens = call_ai(user, IR_SYSTEM, max_tokens=32000)
    write_trace(output_html_path, f"## SYSTEM\n{IR_SYSTEM}\n\n## USER\n{user}", response)
    ir = parse_ir_response(response)
    errors = ir_validate(ir)
    if errors:
        raise RuntimeError("IR validation failed: " + "; ".join(errors))
    normalized = apply_ir(raw, ir)
    write_normalized(output_html_path, normalized)
    write_structure_json(output_html_path, output_structure_path)
    return tokens
