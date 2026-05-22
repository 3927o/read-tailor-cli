#!/usr/bin/env python3
"""Generic structural evaluator for normalized HTML.

Replaces the previous book-specific evaluator. Validates only against the
target normalized HTML contract from `tests/step2_research_clarified.md` §2.
No book titles, chapter names, or expected counts are hardcoded.

Errors are violations of the contract. Warnings are signals of low quality.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup, Tag


@dataclass
class EvalResult:
    approach: str
    time_seconds: float = 0.0
    ai_tokens: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "approach": self.approach,
            "time_seconds": round(self.time_seconds, 2),
            "ai_tokens": self.ai_tokens,
            "pass": self.passed,
            "errors": self.errors,
            "warnings": self.warnings,
            "metrics": self.metrics,
        }

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [f"[{status}] {self.approach} ({self.time_seconds:.1f}s)"]
        for key, value in self.metrics.items():
            lines.append(f"  {key}: {value}")
        for w in self.warnings:
            lines.append(f"  ! {w}")
        for e in self.errors:
            lines.append(f"  x {e}")
        return "\n".join(lines)


def _visible_text_chars(html: str) -> int:
    """Total length of the document's visible text. We strip head metadata
    and any code/style islands first so the count reflects body content the
    reader actually sees. Two documents with the same value preserve the
    same text — useful as a content-loss canary."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "head"]):
        t.decompose()
    return len(" ".join(soup.stripped_strings))


def evaluate(
    html_path: str, approach_name: str, raw_html_path: str | None = None
) -> EvalResult:
    result = EvalResult(approach=approach_name)
    with open(html_path, "r", encoding="utf-8") as fh:
        html = fh.read()
    soup = BeautifulSoup(html, "html.parser")

    # ---- 1. Document skeleton -------------------------------------
    book = soup.find(attrs={"data-type": "book"})
    if book is None or book.name != "main":
        result.errors.append("缺少 main[data-type='book']")
    elif book.get("id") != "book":
        result.warnings.append("main[data-type='book'] 缺少 id='book'")
    bodymatter = soup.find(attrs={"data-role": "bodymatter"})
    if bodymatter is None:
        result.errors.append("缺少 [data-role='bodymatter']")

    # ---- 2. Chapters ----------------------------------------------
    chapters = (
        bodymatter.find_all(attrs={"data-type": "chapter"}) if bodymatter else []
    )
    chapter_count = len(chapters)
    result.metrics["chapter_count"] = chapter_count
    if chapter_count == 0:
        result.errors.append("正文中未识别出任何 [data-type='chapter']")

    # ---- 3. Per-chapter contract -----------------------------------
    heading_jump_count = 0
    chapters_missing_id = 0
    chapters_first_heading_not_h1 = 0
    sub_section_count = 0
    paragraph_count = 0
    for chapter in chapters:
        if not chapter.get("id"):
            chapters_missing_id += 1
        first_heading = chapter.find(["h1", "h2", "h3", "h4", "h5", "h6"])
        if first_heading is None or first_heading.name != "h1":
            chapters_first_heading_not_h1 += 1
        # Heading-jump check: walk all headings in chapter and ensure
        # consecutive levels never skip (e.g. h2 -> h4).
        heading_levels = [
            int(h.name[1])
            for h in chapter.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
        ]
        for prev, nxt in zip(heading_levels, heading_levels[1:]):
            if nxt > prev + 1:
                heading_jump_count += 1
        sub_section_count += len(chapter.find_all(attrs={"data-type": "section"}))
        paragraph_count += len(chapter.find_all("p"))

    if chapters_missing_id:
        result.errors.append(
            f"{chapters_missing_id} 个 chapter 缺少 id 属性"
        )
    if chapters_first_heading_not_h1:
        result.errors.append(
            f"{chapters_first_heading_not_h1} 个 chapter 的首个标题不是 h1"
        )
    if heading_jump_count:
        result.warnings.append(
            f"检测到 {heading_jump_count} 处标题层级跳级（如 h2 直接跳到 h4）"
        )
    result.metrics["section_count"] = sub_section_count
    result.metrics["paragraph_count"] = paragraph_count
    result.metrics["heading_jump_count"] = heading_jump_count

    # ---- 4. Note refs / note bodies --------------------------------
    noterefs = soup.find_all(attrs={"data-role": "noteref"})
    notes = soup.find_all(attrs={"data-role": "note"})
    note_ids = {n.get("id") for n in notes if n.get("id")}
    notes_without_id = sum(1 for n in notes if not n.get("id"))
    orphan_refs = 0
    for ref in noterefs:
        href = (ref.get("href") or "").strip()
        if not href.startswith("#"):
            orphan_refs += 1
            continue
        target = href[1:]
        if target not in note_ids:
            orphan_refs += 1
    result.metrics["noteref_count"] = len(noterefs)
    result.metrics["note_count"] = len(notes)
    result.metrics["noteref_to_note_orphan_count"] = orphan_refs
    if notes_without_id:
        result.errors.append(f"{notes_without_id} 个 note 缺少 id")
    if noterefs and len(notes) == 0:
        result.errors.append("存在 noteref 但没有任何 note 正文")
    if orphan_refs and len(notes):
        result.warnings.append(
            f"{orphan_refs} 个 noteref 指向不存在的 note id"
        )

    # ---- 5. Banned tags --------------------------------------------
    banned_b = len(soup.find_all("b"))
    banned_i = len(soup.find_all("i"))
    if banned_b:
        result.errors.append(f"出现 {banned_b} 个 <b> (应为 <strong>)")
    if banned_i:
        result.errors.append(f"出现 {banned_i} 个 <i> (应为 <em>)")

    # ---- 6. Unknown blocks (warning if very high) ------------------
    unknown_blocks = soup.find_all(attrs={"data-role": "unknown"})
    result.metrics["unknown_block_count"] = len(unknown_blocks)
    total_blocks = len(soup.find_all(["section", "p", "div"]))
    if total_blocks and len(unknown_blocks) / max(total_blocks, 1) > 0.20:
        result.warnings.append(
            f"unknown 区块占比偏高 ({len(unknown_blocks)}/{total_blocks})"
        )

    # ---- 7. TOC (informational) ------------------------------------
    toc = soup.find(attrs={"data-role": "toc"})
    result.metrics["has_toc"] = toc is not None

    # ---- 8. Character recall (only when raw is available) ----------
    # Compares visible-text character counts between the raw input and the
    # normalized output. 100% means no text was silently dropped; lower
    # values flag content loss (whether from over-aggressive note
    # extraction, missing chapter slicing, etc.).
    if raw_html_path:
        try:
            with open(raw_html_path, "r", encoding="utf-8") as fh:
                raw_html = fh.read()
        except FileNotFoundError:
            result.warnings.append(f"未找到 raw HTML，跳过字符召回检查: {raw_html_path}")
        else:
            raw_chars = _visible_text_chars(raw_html)
            out_chars = _visible_text_chars(html)
            recall = out_chars / raw_chars if raw_chars else 0.0
            result.metrics["raw_char_count"] = raw_chars
            result.metrics["out_char_count"] = out_chars
            result.metrics["char_recall"] = round(recall, 4)
            if recall < 0.95:
                result.warnings.append(
                    f"字符召回率偏低 ({recall * 100:.1f}%, 丢失 {raw_chars - out_chars} 字)"
                )

    return result


def evaluate_structure_json(json_path: str, result: EvalResult) -> None:
    """Validate that structure.json conforms to the minimal schema shape."""
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        result.warnings.append("structure.json 不存在")
        return
    except json.JSONDecodeError as exc:
        result.errors.append(f"structure.json 解析失败: {exc}")
        return

    required_top = ("version", "document", "landmarks", "chapters")
    for key in required_top:
        if key not in data:
            result.errors.append(f"structure.json 缺少字段 {key}")

    chapters = data.get("chapters") or []
    result.metrics["structure_json_chapter_count"] = len(chapters)

    landmarks = data.get("landmarks") or {}
    for key in ("book_main_id", "bodymatter_id", "has_toc", "has_notes_section"):
        if key not in landmarks:
            result.warnings.append(f"structure.json.landmarks 缺少 {key}")

    stats = data.get("stats") or {}
    if stats:
        result.metrics["structure_json_paragraph_count"] = stats.get(
            "paragraph_count", "?"
        )


# ---- self-check (optional) -----------------------------------------


_PASS_SAMPLE = """<!doctype html><html lang="zh"><head><title>X</title></head><body>
<main id="book" data-type="book">
  <section id="bodymatter" data-role="bodymatter">
    <section class="chapter" data-type="chapter" id="ch-001">
      <h1>One</h1>
      <p>x <a data-role="noteref" href="#note-0001" id="r1">[1]</a></p>
    </section>
  </section>
  <section data-role="notes" id="book-notes"><div data-role="note" id="note-0001">a</div></section>
</main></body></html>"""

_FAIL_SAMPLE = """<!doctype html><html><head><title>X</title></head><body>
<article><h2>One</h2><b>bad</b></article>
</body></html>"""


def _self_check() -> None:
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ok_path = os.path.join(tmp, "ok.html")
        bad_path = os.path.join(tmp, "bad.html")
        with open(ok_path, "w", encoding="utf-8") as fh:
            fh.write(_PASS_SAMPLE)
        with open(bad_path, "w", encoding="utf-8") as fh:
            fh.write(_FAIL_SAMPLE)
        ok_result = evaluate(ok_path, "self-check-pass")
        bad_result = evaluate(bad_path, "self-check-fail")
        print(ok_result.summary())
        print()
        print(bad_result.summary())
        assert ok_result.passed, "minimal pass sample should pass"
        assert not bad_result.passed, "minimal fail sample should fail"
        print("\nself-check OK")


if __name__ == "__main__":
    _self_check()
