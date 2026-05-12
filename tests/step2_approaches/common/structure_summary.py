#!/usr/bin/env python3
"""Generic `structure.json` summarizer.

Walks a *normalized* HTML document (one that conforms to the PRD-style
target format) and emits the minimal `structure.json` schema described in
`docs/prd.md` §3.5. Book-agnostic — never references specific titles.
"""
from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup, Tag


def summarize(normalized_html: str) -> dict[str, Any]:
    soup = BeautifulSoup(normalized_html, "html.parser")
    title_node = soup.find("title")
    html_node = soup.find("html")
    document = {
        "title": title_node.get_text(strip=True) if title_node else "Untitled",
        "language": (html_node.get("lang") if isinstance(html_node, Tag) else None) or "und",
    }

    book = soup.find(attrs={"data-type": "book"})
    bodymatter = soup.find(attrs={"data-role": "bodymatter"})
    toc = soup.find(attrs={"data-role": "toc"})
    notes_section = soup.find(attrs={"data-role": "notes"})

    landmarks = {
        "book_main_id": book.get("id", "") if isinstance(book, Tag) else "",
        "bodymatter_id": bodymatter.get("id", "") if isinstance(bodymatter, Tag) else "",
        "toc_id": toc.get("id", "") if isinstance(toc, Tag) else "",
        "has_toc": toc is not None,
        "has_notes_section": notes_section is not None,
    }

    chapters: list[dict[str, Any]] = []
    unknown_blocks: list[dict[str, Any]] = []
    total_sections = 0
    total_paragraphs = 0

    chapter_nodes = []
    if isinstance(bodymatter, Tag):
        chapter_nodes = bodymatter.find_all(attrs={"data-type": "chapter"})

    for index, chapter in enumerate(chapter_nodes, start=1):
        chapter_id = chapter.get("id") or f"ch-{index:03d}"
        h1 = chapter.find("h1")
        title = h1.get_text(strip=True) if h1 else f"Chapter {index}"

        paragraphs = chapter.find_all("p")
        paragraph_count = len(paragraphs)
        noterefs = chapter.find_all(attrs={"data-role": "noteref"})
        unknowns = chapter.find_all(attrs={"data-role": "unknown"})
        sections = chapter.find_all(attrs={"data-type": "section"})

        section_summaries = []
        for sec_index, section in enumerate(sections, start=1):
            heading = section.find(["h1", "h2", "h3", "h4"])
            heading_level = int(heading.name[1]) if heading else 2
            section_summaries.append(
                {
                    "id": section.get("id", f"{chapter_id}-sec{sec_index}"),
                    "title": heading.get_text(strip=True) if heading else "",
                    "heading_level": heading_level,
                    "index": sec_index,
                }
            )

        for u_index, unknown in enumerate(unknowns, start=1):
            unknown_blocks.append(
                {
                    "id": unknown.get("id") or f"unknown-{len(unknown_blocks) + 1:04d}",
                    "chapter_id": chapter_id,
                    "index": u_index,
                    "reason": unknown.get("data-reason", "ambiguous-structure"),
                    "text_preview": unknown.get_text(strip=True)[:30],
                }
            )

        total_paragraphs += paragraph_count
        total_sections += len(section_summaries)

        chapters.append(
            {
                "id": chapter_id,
                "index": index,
                "title": title,
                "section_count": len(section_summaries),
                "paragraph_count": paragraph_count,
                "note_ref_count": len(noterefs),
                "unknown_block_count": len(unknowns),
                "sections": section_summaries,
            }
        )

    notes_meta = {
        "note_ref_count": len(soup.find_all(attrs={"data-role": "noteref"})),
        "notes_section_id": notes_section.get("id", "") if isinstance(notes_section, Tag) else "",
    }

    return {
        "version": "1.0",
        "document": document,
        "landmarks": landmarks,
        "chapters": chapters,
        "notes": notes_meta,
        "unknown_blocks": unknown_blocks,
        "stats": {
            "chapter_count": len(chapters),
            "section_count": total_sections,
            "paragraph_count": total_paragraphs,
        },
    }


if __name__ == "__main__":
    sample = """<!doctype html><html lang="zh"><head><title>Test</title></head><body>
<main id="book" data-type="book">
  <section id="bodymatter" data-role="bodymatter">
    <section class="chapter" data-type="chapter" id="ch-001"><h1>One</h1><p>x</p>
      <a data-role="noteref" href="#n1" id="r1">[1]</a></section>
  </section>
  <section data-role="notes" id="book-notes"><div data-role="note" id="n1">a</div></section>
</main></body></html>"""
    import json

    print(json.dumps(summarize(sample), ensure_ascii=False, indent=2))
