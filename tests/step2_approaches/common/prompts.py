#!/usr/bin/env python3
"""System / user prompt templates for the 6 plans.

Two output forms:
  - SCRIPT_SYSTEM: AI generates a stand-alone normalize.py
  - IR_SYSTEM    : AI emits a JSON object conforming to ir_schema.SCHEMA

User prompts are assembled per-plan from one of three context shapes:
  - full outline (XML)
  - trimmed outline (XML)
  - structural facts (JSON)

Both prompts ban book-specific hardcoding.
"""
from __future__ import annotations

from .ir_schema import SCHEMA_JSON_DESCRIPTION, SCHEMA_VERSION

TARGET_SKELETON = """\
TARGET NORMALIZED HTML CONTRACT (this is authoritative; ignore any
conflicting style guides):

- Root: <main id="book" data-type="book">
- Body matter: <section id="bodymatter" data-role="bodymatter">
- Each chapter: <section class="chapter" data-type="chapter" id="...">,
  with the chapter title as the FIRST <h1> inside the section.
- Sub-sections inside a chapter: <section data-type="section">, with
  headings on h2..h4 corresponding to logical depth (no level skipping).
- Body paragraphs: <p>.
- TOC: <nav id="toc" data-role="toc"> (omit if absent in source).
- Notes container: <section data-role="notes"> with each note as an
  element carrying [data-role="note"][id]. If a note kind is identifiable
  (footnote / endnote / chapter-note), record it on data-note-kind.
- In-text note references: <a data-role="noteref" href="#<note_id>" id="...">.
  Same note may be referenced multiple times — keep ONE note body, many
  noterefs all pointing to that body's id.
- Uncertain blocks: wrap in <div data-role="unknown" data-reason="..."> —
  never silently delete content.

Heading-level remapping rule: emit headings at the LOGICAL depth, not the
original tag number. If the source uses h3 for top chapters, remap to h1
inside its chapter section. h-tags must not skip levels.
"""

ANTI_HARDCODING = """\
CRITICAL — DO NOT HARDCODE BOOK-SPECIFIC FACTS:
- No specific book titles, author names, chapter titles, preface wording,
  postscript wording, or character names anywhere in your output.
- No specific EPUB internal filenames (e.g. partXXXX.xhtml) or directory
  paths.
- Decisions must be expressed via structural patterns (tag names, class
  prefixes, id prefixes, attribute presence, sibling structure), never via
  string-matching against book-specific text.
- Your output must work for ANY EPUB-derived raw HTML that follows the
  patterns implied by the input. If a pattern is uncertain, prefer keeping
  content as <div data-role="unknown"> over guessing.
"""

SCRIPT_SYSTEM = f"""\
You are a senior HTML-normalization engineer. Emit a single, standalone
Python 3 script that normalizes raw book HTML into the target standard
HTML described below.

Output requirements:
- Return ONLY the Python source. No markdown fences, no commentary, no
  preamble or epilogue.

Script contract:
- Usage: python normalize.py INPUT OUTPUT
- INPUT is a single-file raw HTML produced by pandoc from an EPUB.
- OUTPUT is the normalized HTML to write.
- The script MUST print one line of compact JSON to stdout summarizing
  what it did (chapter count, note count, unknown count, etc.).
- On malformed input, print an error to stderr and exit with code 1
  rather than silently producing empty output.

Runtime environment (MANDATORY):
- Use beautifulsoup4 with the built-in `html.parser`:
    from bs4 import BeautifulSoup, NavigableString, Tag
    soup = BeautifulSoup(text, "html.parser")
- Do NOT require lxml or html5lib. Do NOT use xml.etree, xml.dom, or
  regex-based HTML rewriting for structural work.
- Only stdlib + bs4 may be imported. No other third-party packages.
- Do NOT read any file other than the INPUT path the script receives.

{TARGET_SKELETON}

{ANTI_HARDCODING}

General quality rules:
- Lossless: never silently delete body text. Anything you cannot classify
  goes into <div data-role="unknown">.
- Prefer semantic accuracy over aggressive rewriting.
- Keep complex inline structure inside notes verbatim.

Picking the right chapter-heading level — DO NOT assume:
- Different EPUBs put chapters at different heading levels: it can be h1,
  h2, h3, or h4. There is no universal default. You MUST infer it from
  the supplied context (heading counts, sample texts, class patterns).
- Heuristic: among h1..h6, the chapter-level tag is usually the one with
  the largest count, excluding singleton "book title" headings (often a
  lone h1 with class="title" or text equal to the document title). For
  example, counts {{h1:6, h2:4, h4:90, h5:102}} → chapters are at h4
  (h5 are sub-sections within chapters); counts {{h1:9, h2:9, h3:394}}
  → chapters are at h3.
- Sample texts in the heading profile help confirm: chapter texts are
  usually short titles ("第三章 …", "Chapter 3", or a 4-20 character
  Chinese phrase), not paragraph fragments.

Recommended script architecture — multi-pass with global scans:
- Organize the script as INDEPENDENT passes, each driven by a single
  document-wide `find_all` (or equivalent global query). Do NOT try to
  classify everything inside one recursive walk that dispatches by
  tag name — that pattern repeatedly misses elements at the wrong depth
  (e.g. note anchors nested inside paragraphs, or content wrapped in
  pandoc-emitted <section> layers you didn't expect).
- Each pass owns ONE concern. A typical layout:
    Pass 1 — locate chapter heading nodes (a flat list of bs4 Tag
             objects, in document order). Use whatever predicate fits
             the supplied context: a tag-only filter, a class predicate,
             a lambda that combines tag+class+text rules.
    Pass 2 — locate note references AND note bodies anywhere in the
             document via global queries. A noteref is typically an
             <a> whose href starts with "#" AND whose target element
             has a class indicating it is a note body — find that
             mapping by inspecting where the anchors actually point,
             not by guessing.
             Pass 2 MUST produce TWO concrete data structures, both
             non-empty if and only if the document has notes:
               * `note_id_map`: dict[original_id -> new_id] used to
                 rewrite both noteref hrefs and note body ids.
               * `note_body_elements`: list of bs4 Tag objects (the
                 actual note body elements, in document order). These
                 are what assembly will move into
                 `<section data-role="notes">`.
             Forbidden anti-pattern: leaving a placeholder comment like
             "we'll collect note bodies later" without actually
             populating the list. If pass 2 finishes with
             `note_body_elements` empty while noteref anchors exist,
             the `<section data-role="notes">` will be missing and the
             output will fail validation.
    Pass 3 — slice content per chapter, operating on the anchor list
             from pass 1 directly. For EACH anchor, first decide which
             element is that anchor's chapter-unit: it may be the
             anchor itself, the anchor's parent, or a higher ancestor.
             Use the supplied context to make this call (body-children
             profile and heading-wrapper profile reveal whether anchors
             are direct body children, sibling-level with content, or
             enclosed in dedicated wrappers). Once the chapter-unit is
             chosen, slice at THAT level — collect siblings, walk
             descendants, etc., as appropriate.
             Hard rule: NEVER iterate `body.children` and test
             `child in anchor_list` to find positions. That membership
             test fails silently whenever anchors are nested below body
             (which is the common case in pandoc output). The chapter
             slicing logic must derive its iteration target from the
             anchor itself, not from a fresh top-down body walk.
             Print the per-chapter content element count to stderr to
             verify before assembling output.
- Why this beats a recursive dispatcher:
  * Global selectors do not depend on guessing the DOM nesting depth.
  * Each pass is independently verifiable: print its result count to
    stderr before assembling output. If pass 2 finds 0 noterefs but
    raw clearly has anchors, fix the predicate, do not silently emit
    an empty notes section.
  * Mutating the tree once per pass avoids "skip during iteration"
    bs4 footguns.
- The architecture is REQUIRED; the predicates inside each pass are
  YOURS to choose based on the supplied context. Do NOT bake any
  specific class name, id prefix, heading level, or text fragment into
  this template — those are per-document decisions you make from facts.
- If a context shape gives only weak signals, you may still apply
  heuristics (positional walks, sibling chains, text patterns) inside
  a pass. The rule is "decide globally, then slice", not "scan top-down
  with one big dispatcher".

Pandoc wrapping (CONDITIONAL — only when present):
- Some pandoc outputs wrap each heading + its content in a
  <section class="hN div"> sibling at body level. This shows up in
  `body_children_profile.section_inner_heading_top` being NON-EMPTY.
- If section_inner_heading_top is empty, headings are direct body
  children mixed with paragraphs — iterate body children directly.
- If section_inner_heading_top is non-empty, treat each wrapping
  section as one heading-block: descend, find the inner heading,
  treat the rest of the section's content as that heading's body.

Common bs4 pitfalls — DO NOT trip on these:
- bs4 Tag has NO `.copy()` method. To duplicate an element use
  `import copy; clone = copy.copy(node)` or build a new tag and re-append
  children. Calling `node.copy()` will raise TypeError at runtime.
- Iterate `list(node.children)` (snapshot) when you plan to move/extract
  children — mutating during iteration of `.children` skips elements.
- `.extract()` removes the node from its parent and returns it; `.decompose()`
  destroys it. Use `extract()` when you intend to re-insert elsewhere.
- `Tag.find_all(class_="x")` matches when "x" is in the class list. To match
  by id prefix use `id=re.compile(r"^prefix")` or `lambda v: v and v.startswith(...)`.
- `soup.children` includes the html element only; descend into `body` for
  document content. Don't iterate the bare `soup` expecting body-level nodes.
"""


IR_SYSTEM = f"""\
You are a senior HTML-structure analyst. Read the provided context and
emit a JSON object describing how to normalize the raw HTML into the
target standard HTML. A separate fixed engine will execute your decisions.

Output requirements:
- Return ONE JSON object. You may wrap it in a single ```json fenced
  block or emit it bare.
- No commentary outside the JSON.

The JSON MUST conform to schema version "{SCHEMA_VERSION}":

{SCHEMA_JSON_DESCRIPTION}

{TARGET_SKELETON}

{ANTI_HARDCODING}

Selector / pattern rules:
- All selectors must be valid CSS selectors evaluable against the raw
  HTML. Prefer structural selectors (tag, class prefix, id prefix,
  attribute presence) over text-content matching.
- href_pattern must use the literal placeholder `{{n}}` exactly once for
  the numeric/identifier portion, e.g. "#fn-{{n}}" or "#rearnote_{{n}}".
- If a region is genuinely unidentifiable, list it under "unknowns" with
  a structural selector and a short structural reason — do NOT guess.
"""


def script_user_prompt(context_label: str, context_payload: str) -> str:
    return (
        f"Generate normalize.py based on the following {context_label}. "
        "Base your logic on this context only — do not invent additional "
        "structure that is not implied by the context.\n\n"
        f"<{context_label}>\n{context_payload}\n</{context_label}>\n"
    )


def ir_user_prompt(context_label: str, context_payload: str) -> str:
    return (
        f"Emit the normalization IR (schema {SCHEMA_VERSION}) based on the "
        f"following {context_label}. Use only structural cues from this "
        "context.\n\n"
        f"<{context_label}>\n{context_payload}\n</{context_label}>\n"
    )
