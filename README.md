# read-tailor-cli

一个基于 Rust 的 CLI，用来把 EPUB 转成更适合阅读的 HTML。

当前版本已经实现了 PRD 的 MVP 主流程骨架：

1. EPUB 转单文件 HTML
2. 生成原始结构视图 `raw_outline.xml`
3. 规范化为统一的 `book.normalized.html`
4. 提取注释到 `notes.json`
5. 进行 3-5 个问题的阅读访谈
6. 生成阅读策略和最终 `book.final.html`

AI 是可选的。未配置 AI 时，Step 2 和 Step 5 会回退到内置 Python 模板，仍然可以跑出一个可用的基线结果。

## 状态

这是一个可编译、可运行的 MVP 基线，不是完整成品。

已完成：

- `bookcli run <book.epub>` 全流程命令
- `--step` 和 `--resume-from`
- 默认输出目录和工作目录
- `run.log` / `summary.md` 等中间产物落盘
- Step 2、Step 4、Step 5 的 AI 配置解析
- AI 失败时的回退逻辑

当前限制：

- 只支持 `.epub` 输入
- 最终输出仍是 `.html`，不会重新打包 EPUB
- HTML 规范化和最终转换的 fallback 规则还比较保守
- 复杂注释结构、复杂目录结构、极不规整的书籍 HTML 还没有做深度适配
- `--keep-intermediate` 参数当前只是保留接口；实际上现在默认就不会清理中间产物

## 依赖

运行前请确保本机有：

- Rust / Cargo
- `python`
- `pandoc`

本项目启动时会自动加载当前目录下的 `.env`。

首次使用时，可以基于仓库里的示例文件生成自己的配置：

```bash
cp .env.example .env
cp bookcli.toml.example bookcli.toml
```

两者都是可选的：

- 只用 `.env` 就足以驱动全部 AI 能力
- `bookcli.toml` 适合把配置提交到团队的私有仓库或做分步骤覆盖
- 两者都不配置时，Step 2 / Step 5 会回退到内置 Python 模板，仍可跑通

## 快速开始

编译：

```bash
cargo build
```

查看命令帮助：

```bash
cargo run -- run --help
```

运行完整流程：

```bash
cargo run -- run ./book.epub --verbose
```

默认输出位置：

- 最终输出：`./dist/<book-name>/`
- 中间产物：`./dist/<book-name>/work/`

## CLI 用法

```bash
bookcli run [OPTIONS] <INPUT>
```

当前支持的参数：

- `--output <DIR>`：指定输出目录
- `--workdir <DIR>`：指定工作目录
- `--step <step1..step6>`：只执行某一步
- `--resume-from <step1..step6>`：从某一步继续往后执行
- `--keep-intermediate`：当前版本中默认已经保留中间产物
- `--verbose`：打印步骤执行摘要

示例：

只执行 Step 1：

```bash
cargo run -- run ./book.epub --step step1
```

从 Step 4 开始继续：

```bash
cargo run -- run ./book.epub --resume-from step4 --verbose
```

## 流程说明

### Step 1

调用 `pandoc` 把 EPUB 转为单文件 HTML，输出：

- `book.raw.html`

### Step 2

读取 `book.raw.html`，生成结构视图 `raw_outline.xml`，然后产出：

- `normalize.py`
- `book.normalized.html`
- `structure.json`
- `normalize_report.md`

如果配置了 Step 2 的 AI，会优先让 AI 生成 `normalize.py`；失败时回退到内置模板。

### Step 3

从 `book.normalized.html` 中提取注释，输出：

- `notes.json`
- 更新后的 `book.normalized.html`

### Step 4

进行 3-5 个问题的阅读访谈，输出：

- `interview.md`
- `strategy.md`

如果当前不是交互终端，会自动使用默认答案生成策略。

### Step 5

根据结构摘要和策略生成：

- `transform.py`

如果配置了 Step 5 的 AI，会优先让 AI 生成脚本；失败时回退到内置模板。

### Step 6

执行 `transform.py`，输出：

- `book.final.html`
- `run.log`
- `summary.md`

## AI 配置

AI 通过 OpenAI 兼容接口调用，当前只会在 Step 2、Step 4、Step 5 使用。

支持两种配置方式：

- 环境变量
- `bookcli.toml`

环境变量优先级高于配置文件。

### 环境变量

全局默认：

```bash
AI_BASE_URL=
AI_API_KEY=
AI_MODEL=
```

分步骤覆盖：

```bash
AI_BASE_URL_STEP2=
AI_API_KEY_STEP2=
AI_MODEL_STEP2=

AI_BASE_URL_STEP4=
AI_API_KEY_STEP4=
AI_MODEL_STEP4=

AI_BASE_URL_STEP5=
AI_API_KEY_STEP5=
AI_MODEL_STEP5=
```

### `bookcli.toml`

```toml
[ai]
base_url = "https://your-endpoint.example/v1"
api_key = "your-api-key"
model = "your-default-model"

[ai.step2]
model = "your-step2-model"

[ai.step4]
model = "your-step4-model"

[ai.step5]
model = "your-step5-model"
```

说明：

- 如果某个步骤完全不配置 AI，会直接使用本地 fallback
- 如果某个步骤只配置了部分字段，会报错；`base_url`、`api_key`、`model` 必须同时存在

## 输出产物

典型输出目录结构：

```text
dist/<book-name>/
  book.final.html
  run.log
  summary.md
  work/
    book.raw.html
    raw_outline.xml
    normalize.py
    book.normalized.html
    structure.json
    notes.json
    normalize_report.md
    interview.md
    strategy.md
    transform.py
```

## 开发

格式化：

```bash
cargo fmt
```

编译：

```bash
cargo build
```

当前已验证：

- `cargo build`
- `cargo run -- run --help`

## 代码结构

- `src/main.rs`：程序入口
- `src/cli.rs`：CLI 参数定义
- `src/config.rs`：配置加载与 AI 配置解析
- `src/ai.rs`：OpenAI 兼容接口调用
- `src/pipeline/mod.rs`：主流程编排
- `src/pipeline/steps.rs`：6 个步骤实现
- `src/pipeline/helpers.rs`：共享辅助函数
- `src/pipeline/templates.rs`：内置 Python fallback 模板
- `src/pipeline/types.rs`：共享数据结构
- `docs/prd.md`：产品需求文档
