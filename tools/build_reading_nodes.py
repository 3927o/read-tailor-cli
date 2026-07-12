#!/usr/bin/env python3
"""Build deterministic reading nodes from an nb-1.0 normalized HTML file.

A reading node is one contiguous piece of content owned by a normalized
semantic element. Nested semantic elements become their own nodes, so parent
and child content never overlap. Short semantic elements are not merged.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup, NavigableString, PageElement, Tag

from nb_linter import NbBookLinter


CONTENT_ROLES = ("frontmatter", "bodymatter", "backmatter")
HEADING_NAMES = {"h1", "h2", "h3", "h4", "h5", "h6"}
MEDIA_NAMES = {"audio", "canvas", "figure", "img", "math", "svg", "table", "video"}
TEXT_BLOCK_NAMES = {"p", "pre", "dt", "dd", "th", "td"}
MEDIA_BLOCK_NAMES = {"figure", "audio", "video"}
ROLE_BLOCKS = {"separator", "math", "verse", "unit", "unknown"}


@dataclass(frozen=True)
class Block:
    kind: str
    text: str


@dataclass(frozen=True)
class Segment:
    html: str
    character_count: int
    block_count: int


@dataclass(frozen=True)
class ReadingNode:
    section_id: str
    segment: int
    order: int
    region: str
    data_type: str
    title: str
    parent_section_id: str | None
    character_count: int
    block_count: int
    content_html: str

    def as_dict(self, html_file: str | None = None) -> dict[str, object]:
        result: dict[str, object] = {
            "section_id": self.section_id,
            "segment": self.segment,
            "order": self.order,
            "region": self.region,
            "data_type": self.data_type,
            "title": self.title,
            "parent_section_id": self.parent_section_id,
            "character_count": self.character_count,
            "block_count": self.block_count,
        }
        if html_file is not None:
            result["html_file"] = html_file
        return result


def is_boundary(element: PageElement) -> bool:
    return (
        isinstance(element, Tag)
        and element.name == "section"
        and bool(element.get("data-type"))
    )


def own_title(element: Tag) -> str:
    for child in element.children:
        if isinstance(child, Tag) and child.name in HEADING_NAMES:
            return child.get_text(" ", strip=True)
    return ""


def is_title(element: PageElement) -> bool:
    return isinstance(element, Tag) and element.name in HEADING_NAMES


def _text_projection(element: Tag, skip_nested_lists: bool = False) -> str:
    pieces: list[str] = []

    def visit(node: PageElement, root: bool = False) -> None:
        if isinstance(node, NavigableString):
            pieces.append(str(node))
            return
        if not isinstance(node, Tag):
            return
        if node.name == "br":
            pieces.append("\n")
            return
        if skip_nested_lists and not root and node.name in {"ul", "ol"}:
            return
        for child in node.children:
            visit(child)

    visit(element, root=True)
    return "".join(pieces).replace("\r\n", "\n").replace("\r", "\n")


def _has_visible_direct_inline_content(element: Tag) -> bool:
    for child in element.children:
        if isinstance(child, NavigableString) and str(child).strip():
            return True
        if not isinstance(child, Tag):
            continue
        if child.name in {"ul", "ol"} or child.name in TEXT_BLOCK_NAMES:
            continue
        if child.get_text(strip=True) or child.name in {"img", "audio", "video", "br"}:
            return True
    return False


def extract_blocks(html: str) -> list[Block]:
    """Enumerate Block v1 entries and their canonical text projections."""
    fragment = BeautifulSoup(html, "html.parser")
    blocks: list[Block] = []

    for element in fragment.find_all(True):
        if element.name in TEXT_BLOCK_NAMES:
            # Flow containers such as td/dd may legally contain paragraphs.
            # In that case the nested atomic blocks own the text; emitting the
            # container as well would make the same source text addressable by
            # two different block indexes.
            has_nested_text_block = element.find(list(TEXT_BLOCK_NAMES)) is not None
            if not has_nested_text_block:
                text = _text_projection(element)
                if text.strip():
                    blocks.append(Block(kind=element.name, text=text))
            continue

        if element.name == "li":
            if element.find("p", recursive=False) is None and _has_visible_direct_inline_content(element):
                blocks.append(
                    Block(kind="li", text=_text_projection(element, skip_nested_lists=True))
                )
            continue

        if element.name == "figcaption":
            if element.find("p", recursive=False) is None:
                blocks.append(Block(kind="figcaption", text=_text_projection(element)))
            continue

        if element.name in MEDIA_BLOCK_NAMES:
            blocks.append(Block(kind=element.name, text=""))
            continue

        if element.name == "div" and element.get("data-role") in ROLE_BLOCKS:
            has_nested_text_block = element.find(
                list(TEXT_BLOCK_NAMES | {"li", "figcaption"})
            ) is not None
            if not has_nested_text_block and (
                element.get_text(strip=True) or element.find(list(MEDIA_NAMES))
            ):
                blocks.append(
                    Block(kind=f"div:{element.get('data-role')}", text=_text_projection(element))
                )

    return blocks


def make_segment(parts: Iterable[PageElement]) -> Segment | None:
    html = "".join(str(part) for part in parts).strip()
    if not html:
        return None

    fragment = BeautifulSoup(html, "html.parser")
    text = " ".join(fragment.stripped_strings)
    has_media = fragment.find(list(MEDIA_NAMES)) is not None
    if not text and not has_media:
        return None

    return Segment(
        html=html,
        character_count=len(text),
        block_count=len(extract_blocks(html)),
    )


def direct_boundaries(element: Tag) -> list[Tag]:
    return [child for child in element.children if is_boundary(child)]


class ReadingNodeBuilder:
    def __init__(self, soup: BeautifulSoup) -> None:
        self.soup = soup
        self.nodes: list[ReadingNode] = []
        self.warnings: list[str] = []
        self._seen_ids: set[str] = set()

    def build(self) -> list[ReadingNode]:
        book = self.soup.select_one('main#book[data-type="book"]')
        if book is None:
            raise ValueError('missing main#book[data-type="book"]')

        regions = [
            child
            for child in book.find_all("section", recursive=False)
            if child.get("data-role") in CONTENT_ROLES
        ]
        if not any(region.get("data-role") == "bodymatter" for region in regions):
            raise ValueError('missing direct section[data-role="bodymatter"]')

        for region in regions:
            role = str(region["data-role"])
            self._emit_region(region, role)
        return self.nodes

    def _emit_region(self, region: Tag, role: str) -> None:
        region_id = region.get("id")
        if not region_id:
            raise ValueError(f'{role} region is missing required id')

        events: list[Segment | Tag] = []
        pending: list[PageElement] = []
        for child in region.children:
            if is_boundary(child):
                segment = make_segment(pending)
                if segment is not None:
                    events.append(segment)
                pending = []
                events.append(child)
            else:
                pending.append(child)
        segment = make_segment(pending)
        if segment is not None:
            events.append(segment)

        segment_number = 0
        for event in events:
            if isinstance(event, Segment):
                segment_number += 1
                self.nodes.append(
                    ReadingNode(
                        section_id=str(region_id),
                        segment=segment_number,
                        order=len(self.nodes) + 1,
                        region=role,
                        data_type=role,
                        title="",
                        parent_section_id=None,
                        character_count=event.character_count,
                        block_count=event.block_count,
                        content_html=event.html,
                    )
                )
            else:
                self._emit_boundary(event, role, parent_section_id=None)

    def _emit_boundary(
        self,
        element: Tag,
        region: str,
        parent_section_id: str | None,
    ) -> None:
        section_id = self._element_id(element)
        title = own_title(element)
        data_type = str(element.get("data-type") or element.get("data-role") or "unknown")

        events: list[Segment | Tag] = []
        pending: list[PageElement] = []
        for child in element.children:
            if is_boundary(child):
                segment = make_segment(pending)
                if segment is not None:
                    events.append(segment)
                pending = []
                events.append(child)
            elif not is_title(child):
                pending.append(child)
        segment = make_segment(pending)
        if segment is not None:
            events.append(segment)

        segment_count = sum(isinstance(event, Segment) for event in events)
        segment_number = 0
        for event in events:
            if isinstance(event, Segment):
                segment_number += 1
                self.nodes.append(
                    ReadingNode(
                        section_id=section_id,
                        segment=segment_number,
                        order=len(self.nodes) + 1,
                        region=region,
                        data_type=data_type,
                        title=title,
                        parent_section_id=parent_section_id,
                        character_count=event.character_count,
                        block_count=event.block_count,
                        content_html=event.html,
                    )
                )
            else:
                self._emit_boundary(event, region, parent_section_id=section_id)

        if segment_count == 0 and not direct_boundaries(element):
            self.warnings.append(f"empty semantic element skipped: {section_id}")

    def _element_id(self, element: Tag) -> str:
        existing = element.get("id")
        if existing:
            element_id = str(existing)
        else:
            raise ValueError("semantic section is missing required id")

        if element_id in self._seen_ids:
            raise ValueError(f"duplicate semantic element id: {element_id}")
        self._seen_ids.add(element_id)
        return element_id


def safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return value[:80] or "node"


def build_manifest(
    input_path: Path,
    html_dir: Path | None = None,
    require_valid: bool = False,
) -> dict[str, object]:
    raw = input_path.read_text(encoding="utf-8")
    lint_result = NbBookLinter(raw).run_all_checks()
    lint_errors = lint_result["errors"]
    if require_valid and lint_errors:
        preview = "; ".join(lint_errors[:3])
        if len(lint_errors) > 3:
            preview += f"; ... ({len(lint_errors)} errors total)"
        raise ValueError(f"input does not conform to nb-1.0: {preview}")

    soup = BeautifulSoup(raw, "html.parser")
    builder = ReadingNodeBuilder(soup)
    nodes = builder.build()

    title = soup.find("title")
    html = soup.find("html")
    node_dicts: list[dict[str, object]] = []

    if html_dir is not None:
        html_dir.mkdir(parents=True, exist_ok=True)

    for node in nodes:
        html_file: str | None = None
        if html_dir is not None:
            suffix = f"-{node.segment}" if node.segment > 1 else ""
            filename = f"{node.order:04d}-{safe_filename(node.section_id)}{suffix}.html"
            (html_dir / filename).write_text(node.content_html + "\n", encoding="utf-8")
            html_file = filename
        node_dicts.append(node.as_dict(html_file))

    return {
        "version": "reading-nodes-1.0",
        "document": {
            "title": title.get_text(" ", strip=True) if title else "",
            "language": str(html.get("lang") or "und") if isinstance(html, Tag) else "und",
        },
        "node_count": len(node_dicts),
        "nodes": node_dicts,
        "warnings": builder.warnings,
        "validation": {
            "is_valid": not lint_errors,
            "error_count": len(lint_errors),
            "warning_count": len(lint_result["warnings"]),
        },
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build non-overlapping reading nodes from nb-1.0 normalized HTML."
    )
    parser.add_argument("input", type=Path, help="Path to book.normalized.html")
    parser.add_argument("-o", "--output", type=Path, help="Write manifest JSON to this path")
    parser.add_argument(
        "--html-dir",
        type=Path,
        help="Optionally write each node's exclusive HTML to this directory",
    )
    parser.add_argument(
        "--require-valid",
        action="store_true",
        help="Refuse input that has nb-1.0 linter errors",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        manifest = build_manifest(args.input, args.html_dir, args.require_valid)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
