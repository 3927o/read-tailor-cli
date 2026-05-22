#!/usr/bin/env python3
"""Run plan_1..plan_6 and produce a comparison report.

Usage:
  python3 runner.py                 # run all 6 plans
  python3 runner.py --plan 1 4 6    # run a subset
  python3 runner.py --eval-only     # only evaluate existing outputs
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import time

from config import RAW_HTML, RESULTS_FILE, TEST_OUTPUT_DIR
from evaluator import EvalResult, evaluate, evaluate_structure_json


PLANS: dict[str, dict[str, str]] = {
    "1": {
        "name": "1: AI script + full outline",
        "module": "plan_1_script_full",
        "html": "plan_1.normalized.html",
        "structure": "plan_1.structure.json",
    },
    "2": {
        "name": "2: AI script + trimmed outline",
        "module": "plan_2_script_trimmed",
        "html": "plan_2.normalized.html",
        "structure": "plan_2.structure.json",
    },
    "3": {
        "name": "3: AI script + facts",
        "module": "plan_3_script_facts",
        "html": "plan_3.normalized.html",
        "structure": "plan_3.structure.json",
    },
    "4": {
        "name": "4: AI IR + full outline",
        "module": "plan_4_ir_full",
        "html": "plan_4.normalized.html",
        "structure": "plan_4.structure.json",
    },
    "5": {
        "name": "5: AI IR + trimmed outline",
        "module": "plan_5_ir_trimmed",
        "html": "plan_5.normalized.html",
        "structure": "plan_5.structure.json",
    },
    "6": {
        "name": "6: AI IR + facts",
        "module": "plan_6_ir_facts",
        "html": "plan_6.normalized.html",
        "structure": "plan_6.structure.json",
    },
    "7": {
        "name": "7: program-first + AI labels",
        "module": "plan_7_program_first",
        "html": "plan_7.normalized.html",
        "structure": "plan_7.structure.json",
    },
}


def run_plan(plan_id: str, eval_only: bool = False) -> EvalResult:
    info = PLANS[plan_id]
    output_html = os.path.join(TEST_OUTPUT_DIR, info["html"])
    output_structure = os.path.join(TEST_OUTPUT_DIR, info["structure"])
    result = EvalResult(approach=info["name"])

    if not eval_only:
        print(f"\n{'=' * 60}\n  Running: {info['name']}\n{'=' * 60}")
        try:
            module = importlib.import_module(info["module"])
        except Exception as exc:
            result.errors.append(f"加载脚本失败: {type(exc).__name__}: {exc}")
            print(result.summary())
            return result
        start = time.time()
        try:
            tokens = module.run(RAW_HTML, output_html, output_structure)
            result.ai_tokens = int(tokens or 0)
        except Exception as exc:
            result.time_seconds = time.time() - start
            msg = str(exc)
            if len(msg) > 300:
                msg = msg[:300] + "..."
            result.errors.append(f"执行异常: {type(exc).__name__}: {msg}")
            print(result.summary())
            return result
        result.time_seconds = time.time() - start
        print(f"  完成，耗时 {result.time_seconds:.1f}s")

    if os.path.exists(output_html):
        eval_result = evaluate(output_html, info["name"], raw_html_path=RAW_HTML)
        result.errors.extend(eval_result.errors)
        result.warnings.extend(eval_result.warnings)
        result.metrics.update(eval_result.metrics)
        if os.path.exists(output_structure):
            evaluate_structure_json(output_structure, result)
    elif not eval_only:
        result.errors.append(f"输出文件不存在: {output_html}")

    print(result.summary())
    return result


def print_comparison(all_results: list[dict]) -> None:
    print(f"\n{'=' * 80}\n  方案对比\n{'=' * 80}")
    header = (
        f"{'方案':<32} {'耗时(s)':>8} {'tokens':>8} {'章节':>5} "
        f"{'noteref':>8} {'note':>5} {'orphan':>7} {'jump':>5} "
        f"{'字符召回':>9} {'状态':>6}"
    )
    print(header)
    print("-" * len(header))
    for r in all_results:
        m = r.get("metrics", {})
        status = "PASS" if r.get("pass") else "FAIL"
        recall = m.get("char_recall")
        recall_str = f"{recall * 100:.1f}%" if isinstance(recall, (int, float)) else "?"
        print(
            f"{r['approach']:<32} "
            f"{r['time_seconds']:>8.1f} "
            f"{r['ai_tokens']:>8} "
            f"{m.get('chapter_count', '?'):>5} "
            f"{m.get('noteref_count', '?'):>8} "
            f"{m.get('note_count', '?'):>5} "
            f"{m.get('noteref_to_note_orphan_count', '?'):>7} "
            f"{m.get('heading_jump_count', '?'):>5} "
            f"{recall_str:>9} "
            f"{status:>6}"
        )
    print("-" * len(header))
    for r in all_results:
        if r.get("errors") or r.get("warnings"):
            print(f"\n  {r['approach']}:")
            for e in r.get("errors", []):
                print(f"    x {e}")
            for w in r.get("warnings", []):
                print(f"    ! {w}")


def main() -> None:
    eval_only = "--eval-only" in sys.argv
    plan_filter: list[str] | None = None
    if "--plan" in sys.argv:
        idx = sys.argv.index("--plan")
        plan_filter = [arg for arg in sys.argv[idx + 1 :] if not arg.startswith("--")]
    plans_to_run = plan_filter or list(PLANS.keys())

    all_results = []
    for plan_id in plans_to_run:
        if plan_id not in PLANS:
            print(f"Unknown plan: {plan_id}")
            continue
        all_results.append(run_plan(plan_id, eval_only=eval_only).to_dict())

    with open(RESULTS_FILE, "w", encoding="utf-8") as fh:
        json.dump(all_results, fh, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到 {RESULTS_FILE}")
    print_comparison(all_results)


if __name__ == "__main__":
    main()
