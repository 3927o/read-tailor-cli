#!/usr/bin/env python3
"""跑查这本书的两种 normalize 方案，互相独立、共享 plan_7 下游。"""
import os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bs4 import BeautifulSoup
from common.epub_recover import recover
from common.epub_direct import epub_to_html as direct_epub_to_html
from plan_7_program_first import build_normalized

REPO = os.path.dirname(os.path.dirname(HERE))
EPUB = os.path.join(REPO, "查拉图斯特拉如是说（译文经典）.epub")
META = {"title": "查拉图斯特拉如是说（译文经典）", "language": "zh"}

def title_from(html):
    t = BeautifulSoup(html, "html.parser").find("title")
    return t.get_text(strip=True) if t else META["title"]

def run_route_1():
    print("[route 1] pandoc + epub_recover + plan_7", file=sys.stderr)
    t0 = time.time()
    raw = subprocess.run(
        ["pandoc", EPUB, "-f", "epub", "-t", "html", "-s", "-o", "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    print(f"  pandoc raw: {len(raw):>9} chars  ({time.time()-t0:.1f}s)", file=sys.stderr)
    augmented, info = recover(EPUB, raw)
    print(f"  recover  -> {len(augmented):>9} chars  notes recovered: {info.get('asides_recovered','?')}", file=sys.stderr)
    norm = build_normalized(augmented, {}, {"title": title_from(augmented), "language": "zh"})
    print(f"  plan_7   -> {len(norm):>9} chars  ({time.time()-t0:.1f}s total)", file=sys.stderr)
    return norm

def run_route_2():
    print("[route 2] epub_direct + plan_7 (no recover)", file=sys.stderr)
    t0 = time.time()
    raw = direct_epub_to_html(EPUB)
    print(f"  direct   -> {len(raw):>9} chars  ({time.time()-t0:.1f}s)", file=sys.stderr)
    norm = build_normalized(raw, {}, {"title": title_from(raw), "language": "zh"})
    print(f"  plan_7   -> {len(norm):>9} chars  ({time.time()-t0:.1f}s total)", file=sys.stderr)
    return norm

if __name__ == "__main__":
    out1 = "/tmp/查_方案1_pandoc+recover+plan7.normalized.html"
    out2 = "/tmp/查_方案2_direct+plan7.normalized.html"
    n1 = run_route_1();  open(out1,"w",encoding="utf-8").write(n1)
    print("WROTE",out1,len(n1),"chars",file=sys.stderr)
    n2 = run_route_2();  open(out2,"w",encoding="utf-8").write(n2)
    print("WROTE",out2,len(n2),"chars",file=sys.stderr)
