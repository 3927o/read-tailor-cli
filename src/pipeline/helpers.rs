use std::{fmt::Write as _, fs, path::Path};

use anyhow::{Context, Result, anyhow, bail};
use inquire::{Select, Text};
use kuchiki::{NodeRef, parse_html, traits::TendrilSink};
use regex::Regex;

use crate::cli::{RunArgs, Step};

use super::types::{
    ChapterSummary, DocumentMeta, InterviewAnswer, Landmarks, NotesFile, SectionSummary, Stats,
    StrategyData, StructureSummary, UnknownBlock,
};

pub(crate) fn write_summary(
    context: &super::types::RunContext,
    script_stdout: &str,
    transform_source: &str,
) -> Result<()> {
    let strategy = parse_strategy_data(&fs::read_to_string(&context.artifacts.strategy_md)?)?;
    let notes: NotesFile =
        serde_json::from_str(&fs::read_to_string(&context.artifacts.notes_json)?)?;
    let summary = format!(
        "# Summary\n\n- 输入文件：`{}`\n- 输出目录：`{}`\n- 工作目录：`{}`\n- 处理目标：{}\n- 处理重点：{}\n- 注释策略：{}\n- 标题策略：{}\n- 增强内容：{}\n- 阅读场景：{}\n- 注释数量：{}\n- transform.py 来源：{}\n\n## Transform Output\n\n```text\n{}\n```\n",
        context.args.input.display(),
        context.output_dir.display(),
        context.work_dir.display(),
        strategy.processing_goal,
        strategy.processing_focus,
        strategy.note_policy,
        strategy.heading_policy,
        if strategy.enhancements.is_empty() {
            "无".to_string()
        } else {
            strategy.enhancements.join("、")
        },
        strategy.reading_scenario,
        notes.notes.len(),
        transform_source,
        script_stdout.trim()
    );
    fs::write(&context.artifacts.summary_md, summary)
        .with_context(|| format!("failed to write {}", context.artifacts.summary_md.display()))
}

pub(crate) fn write_run_log(context: &super::types::RunContext) -> Result<()> {
    fs::write(&context.artifacts.run_log, context.log_lines.join("\n\n"))
        .with_context(|| format!("failed to write {}", context.artifacts.run_log.display()))
}

pub(crate) fn render_normalize_report(summary: &StructureSummary, script_source: &str) -> String {
    format!(
        "# Normalize Report\n\n- 脚本来源：{}\n- 章节数：{}\n- 小节数：{}\n- 段落数：{}\n- 是否含目录：{}\n- 是否含注释区：{}\n\n## 保留策略\n\n- 无法稳定识别的区域保留为 `div[data-role=\"unknown\"]`\n- 仅对常见 Pandoc 注释结构做了标准化，特殊结构仍可能保留原样\n- 后续步骤以 `book.normalized.html` 和 `structure.json` 为准\n",
        script_source,
        summary.stats.chapter_count,
        summary.stats.section_count,
        summary.stats.paragraph_count,
        summary.landmarks.has_toc,
        summary.landmarks.has_notes_section
    )
}

pub(crate) fn summarize_structure(path: &Path) -> Result<StructureSummary> {
    let html = fs::read_to_string(path)
        .with_context(|| format!("failed to read normalized html {}", path.display()))?;
    let document = parse_html().one(html);

    let title = document
        .select_first("title")
        .ok()
        .map(|node| compact_text(&node.text_contents()))
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "Untitled".to_string());
    let language = document
        .select_first("html")
        .ok()
        .and_then(|node| node.attributes.borrow().get("lang").map(ToOwned::to_owned))
        .unwrap_or_else(|| "und".to_string());
    let has_toc = document.select_first("nav#toc[data-role=\"toc\"]").is_ok();
    let has_notes_section = document
        .select_first("section[data-role=\"notes\"]")
        .is_ok();

    let mut chapters = Vec::new();
    let mut all_unknown = Vec::new();
    let mut total_sections = 0usize;
    let mut total_paragraphs = 0usize;

    if let Ok(selection) =
        document.select("section#bodymatter > section.chapter[data-type=\"chapter\"]")
    {
        for (index, chapter) in selection.enumerate() {
            let attrs = chapter.attributes.borrow();
            let chapter_id = attrs.get("id").unwrap_or("").to_string();
            drop(attrs);

            let title = chapter
                .as_node()
                .select_first("h1")
                .ok()
                .map(|node| compact_text(&node.text_contents()))
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| format!("Chapter {}", index + 1));

            let paragraph_count = chapter
                .as_node()
                .select("p")
                .map(|it| it.count())
                .unwrap_or(0);
            let note_ref_count = chapter
                .as_node()
                .select("a[data-role=\"noteref\"]")
                .map(|it| it.count())
                .unwrap_or(0);
            let unknown_count = chapter
                .as_node()
                .select("[data-role=\"unknown\"]")
                .map(|it| it.count())
                .unwrap_or(0);
            total_paragraphs += paragraph_count;

            let mut sections = Vec::new();
            if let Ok(headings) = chapter.as_node().select("h2, h3, h4") {
                for (section_index, heading) in headings.enumerate() {
                    let element = heading.as_node().as_element().unwrap();
                    let heading_level = match element.name.local.as_ref() {
                        "h2" => 2,
                        "h3" => 3,
                        _ => 4,
                    };
                    sections.push(SectionSummary {
                        id: format!("{}-sec{}", chapter_id, section_index + 1),
                        title: compact_text(&heading.text_contents()),
                        heading_level,
                        index: section_index + 1,
                    });
                }
            }
            total_sections += sections.len();

            if let Ok(unknowns) = chapter.as_node().select("[data-role=\"unknown\"]") {
                for (unknown_index, unknown) in unknowns.enumerate() {
                    let attrs = unknown.attributes.borrow();
                    all_unknown.push(UnknownBlock {
                        id: attrs
                            .get("id")
                            .map(ToOwned::to_owned)
                            .unwrap_or_else(|| format!("unknown-{:04}", all_unknown.len() + 1)),
                        chapter_id: chapter_id.clone(),
                        index: unknown_index + 1,
                        reason: attrs
                            .get("data-reason")
                            .map(ToOwned::to_owned)
                            .unwrap_or_else(|| "ambiguous-structure".to_string()),
                        text_preview: preview(&unknown.text_contents(), 30),
                    });
                }
            }

            chapters.push(ChapterSummary {
                id: chapter_id,
                index: index + 1,
                title,
                section_count: sections.len(),
                paragraph_count,
                note_ref_count,
                unknown_block_count: unknown_count,
                sections,
            });
        }
    }

    let chapter_count = chapters.len();
    Ok(StructureSummary {
        version: "1.0".to_string(),
        document: DocumentMeta { title, language },
        landmarks: Landmarks {
            book_main_id: "book".to_string(),
            bodymatter_id: "bodymatter".to_string(),
            toc_id: "toc".to_string(),
            has_toc,
            has_notes_section,
        },
        chapters,
        unknown_blocks: all_unknown,
        stats: Stats {
            chapter_count,
            section_count: total_sections,
            paragraph_count: total_paragraphs,
        },
    })
}

pub(crate) fn build_raw_outline(html: &str) -> Result<String> {
    let document = parse_html().one(html.to_string());
    let mut output = String::from("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<outline>\n");
    let body = document
        .select_first("body")
        .map_err(|_| anyhow!("raw HTML is missing <body>"))?;
    for child in body.as_node().children() {
        render_outline_node(&child, 1, &mut output);
    }
    output.push_str("</outline>\n");
    Ok(output)
}

fn render_outline_node(node: &NodeRef, depth: usize, output: &mut String) {
    if let Some(element) = node.as_element() {
        let indent = "  ".repeat(depth);
        let tag = element.name.local.to_string();
        let attrs = render_xml_attributes(element.attributes.borrow().map.iter());
        let child_elements: Vec<_> = node
            .children()
            .filter(|child| child.as_element().is_some())
            .collect();

        if child_elements.is_empty() {
            if tag == "p" {
                let _ = writeln!(output, "{}<{}{} />", indent, tag, attrs);
                return;
            }

            let text_preview = if matches!(tag.as_str(), "h1" | "h2" | "h3" | "h4") {
                compact_text(&node.text_contents())
            } else {
                preview(&node.text_contents(), 30)
            };

            if text_preview.is_empty() {
                let _ = writeln!(output, "{}<{}{} />", indent, tag, attrs);
            } else {
                let _ = writeln!(
                    output,
                    "{}<{}{}>{}</{}>",
                    indent,
                    tag,
                    attrs,
                    escape_xml(&text_preview),
                    tag
                );
            }
            return;
        }

        let _ = writeln!(output, "{}<{}{}>", indent, tag, attrs);
        for child in child_elements {
            render_outline_node(&child, depth + 1, output);
        }
        let _ = writeln!(output, "{}</{}>", indent, tag);
    }
}

fn render_xml_attributes<'a, I>(attributes: I) -> String
where
    I: IntoIterator<Item = (&'a kuchiki::ExpandedName, &'a kuchiki::Attribute)>,
{
    let mut rendered = String::new();
    for (name, attribute) in attributes {
        if is_base64_data_uri(&attribute.value) {
            continue;
        }
        let _ = write!(
            rendered,
            " {}=\"{}\"",
            name.local.as_ref(),
            escape_xml(&attribute.value)
        );
    }
    rendered
}

fn is_base64_data_uri(value: &str) -> bool {
    let trimmed = value.trim_start();
    trimmed.starts_with("data:") && trimmed.contains(";base64,")
}

pub(crate) fn render_interview_markdown(
    structure: &StructureSummary,
    answers: &[InterviewAnswer],
    interactive: bool,
) -> String {
    let mut output = String::new();
    let _ = writeln!(output, "# Interview\n");
    let _ = writeln!(output, "- 书名：{}", structure.document.title);
    let _ = writeln!(output, "- 章节数：{}", structure.chapters.len());
    let _ = writeln!(
        output,
        "- 模式：{}",
        if interactive {
            "interactive"
        } else {
            "auto-default"
        }
    );
    output.push('\n');
    for (index, answer) in answers.iter().enumerate() {
        let _ = writeln!(output, "## Q{}\n", index + 1);
        let _ = writeln!(output, "- 问题：{}", answer.question);
        let _ = writeln!(output, "- 回答：{}\n", answer.answer);
    }
    output
}

pub(crate) fn render_strategy_markdown(title: &str, answers: &[InterviewAnswer]) -> String {
    let note_policy = if answers.iter().any(|item| item.answer.contains("集中查看")) {
        "endnotes"
    } else if answers.iter().any(|item| item.answer.contains("预览")) {
        "preview"
    } else {
        "minimal"
    };
    let heading_policy = if answers
        .iter()
        .any(|item| item.answer.contains("保留") && item.answer.contains("标题"))
    {
        "preserve-sections"
    } else {
        "chapter-first"
    };
    let enhancements = if answers
        .iter()
        .any(|item| item.answer.contains("摘要") || item.answer.contains("导读"))
    {
        vec!["chapter-guides".to_string()]
    } else {
        Vec::new()
    };
    let strategy = StrategyData {
        title: title.to_string(),
        processing_goal: answers
            .first()
            .map(|item| item.answer.clone())
            .unwrap_or_else(|| "提升阅读流畅度".to_string()),
        processing_focus: answers
            .get(2)
            .map(|item| item.answer.clone())
            .unwrap_or_else(|| "保留核心结构并减少干扰".to_string()),
        note_policy: note_policy.to_string(),
        heading_policy: heading_policy.to_string(),
        enhancements: enhancements.clone(),
        reading_scenario: answers
            .iter()
            .find(|item| item.question.contains("阅读场景"))
            .map(|item| item.answer.clone())
            .unwrap_or_else(|| "桌面精读".to_string()),
    };

    format!(
        "# Strategy\n\n- 处理目标：{}\n- 处理重点：{}\n- 注释处理：{}\n- 标题处理：{}\n- 增强内容：{}\n- 输出阅读场景：{}\n\n```json\n{}\n```\n",
        strategy.processing_goal,
        strategy.processing_focus,
        strategy.note_policy,
        strategy.heading_policy,
        if strategy.enhancements.is_empty() {
            "无".to_string()
        } else {
            strategy.enhancements.join("、")
        },
        strategy.reading_scenario,
        serde_json::to_string_pretty(&strategy).unwrap_or_else(|_| "{}".to_string())
    )
}

pub(crate) fn ask_text(interactive: bool, prompt: &str, default: &str) -> Result<String> {
    if interactive {
        let value = Text::new(prompt)
            .with_default(default)
            .prompt()
            .with_context(|| format!("failed to ask: {prompt}"))?;
        Ok(value)
    } else {
        Ok(default.to_string())
    }
}

pub(crate) fn ask_select(
    interactive: bool,
    prompt: &str,
    options: Vec<&str>,
    default: &str,
) -> Result<String> {
    if interactive {
        let value = Select::new(prompt, options.clone())
            .prompt()
            .with_context(|| format!("failed to ask: {prompt}"))?;
        Ok(value.to_string())
    } else {
        Ok(default.to_string())
    }
}

pub(crate) fn parse_strategy_data(markdown: &str) -> Result<StrategyData> {
    let regex = Regex::new(r"```json\s*(\{[\s\S]*?\})\s*```")?;
    let payload = regex
        .captures(markdown)
        .and_then(|captures| captures.get(1))
        .map(|item| item.as_str())
        .context("strategy.md does not contain a JSON block")?;
    serde_json::from_str(payload).context("failed to parse strategy JSON block")
}

pub(crate) fn inner_html(node: &NodeRef) -> String {
    node.children()
        .map(|child| child.to_string())
        .collect::<Vec<_>>()
        .join("")
}

pub(crate) fn find_chapter_id(node: &NodeRef) -> String {
    for ancestor in node.ancestors() {
        if let Some(element) = ancestor.as_element() {
            let attrs = element.attributes.borrow();
            if element.name.local.as_ref() == "section" && attrs.get("data-type") == Some("chapter")
            {
                return attrs.get("id").unwrap_or("unknown").to_string();
            }
        }
    }
    "unknown".to_string()
}

pub(crate) fn preview(text: &str, length: usize) -> String {
    text.chars().take(length).collect::<String>()
}

pub(crate) fn compact_text(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

pub(crate) fn escape_xml(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

pub(crate) fn slugify(value: &str) -> String {
    let mut slug = String::new();
    let mut last_dash = false;
    for ch in value.chars() {
        if ch.is_ascii_alphanumeric() {
            slug.push(ch.to_ascii_lowercase());
            last_dash = false;
        } else if !last_dash {
            slug.push('-');
            last_dash = true;
        }
    }
    slug.trim_matches('-').to_string()
}

pub(crate) fn relative_display(path: &Path, base: &Path) -> String {
    path.strip_prefix(base)
        .map(|item| item.display().to_string())
        .unwrap_or_else(|_| path.display().to_string())
}

pub(crate) fn ensure_exists(path: &Path) -> Result<()> {
    if !path.exists() {
        bail!("required artifact is missing: {}", path.display());
    }
    Ok(())
}

pub(crate) fn select_steps(args: &RunArgs) -> Vec<Step> {
    if let Some(step) = args.step {
        return vec![step];
    }

    let start = args.resume_from.unwrap_or(Step::Step1);
    Step::all()
        .into_iter()
        .filter(|step| *step >= start)
        .collect()
}
