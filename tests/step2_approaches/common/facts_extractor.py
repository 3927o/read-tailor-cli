#!/usr/bin/env python3
"""Extract structural facts from raw HTML (维度二·形式 3).

Produces a JSON-serializable dict of *statistical structural facts* — never
specific titles or phrases — that an AI can use to make normalization
decisions without seeing the full DOM.

Constraints:
  - No DOM rewriting, no chapter-boundary judgment, no AI calls.
  - No book-specific feature values; only counts, prefixes, and patterns.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from bs4 import BeautifulSoup, Tag

HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
# 含拼音简写（zy=注引、zs=注释、hs=后注、bz=备注、jz/zhushi）和西文常见
NOTE_KEYWORDS = (
    "footnote", "endnote", "rearnote", "note", "fn", "zhu", "zhushi",
    "zy", "zs", "hs", "bz", "jz",
)
TOC_KEYWORDS = ("toc", "table-of-contents", "contents", "目录", "mulu")


def extract_facts(raw_html: str) -> dict[str, Any]:
    soup = BeautifulSoup(raw_html, "html.parser")
    body = soup.find("body")
    if body is None:
        raise ValueError("raw HTML has no <body>")

    headings = _collect_headings(body)
    return {
        "document_meta": _document_meta(soup),
        "tag_distribution": _tag_distribution(body),
        "body_children_profile": _body_children_profile(body),
        "heading_wrapper_profile": _heading_wrapper_profile(body),
        "heading_profile": _heading_profile(headings),
        "headings": headings,
        "in_document_link_summary": _in_document_link_summary(body),
        "noteref_patterns": _noteref_patterns(body),
        "note_container_candidates": _note_container_candidates(body),
        "toc_candidates": _toc_candidates(body),
        "class_distribution": _class_distribution(body, top_n=40),
        "id_prefix_distribution": _id_prefix_distribution(body, top_n=20),
    }


def _body_children_profile(body: Tag) -> dict[str, Any]:
    """body 直接子节点画像 —— 揭示 pandoc 的 wrapping 风格。

    比如毛选这本：body 直接子节点是 405 个 <section class="h3 div">，
    AI 不知道这点就会去 body.children 里找 h3，永远找不到。
    """
    direct: list[Tag] = [c for c in body.children if isinstance(c, Tag)]
    tag_counter: Counter[str] = Counter()
    section_class_counter: Counter[str] = Counter()  # 形如 "h3 div" 的组合
    section_inner_heading_counter: Counter[str] = Counter()  # section 里第一个 heading 的 tag
    for ch in direct:
        tag_counter[ch.name] += 1
        if ch.name in ("section", "div", "article"):
            classes = " ".join(ch.get("class") or [])
            section_class_counter[classes] += 1
            inner = ch.find(HEADING_TAGS)
            if inner is not None:
                section_inner_heading_counter[inner.name] += 1
    return {
        "total_direct_children": len(direct),
        "tag_top": tag_counter.most_common(10),
        "section_class_top": section_class_counter.most_common(10),
        "section_inner_heading_top": section_inner_heading_counter.most_common(10),
    }


def _heading_wrapper_profile(body: Tag) -> dict[str, Any]:
    """每个 heading 是被什么包着的 —— parent tag/class 分布，按 heading tag 分桶。

    AI 看到 'h3 的 parent 99% 是 <section class="h3 div">' 就知道
    section.h3 才是章节单元，不是孤立 h3。
    """
    profile: dict[str, dict[str, Any]] = {}
    for h in body.find_all(HEADING_TAGS):
        parent = h.parent if isinstance(h.parent, Tag) else None
        bucket = profile.setdefault(h.name, {
            "parent_tag_top": Counter(),
            "parent_class_top": Counter(),
            "is_first_child_count": 0,
            "total": 0,
        })
        bucket["total"] += 1
        if parent is not None:
            bucket["parent_tag_top"][parent.name] += 1
            for c in parent.get("class") or []:
                bucket["parent_class_top"][c] += 1
            first_tag_child = next(
                (cc for cc in parent.children if isinstance(cc, Tag)),
                None,
            )
            if first_tag_child is h:
                bucket["is_first_child_count"] += 1
    return {
        tag: {
            "total": b["total"],
            "parent_tag_top": b["parent_tag_top"].most_common(5),
            "parent_class_top": b["parent_class_top"].most_common(8),
            "is_first_child_count": b["is_first_child_count"],
        }
        for tag, b in profile.items()
    }


def _heading_profile(headings: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-tag aggregates: count, common class, common id prefix, sample texts.

    Helps the AI see 'h3 是主导章节级' without rebuilding a histogram itself.
    """
    by_tag: dict[str, list[dict[str, Any]]] = {}
    for h in headings:
        by_tag.setdefault(h["tag"], []).append(h)

    profile: dict[str, Any] = {}
    for tag, items in by_tag.items():
        class_counter: Counter[str] = Counter()
        id_prefix_counter: Counter[str] = Counter()
        text_lens: list[int] = []
        sample_texts: list[str] = []
        for h in items:
            for c in h["class"]:
                class_counter[c] += 1
            if h["id"]:
                p = _pluck_prefix(h["id"])
                if p:
                    id_prefix_counter[p] += 1
            text_lens.append(len(h["text"]))
            if len(sample_texts) < 6:
                sample_texts.append(h["text"][:40])
        profile[tag] = {
            "count": len(items),
            "top_classes": class_counter.most_common(5),
            "top_id_prefixes": id_prefix_counter.most_common(5),
            "avg_text_len": round(sum(text_lens) / len(text_lens), 1) if text_lens else 0,
            "sample_texts": sample_texts,
        }
    return profile


def _in_document_link_summary(body: Tag) -> dict[str, Any]:
    """对所有 in-document 链接（href='#xxx'）做 target 端画像。

    AI 看到 "5260 个锚点都指向 <span class='hl'>" 就能立刻判定 hl 是注释体类。
    这是以前 facts 完全缺失的信号 —— 只看锚点本身判不出注释，必须看它指向哪。
    """
    target_id_set: set[str] = set()
    target_to_anchor_count: Counter[str] = Counter()
    target_tag_counter: Counter[str] = Counter()
    target_class_counter: Counter[str] = Counter()
    target_parent_class_counter: Counter[str] = Counter()
    href_prefix_counter: Counter[str] = Counter()
    total_in_doc = 0
    unresolved = 0

    # 先建 id 索引一次，避免 O(n^2)
    id_index: dict[str, Tag] = {}
    for node in body.find_all(True):
        nid = node.get("id")
        if nid:
            id_index[nid] = node

    for anchor in body.find_all("a"):
        href = (anchor.get("href") or "").strip()
        if not href.startswith("#"):
            continue
        total_in_doc += 1
        target_id = href[1:]
        prefix = _pluck_prefix(target_id)
        if prefix:
            href_prefix_counter[prefix] += 1
        target_to_anchor_count[target_id] += 1
        target_id_set.add(target_id)
        node = id_index.get(target_id)
        if node is None:
            unresolved += 1
            continue
        target_tag_counter[node.name] += 1
        for c in node.get("class") or []:
            target_class_counter[c] += 1
        parent = node.parent if isinstance(node.parent, Tag) else None
        if parent is not None:
            for c in parent.get("class") or []:
                target_parent_class_counter[c] += 1

    return {
        "total_in_document_anchors": total_in_doc,
        "unique_targets": len(target_id_set),
        "unresolved_targets": unresolved,
        "href_prefix_top": href_prefix_counter.most_common(10),
        "target_tag_top": target_tag_counter.most_common(10),
        "target_class_top": target_class_counter.most_common(15),
        "target_parent_class_top": target_parent_class_counter.most_common(15),
        "anchors_per_target_distribution": _bucket_counter(target_to_anchor_count),
    }


def _bucket_counter(c: Counter[str]) -> dict[str, int]:
    """把 target → anchor 命中数分桶，用于判断"一对多"的注释模式。"""
    buckets = {"=1": 0, "=2": 0, "3-5": 0, "6-10": 0, ">10": 0}
    for count in c.values():
        if count == 1:
            buckets["=1"] += 1
        elif count == 2:
            buckets["=2"] += 1
        elif count <= 5:
            buckets["3-5"] += 1
        elif count <= 10:
            buckets["6-10"] += 1
        else:
            buckets[">10"] += 1
    return buckets


def _collect_headings(body: Tag) -> list[dict[str, Any]]:
    headings = []
    for index, node in enumerate(body.find_all(HEADING_TAGS)):
        text = node.get_text(strip=True)
        headings.append(
            {
                "tag": node.name,
                "text": text,
                "class": list(node.get("class", []) or []),
                "id": node.get("id", "") or "",
                "index": index,
            }
        )
    return headings


def _noteref_patterns(body: Tag) -> dict[str, Any]:
    href_prefixes: Counter[str] = Counter()
    class_hits: Counter[str] = Counter()
    epub_type_hits: Counter[str] = Counter()
    role_hits: Counter[str] = Counter()
    total_anchors = 0
    suspected_noterefs = 0

    for anchor in body.find_all("a"):
        total_anchors += 1
        href = (anchor.get("href") or "").strip()
        cls = anchor.get("class") or []
        epub_type = anchor.get("epub:type") or anchor.get("epub_type") or ""
        role = anchor.get("role") or anchor.get("data-role") or ""

        looks_like_note = False
        if href.startswith("#"):
            target = href[1:]
            prefix = _pluck_prefix(target)
            if prefix:
                href_prefixes[prefix] += 1
            if any(keyword in target.lower() for keyword in NOTE_KEYWORDS):
                looks_like_note = True
        for c in cls:
            cl = c.lower()
            if any(keyword in cl for keyword in NOTE_KEYWORDS):
                class_hits[c] += 1
                looks_like_note = True
        if "noteref" in epub_type.lower():
            epub_type_hits[epub_type] += 1
            looks_like_note = True
        if "noteref" in role.lower():
            role_hits[role] += 1
            looks_like_note = True
        if looks_like_note:
            suspected_noterefs += 1

    return {
        "total_anchors": total_anchors,
        "suspected_noterefs": suspected_noterefs,
        "href_prefix_top": href_prefixes.most_common(15),
        "class_hits": class_hits.most_common(15),
        "epub_type_hits": epub_type_hits.most_common(10),
        "role_hits": role_hits.most_common(10),
    }


def _note_container_candidates(body: Tag) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for tag_name in ("aside", "section", "ol", "ul", "div"):
        for node in body.find_all(tag_name):
            classes = " ".join(node.get("class") or []).lower()
            node_id = (node.get("id") or "").lower()
            epub_type = (node.get("epub:type") or node.get("epub_type") or "").lower()
            haystack = f"{classes} {node_id} {epub_type}"
            if any(keyword in haystack for keyword in NOTE_KEYWORDS):
                candidates.append(
                    {
                        "tag": tag_name,
                        "id": node.get("id", "") or "",
                        "class": list(node.get("class") or []),
                        "epub_type": node.get("epub:type", "") or node.get("epub_type", "") or "",
                        "child_count": sum(1 for _ in node.find_all(True, recursive=False)),
                    }
                )
            if len(candidates) >= 30:
                return candidates
    return candidates


def _toc_candidates(body: Tag) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for node in body.find_all(["nav", "section", "div", "ol", "ul"]):
        classes = " ".join(node.get("class") or []).lower()
        node_id = (node.get("id") or "").lower()
        epub_type = (node.get("epub:type") or node.get("epub_type") or "").lower()
        haystack = f"{classes} {node_id} {epub_type}"
        if any(keyword in haystack for keyword in TOC_KEYWORDS):
            candidates.append(
                {
                    "tag": node.name,
                    "id": node.get("id", "") or "",
                    "class": list(node.get("class") or []),
                    "link_count": len(node.find_all("a")),
                }
            )
        if len(candidates) >= 10:
            break
    return candidates


def _class_distribution(body: Tag, top_n: int) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for node in body.find_all(True):
        for cls in node.get("class") or []:
            counter[cls] += 1
    return counter.most_common(top_n)


def _id_prefix_distribution(body: Tag, top_n: int) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for node in body.find_all(True):
        node_id = node.get("id")
        if not node_id:
            continue
        prefix = _pluck_prefix(node_id)
        if prefix:
            counter[prefix] += 1
    return counter.most_common(top_n)


def _tag_distribution(body: Tag) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for node in body.find_all(True):
        counter[node.name] += 1
    return counter.most_common(40)


def _document_meta(soup: BeautifulSoup) -> dict[str, Any]:
    title_node = soup.find("title")
    title = title_node.get_text(strip=True) if title_node else ""
    html_node = soup.find("html")
    lang = ""
    if isinstance(html_node, Tag):
        lang = html_node.get("lang") or html_node.get("xml:lang") or ""
    return {"title": title, "language": lang}


_PREFIX_RE = re.compile(r"^([A-Za-z]+(?:[_-]?[A-Za-z]+)*)")


def _pluck_prefix(token: str) -> str:
    match = _PREFIX_RE.match(token)
    return match.group(1) if match else ""


if __name__ == "__main__":
    import json
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import RAW_HTML

    with open(RAW_HTML, "r", encoding="utf-8") as fh:
        facts = extract_facts(fh.read())
    print(
        f"[facts] headings={len(facts['headings'])}"
        f" toc_candidates={len(facts['toc_candidates'])}"
        f" note_containers={len(facts['note_container_candidates'])}"
    )
    print("noteref_patterns:", json.dumps(facts["noteref_patterns"], ensure_ascii=False))
    print("class_distribution[:10]:", facts["class_distribution"][:10])
