use std::path::PathBuf;

use kuchiki::NodeRef;
use serde::{Deserialize, Serialize};

use crate::{cli::RunArgs, config::FileConfig};

#[derive(Debug)]
pub(crate) struct RunContext {
    pub(crate) args: RunArgs,
    pub(crate) root_dir: PathBuf,
    pub(crate) output_dir: PathBuf,
    pub(crate) work_dir: PathBuf,
    pub(crate) artifacts: Artifacts,
    pub(crate) config: FileConfig,
    pub(crate) log_lines: Vec<String>,
}

#[derive(Debug)]
pub(crate) struct Artifacts {
    pub(crate) raw_html: PathBuf,
    pub(crate) raw_outline: PathBuf,
    pub(crate) normalize_py: PathBuf,
    pub(crate) normalized_html: PathBuf,
    pub(crate) structure_json: PathBuf,
    pub(crate) normalize_report: PathBuf,
    pub(crate) notes_json: PathBuf,
    pub(crate) interview_md: PathBuf,
    pub(crate) strategy_md: PathBuf,
    pub(crate) transform_py: PathBuf,
    pub(crate) final_html: PathBuf,
    pub(crate) run_log: PathBuf,
    pub(crate) summary_md: PathBuf,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct StructureSummary {
    pub(crate) version: String,
    pub(crate) document: DocumentMeta,
    pub(crate) landmarks: Landmarks,
    pub(crate) chapters: Vec<ChapterSummary>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub(crate) unknown_blocks: Vec<UnknownBlock>,
    pub(crate) stats: Stats,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct DocumentMeta {
    pub(crate) title: String,
    pub(crate) language: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct Landmarks {
    pub(crate) book_main_id: String,
    pub(crate) bodymatter_id: String,
    pub(crate) toc_id: String,
    pub(crate) has_toc: bool,
    pub(crate) has_notes_section: bool,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct ChapterSummary {
    pub(crate) id: String,
    pub(crate) index: usize,
    pub(crate) title: String,
    pub(crate) section_count: usize,
    pub(crate) paragraph_count: usize,
    pub(crate) note_ref_count: usize,
    pub(crate) unknown_block_count: usize,
    pub(crate) sections: Vec<SectionSummary>,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct SectionSummary {
    pub(crate) id: String,
    pub(crate) title: String,
    pub(crate) heading_level: usize,
    pub(crate) index: usize,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct UnknownBlock {
    pub(crate) id: String,
    pub(crate) chapter_id: String,
    pub(crate) index: usize,
    pub(crate) reason: String,
    pub(crate) text_preview: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct Stats {
    pub(crate) chapter_count: usize,
    pub(crate) section_count: usize,
    pub(crate) paragraph_count: usize,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct NotesFile {
    pub(crate) version: String,
    pub(crate) id_scheme: String,
    pub(crate) notes: Vec<NoteRecord>,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct NoteRecord {
    pub(crate) id: String,
    pub(crate) kind: String,
    pub(crate) chapter_id: String,
    pub(crate) order: usize,
    pub(crate) source: NoteSource,
    pub(crate) refs: Vec<NoteRefRecord>,
    pub(crate) content: NoteContent,
    pub(crate) position: NotePosition,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct NoteSource {
    pub(crate) original_note_id: String,
    pub(crate) original_href_target: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct NoteRefRecord {
    pub(crate) ref_id: String,
    pub(crate) source_anchor_id: String,
    pub(crate) source_href: String,
    pub(crate) chapter_id: String,
    pub(crate) order: usize,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct NoteContent {
    pub(crate) html: String,
    pub(crate) text: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct NotePosition {
    pub(crate) notes_section_id: String,
    pub(crate) index_in_notes_section: usize,
}

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct StrategyData {
    pub(crate) title: String,
    pub(crate) processing_goal: String,
    pub(crate) processing_focus: String,
    pub(crate) note_policy: String,
    pub(crate) heading_policy: String,
    pub(crate) enhancements: Vec<String>,
    pub(crate) reading_scenario: String,
}

#[derive(Debug)]
pub(crate) struct InterviewAnswer {
    pub(crate) question: String,
    pub(crate) answer: String,
}

pub(crate) struct AnchorMutation {
    pub(crate) node: NodeRef,
    pub(crate) record: NoteRefRecord,
}
