#!/usr/bin/env python3
"""Build the trimmed tag-tree outline (维度二·形式 2).

Trimming rule (per `tests/step2_research_clarified.md` §3.2):
  - Within each `h1` region, keep only the FIRST `h2` (and its descendants).
  - All subsequent siblings of that first `h2` (including other `h2`s and
    their descendants) are dropped from the outline.
  - h-tag levels are NOT rewritten: trimming only removes nodes, never
    relabels them.

Trimming is applied to the same flat-XML representation produced by
`outline_full.build_full_outline`, by post-processing its source DOM.
"""
from __future__ import annotations

from io import StringIO
from typing import Iterable

from bs4 import BeautifulSoup, NavigableString, Tag

from .outline_full import (
    HEADING_TAGS,
    KEEP_ATTRS,
    PREVIEW_LEN,
    _is_base64_data_uri,
    _xml_escape,
)


def build_trimmed_outline(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    body = soup.find("body")
    if body is None:
        raise ValueError("raw HTML has no <body>")

    body_children = [c for c in body.children if isinstance(c, Tag)]
    keep = _trim_top_level(body_children)

    out = StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n<outline trimmed="true">\n')
    for child in keep:
        _render_node(child, depth=1, out=out)
    out.write("</outline>\n")
    return out.getvalue()


def _trim_top_level(children: list[Tag]) -> list[Tag]:
    """Within each h1-delimited region, keep only the first h2 and its
    following non-heading siblings until the next h2 / h1 boundary.

    Concretely:
      - Walk through top-level children in order.
      - Track current h1 region. When inside an h1 region:
        * On hitting the first h2: mark "in first h2 group", keep this h2
          and any following non-h2 siblings until the next h2 / h1.
        * On hitting subsequent h2s: drop them (and any non-heading content
          following them) until the next h1.
      - Outside any h1 region (e.g. before the first h1), keep everything.
      - h3..h6 follow the same rule indirectly: they are kept only if they
        appear within the kept window (before the first h2 or attached to
        the first h2).
    """
    kept: list[Tag] = []
    seen_h1 = False
    seen_first_h2_in_region = False
    after_second_plus_h2 = False

    for node in children:
        tag = node.name
        if tag == "h1":
            seen_h1 = True
            seen_first_h2_in_region = False
            after_second_plus_h2 = False
            kept.append(node)
            continue
        if not seen_h1:
            kept.append(node)
            continue
        if tag == "h2":
            if not seen_first_h2_in_region:
                seen_first_h2_in_region = True
                after_second_plus_h2 = False
                kept.append(node)
            else:
                after_second_plus_h2 = True
            continue
        if after_second_plus_h2:
            continue
        kept.append(node)

    return kept


def _render_node(node: Tag, depth: int, out: StringIO) -> None:
    indent = "  " * depth
    tag = node.name
    attrs = _render_attrs(node)
    child_tags = [c for c in node.children if isinstance(c, Tag)]

    if not child_tags:
        preview = _compact(_node_text(node))
        if tag not in HEADING_TAGS:
            preview = preview[:PREVIEW_LEN]
        if not preview:
            out.write(f"{indent}<{tag}{attrs} />\n")
        else:
            out.write(f"{indent}<{tag}{attrs}>{_xml_escape(preview)}</{tag}>\n")
        return

    out.write(f"{indent}<{tag}{attrs}>\n")
    for child in child_tags:
        _render_node(child, depth + 1, out)
    out.write(f"{indent}</{tag}>\n")


def _render_attrs(node: Tag) -> str:
    parts = []
    for key in KEEP_ATTRS:
        if key not in node.attrs:
            continue
        value = node.attrs[key]
        if isinstance(value, list):
            value = " ".join(value)
        if not isinstance(value, str):
            value = str(value)
        if _is_base64_data_uri(value):
            continue
        parts.append(f' {key}="{_xml_escape(value)}"')
    return "".join(parts)


def _node_text(node: Tag) -> str:
    chunks: list[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            chunks.append(str(child))
    return "".join(chunks)


def _compact(text: str) -> str:
    return " ".join(text.split())


if __name__ == "__main__":
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import RAW_HTML

    with open(RAW_HTML, "r", encoding="utf-8") as fh:
        outline = build_trimmed_outline(fh.read())
    print(f"[outline_trimmed] lines={outline.count(chr(10))} bytes={len(outline)}")
    print("\n".join(outline.splitlines()[:30]))
