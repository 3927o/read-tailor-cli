#!/usr/bin/env python3
"""SPIKE: a minimal direct EPUB -> single working-HTML reader, as an
alternative Step-1 front-end to pandoc.

The whole point: we control id/href namespacing, so internal links, notes
and the TOC stay resolvable BY CONSTRUCTION — there is nothing to "recover"
afterwards, because nothing is broken in the first place. Contrast with the
pandoc route, where Step-1 drops titles, suppresses footnote/rearnote asides,
drops ids on p/sup/em, leaves aside/table ids unprefixed, and fails to rewrite
../ TOC links — all of which epub_recover then has to undo.

What this spike does:
  - read the OPF: spine order + metadata (title / language)
  - parse each spine XHTML, concatenate <body> content in spine order
  - namespace EVERY id to "{slug(path)}__{id}" and rewrite EVERY internal
    href (same-file #frag, relative file[#frag], ../dir paths, whole-file)
    to the matching namespaced id, with a fuzzy basename fallback for the
    doubled-extension source defect (TOC -> Volume01.xhtml vs file
    Volume01.xhtml.xhtml)
  - conditionally inject each file's <title> as an <h1> when the body does
    not already state it (same policy as epub_recover, for a fair compare)
  - keep <aside epub:type=...> notes exactly as-is (plan_7 pairs them via the
    bidirectional href invariant, which we leave intact)

NOT done (deliberately, it's a spike): image/resource embedding (img tags are
kept with their original src), EPUB2 NCX-only TOC, CSS.
"""
from __future__ import annotations

import posixpath
import re
import sys
import zipfile

from bs4 import BeautifulSoup, NavigableString, Tag

sys.path.insert(0, posixpath.dirname(posixpath.dirname(posixpath.abspath(__file__))))

from common.epub_recover import (  # noqa: E402
    HEADINGS,
    _heading_matches_title,
    _norm,
    _strip_tags,
)

_EXTERNAL_RE = re.compile(r"^(?:[a-z][a-z0-9+.\-]*:|//)", re.I)


def _slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")


# --------------------------------------------------------------------------
# OPF
# --------------------------------------------------------------------------


def _read_opf(zf: zipfile.ZipFile) -> dict:
    names = zf.namelist()
    opf_name = next((n for n in names if n.lower().endswith(".opf")), None)
    if opf_name is None:
        raise RuntimeError("no .opf in EPUB")
    opf = BeautifulSoup(zf.read(opf_name).decode("utf-8", "replace"), "xml")
    opf_dir = posixpath.dirname(opf_name)

    def _full(href: str) -> str:
        return posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href

    manifest: dict[str, str] = {}
    for item in opf.find_all("item"):
        iid, href = item.get("id"), item.get("href")
        if iid and href:
            manifest[iid] = _full(href)

    spine: list[str] = []
    for itemref in opf.find_all("itemref"):
        path = manifest.get(itemref.get("idref"))
        if path:
            spine.append(path)

    title_node = opf.find("title")  # dc:title (xml parser drops the dc: prefix)
    lang_node = opf.find("language")
    return {
        "spine": spine,
        "all_paths": set(manifest.values()),
        "title": title_node.get_text(strip=True) if title_node else "",
        "language": lang_node.get_text(strip=True) if lang_node else None,
    }


# --------------------------------------------------------------------------
# link resolution
# --------------------------------------------------------------------------


def _resolve_path(target: str, all_paths: set[str]) -> str | None:
    """Map a (normalized) link path to an actual EPUB path, tolerating the
    doubled-extension source defect via a basename/prefix fallback."""
    if target in all_paths:
        return target
    tb = posixpath.basename(target)
    for p in all_paths:
        if posixpath.basename(p) == tb:
            return p
    for p in all_paths:
        bp = posixpath.basename(p)
        if bp.startswith(tb) or tb.startswith(bp):
            return p
    return None


def _rewrite_href(
    href: str,
    this_path: str,
    all_paths: set[str],
    file_anchor: dict[str, str],
) -> str:
    href = (href or "").strip()
    if not href:
        return href
    if href.startswith("#"):
        return f"#{_slug(this_path)}__{href[1:]}"
    if _EXTERNAL_RE.match(href):
        return href  # http(s) / mailto / data / protocol-relative: leave alone
    path_part, sep, frag = href.partition("#")
    target = posixpath.normpath(
        posixpath.join(posixpath.dirname(this_path), path_part)
    )
    real = _resolve_path(target, all_paths)
    if real is None:
        return href  # unknown / external resource: leave alone
    if frag:
        return f"#{_slug(real)}__{frag}"
    return f"#{file_anchor.get(real, _slug(real) + '__FILE')}"


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------


def epub_to_html(epub_path: str) -> str:
    with zipfile.ZipFile(epub_path) as zf:
        meta = _read_opf(zf)
        spine = meta["spine"]
        all_paths = meta["all_paths"]

        # Pass 1: parse each spine doc, namespace ids, decide title injection,
        # and record each file's anchor id (the id whole-file links resolve to,
        # placed on REAL content so it survives plan_7's content walk).
        docs: list[tuple[str, Tag]] = []
        file_anchor: dict[str, str] = {}
        for path in spine:
            try:
                raw = zf.read(path).decode("utf-8", "replace")
            except KeyError:
                continue
            doc = BeautifulSoup(raw, "html.parser")
            body = doc.find("body") or doc
            slug = _slug(path)

            for el in body.find_all(id=True):
                el["id"] = f"{slug}__{el.get('id')}"

            title = (doc.find("title").get_text(strip=True) if doc.find("title") else "")
            headings = body.find_all(HEADINGS)
            heading_texts = [h.get_text(strip=True) for h in headings]
            tnorm = _norm(title)
            wants_title = bool(
                tnorm
                and heading_texts
                and not any(_heading_matches_title(tnorm, _norm(h)) for h in heading_texts)
            )

            anchor_id = f"{slug}__FILE"
            if wants_title:
                h1 = doc.new_tag("h1")
                h1["class"] = ["epub-file-title"]
                h1["data-source"] = "epub-file-title"
                h1["id"] = anchor_id
                h1.string = re.sub(r"\s+", " ", _strip_tags(title)).strip()
                body.insert(0, h1)
            elif headings:
                first = headings[0]
                if not first.get("id"):
                    first["id"] = anchor_id
                else:
                    anchor_id = first["id"]
            else:
                first_block = body.find(True)
                if first_block is not None and not first_block.get("id"):
                    first_block["id"] = anchor_id
                elif first_block is not None:
                    anchor_id = first_block["id"]
            file_anchor[path] = anchor_id
            docs.append((path, body))

        # Pass 2: rewrite every href now that all targets/anchors are known.
        for path, body in docs:
            for a in body.find_all("a", href=True):
                a["href"] = _rewrite_href(a["href"], path, all_paths, file_anchor)

    # Assemble one document.
    out = BeautifulSoup(
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title></title>"
        "</head><body></body></html>",
        "html.parser",
    )
    out.find("html")["lang"] = meta["language"] or "und"
    out.find("title").string = meta["title"] or "Untitled"
    obody = out.find("body")
    for path, body in docs:
        wrapper = out.new_tag("div")
        wrapper["data-epub-file"] = posixpath.basename(path)
        for child in list(body.children):
            if isinstance(child, (Tag, NavigableString)):
                wrapper.append(child)
        obody.append(wrapper)
    return str(out)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: epub_direct.py EPUB OUT_HTML", file=sys.stderr)
        return 2
    html = epub_to_html(argv[1])
    with open(argv[2], "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"wrote {len(html)} bytes -> {argv[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
