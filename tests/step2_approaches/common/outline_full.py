#!/usr/bin/env python3
"""Build the full tag-tree outline (维度二·形式 1).

Produces a flat XML view of the raw HTML body where:
  - non-heading nodes are truncated to 30 char text previews
  - h1..h6 retain full text
  - only `id`, `class`, `href` attributes are kept
  - base64 data URIs are dropped from attribute values

Mirrors the spirit of `src/pipeline/helpers.rs::build_raw_outline` but
re-implemented in Python so the experiment is self-contained.
"""
from __future__ import annotations

from io import StringIO

from bs4 import BeautifulSoup, NavigableString, Tag

KEEP_ATTRS = ("id", "class", "href")
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
PREVIEW_LEN = 30


def build_full_outline(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    body = soup.find("body")
    if body is None:
        raise ValueError("raw HTML has no <body>")

    out = StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n<outline>\n')
    for child in body.children:
        if isinstance(child, Tag):
            _render_node(child, depth=1, out=out)
    out.write("</outline>\n")
    return out.getvalue()


def _render_node(node: Tag, depth: int, out: StringIO) -> None:
    indent = "  " * depth
    tag = node.name
    attrs = _render_attrs(node)
    child_tags = [c for c in node.children if isinstance(c, Tag)]

    if not child_tags:
        text = _node_text(node)
        if tag in HEADING_TAGS:
            preview = _compact(text)
        else:
            preview = _compact(text)[:PREVIEW_LEN]
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
    chunks = []
    for child in node.children:
        if isinstance(child, NavigableString):
            chunks.append(str(child))
    return "".join(chunks)


def _compact(text: str) -> str:
    return " ".join(text.split())


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _is_base64_data_uri(value: str) -> bool:
    stripped = value.lstrip()
    return stripped.startswith("data:") and ";base64," in stripped


if __name__ == "__main__":
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import RAW_HTML

    with open(RAW_HTML, "r", encoding="utf-8") as fh:
        outline = build_full_outline(fh.read())
    print(f"[outline_full] lines={outline.count(chr(10))} bytes={len(outline)}")
    print("\n".join(outline.splitlines()[:30]))
