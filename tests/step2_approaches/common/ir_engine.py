#!/usr/bin/env python3
"""Fixed engine: IR (ir-1.0) + raw HTML -> normalized HTML.

Used by plan_4 / plan_5 / plan_6.

Strategy (intentionally simple, flat-linearization):
  1. Resolve all "anchor" nodes from the IR's CSS selectors against the
     source body: chapter titles, subsection titles, note containers /
     bodies, noterefs, unknown markers.
  2. Walk body.children in document order. Each direct child is a "block".
  3. Track which chapter (and subsection within it) each block belongs to:
     - A block whose subtree contains a chapter title element starts a
       new chapter section.
     - A block whose subtree contains a subsection title element opens a
       new sub-section inside the current chapter.
     - Blocks that wholly belong to a note container or unknown selector
       are diverted to the notes section / unknown wrapper.
     - Blocks before the first chapter are accumulated as
       <section data-role="frontmatter"> (preserved, never dropped).
  4. Heading levels are remapped: chapter titles -> h1, subsection titles
     -> h2/h3/h4 by `logical_level`. Other in-block headings keep their
     tags (engine does not rewrite arbitrary inline headings).
  5. Note refs matching `noterefs.selector` get rewritten to
     a[data-role="noteref"][href][id] and href patched to the new note id.
  6. Note bodies matching `notes.container_selector` + `item_selector`
     are extracted into a single <section data-role="notes">.
  7. <b>/<i> are remapped to <strong>/<em>.
  8. Anything that doesn't fit the IR is wrapped in
     <div data-role="unknown" data-reason="..."> rather than dropped.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def apply_ir(raw_html: str, ir: dict[str, Any]) -> str:
    src = BeautifulSoup(raw_html, "html.parser")
    body = src.find("body")
    if body is None:
        raise RuntimeError("raw HTML has no <body>")

    document_meta = ir.get("document") or {}
    title = (document_meta.get("title") or "").strip() or _fallback_title(src)
    language = document_meta.get("language") or _fallback_lang(src) or "und"

    # --- 1. Resolve anchors from the IR ---------------------------------
    chapters_ir = ir.get("chapters") or []
    if not chapters_ir:
        raise RuntimeError("IR has no chapters")

    chapter_title_to_meta: dict[int, dict[str, Any]] = {}
    chapter_titles: list[Tag] = []
    for chapter_meta in chapters_ir:
        selector = chapter_meta.get("title_selector") or ""
        if not selector:
            continue
        candidates = body.select(selector)
        if not candidates:
            continue
        title_node = candidates[0]
        chapter_title_to_meta[id(title_node)] = chapter_meta
        chapter_titles.append(title_node)
    if not chapter_titles:
        raise RuntimeError("IR resolved zero chapter titles in source")

    sub_title_to_meta: dict[int, dict[str, Any]] = {}
    for chapter_meta in chapters_ir:
        for sub in chapter_meta.get("subsections") or []:
            sel = sub.get("title_selector") or ""
            for matched in body.select(sel):
                sub_title_to_meta[id(matched)] = sub

    notes_policy = ir.get("notes") or {}
    note_body_nodes, original_note_ids = _collect_notes(body, notes_policy)
    in_note: set[int] = set()
    for nb in note_body_nodes:
        in_note.add(id(nb))
        for d in nb.descendants:
            in_note.add(id(d))

    # Also avoid emitting the notes container itself in the chapter flow.
    container_selector = notes_policy.get("container_selector") or ""
    notes_containers: list[Tag] = body.select(container_selector) if container_selector else []
    in_notes_container: set[int] = set()
    for c in notes_containers:
        in_notes_container.add(id(c))
        for d in c.descendants:
            in_notes_container.add(id(d))

    noterefs_policy = ir.get("noterefs") or {}
    noteref_id_map = _build_note_id_map(original_note_ids)
    _rewrite_noterefs(body, noterefs_policy, noteref_id_map)

    unknown_root_ids: set[int] = set()
    for entry in ir.get("unknowns") or []:
        sel = (entry or {}).get("selector") or ""
        if not sel:
            continue
        for u in body.select(sel):
            unknown_root_ids.add(id(u))

    # --- 2. Build output skeleton --------------------------------------
    out = BeautifulSoup("", "html.parser")
    html_tag = out.new_tag("html", lang=language)
    head = out.new_tag("head")
    meta_charset = out.new_tag("meta")
    meta_charset.attrs["charset"] = "utf-8"
    head.append(meta_charset)
    title_tag = out.new_tag("title")
    title_tag.string = title
    head.append(title_tag)
    out_body = out.new_tag("body")
    main = out.new_tag("main", id="book")
    main.attrs["data-type"] = "book"
    out_body.append(main)
    html_tag.append(head)
    html_tag.append(out_body)
    out.append(html_tag)

    bodymatter = out.new_tag("section", id="bodymatter")
    bodymatter.attrs["data-role"] = "bodymatter"
    main.append(bodymatter)

    # --- 3. Walk body children in document order -----------------------
    current_chapter: Tag | None = None
    current_chapter_id_index = 0
    current_subsection: Tag | None = None
    pre_chapter_blocks: list[Tag] = []

    for block in body.children:
        if not isinstance(block, Tag):
            continue
        if id(block) in in_notes_container:
            continue

        chapter_title_in_block = _find_descendant_in(block, chapter_title_to_meta)
        if chapter_title_in_block is not None:
            current_chapter_id_index += 1
            chapter_id = f"ch-{current_chapter_id_index:03d}"
            chapter_section = out.new_tag("section", **{"class": "chapter"})
            chapter_section.attrs["data-type"] = "chapter"
            chapter_section.attrs["id"] = chapter_id
            h1 = out.new_tag("h1")
            h1.string = chapter_title_in_block.get_text(strip=True)
            chapter_section.append(h1)
            bodymatter.append(chapter_section)
            current_chapter = chapter_section
            current_subsection = None
            # If the block IS the title element, we're done — title was
            # already emitted as the chapter h1. Otherwise, emit the rest
            # of the block's content excluding the title node.
            if block is chapter_title_in_block:
                continue
            _emit_block_minus(
                out,
                block,
                current_chapter,
                exclude_ids={id(chapter_title_in_block)},
                in_note=in_note,
                in_notes_container=in_notes_container,
                unknown_roots=unknown_root_ids,
                sub_title_to_meta=sub_title_to_meta,
                current_subsection_holder=(holder := [current_subsection]),
            )
            current_subsection = holder[0]
            continue

        if current_chapter is None:
            pre_chapter_blocks.append(block)
            continue

        sub_title_in_block = _find_descendant_in(block, sub_title_to_meta)
        if sub_title_in_block is not None:
            sub_meta = sub_title_to_meta[id(sub_title_in_block)]
            level = int(sub_meta.get("logical_level", 2))
            level = max(2, min(4, level))
            section = out.new_tag("section")
            section.attrs["data-type"] = "section"
            heading = out.new_tag(f"h{level}")
            heading.string = sub_title_in_block.get_text(strip=True)
            section.append(heading)
            current_chapter.append(section)
            current_subsection = section
            if block is sub_title_in_block:
                continue
            _emit_block_minus(
                out,
                block,
                current_subsection,
                exclude_ids={id(sub_title_in_block)},
                in_note=in_note,
                in_notes_container=in_notes_container,
                unknown_roots=unknown_root_ids,
                sub_title_to_meta=sub_title_to_meta,
                current_subsection_holder=(holder := [current_subsection]),
            )
            current_subsection = holder[0]
            continue

        target = current_subsection or current_chapter
        _emit_block(
            out,
            block,
            target,
            in_note=in_note,
            in_notes_container=in_notes_container,
            unknown_roots=unknown_root_ids,
        )

    # --- 4. Frontmatter (everything before the first chapter) ----------
    if pre_chapter_blocks:
        frontmatter = out.new_tag("section", id="frontmatter")
        frontmatter.attrs["data-role"] = "frontmatter"
        for block in pre_chapter_blocks:
            _emit_block(
                out,
                block,
                frontmatter,
                in_note=in_note,
                in_notes_container=in_notes_container,
                unknown_roots=unknown_root_ids,
            )
        # Place frontmatter before bodymatter
        bodymatter.insert_before(frontmatter)

    # --- 5. Notes section ---------------------------------------------
    if note_body_nodes:
        notes_section = out.new_tag("section", id="book-notes")
        notes_section.attrs["data-role"] = "notes"
        kind_hint = (notes_policy.get("kind_hint") or "").strip()
        for original_id, note_body in zip(original_note_ids, note_body_nodes):
            new_id = noteref_id_map.get(original_id) or _safe_id(original_id, prefix="note")
            div = out.new_tag("div")
            div.attrs["data-role"] = "note"
            div.attrs["id"] = new_id
            if kind_hint and kind_hint != "unknown":
                div.attrs["data-note-kind"] = kind_hint
            for child in note_body.children:
                if isinstance(child, NavigableString):
                    div.append(out.new_string(str(child)))
                elif isinstance(child, Tag):
                    div.append(_clone_subtree(out, child, in_note, in_notes_container, unknown_roots=set()))
            notes_section.append(div)
        main.append(notes_section)

    return str(out)


# ---------- emission helpers ----------------------------------------


def _emit_block(
    out: BeautifulSoup,
    block: Tag,
    target: Tag,
    in_note: set[int],
    in_notes_container: set[int],
    unknown_roots: set[int],
) -> None:
    if id(block) in in_note or id(block) in in_notes_container:
        return
    if id(block) in unknown_roots:
        wrapper = out.new_tag("div")
        wrapper.attrs["data-role"] = "unknown"
        wrapper.attrs["data-reason"] = "ir-marked"
        wrapper.append(_clone_subtree(out, block, in_note, in_notes_container, unknown_roots))
        target.append(wrapper)
        return
    cloned = _clone_subtree(out, block, in_note, in_notes_container, unknown_roots)
    target.append(cloned)


def _emit_block_minus(
    out: BeautifulSoup,
    block: Tag,
    target: Tag,
    exclude_ids: set[int],
    in_note: set[int],
    in_notes_container: set[int],
    unknown_roots: set[int],
    sub_title_to_meta: dict[int, dict[str, Any]],
    current_subsection_holder: list[Tag | None],
) -> None:
    """Emit children of `block` to `target`, skipping any node whose id is
    in `exclude_ids`. Used when the block contains the chapter/subsection
    title we already rendered above."""
    for child in block.children:
        if isinstance(child, NavigableString):
            text = str(child)
            if text.strip():
                target.append(out.new_string(text))
            continue
        if not isinstance(child, Tag):
            continue
        if id(child) in exclude_ids:
            continue
        if id(child) in in_note or id(child) in in_notes_container:
            continue
        # Subsection within the same block?
        sub_descendant = _find_descendant_in(child, sub_title_to_meta)
        if sub_descendant is not None:
            sub_meta = sub_title_to_meta[id(sub_descendant)]
            level = max(2, min(4, int(sub_meta.get("logical_level", 2))))
            section = out.new_tag("section")
            section.attrs["data-type"] = "section"
            heading = out.new_tag(f"h{level}")
            heading.string = sub_descendant.get_text(strip=True)
            section.append(heading)
            # find chapter via target ancestry
            chapter_ancestor = target
            while chapter_ancestor is not None and chapter_ancestor.get("data-type") != "chapter":
                chapter_ancestor = chapter_ancestor.parent
            (chapter_ancestor or target).append(section)
            current_subsection_holder[0] = section
            _emit_block_minus(
                out,
                child,
                section,
                exclude_ids={id(sub_descendant)},
                in_note=in_note,
                in_notes_container=in_notes_container,
                unknown_roots=unknown_roots,
                sub_title_to_meta=sub_title_to_meta,
                current_subsection_holder=current_subsection_holder,
            )
            continue
        if id(child) in unknown_roots:
            wrapper = out.new_tag("div")
            wrapper.attrs["data-role"] = "unknown"
            wrapper.attrs["data-reason"] = "ir-marked"
            wrapper.append(_clone_subtree(out, child, in_note, in_notes_container, unknown_roots))
            target.append(wrapper)
            continue
        cloned = _clone_subtree(out, child, in_note, in_notes_container, unknown_roots)
        actual_target = current_subsection_holder[0] or target
        actual_target.append(cloned)


def _clone_subtree(
    out: BeautifulSoup,
    node: Tag,
    in_note: set[int],
    in_notes_container: set[int],
    unknown_roots: set[int],
) -> Tag:
    name = node.name
    if name == "b":
        name = "strong"
    elif name == "i":
        name = "em"
    new_tag = out.new_tag(name)
    for key, value in node.attrs.items():
        if isinstance(value, list):
            new_tag.attrs[key] = list(value)
        else:
            new_tag.attrs[key] = value
    for child in node.children:
        if isinstance(child, NavigableString):
            new_tag.append(out.new_string(str(child)))
            continue
        if not isinstance(child, Tag):
            continue
        if id(child) in in_notes_container:
            continue
        new_tag.append(_clone_subtree(out, child, in_note, in_notes_container, unknown_roots))
    return new_tag


def _find_descendant_in(node: Tag, marker_map: dict[int, Any]) -> Tag | None:
    if id(node) in marker_map:
        return node
    for d in node.descendants:
        if isinstance(d, Tag) and id(d) in marker_map:
            return d
    return None


def _collect_notes(body: Tag, policy: dict[str, Any]) -> tuple[list[Tag], list[str]]:
    container_selector = (policy or {}).get("container_selector") or ""
    item_selector = (policy or {}).get("item_selector") or ""
    id_attr = (policy or {}).get("item_id_attr") or "id"
    if not container_selector or not item_selector:
        return [], []
    bodies: list[Tag] = []
    ids: list[str] = []
    for container in body.select(container_selector):
        for item in container.select(item_selector):
            bodies.append(item)
            ids.append(str(item.get(id_attr) or "").strip())
    return bodies, ids


def _build_note_id_map(original_note_ids: list[str]) -> dict[str, str]:
    id_map: dict[str, str] = {}
    counter = 1
    for original in original_note_ids:
        if not original:
            continue
        id_map[original] = f"note-{counter:04d}"
        counter += 1
    return id_map


def _rewrite_noterefs(
    body: Tag, policy: dict[str, Any], note_id_map: dict[str, str]
) -> int:
    selector = (policy or {}).get("selector") or ""
    href_pattern = (policy or {}).get("href_pattern") or ""
    if not selector or not href_pattern or "{n}" not in href_pattern:
        return 0
    # Pattern is used only to filter which <a> tags qualify as noterefs;
    # actual note resolution is done by direct href-target lookup against
    # the original note ids.
    pattern = re.compile(
        "^" + re.escape(href_pattern).replace(r"\{n\}", r"[^\"#]+") + "$"
    )
    rewritten = 0
    counter = 1
    for node in body.select(selector):
        href = (node.get("href") or "").strip()
        if not href.startswith("#"):
            continue
        if not pattern.match(href):
            continue
        target_id = href[1:]
        new_id = note_id_map.get(target_id)
        if not new_id:
            continue
        node["data-role"] = "noteref"
        node["href"] = f"#{new_id}"
        if not node.get("id"):
            node["id"] = f"ref-{counter:05d}"
        counter += 1
        rewritten += 1
    return rewritten


def _safe_id(original: str, prefix: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", original or "")
    return f"{prefix}-{cleaned or 'x'}"


def _fallback_title(soup: BeautifulSoup) -> str:
    title_tag = soup.find("title")
    return title_tag.get_text(strip=True) if title_tag else "Untitled"


def _fallback_lang(soup: BeautifulSoup) -> str | None:
    html_tag = soup.find("html")
    if isinstance(html_tag, Tag):
        return html_tag.get("lang") or html_tag.get("xml:lang")
    return None
