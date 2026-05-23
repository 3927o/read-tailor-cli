#!/usr/bin/env python3
"""Plan 7: program-first normalization.

Philosophy: the PROGRAM does everything mechanical and deterministic
(document skeleton, heading-tree nesting from per-heading levels, note
pairing via the bidirectional noteref<->note link invariant). The AI is
asked ONLY the semantic question it cannot decide mechanically: for each
heading (in document order) what is its logical LEVEL in the final table
of contents — 1 = a direct child of the book, 2 = nested under a level-1,
and so on. The AI returns pure levels (plus skip / merge); it never writes
CSS selectors, code, or content, and never names a level "part"/"chapter".

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
You assign a logical OUTLINE LEVEL to every heading of a book. A
deterministic program has already parsed the raw HTML and extracted the
headings in document order; for each it gives an index, its tag+class, and
its text. You decide the nesting; the program then builds the normalized
HTML and the table of contents from your levels.

Return ONE JSON object:

{
  "document": { "title": "string", "language": "BCP-47 code or null" },
  "headings": [
    { "i": <index, copied verbatim>, "level": <int >= 1> },
    { "i": <index>, "skip": true },
    { "i": <index>, "merge": true }
  ]
}

Meaning of level (think of the final collapsible TOC):
- level 1 = a DIRECT child of the book (top of the outline). Preface,
  introduction, each top-level division, postscript, bibliography, etc.
  are usually all level 1.
- level 2 = nested one step under a level-1 heading.
- level 3 = nested under a level-2 heading, and so on.
  A heading that reads as being INSIDE another (a chapter inside a part, a
  section inside a chapter) gets parent_level + 1.

Special actions instead of a level:
- "skip": true  -> drop this heading (e.g. one that merely repeats the
  whole-book title).
- "merge": true -> this heading is only the CONTINUATION of the
  IMMEDIATELY PRECEDING heading's title (a title split across two adjacent
  headings, e.g. "Chapter 1" then its name). Its text is merged into that
  previous heading; do not also give it a level.

Rules:
- Give a decision for EVERY index from 0 to N-1. Copy "i" verbatim.
- Decide nesting from reading logic + document order + the texts/classes.
  Same class OFTEN means same level, but NOT always: the same class can
  appear at different depths (e.g. a "preface" and a "chapter" sharing one
  class — the preface is level 1, a chapter sitting under a part is
  level 2). A concluding section (postscript / appendix / bibliography)
  returns to level 1 even if it shares a class with the chapters.
- A heading that, by position, encloses several following headings is
  their parent: give it a SMALLER level than the headings it encloses.
- Never let levels jump by more than 1 when going deeper (1 -> 2 -> 3,
  never 1 -> 3).
- Output ONLY the JSON object, no commentary.
"""


def _outline(body: Tag) -> tuple[list[dict], list[Tag]]:
    """Headings in document order: a compact list for the AI plus the
    parallel list of Tag objects (same order) for index alignment."""
    rows: list[dict] = []
    tags: list[Tag] = []
    for i, h in enumerate(body.find_all(HEADINGS)):
        cls = " ".join(h.get("class") or [])
        rows.append(
            {
                "i": i,
                "tag": h.name,
                "class": cls,
                "text": h.get_text(strip=True)[:60],
            }
        )
        tags.append(h)
    return rows, tags


def _user_prompt(rows: list[dict], title_guess: str) -> str:
    payload = json.dumps(
        {"title_guess": title_guess, "headings": rows},
        ensure_ascii=False,
        indent=2,
    )
    return (
        "Assign an outline level to every heading below (document order).\n\n"
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
        if not isinstance(target, Tag):
            continue

        if target.name == "a":
            # The note id sits on an anchor; the note body is the block that
            # anchor opens, and that anchor must point back to this ref.
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
            note_key = note_anchor.get("id") or ""
        else:
            # Pandoc-footnote layout: the note id sits on the note body block
            # itself (e.g. <li id="fn1">), which contains a back-anchor
            # pointing to this in-text ref's id.
            ref_id = a.get("id") or ""
            if not ref_id:
                continue
            note_block = target if target.name in BLOCK_TAGS else _block_ancestor(target)
            if note_block is None or note_block is _block_ancestor(a):
                continue
            if not any(
                (bk.get("href") or "").strip()[1:] == ref_id
                for bk in note_block.find_all("a", href=True)
            ):
                continue  # no back-link -> not a note body
            note_key = target.get("id") or ""

        if note_block is None or id(note_block) in seen:
            continue
        seen.add(id(note_block))
        note_blocks.append(note_block)
        note_anchor_id_by_block[id(note_block)] = note_key

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


def build_normalized(raw_html: str, decisions: dict[int, dict], doc_meta: dict) -> str:
    src = BeautifulSoup(raw_html, "html.parser")
    body = src.find("body")
    if body is None:
        raise RuntimeError("raw HTML has no <body>")

    # Align decisions to headings by document-order index (same enumeration
    # the prompt used).
    head_index: dict[int, int] = {
        id(h): i for i, h in enumerate(body.find_all(HEADINGS))
    }

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

    # Detach note bodies from the working tree so the content walk does not
    # also emit them inline (e.g. when they sit inside a shared
    # <section class="footnotes"> wrapper). They are re-attached in the notes
    # section below; detached nodes remain usable for cloning.
    for b in note_blocks:
        b.extract()

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

    bodymatter = out.new_tag("section", id="bodymatter")
    bodymatter.attrs["data-role"] = "bodymatter"
    main.append(bodymatter)

    stack: list[dict] = []  # {'level': ai_level, 'section': tag, 'heading': tag}
    chap_counter = [0]
    pending_prefix: list[str] = []
    pending_ids: list[str] = []  # referenced ids from merged-away headings

    def open_section(ai_level: int, source_heading: Tag) -> None:
        while stack and stack[-1]["level"] >= ai_level:
            stack.pop()
        depth = len(stack) + 1
        parent = stack[-1]["section"] if stack else bodymatter
        sec = out.new_tag("section")
        if depth == 1:
            chap_counter[0] += 1
            sec.attrs["class"] = "chapter"
            sec.attrs["data-type"] = "chapter"
            sec.attrs["id"] = f"ch-{chap_counter[0]:03d}"
            heading = out.new_tag("h1")
        else:
            sec.attrs["data-type"] = "section"
            heading = out.new_tag(f"h{min(depth, 6)}")
        # Preserve the heading's full content (text + inline media such as
        # <img>) rather than flattening to plain text — flattening dropped
        # images embedded inside chapter titles.
        if pending_prefix:
            heading.append(out.new_string(" ".join(pending_prefix).strip() + " "))
            pending_prefix.clear()
        for child in source_heading.children:
            if isinstance(child, NavigableString):
                heading.append(out.new_string(str(child)))
            elif isinstance(child, Tag):
                heading.append(_clone(out, child))
        # Carry every referenced id of this heading (and of any heading merged
        # into it) so TOC entries that point at a chapter heading — including
        # the split-title fragment that became a `merge` — still resolve. One
        # id rides on the heading; extras become empty anchor spans.
        ref_ids = list(pending_ids)
        pending_ids.clear()
        hid = source_heading.get("id")
        if hid and hid in referenced_ids and hid not in ref_ids:
            ref_ids.append(hid)
        if ref_ids:
            heading["id"] = ref_ids[0]
            for extra in ref_ids[1:]:
                anchor = out.new_tag("span")
                anchor["id"] = extra
                heading.append(anchor)
        sec.append(heading)
        parent.append(sec)
        stack.append({"level": ai_level, "section": sec, "heading": heading})

    def attach_content(node: Tag) -> None:
        target = stack[-1]["section"] if stack else bodymatter
        target.append(_clone(out, node))

    # Ids that some in-document link points at. An otherwise-empty block is
    # kept iff it (or a descendant) carries one of these ids — this preserves
    # pandoc's empty file-boundary <span id="partNN.xhtml"> markers that a TOC
    # links to, while still dropping decorative empty blocks nobody references.
    referenced_ids = {
        href[1:]
        for a in body.find_all("a", href=True)
        for href in [(a.get("href") or "").strip()]
        if href.startswith("#") and len(href) > 1
    }

    def _is_referenced_anchor(block: Tag) -> bool:
        if block.get("id") in referenced_ids:
            return True
        return any(d.get("id") in referenced_ids for d in block.find_all(id=True))

    def process(node: Tag) -> None:
        # Document-order walk that splits content at headings. Headings are
        # NOT always direct children of <body>: pandoc/calibre often wrap
        # each heading + its content in a <section>/<div>. So descend into
        # any element that encloses a heading, and treat heading-free
        # elements as atomic content blocks.
        for block in list(node.children):
            if not isinstance(block, Tag):
                continue
            if id(block) in in_note:
                continue
            if block.name == "hr":
                # Preserve semantic separators (used by collection-style books
                # to delimit essays). Drop the visual <hr>, emit a structural
                # marker so downstream consumers can still see the boundary.
                sep = out.new_tag("div")
                sep.attrs["data-role"] = "separator"
                target = stack[-1]["section"] if stack else bodymatter
                target.append(sep)
                continue
            if block.name in HEADINGS:
                idx = head_index.get(id(block))
                decision = decisions.get(idx, {}) if idx is not None else {}
                text = block.get_text(strip=True)
                if decision.get("skip"):
                    continue
                if decision.get("merge"):
                    pending_prefix.append(text)
                    mid = block.get("id")
                    if mid and mid in referenced_ids:
                        pending_ids.append(mid)
                    continue
                try:
                    level = max(1, int(decision.get("level") or 1))
                except (TypeError, ValueError):
                    level = 1
                open_section(level, block)
                continue
            if block.find(HEADINGS):
                process(block)
            elif (
                block.get_text(strip=True)
                or block.find(["img", "svg", "picture", "audio", "video"])
                or _is_referenced_anchor(block)
            ):
                attach_content(block)

    process(body)

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

    rows, _tags = _outline(body)
    title_node = src.find("title")
    title_guess = title_node.get_text(strip=True) if title_node else ""

    user = _user_prompt(rows, title_guess)
    response, tokens = call_ai(user, SYSTEM, max_tokens=32000)
    write_trace(
        output_html_path,
        f"## SYSTEM\n{SYSTEM}\n\n## USER\n{user}",
        response,
    )

    parsed = parse_ir_response(response)
    decisions: dict[int, dict] = {}
    for item in parsed.get("headings") or []:
        if isinstance(item, dict) and "i" in item:
            try:
                decisions[int(item["i"])] = item
            except (TypeError, ValueError):
                continue
    doc_meta = parsed.get("document") or {}
    if not doc_meta.get("title"):
        doc_meta["title"] = title_guess or "Untitled"

    normalized = build_normalized(raw, decisions, doc_meta)
    write_normalized(output_html_path, normalized)
    write_structure_json(output_html_path, output_structure_path)
    return tokens
