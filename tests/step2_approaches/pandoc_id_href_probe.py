#!/usr/bin/env python3
"""Systematic probe of how pandoc rewrites ids / hrefs when converting an
EPUB to single-file HTML (the Step-1 conversion this repo relies on).

WHY THIS EXISTS
---------------
Our normalizer consumes pandoc's flattened HTML. Several "lost note / dead
link" bugs traced back to pandoc dropping or failing to rewrite ids and
hrefs. Rather than keep guessing, this script builds tiny synthetic EPUBs
that vary one dimension at a time, runs pandoc, and reports — by analyzing
the LINK GRAPH (not marker text, which pandoc transforms) — whether an
in-text reference still reaches its target.

Run it:  python3 pandoc_id_href_probe.py
It prints the local pandoc version followed by the result matrix.

VERIFIED FINDINGS  (pandoc 3.9 — the version this project ships against)
------------------------------------------------------------------------
Whether a cross-file `other.xhtml#frag` reference still resolves after
flattening depends entirely on the TARGET element's tag, because pandoc's
AST only carries ids ("Attr") on some node types:

  id kept AND file-prefixed ({file}_{id})  -> reference RESOLVES
      div, span, section, h1..h6, li, a
  id DROPPED (AST node has no Attr)         -> reference BREAKS
      p (Para), sup (Superscript), sub (Subscript), em (Emph),
      strong (Strong)            <-- note: <sup>/<em> markers lose their id
  id KEPT but NOT prefixed (raw passthrough)-> reference BREAKS (id mismatch)
      aside (non-suppressed), table
  element DROPPED entirely (suppressed)     -> reference BREAKS, body lost
      aside epub:type="footnote", aside epub:type="rearnote"
          ...UNLESS a SAME-FILE epub:type="noteref" promotes it to a native
          pandoc footnote (then it works perfectly, content moved into a
          <section class="footnotes">).

href rewriting:
  other.xhtml#frag   -> #other.xhtml_frag        (basename, '#'→'_')
  #frag (same file)  -> #thisfile.xhtml_frag     (own filename prefixed)
  other.xhtml        -> #other.xhtml             (jumps to the file-boundary
                                                  <span id="other.xhtml">;
                                                  good enough for a TOC)
  ../dir/other.xhtml -> NOT rewritten, left as a relative path -> dead link
                        in the single-file output (this is why 毛泽东选集's
                        目录 is broken: its TOC uses ../Text/… links)

id string formats: '.', '_', '-', ':' and leading digits all survive the
  prefixing; a UNICODE id is mishandled (pandoc emits a malformed double-'#'
  href like '#file.xhtml#中文').

CAVEATS / NOT COVERED
---------------------
- Only pandoc 3.9 (this project's pinned/installed version). Other majors
  (2.x) may differ.
- A few cells may be fixture-sensitive (the table case has no <thead>, the
  figure case no <img>).
- EPUB2-style notes (NCX / class-based / no epub:type) are not modelled.
- <div epub:type="footnote">, <dt>/<dd>, and cross-file raw-id collisions
  are not modelled.
Extend CASES below to cover more as needed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from bs4 import BeautifulSoup

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>
"""
OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">x</dc:identifier><dc:title>t</dc:title><dc:language>zh</dc:language></metadata>
<manifest>{manifest}</manifest><spine>{spine}</spine></package>
"""
XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{title}</title></head><body>
{body}
</body></html>
"""

# Markers. NOTE: pandoc may rewrite REFTEXT (e.g. a noteref becomes <sup>N</sup>),
# so analysis must NOT depend on finding these by text — only TGTBODY presence
# (which survives) and the link graph are used.
REF_MARK = "REFTEXT"
TGT_MARK = "TGTBODY"


def _etype(et: str | None) -> str:
    return f' epub:type="{et}"' if et else ""


def make_ref(ref_etype: str | None, href: str) -> str:
    return f'<p>正文 <a{_etype(ref_etype)} id="theref" href="{href}">{REF_MARK}</a> 后续。</p>'


def make_target(tag: str, etype: str | None, tid: str) -> str:
    et = _etype(etype)
    if tag in ("sup", "sub", "em", "strong"):
        return f'<p>前 <{tag}{et} id="{tid}">{TGT_MARK}</{tag}> 后</p>'
    if tag == "nested_span":
        return f'<p>前 <span{et} id="{tid}">{TGT_MARK}</span> 后</p>'
    if tag == "nested_p":
        return f'<div><p{et} id="{tid}">{TGT_MARK}</p></div>'
    if tag == "aside":
        return f'<aside{et} id="{tid}"><p>{TGT_MARK}</p></aside>'
    if tag == "a":
        return f'<p><a{et} id="{tid}" href="#backref">{TGT_MARK}</a></p>'
    if tag == "li":
        return f'<ol><li{et} id="{tid}">{TGT_MARK}</li></ol>'
    if tag == "section":
        return f'<section{et} id="{tid}"><p>{TGT_MARK}</p></section>'
    if tag == "table":
        return f'<table{et} id="{tid}"><tr><td>{TGT_MARK}</td></tr></table>'
    if tag == "figure":
        return f'<figure{et} id="{tid}"><figcaption>{TGT_MARK}</figcaption></figure>'
    return f'<{tag}{et} id="{tid}">{TGT_MARK}</{tag}>'


def build_epub(workdir: str, path: str, files: list[tuple[str, str]], spine: list[str]) -> None:
    d = os.path.join(workdir, "build")
    if os.path.exists(d):
        shutil.rmtree(d)
    os.makedirs(os.path.join(d, "META-INF"))
    os.makedirs(os.path.join(d, "OEBPS"))
    with open(os.path.join(d, "mimetype"), "w") as f:
        f.write("application/epub+zip")
    with open(os.path.join(d, "META-INF", "container.xml"), "w") as f:
        f.write(CONTAINER)
    fnames = [fn for fn, _ in files]
    manifest = []
    for i, (fn, body) in enumerate(files):
        full = os.path.join(d, "OEBPS", fn)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(XHTML.format(title=f"f{i}", body=body))
        manifest.append(f'<item id="i{i}" href="{fn}" media-type="application/xhtml+xml"/>')
    spine_xml = "".join(f'<itemref idref="i{fnames.index(s)}"/>' for s in spine)
    with open(os.path.join(d, "OEBPS", "content.opf"), "w", encoding="utf-8") as f:
        f.write(OPF.format(manifest="".join(manifest), spine=spine_xml))
    if os.path.exists(path):
        os.remove(path)
    subprocess.run(["zip", "-q", "-X", path, "mimetype"], cwd=d, check=True)
    subprocess.run(["zip", "-q", "-rX", path, ".", "-x", "mimetype"], cwd=d, check=True)


def _files_for(scope: str, target_tag, target_etype, ref_etype, tid, frag):
    """Return (files, spine) for a scope."""
    tgt = make_target(target_tag, target_etype, tid)
    if scope == "same":
        return ([("Text/body.xhtml", make_ref(ref_etype, f"#{frag}") + "\n" + tgt)],
                ["Text/body.xhtml"])
    if scope == "wholefile":
        return ([("Text/body.xhtml", make_ref(ref_etype, "tgt.xhtml")), ("Text/tgt.xhtml", tgt)],
                ["Text/body.xhtml", "Text/tgt.xhtml"])
    if scope == "wholefile_dotdot":
        return ([("Text/body.xhtml", make_ref(ref_etype, "../Text/tgt.xhtml")), ("Text/tgt.xhtml", tgt)],
                ["Text/body.xhtml", "Text/tgt.xhtml"])
    if scope == "crossdir":
        return ([("Text/body.xhtml", make_ref(ref_etype, f"../Sub/tgt.xhtml#{frag}")), ("Sub/tgt.xhtml", tgt)],
                ["Text/body.xhtml", "Sub/tgt.xhtml"])
    if scope == "cross_multi":
        ref = (make_ref(ref_etype, f"tgt.xhtml#{frag}")
               + f'<p>再引 <a id="theref2" href="tgt.xhtml#{frag}">REF2</a>。</p>')
        return ([("Text/body.xhtml", ref), ("Text/tgt.xhtml", tgt)],
                ["Text/body.xhtml", "Text/tgt.xhtml"])
    # default: cross
    return ([("Text/body.xhtml", make_ref(ref_etype, f"tgt.xhtml#{frag}")), ("Text/tgt.xhtml", tgt)],
            ["Text/body.xhtml", "Text/tgt.xhtml"])


def analyze(html: str) -> dict:
    """Judge by the link graph, which survives pandoc's transformations."""
    s = BeautifulSoup(html, "html.parser")
    tgt_present = TGT_MARK in html
    promoted = s.find("section", class_="footnotes") is not None
    id_map = {e.get("id"): e for e in s.find_all(id=True) if e.get("id")}

    def is_backlink(a):
        return "footnote-back" in (a.get("class") or []) or a.get("role") == "doc-backlink"

    fwd = [a for a in s.find_all("a", href=True)
           if a["href"].startswith("#") and len(a["href"]) > 1 and not is_backlink(a)]
    fwd_href = fwd[0]["href"] if fwd else "(no fwd ref)"
    # The reliable verdict: does a forward ref land on an element whose
    # subtree actually contains the target body?
    reaches = any(
        id_map.get(a["href"][1:]) is not None and TGT_MARK in id_map[a["href"][1:]].get_text()
        for a in fwd
    )
    doc_broken = sum(
        1 for a in s.find_all("a", href=True)
        if a["href"].startswith("#") and len(a["href"]) > 1 and a["href"][1:] not in id_map
    )
    return {
        "tgt": "live" if tgt_present else "DROP",
        "promoted": "Y" if promoted else "-",
        "fwd_href": fwd_href,
        "reaches": "Y" if reaches else "N",
        "doc_broken": doc_broken,
    }


def run_case(workdir, name, scope, target_tag, target_etype, ref_etype, tid, frag=None):
    files, spine = _files_for(scope, target_tag, target_etype, ref_etype, tid, frag or tid)
    epub = os.path.join(workdir, "case.epub")
    build_epub(workdir, epub, files, spine)
    out = os.path.join(workdir, "case.html")
    r = subprocess.run(["pandoc", epub, "-f", "epub", "-t", "html", "-s", "-o", out],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return {"name": name, "tgt": "ERR", "promoted": "", "fwd_href": r.stderr[:40], "reaches": "", "doc_broken": ""}
    res = analyze(open(out, encoding="utf-8").read())
    res["name"] = name
    return res


# name, scope, target_tag, target_etype, ref_etype, id, [frag]
CASES = [
    ("cross p",            "cross", "p",      None, None, "t1"),
    ("cross div",          "cross", "div",    None, None, "t1"),
    ("cross span",         "cross", "span",   None, None, "t1"),
    ("cross h2",           "cross", "h2",     None, None, "t1"),
    ("cross li",           "cross", "li",     None, None, "t1"),
    ("cross a",            "cross", "a",      None, None, "t1"),
    ("cross sup",          "cross", "sup",    None, None, "t1"),
    ("cross sub",          "cross", "sub",    None, None, "t1"),
    ("cross em",           "cross", "em",     None, None, "t1"),
    ("cross strong",       "cross", "strong", None, None, "t1"),
    ("cross section",      "cross", "section",None, None, "t1"),
    ("cross table",        "cross", "table",  None, None, "t1"),
    ("cross figure",       "cross", "figure", None, None, "t1"),
    ("cross nested_span",  "cross", "nested_span", None, None, "t1"),
    ("cross nested_p",     "cross", "nested_p",    None, None, "t1"),
    ("cross aside none",   "cross", "aside", None,       None, "t1"),
    ("cross aside footnt", "cross", "aside", "footnote", None, "t1"),
    ("cross aside rearnt", "cross", "aside", "rearnote", None, "t1"),
    ("cross aside endnt",  "cross", "aside", "endnote",  None, "t1"),
    ("cross aside note",   "cross", "aside", "note",     None, "t1"),
    ("cross p +ntref",     "cross", "p",     None,       "noteref", "t1"),
    ("cross div +ntref",   "cross", "div",   None,       "noteref", "t1"),
    ("cross footnt+ntref", "cross", "aside", "footnote", "noteref", "t1"),
    ("same footnt+ntref",  "same",  "aside", "footnote", "noteref", "t1"),
    ("same p +ntref",      "same",  "p",     None,       "noteref", "t1"),
    ("same div",           "same",  "div",   None,       None,      "t1"),
    ("same p",             "same",  "p",     None,       None,      "t1"),
    ("cross div x2refs",   "cross_multi", "div",   None,       None,      "t1"),
    ("cross footnt x2",    "cross_multi", "aside", "footnote", "noteref", "t1"),
    ("wholefile bare",     "wholefile",        "div", None, None, "t1"),
    ("wholefile ../Text",  "wholefile_dotdot", "div", None, None, "t1"),
    ("crossdir ../Sub#f",  "crossdir",         "div", None, None, "t1"),
    ("id dot a.b",         "cross", "div", None, None, "a.b"),
    ("id underscore a_b",  "cross", "div", None, None, "a_b"),
    ("id hyphen a-b",      "cross", "div", None, None, "a-b"),
    ("id leading digit",   "cross", "div", None, None, "1x"),
    ("id colon a:b",       "cross", "div", None, None, "a:b"),
    ("id unicode",         "cross", "div", None, None, "zhongwen中文"),
]


def main() -> None:
    ver = subprocess.run(["pandoc", "--version"], capture_output=True, text=True)
    print(ver.stdout.splitlines()[0] if ver.returncode == 0 else "pandoc not found")
    print()
    workdir = tempfile.mkdtemp(prefix="pandoc_probe_")
    try:
        rows = [run_case(workdir, *c) for c in CASES]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    w = max(len(r["name"]) for r in rows)
    print(f"{'case':<{w}}  {'tgt':<5} {'promo':<5} {'fwd_href':<24} {'reaches':<7} doc_broken")
    print("-" * (w + 56))
    for r in rows:
        print(f"{r['name']:<{w}}  {r['tgt']:<5} {r['promoted']:<5} {str(r['fwd_href']):<24} {r['reaches']:<7} {r['doc_broken']}")


if __name__ == "__main__":
    main()
