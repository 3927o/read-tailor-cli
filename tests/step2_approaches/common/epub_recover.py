#!/usr/bin/env python3
"""Post-process step (runs AFTER pandoc step 1) that recovers information
pandoc silently drops when it flattens a multi-file EPUB into one HTML.

Two independent recoveries, both reading the original EPUB:

1. FILE TITLES  (inject_file_titles)
   Many EPUBs put a section's real name ONLY in that file's <title>, with
   just sub-section headings in the body. pandoc keeps each file's <body>
   and discards <title>, so the chapter name vanishes. We re-inject it as an
   <h1 data-source="epub-file-title"> at pandoc's file-boundary anchor
   (<span id="chapterN.xhtml">), but only when it is genuinely missing.

2. DROPPED NOTES  (inject_dropped_notes)
   pandoc's HTML reader UNCONDITIONALLY suppresses any
   <aside epub:type="footnote"|"rearnote">: it only re-emits the content if a
   SAME-FILE noteref can promote it to a native footnote. When the noteref
   lives in another file (the common "rearnotes collected in one file"
   layout), the aside is removed and never re-attached — the note body is
   lost while the in-text ref survives as a dangling link.

   pandoc rewrites a cross-file ref "part0091.xhtml#rearnote_1" into the
   fragment "#part0091.xhtml_rearnote_1" (i.e. "{file}_{frag}"). We use that
   deterministic rule to map each EPUB note to the id its surviving ref now
   points at, and re-inject the note body — but ONLY for refs that are
   actually dangling, so notes pandoc DID promote are never duplicated.

   Only epub:type values "footnote" and "rearnote" are affected by this
   suppression bug (verified empirically: endnote / note / annotation /
   sidebar / etc. and plain <aside> all survive), so those are the only types
   we recover.

3. SURVIVING-ID REPAIR  (repair_surviving_ids)
   A cross-file reference can also break even though pandoc KEEPS the target's
   content, because pandoc only carries an id on AST nodes that have "Attr":
     - p / sup / sub / em / strong  -> id is DROPPED (content stays, id gone)
     - aside (non-suppressed) / table -> id KEPT but NOT file-prefixed, so the
       rewritten ref "#{file}_{id}" no longer matches the bare "{id}"
   (See pandoc_id_href_probe.py for the full, version-pinned taxonomy.)

   For each still-dangling ref we trace it back to the EPUB element pandoc
   kept and re-attach the prefixed id to that SURVIVING content element —
   never to an empty <span> anchor, because the downstream plan_7 content walk
   drops empty blocks (only its id, carried on real content, survives).
"""
from __future__ import annotations

import os
import posixpath
import re
import sys
import zipfile
from collections import Counter

from bs4 import BeautifulSoup, Tag

HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
# epub:type values pandoc suppresses (and therefore we may need to recover).
SUPPRESSED_NOTE_TYPES = {"footnote", "rearnote"}

_TAG_RE = re.compile(r"<[^>]*>")
_NOTE_BRACKET_RE = re.compile(r"〔[^〕]*〕|［[^］]*］|\[[^\]]*\]")


# --------------------------------------------------------------------------
# shared text helpers
# --------------------------------------------------------------------------


def _strip_tags(text: str) -> str:
    """Remove any literal HTML tags embedded in the text. Some EPUBs put
    escaped markup inside <title> (e.g. ``…日报<a class="zy">〔1〕</a>记者…``);
    after get_text() that markup is plain text and must be removed before we
    use the title as a visible heading."""
    return _TAG_RE.sub("", text or "")


def _norm(text: str) -> str:
    """Normalise for comparison: drop literal tags, note-reference brackets
    (〔1〕 / [1]) and asterisk note markers (实践论* -> 实践论), then collapse
    all whitespace (including the ideographic space U+3000)."""
    t = _strip_tags(text or "")
    t = _NOTE_BRACKET_RE.sub("", t)
    t = t.replace("*", "").replace("＊", "")
    return re.sub(r"\s+", "", t.replace("　", " ")).strip()


# --------------------------------------------------------------------------
# pandoc id-rewrite rule
# --------------------------------------------------------------------------


def _pandoc_fragment(containing_file: str, href: str) -> str:
    """Reproduce how pandoc rewrites an EPUB href into a flattened fragment.

    pandoc prefixes every id with its source file to avoid collisions when
    concatenating, so:
      "part0091.xhtml#rearnote_1"  (cross-file)  -> "#part0091.xhtml_rearnote_1"
      "#noteref_1"                 (same-file)   -> "#{containing_file}_noteref_1"
    Non-fragment hrefs (external links) are returned unchanged.
    """
    if not href:
        return href
    if href.startswith("#"):
        return f"#{containing_file}_{href[1:]}"
    if "#" in href:
        path, frag = href.split("#", 1)
        return f"#{posixpath.basename(path)}_{frag}"
    return href  # external / non-anchor link: leave alone


def _rewritten_id(containing_file: str, element_id: str) -> str:
    return f"{containing_file}_{element_id}"


# --------------------------------------------------------------------------
# EPUB reading
# --------------------------------------------------------------------------


def _iter_epub_docs(zf: zipfile.ZipFile):
    """Yield (basename, BeautifulSoup) for every (x)html document in the zip."""
    for name in zf.namelist():
        low = name.lower()
        if low.endswith((".xhtml", ".html", ".htm")):
            try:
                doc = BeautifulSoup(
                    zf.read(name).decode("utf-8", "replace"), "html.parser"
                )
            except Exception:
                continue
            yield posixpath.basename(name), doc


def _read_epub_spine(epub_path: str) -> list[dict]:
    """Spine as ordered dicts: {filename, title, heading_texts}."""
    out: list[dict] = []
    with zipfile.ZipFile(epub_path) as zf:
        names = zf.namelist()
        opf_name = next((n for n in names if n.lower().endswith(".opf")), None)
        if opf_name is None:
            return out
        opf = BeautifulSoup(zf.read(opf_name).decode("utf-8", "replace"), "xml")
        opf_dir = posixpath.dirname(opf_name)

        manifest: dict[str, str] = {}
        for item in opf.find_all("item"):
            iid, href = item.get("id"), item.get("href")
            if iid and href:
                manifest[iid] = href

        for itemref in opf.find_all("itemref"):
            href = manifest.get(itemref.get("idref"))
            if not href:
                continue
            full = (
                posixpath.normpath(posixpath.join(opf_dir, href))
                if opf_dir
                else href
            )
            if full not in names:
                base = posixpath.basename(href)
                full = next(
                    (n for n in names if posixpath.basename(n) == base), None
                )
                if full is None:
                    continue
            try:
                doc = BeautifulSoup(
                    zf.read(full).decode("utf-8", "replace"), "html.parser"
                )
            except Exception:
                continue
            title_node = doc.find("title")
            title = title_node.get_text(strip=True) if title_node else ""
            body = doc.find("body") or doc
            out.append(
                {
                    "filename": posixpath.basename(href),
                    "title": title,
                    "heading_texts": [
                        h.get_text(strip=True) for h in body.find_all(HEADINGS)
                    ],
                }
            )
    return out


def _read_epub_notes(epub_path: str) -> list[dict]:
    """Collect every suppressed-type note body in the EPUB.

    Returns dicts: {containing_file, orig_id, rewritten_id, element}, where
    element is the note body Tag (parsed from the EPUB)."""
    notes: list[dict] = []
    with zipfile.ZipFile(epub_path) as zf:
        for fname, doc in _iter_epub_docs(zf):
            for el in doc.find_all(
                lambda t: isinstance(t, Tag)
                and (t.get("epub:type") or "") in SUPPRESSED_NOTE_TYPES
            ):
                oid = el.get("id")
                if not oid:
                    continue
                notes.append(
                    {
                        "containing_file": fname,
                        "orig_id": oid,
                        "rewritten_id": _rewritten_id(fname, oid),
                        "element": el,
                    }
                )
    return notes


# --------------------------------------------------------------------------
# 1. file-title injection
# --------------------------------------------------------------------------


def _heading_matches_title(title_norm: str, heading_norm: str) -> bool:
    """True if a body heading already states the chapter title: an exact
    match, or the heading is the title plus a trailing suffix (note marker or
    parenthetical edition tag, e.g. 第六卷 -> 第六卷（静火版）).

    Only the heading-extends-title direction (prefix) is allowed, never an
    arbitrary substring — that dodges short-number coincidences such as a body
    heading '187' matching the '1878' inside a long title."""
    if not title_norm or len(title_norm) < 2 or not heading_norm:
        return False
    return heading_norm == title_norm or heading_norm.startswith(title_norm)


def _wants_title_injection(entry: dict) -> bool:
    title = _norm(entry["title"])
    if not title:
        return False
    if not entry["heading_texts"]:
        return False  # nothing to parent (cover / epigraph / colophon)
    for h in entry["heading_texts"]:
        if _heading_matches_title(title, _norm(h)):
            return False  # body already states the chapter name -> no dupe
    return True


def _find_anchor_for(body: Tag, filename: str) -> Tag | None:
    """Find pandoc's file-boundary anchor <span id="..."> for a spine file.
    pandoc names it after the file, sometimes with a doubled extension
    (Volume01.xhtml.xhtml), so match by exact id then by id-prefix."""
    span = body.find("span", id=filename)
    if span is not None:
        return span
    for sp in body.find_all("span", id=True):
        sid = sp.get("id") or ""
        if sid == filename or sid.startswith(filename):
            return sp
    return None


def inject_file_titles(
    epub_path: str, soup: BeautifulSoup
) -> list[dict]:
    """Inject recovered file titles into `soup` (mutated in place).
    Returns a log of {filename, title, action}."""
    body = soup.find("body")
    if body is None:
        return [{"action": "skipped:no-body"}]

    spine = _read_epub_spine(epub_path)
    # A <title> shared by more than one spine file is a global/book title (many
    # EPUBs stamp the book name into every file's <title>), never a per-file
    # chapter name. Injecting it would brand every chapter with the book title,
    # so only titles UNIQUE to their file are eligible.
    title_counts = Counter(_norm(e["title"]) for e in spine if _norm(e["title"]))

    log: list[dict] = []
    for entry in spine:
        fn, title = entry["filename"], entry["title"]
        if title_counts.get(_norm(title), 0) > 1:
            log.append({"filename": fn, "title": title, "action": "skipped:shared-title"})
            continue
        if not _wants_title_injection(entry):
            reason = (
                "no-title"
                if not _norm(title)
                else "no-headings"
                if not entry["heading_texts"]
                else "body-has-matching-heading"
            )
            log.append({"filename": fn, "title": title, "action": f"skipped:{reason}"})
            continue

        anchor = _find_anchor_for(body, fn)
        if anchor is None:
            log.append({"filename": fn, "title": title, "action": "skipped:no-anchor"})
            continue

        insert_point = anchor
        parent = anchor.parent
        if (
            parent is not None
            and parent.name in ("p", "div")
            and not parent.get_text(strip=True)
        ):
            insert_point = parent

        clean_title = re.sub(r"\s+", " ", _strip_tags(title)).strip()
        h1 = soup.new_tag("h1")
        h1["class"] = ["epub-file-title"]
        h1["data-source"] = "epub-file-title"
        h1.string = clean_title
        insert_point.insert_after(h1)
        log.append({"filename": fn, "title": clean_title, "action": "injected"})
    return log


# --------------------------------------------------------------------------
# 2. dropped-note injection
# --------------------------------------------------------------------------


def inject_dropped_notes(epub_path: str, soup: BeautifulSoup) -> list[dict]:
    """Re-inject note bodies pandoc dropped, into `soup` (mutated in place).
    Driven by DANGLING refs so that notes pandoc kept are never duplicated.
    Returns a log of {rewritten_id, action}."""
    body = soup.find("body")
    if body is None:
        return [{"action": "skipped:no-body"}]

    existing_ids = {el.get("id") for el in soup.find_all(id=True) if el.get("id")}
    dangling_targets: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if href.startswith("#") and href[1:] not in existing_ids:
            dangling_targets.add(href[1:])

    if not dangling_targets:
        return [{"action": "skipped:no-dangling-refs"}]

    notes = _read_epub_notes(epub_path)
    notes_by_id = {n["rewritten_id"]: n for n in notes}

    container: Tag | None = None
    log: list[dict] = []
    injected = 0
    for target in sorted(dangling_targets):
        note = notes_by_id.get(target)
        if note is None:
            log.append({"rewritten_id": target, "action": "skipped:no-epub-note"})
            continue
        if target in existing_ids:
            log.append({"rewritten_id": target, "action": "skipped:already-present"})
            continue

        if container is None:
            container = soup.new_tag("section", id="recovered-notes")
            container["data-source"] = "epub-dropped-notes"
            body.append(container)

        # Clone the EPUB note into this soup, rewrite its id and any internal
        # hrefs (back-links) to pandoc's flattened form so the bidirectional
        # noteref<->note link is restored.
        frag = BeautifulSoup(str(note["element"]), "html.parser")
        el = frag.find(True)
        if el is None:
            log.append({"rewritten_id": target, "action": "skipped:empty"})
            continue
        el["id"] = target
        cfile = note["containing_file"]
        for a in el.find_all("a", href=True):
            a["href"] = _pandoc_fragment(cfile, a.get("href") or "")
        container.append(el)
        existing_ids.add(target)
        injected += 1
        log.append({"rewritten_id": target, "action": "injected"})

    log.append({"action": "summary", "injected": injected, "dangling": len(dangling_targets)})
    return log


# --------------------------------------------------------------------------
# 3. surviving-id repair (content kept by pandoc, but its id was dropped or
#    left unprefixed, so a cross-file reference no longer resolves)
# --------------------------------------------------------------------------

_FILE_BOUNDARY_RE = re.compile(r"\.(?:xhtml|html|htm)$", re.I)


def _collapse_ws(text: str) -> str:
    """Remove ALL whitespace (including the ideographic space U+3000) so a
    target can be matched by text even after pandoc reflows / re-wraps it."""
    return re.sub(r"\s+", "", (text or "").replace("　", " "))


def _index_epub_ids(epub_path: str) -> dict[str, dict]:
    """Map pandoc's rewritten id ({basename}_{orig}) -> info for EVERY id-
    bearing element in the EPUB, so a dangling flattened ref can be traced
    back to the element pandoc kept (under a different / no id)."""
    idx: dict[str, dict] = {}
    with zipfile.ZipFile(epub_path) as zf:
        for fname, doc in _iter_epub_docs(zf):
            for el in doc.find_all(id=True):
                oid = el.get("id")
                if not oid:
                    continue
                key = _rewritten_id(fname, oid)
                if key in idx:
                    continue
                etype = el.get("epub:type") or ""
                suppressed = etype in SUPPRESSED_NOTE_TYPES
                idx[key] = {
                    "file": fname,
                    "orig_id": oid,
                    "tag": el.name,
                    "etype": etype,
                    "suppressed": suppressed,
                    # text is only needed to relocate id-dropped targets
                    "norm_text": ""
                    if suppressed
                    else _collapse_ws(el.get_text(" ", strip=True)),
                }
    return idx


def _is_boundary_span(tag: Tag) -> bool:
    """pandoc marks each source file's start with an empty
    <span id="that-file.xhtml">. Recognise it by a filename-like id and the
    absence of any text."""
    if tag.name != "span":
        return False
    sid = tag.get("id") or ""
    return bool(_FILE_BOUNDARY_RE.search(sid)) and not tag.get_text(strip=True)


def _region_window(
    tags: list[Tag], boundaries: list[tuple[int, str]], fileid: str
) -> list[Tag] | None:
    """The tags (document order) belonging to `fileid`: those between its
    file-boundary span and the next boundary. None if no boundary matches
    (so the caller can distinguish "not located" from "not present")."""
    bidx = None
    for i, sid in boundaries:
        if sid and (sid == fileid or sid.startswith(fileid) or fileid.startswith(sid)):
            bidx = i
            break
    if bidx is None:
        return None
    nxt = next((i for i, _ in boundaries if i > bidx), len(tags))
    return tags[bidx + 1 : nxt]


def _locate_surviving(
    window: list[Tag] | None, info: dict
) -> tuple[Tag | None, str]:
    """Within a file's region, find pandoc's surviving copy of the EPUB
    target — by its (unprefixed) original id if that survived (aside / table),
    else by exact text (p / sup / em … that lost their id)."""
    if window is None:
        return None, "no-region"
    orig = info["orig_id"]
    for e in window:
        if e.get("id") == orig:
            return e, "bare-id"
    want = info["norm_text"]
    if want:
        same_tag = [
            e
            for e in window
            if e.name == info["tag"]
            and _collapse_ws(e.get_text(" ", strip=True)) == want
        ]
        if same_tag:
            return same_tag[0], "text-tag"
        any_tag = [
            e for e in window if _collapse_ws(e.get_text(" ", strip=True)) == want
        ]
        if any_tag:
            return any_tag[0], "text"
    return None, "unlocatable"


def _apply_repair(
    soup: BeautifulSoup, el: Tag, target_id: str, refs: list[Tag]
) -> str:
    """Make `target_id` resolve to `el`. Prefer giving `el` the id directly
    (it survives plan_7's content walk, which drops empty anchors); only if
    `el` already carries a still-referenced id do we instead retarget the
    dangling refs onto that surviving id."""
    cur = el.get("id")
    if cur == target_id:
        return "already-present"
    if cur is None:
        el["id"] = target_id
        el["data-source"] = "epub-id-repair"
        return "repaired:set-id"
    referenced = any(
        (a.get("href") or "").strip() == f"#{cur}"
        for a in soup.find_all("a", href=True)
    )
    if not referenced:
        el["id"] = target_id
        el["data-source"] = "epub-id-repair"
        return "repaired:rename-id"
    for a in refs:
        a["href"] = f"#{cur}"
    return "repaired:retarget-refs"


def repair_surviving_ids(epub_path: str, soup: BeautifulSoup) -> list[dict]:
    """Repair dangling '#'-refs whose EPUB target pandoc KEPT (content present)
    but under a dropped or unprefixed id. Driven by dangling refs, runs AFTER
    note injection, and never touches suppressed-note targets.
    Returns a log of {target, tag, action}."""
    body = soup.find("body")
    if body is None:
        return [{"action": "skipped:no-body"}]

    existing_ids = {el.get("id") for el in soup.find_all(id=True) if el.get("id")}
    refs_by_target: dict[str, list[Tag]] = {}
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if href.startswith("#") and len(href) > 1 and href[1:] not in existing_ids:
            refs_by_target.setdefault(href[1:], []).append(a)
    if not refs_by_target:
        return [{"action": "skipped:no-dangling-refs"}]

    idx = _index_epub_ids(epub_path)
    tags = [e for e in body.descendants if isinstance(e, Tag)]
    boundaries = [(i, t.get("id")) for i, t in enumerate(tags) if _is_boundary_span(t)]

    log: list[dict] = []
    repaired = 0
    for target in sorted(refs_by_target):
        info = idx.get(target)
        if info is None:
            log.append({"target": target, "action": "skipped:no-epub-target"})
            continue
        if info["suppressed"]:
            log.append({"target": target, "action": "skipped:suppressed-note"})
            continue
        window = _region_window(tags, boundaries, info["file"])
        el, mode = _locate_surviving(window, info)
        if el is None:
            log.append(
                {"target": target, "tag": info["tag"], "action": f"skipped:{mode}"}
            )
            continue
        action = _apply_repair(soup, el, target, refs_by_target[target])
        if action.startswith("repaired"):
            existing_ids.add(target)
            repaired += 1
        log.append({"target": target, "tag": info["tag"], "action": action})

    log.append(
        {"action": "summary", "repaired": repaired, "dangling": len(refs_by_target)}
    )
    return log


# --------------------------------------------------------------------------
# combined entry point
# --------------------------------------------------------------------------


def recover(epub_path: str, raw_html: str) -> tuple[str, dict]:
    """Run all recoveries on raw HTML. Returns (augmented_html, log_dict)."""
    soup = BeautifulSoup(raw_html, "html.parser")
    title_log = inject_file_titles(epub_path, soup)
    note_log = inject_dropped_notes(epub_path, soup)
    repair_log = repair_surviving_ids(epub_path, soup)
    return str(soup), {"titles": title_log, "notes": note_log, "repairs": repair_log}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(
            "usage: epub_recover.py EPUB RAW_IN [RAW_OUT]\n"
            "  If RAW_OUT is omitted, RAW_IN is rewritten in place "
            "(a .bak copy is kept).",
            file=sys.stderr,
        )
        return 2
    epub_path, raw_in = argv[1], argv[2]
    raw_out = argv[3] if len(argv) == 4 else raw_in

    with open(raw_in, "r", encoding="utf-8") as fh:
        raw = fh.read()
    augmented, log = recover(epub_path, raw)

    if raw_out == raw_in:
        bak = raw_in + ".bak"
        if not os.path.exists(bak):
            with open(bak, "w", encoding="utf-8") as fh:
                fh.write(raw)
    with open(raw_out, "w", encoding="utf-8") as fh:
        fh.write(augmented)

    titles = [e for e in log["titles"] if e.get("action") == "injected"]
    print(f"[titles] injected {len(titles)} -> {raw_out}")
    for e in titles:
        print(f"  + <h1>{e['title']}</h1>  (from {e['filename']})")

    note_summary = next((e for e in log["notes"] if e.get("action") == "summary"), None)
    note_injected = [e for e in log["notes"] if e.get("action") == "injected"]
    if note_summary:
        print(
            f"[notes]  injected {note_summary['injected']} "
            f"(of {note_summary['dangling']} dangling refs)"
        )
    else:
        only = log["notes"][0].get("action") if log["notes"] else "n/a"
        print(f"[notes]  {only}")
    if note_injected:
        print(f"  e.g. {note_injected[0]['rewritten_id']} … (+{len(note_injected) - 1} more)")

    repair_summary = next((e for e in log["repairs"] if e.get("action") == "summary"), None)
    repaired = [
        e for e in log["repairs"] if str(e.get("action", "")).startswith("repaired")
    ]
    if repair_summary:
        print(
            f"[repair] fixed {repair_summary['repaired']} "
            f"(of {repair_summary['dangling']} dangling refs)"
        )
    else:
        only = log["repairs"][0].get("action") if log["repairs"] else "n/a"
        print(f"[repair] {only}")
    if repaired:
        e = repaired[0]
        print(
            f"  e.g. {e['target']} <- <{e.get('tag')}> ({e['action']})"
            f"  (+{len(repaired) - 1} more)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
