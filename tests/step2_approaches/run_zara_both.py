#!/usr/bin/env python3
"""跑查这本书的两条 normalize 方案；两路都走完整 plan_7（含 AI label 决策）。

依赖 AI_BASE_URL / AI_API_KEY / AI_MODEL 环境变量。无 AI 时单独传 --no-ai
退回纯程序基线。
"""
import argparse, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bs4 import BeautifulSoup
from _shared import parse_ir_response
from ai_utils import call_ai
from common.epub_recover import recover
from common.epub_direct import epub_to_html as direct_epub_to_html
from plan_7_program_first import (
    SYSTEM, _outline, _user_prompt, build_normalized,
)

REPO = os.path.dirname(os.path.dirname(HERE))
EPUB = os.path.join(REPO, "查拉图斯特拉如是说（译文经典）.epub")
TITLE_FALLBACK = "查拉图斯特拉如是说（译文经典）"


def title_from(html: str) -> str:
    t = BeautifulSoup(html, "html.parser").find("title")
    return (t.get_text(strip=True) if t else "") or TITLE_FALLBACK


def plan7_with_ai(raw_html: str, label: str, use_ai: bool) -> str:
    """共享下游：outline → (AI 决策 | 空) → build_normalized。"""
    src = BeautifulSoup(raw_html, "html.parser")
    body = src.find("body")
    if body is None:
        raise RuntimeError(f"{label}: raw HTML has no <body>")
    rows, _ = _outline(body)
    title_guess = title_from(raw_html)
    print(f"  outline: {len(rows)} headings", file=sys.stderr)

    decisions: dict[int, dict] = {}
    doc_meta = {"title": title_guess, "language": "zh"}
    if use_ai:
        user = _user_prompt(rows, title_guess)
        t = time.time()
        resp, tokens = call_ai(user, SYSTEM, max_tokens=32000)
        print(f"  AI:      {tokens} tokens   {time.time()-t:.1f}s", file=sys.stderr)
        parsed = parse_ir_response(resp)
        for item in parsed.get("headings") or []:
            if isinstance(item, dict) and "i" in item:
                try:
                    decisions[int(item["i"])] = item
                except (TypeError, ValueError):
                    continue
        if parsed.get("document"):
            doc_meta.update(parsed["document"])
        print(f"  decisions: {len(decisions)}", file=sys.stderr)
    return build_normalized(raw_html, decisions, doc_meta)


def run_route_1(use_ai: bool) -> str:
    print("[route 1] pandoc + epub_recover + plan_7", file=sys.stderr)
    t0 = time.time()
    raw = subprocess.run(
        ["pandoc", EPUB, "-f", "epub", "-t", "html", "-s", "-o", "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    print(f"  pandoc raw: {len(raw):>9} chars  ({time.time()-t0:.1f}s)", file=sys.stderr)
    augmented, _info = recover(EPUB, raw)
    print(f"  recover  -> {len(augmented):>9} chars", file=sys.stderr)
    norm = plan7_with_ai(augmented, "route1", use_ai)
    print(f"  plan_7   -> {len(norm):>9} chars  ({time.time()-t0:.1f}s total)", file=sys.stderr)
    return norm


def run_route_2(use_ai: bool) -> str:
    print("[route 2] epub_direct + plan_7 (no recover)", file=sys.stderr)
    t0 = time.time()
    raw = direct_epub_to_html(EPUB)
    print(f"  direct   -> {len(raw):>9} chars  ({time.time()-t0:.1f}s)", file=sys.stderr)
    norm = plan7_with_ai(raw, "route2", use_ai)
    print(f"  plan_7   -> {len(norm):>9} chars  ({time.time()-t0:.1f}s total)", file=sys.stderr)
    return norm


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ai", action="store_true", help="跳过 plan_7 的 AI label 步骤")
    args = ap.parse_args()
    use_ai = (not args.no_ai)
    if use_ai and not os.environ.get("AI_API_KEY"):
        print("[err] AI_API_KEY 未设置；--no-ai 或先 export env", file=sys.stderr)
        sys.exit(2)

    out1 = "/tmp/查_方案1_pandoc+recover+plan7.normalized.html"
    out2 = "/tmp/查_方案2_direct+plan7.normalized.html"
    n1 = run_route_1(use_ai); open(out1, "w", encoding="utf-8").write(n1)
    print("WROTE", out1, len(n1), "chars", file=sys.stderr)
    n2 = run_route_2(use_ai); open(out2, "w", encoding="utf-8").write(n2)
    print("WROTE", out2, len(n2), "chars", file=sys.stderr)
