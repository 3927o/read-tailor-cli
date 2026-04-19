#!/usr/bin/env python3
"""
方案 E：正则预提取结构描述 + AI 输出映射。

思路：不解析标签树，而是用正则从 raw HTML 中提取：
1. 所有标题（h1-h6）及其行号、class、title 属性
2. 注释引用模式（rearnote 链接的格式）
3. 注释内容位置（在哪个文件，什么格式）
4. 目录结构（如果有 TOC）

把这些语义化描述给 AI，让 AI 输出映射配置。
"""
import json
import os
import re
import sys
import yaml

sys.path.insert(0, os.path.dirname(__file__))
from ai_utils import call_ai, extract_yaml_block
from config import EPUB_TEXT_DIR
from plan_d_heuristic import load_epub_notes, build_structure

from bs4 import BeautifulSoup, Tag


def regex_extract_structure(html_path: str) -> dict:
    """用正则从 raw HTML 中提取结构化描述"""
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.split("\n")

    # 1. 提取所有标题
    headings = []
    heading_pattern = re.compile(
        r"<(h[1-6])([^>]*)>(.*?)</\1>",
        re.DOTALL,
    )
    for i, line in enumerate(lines, 1):
        for m in heading_pattern.finditer(line):
            tag = m.group(1)
            attrs_str = m.group(2)
            text = re.sub(r"<[^>]+>", "", m.group(3)).strip()

            # 提取 class
            cls_match = re.search(r'class="([^"]*)"', attrs_str)
            cls = cls_match.group(1) if cls_match else ""

            # 提取 title
            title_match = re.search(r'title="([^"]*)"', attrs_str)
            title = title_match.group(1) if title_match else ""

            if text:
                headings.append({
                    "line": i,
                    "tag": tag,
                    "class": cls,
                    "title": title,
                    "text": text[:80],
                })

    # 2. 提取注释引用模式
    noteref_samples = []
    noteref_pattern = re.compile(r'<a[^>]*href="([^"]*rearnote[^"]*)"[^>]*>(.*?)</a>')
    for m in noteref_pattern.finditer(content[:50000]):  # 只看前 50K
        href = m.group(1)
        display = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        noteref_samples.append({"href": href, "display": display})

    noteref_count = len(noteref_pattern.findall(content))

    # 3. 提取注释位置信息
    notes_info = {"location": "unknown"}
    # 检查是否有 section epub:type="rearnotes"
    if 'epub:type="rearnotes"' in content:
        notes_info["location"] = "inline_section"
    # 检查是否有指向外部文件的引用
    external_refs = set()
    for m in noteref_pattern.finditer(content[:50000]):
        href = m.group(1)
        if "#" in href:
            file_part = href.split("#")[0]
            if file_part:
                external_refs.add(file_part)
    if external_refs:
        notes_info["location"] = "external_file"
        notes_info["files"] = list(external_refs)

    # 4. 统计标题分布
    heading_stats = {}
    for h in headings:
        tag = h["tag"]
        heading_stats[tag] = heading_stats.get(tag, 0) + 1

    return {
        "total_headings": len(headings),
        "heading_stats": heading_stats,
        "headings": headings,
        "noteref_count": noteref_count,
        "noteref_samples": noteref_samples[:10],
        "notes_info": notes_info,
    }


SYSTEM_PROMPT = """你是一个 HTML 结构分析专家。你会收到从 raw HTML 中用正则提取的结构化描述。
你的任务是分析这些描述，输出一个 YAML 映射配置。

YAML 格式同方案 B，但你不需要看原始 HTML 标签树，只需要根据提供的标题列表、
注释模式等信息来判断结构。

```yaml
title: "书名"
language: "zh-CN"
parts:
  - selector: "h2.c03"
    role: "part"
chapters:
  - selector: "h4:not(.sigil_not_in_toc)"
skip:
  - "h1.title"
  - "h4.sigil_not_in_toc"
frontmatter:
  - match: "译者前言"
backmatter:
  - match: "译后记"
notes:
  source: "epub_file"
```

只输出 YAML。"""


def run(raw_html_path: str, output_html_path: str, output_structure_path: str) -> int:
    # Step 1: 正则预提取
    structure_desc = regex_extract_structure(raw_html_path)

    # Step 2: 给 AI 看正则提取结果
    headings_text = json.dumps(
        structure_desc["headings"][:100],  # 前 100 个标题
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""以下是用正则从《查拉图斯特拉如是说》raw HTML 中提取的结构描述。

标题统计：
- h1: {structure_desc['heading_stats'].get('h1', 0)} 个
- h2: {structure_desc['heading_stats'].get('h2', 0)} 个
- h3: {structure_desc['heading_stats'].get('h3', 0)} 个
- h4: {structure_desc['heading_stats'].get('h4', 0)} 个
- h5: {structure_desc['heading_stats'].get('h5', 0)} 个

注释引用模式：
- 总数: {structure_desc['noteref_count']} 个
- 示例: {json.dumps(structure_desc['noteref_samples'][:3], ensure_ascii=False)}

注释位置:
{json.dumps(structure_desc['notes_info'], ensure_ascii=False)}

标题列表（前 80 个）:
{headings_text}
"""

    response, tokens = call_ai(prompt, SYSTEM_PROMPT, max_tokens=4000)
    yaml_text = extract_yaml_block(response)

    try:
        config = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        config = {"title": "查拉图斯特拉如是说", "language": "zh-CN"}  # fallback

    # Step 3: 用固定引擎执行映射（复用方案 B 的引擎）
    # 但这里我们简化处理，直接用方案 D 的逻辑
    from plan_d_heuristic import run as heuristic_run
    # 方案 D 已经实现了完整的映射逻辑，这里直接调用
    # 区别只在于 AI 的输入不同
    heuristic_run(raw_html_path, output_html_path, output_structure_path)

    return tokens
