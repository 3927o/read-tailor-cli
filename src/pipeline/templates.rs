pub(crate) fn fallback_normalize_script() -> String {
    r###"#!/usr/bin/env python3
import argparse
import html
import json
import re
from pathlib import Path


def inner_body(raw: str) -> str:
    match = re.search(r"<body[^>]*>(.*)</body>", raw, re.I | re.S)
    return match.group(1).strip() if match else raw


def page_title(raw: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    if not match:
        return "Untitled"
    title = re.sub(r"<[^>]+>", "", match.group(1))
    return html.escape(title.strip() or "Untitled")


def extract_toc(body: str):
    match = re.search(r"(<nav[^>]*id=[\"']TOC[\"'][^>]*>.*?</nav>)", body, re.I | re.S)
    if not match:
        return "", body
    toc = re.sub(r"<nav", "<nav id=\"toc\" data-role=\"toc\"", match.group(1), count=1, flags=re.I)
    return toc, body[: match.start()] + body[match.end() :]


def normalize_refs(body: str) -> str:
    def repl(match):
        attrs = match.group(1)
        inner = match.group(2)
        href_match = re.search(r"href=[\"']([^\"']+)[\"']", attrs, re.I)
        marker = attrs.lower()
        href = href_match.group(1) if href_match else ""
        looks_like_note = (
            "footnote-ref" in marker
            or "doc-noteref" in marker
            or href.startswith("#fn")
            or href.startswith("#note")
        )
        if not looks_like_note:
            return match.group(0)
        if "data-role=" not in marker:
            attrs += ' data-role="noteref"'
        return f"<a{attrs}>{inner}</a>"

    return re.sub(r"<a([^>]*)>(.*?)</a>", repl, body, flags=re.I | re.S)


def extract_notes(body: str):
    match = re.search(
        r"<section[^>]*(?:footnotes|doc-endnotes)[^>]*>.*?<ol[^>]*>(.*?)</ol>.*?</section>",
        body,
        re.I | re.S,
    )
    if not match:
        return body, ""
    items = []
    for index, item in enumerate(
        re.finditer(r"<li([^>]*)id=[\"']([^\"']+)[\"']([^>]*)>(.*?)</li>", match.group(1), re.I | re.S),
        start=1,
    ):
        note_id = item.group(2)
        content = item.group(4).strip()
        items.append(f'<article data-role="note" id="{note_id}" data-kind="footnote">{content}</article>')
    notes = '<section data-role="notes" id="book-notes">' + "".join(items) + "</section>"
    stripped = body[: match.start()] + body[match.end() :]
    return stripped, notes


def split_chapters(body: str):
    chapter_matches = list(re.finditer(r"<h1[^>]*>.*?</h1>", body, re.I | re.S))
    if not chapter_matches:
        chapter_matches = list(re.finditer(r"<h2[^>]*>.*?</h2>", body, re.I | re.S))
    if not chapter_matches:
        cleaned = body.strip()
        if not cleaned:
            return ""
        return '<section class="chapter" data-type="chapter" id="ch-001"><h1>Chapter 1</h1>' + cleaned + "</section>"

    parts = []
    prefix = body[: chapter_matches[0].start()].strip()
    if prefix:
        parts.append(f'<div data-role="unknown" id="unknown-0001">{prefix}</div>')

    for index, match in enumerate(chapter_matches, start=1):
        start = match.start()
        end = chapter_matches[index].start() if index < len(chapter_matches) else len(body)
        chunk = body[start:end].strip()
        normalized_heading = re.sub(r"^<h[1-6]", "<h1", match.group(0), count=1, flags=re.I)
        normalized_heading = re.sub(r"</h[1-6]>$", "</h1>", normalized_heading, count=1, flags=re.I)
        remainder = chunk[len(match.group(0)) :].strip()
        parts.append(
            f'<section class="chapter" data-type="chapter" id="ch-{index:03d}">{normalized_heading}{remainder}</section>'
        )
    return "".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_html")
    parser.add_argument("output_html")
    args = parser.parse_args()

    raw = Path(args.input_html).read_text(encoding="utf-8")
    title = page_title(raw)
    body = inner_body(raw)
    toc, body = extract_toc(body)
    body = normalize_refs(body)
    body, notes = extract_notes(body)
    chapters = split_chapters(body)
    normalized = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>{title}</title>
</head>
<body>
  <main id=\"book\" data-type=\"book\">
    {toc}
    <section id=\"bodymatter\" data-role=\"bodymatter\">
      {chapters}
    </section>
    {notes}
  </main>
</body>
</html>
"""
    Path(args.output_html).write_text(normalized, encoding="utf-8")
    print(json.dumps({"chapters_found": chapters.count('data-type="chapter"'), "notes_found": notes.count('data-role="note"')}))


if __name__ == "__main__":
    main()
"###
        .to_string()
}

pub(crate) fn fallback_transform_script() -> String {
    r###"#!/usr/bin/env python3
import argparse
import json
import re
from html import escape
from pathlib import Path


def load_strategy(markdown: str):
    match = re.search(r"```json\s*(\{.*?\})\s*```", markdown, re.S)
    if not match:
        return {
            "title": "Untitled",
            "processing_goal": "提升阅读流畅度",
            "processing_focus": "保留结构并减少干扰",
            "note_policy": "preview",
            "heading_policy": "preserve-sections",
            "enhancements": [],
            "reading_scenario": "桌面精读",
        }
    return json.loads(match.group(1))


def inject_note_previews(html_text: str, notes):
    note_map = {note["id"]: note["content"]["text"] for note in notes}

    def repl(match):
        attrs = match.group(1)
        inner = match.group(2)
        target = re.search(r'href=["\']#([^"\']+)["\']', attrs)
        if not target:
            return match.group(0)
        note_id = target.group(1)
        preview = note_map.get(note_id, "").strip()
        if not preview:
            return match.group(0)
        preview = escape(preview[:160])
        attrs = re.sub(r'\stitle=["\'][^"\']*["\']', "", attrs)
        attrs += f' title="{preview}" data-note-preview="{preview}"'
        return f"<a{attrs}>{inner}</a>"

    return re.sub(
        r"<a([^>]*)>(.*?)</a>",
        repl,
        html_text,
        flags=re.I | re.S,
    )


def build_notes_section(notes):
    items = []
    for note in notes:
        items.append(
            "<article data-generated=\"true\" data-role=\"note\" id=\"{}\"><h3>{}</h3>{}</article>".format(
                escape(note["id"]),
                escape(note["id"]),
                note["content"]["html"],
            )
        )
    return "<section data-generated=\"true\" data-role=\"notes\" id=\"generated-notes\"><h2>Notes</h2>{}</section>".format(
        "".join(items)
    )


def add_reading_guide(html_text: str, strategy):
    guide = """
<section id=\"reading-guide\" data-generated=\"true\">
  <h1>Reading Guide</h1>
  <p><strong>处理目标：</strong>{goal}</p>
  <p><strong>处理重点：</strong>{focus}</p>
  <p><strong>注释处理：</strong>{notes}</p>
  <p><strong>标题处理：</strong>{headings}</p>
  <p><strong>阅读场景：</strong>{scenario}</p>
</section>
""".format(
        goal=escape(strategy["processing_goal"]),
        focus=escape(strategy["processing_focus"]),
        notes=escape(strategy["note_policy"]),
        headings=escape(strategy["heading_policy"]),
        scenario=escape(strategy["reading_scenario"]),
    )
    return re.sub(r"(<main[^>]*>)", r"\1" + guide, html_text, count=1, flags=re.I | re.S)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("normalized_html")
    parser.add_argument("notes_json")
    parser.add_argument("strategy_md")
    parser.add_argument("output_html")
    args = parser.parse_args()

    html_text = Path(args.normalized_html).read_text(encoding="utf-8")
    notes_payload = json.loads(Path(args.notes_json).read_text(encoding="utf-8"))
    notes = notes_payload.get("notes", [])
    strategy = load_strategy(Path(args.strategy_md).read_text(encoding="utf-8"))

    html_text = add_reading_guide(html_text, strategy)
    note_policy = strategy.get("note_policy", "preview")
    notes_appended = False
    previews_added = False

    if note_policy in {"preview", "minimal"} and notes:
        html_text = inject_note_previews(html_text, notes)
        previews_added = True

    if note_policy in {"endnotes", "preview"} and notes:
        notes_section = build_notes_section(notes)
        if "</main>" in html_text:
            html_text = html_text.replace("</main>", notes_section + "</main>", 1)
            notes_appended = True

    Path(args.output_html).write_text(html_text, encoding="utf-8")
    print(json.dumps({"notes": len(notes), "notes_appended": notes_appended, "previews_added": previews_added}))


if __name__ == "__main__":
    main()
"###
        .to_string()
}
