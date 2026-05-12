#!/usr/bin/env python3
"""
方案 A：AI 生成脚本（复用已有实现）。

这是最原始的方案：让 AI 读取 raw_outline.xml，生成一个 Python 规范化脚本，
然后执行该脚本。
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(__file__))
from ai_utils import call_ai, extract_code_block
from config import EPUB_TEXT_DIR


def generate_outline(raw_html_path: str) -> str:
    """从 raw HTML 生成结构视图 XML"""
    from bs4 import BeautifulSoup
    from html.parser import HTMLParser
    import xml.etree.ElementTree as ET
    from xml.dom import minidom

    with open(raw_html_path, "r", encoding="utf-8") as f:
        html = f.read()

    class StructureExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.elements = []
            self.text_buffer = ""
            self.depth = 0
            self.in_body = False
            self.current_tag = None
            self.current_attrs = {}

        def handle_starttag(self, tag, attrs):
            if tag == "body":
                self.in_body = True
            if not self.in_body:
                return
            self.depth += 1
            self.current_tag = tag
            self.current_attrs = dict(attrs)
            self.text_buffer = ""

        def handle_endtag(self, tag):
            if tag == "body":
                self.in_body = False
            if not self.in_body:
                return
            if self.text_buffer.strip():
                if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                    text = self.text_buffer.strip()
                else:
                    text = self.text_buffer.strip()[:30]
                self.elements.append({
                    "tag": tag,
                    "attrs": self.current_attrs,
                    "text": text,
                })
            self.depth -= 1
            self.text_buffer = ""

        def handle_data(self, data):
            if self.in_body:
                self.text_buffer += data

    parser = StructureExtractor()
    parser.feed(html)

    root = ET.Element("raw_outline")
    body_el = ET.SubElement(root, "body")
    for elem in parser.elements:
        el = ET.SubElement(body_el, "element")
        el.set("tag", elem["tag"])
        for k, v in elem["attrs"].items():
            if k in ("id", "class", "title", "href"):
                el.set(k, v)
        if elem["text"]:
            el.set("text", elem["text"])

    return minidom.parseString(ET.tostring(root, encoding="unicode")).toprettyxml(indent="  ")


SYSTEM_PROMPT = """你是一个 HTML 规范化专家。你会收到一份书籍的 HTML 结构视图（标签树 + 属性 + 文本摘要）。
你的任务是生成一个 Python 脚本，把原始 HTML 规范化为标准结构。

标准 HTML 要求：
- main#book[data-type="book"]
- section#bodymatter[data-role="bodymatter"]
- 章节：section.chapter[data-type="chapter"][id]，标题为 h1
- 注释引用：a[data-role="noteref"][href][id]，href 指向 #note-N
- 注释正文：section[data-role="notes"] > div#note-N[data-role="note"]
- 前言放 section#frontmatter[data-role="frontmatter"]
- 后记放 section#backmatter[data-role="backmatter"]

脚本需要：
1. 使用 BeautifulSoup 解析原始 HTML
2. 重建结构
3. 从原始 EPUB (part0091.xhtml) 加载注释并注入
4. 输出 normalized HTML 和 structure.json

只输出 Python 代码，用 ```python 包裹。"""


def run(raw_html_path: str, output_html_path: str, output_structure_path: str) -> int:
    # Step 1: 生成结构视图
    outline = generate_outline(raw_html_path)

    # Step 2: 让 AI 生成脚本
    prompt = f"""以下是书籍《查拉图斯特拉如是说》的 HTML 结构视图。

注释信息：
- 注释在单独文件 part0091.xhtml 中
- 格式：<aside id="rearnote_N" epub:type="rearnote">...<p>注释内容</p></aside>
- 正文引用格式：<a class="noteref" href="#part0091.xhtml_rearnote_N">[N]</a>
- 注释文件路径：{EPUB_TEXT_DIR}/part0091.xhtml

输出文件：
- 规范化 HTML：{output_html_path}
- 结构 JSON：{output_structure_path}

结构视图：
{outline[:15000]}
"""

    response, tokens = call_ai(prompt, SYSTEM_PROMPT, max_tokens=8000)
    script_code = extract_code_block(response, "python")

    # Step 3: 执行 AI 生成的脚本
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script_code)
        script_path = f.name

    try:
        result = subprocess.run(
            ["python3", script_path],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=os.path.dirname(output_html_path),
        )
        if result.returncode != 0:
            raise RuntimeError(f"Script failed: {result.stderr[:500]}")
    finally:
        os.unlink(script_path)

    return tokens
