#!/usr/bin/env python3
"""
方案 D：纯规则，不调 AI。

策略：
- 用 BeautifulSoup 直接解析 raw HTML
- 基于标签名和 class 属性做启发式判断
- h2 class="c03"/"c04" → 部分（part）
- h3 → 部内分组
- h4（非 sigil_not_in_toc 的纯数字标题）→ 独立章节
- 包含 rearnote 的链接 → noteref
- 注释从 EPUB 原始文件中提取
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from config import EPUB_TEXT_DIR

from bs4 import BeautifulSoup, NavigableString, Tag


def run(raw_html_path: str, output_html_path: str, output_structure_path: str) -> int:
    """返回 AI token 消耗（此方案为 0）"""
    with open(raw_html_path, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("body")
    if not body:
        raise ValueError("No <body> found")

    # --- 加载 EPUB 原始注释 ---
    epub_notes = load_epub_notes()

    # --- 重建文档结构 ---
    new_soup = BeautifulSoup(
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><meta charset="utf-8"/><title>查拉图斯特拉如是说</title></head>'
        "<body></body></html>",
        "html.parser",
    )
    main = new_soup.new_tag("main", id="book")
    main["data-type"] = "book"

    # Frontmatter
    frontmatter = new_soup.new_tag("section", id="frontmatter")
    frontmatter["data-role"] = "frontmatter"

    # Bodymatter
    bodymatter = new_soup.new_tag("section", id="bodymatter")
    bodymatter["data-role"] = "bodymatter"

    # Backmatter
    backmatter = new_soup.new_tag("section", id="backmatter")
    backmatter["data-role"] = "backmatter"

    # --- 遍历原始 body 的所有元素 ---
    current_part = None
    current_group = None
    current_chapter = None
    chapter_counter = 0
    in_bodymatter = False

    for elem in body.children:
        if not isinstance(elem, Tag):
            continue

        # 跳过 header, 图片等
        if elem.name in ("header",):
            continue
        if elem.name == "p" and elem.find("img") and not elem.get_text(strip=True):
            continue
        # 跳过空 span 锚点
        if elem.name == "p":
            span = elem.find("span", id=True)
            if span and not span.get_text(strip=True) and not span.find("a"):
                continue

        # 识别标题
        if elem.name == "h1":
            text = elem.get_text(strip=True)
            if "译者前言" in text:
                # 前言内容收集到 frontmatter
                preface = new_soup.new_tag("section", id="preface")
                preface["data-type"] = "section"
                h1 = new_soup.new_tag("h1")
                h1.string = "译者前言"
                preface.append(h1)
                # 收集后续内容直到下一个 h1
                collecting = True
                for sibling in elem.next_siblings:
                    if isinstance(sibling, Tag) and sibling.name == "h1":
                        break
                    if isinstance(sibling, Tag):
                        cloned = clone_element(new_soup, sibling)
                        if cloned:
                            preface.append(cloned)
                frontmatter.append(preface)
                continue
            elif "注释" in text:
                in_bodymatter = False
                continue
            elif "译后记" in text:
                postscript = new_soup.new_tag("section", id="postscript")
                postscript["data-type"] = "section"
                h1 = new_soup.new_tag("h1")
                h1.string = "译后记"
                postscript.append(h1)
                for sibling in elem.next_siblings:
                    if isinstance(sibling, Tag) and sibling.name == "h1":
                        break
                    if isinstance(sibling, Tag):
                        cloned = clone_element(new_soup, sibling)
                        if cloned:
                            postscript.append(cloned)
                backmatter.append(postscript)
                continue
            elif "查拉图斯特拉如是说" in text and not in_bodymatter:
                in_bodymatter = True
                continue
            else:
                # 跳过扉页等其他 h1
                continue

        if not in_bodymatter:
            continue

        # --- 正文内的结构处理 ---
        if elem.name == "h2":
            # 新的部分
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
            # 部内分组
            text = elem.get_text(strip=True)
            current_group = new_soup.new_tag("section", id=f"group-{text}")
            current_group["data-type"] = "section"
            h = new_soup.new_tag("h3")
            h.string = text
            current_group.append(h)
            if current_part:
                current_part.append(current_group)
            else:
                bodymatter.append(current_group)
            current_chapter = None

        elif elem.name == "h4":
            text = elem.get_text(strip=True)
            # 清理标题中的注释标记
            clean = re.sub(r"\[?\d+\]?", "", text).strip()
            # 跳过纯数字标题（前言中的段落编号）
            cls = elem.get("class", [])
            if "sigil_not_in_toc" in cls and re.match(r"^\d+$", clean):
                # 这是前言中的编号段落，不是独立章节
                # 把它作为内容附加到当前章节
                if current_chapter:
                    p = clone_element(new_soup, elem)
                    if p:
                        # 把 h4 降级为普通段落
                        p_tag = new_soup.new_tag("p")
                        p_tag["class"] = "section-number"
                        p_tag.string = clean
                        current_chapter.append(p_tag)
                continue

            chapter_counter += 1
            current_chapter = new_soup.new_tag("section", id=f"ch{chapter_counter}")
            current_chapter["data-type"] = "chapter"
            h = new_soup.new_tag("h1")
            h.string = clean if clean else text
            current_chapter.append(h)

            if current_group:
                current_group.append(current_chapter)
            elif current_part:
                current_part.append(current_chapter)
            else:
                bodymatter.append(current_chapter)

        elif elem.name == "h5":
            # 章内小节
            if current_chapter:
                sec = new_soup.new_tag("section")
                sec["data-type"] = "section"
                h = new_soup.new_tag("h2")
                h.string = elem.get_text(strip=True)
                sec.append(h)
                current_chapter.append(sec)
                current_group = sec  # 追踪当前小节
            elif current_part:
                # 没有 current_chapter 时直接附加到 part
                p = clone_element(new_soup, elem)
                if p:
                    current_part.append(p)

        else:
            # 普通内容
            cloned = clone_element(new_soup, elem, fix_noterefs=True)
            if cloned:
                if current_chapter:
                    if current_group and current_group.parent == current_chapter:
                        current_group.append(cloned)
                    else:
                        current_chapter.append(cloned)
                elif current_group:
                    current_group.append(cloned)
                elif current_part:
                    current_part.append(cloned)
                else:
                    bodymatter.append(cloned)

    # --- 注释区域 ---
    notes_section = new_soup.new_tag("section", id="book-notes")
    notes_section["data-role"] = "notes"

    # 从 EPUB 加载注释
    for num in sorted(epub_notes.keys(), key=lambda x: int(x)):
        note_div = new_soup.new_tag("div", id=f"note-{num}")
        note_div["data-role"] = "note"
        note_div["data-note-kind"] = "endnote"
        note_content = BeautifulSoup(epub_notes[num]["html"], "html.parser")
        note_div.append(note_content)
        notes_section.append(note_div)

    main.append(frontmatter)
    main.append(bodymatter)
    main.append(notes_section)
    main.append(backmatter)
    new_soup.body.append(main)

    # --- 保存 ---
    with open(output_html_path, "w", encoding="utf-8") as f:
        f.write(str(new_soup))

    # --- 生成 structure.json ---
    structure = build_structure(new_soup)
    with open(output_structure_path, "w", encoding="utf-8") as f:
        json.dump(structure, f, ensure_ascii=False, indent=2)

    return 0  # 无 AI 消耗


def clone_element(new_soup, elem, fix_noterefs=False):
    """将原始元素克隆到新文档中，修复 noteref 格式"""
    if isinstance(elem, NavigableString):
        return new_soup.new_string(str(elem))

    if not isinstance(elem, Tag):
        return None

    new_elem = new_soup.new_tag(elem.name)
    # 复制属性
    for k, v in elem.attrs.items():
        if k in ("id", "class", "style", "title", "href", "src", "epub:type"):
            new_elem[k] = v

    # 处理子元素
    for child in elem.children:
        if isinstance(child, Tag):
            child_clone = clone_element(new_soup, child, fix_noterefs=fix_noterefs)
            if child_clone:
                new_elem.append(child_clone)
        else:
            text = str(child)
            if text.strip():
                new_elem.append(new_soup.new_string(text))

    # 修复 noteref 链接
    if fix_noterefs and elem.name == "a":
        href = elem.get("href", "")
        if "rearnote" in href:
            match = re.search(r"rearnote_(\d+)", href)
            if match:
                num = match.group(1)
                new_elem["data-role"] = "noteref"
                new_elem["href"] = f"#note-{num}"
                new_elem["id"] = f"noteref-{num}"
                new_elem.string = f"[{num}]"
                if "class" in new_elem.attrs:
                    del new_elem["class"]

    return new_elem


def load_epub_notes():
    """从 EPUB 原始文件加载注释"""
    notes = {}
    notes_file = os.path.join(EPUB_TEXT_DIR, "part0091.xhtml")
    if not os.path.exists(notes_file):
        return notes

    with open(notes_file, "r", encoding="utf-8") as f:
        content = f.read()

    soup = BeautifulSoup(content, "html.parser")
    for aside in soup.find_all("aside"):
        note_id = aside.get("id", "")
        if note_id.startswith("rearnote_"):
            num = note_id.replace("rearnote_", "")
            p = aside.find("p")
            if p:
                for a in p.find_all("a"):
                    if a.get("epub:type") == "noteref" or "noteref" in a.get("class", []):
                        a.decompose()
                text = p.get_text(strip=True)
                text = re.sub(r"^\[\d+\]", "", text).strip()
                notes[num] = {"html": f"<p>{text}</p>", "text": text}
    return notes


def build_structure(soup):
    """从规范化后的 HTML 生成 structure.json"""
    chapters = []
    idx = 0
    for ch in soup.find_all("section", attrs={"data-type": "chapter"}):
        idx += 1
        h1 = ch.find("h1")
        title = h1.get_text(strip=True) if h1 else ""
        paragraphs = ch.find_all("p")
        noterefs = ch.find_all("a", attrs={"data-role": "noteref"})
        sections = ch.find_all("section", attrs={"data-type": "section"}, recursive=False)

        sec_list = []
        for i, sec in enumerate(sections):
            heading = sec.find(["h1", "h2", "h3", "h4", "h5", "h6"])
            sec_list.append({
                "id": sec.get("id", ""),
                "title": heading.get_text(strip=True) if heading else "",
                "heading_level": int(heading.name[1]) if heading else 2,
                "index": i + 1,
            })

        chapters.append({
            "id": ch.get("id", f"ch{idx}"),
            "index": idx,
            "title": title,
            "section_count": len(sections),
            "paragraph_count": len(paragraphs),
            "note_ref_count": len(noterefs),
            "unknown_block_count": 0,
            "sections": sec_list,
        })

    return {
        "version": "1.0",
        "document": {"title": "查拉图斯特拉如是说", "language": "zh-CN"},
        "landmarks": {
            "book_main_id": "book",
            "bodymatter_id": "bodymatter",
            "toc_id": "",
            "has_toc": False,
            "has_notes_section": True,
        },
        "chapters": chapters,
        "notes": {
            "note_ref_count": sum(c["note_ref_count"] for c in chapters),
            "notes_section_id": "book-notes",
        },
        "unknown_blocks": [],
        "stats": {
            "chapter_count": len(chapters),
            "section_count": sum(c["section_count"] for c in chapters),
            "paragraph_count": sum(c["paragraph_count"] for c in chapters),
        },
    }
