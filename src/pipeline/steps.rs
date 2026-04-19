use std::{collections::BTreeMap, fs, io::IsTerminal, process::Command};

use anyhow::{Context, Result, bail};
use kuchiki::{parse_html, traits::TendrilSink};
use serde::Deserialize;
use serde_json::json;

use crate::{ai, cli::Step, config::resolve_ai_config};

use super::{
    helpers::{
        ask_choice_with_custom, build_raw_outline, compact_text, ensure_exists, find_chapter_id,
        inner_html, parse_strategy_data, relative_display, render_interview_markdown,
        render_normalize_report, render_strategy_markdown, summarize_structure, write_summary,
    },
    templates::{fallback_normalize_script, fallback_transform_script},
    types::{
        AnchorMutation, InterviewAnswer, NoteContent, NotePosition, NoteRecord, NoteRefRecord,
        NoteSource, NotesFile, RunContext, StructureSummary,
    },
};

fn python_cmd() -> &'static str {
    if Command::new("python3").arg("--version").output().is_ok() {
        "python3"
    } else {
        "python"
    }
}

pub(super) fn step1_convert_epub(context: &mut RunContext) -> Result<String> {
    let output = Command::new("pandoc")
        .arg(&context.args.input)
        .arg("-f")
        .arg("epub")
        .arg("-t")
        .arg("html")
        .arg("-s")
        .arg("--embed-resources")
        .arg("-o")
        .arg(&context.artifacts.raw_html)
        .output()
        .context("failed to invoke pandoc")?;

    context.log_lines.push(format!(
        "[pandoc stdout]\n{}",
        String::from_utf8_lossy(&output.stdout)
    ));
    context.log_lines.push(format!(
        "[pandoc stderr]\n{}",
        String::from_utf8_lossy(&output.stderr)
    ));

    if !output.status.success() {
        bail!(
            "pandoc failed with status {}",
            output.status.code().unwrap_or_default()
        );
    }

    Ok(format!(
        "wrote {}",
        relative_display(&context.artifacts.raw_html, &context.root_dir)
    ))
}

pub(super) async fn step2_normalize(context: &mut RunContext) -> Result<String> {
    ensure_exists(&context.artifacts.raw_html)?;

    let raw_html = fs::read_to_string(&context.artifacts.raw_html)
        .with_context(|| format!("failed to read {}", context.artifacts.raw_html.display()))?;
    let outline = build_raw_outline(&raw_html)?;
    fs::write(&context.artifacts.raw_outline, outline).with_context(|| {
        format!(
            "failed to write {}",
            context.artifacts.raw_outline.display()
        )
    })?;

    let mut ai_status = "fallback";
    let mut normalize_script = fallback_normalize_script();
    if let Some(ai_config) = resolve_ai_config(&context.config, Step::Step2)? {
        let system = r#"You are a senior HTML-normalization engineer. Your job is to emit a single, standalone Python 3 script that normalizes raw book HTML into a standard book HTML.

Output requirements:
- Return ONLY the Python source code.
- Do NOT wrap it in markdown fences, do NOT add commentary, do NOT add any preamble or epilogue text.

Script contract:
- Usage: python normalize.py INPUT OUTPUT
- INPUT is a single-file raw HTML produced by pandoc from an EPUB.
- OUTPUT is the normalized standard HTML to be written.

Runtime environment (MUST follow):
- Parse and build HTML using beautifulsoup4 only: `from bs4 import BeautifulSoup, NavigableString, Tag`.
- Use the built-in `html.parser` backend: `BeautifulSoup(text, "html.parser")`. Do NOT require `lxml` or `html5lib`.
- Do NOT use `xml.etree.ElementTree`, `xml.dom`, `lxml`, `html5lib`, or regex-based HTML rewriting for structural work. Raw HTML from pandoc is not well-formed XML and XML parsers will fail on it.
- Only stdlib + `bs4` may be imported. No other third-party packages.
- On malformed input, emit an error to stderr and exit with code 1 rather than silently producing empty output.

Document skeleton the script must produce:
- main#book[data-type="book"]
- section#bodymatter[data-role="bodymatter"]
- chapters as section.chapter[data-type="chapter"][id]
- chapter title must be the first h1 inside each chapter
- sub-sections as section[data-type="section"] with heading levels h2..h4
- body paragraphs unified as p
- table of contents as nav#toc[data-role="toc"]
- preserve uncertain content as div[data-role="unknown"] (never silently drop)

Note normalization (Step 3 performs pure extraction and depends on this):
- collect every note body (footnote / endnote / chapter-end note) into a single section[data-role="notes"]
- each note body must be [data-role="note"][id]
- when the note kind is identifiable, record it via data-note-kind attribute (e.g. footnote / endnote / chapter-note)
- all in-text note references must be normalized to a[data-role="noteref"][href][id], where href targets the note body id
- if a note is referenced multiple times, keep exactly ONE note body and let multiple noterefs point to the same id (do NOT duplicate note bodies)
- both the in-text noteref and any back-reference inside the note body must use explicit href/id pairs so references can be mapped unambiguously
- preserve the original HTML fragment inside each note body verbatim (nested notes, cross-refs, block structures must NOT be flattened)
- if a note cannot be reliably classified or relocated into section[data-role="notes"], downgrade to div[data-role="unknown"] instead of dropping it

General rules:
- keep content lossless; never silently delete body text
- prefer semantic accuracy over aggressive rewriting"#;
        let user = format!(
            r#"Generate normalize.py for a book whose structural outline is shown below. Base your logic on this outline only; do not try to fetch or invent additional context.

<structural_outline>
{}
</structural_outline>
"#,
            fs::read_to_string(&context.artifacts.raw_outline)?
        );

        let debug = ai::complete_with_debug(&ai_config, system, &user).await;
        write_ai_trace_files(context, "step2", system, &user, &debug)?;
        log_ai_raw_response(context, "step2", &debug);

        match debug.extracted_content.clone() {
            Some(script) => {
                normalize_script = format!("# generated by AI\n{script}");
                ai_status = "ai";
            }
            None => {
                context.log_lines.push(format!(
                    "[warn] step2 AI generation failed: {}",
                    debug.error.unwrap_or_else(|| "unknown error".to_string())
                ));
                context.log_lines.push(format!(
                    "[warn] step2 AI diagnostics: {}, {}, {}",
                    context.work_dir.join("step2.ai.prompt.md").display(),
                    context.work_dir.join("step2.ai.response.txt").display(),
                    context.work_dir.join("step2.ai.debug.json").display()
                ));
            }
        }
    }

    fs::write(&context.artifacts.normalize_py, normalize_script).with_context(|| {
        format!(
            "failed to write generated script {}",
            context.artifacts.normalize_py.display()
        )
    })?;

    let run = Command::new(python_cmd())
        .arg(&context.artifacts.normalize_py)
        .arg(&context.artifacts.raw_html)
        .arg(&context.artifacts.normalized_html)
        .output()
        .context("failed to execute normalize.py")?;

    context.log_lines.push(format!(
        "[normalize stdout]\n{}",
        String::from_utf8_lossy(&run.stdout)
    ));
    context.log_lines.push(format!(
        "[normalize stderr]\n{}",
        String::from_utf8_lossy(&run.stderr)
    ));

    if !run.status.success() {
        if ai_status == "ai" {
            context.log_lines.push(
                "[warn] AI-generated normalize.py failed; switching to fallback template".into(),
            );
            fs::write(&context.artifacts.normalize_py, fallback_normalize_script())?;
            let rerun = Command::new(python_cmd())
                .arg(&context.artifacts.normalize_py)
                .arg(&context.artifacts.raw_html)
                .arg(&context.artifacts.normalized_html)
                .output()
                .context("failed to execute fallback normalize.py")?;

            context.log_lines.push(format!(
                "[normalize fallback stdout]\n{}",
                String::from_utf8_lossy(&rerun.stdout)
            ));
            context.log_lines.push(format!(
                "[normalize fallback stderr]\n{}",
                String::from_utf8_lossy(&rerun.stderr)
            ));

            if !rerun.status.success() {
                bail!("normalize.py failed after fallback");
            }
            ai_status = "fallback";
        } else {
            bail!("normalize.py failed");
        }
    }

    let summary = summarize_structure(&context.artifacts.normalized_html)?;
    fs::write(
        &context.artifacts.structure_json,
        serde_json::to_string_pretty(&summary)?,
    )
    .with_context(|| {
        format!(
            "failed to write {}",
            context.artifacts.structure_json.display()
        )
    })?;

    let report = render_normalize_report(&summary, ai_status);
    fs::write(&context.artifacts.normalize_report, report).with_context(|| {
        format!(
            "failed to write {}",
            context.artifacts.normalize_report.display()
        )
    })?;

    Ok(format!(
        "generated normalized HTML, structure summary and report via {ai_status}"
    ))
}

pub(super) fn step3_extract_notes(context: &mut RunContext) -> Result<String> {
    ensure_exists(&context.artifacts.normalized_html)?;

    let html = fs::read_to_string(&context.artifacts.normalized_html).with_context(|| {
        format!(
            "failed to read normalized html {}",
            context.artifacts.normalized_html.display()
        )
    })?;
    let document = parse_html().one(html);

    let mut refs_by_target: BTreeMap<String, Vec<AnchorMutation>> = BTreeMap::new();
    let mut ref_order = 0usize;
    if let Ok(selection) = document.select("a[data-role=\"noteref\"]") {
        for anchor in selection {
            ref_order += 1;
            let mut attrs = anchor.attributes.borrow_mut();
            let source_href = attrs.get("href").unwrap_or("").to_string();
            let target = source_href.trim_start_matches('#').to_string();
            let source_anchor_id = attrs
                .get("id")
                .map(ToOwned::to_owned)
                .unwrap_or_else(|| format!("ref-{ref_order:04}"));
            if attrs.get("id").is_none() {
                attrs.insert("id", source_anchor_id.clone());
            }
            drop(attrs);

            refs_by_target
                .entry(target)
                .or_default()
                .push(AnchorMutation {
                    node: anchor.as_node().clone(),
                    record: NoteRefRecord {
                        ref_id: format!("ref-{ref_order:04}"),
                        source_anchor_id,
                        source_href,
                        chapter_id: find_chapter_id(anchor.as_node()),
                        order: ref_order,
                    },
                });
        }
    }

    let notes_section = document.select_first("section[data-role=\"notes\"]").ok();
    if notes_section.is_none() {
        let notes_file = NotesFile {
            version: "1.0".to_string(),
            id_scheme: "note-seq".to_string(),
            notes: Vec::new(),
        };
        fs::write(
            &context.artifacts.notes_json,
            serde_json::to_string_pretty(&notes_file)?,
        )?;
        return Ok("no standardized notes section found; wrote empty notes.json".to_string());
    }
    let notes_section = notes_section.unwrap();
    let notes_section_id = notes_section
        .attributes
        .borrow()
        .get("id")
        .unwrap_or("book-notes")
        .to_string();

    let mut note_nodes = Vec::new();
    if let Ok(selection) = notes_section.as_node().select("[data-role=\"note\"]") {
        for note in selection {
            note_nodes.push(note);
        }
    }

    let mut extracted = Vec::new();
    for (index, note) in note_nodes.into_iter().enumerate() {
        let order = index + 1;
        let new_id = format!("note-{order:04}");
        let mut attrs = note.attributes.borrow_mut();
        let original_note_id = attrs.get("id").unwrap_or("").to_string();
        attrs.insert("id", new_id.clone());
        attrs.insert("data-original-id", original_note_id.clone());
        let kind = attrs
            .get("data-note-kind")
            .or_else(|| attrs.get("data-kind"))
            .or_else(|| attrs.get("class"))
            .map(ToOwned::to_owned)
            .unwrap_or_else(|| "unknown".to_string());
        drop(attrs);

        let refs = refs_by_target.remove(&original_note_id).unwrap_or_default();
        for anchor in &refs {
            let mut attrs = anchor.node.as_element().unwrap().attributes.borrow_mut();
            attrs.insert("href", format!("#{new_id}"));
            attrs.insert("data-note-id", new_id.clone());
        }

        let chapter_id = refs
            .first()
            .map(|item| item.record.chapter_id.clone())
            .unwrap_or_else(|| "unknown".to_string());

        extracted.push(NoteRecord {
            id: new_id,
            kind,
            chapter_id,
            order,
            source: NoteSource {
                original_note_id: original_note_id.clone(),
                original_href_target: format!("#{original_note_id}"),
            },
            refs: refs.into_iter().map(|item| item.record).collect(),
            content: NoteContent {
                html: inner_html(note.as_node()),
                text: compact_text(&note.text_contents()),
            },
            position: NotePosition {
                notes_section_id: notes_section_id.clone(),
                index_in_notes_section: order,
            },
        });
    }

    let note_children: Vec<_> = notes_section.as_node().children().collect();
    for child in note_children {
        child.detach();
    }
    notes_section
        .attributes
        .borrow_mut()
        .insert("data-extracted", "true".to_string());

    let notes_file = NotesFile {
        version: "1.0".to_string(),
        id_scheme: "note-seq".to_string(),
        notes: extracted,
    };

    fs::write(
        &context.artifacts.notes_json,
        serde_json::to_string_pretty(&notes_file)?,
    )
    .with_context(|| format!("failed to write {}", context.artifacts.notes_json.display()))?;
    fs::write(&context.artifacts.normalized_html, document.to_string()).with_context(|| {
        format!(
            "failed to update normalized html {}",
            context.artifacts.normalized_html.display()
        )
    })?;

    Ok(format!("extracted {} notes", notes_file.notes.len()))
}

pub(super) async fn step4_interview(context: &mut RunContext) -> Result<String> {
    ensure_exists(&context.artifacts.structure_json)?;

    let structure: StructureSummary = serde_json::from_str(
        &fs::read_to_string(&context.artifacts.structure_json).with_context(|| {
            format!(
                "failed to read {}",
                context.artifacts.structure_json.display()
            )
        })?,
    )
    .context("failed to parse structure.json")?;

    let interactive = std::io::stdin().is_terminal();
    let mut answers = Vec::new();
    let mut interview_mode = if interactive {
        "fallback-fixed".to_string()
    } else {
        "auto-default".to_string()
    };

    if interactive {
        if let Some(ai_config) = resolve_ai_config(&context.config, Step::Step4)? {
            interview_mode = "ai-dynamic".to_string();
            let interview_result =
                run_dynamic_interview(context, &structure, &ai_config, interactive).await;
            match interview_result {
                Ok(dynamic_answers) => {
                    answers = dynamic_answers;
                }
                Err(error) => {
                    context.log_lines.push(format!(
                        "[warn] step4 AI interview failed, falling back to fixed questions: {error:#}"
                    ));
                    interview_mode = "ai-dynamic+fallback-fixed".to_string();
                }
            }
        }
    }

    if answers.len() < 3 {
        answers = collect_fallback_interview_answers(&structure, interactive, answers)?;
    }

    let interview_md = render_interview_markdown(&structure, &answers, &interview_mode);
    fs::write(&context.artifacts.interview_md, interview_md).with_context(|| {
        format!(
            "failed to write {}",
            context.artifacts.interview_md.display()
        )
    })?;

    let fallback_strategy = render_strategy_markdown(&structure.document.title, &answers);
    let strategy = if let Some(ai_config) = resolve_ai_config(&context.config, Step::Step4)? {
        let system = r#"You are a reading-experience strategist. Given a book's structural summary and a short user interview, produce a concise reading strategy in Simplified Chinese markdown.

Output contract:
- The entire response must be in Simplified Chinese markdown.
- The strategy description must be human-readable, concise, and focused on decisions that drive the downstream processing script.
- At the very end of the response, append ONE fenced JSON code block (```json ... ```) containing the machine-readable strategy with these required keys:
  - title
  - processing_goal
  - processing_focus
  - note_policy
  - heading_policy
  - enhancements
  - reading_scenario
- Do NOT add any content after that JSON block.
- Do NOT wrap the entire response in a single outer code fence.

Content guidance:
- The strategy must clearly state: processing goal, processing focus, how notes are handled, how titles / chapters are handled, what enhancement content (summaries, reading guides, indices) is added, and what reading scenarios the output targets.
- Do NOT invent facts that are not implied by the structural summary or the interview."#;
        let user = format!(
            r#"Use the following inputs to produce strategy.md.

<book_structure>
{}
</book_structure>

<interview>
{}
</interview>
"#,
            fs::read_to_string(&context.artifacts.structure_json)?,
            fs::read_to_string(&context.artifacts.interview_md)?,
        );
        let debug = ai::complete_with_debug(&ai_config, system, &user).await;
        write_ai_trace_files(context, "step4", system, &user, &debug)?;
        log_ai_raw_response(context, "step4", &debug);

        match debug.extracted_content.clone() {
            Some(strategy) => strategy,
            None => {
                context.log_lines.push(format!(
                    "[warn] step4 AI strategy generation failed: {}",
                    debug.error.unwrap_or_else(|| "unknown error".to_string())
                ));
                fallback_strategy.clone()
            }
        }
    } else {
        fallback_strategy.clone()
    };
    let strategy = if parse_strategy_data(&strategy).is_ok() {
        strategy
    } else {
        context.log_lines.push(
            "[warn] strategy.md was missing a valid JSON block; fallback strategy applied".into(),
        );
        fallback_strategy
    };

    fs::write(&context.artifacts.strategy_md, strategy).with_context(|| {
        format!(
            "failed to write {}",
            context.artifacts.strategy_md.display()
        )
    })?;

    Ok(format!(
        "recorded {} interview answers and generated strategy",
        answers.len()
    ))
}

async fn run_dynamic_interview(
    context: &mut RunContext,
    structure: &StructureSummary,
    ai_config: &crate::config::ResolvedAiConfig,
    interactive: bool,
) -> Result<Vec<InterviewAnswer>> {
    let mut answers = Vec::new();

    loop {
        if answers.len() >= 5 {
            break;
        }

        let decision = request_interview_decision(context, structure, ai_config, &answers).await?;
        if let Some(reason) = &decision.reason {
            context.log_lines.push(format!(
                "[step4 interview decision {}]\n{}",
                answers.len() + 1,
                reason
            ));
        }
        if decision.decision == "finish" {
            if answers.len() >= 3 {
                break;
            }
            context.log_lines.push(
                "[warn] step4 AI attempted to finish before reaching 3 questions; continuing"
                    .to_string(),
            );
        }

        let question = decision
            .question
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .context("step4 AI interview returned no question")?;
        if answers.iter().any(|item| item.question == question) {
            bail!("step4 AI repeated an existing interview question: {question}");
        }
        let options = sanitize_interview_options(&decision.options)?;
        let default_answer = decision
            .default_answer
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| {
                options
                    .first()
                    .map(|item| item.as_str())
                    .unwrap_or("保持当前默认推荐")
            });
        let answer = ask_choice_with_custom(interactive, question, &options, default_answer)?;
        answers.push(InterviewAnswer {
            question: question.to_string(),
            answer: normalize_interview_answer(&answer, default_answer),
        });
    }

    Ok(answers)
}

async fn request_interview_decision(
    context: &mut RunContext,
    structure: &StructureSummary,
    ai_config: &crate::config::ResolvedAiConfig,
    answers: &[InterviewAnswer],
) -> Result<InterviewDecision> {
    let system = r#"你是阅读处理策略访谈代理。你的任务是决定下一步是继续提问，还是结束访谈并进入策略生成。

你必须严格输出一个 JSON 对象，不要输出 markdown，不要输出解释，不要使用代码块。

JSON 格式：
{
  "decision": "ask" | "finish",
  "question": "当 decision=ask 时必须提供的中文问题",
  "options": ["当 decision=ask 时必须提供的 2-5 个简短候选项"],
  "default_answer": "该问题的简短默认回答，供非交互模式降级使用，可为空",
  "reason": "一句话说明为什么问这个问题或为什么现在可以结束"
}

规则：
- 整个访谈总问题数必须在 3 到 5 之间。
- 当前答案数少于 3 时，decision 只能是 "ask"。
- 当前答案数达到 5 时，decision 只能是 "finish"。
- 问题必须只服务于生成阅读处理策略，不得闲聊。
- 当 decision=ask 时，options 必须提供 2 到 5 个可直接选择的候选项，且彼此尽量区分明确。
- 不要把“其他”“自定义”之类的候选项放进 options；CLI 会自动额外提供“自定义输入”入口。
- default_answer 最好直接取自 options 之一。
- 优先补齐以下决策信息：处理目标、处理重点、注释策略、标题/章节策略、增强内容、阅读场景。
- 不要重复已经问过的问题。
- 如果已有信息足够支撑以上策略字段，且当前答案数已经至少 3 个，可以返回 "finish"。
- 问题必须简洁、单一、可直接回答。"#;
    let user = format!(
        "{}",
        json!({
            "book_structure": structure,
            "current_answer_count": answers.len(),
            "required_topics": [
                "processing_goal",
                "processing_focus",
                "note_policy",
                "heading_policy",
                "enhancements",
                "reading_scenario"
            ],
            "answers": answers.iter().enumerate().map(|(index, answer)| {
                json!({
                    "index": index + 1,
                    "question": answer.question,
                    "answer": answer.answer
                })
            }).collect::<Vec<_>>()
        })
    );
    let debug = ai::complete_with_debug(ai_config, system, &user).await;
    let step_name = format!("step4-interview-{:02}", answers.len() + 1);
    write_ai_trace_files(context, &step_name, system, &user, &debug)?;
    log_ai_raw_response(context, &step_name, &debug);

    let payload = debug
        .extracted_content
        .clone()
        .context("step4 AI interview returned empty content")?;
    let decision: InterviewDecision = serde_json::from_str(&payload)
        .with_context(|| format!("failed to parse step4 interview JSON: {payload}"))?;

    match decision.decision.as_str() {
        "ask" | "finish" => Ok(decision),
        other => bail!("unsupported step4 interview decision: {other}"),
    }
}

fn collect_fallback_interview_answers(
    structure: &StructureSummary,
    interactive: bool,
    existing_answers: Vec<InterviewAnswer>,
) -> Result<Vec<InterviewAnswer>> {
    let mut answers = existing_answers;
    let presets = vec![
        (
            format!(
                "你这次阅读《{}》最主要的目标是什么？",
                structure.document.title
            ),
            vec![
                "快速抓住主线与关键观点".to_string(),
                "为课程或考试梳理框架".to_string(),
                "做研究，保留更多论证细节".to_string(),
            ],
            "快速抓住主线与关键观点",
        ),
        (
            "你更希望这份阅读版在内容组织上偏向精简主线，还是尽量保留原书层次与细节？".to_string(),
            vec![
                "优先精简主线，减少干扰".to_string(),
                "主线优先，但保留关键层级".to_string(),
                "尽量完整保留原书层次与细节".to_string(),
            ],
            "保留主线，同时尽量保留章节层次",
        ),
        (
            "你希望注释如何处理？是文末集中查看、就地短预览，还是尽量弱化？".to_string(),
            vec![
                "保留到文末集中查看".to_string(),
                "在引用处提供简短预览".to_string(),
                "尽量弱化，只在需要时查看".to_string(),
            ],
            "在引用处提供简短预览，必要时可查看完整注释",
        ),
        (
            "你希望额外增加哪些增强内容，比如每章摘要、导读或索引？".to_string(),
            vec![
                "每章增加简短导读与摘要".to_string(),
                "增加目录导航或索引".to_string(),
                "不额外增加增强内容".to_string(),
            ],
            "每章增加简短导读与摘要",
        ),
        (
            "你通常会在什么场景阅读这份 HTML？".to_string(),
            vec![
                "桌面精读".to_string(),
                "手机碎片阅读".to_string(),
                "打印或导出前检查".to_string(),
            ],
            "桌面精读",
        ),
    ];

    for (question, options, default_answer) in presets {
        if answers.iter().any(|item| item.question == question) {
            continue;
        }
        let answer = ask_choice_with_custom(interactive, &question, &options, default_answer)?;
        answers.push(InterviewAnswer {
            question,
            answer: normalize_interview_answer(&answer, default_answer),
        });
        if answers.len() >= 3 {
            break;
        }
    }

    Ok(answers)
}

fn normalize_interview_answer(answer: &str, default_answer: &str) -> String {
    let trimmed = answer.trim();
    if trimmed.is_empty() {
        default_answer.to_string()
    } else {
        trimmed.to_string()
    }
}

fn sanitize_interview_options(options: &[String]) -> Result<Vec<String>> {
    let cleaned = options
        .iter()
        .map(|item| item.trim())
        .filter(|item| !item.is_empty())
        .map(ToOwned::to_owned)
        .collect::<Vec<_>>();
    if !(2..=5).contains(&cleaned.len()) {
        bail!(
            "step4 AI must provide 2-5 interview options, got {}",
            cleaned.len()
        );
    }
    Ok(cleaned)
}

#[derive(Debug, Deserialize)]
struct InterviewDecision {
    decision: String,
    question: Option<String>,
    #[serde(default)]
    options: Vec<String>,
    default_answer: Option<String>,
    #[allow(dead_code)]
    reason: Option<String>,
}

pub(super) async fn step5_generate_transform(context: &mut RunContext) -> Result<String> {
    ensure_exists(&context.artifacts.normalized_html)?;
    ensure_exists(&context.artifacts.notes_json)?;
    ensure_exists(&context.artifacts.strategy_md)?;
    ensure_exists(&context.artifacts.structure_json)?;
    ensure_exists(&context.artifacts.normalize_report)?;

    let mut ai_status = "fallback";
    let mut script = fallback_transform_script();
    if let Some(ai_config) = resolve_ai_config(&context.config, Step::Step5)? {
        let system = r#"You are a senior Python engineer. Your job is to emit a single, standalone Python 3 script that applies a reading strategy to a normalized book HTML.

Output requirements:
- Return ONLY the Python source code.
- Do NOT wrap it in markdown fences, do NOT add commentary, do NOT add any preamble or epilogue text.

Script contract:
- Usage: python transform.py NORMALIZED_HTML NOTES_JSON STRATEGY_MD OUTPUT_HTML
- Inputs:
  - NORMALIZED_HTML: standard book HTML whose note bodies have already been stripped; in-text note references remain as a[data-role="noteref"][href][id].
  - NOTES_JSON: structured notes file (schema: version, id_scheme, notes[{id, kind, chapter_id, order, source, refs, content, position}]).
  - STRATEGY_MD: reading strategy in Simplified Chinese markdown; the final fenced JSON block holds machine-readable fields (title, processing_goal, processing_focus, note_policy, heading_policy, enhancements, reading_scenario).
- Output:
  - OUTPUT_HTML: the final reading-optimized HTML.
- The script MUST also print a compact single-line JSON summary to stdout describing what was applied, what was skipped, and any uncertain content that was preserved.

Behavior the script must implement:
- Parse the strategy JSON block and apply its decisions.
- Preserve content losslessly; never silently delete body text.
- Optionally re-inject notes (inline preview, grouped endnotes, collapsed, etc.) according to note_policy.
- Respect heading_policy for heading / chapter structure.
- Add a reading guide and other enhancements according to enhancements.
- Keep div[data-role="unknown"] blocks intact."#;
        let user = format!(
            r#"Generate transform.py for the following book.

<structure_json>
{}
</structure_json>

<normalize_report>
{}
</normalize_report>

<strategy_md>
{}
</strategy_md>
"#,
            fs::read_to_string(&context.artifacts.structure_json)?,
            fs::read_to_string(&context.artifacts.normalize_report)?,
            fs::read_to_string(&context.artifacts.strategy_md)?
        );

        let debug = ai::complete_with_debug(&ai_config, system, &user).await;
        write_ai_trace_files(context, "step5", system, &user, &debug)?;
        log_ai_raw_response(context, "step5", &debug);

        match debug.extracted_content.clone() {
            Some(result) => {
                script = format!("# generated by AI\n{result}");
                ai_status = "ai";
            }
            None => {
                context.log_lines.push(format!(
                    "[warn] step5 AI transform generation failed: {}",
                    debug.error.unwrap_or_else(|| "unknown error".to_string())
                ));
            }
        }
    }

    fs::write(&context.artifacts.transform_py, script).with_context(|| {
        format!(
            "failed to write {}",
            context.artifacts.transform_py.display()
        )
    })?;

    Ok(format!("generated transform.py via {ai_status}"))
}

pub(super) fn step6_run_transform(context: &mut RunContext) -> Result<String> {
    ensure_exists(&context.artifacts.transform_py)?;

    let ai_used = fs::read_to_string(&context.artifacts.transform_py)
        .unwrap_or_default()
        .contains("generated by AI");
    let run = Command::new(python_cmd())
        .arg(&context.artifacts.transform_py)
        .arg(&context.artifacts.normalized_html)
        .arg(&context.artifacts.notes_json)
        .arg(&context.artifacts.strategy_md)
        .arg(&context.artifacts.final_html)
        .output()
        .context("failed to execute transform.py")?;

    context.log_lines.push(format!(
        "[transform stdout]\n{}",
        String::from_utf8_lossy(&run.stdout)
    ));
    context.log_lines.push(format!(
        "[transform stderr]\n{}",
        String::from_utf8_lossy(&run.stderr)
    ));

    let stdout = String::from_utf8_lossy(&run.stdout).to_string();
    if !run.status.success() {
        if ai_used {
            context.log_lines.push(
                "[warn] AI-generated transform.py failed; switching to fallback template".into(),
            );
            fs::write(&context.artifacts.transform_py, fallback_transform_script())?;
            let rerun = Command::new(python_cmd())
                .arg(&context.artifacts.transform_py)
                .arg(&context.artifacts.normalized_html)
                .arg(&context.artifacts.notes_json)
                .arg(&context.artifacts.strategy_md)
                .arg(&context.artifacts.final_html)
                .output()
                .context("failed to execute fallback transform.py")?;
            context.log_lines.push(format!(
                "[transform fallback stdout]\n{}",
                String::from_utf8_lossy(&rerun.stdout)
            ));
            context.log_lines.push(format!(
                "[transform fallback stderr]\n{}",
                String::from_utf8_lossy(&rerun.stderr)
            ));
            if !rerun.status.success() {
                bail!("transform.py failed after fallback");
            }
            write_summary(context, &String::from_utf8_lossy(&rerun.stdout), "fallback")?;
            return Ok("rendered final HTML via fallback transform".to_string());
        }
        bail!("transform.py failed");
    }

    write_summary(context, &stdout, if ai_used { "ai" } else { "fallback" })?;
    Ok("rendered final HTML and summary".to_string())
}

fn write_ai_trace_files(
    context: &RunContext,
    step_name: &str,
    system: &str,
    user: &str,
    debug: &ai::AiCallRecord,
) -> Result<()> {
    let prompt_path = context.work_dir.join(format!("{step_name}.ai.prompt.md"));
    let response_path = context
        .work_dir
        .join(format!("{step_name}.ai.response.txt"));
    let debug_path = context.work_dir.join(format!("{step_name}.ai.debug.json"));

    let prompt = format!(
        "# AI Prompt\n\n## System\n\n```text\n{}\n```\n\n## User\n\n```text\n{}\n```\n",
        system, user
    );
    fs::write(&prompt_path, prompt)
        .with_context(|| format!("failed to write {}", prompt_path.display()))?;

    fs::write(
        &response_path,
        debug.response_body.as_deref().unwrap_or_default(),
    )
    .with_context(|| format!("failed to write {}", response_path.display()))?;

    fs::write(&debug_path, serde_json::to_string_pretty(debug)?)
        .with_context(|| format!("failed to write {}", debug_path.display()))?;

    Ok(())
}

fn log_ai_raw_response(context: &mut RunContext, step_name: &str, debug: &ai::AiCallRecord) {
    context
        .log_lines
        .push(format!("[{step_name} ai endpoint]\n{}", debug.endpoint));
    context
        .log_lines
        .push(format!("[{step_name} ai model]\n{}", debug.model));
    context.log_lines.push(format!(
        "[{step_name} ai response status]\n{}",
        debug
            .response_status
            .map(|status| status.to_string())
            .unwrap_or_else(|| "none".to_string())
    ));
    context.log_lines.push(format!(
        "[{step_name} ai raw response]\n{}",
        debug.response_body.as_deref().unwrap_or_default()
    ));
    if let Some(error) = &debug.error {
        context
            .log_lines
            .push(format!("[{step_name} ai error]\n{error}"));
    }
}
