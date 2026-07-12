# 裁读 ReadTailor Agent 设计

**版本**：v0.1  
**日期**：2026-07-12  
**关联产品文档**：[`product_mvp_plan.md`](product_mvp_plan.md)  
**规范化契约**：[`normalized_book_spec.md`](normalized_book_spec.md)

**阅读数据契约**：[`reading_contract.md`](reading_contract.md)

本文档定义首发产品中各类 Agent 的职责、输入输出、工具权限、完成条件和运行边界。
目标是让 Agent 可以直接进入实现，而不是只停留在“这里调用一下 AI”的描述上。

---

## 1. 设计原则

### 1.1 Agent 只用于需要探索和多轮决策的任务

适合 Agent 的任务：

- 需要按需读取一本书的不同位置
- 需要使用工具验证结果
- 需要根据工具反馈反复修正
- 需要与用户进行多轮交互

不适合 Agent 的任务：

- 输入输出固定的单个阅读节点内容生成
- HTML 注入、锚点校验、缓存和任务调度
- 文件哈希、权限校验和状态迁移

这些任务使用普通模型调用或确定性程序完成。

### 1.2 业务数据库是事实来源

Pi Agent 的 session 保存对话和工具轨迹，但不能代替业务状态。

- 用户画像以业务数据库中的当前版本为准
- 正式阅读中的本书处理方式以已确认的 `strategy_version` 为准
- 试读内容只能使用用户批准用于试读的 `strategy_draft_version`
- 规范化是否完成以 `nb_check` 报告为准
- 试读次数以业务数据库计数为准
- Agent 重启后从业务状态和已有产物继续，不假定能恢复执行到一半的工具调用

### 1.3 所有工具遵循最小权限

- 工具只暴露当前 Agent 完成任务所需的能力
- 读工具和写工具分开
- 所有文件路径、书籍 ID 和节点 ID 在宿主程序中校验
- Agent 不接触数据库连接、对象存储密钥、E2B API key 或模型 API key
- 工具失败必须返回明确错误，不能把失败伪装成正常文本

### 1.4 原文和共享书籍数据不可由个性化 Agent 修改

只有规范化 Coding Agent 可以生成共享书籍包，而且必须通过校验才能发布。
访谈、处理方式、章节生成和问 AI Agent 都只能读取原书，不得修改规范化 HTML。

---

## 2. 首发 Agent 总览

| Agent | 生命周期 | 是否写代码 | 是否与用户交互 | 主要产物 |
|---|---|---:|---:|---|
| 规范化 Coding Agent | 每个未命中的 EPUB 版本一次 | 是 | 否 | `normalize.py`、规范化书籍包 |
| 书籍分析 Agent | 每个规范化书籍包一次 | 否 | 否 | `book_profile.json` |
| 访谈与处理方式 Agent | 每个用户 × 每本书一个持续会话 | 否 | 是 | 本书画像、读前简报、处理方式、试读选择 |
| 问 AI Agent | 每个新问题一个独立会话 | 否 | 是 | 回答、长期画像更新、处理方式调整建议 |

此外有一个**阅读节点内容生成器**。它是普通模型调用，不是 Agent。

---

## 3. 统一运行方式

### 3.1 Pi Agent SDK 的位置

Agent 编排运行在 Node.js Worker 中，使用：

- `@earendil-works/pi-agent-core`
- `@earendil-works/pi-ai`

Pi 负责：

- Agent loop
- 工具调用
- 流式事件
- session 消息
- 上下文压缩或转换
- 工具调用前后的拦截

业务系统负责：

- 创建和恢复业务任务
- 装配模型和工具
- 保存业务产物
- 权限校验
- 超时、重试和取消
- 把 Agent 事件转换成网页进度

### 3.2 模型配置

所有 Agent 和普通模型调用均支持任意 OpenAI 兼容接口。建议提供全局默认值和按任务覆盖：

```text
AI_BASE_URL
AI_API_KEY
AI_MODEL

NORMALIZER_AI_BASE_URL / API_KEY / MODEL
BOOK_ANALYSIS_AI_BASE_URL / API_KEY / MODEL
INTERVIEW_AI_BASE_URL / API_KEY / MODEL
CONTENT_AI_BASE_URL / API_KEY / MODEL
QA_AI_BASE_URL / API_KEY / MODEL
```

按任务配置缺失时回落到全局配置。首发默认可全部使用 DeepSeek，但业务代码不得写死
DeepSeek 特有请求格式。OpenAI 兼容 provider 适配集中在一个模型工厂中完成。

### 3.3 Session 划分

- 规范化：一个 normalization job 一个 session
- 书籍分析：一个 normalized book package 一个 session
- 访谈与处理方式：一个 user-book 一个 session，贯穿访谈、两次确认和最多 5 次反馈调整
- 问 AI：一个新问题一个 session，同一问题的追问继续使用该 session

不同 session 之间不直接共享完整消息历史，只通过结构化业务产物共享信息。

---

## 4. 规范化 Coding Agent

### 4.1 目标

针对一个具体 EPUB 编写可重复执行的 `normalize.py`，将源书转换成符合 `nb-1.0`
的规范化书籍包，并通过结构层和保真层校验。

它不是从旧 plan1/2/3 中复制一次性“生成代码”调用，而是完整的：

```text
检查源文件 -> 编写脚本 -> 执行 -> 校验 -> 定位错误 -> 修改脚本 -> 再校验
```

### 4.2 输入

- 源 EPUB
- 已解压的 EPUB 工作区
- `normalized_book_spec.md`
- `nb_check.py` 和 `nb_linter.py`
- 当前任务之前已经生成的 `normalize.py`、产物和校验报告（如果是重试）

不把整本书直接塞进 prompt。Agent 通过工具按需查看文件、目录、HTML 片段和校验错误。

### 4.3 工具

#### `list_source_files`

列出 EPUB 解包工作区中的文件。

输入：

- 可选目录
- 可选 glob
- 最大返回数量

限制：只能读取当前 job 的源目录。

#### `read_source_file`

读取 OPF、nav、NCX、XHTML、CSS 等文本文件的指定范围。

输入：

- 相对路径
- 起始行或字符偏移
- 最大行数或字符数

输出必须截断并支持继续读取，禁止一次返回整本大文件。

#### `search_source`

在当前 EPUB 解包目录中搜索标签、属性、class、id 或文本模式。

输入：

- 查询文本或受限正则
- 文件 glob
- 最大结果数

输出包含文件路径、位置和短上下文。

#### `read_normalized_spec`

读取 `nb-1.0` 规范的指定章节。Agent 初始 prompt 包含核心完成标准，细节按需读取。

#### `write_normalizer`

创建或完整替换当前 job 的 `normalize.py`。

限制：

- 只能写这一个脚本
- 每次写入记录版本和 diff
- 不允许写宿主项目文件

#### `patch_normalizer`

对现有 `normalize.py` 做局部补丁，避免每轮完整重写。

限制同 `write_normalizer`。

#### `run_normalizer`

在 E2B 沙箱中执行固定命令语义：

```text
python normalize.py <source.epub-or-workspace> <output-dir>
```

Agent 不能自定义任意 shell 命令。工具负责：

- 设置超时和资源限制
- 禁止网络
- 捕获 stdout、stderr 和退出码
- 限制日志长度，完整日志保存到任务产物
- 执行前清理或版本化上一次临时输出

#### `run_nb_check`

对当前产物执行固定校验：

```text
python tools/nb_check.py book.normalized.html --baseline source.epub
```

返回：

- 退出码
- error/warning 数
- 关键指标
- 可继续分页读取的完整报告引用

#### `inspect_normalized_output`

按结构查看规范化产物，避免 Agent 读取整个大 HTML。

支持：

- 查看 head 和顶层骨架
- 按 `data-role` / `data-type` / id 查询
- 查看指定节点短片段
- 统计标签、注释、图片和链接

#### `finish_normalization`

声明任务完成。

宿主程序必须在工具内部再次验证：

- 最新 `run_normalizer` 成功
- 最新 `run_nb_check` 属于当前脚本和当前产物
- 结构层 0 error
- 保真层 0 error
- 必需产物存在

不满足时工具调用失败，Agent 必须继续修复。成功时结束 Agent loop，发布规范化核心产物，
并把业务状态推进到 `indexing`。此时尚不能标记书籍包 ready；宿主程序还必须生成 reading
manifest、完成包级资源校验并运行书籍分析。

### 4.4 禁止事项

- 不允许任意 bash 或任意 Python 片段执行工具
- 不允许访问网络
- 不允许读取其他用户或其他书籍的 job 目录
- 不允许降低或修改校验阈值
- 不允许修改 `nb_check.py`、`nb_linter.py` 或规范文档
- 不允许用删除原文的方式换取结构通过
- 不允许自行宣称“基本可用”后绕过 `finish_normalization`

### 4.5 完成与失败

成功条件只有一个：`finish_normalization` 通过。

建议失败保护：

- 整体任务有最大时长
- Agent 有最大 turn 数
- 脚本运行和校验分别有超时
- 连续多轮 error 数不下降时停止
- 失败保留脚本、日志、最后产物和校验报告，供重试诊断

网页只展示用户可理解的阶段，不暴露模型推理和内部命令：

```text
正在检查书籍结构
正在生成转换程序
正在验证正文和注释
发现结构问题，正在修复
处理完成 / 该版本暂时无法处理
```

---

## 5. 书籍分析 Agent

### 5.1 目标

在规范化核心产物、`reading_manifest.json` 和资源校验全部完成后生成共享
`book_profile.json`。它描述这本书本身，不包含用户画像，只生成一次，可被所有用户的
访谈和内容生产复用。

### 5.2 职责

- 理解全书主要结构和阅读顺序
- 给 reading manifest 中的每个阅读节点生成短摘要和内容类型
- 识别全书主题、核心问题和主要难点
- 判断不同节点的典型性和阅读门槛
- 提供试读候选特征，但不直接决定某个用户最终看到哪三个样章

### 5.3 工具

#### `get_book_metadata`

返回书名、作者、语言、来源格式和基本统计。

#### `get_book_outline`

返回由规范化 section 树和 reading manifest 组成的阅读结构。源 TOC 作为单独字段返回，
不能用来代替完整阅读节点清单。支持限制深度、分页和按子树读取。

#### `read_book_node`

按稳定的 `section_id + segment` 读取某个阅读节点的标题路径、正文片段、相邻节点和原书
注释摘要。

#### `search_book`

在全书规范化文本中搜索关键词，返回 reading manifest 节点位置和短上下文。

#### `get_node_stats`

返回节点字数、子节点数、原书注释数、图片数等机械统计。

#### `save_book_profile`

提交符合 schema 的 `book_profile.json`。工具验证：

- 引用的 `section_id + segment` 全部存在
- reading manifest 节点没有丢失或重复
- 摘要长度和字段符合约束
- 不包含用户信息

### 5.4 禁止事项

- 不修改规范化书籍包
- 不生成个性化导读或注释
- 不猜测用户背景
- 不选择最终试读样章
- 不把整本书原文复制进 `book_profile.json`

---

## 6. 访谈与处理方式 Agent

### 6.1 生命周期

一个用户对一本书建立一个 session，包含三个连续阶段：

```text
访谈 -> 读前简报与处理方式确认 -> 试读与最终确认
```

处理方式阶段和样章阶段的反馈合计最多 5 次。用户最终采用样章对应的处理方式后，session
可以保留用于审计，但不再直接修改已生效策略。正式阅读中的策略调整改由问 AI Agent 提议。

### 6.2 输入

- `book_profile.json`
- `reader_profile.json`
- 当前访谈历史
- 当前 `book_reader_profile.json`
- 当前策略版本和试读反馈（如有）

### 6.3 访谈职责

- 每次只提出一个问题
- 以 2–5 个清晰选项为主，允许自由文字补充
- 问题必须直接服务于本书处理方式
- 不重复询问长期画像里已经足够明确的信息
- 总问题数不超过 7 个
- 信息足够时提前结束

### 6.4 访谈阶段工具

#### `get_reader_profile`

读取当前长期画像的必要字段，不返回无关账户信息。

#### `get_book_profile`

读取共享书籍分析，可按章节或字段分页。

#### `present_interview_question`

提交一个要展示给用户的问题。

输入至少包含：

- question id
- 问题正文
- 2–5 个选项
- 是否允许文字补充（首发始终为 true）
- 该问题要补足的画像维度

宿主程序检查问题计数，达到 7 个后拒绝继续提问。

#### `finish_interview`

结束访谈并提交：

- `book_reader_profile.json`
- 可选 `reader_profile_patch`
- 访谈结论的人类可读摘要

长期画像 patch 可以直接合并，不需要用户逐次确认。

### 6.5 简报、策略和试读工具

#### `save_reading_briefing`

保存个性化读前简报。内容只能作为附加层，不得改写原文。

#### `save_strategy_draft`

保存新的未确认策略版本，包括：

- 用户可读说明
- 导读目标
- 注释重点与排除项
- 节后助读目标

首发不保存逐节点处理模式。工具只验证结构化处理原则和当前 draft 版本。

保存完成后应用进入 `strategy_review`。只有用户明确点击“处理方式没问题，生成样章”，
业务系统才能把当前 draft 标记为 `approved_for_trial`。这不是正式生效，也不创建正式阅读任务。

#### `select_trial_nodes`

选择三个试读节点并说明内部选择理由。

约束：

- 当前策略草稿必须已经由用户批准用于试读
- 必须恰好三个不同节点
- 必须是可独立阅读的节点
- 不能选择目录、版权页等无代表性内容
- 尽量覆盖进入门槛、典型内容和高难度内容

选择理由用于审计和调试，首发不一定展示给用户。

#### `request_trial_generation`

请求应用按当前策略生成三个试读样章。Agent 不直接生成整段样章，也不等待一个工具调用
完成所有模型任务。宿主程序先分配新的 `trial_revision`，再创建三个携带该 revision 和当前
`strategy_draft_version` 的内容生成任务，并在任务完成后恢复 session。

每个任务使用与正式章节相同的有限重试策略。任一任务耗尽重试仍失败时，整轮进入
`trial_generation_failed`，不得发布部分结果，也不得进入最终确认。用户重试时沿用当前已
批准策略和三个节点重新创建任务；这种技术重试不增加 `adjustment_count`。

#### `publish_trial_revision`

只有同一 `trial_revision` 下的三个样章都达到可展示状态，且其
`strategy_draft_version` 仍是当前已批准草稿时，才能发布当前试读版本。`trial_revision` 用于
版本和幂等控制；它与限制用户反馈次数的 `adjustment_count` 是两个不同计数。

### 6.6 用户反馈与版本循环

处理方式确认页收到反馈后：

1. 反馈作为新 user message 恢复同一个 session。
2. Agent 修订本书画像（如有必要）、文字处理方式和结构化策略草稿。
3. 应用发布新的 draft，并继续停留在 `strategy_review`。

样章页收到反馈后：

1. 当前整个 `trial_revision` 标记为 superseded；它的所有任务和结果无论是否完成都立即失效。
2. 反馈作为新 user message 恢复同一个 session。
3. Agent 修订本书画像（如有必要）、文字处理方式和结构化策略草稿。
4. 应用返回 `strategy_review`，不立即生成新样章。
5. 用户重新确认处理方式后，Agent 才可重新选择或保留试读节点并请求生成三个样章。

两种反馈共用一个调整计数，初始版本不计数，最多 5 次。达到上限后 Agent 不能再保存新
策略草稿。处理方式页和样章页必须隐藏反馈输入框、反馈按钮及其他调整入口，页面主体只
保留当前阶段的确认操作；用户仍可确认最后的策略草稿、生成最后一轮样章，并最终采用或
退出本书流程。

### 6.7 两个确认关口

Agent 没有任何确认权限。

第一次确认发生在 `strategy_review`：用户批准当前 draft 仅用于生成试读样章。

第二次确认发生在 `trial_review`。只有用户点击“采用这个处理方式并开始阅读”后，业务系统才能：

- 校验用户提交的 `trial_revision` 是当前可用版本，且其中的 `strategy_draft_version` 是当前
  已批准草稿
- 将样章对应的 draft 标记为 confirmed
- 创建生效的 `strategy_version`
- 把用户书籍状态改为 `active_reading`
- 创建第一个正式阅读节点及后续三个节点的生成任务

### 6.8 禁止事项

- 不修改规范化书籍和原文
- 不超过 7 个访谈问题
- 不把设置项堆成复杂控制面板
- 不在第一次确认前生成样章
- 不在第二次确认前生效策略
- 不绕过两个阶段合计 5 次的反馈限制
- 不把用户反馈当作对原文的改写指令

---

## 7. 阅读节点内容生成器（非 Agent）

### 7.1 为什么不是 Agent

节点生成是大量重复、输入输出稳定的任务。用普通 OpenAI 兼容模型调用更便宜、更容易
重试和缓存，也不会让模型在生产阶段自行扩大任务范围。

试读样章和正式阅读章节必须复用同一个内容生成器及固定生成脚本。不得为样章增加单独的
缩写、润色、演示优化或特殊 prompt 分支。`generation_scope` 只负责策略版本门禁与缓存隔离；
真正影响生成内容的是节点正文、用户画像和对应策略版本。

### 7.2 输入

- 当前 `section_id + segment` 和结构化 HTML
- 上级节点标题与必要上下文
- 必要的相邻节点摘要
- 当前节点相关原书注释
- `book_profile.json`
- `reader_profile.json`
- `book_reader_profile.json`
- `generation_scope = trial` 时：用户已批准用于试读的 `strategy_draft_version`
- `generation_scope = formal` 时：最终生效的 `strategy_version`
- `reading_manifest.json` 中的当前节点位置和 block 文本

### 7.3 输出

固定结构：

```json
{
  "guide": null,
  "annotations": [],
  "after_reading": null
}
```

- `guide` 和 `after_reading` 的展示结构相同，但语义和内容不同
- 导读、裁读注和节后助读全书统一支持，没有逐节点强度模式
- 某项对当前节点没有价值时允许返回 null 或空数组
- 每条 AI 注释先返回指定 block 中完全一致的 quote，由程序解析成 UTF-16 range
- 输出中不得包含改写后的完整原文

程序在保存前校验 schema、节点位置、锚点、`generation_scope` 和对应策略版本。试读任务不得
引用正式策略版本，正式阅读任务不得引用未确认草稿。生成失败不影响原文阅读。

发给模型的 HTML 保留 `p`、`strong`、`em`、链接、列表和引用等语义标签，并同时提供
每个 block 的标准文本。首发不使用视觉模型；图片输入去除真实 src/base64，只保留位置、
原始 alt 和图注。

### 7.4 缓存与失效

缓存至少由以下内容决定：

- normalized book id
- section id + segment
- user id，必须始终包含，不能用画像版本替代
- reader profile 的数据库版本或内容哈希
- book reader profile 的数据库版本或内容哈希
- generation scope + strategy draft/version
- prompt version
- model configuration version

正式阅读中用户确认新的处理方式后：

- 未读节点旧结果标记为 stale 并重新生成
- 已读节点保留旧版本
- 当前正在阅读的节点不被后台突然替换

---

## 8. 问 AI Agent

### 8.1 生命周期

每个新问题创建一个独立 session。同一问题下的连续追问复用该 session。

初始上下文：

- 划线提问：划线文本、所在段落、当前节点
- 直接提问：当前屏幕原文、当前节点

Agent 可以检索全书后续内容，不做默认防剧透限制。首发不强制为回答生成可点击引用。

### 8.2 工具

#### `get_question_context`

返回发起问题时的划线或屏幕上下文、当前 `section_id + segment` 和书中位置。

#### `get_book_outline`

返回目录树或指定子树。

#### `read_book_node`

读取指定节点的必要片段。支持按锚点、段落范围或字符范围读取。

#### `search_book`

搜索全书，返回 reading manifest 节点位置、原文锚点和短上下文。可以命中未读节点。

#### `get_original_notes`

读取与当前或指定位置相关的原书脚注、尾注。

#### `get_reader_context`

读取长期画像、本书画像和当前已确认处理方式的必要部分。

#### `update_reader_profile`

提交长期画像 patch，无需用户确认。

要求：

- 只记录有明确对话证据的稳定偏好或背景
- 不把用户针对一句话的临时困惑升级成长期偏好
- 不修改本书处理方式

#### `propose_strategy_change`

提出本书处理方式调整建议，例如改变导读目标、注释重点或节后助读方式。

工具只创建 pending proposal，不生效。用户确认后由业务系统创建新的策略版本并安排未读节点
重新生成；用户拒绝则关闭 proposal。

### 8.3 禁止事项

- 不修改原文或规范化书籍
- 不直接应用处理方式变更
- 不访问其他用户数据
- 不为了回答一个问题加载整本书
- 不在没有证据时更新长期画像

---

## 9. 工具实现的统一要求

### 9.1 输入输出

- 所有工具使用 TypeBox/JSON Schema 定义参数
- 返回结构化结果和简短文本摘要
- 大结果必须分页或返回 artifact reference
- section id、segment、book id、user id 必须由宿主程序重新校验
- 不信任模型提供的绝对路径、URL 或对象存储 key

### 9.2 幂等性

读工具天然幂等。写工具必须携带业务版本或幂等 key：

- 保存策略使用 `strategy_draft_version`
- 创建、发布和确认试读均使用 `trial_revision + strategy_draft_version`
- 生成节点使用 `generation_job_id`
- 规范化完成使用 `normalization_job_id + script_hash + output_hash`

重复工具调用不得生成多个生效版本。

### 9.3 工具拦截

Pi 的 tool preflight hook 至少检查：

- 当前 Agent 是否拥有该工具
- 当前业务状态是否允许调用
- 参数引用的资源是否属于当前任务和用户
- 访谈问题数、两阶段 `adjustment_count` 和执行次数是否超限
- 写入版本是否仍是最新版本

tool result hook 负责：

- 去除不应回传模型的密钥和内部路径
- 截断过长 stdout/stderr
- 记录耗时、错误和产物引用
- 把关键状态变化写入业务事件日志

### 9.4 可观测性

每次 Agent run 至少记录：

- agent 类型和 session id
- user/book/job id（适用时）
- 模型配置标识，不记录 API key
- turn 数和工具调用数
- token、耗时和估算成本
- 工具错误
- 最终完成或停止原因

用户界面只接收经过映射的阶段事件，不直接展示内部工具参数、模型推理或脚本日志。

---

## 10. 编排关系

```text
用户上传 EPUB
  -> 哈希命中？
     -> 是：复用包含 manifest 和 book_profile 的 ready 书籍包
     -> 否：规范化 Coding Agent -> nb_check -> 发布核心产物
          -> 程序生成并验证 reading_manifest
          -> 包级校验 assets 路径与文件
          -> 书籍分析 Agent -> book_profile -> 书籍包 ready
  -> 访谈与处理方式 Agent
     -> 最多 7 个问题
     -> 读前简报 + 文字处理方式 + 策略草稿
     -> 用户确认处理方式
     -> 选择 3 个试读节点 -> 内容生成器使用 approved strategy draft 生成样章
     -> 样章有问题：返回处理方式反馈与确认
     -> 两阶段反馈合计最多 5 次
     -> 用户最终采用
  -> 内容生成器使用 confirmed strategy version：当前节点 + 后续 3 个
  -> 阅读器
     -> 问 AI Agent（每个问题独立 session）
     -> 可自动更新长期画像
     -> 可提议、但不能直接修改本书处理方式
```

---

## 11. 首发验收清单

### 规范化 Coding Agent

- [ ] 只能访问当前 job 工作区
- [ ] 没有任意 shell 工具
- [ ] 脚本在 E2B 内执行且网络关闭
- [ ] 不能修改规范和校验器
- [ ] `finish_normalization` 会重新核对最新校验
- [ ] 失败保留完整诊断产物

### 书籍分析 Agent

- [ ] 产物不含用户信息
- [ ] 所有 `section_id + segment` 可解析
- [ ] reading manifest 节点没有丢失或重复
- [ ] 不复制大段原文到 profile
- [ ] 只读规范化书籍包

### 访谈与处理方式 Agent

- [ ] 每次只问一个问题
- [ ] 问题以选项为主并允许文字补充
- [ ] 不超过 7 问
- [ ] 策略包含导读、裁读注和节后助读的全书处理原则
- [ ] 处理方式未经第一次确认不能生成样章
- [ ] 试读恰好选择三个不同节点
- [ ] 两阶段反馈合计最多 5 次
- [ ] 达到 5 次后反馈输入和调整入口全部隐藏，只保留当前阶段确认操作
- [ ] 样章反馈返回处理方式确认，不直接重生成样章
- [ ] 新反馈使整个旧 `trial_revision` 失效，包括已经完成的样章
- [ ] 任一试读任务重试耗尽后不得发布部分样章或进入最终确认
- [ ] 发布和最终确认同时校验当前 `trial_revision` 与 `strategy_draft_version`
- [ ] 用户最终确认前策略不能生效

### 阅读节点内容生成器

- [ ] 不改写或返回完整原文
- [ ] AI 注释锚点全部可解析
- [ ] 个性化缓存键始终包含 user id、两类画像版本和对应策略版本
- [ ] 使用 reading manifest 的 block 文本和精确 range
- [ ] 试读只接受 approved strategy draft，正式阅读只接受 confirmed strategy version
- [ ] 图片不发送真实 src/base64，且不调用视觉模型
- [ ] 失败不影响原文阅读
- [ ] 策略版本变化时正确处理 stale 结果

### 问 AI Agent

- [ ] 每个问题独立 session
- [ ] 划线和当前屏幕两种上下文都可用
- [ ] 可以检索后续内容
- [ ] 更新长期画像不需要确认但必须有证据
- [ ] 本书处理方式只能创建待确认建议

---

## 12. 仍属于实现阶段的细节

以下事项不影响 Agent 边界，可以在编码时确定：

- 每个工具的最终字段名和分页大小
- Agent 最大 turn、超时和日志截断的具体数值
- session transcript 的长期存储格式
- 搜索使用全文索引、向量检索或混合检索
- 不同 Agent 是否使用同一默认模型
- 网页进度事件的最终文案和动画

这些参数应配置化，不应改变本文定义的职责、权限和完成条件。
