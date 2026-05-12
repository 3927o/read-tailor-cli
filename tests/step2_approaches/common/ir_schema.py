#!/usr/bin/env python3
"""Intermediate representation for "AI outputs fixed structure" plans.

Schema version: ir-1.0

The IR carries STRUCTURAL DECISIONS only — never the actual content. The
fixed engine (`ir_engine.apply_ir`) reads the raw HTML and lays it out
according to the IR's selectors and policies.

Top-level shape:

{
  "schema_version": "ir-1.0",
  "document": {"title": str, "language": str|null},
  "regions": [Region, ...],          # Coarse top-level partitioning
  "chapters": [Chapter, ...],        # Ordered chapter decisions
  "noterefs": NoterefPolicy,         # How in-text note refs are recognized
  "notes": NotesPolicy,              # How note bodies are recognized
  "unknowns": [Unknown, ...]         # Selectors to wrap as data-role=unknown
}

Region: {
  "role": "frontmatter"|"bodymatter"|"backmatter"|"toc"|"notes"|"unknown",
  "anchor_selector": str,            # CSS selector identifying the region
  "end_selector": str|null           # Optional explicit end (next sibling
                                     # at which the region ends)
}

Chapter: {
  "id_hint": str,                    # Optional. Ignored if collides; the
                                     # engine generates ch-001-style ids.
  "title_selector": str,             # CSS selector for the title element.
                                     # First match per chapter wins.
  "title_raw_tag": "h1"|...,         # Original tag of the title (used to
                                     # detect surrounding scope).
  "scope_selector": str|null,        # If provided, restricts chapter content
                                     # to this CSS subtree. Otherwise the
                                     # chapter spans from title up to the
                                     # next sibling matching another
                                     # chapter's title_selector.
  "subsections": [Subsection, ...]   # Optional explicit subsections
}

Subsection: {
  "title_selector": str,
  "title_raw_tag": "h1"|...,
  "logical_level": 2|3|4
}

NoterefPolicy: {
  "selector": str,                   # CSS selector matching ALL noterefs
                                     # in the document (e.g. 'a.footnote-ref')
  "href_pattern": str,               # Pattern with literal '{n}' for the
                                     # numeric / identifier portion. Used
                                     # for grouping refs to the same note.
  "id_strategy": "preserve"|"regenerate"
}

NotesPolicy: {
  "container_selector": str,         # CSS selector for the container that
                                     # holds note bodies
  "item_selector": str,              # CSS selector (relative to container)
                                     # for each note body
  "item_id_attr": "id"|"data-id"|...,# Attribute on the note body that
                                     # carries the note id. Defaults to "id".
  "kind_hint": "footnote"|"endnote"|"chapter-note"|"unknown"
}

Unknown: {
  "selector": str,
  "reason": str                      # Short structural reason
}
"""
from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "ir-1.0"

SCHEMA_JSON_DESCRIPTION = """\
{
  "schema_version": "ir-1.0",                     // string, must equal "ir-1.0"
  "document": {
    "title": "string",                            // <title> text or best guess
    "language": "string|null"                     // BCP-47 lang or null
  },
  "regions": [                                    // 0..N top-level regions
    {
      "role": "frontmatter|bodymatter|backmatter|toc|notes|unknown",
      "anchor_selector": "string",                // CSS selector locating this region
      "end_selector": "string|null"               // optional sibling at which it ends
    }
  ],
  "chapters": [                                   // ordered chapters in bodymatter
    {
      "id_hint": "string",                        // optional; engine generates final id
      "title_selector": "string",                 // CSS selector to the title element
      "title_raw_tag": "h1|h2|h3|h4|h5|h6",       // original tag (logical level becomes 1)
      "scope_selector": "string|null",            // optional explicit subtree
      "subsections": [                            // optional explicit subsections
        {
          "title_selector": "string",
          "title_raw_tag": "h1|h2|h3|h4|h5|h6",
          "logical_level": 2|3|4
        }
      ]
    }
  ],
  "noterefs": {                                   // optional; omit by setting selector=""
    "selector": "string",                         // CSS selector for all in-text noterefs
    "href_pattern": "string",                     // contains literal '{n}', e.g. '#fn-{n}'
    "id_strategy": "preserve|regenerate"
  },
  "notes": {                                      // optional; omit by setting container=""
    "container_selector": "string",               // CSS selector for notes container
    "item_selector": "string",                    // CSS selector relative to container
    "item_id_attr": "id|data-id|...",             // attribute carrying note id
    "kind_hint": "footnote|endnote|chapter-note|unknown"
  },
  "unknowns": [                                   // 0..N unknown regions to preserve
    { "selector": "string", "reason": "string" }
  ]
}
"""


def validate(ir: dict[str, Any]) -> list[str]:
    """Lightweight validation — returns a list of error strings (empty = ok)."""
    errors: list[str] = []
    if not isinstance(ir, dict):
        return ["IR must be a JSON object"]
    if ir.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {SCHEMA_VERSION!r}, got {ir.get('schema_version')!r}"
        )
    document = ir.get("document")
    if not isinstance(document, dict):
        errors.append("document must be an object")
    chapters = ir.get("chapters")
    if not isinstance(chapters, list):
        errors.append("chapters must be a list")
    elif len(chapters) == 0:
        errors.append("chapters must contain at least one chapter")
    else:
        for index, chapter in enumerate(chapters):
            if not isinstance(chapter, dict):
                errors.append(f"chapters[{index}] must be an object")
                continue
            if not chapter.get("title_selector"):
                errors.append(f"chapters[{index}].title_selector is required")
            if chapter.get("title_raw_tag") not in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                errors.append(
                    f"chapters[{index}].title_raw_tag must be one of h1..h6"
                )
    noterefs = ir.get("noterefs")
    if noterefs and isinstance(noterefs, dict) and noterefs.get("selector"):
        if "{n}" not in (noterefs.get("href_pattern") or ""):
            errors.append("noterefs.href_pattern must contain literal '{n}'")
        if noterefs.get("id_strategy") not in {"preserve", "regenerate"}:
            errors.append("noterefs.id_strategy must be 'preserve' or 'regenerate'")
    notes = ir.get("notes")
    if notes and isinstance(notes, dict) and notes.get("container_selector"):
        if not notes.get("item_selector"):
            errors.append("notes.item_selector is required when container is set")
    unknowns = ir.get("unknowns") or []
    if not isinstance(unknowns, list):
        errors.append("unknowns must be a list")
    return errors
