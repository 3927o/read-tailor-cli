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
    mode: &str,
) -> String {
    let mut output = String::new();
    let _ = writeln!(output, "# Interview\n");
    let _ = writeln!(output, "- 书名：{}", structure.document.title);
    let _ = writeln!(output, "- 章节数：{}", structure.chapters.len());
    let _ = writeln!(output, "- 模式：{}", mode);
    let _ = writeln!(output, "- 问题数：{}", answers.len());
    output.push('\n');
    for (index, answer) in answers.iter().enumerate() {
        let _ = writeln!(output, "## Q{}\n", index + 1);
        let _ = writeln!(output, "- 问题：{}", answer.question);
        let _ = writeln!(output, "- 回答：{}\n", answer.answer);
    }
    output
}

pub(crate) fn render_strategy_markdown(title: &str, answers: &[InterviewAnswer]) -> String {
    let goal = find_answer_by_keywords(
        answers,
        &["目标", "目的", "想获得", "主要想看", "为什么"],
        &["主线", "关键观点", "理解", "梳理", "精读", "综述"],
    )
    .unwrap_or_else(|| "提升阅读流畅度并更快抓住核心内容".to_string());
    let focus = find_answer_by_keywords(
        answers,
        &[
            "处理重点",
            "内容组织",
            "保留",
            "层级",
            "标题",
            "章节",
            "结构",
        ],
        &["层次", "结构", "重点", "主线", "细节", "摘要", "导读"],
    )
    .unwrap_or_else(|| "保留核心结构并减少干扰信息".to_string());
    let note_policy = if answers.iter().any(|item| {
        contains_any(&item.question, &["注释", "脚注", "尾注"])
            || contains_any(&item.answer, &["文末", "集中查看", "集中", "统一查看"])
    }) && answers
        .iter()
        .any(|item| contains_any(&item.answer, &["文末", "集中查看", "集中"]))
    {
        "endnotes"
    } else if answers
        .iter()
        .any(|item| contains_any(&item.answer, &["预览", "提示", "就地", "悬浮"]))
    {
        "preview"
    } else {
        "minimal"
    };
    let heading_policy = if answers.iter().any(|item| {
        contains_any(&item.question, &["标题", "章节", "层级", "小节"])
            || contains_any(
                &item.answer,
                &["保留章节", "保留小节", "保留层级", "细标题", "层次"],
            )
    }) {
        "preserve-sections"
    } else {
        "chapter-first"
    };
    let mut enhancements = Vec::new();
    if answers
        .iter()
        .any(|item| contains_any(&item.answer, &["摘要", "导读", "提要"]))
    {
        enhancements.push("chapter-guides".to_string());
    }
    if answers
        .iter()
        .any(|item| contains_any(&item.answer, &["索引", "检索", "目录导航"]))
    {
        enhancements.push("index".to_string());
    }
    let strategy = StrategyData {
        title: title.to_string(),
        processing_goal: goal,
        processing_focus: focus,
        note_policy: note_policy.to_string(),
        heading_policy: heading_policy.to_string(),
        enhancements: enhancements.clone(),
        reading_scenario: find_answer_by_keywords(
            answers,
            &["阅读场景", "什么场景", "设备", "终端"],
            &["手机", "桌面", "打印", "导出", "碎片阅读"],
        )
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
        let mut text = Text::new(prompt);
        if !default.trim().is_empty() {
            text = text.with_default(default);
        }
        let value = text
            .prompt()
            .with_context(|| format!("failed to ask: {prompt}"))?;
        Ok(value)
    } else {
        Ok(default.to_string())
    }
}

pub(crate) fn ask_choice_with_custom(
    interactive: bool,
    prompt: &str,
    options: &[String],
    default: &str,
) -> Result<String> {
    if !interactive {
        return Ok(default
            .trim()
            .to_string()
            .if_empty_then(|| options.first().cloned().unwrap_or_default()));
    }

    let custom_label = "自定义输入...".to_string();
    let mut choices = options
        .iter()
        .filter(|item| !item.trim().is_empty())
        .cloned()
        .collect::<Vec<_>>();
    if choices.is_empty() {
        choices.push(default.to_string());
    }
    if !choices.iter().any(|item| item == &custom_label) {
        choices.push(custom_label.clone());
    }

    let selected = Select::new(prompt, choices)
        .prompt()
        .with_context(|| format!("failed to ask: {prompt}"))?;
    if selected == custom_label {
        return ask_text(true, "请输入你的自定义回答", default);
    }
    Ok(selected)
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
        let safe = ch.is_alphanumeric() || matches!(ch, '_' | '-' | '.');
        if safe {
            for lower in ch.to_lowercase() {
                slug.push(lower);
            }
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

fn find_answer_by_keywords(
    answers: &[InterviewAnswer],
    question_keywords: &[&str],
    answer_keywords: &[&str],
) -> Option<String> {
    answers
        .iter()
        .find(|item| {
            contains_any(&item.question, question_keywords)
                || contains_any(&item.answer, answer_keywords)
        })
        .map(|item| item.answer.clone())
}

fn contains_any(value: &str, keywords: &[&str]) -> bool {
    keywords.iter().any(|keyword| value.contains(keyword))
}

trait IfEmptyThen {
    fn if_empty_then<F>(self, fallback: F) -> Self
    where
        F: FnOnce() -> Self;
}

impl IfEmptyThen for String {
    fn if_empty_then<F>(self, fallback: F) -> Self
    where
        F: FnOnce() -> Self,
    {
        if self.is_empty() { fallback() } else { self }
    }
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
