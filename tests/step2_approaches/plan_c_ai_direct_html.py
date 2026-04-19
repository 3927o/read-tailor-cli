#!/usr/bin/env python3
"""
方案 C：AI 直接输出结构 JSON + 固定引擎执行。

AI 看结构视图，输出具体的结构清单（parts/groups/chapters 列表）。
固定引擎根据清单组装 HTML。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from ai_utils import call_ai, extract_code_block
from config import EPUB_TEXT_DIR
from plan_d_heuristic import load_epub_notes, build_structure, clone_element

from bs4 import BeautifulSoup, Tag


SYSTEM_PROMPT = """你是一个 HTML 结构分析专家。分析书籍结构视图，输出 JSON 结构清单。

```json
{
  "title": "查拉图斯特拉如是说",
  "frontmatter_h1": "译者前言",
  "backmatter_h1": "译后记",
  "parts": ["第一部", "第二部", "第三部", "第四部"],
  "groups": ["查拉图斯特拉的前言", "查拉图斯特拉的说教"],
  "chapter_h4s": ["三段变化", "道德的讲座", "背后的world论者"],
  "skip_h4_patterns": ["纯数字的h4，如1,2,3..."]
}
```

规则：
- chapter_h4s 是真正的章节标题（h4 标签的 text）
- skip_h4_patterns 是应该跳过的 h4（前言中的编号段落）
- groups 是 h3 标签
- parts 是 h2 标签

只输出 JSON。"""


def run(raw_html_path: str, output_html_path: str, output_structure_path: str) -> int:
    from plan_a_ai_gen_script import generate_outline
    outline = generate_outline(raw_html_path)

    prompt = f"""分析《查拉图斯特拉如是说》的 HTML 结构。

要求：
- 有 4 部（第一部~第四部），每个 h2 是一个部分
- h3 是部内分组（如"查拉图斯特拉的前言"）
- h4 是章节（约 90 个），但 sigil_not_in_toc 的纯数字 h4 不是独立章节
- 列出所有真正的章节标题

结构视图：
{outline[:12000]}
"""

    response, tokens = call_ai(prompt, SYSTEM_PROMPT, max_tokens=4000)
    json_text = extract_code_block(response, "json")

    try:
        desc = json.loads(json_text)
    except json.JSONDecodeError:
        json_text = re.sub(r",\s*}", "}", json_text)
        json_text = re.sub(r",\s*]", "]", json_text)
        desc = json.loads(json_text)

    # --- 固定引擎（和方案 D 相同的逻辑）---
    with open(raw_html_path, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("body")
    epub_notes = load_epub_notes()

    new_soup = BeautifulSoup(
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><meta charset="utf-8"/><title>查拉图斯特拉如是说</title></head>'
        "<body></body></html>",
        "html.parser",
    )
    main = new_soup.new_tag("main", id="book")
    main["data-type"] = "book"

    frontmatter = new_soup.new_tag("section", id="frontmatter")
    frontmatter["data-role"] = "frontmatter"
    bodymatter = new_soup.new_tag("section", id="bodymatter")
    bodymatter["data-role"] = "bodymatter"
    backmatter = new_soup.new_tag("section", id="backmatter")
    backmatter["data-role"] = "backmatter"
    notes_section = new_soup.new_tag("section", id="book-notes")
    notes_section["data-role"] = "notes"

    fm_match = desc.get("frontmatter_h1", "译者前言")
    bm_match = desc.get("backmatter_h1", "译后记")
    # AI 列出的章节标题集合
    ai_chapters = set(desc.get("chapter_h4s", []))
    # AI 列出的跳过模式
    skip_patterns = set(desc.get("skip_h4_patterns", []))

    current_part = None
    current_group = None
    current_chapter = None
    chapter_counter = 0
    in_bodymatter = False

    for elem in list(body.children):
        if not isinstance(elem, Tag):
            continue
        if elem.name in ("header",):
            continue
        if elem.name == "p" and elem.find("img") and not elem.get_text(strip=True):
            continue
        if elem.name == "p":
            span = elem.find("span", id=True)
            if span and not span.get_text(strip=True) and not span.find("a"):
                continue

        if elem.name == "h1":
            text = elem.get_text(strip=True)
            if fm_match in text:
                preface = new_soup.new_tag("section", id="preface")
                preface["data-type"] = "section"
                h = new_soup.new_tag("h1")
                h.string = text
                preface.append(h)
                for sib in elem.next_siblings:
                    if isinstance(sib, Tag) and sib.name == "h1":
                        break
                    if isinstance(sib, Tag):
                        c = clone_element(new_soup, sib, fix_noterefs=True)
                        if c:
                            preface.append(c)
                frontmatter.append(preface)
                continue
            elif bm_match in text:
                postscript = new_soup.new_tag("section", id="postscript")
                postscript["data-type"] = "section"
                h = new_soup.new_tag("h1")
                h.string = text
                postscript.append(h)
                for sib in elem.next_siblings:
                    if isinstance(sib, Tag) and sib.name == "h1":
                        break
                    if isinstance(sib, Tag):
                        c = clone_element(new_soup, sib, fix_noterefs=True)
                        if c:
                            postscript.append(c)
                backmatter.append(postscript)
                continue
            elif "注释" in text:
                in_bodymatter = False
                continue
            elif "查拉图斯特拉" in text and not in_bodymatter:
                in_bodymatter = True
                continue
            else:
                continue

        if not in_bodymatter:
            continue

        if elem.name == "h2":
            text = elem.get_text(strip=True)
            current_part = new_soup.new_tag("section", id=f"part-{text}")
            current_part["data-type"] = "section"
            current_part["data-role"] = "part"
            h = new_soup.new_tag("h2")
            h.string = text
            current_part.append(h)
            bodymatter.append(current_part)
            current_group = None
            current_chapter = None

        elif elem.name == "h3":
            text = elem.get_text(strip=True)
            current_group = new_soup.new_tag("section", id=f"group-{text}")
            current_group["data-type"] = "section"
            h = new_soup.new_tag("h3")
            h.string = text
            current_group.append(h)
            (current_part or bodymatter).append(current_group)
            current_chapter = None

        elif elem.name == "h4":
            text = elem.get_text(strip=True)
            clean = re.sub(r"\[?\d+\]?", "", text).strip()
            cls = elem.get("class", [])

            # 判断是否跳过：启发式 + AI 判断
            should_skip = False
            if "sigil_not_in_toc" in cls and re.match(r"^\d+$", clean or text):
                should_skip = True
            if clean in skip_patterns:
                should_skip = True
            # 如果 AI 有章节列表，且当前标题不在列表中，也跳过
            if ai_chapters and clean not in ai_chapters and re.match(r"^\d+$", clean):
                should_skip = True

            if should_skip:
                if current_chapter:
                    p = new_soup.new_tag("p")
                    p.string = clean or text
                    current_chapter.append(p)
                continue

            chapter_counter += 1
            current_chapter = new_soup.new_tag("section", id=f"ch{chapter_counter}")
            current_chapter["data-type"] = "chapter"
            h = new_soup.new_tag("h1")
            h.string = clean or text
            current_chapter.append(h)
            (current_group or current_part or bodymatter).append(current_chapter)

        elif elem.name == "h5":
            if current_chapter:
                sec = new_soup.new_tag("section")
                sec["data-type"] = "section"
                h = new_soup.new_tag("h2")
                h.string = elem.get_text(strip=True)
                sec.append(h)
                current_chapter.append(sec)
                current_group = sec

        else:
            cloned = clone_element(new_soup, elem, fix_noterefs=True)
            if cloned:
                if current_chapter and current_group and current_group.parent == current_chapter:
                    current_group.append(cloned)
                elif current_chapter:
                    current_chapter.append(cloned)
                elif current_group:
                    current_group.append(cloned)
                elif current_part:
                    current_part.append(cloned)
                else:
                    bodymatter.append(cloned)

    for num in sorted(epub_notes.keys(), key=lambda x: int(x)):
        note_div = new_soup.new_tag("div", id=f"note-{num}")
        note_div["data-role"] = "note"
        note_div["data-note-kind"] = "endnote"
        note_div.append(BeautifulSoup(epub_notes[num]["html"], "html.parser"))
        notes_section.append(note_div)

    main.append(frontmatter)
    main.append(bodymatter)
    main.append(notes_section)
    main.append(backmatter)
    new_soup.body.append(main)

    with open(output_html_path, "w", encoding="utf-8") as f:
        f.write(str(new_soup))

    structure = build_structure(new_soup)
    with open(output_structure_path, "w", encoding="utf-8") as f:
        json.dump(structure, f, ensure_ascii=False, indent=2)

    return tokens
