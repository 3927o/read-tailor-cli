#!/usr/bin/env python3
"""Plan 7: program-first normalization.

Philosophy: the PROGRAM does everything mechanical and deterministic
(document skeleton, heading-tree nesting with continuous level remapping,
note pairing via the bidirectional noteref<->note link invariant). The AI
is asked ONLY the small semantic question it cannot decide mechanically:
what role does each heading *signature* play — front-matter / back-matter /
body heading at logical level N / title-continuation / skip. The AI returns
a tiny JSON of labels; it never writes CSS selectors, code, or content.

Self-contained: does not import or modify the IR engine used by plans 4-6,
so the existing plans are unaffected.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bs4 import BeautifulSoup, NavigableString, Tag

from _shared import (
    parse_ir_response,
    read_raw,
    require_ai_env,
    write_normalized,
    write_structure_json,
    write_trace,
)
from ai_utils import call_ai

HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
BLOCK_TAGS = {"p", "div", "li", "section", "blockquote", "aside", "td", "figure"}

SYSTEM = """\
You are a book-structure SEMANTIC LABELER. A deterministic program has
already parsed the raw HTML and extracted every distinct heading
"signature" (tag + class) together with its occurrence count and a few
sample texts.

Your ONLY job is to assign a semantic role to each signature. You do NOT
write CSS selectors, code, ids, or content. Return ONE JSON object:

{
  "document": { "title": "string", "language": "BCP-47 code or null" },
  "labels": [
    { "signature": "<copied verbatim>", "role": "...", "level": <int> }
  ]
}

Allowed roles:
- "body"  : a real body-structure heading. MUST include "level":
            1 = top reading unit (becomes a chapter);
            2, 3, ... = nested sub-sections (deeper level = larger number).
- "front" : a front-matter heading (preface / foreword / intro before the
            main body).
- "back"  : a back-matter heading (bibliography / appendix / postscript).
- "merge" : this heading is a CONTINUATION of the immediately preceding
            heading's title and should be merged into it. Use for titles
            split across two adjacent headings (e.g. "Chapter 1" followed
            by the chapter name on a separate heading).
- "skip"  : drop this heading (e.g. a heading that merely repeats the
            whole-book title).

Rules:
- Decide "level" from the LOGICAL reading hierarchy implied by the counts
  and sample texts, NOT from the raw tag number. The chapter level is the
  top reading unit; it is frequently NOT the most common heading (the most
  common one is usually the deepest sub-section).
- Each signature includes "lines": the source line numbers (in document
  order) where its headings occur. Use them to infer RELATIVE level by
  containment: if one signature's occurrences BRACKET many occurrences of
  another (its line range spans across them), the bracketing one is a
  HIGHER level (smaller "level" number). For example, a "part" heading
  whose two occurrences enclose several chapter headings sits ABOVE those
  chapters and must get a SMALLER level number than them. Never give a
  bracketing heading a larger level number than the headings it encloses.
- Copy each "signature" string verbatim from the input.
- Output ONLY the JSON object, no commentary.
"""


def _signatures(body: Tag) -> list[dict]:
    out: list[dict] = []
    index: dict[str, dict] = {}
    for h in body.find_all(HEADINGS):
        cls = " ".join(h.get("class") or [])
        sig = f"{h.name}|{cls}"
        entry = index.get(sig)
        if entry is None:
            entry = {"signature": sig, "count": 0, "lines": [], "samples": []}
            index[sig] = entry
            out.append(entry)
        entry["count"] += 1
        if h.sourceline is not None:
            entry["lines"].append(h.sourceline)
        txt = h.get_text(strip=True)
        if txt and len(entry["samples"]) < 6:
            entry["samples"].append(txt[:40])
    return out


def _user_prompt(sigs: list[dict], title_guess: str) -> str:
    payload = json.dumps(
        {"title_guess": title_guess, "heading_signatures": sigs},
        ensure_ascii=False,
        indent=2,
    )
    return (
        "Label the following heading signatures extracted by the program.\n\n"
        f"{payload}\n"
    )


# ---------- deterministic note pairing (bidirectional link) ----------


def _block_ancestor(node: Tag) -> Tag | None:
    cur: Tag | None = node
    while isinstance(cur, Tag):
        if cur.name in BLOCK_TAGS:
            return cur
        cur = cur.parent
    return None


def _first_in_block(anchor: Tag, block: Tag | None) -> bool:
    """True if `anchor` is the first non-whitespace content in `block`
    (tolerating inline wrappers like <span> that produce no text)."""
    if block is None:
        return False
    for node in block.descendants:
        if node is anchor:
            return True
        if isinstance(node, NavigableString) and node.strip():
            return False
    return False


def detect_note_pairs(body: Tag) -> tuple[list[Tag], dict[int, str]]:
    """Find note bodies with zero AI help, using the footnote invariant:
    an in-text ref <a> points (href) to an element whose own anchor points
    back (href) to the ref's id. The note body is the block whose anchor is
    at the start of the block; the other side is the in-text reference.
    Targets are matched by exact id equality, so ids containing '#'/'.'
    (common in pandoc output) still resolve.
    """
    id_index: dict[str, Tag] = {}
    for el in body.find_all(id=True):
        key = el.get("id")
        if key and key not in id_index:
            id_index[key] = el

    note_blocks: list[Tag] = []
    note_anchor_id_by_block: dict[int, str] = {}
    seen: set[int] = set()

    for a in body.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href.startswith("#"):
            continue
        target = id_index.get(href[1:])
        if not isinstance(target, Tag) or target.name != "a":
            continue
        back = (target.get("href") or "").strip()
        if not back.startswith("#") or back[1:] != (a.get("id") or ""):
            continue  # require a genuine bidirectional link

        a_block = _block_ancestor(a)
        t_block = _block_ancestor(target)
        a_first = _first_in_block(a, a_block)
        t_first = _first_in_block(target, t_block)
        if a_first and not t_first:
            note_anchor, note_block = a, a_block
        else:
            note_anchor, note_block = target, t_block

        if note_block is None or id(note_block) in seen:
            continue
        seen.add(id(note_block))
        note_blocks.append(note_block)
        note_anchor_id_by_block[id(note_block)] = note_anchor.get("id") or ""

    return note_blocks, note_anchor_id_by_block


# ---------- deterministic tree building ----------


def _clone(out: BeautifulSoup, node: Tag) -> Tag:
    name = node.name
    if name == "b":
        name = "strong"
    elif name == "i":
        name = "em"
    new = out.new_tag(name)
    for key, value in node.attrs.items():
        new.attrs[key] = list(value) if isinstance(value, list) else value
    for child in node.children:
        if isinstance(child, NavigableString):
            new.append(out.new_string(str(child)))
        elif isinstance(child, Tag):
            new.append(_clone(out, child))
    return new


def build_normalized(raw_html: str, labels: list[dict], doc_meta: dict) -> str:
    src = BeautifulSoup(raw_html, "html.parser")
    body = src.find("body")
    if body is None:
        raise RuntimeError("raw HTML has no <body>")

    role_of: dict[str, str] = {}
    level_of: dict[str, int] = {}
    for item in labels or []:
        sig = item.get("signature")
        if not sig:
            continue
        role = item.get("role") or "body"
        role_of[sig] = role
        if role == "body":
            try:
                level_of[sig] = max(1, int(item.get("level") or 1))
            except (TypeError, ValueError):
                level_of[sig] = 1

    # --- notes (deterministic) ---
    note_blocks, note_anchor_id_by_block = detect_note_pairs(body)
    in_note: set[int] = set()
    for b in note_blocks:
        in_note.add(id(b))
        for d in b.descendants:
            in_note.add(id(d))

    note_id_by_block: dict[int, str] = {}
    new_id_by_anchor_id: dict[str, str] = {}
    for i, b in enumerate(note_blocks, start=1):
        nid = f"note-{i:04d}"
        note_id_by_block[id(b)] = nid
        aid = note_anchor_id_by_block.get(id(b)) or ""
        if aid:
            new_id_by_anchor_id[aid] = nid

    ref_counter = 1
    for a in body.find_all("a", href=True):
        if id(a) in in_note:
            continue
        href = (a.get("href") or "").strip()
        if not href.startswith("#"):
            continue
        new_id = new_id_by_anchor_id.get(href[1:])
        if not new_id:
            continue
        a["data-role"] = "noteref"
        a["href"] = f"#{new_id}"
        if not a.get("id"):
            a["id"] = f"ref-{ref_counter:05d}"
        ref_counter += 1

    # --- output skeleton ---
    out = BeautifulSoup("", "html.parser")
    html = out.new_tag("html", lang=(doc_meta.get("language") or "und"))
    head = out.new_tag("head")
    meta_charset = out.new_tag("meta")
    meta_charset.attrs["charset"] = "utf-8"
    head.append(meta_charset)
    title_tag = out.new_tag("title")
    title_tag.string = doc_meta.get("title") or "Untitled"
    head.append(title_tag)
    obody = out.new_tag("body")
    main = out.new_tag("main", id="book")
    main.attrs["data-type"] = "book"
    obody.append(main)
    html.append(head)
    html.append(obody)
    out.append(html)

    regions: dict[str, Tag] = {}

    def region_root(name: str) -> Tag:
        if name not in regions:
            if name == "body":
                sec = out.new_tag("section", id="bodymatter")
                sec.attrs["data-role"] = "bodymatter"
            elif name == "front":
                sec = out.new_tag("section", id="frontmatter")
                sec.attrs["data-role"] = "frontmatter"
            else:
                sec = out.new_tag("section", id="backmatter")
                sec.attrs["data-role"] = "backmatter"
            regions[name] = sec
            main.append(sec)
        return regions[name]

    region: str | None = None
    stack: list[dict] = []
    chap_counter = [0]
    pending_prefix: list[str] = []  # "merge" headings waiting to prefix the next title

    def open_section(ai_level: int, title_text: str) -> None:
        if pending_prefix:
            title_text = " ".join([*pending_prefix, title_text]).strip()
            pending_prefix.clear()
        while stack and stack[-1]["level"] >= ai_level:
            stack.pop()
        depth = len(stack) + 1
        parent = stack[-1]["section"] if stack else region_root(region or "front")
        sec = out.new_tag("section")
        if depth == 1 and region == "body":
            chap_counter[0] += 1
            sec.attrs["class"] = "chapter"
            sec.attrs["data-type"] = "chapter"
            sec.attrs["id"] = f"ch-{chap_counter[0]:03d}"
            heading = out.new_tag("h1")
        else:
            sec.attrs["data-type"] = "section"
            heading = out.new_tag(f"h{min(depth, 6)}")
        heading.string = title_text
        sec.append(heading)
        parent.append(sec)
        stack.append({"level": ai_level, "section": sec, "heading": heading})

    def attach_content(node: Tag) -> None:
        target = stack[-1]["section"] if stack else region_root(region or "front")
        target.append(_clone(out, node))

    for block in list(body.children):
        if not isinstance(block, Tag):
            continue
        if id(block) in in_note or block.name == "hr":
            continue
        if block.name in HEADINGS:
            cls = " ".join(block.get("class") or [])
            sig = f"{block.name}|{cls}"
            role = role_of.get(sig, "body")
            text = block.get_text(strip=True)
            if role == "skip":
                continue
            if role == "merge":
                pending_prefix.append(text)
                continue
            if role == "front":
                if region != "front":
                    region = "front"
                    stack.clear()
                open_section(1, text)
                continue
            if role == "back":
                if region != "back":
                    region = "back"
                    stack.clear()
                open_section(1, text)
                continue
            # role == "body"
            level = level_of.get(sig, int(block.name[1]))
            if level <= 1:
                if region != "body":
                    region = "body"
                    stack.clear()
                open_section(1, text)
            else:
                if region is None:
                    region = "front"
                open_section(level, text)
            continue
        # content block
        if region is None:
            region = "front"
        attach_content(block)

    # Ensure bodymatter exists and regions are ordered front, body, back.
    region_root("body")
    for name in ("front", "body", "back"):
        if name in regions:
            main.append(regions[name])

    if note_blocks:
        notes_sec = out.new_tag("section", id="book-notes")
        notes_sec.attrs["data-role"] = "notes"
        for b in note_blocks:
            div = out.new_tag("div")
            div.attrs["data-role"] = "note"
            div.attrs["id"] = note_id_by_block[id(b)]
            for child in b.children:
                if isinstance(child, NavigableString):
                    div.append(out.new_string(str(child)))
                elif isinstance(child, Tag):
                    div.append(_clone(out, child))
            notes_sec.append(div)
        main.append(notes_sec)

    return str(out)


def run(raw_html_path: str, output_html_path: str, output_structure_path: str) -> int:
    require_ai_env()
    raw = read_raw(raw_html_path)
    src = BeautifulSoup(raw, "html.parser")
    body = src.find("body")
    if body is None:
        raise RuntimeError("raw HTML has no <body>")

    sigs = _signatures(body)
    title_node = src.find("title")
    title_guess = title_node.get_text(strip=True) if title_node else ""

    user = _user_prompt(sigs, title_guess)
    response, tokens = call_ai(user, SYSTEM, max_tokens=8000)
    write_trace(
        output_html_path,
        f"## SYSTEM\n{SYSTEM}\n\n## USER\n{user}",
        response,
    )

    parsed = parse_ir_response(response)
    labels = parsed.get("labels") or []
    doc_meta = parsed.get("document") or {}
    if not doc_meta.get("title"):
        doc_meta["title"] = title_guess or "Untitled"

    normalized = build_normalized(raw, labels, doc_meta)
    write_normalized(output_html_path, normalized)
    write_structure_json(output_html_path, output_structure_path)
    return tokens
