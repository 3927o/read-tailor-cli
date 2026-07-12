#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from build_reading_nodes import ReadingNodeBuilder, extract_blocks


class ReadingNodeBuilderTests(unittest.TestCase):
    def build(self, body: str):
        html = f"""<!doctype html><html lang="zh"><head><title>T</title></head><body>
        <main id="book" data-type="book">
          <section id="bodymatter" data-role="bodymatter">{body}</section>
        </main></body></html>"""
        builder = ReadingNodeBuilder(BeautifulSoup(html, "html.parser"))
        return builder.build()

    def test_leaf_subsections_remain_independent(self) -> None:
        nodes = self.build("""
          <section id="part-1" data-type="part"><h1>Part</h1>
            <section id="s-1" data-type="subsection"><h2>1</h2><p>alpha</p></section>
            <section id="s-2" data-type="subsection"><h2>2</h2><p>beta</p></section>
          </section>
        """)
        self.assertEqual([node.section_id for node in nodes], ["s-1", "s-2"])
        self.assertEqual([node.title for node in nodes], ["1", "2"])

    def test_parent_content_keeps_document_order_around_children(self) -> None:
        nodes = self.build("""
          <section id="chapter-1" data-type="chapter"><h1>Chapter</h1>
            <p>before</p>
            <section id="epigraph" data-type="epigraph"><p>quotation</p></section>
            <p>after</p>
          </section>
        """)
        self.assertEqual(
            [(node.section_id, node.segment) for node in nodes],
            [("chapter-1", 1), ("epigraph", 1), ("chapter-1", 2)],
        )
        self.assertEqual(
            [BeautifulSoup(node.content_html, "html.parser").get_text(strip=True) for node in nodes],
            ["before", "quotation", "after"],
        )

    def test_parent_and_child_content_do_not_overlap(self) -> None:
        nodes = self.build("""
          <section id="chapter-1" data-type="chapter"><h1>Chapter</h1>
            <p>intro</p>
            <section id="section-1" data-type="section"><h2>Section</h2><p>child</p></section>
          </section>
        """)
        parent, child = nodes
        self.assertIn("intro", parent.content_html)
        self.assertNotIn("child", parent.content_html)
        self.assertIn("child", child.content_html)

    def test_empty_grouping_section_is_not_a_node(self) -> None:
        nodes = self.build("""
          <section id="part-1" data-type="part"><h1>Part</h1>
            <section id="chapter-1" data-type="chapter"><h2>Chapter</h2><p>text</p></section>
          </section>
        """)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].section_id, "chapter-1")

    def test_region_owned_unknown_content_is_preserved(self) -> None:
        nodes = self.build("""
          <div data-role="unknown" data-reason="publisher_notice"><p>notice</p></div>
          <section id="chapter-1" data-type="chapter"><h1>Chapter</h1><p>text</p></section>
        """)
        self.assertEqual(
            [(node.section_id, node.data_type) for node in nodes],
            [("bodymatter", "bodymatter"), ("chapter-1", "chapter")],
        )
        self.assertIn("notice", nodes[0].content_html)

    def test_non_navigable_unit_stays_inside_its_section(self) -> None:
        nodes = self.build("""
          <section id="chapter-1" data-type="chapter"><h1>Chapter</h1>
            <div data-role="unit" data-unit-num="1"><p>first</p></div>
            <div data-role="unit" data-unit-num="2"><p>second</p></div>
          </section>
        """)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].section_id, "chapter-1")
        self.assertIn("first", nodes[0].content_html)
        self.assertIn("second", nodes[0].content_html)
        self.assertEqual(nodes[0].block_count, 2)

    def test_block_v1_preserves_inline_semantics_as_text(self) -> None:
        blocks = extract_blocks(
            "<p>这是<strong>重要</strong>而且<em>强调</em>的正文。<br>下一行</p>"
        )
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].kind, "p")
        self.assertEqual(blocks[0].text, "这是重要而且强调的正文。\n下一行")

    def test_block_v1_uses_atomic_children_without_container_overlap(self) -> None:
        blocks = extract_blocks("""
          <blockquote><p>first</p><p>second</p></blockquote>
          <ul><li>one</li><li><p>two</p><ul><li>nested</li></ul></li></ul>
          <figure><img src="assets/a.png"><figcaption><p>caption</p></figcaption></figure>
        """)
        self.assertEqual(
            [(block.kind, block.text.strip()) for block in blocks],
            [
                ("p", "first"),
                ("p", "second"),
                ("li", "one"),
                ("p", "two"),
                ("li", "nested"),
                ("figure", ""),
                ("p", "caption"),
            ],
        )

    def test_block_v1_does_not_repeat_paragraphs_inside_flow_containers(self) -> None:
        blocks = extract_blocks("""
          <table><tbody><tr><td><p>cell</p></td></tr></tbody></table>
          <dl><dt>term</dt><dd><p>definition</p></dd></dl>
        """)
        self.assertEqual(
            [(block.kind, block.text.strip()) for block in blocks],
            [
                ("p", "cell"),
                ("dt", "term"),
                ("p", "definition"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
