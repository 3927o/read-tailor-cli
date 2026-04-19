#!/usr/bin/env python3
"""
测试运行器：依次执行 6 个方案，收集结果并生成对比报告。

用法：
  python3 runner.py              # 跑所有方案
  python3 runner.py --plan B D   # 只跑方案 B 和 D
  python3 runner.py --eval-only  # 只评估已有输出（不重新生成）
"""
import json
import os
import sys
import time
import importlib

from config import TEST_OUTPUT_DIR, RESULTS_FILE, RAW_HTML
from evaluator import evaluate, evaluate_structure_json, EvalResult


APPROACHES = {
    "A": {
        "name": "A: AI生成脚本",
        "script": "plan_a_ai_gen_script",
        "output_html": "plan_a.normalized.html",
        "output_structure": "plan_a.structure.json",
    },
    "B": {
        "name": "B: AI输出YAML映射",
        "script": "plan_b_ai_yaml_mapping",
        "output_html": "plan_b.normalized.html",
        "output_structure": "plan_b.structure.json",
    },
    "C": {
        "name": "C: AI直接输出HTML",
        "script": "plan_c_ai_direct_html",
        "output_html": "plan_c.normalized.html",
        "output_structure": "plan_c.structure.json",
    },
    "D": {
        "name": "D: 纯规则无AI",
        "script": "plan_d_heuristic",
        "output_html": "plan_d.normalized.html",
        "output_structure": "plan_d.structure.json",
    },
    "E": {
        "name": "E: 正则预提取+AI",
        "script": "plan_e_regex_ai",
        "output_html": "plan_e.normalized.html",
        "output_structure": "plan_e.structure.json",
    },
    "F": {
        "name": "F: 约束层级分块",
        "script": "plan_f_constrained",
        "output_html": "plan_f.normalized.html",
        "output_structure": "plan_f.structure.json",
    },
}


def run_approach(plan_id: str, eval_only: bool = False) -> EvalResult:
    """运行单个方案并评估"""
    info = APPROACHES[plan_id]
    output_html = os.path.join(TEST_OUTPUT_DIR, info["output_html"])
    output_structure = os.path.join(TEST_OUTPUT_DIR, info["output_structure"])

    result = EvalResult(info["name"])

    if not eval_only:
        print(f"\n{'='*60}")
        print(f"  Running: {info['name']}")
        print(f"{'='*60}")

        # 动态加载方案脚本
        try:
            module = importlib.import_module(info["script"])
        except Exception as e:
            result.errors.append(f"加载脚本失败: {e}")
            print(result.summary())
            return result

        start = time.time()
        try:
            ai_tokens = module.run(RAW_HTML, output_html, output_structure)
            result.ai_tokens = ai_tokens or 0
        except Exception as e:
            result.time_seconds = time.time() - start
            result.errors.append(f"执行异常: {type(e).__name__}: {str(e)[:200]}")
            print(result.summary())
            return result

        result.time_seconds = time.time() - start
        print(f"  完成，耗时 {result.time_seconds:.1f}s")

    # 评估输出
    if os.path.exists(output_html):
        eval_result = evaluate(output_html, info["name"])
        # 合并评估结果到 result
        result.errors.extend(eval_result.errors)
        result.warnings.extend(eval_result.warnings)
        result.metrics.update(eval_result.metrics)

        if os.path.exists(output_structure):
            evaluate_structure_json(output_structure, result)
    else:
        if not eval_only:
            result.errors.append(f"输出文件不存在: {output_html}")

    print(result.summary())
    return result


def print_comparison(all_results: list[dict]):
    """打印对比表"""
    print(f"\n{'='*80}")
    print("  方案对比")
    print(f"{'='*80}")
    print(f"{'方案':<25} {'耗时(s)':>8} {'章节数':>8} {'注释引用':>10} {'标准noteref':>12} {'状态':>6}")
    print("-" * 80)

    for r in all_results:
        m = r.get("metrics", {})
        status = "PASS" if r.get("pass") else "FAIL"
        print(
            f"{r['approach']:<25} "
            f"{r['time_seconds']:>8.1f} "
            f"{m.get('chapter_count', '?'):>8} "
            f"{m.get('note_ref_count', '?'):>10} "
            f"{m.get('standard_noteref_count', '?'):>12} "
            f"{status:>6}"
        )

    print("-" * 80)

    # 详细警告
    print("\n各方案详情：")
    for r in all_results:
        if r.get("warnings") or r.get("errors"):
            print(f"\n  {r['approach']}:")
            for e in r.get("errors", []):
                print(f"    ✗ {e}")
            for w in r.get("warnings", []):
                print(f"    ⚠ {w}")


def main():
    eval_only = "--eval-only" in sys.argv
    plan_filter = None
    if "--plan" in sys.argv:
        idx = sys.argv.index("--plan")
        plan_filter = sys.argv[idx + 1:]

    plans_to_run = plan_filter or list(APPROACHES.keys())

    all_results = []
    for plan_id in plans_to_run:
        if plan_id not in APPROACHES:
            print(f"Unknown plan: {plan_id}")
            continue
        result = run_approach(plan_id, eval_only=eval_only)
        all_results.append(result.to_dict())

    # 保存结果
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到 {RESULTS_FILE}")

    # 打印对比
    print_comparison(all_results)


if __name__ == "__main__":
    main()
