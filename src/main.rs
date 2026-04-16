mod ai;
mod cli;
mod config;
mod pipeline;

use anyhow::Result;
use clap::Parser;

#[tokio::main]
async fn main() -> Result<()> {
    dotenvy::dotenv().ok();
    let cli = cli::Cli::parse();
    pipeline::run(cli).await
}
