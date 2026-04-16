mod helpers;
mod steps;
mod templates;
mod types;

use std::{fs, path::PathBuf};

use anyhow::{Context, Result, bail};

use crate::{
    cli::{Cli, Commands, RunArgs},
    config::load_config,
};

use self::{
    helpers::{select_steps, slugify, write_run_log},
    steps::{
        step1_convert_epub, step2_normalize, step3_extract_notes, step4_interview,
        step5_generate_transform, step6_run_transform,
    },
    types::{Artifacts, RunContext},
};

pub async fn run(cli: Cli) -> Result<()> {
    match cli.command {
        Commands::Run(args) => run_pipeline(args).await,
    }
}

async fn run_pipeline(args: RunArgs) -> Result<()> {
    if args.step.is_some() && args.resume_from.is_some() {
        bail!("--step and --resume-from cannot be used together");
    }

    let input = fs::canonicalize(&args.input)
        .with_context(|| format!("failed to resolve input path {}", args.input.display()))?;

    let root_dir = std::env::current_dir().context("failed to read current directory")?;
    let config = load_config(&root_dir)?;
    let book_name = input
        .file_stem()
        .and_then(|value| value.to_str())
        .map(slugify)
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "book".to_string());

    let output_dir = args
        .output
        .clone()
        .unwrap_or_else(|| root_dir.join("dist").join(&book_name));
    let work_dir = args
        .workdir
        .clone()
        .unwrap_or_else(|| output_dir.join("work"));

    fs::create_dir_all(&output_dir)
        .with_context(|| format!("failed to create output directory {}", output_dir.display()))?;
    fs::create_dir_all(&work_dir)
        .with_context(|| format!("failed to create work directory {}", work_dir.display()))?;

    let artifacts = Artifacts {
        raw_html: work_dir.join("book.raw.html"),
        raw_outline: work_dir.join("raw_outline.xml"),
        normalize_py: work_dir.join("normalize.py"),
        normalized_html: work_dir.join("book.normalized.html"),
        structure_json: work_dir.join("structure.json"),
        normalize_report: work_dir.join("normalize_report.md"),
        notes_json: work_dir.join("notes.json"),
        interview_md: work_dir.join("interview.md"),
        strategy_md: work_dir.join("strategy.md"),
        transform_py: work_dir.join("transform.py"),
        final_html: output_dir.join("book.final.html"),
        run_log: output_dir.join("run.log"),
        summary_md: output_dir.join("summary.md"),
    };

    let mut context = RunContext {
        args,
        root_dir: PathBuf::from(root_dir),
        output_dir,
        work_dir,
        artifacts,
        config,
        log_lines: Vec::new(),
    };

    let steps = select_steps(&context.args);
    for step in steps {
        context
            .log_lines
            .push(format!("[START] {} ({})", step.label(), step.number()));
        write_run_log(&context)?;

        let result = match step {
            crate::cli::Step::Step1 => step1_convert_epub(&mut context),
            crate::cli::Step::Step2 => step2_normalize(&mut context).await,
            crate::cli::Step::Step3 => step3_extract_notes(&mut context),
            crate::cli::Step::Step4 => step4_interview(&mut context).await,
            crate::cli::Step::Step5 => step5_generate_transform(&mut context).await,
            crate::cli::Step::Step6 => step6_run_transform(&mut context),
        };

        match result {
            Ok(summary) => {
                context
                    .log_lines
                    .push(format!("[OK] {}: {}", step.label(), summary));
                write_run_log(&context)?;
                if context.args.verbose {
                    println!("{}: {}", step.label(), summary);
                }
            }
            Err(error) => {
                context
                    .log_lines
                    .push(format!("[ERROR] {}: {error:#}", step.label()));
                write_run_log(&context)?;
                return Err(error);
            }
        }
    }

    if context.args.verbose {
        println!("output: {}", context.output_dir.display());
    }
    Ok(())
}
