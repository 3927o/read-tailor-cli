#!/usr/bin/env python3
"""Post-process step (runs AFTER pandoc step 1) that recovers per-file
chapter titles which pandoc drops when it flattens a multi-file EPUB into
one HTML.

The problem
-----------
Many EPUBs put a section's real title ONLY in that file's ``<title>``
element, while the file body carries just the sub-section headings. Example
(悲剧的诞生 / chapter4.xhtml):

    <title>哲人尼采剪影</title>            <- the real chapter name
    ...
    <body>
      <h2>一忧郁的小诗人</h2>             <- only sub-sections in the body
      <h2>二“我终究是个老音乐家”</h2>
      ...

When pandoc concatenates files it keeps only each file's <body> and discards
<title>, so the chapter name "哲人尼采剪影" is gone from the raw HTML. Any
downstream consumer then sees a flat run of identically-classed <h2> and
cannot reconstruct the chapter grouping.

The fix
-------
pandoc DOES leave a file-boundary anchor in the flattened output:

    <p><span id="chapter4.xhtml"></span></p>   <- marks where chapter4 began
    <h2>一忧郁的小诗人</h2>
    ...

We read the original EPUB to recover ``file -> <title>`` and, for each
boundary anchor, inject an ``<h1 data-source="epub-file-title">`` carrying the
recovered title — but ONLY when it is actually missing, to avoid duplicating
titles in books that already repeat the name in the body.

Inject decision (per spine file)
---------------------------------
Inject the file's title as an <h1> at its boundary IFF ALL hold:
  1. the file has >= 1 heading in its body (otherwise there is nothing to
     parent — covers / epigraphs / colophons are skipped), AND
  2. NONE of the file's body headings already matches the title (normalised
     substring either direction), AND
  3. a boundary anchor for the file exists in the raw HTML.

This keeps 毛泽东选集 untouched (its volume files already carry an
``<h1>第一卷</h1>`` matching the title) while fixing 悲剧的诞生.
"""
from __future__ import annotations

import os
import posixpath
import re
import sys
import zipfile

from bs4 import BeautifulSoup, Tag

HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]


_TAG_RE = re.compile(r"<[^>]*>")
_NOTE_BRACKET_RE = re.compile(r"〔[^〕]*〕|［[^］]*］|\[[^\]]*\]")


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


def _heading_matches_title(title_norm: str, heading_norm: str) -> bool:
    """True if a body heading already states the chapter title. Either an
    exact match, or the heading is the title plus a trailing suffix (a note
    marker or a parenthetical edition tag, e.g. 第六卷 -> 第六卷（静火版）).

    We only allow the heading-extends-title direction (prefix), never an
    arbitrary substring: that dodges short-number coincidences such as a
    body heading '187' matching the '1878' inside a long title."""
    if not title_norm or len(title_norm) < 2 or not heading_norm:
        return False
    return heading_norm == title_norm or heading_norm.startswith(title_norm)


def _read_epub_spine(epub_path: str) -> list[dict]:
    """Return the spine as an ordered list of dicts:
    {filename, title, heading_texts}. filename is the basename of the
    spine href (e.g. 'chapter4.xhtml')."""
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
            iid = item.get("id")
            href = item.get("href")
            if iid and href:
                manifest[iid] = href

        for itemref in opf.find_all("itemref"):
            idref = itemref.get("idref")
            href = manifest.get(idref)
            if not href:
                continue
            # Resolve href relative to the OPF location inside the zip.
            full = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href
            if full not in names:
                # Some EPUBs store paths case- or separator-differently; try a
                # basename match as a fallback.
                base = posixpath.basename(href)
                full = next((n for n in names if posixpath.basename(n) == base), None)
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
            heading_texts = [
                h.get_text(strip=True) for h in body.find_all(HEADINGS)
            ]
            out.append(
                {
                    "filename": posixpath.basename(href),
                    "title": title,
                    "heading_texts": heading_texts,
                }
            )
    return out


def _wants_injection(entry: dict) -> bool:
    title = _norm(entry["title"])
    if not title:
        return False
    headings = entry["heading_texts"]
    if not headings:
        # Nothing to parent (cover / epigraph / colophon). Skip.
        return False
    for h in headings:
        if _heading_matches_title(title, _norm(h)):
            # The body already states the chapter name. Skip to avoid dupes.
            return False
    return True


def _find_anchor_for(body: Tag, filename: str) -> Tag | None:
    """Find the file-boundary anchor <span id="..."> for a spine file.

    pandoc names the anchor after the source file (e.g. 'chapter4.xhtml'),
    but sometimes appends an extra extension ('Volume01.xhtml.xhtml'), so we
    match by exact id, then by 'id starts with the filename'."""
    span = body.find("span", id=filename)
    if span is not None:
        return span
    stem = filename
    for sp in body.find_all("span", id=True):
        sid = sp.get("id") or ""
        if sid == filename or sid.startswith(stem):
            return sp
    return None


def inject_file_titles(epub_path: str, raw_html: str) -> tuple[str, list[dict]]:
    """Return (augmented_html, log). log entries:
    {filename, title, action} where action is 'injected' or 'skipped:<reason>'.
    """
    spine = _read_epub_spine(epub_path)
    soup = BeautifulSoup(raw_html, "html.parser")
    body = soup.find("body")
    if body is None:
        return raw_html, [{"action": "skipped:no-body"}]

    log: list[dict] = []
    for entry in spine:
        fn = entry["filename"]
        title = entry["title"]
        if not _wants_injection(entry):
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
            log.append(
                {"filename": fn, "title": title, "action": "skipped:no-anchor"}
            )
            continue

        # The anchor usually sits inside an (empty) <p>; insert the heading
        # after that wrapper so it precedes the file's first real content.
        insert_point = anchor
        parent = anchor.parent
        if parent is not None and parent.name in ("p", "div") and not parent.get_text(
            strip=True
        ):
            insert_point = parent

        clean_title = re.sub(r"\s+", " ", _strip_tags(title)).strip()
        h1 = soup.new_tag("h1")
        h1["class"] = ["epub-file-title"]
        h1["data-source"] = "epub-file-title"
        h1.string = clean_title
        insert_point.insert_after(h1)
        log.append({"filename": fn, "title": clean_title, "action": "injected"})

    return str(soup), log


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(
            "usage: epub_title_inject.py EPUB RAW_IN [RAW_OUT]\n"
            "  If RAW_OUT is omitted, RAW_IN is rewritten in place "
            "(a .bak copy is kept).",
            file=sys.stderr,
        )
        return 2
    epub_path, raw_in = argv[1], argv[2]
    raw_out = argv[3] if len(argv) == 4 else raw_in

    with open(raw_in, "r", encoding="utf-8") as fh:
        raw = fh.read()
    augmented, log = inject_file_titles(epub_path, raw)

    if raw_out == raw_in:
        bak = raw_in + ".bak"
        if not os.path.exists(bak):
            with open(bak, "w", encoding="utf-8") as fh:
                fh.write(raw)

    with open(raw_out, "w", encoding="utf-8") as fh:
        fh.write(augmented)

    injected = [e for e in log if e["action"] == "injected"]
    print(f"injected {len(injected)} file title(s) -> {raw_out}")
    for e in injected:
        print(f"  + <h1>{e['title']}</h1>  (from {e['filename']})")
    skipped_with_title = [
        e for e in log
        if e["action"].startswith("skipped") and _norm(e.get("title", ""))
        and e["action"] != "skipped:no-headings"
    ]
    for e in skipped_with_title:
        print(f"  - {e['filename']}: {e['action']}  (title={e['title']!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
