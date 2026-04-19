#!/usr/bin/env python3
"""
评估器：对规范化后的 HTML 进行质量评估。

评估维度：
1. 结构完整性 - 是否包含必要的语义结构
2. 章节数量 - 是否识别出正确的章节数（预期 90 章）
3. 注释引用 - noteref 是否保留并格式正确
4. 前后结构 - 是否区分了 frontmatter / bodymatter / backmatter
5. HTML 有效性 - 是否有明显的结构性错误
"""
import json
import re
import time
from bs4 import BeautifulSoup


# 本书的已知 ground truth（基于我们之前的分析）
GROUND_TRUTH = {
    "expected_chapters_min": 85,   # 至少识别出 85 章（允许误差）
    "expected_chapters_max": 95,
    "expected_note_refs_min": 1200,  # 至少保留 1200 个 noteref
    "has_frontmatter": True,        # 有译者前言
    "has_backmatter": True,         # 有译后记
    "has_notes_section": True,      # 有注释区域
    "parts": ["第一部", "第二部", "第三部", "第四部"],
    "known_chapter_titles": [       # 部分已知章节标题，用于抽样检查
        "三段变化",
        "道德的讲座",
        "夜歌",
        "古老的法版和新的法版",
        "醉歌",
        "预兆",
    ],
}


class EvalResult:
    def __init__(self, approach_name: str):
        self.approach = approach_name
        self.time_seconds = 0.0
        self.ai_tokens = 0  # 对于 AI 方案
        self.errors = []
        self.warnings = []
        self.metrics = {}

    def to_dict(self):
        return {
            "approach": self.approach,
            "time_seconds": round(self.time_seconds, 2),
            "ai_tokens": self.ai_tokens,
            "pass": len(self.errors) == 0,
            "errors": self.errors,
            "warnings": self.warnings,
            "metrics": self.metrics,
        }

    def summary(self):
        status = "PASS" if not self.errors else "FAIL"
        lines = [f"[{status}] {self.approach} ({self.time_seconds:.1f}s)"]
        for k, v in self.metrics.items():
            lines.append(f"  {k}: {v}")
        for w in self.warnings:
            lines.append(f"  ⚠ {w}")
        for e in self.errors:
            lines.append(f"  ✗ {e}")
        return "\n".join(lines)


def evaluate(html_path: str, approach_name: str) -> EvalResult:
    """评估一个规范化后的 HTML 文件"""
    result = EvalResult(approach_name)

    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    soup = BeautifulSoup(content, "html.parser")

    # --- 1. 基础结构检查 ---
    main = soup.find("main") or soup.find(attrs={"data-type": "book"})
    if not main:
        result.errors.append("缺少 main#book 或 data-type='book' 的根元素")
    else:
        result.metrics["root_element"] = f"<{main.name}> id={main.get('id', 'none')}"

    bodymatter = soup.find(attrs={"data-role": "bodymatter"})
    if not bodymatter:
        result.errors.append("缺少 section[data-role='bodymatter']")
    else:
        result.metrics["bodymatter"] = "found"

    # --- 2. 章节计数 ---
    chapters = soup.find_all(attrs={"data-type": "chapter"})
    if not chapters:
        # fallback: 尝试其他常见模式
        chapters = soup.find_all("h4")
        if not chapters:
            chapters = soup.find_all("section")
            chapters = [c for c in chapters if c.find(["h1", "h2", "h3", "h4"])]

    chapter_count = len(chapters)
    result.metrics["chapter_count"] = chapter_count

    if chapter_count < GROUND_TRUTH["expected_chapters_min"]:
        result.errors.append(
            f"章节数 {chapter_count} < 预期最小值 {GROUND_TRUTH['expected_chapters_min']}"
        )
    elif chapter_count > GROUND_TRUTH["expected_chapters_max"]:
        result.warnings.append(
            f"章节数 {chapter_count} > 预期最大值 {GROUND_TRUTH['expected_chapters_max']}"
        )

    # --- 3. 注释引用 ---
    # 查找所有包含 rearnote 或 data-role="noteref" 的链接
    all_links = soup.find_all("a")
    note_ref_links = []
    for a in all_links:
        href = a.get("href", "")
        data_role = a.get("data-role", "")
        cls = " ".join(a.get("class", []))
        if "rearnote" in href or "noteref" in href or data_role == "noteref" or "noteref" in cls:
            note_ref_links.append(a)

    note_ref_count = len(note_ref_links)
    result.metrics["note_ref_count"] = note_ref_count

    if note_ref_count < GROUND_TRUTH["expected_note_refs_min"]:
        result.errors.append(
            f"注释引用 {note_ref_count} < 预期最小值 {GROUND_TRUTH['expected_note_refs_min']}"
        )

    # 检查 noteref 标准化程度
    standard_refs = [a for a in note_ref_links if a.get("data-role") == "noteref"]
    result.metrics["standard_noteref_count"] = len(standard_refs)
    if len(standard_refs) < note_ref_count * 0.8:
        result.warnings.append(
            f"标准化 noteref 仅 {len(standard_refs)}/{note_ref_count}，"
            f"可能未正确转换格式"
        )

    # --- 4. 前言/后记 ---
    frontmatter = soup.find(attrs={"data-role": "frontmatter"})
    if GROUND_TRUTH["has_frontmatter"] and not frontmatter:
        # 也检查是否有译者前言标题
        has_preface = any("译者前言" in (h.get_text() if hasattr(h, "get_text") else "") for h in soup.find_all(["h1", "h2"]))
        if not has_preface:
            result.warnings.append("未识别出译者前言（frontmatter）")

    backmatter = soup.find(attrs={"data-role": "backmatter"})
    if GROUND_TRUTH["has_backmatter"] and not backmatter:
        has_postscript = any("译后记" in (h.get_text() if hasattr(h, "get_text") else "") for h in soup.find_all(["h1", "h2"]))
        if not has_postscript:
            result.warnings.append("未识别出译后记（backmatter）")

    # --- 5. 四部结构 ---
    body_text = soup.get_text()
    for part_name in GROUND_TRUTH["parts"]:
        if part_name not in body_text:
            result.errors.append(f"未找到 '{part_name}' 部分")

    # --- 6. 已知章节标题抽样 ---
    found_titles = 0
    for title in GROUND_TRUTH["known_chapter_titles"]:
        headings = soup.find_all(["h1", "h2", "h3", "h4", "h5"])
        for h in headings:
            text = h.get_text(strip=True) if hasattr(h, "get_text") else ""
            if title in text:
                found_titles += 1
                break

    result.metrics["sample_titles_found"] = f"{found_titles}/{len(GROUND_TRUTH['known_chapter_titles'])}"
    if found_titles < len(GROUND_TRUTH["known_chapter_titles"]) * 0.5:
        result.warnings.append(
            f"已知章节标题仅找到 {found_titles}/{len(GROUND_TRUTH['known_chapter_titles'])}，"
            f"可能章节提取不完整"
        )

    # --- 7. 注释区域 ---
    notes_section = soup.find(attrs={"data-role": "notes"})
    if GROUND_TRUTH["has_notes_section"] and not notes_section:
        result.warnings.append("未找到 section[data-role='notes'] 注释区域")

    # --- 8. unknown blocks ---
    unknowns = soup.find_all(attrs={"data-role": "unknown"})
    result.metrics["unknown_blocks"] = len(unknowns)

    return result


def evaluate_structure_json(json_path: str, result: EvalResult):
    """额外检查 structure.json 的正确性"""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        chapters = data.get("chapters", [])
        result.metrics["structure_json_chapters"] = len(chapters)

        total_note_refs = sum(c.get("note_ref_count", 0) for c in chapters)
        result.metrics["structure_json_note_refs"] = total_note_refs

        stats = data.get("stats", {})
        result.metrics["structure_json_paragraphs"] = stats.get("paragraph_count", "?")

    except FileNotFoundError:
        result.warnings.append("structure.json 不存在")
    except json.JSONDecodeError:
        result.errors.append("structure.json 格式错误")
