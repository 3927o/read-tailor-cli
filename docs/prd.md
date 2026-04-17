# PRD v0.1：基于 AI 的书籍阅读体验优化 CLI

## 1. 产品目标

做一个 CLI 工具，帮助用户把 EPUB 书籍转换并处理成**更适合其个人阅读目标**的 HTML 阅读版本。

该工具不是通用电子书编辑器，重点是：

1. 把 EPUB 转成 HTML
2. 把源 HTML 规范化为统一的标准 HTML
3. 通过 AI 动态提问了解用户需求
4. 让 AI 生成针对这本书的处理脚本
5. 执行脚本并输出优化后的结果

---

## 2. MVP 范围

MVP 只支持：

- 输入：`.epub`
- 中间格式：`.html`
- 输出：优化后的 `.html`
- 交互方式：CLI
- AI 用途：
  - 规范化方案/脚本生成
  - 动态问答
  - 阅读策略生成
  - 最终处理脚本生成

MVP 暂不支持：

- GUI
- PDF 输入
- 批量处理
- 重打包为 EPUB

---

## 3. 核心流程

### Step 1：EPUB 转 HTML

输入 EPUB，调用 Pandoc 生成单文件 HTML。

默认命令：

```bash
pandoc name.epub -f epub -t html -s --embed-resources -o name.raw.html
```

输出：

- `name.raw.html`

---

### Step 2：AI 阅读 raw HTML 架构并生成规范化脚本

工具需要先让 AI 对 `book.raw.html` 做一次**粗粒度架构理解**，识别这本书的大致结构模式，例如：

- 封面、目录、前言、正文、附录的大致分布
- 章节与小节的组织方式
- 标题、段落、列表、引用等常见块级结构
- 注释引用与注释正文的大致组织方式
- 明显异常或难以可靠识别的区域

AI 在这一步**默认不直接阅读完整正文文本**，而是仅读取一份由程序预处理出来的结构视图 XML。该结构视图只包含：

- 标签树
- 标签属性
- 每个标签文本内容的前 30 个字符；其中 `h1`/`h2`/`h3`/`h4` 标签保留完整文本（标题通常较短，完整保留有助于 AI 判断章节结构）

这一步的目的不是让 AI 直接手工改 HTML，而是让 AI 基于这些理解生成一个**规范化处理脚本**，再由脚本把 `book.raw.html` 转成统一格式的 `book.normalized.html`。

同时输出：

- `raw_outline.xml`：供 AI 使用的结构视图快照
- `normalize.py`：AI 生成的规范化脚本
- `book.normalized.html`：规范化后的标准 HTML
- `structure.json`：标准化后的结构摘要
- `normalize_report.md`：本次规范化做了哪些处理、哪些内容无法确定

#### 标准 HTML 最小要求

标准 HTML 必须尽量满足以下结构：

- `main#book[data-type="book"]`
- `section#bodymatter[data-role="bodymatter"]`
- 每个章节为：`section.chapter[data-type="chapter"][id]`
- 每章标题为该章节内第一个 `h1`
- 章内小节使用 `section[data-type="section"]`，标题层级为 `h2` 到 `h4`
- 正文段落统一为 `p`
- 目录为：`nav#toc[data-role="toc"]`
- 注释引用为：`a[data-role="noteref"][href][id]`
- 注释内容位于：`section[data-role="notes"]`
- 每条注释为：`[data-role="note"][id]`
- 无法可靠识别的内容必须保留为：`div[data-role="unknown"]`

#### 注释格式规范化要求

本步骤必须把各种形态的注释（脚注、尾注、章末注等）统一收敛到上述标准结构中，使 Step 3 可以按固定规则进行提取与剥离。具体要求：

- 脚注、尾注、章末注统一抽象为 note，挂载于 `section[data-role="notes"]`；若能识别类型，通过 `data-note-kind` 等扩展属性记录（如 `footnote`/`endnote`/`chapter-note`）
- 所有注释引用统一为 `a[data-role="noteref"][href][id]`，`href` 指向对应注释正文的 `id`
- 所有注释正文统一为 `[data-role="note"][id]`；同一 note 若被多次引用，应保留单一注释正文，由多个 noteref 共同指向
- 正文中引用注释的锚点、以及注释正文中回跳正文的锚点，均应通过 `href` 与 `id` 明确建立映射关系
- 注释正文内部若包含二级注释、交叉引用或复杂块结构，应完整保留原始 HTML 片段，不做破坏性重组
- 若原书注释结构无法可靠判断类型或归属，可降级为 `div[data-role="unknown"]` 保留，不得静默丢弃

要求：

- 尽量语义化
- 尽量简洁
- 遇到边界不清晰、原书结构不规整或多种解释都成立时，可由 AI 自主决定最合理的结构映射
- 不强行猜测不确定内容
- 不允许因为规范化而误删正文内容
- AI 无法稳定判断时，优先保留原内容并降级为 `div[data-role="unknown"]`

---

### Step 3：提取注释并从 normalized HTML 中剥离注释正文

这一步在 `book.normalized.html` 的基础上进行，**前提是 Step 2 已经把各种形态的注释收敛为标准结构**（`a[data-role="noteref"]` / `section[data-role="notes"]` / `[data-role="note"]`）。本步骤不再负责注释的格式规范化，仅做：

1. 按固定规则从标准化 HTML 中提取注释，生成 `notes.json`
2. 从 `book.normalized.html` 中删除注释正文内容
3. 仅保留正文中的注释引用 `a[data-role="noteref"]`
4. 为后续按策略重新注入、改写、折叠或重排注释做准备

这一步是基于标准化 HTML 结构执行的**固定内置处理步骤**，不为单本书额外生成注释提取脚本，也不做形态判定或结构收敛。若输入 HTML 不满足 Step 2 的注释规范化要求，应直接报错或记录异常，而不是在 Step 3 内补做规范化。

要求：

- 不能破坏正文中的注释引用关系
- `notes.json` 中的注释 `id` 不保留原 HTML 中的原始 id，而是在提取阶段重新生成
- 新 `id` 应在单次处理结果中稳定且唯一，默认按文档顺序生成
- `notes.json` 需要保留新 `id`、原始锚点信息、引用关系、原始正文内容及必要的位置信息
- 一个注释若被多个 noteref 引用，`notes.json` 中应保留 `refs` 数组，而不是只保留单个引用
- 注释正文从 HTML 中移除后，`book.normalized.html` 应更适合后续正文处理
- 后续最终输出阶段允许按策略将注释重新注入目标位置

输出：

- `notes.json`
- 更新后的 `book.normalized.html`

---

### Step 4：AI 问答并生成阅读处理策略

这是一个完整的连续流程：AI 先通过动态问答补足用户阅读目标，再基于问答结果直接生成阅读处理策略。

CLI 与用户进行 **3-5 个问题** 的对话。

问题不是固定模板，而是由 AI 根据以下信息动态决定：

- 书籍基本信息
- 已提问的问题
- 用户之前的回答

目标是获取足够信息，以判断如何处理这本书，提升阅读体验，并产出该用户阅读这本书的最佳处理策略。

要求：

- 问题数量限制为 3-5 个
- 问题必须服务于“决定处理策略”
- 如果信息已足够，可以提前结束问答并进入策略生成

输出：


- `interview.md`
- `strategy.md`：给人看的策略描述

策略内容应明确说明：

- 处理目标
- 处理重点
- 注释如何处理
- 标题/章节如何处理
- 是否增加摘要、导读、索引等增强内容
- 输出偏向什么阅读场景

---

### Step 5：AI 生成 Python 处理脚本

基于以下上下文：

- `structure.json`
- `strategy.md`
- 本 PRD 中定义的“标准 HTML 最小要求”
- `normalize_report.md`

AI 生成一个 Python 脚本，对标准化 HTML 进行进一步处理，得到最终阅读版本。

输出：

- `transform.py`

脚本职责：

- 读取 `book.normalized.html`
- 按策略处理 DOM
- 生成最终 HTML
- 输出处理摘要

---

### Step 6：执行脚本

执行 `transform.py`，输出最终结果。

输出：

- `book.final.html`
- `run.log`
- `summary.md`

要求：

- 支持只生成脚本不执行
- 支持执行时保留中间产物
- 执行失败时明确指出失败步骤和错误信息

---

## 3.5 JSON 产物约束

本项目中的 JSON 产物采用**最小约束、允许扩展**的原则，不预先固定过死的 schema。

要求：

- `structure.json`、`notes.json` 均允许 AI 或脚本按需要添加扩展字段
- 只要求每个 JSON 文件包含该步骤继续往后所必需的最小信息
- 当某字段无法可靠生成时，可留空、缺省或显式标记不确定状态，但不应伪造高置信度内容

### `notes.json` 最小格式约定

`notes.json` 是从 `book.normalized.html` 中按固定规则提取出的结构化注释数据，用于后续注释回注、改写、折叠、重排与调试。

最小推荐格式示意：

```json
{
  "version": "1.0",
  "id_scheme": "note-seq",
  "notes": [
    {
      "id": "note-0001",
      "kind": "footnote",
      "chapter_id": "ch1",
      "order": 1,
      "source": {
        "original_note_id": "fn1",
        "original_href_target": "#fn1"
      },
      "refs": [
        {
          "ref_id": "ref-0001",
          "source_anchor_id": "r1",
          "source_href": "#fn1",
          "chapter_id": "ch1",
          "order": 1
        }
      ],
      "content": {
        "html": "<p>这里是注释正文。</p>",
        "text": "这里是注释正文。"
      },
      "position": {
        "notes_section_id": "book-notes",
        "index_in_notes_section": 1
      }
    }
  ]
}
```

字段约定：

- 根对象最少包含：`version`、`id_scheme`、`notes`
- `notes` 按文档顺序排列
- 每条 note 最少包含：
  - `id`：提取阶段重新生成的新 id，单次处理内稳定且唯一
  - `kind`：注释类型，读取自标准化 HTML 中 note 节点的 `data-note-kind` 扩展属性（由 Step 2 写入）；无法判断时可为 `unknown` 或留空
  - `chapter_id`：注释归属章节；无法判断时可留空
  - `order`：该 note 在全书中的顺序
  - `source`：来自**标准化 HTML**（Step 2 产出）的锚点信息，至少包括 `original_note_id`（规范化 HTML 中 note 元素的 id）与 `original_href_target`（规范化 HTML 中 noteref 的 `href` 目标）
  - `refs`：正文中的引用位置数组；一个 note 可对应多个 noteref
  - `content.html`：从标准化 HTML 中取出的注释正文 HTML 片段，优先完整保留
  - `content.text`：由注释正文提取出的纯文本
  - `position`：必要的位置信息（基于标准化 HTML）
- 每个 `refs[]` 项最少包含：
  - `ref_id`
  - `source_anchor_id`
  - `source_href`
  - `chapter_id`
  - `order`

### `structure.json` 最小格式约定

`structure.json` 是 `book.normalized.html` 的结构摘要，供后续策略生成、处理脚本生成与调试使用。它不是完整 DOM 导出，而是面向后续步骤的稳定结构索引。

最小推荐格式示意：

```json
{
  "version": "1.0",
  "document": {
    "title": "示例书名",
    "language": "zh-CN"
  },
  "landmarks": {
    "book_main_id": "book",
    "bodymatter_id": "bodymatter",
    "toc_id": "toc",
    "has_toc": true,
    "has_notes_section": true
  },
  "chapters": [
    {
      "id": "ch1",
      "index": 1,
      "title": "第一章 示例标题",
      "section_count": 3,
      "paragraph_count": 28,
      "note_ref_count": 4,
      "unknown_block_count": 1,
      "sections": [
        {
          "id": "ch1-sec1",
          "title": "第一节",
          "heading_level": 2,
          "index": 1
        }
      ]
    }
  ],
  "notes": {
    "note_ref_count": 12,
    "notes_section_id": "book-notes"
  },
  "unknown_blocks": [
    {
      "id": "unknown-0001",
      "chapter_id": "ch1",
      "index": 1,
      "reason": "ambiguous-structure",
      "text_preview": "这里是一段无法稳定判断结构的内容"
    }
  ],
  "stats": {
    "chapter_count": 10,
    "section_count": 34,
    "paragraph_count": 420
  }
}
```

字段约定：

- 根对象最少包含：`version`、`document`、`landmarks`、`chapters`
- `document` 最少包含：
  - `title`
  - `language`
- `landmarks` 最少包含：
  - `book_main_id`
  - `bodymatter_id`
  - `toc_id`
  - `has_toc`
  - `has_notes_section`
- `chapters` 按正文顺序排列；每个 chapter 最少包含：
  - `id`
  - `index`
  - `title`
  - `section_count`
  - `paragraph_count`
  - `note_ref_count`
  - `unknown_block_count`
  - `sections`
- 每个 `sections[]` 项最少包含：
  - `id`
  - `title`
  - `heading_level`
  - `index`
- 若存在无法稳定识别的区域，建议在 `unknown_blocks` 中记录：
  - `id`
  - `chapter_id`
  - `index`
  - `reason`
  - `text_preview`
- `stats` 用于提供全书范围内的基础统计摘要，最少建议包含：
  - `chapter_count`
  - `section_count`
  - `paragraph_count`

---

## 4. 关键产品原则

1. **先统一结构，再做个性化处理**
  - 所有后续 AI 处理都基于标准 HTML，而不是直接基于原始 HTML
2. **注释先抽离，再按策略回注**
  - 注释正文应先从标准化 HTML 中剥离出来
  - 后续是否注入、注入到哪里、以何种形式呈现，由阅读策略决定
3. **标准规范要小而稳**
  - 尽量覆盖常见书籍结构
  - 不追求一次性穷尽所有情况
4. **动态问答驱动策略生成**
  - 不使用固定题库
  - 但必须限制问题数量
5. **不确定内容宁可保留，不要误改**
  - `unknown` 是正式保留机制
6. **每一步都要有中间产物**
  - 方便调试、复用、回溯

---

## 5. 主要输入输出

### 输入

- `book.epub`

### 中间产物

- `book.raw.html`
- `raw_outline.xml`
- `normalize.py`
- `book.normalized.html`
- `structure.json`
- `notes.json`
- `normalize_report.md`
- `interview.md`
- `strategy.md`
- `transform.py`

### 最终输出

- `book.final.html`
- `run.log`
- `summary.md`

---

## 6. CLI 需求

MVP 至少需要一个全流程命令：

```bash
bookcli run ./book.epub
```

建议支持的参数：

- `--output`
- `--workdir`
- `--step`
- `--resume-from`
- `--keep-intermediate`
- `--verbose`

可选支持分步命令，但不是 MVP 必须项。

建议补充约定：

- 默认输出目录为 `./dist/<book-name>/`
- 默认工作目录为输出目录下的 `work/`
- 所有中间产物默认写入工作目录，最终产物写入输出目录根部
- `--step` 允许只运行单一步骤或从某一步开始运行
- `--resume-from` 用于在已有中间产物基础上继续后续步骤
- 失败后不自动清理现场，便于排查问题和人工重跑

---

## 7. 验收标准

MVP 完成的标准：

1. 用户能通过 CLI 输入 EPUB 并跑完整流程
2. 能成功生成原始 HTML
3. AI 能基于 raw HTML 架构生成可执行的规范化脚本
4. 能成功生成标准化 HTML
5. 标准化结果基本满足最小标准 HTML 规范
6. 能提取注释并生成 `notes.json`
7. 能从 `book.normalized.html` 中移除注释正文，同时保留注释引用
8. AI 能在 3-5 个动态问题内完成访谈并生成明确的阅读处理策略
9. 能生成可执行的 Python 脚本
10. 能输出最终优化后的 HTML
11. 全流程中间产物完整保存，失败时可定位具体步骤

建议增加最低质量验收：

12. `book.normalized.html` 中应存在 `main#book[data-type="book"]` 与 `section#bodymatter[data-role="bodymatter"]`
13. 正文中可识别出的章节应尽量落入 `section.chapter[data-type="chapter"]`；无法稳定识别的内容必须保留，不得静默丢弃
14. `notes.json` 中每条 note 的新 id 必须唯一；正文中的注释引用在提取后仍可映射到对应 note
15. `interview.md` 中实际提问数量应为 3-5 个；若少于 3 个，必须在记录中说明提前结束原因
16. `summary.md` 应说明最终执行了哪些处理、哪些策略未执行、哪些内容因不确定性被保留、

---

## 8. 技术栈

### 主程序

- **语言**：Rust
- **CLI 框架**：`clap`
- **交互式问答**：`inquire`
- **HTML 解析**（生成结构视图）：`scraper`
- **HTTP 客户端 / AI SDK**：`async-openai`（支持自定义 `base_url`，兼容所有 OpenAI 兼容接口）
- **JSON 序列化**：`serde` / `serde_json`
- **环境变量加载**：`dotenvy`（启动时自动加载 `.env` 文件）

### AI 接口

使用 **OpenAI 兼容接口**（`/v1/chat/completions`），通过 `async-openai` SDK 调用，配置自定义 `base_url` 以支持任意兼容接口。

各步骤的 AI 可独立配置，以支持为不同任务选用不同模型或接口（如结构提取用廉价快速模型，脚本生成用高能力模型）。

配置方式（支持全局默认 + 按步骤覆盖）：

| 环境变量 | 说明 |
|---|---|
| `AI_BASE_URL` | 默认接口地址 |
| `AI_API_KEY` | 默认鉴权密钥 |
| `AI_MODEL` | 默认模型名称 |
| `AI_BASE_URL_STEP2` | Step 2 专用接口地址（覆盖默认） |
| `AI_API_KEY_STEP2` | Step 2 专用密钥 |
| `AI_MODEL_STEP2` | Step 2 专用模型 |
| `AI_BASE_URL_STEP4` | Step 4 专用接口地址 |
| `AI_MODEL_STEP4` | Step 4 专用模型 |
| `AI_BASE_URL_STEP5` | Step 5 专用接口地址 |
| `AI_MODEL_STEP5` | Step 5 专用模型 |

未配置步骤专用变量时，回退到全局默认值。也可通过配置文件（如 `bookcli.toml`）统一管理，环境变量优先级高于配置文件。

### AI 生成脚本

- **语言**：Python 3
- **执行方式**：主程序通过 `std::process::Command` 调用 `python`
- AI 生成 `normalize.py` 与 `transform.py`，主程序负责调用并捕获输出

---

## 9. 非目标

以下内容不属于当前版本：

- 做成通用电子书编辑器
- 支持任意格式输入
- 处理所有极端 HTML 结构
- 保证 AI 生成内容绝对正确
- GUI 可视化编辑
