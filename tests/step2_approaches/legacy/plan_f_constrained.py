#!/usr/bin/env python3
"""
方案 F：约束层级（按 H2 分块）+ AI。

按 H2 把文档切块，每块独立让 AI 判断 skip 规则。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from ai_utils import call_ai
from config import EPUB_TEXT_DIR
from plan_d_heuristic import load_epub_notes, build_structure, clone_element

from bs4 import BeautifulSoup, Tag


SYSTEM_PROMPT = """分析这个部分的标题列表，判断哪些 h4 是真正的章节，哪些应该跳过。

输出 JSON：
```json
{"skip": ["1", "2", "3"], "keep": ["三段变化", "道德的讲座"]}
```

规则：
- 纯数字的 h4（如"1", "2"）通常是前言中的编号段落，放 skip
- 带有意义标题的 h4（如"三段变化"）是真正的章节，放 keep

只输出 JSON。"""


def run(raw_html_path: str, output_html_path: str, output_structure_path: str) -> int:
    with open(raw_html_path, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("body")
    epub_notes = load_epub_notes()

    # 找到所有 h2（部）
    h2_tags = body.find_all("h2")
    part_h2s = [h for h in h2_tags if any(c in h.get("class", []) for c in ("c03", "c04"))]

    total_tokens = 0
    skip_h4_texts = set()

    # 对每个部分，提取标题列表让 AI 判断
    for h2 in part_h2s:
        part_name = h2.get_text(strip=True)
        h4_texts = []
        collecting = False
        for child in body.children:
            if not isinstance(child, Tag):
                continue
            if child is h2:
                collecting = True
                continue
            if collecting and child.name == "h2":
                break
            if collecting and child.name == "h4":
                text = child.get_text(strip=True)
                cls = " ".join(child.get("class", []))
                h4_texts.append({"text": text, "class": cls})

        if not h4_texts:
            continue

        prompt = f"部分「{part_name}」的 h4 标题列表：\n{json.dumps(h4_texts, ensure_ascii=False)}"
        response, tokens = call_ai(prompt, SYSTEM_PROMPT, max_tokens=1000)
        total_tokens += tokens

        try:
            json_match = re.search(r"\{[^}]+\}", str(response), re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                for t in result.get("skip", []):
                    skip_h4_texts.add(t)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # --- 组装 HTML（和方案 D 相同，但用 AI 判断的 skip 集合辅助）---
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
            if "译者前言" in text:
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
            elif "译后记" in text:
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

            # 启发式判断 + AI 判断
            should_skip = False
            if "sigil_not_in_toc" in cls and re.match(r"^\d+$", clean or text):
                should_skip = True
            if clean in skip_h4_texts:
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

    return total_tokens
