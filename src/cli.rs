use std::path::PathBuf;

use clap::{Args, Parser, Subcommand, ValueEnum};

#[derive(Debug, Parser)]
#[command(name = "bookcli", version, about = "AI-driven EPUB reading optimizer")]
pub struct Cli {
    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Debug, Subcommand)]
pub enum Commands {
    Run(RunArgs),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, ValueEnum)]
pub enum Step {
    Step1,
    Step2,
    Step3,
    Step4,
    Step5,
    Step6,
}

impl Step {
    pub fn all() -> [Step; 6] {
        [
            Step::Step1,
            Step::Step2,
            Step::Step3,
            Step::Step4,
            Step::Step5,
            Step::Step6,
        ]
    }

    pub fn label(self) -> &'static str {
        match self {
            Step::Step1 => "step1-epub-to-html",
            Step::Step2 => "step2-normalize",
            Step::Step3 => "step3-extract-notes",
            Step::Step4 => "step4-interview",
            Step::Step5 => "step5-generate-transform",
            Step::Step6 => "step6-run-transform",
        }
    }

    pub fn number(self) -> usize {
        match self {
            Step::Step1 => 1,
            Step::Step2 => 2,
            Step::Step3 => 3,
            Step::Step4 => 4,
            Step::Step5 => 5,
            Step::Step6 => 6,
        }
    }
}

#[derive(Debug, Args)]
pub struct RunArgs {
    pub input: PathBuf,

    #[arg(long)]
    pub output: Option<PathBuf>,

    #[arg(long)]
    pub workdir: Option<PathBuf>,

    #[arg(long, value_enum)]
    pub step: Option<Step>,

    #[arg(long, value_enum)]
    pub resume_from: Option<Step>,

    #[arg(long)]
    pub keep_intermediate: bool,

    #[arg(long)]
    pub verbose: bool,
}
