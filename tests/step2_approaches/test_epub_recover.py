#!/usr/bin/env python3
"""Self-contained validation of common.epub_recover.repair_surviving_ids.

WHY SYNTHETIC: our real test books only exercise the suppressed-note recovery
path (查拉/查拉_v2) or carry unrecoverable source defects (毛泽东); none of
them hit the id-dropped (p / sup) or unprefixed-survival (aside / table)
breakages that §28's pandoc probe proved exist. So these synthetic EPUBs,
built with the same machinery as pandoc_id_href_probe.py, are the ONLY
coverage for those repairs.

It checks the FULL contract, end to end, with no AI / network:

    EPUB --pandoc--> raw HTML            (forward ref is dangling)
         --recover-->                    (ref now resolves)
         --plan_7 build_normalized-->    (ref STILL resolves: the repaired id
                                          rides on real content, so plan_7's
                                          content walk keeps it)

Run:  python3 test_epub_recover.py     (exit code != 0 on any failure)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bs4 import BeautifulSoup

import pandoc_id_href_probe as P
from common.epub_recover import recover
from plan_7_program_first import build_normalized

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")


def _pandoc_raw(workdir: str, files, spine) -> tuple[str, str]:
    epub = os.path.join(workdir, "c.epub")
    P.build_epub(workdir, epub, files, spine)
    out = os.path.join(workdir, "c.html")
    subprocess.run(
        ["pandoc", epub, "-f", "epub", "-t", "html", "-s", "-o", out],
        check=True, capture_output=True, text=True,
    )
    return epub, open(out, encoding="utf-8").read()


def _forward(html: str):
    s = BeautifulSoup(html, "html.parser")
    ids = {e.get("id") for e in s.find_all(id=True) if e.get("id")}

    def is_back(a):
        return "footnote-back" in (a.get("class") or []) or a.get("role") == "doc-backlink"

    refs = [
        a["href"][1:]
        for a in s.find_all("a", href=True)
        if a["href"].startswith("#") and len(a["href"]) > 1 and not is_back(a)
    ]
    return refs, ids


def _dangling(html: str) -> list[str]:
    refs, ids = _forward(html)
    return [r for r in refs if r not in ids]


def _resolves(html: str) -> bool:
    refs, ids = _forward(html)
    return bool(refs) and all(r in ids for r in refs)


# (label, scope, target_tag, target_etype) — each is a pandoc-broken cross-file
# reference whose content nonetheless survives flattening.
POSITIVE = [
    ("id-dropped p",        "cross", "p",     None),
    ("id-dropped sup",      "cross", "sup",   None),
    ("id-dropped em",       "cross", "em",    None),
    ("unprefixed table",    "cross", "table", None),
    ("unprefixed aside",    "cross", "aside", None),
    ("unprefixed aside-note","cross","aside", "note"),
]


def run_positive(label, scope, tag, etype) -> None:
    wd = tempfile.mkdtemp(prefix="rec_test_")
    try:
        files, spine = P._files_for(scope, tag, etype, None, "t1", "t1")
        epub, raw = _pandoc_raw(wd, files, spine)
        if not _dangling(raw):
            check(f"{label}: precondition (pandoc broke it)", False,
                  "expected a dangling ref but pandoc kept it resolvable")
            return
        check(f"{label}: precondition (pandoc broke it)", True)
        augmented, _log = recover(epub, raw)
        check(f"{label}: resolves after recover", _resolves(augmented),
              "" if _resolves(augmented) else f"still dangling {_dangling(augmented)}")
        norm = build_normalized(augmented, {}, {"title": "t", "language": "en"})
        check(f"{label}: resolves after plan_7", _resolves(norm),
              "" if _resolves(norm) else f"still dangling {_dangling(norm)}")
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def run_already_good() -> None:
    """A cross-file ref to a <div> already resolves (div keeps a prefixed id);
    recover must be a strict no-op and never break it."""
    wd = tempfile.mkdtemp(prefix="rec_test_")
    try:
        files, spine = P._files_for("cross", "div", None, None, "t1", "t1")
        epub, raw = _pandoc_raw(wd, files, spine)
        check("already-good: pandoc resolves", _resolves(raw))
        augmented, log = recover(epub, raw)
        check("already-good: still resolves after recover", _resolves(augmented))
        rep = log["repairs"]
        check("already-good: repair reported no dangling",
              bool(rep) and rep[0].get("action") == "skipped:no-dangling-refs")
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def run_source_defect() -> None:
    """A ref to a fragment that does not exist anywhere in the EPUB (the
    target file has id 'real', the ref points at 'ghost'). recover must NOT
    fabricate a target — the link honestly stays broken."""
    wd = tempfile.mkdtemp(prefix="rec_test_")
    try:
        files, spine = P._files_for("cross", "div", None, None, "real", "ghost")
        epub, raw = _pandoc_raw(wd, files, spine)
        check("source-defect: pandoc dangling", bool(_dangling(raw)))
        augmented, log = recover(epub, raw)
        check("source-defect: still dangling (not fabricated)", bool(_dangling(augmented)))
        actions = [e.get("action") for e in log["repairs"]]
        check("source-defect: reported no-epub-target",
              "skipped:no-epub-target" in actions)
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def main() -> int:
    ver = subprocess.run(["pandoc", "--version"], capture_output=True, text=True)
    print(ver.stdout.splitlines()[0] if ver.returncode == 0 else "pandoc not found")
    print("\n[positive: pandoc-broken, content survives -> must repair]")
    for case in POSITIVE:
        run_positive(*case)
    print("\n[negative: must not touch / must not fabricate]")
    run_already_good()
    run_source_defect()

    failed = [r for r in _RESULTS if not r[1]]
    print(f"\n{'=' * 50}\n{len(_RESULTS) - len(failed)}/{len(_RESULTS)} checks passed")
    if failed:
        print("FAILURES:")
        for name, _ok, detail in failed:
            print(f"  - {name}  {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
