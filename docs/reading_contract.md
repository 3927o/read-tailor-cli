# 裁读 ReadTailor 阅读数据契约

**版本**：v0.2
**日期**：2026-07-13
**关联产品文档**：[`product_mvp_plan.md`](product_mvp_plan.md)
**规范化契约**：[`normalized_book_spec.md`](normalized_book_spec.md)

本文档定义规范化书籍如何进入阅读器、节点内容生成、精确裁读注、用户划线和问 AI。
规范化 HTML 是不可变的原书事实来源；本文定义的索引和个性化内容都不能修改原文。

---

## 1. 共享书籍产物

每个通过校验的规范化书籍包至少包含：

- `book.normalized.html`
- `reading_manifest.json`
- 图片及其他必要资源
- 规范化与校验报告

书名、作者数组、语言、封面路径、标识符、出版社、出版日期、源文件名和 EPUB SHA-256 等
产品查询所需元数据保存在共享书籍数据库记录中。首发不额外创建 `book_metadata.json`；
规范化 HTML head 和 manifest document 只保存由同一次元数据提取结果生成的必要快照。

`reading_manifest.json` 由程序从规范化 HTML 确定性生成，不使用 AI，不包含用户数据，
可以随时删除并重新生成。它不能反推出完整 HTML，也不能成为第二份原文。

每个书籍包固定 manifest 算法版本。重新生成时必须使用该包原来的版本，保证 node、block 和
offset 不变；升级 Block 或 manifest 算法必须创建显式迁移，不能在已有划线和笔记的书籍包上
静默切换版本。

规范化核心 HTML 通过 `nb_check` 后，还必须成功生成 manifest 并通过包级资源校验，书籍包
才能进入 ready。manifest 生成或资源校验失败都属于 `indexing` 失败。

当前生成器原型为 [`../tools/build_reading_nodes.py`](../tools/build_reading_nodes.py)。
该原型目前只生成 nodes，尚未输出本文 v0.2 要求的完整 `outline`、
`tailoring_eligibility_version`、`tailoring_eligible` 和 `exclusion_reason`；这些属于实现前必须
补齐的已知差距，不代表契约可选。

---

## 2. 阅读位置模型

### 2.1 Segment 所有者

`segment` 必须属于一个稳定的结构所有者。所有者可以是：

- 带 `data-type` 和稳定 `id` 的语义 `section`，例如章、节或 `subsection`
- 带稳定 `id` 的顶层正文区域 `section[data-role="frontmatter|bodymatter|backmatter"]`

顶层区域只有在直接包含不属于任何语义子 section 的正文时才拥有 segment。纯分组 section
如果只有标题和子 section，不产生自己的 segment。

### 2.2 Segment

`segment` 是某个所有者自身正文中的一段连续内容，也是实际阅读节点的内容粒度。绝大多数
所有者只有 `segment = 1`。若所有者的自身正文被语义子 section 分隔，为保持原书顺序并
避免父子内容重复，可以产生多个 segment。

一个阅读节点由 `owner_id + segment` 唯一定位。当前 JSON 字段为兼容既有实现继续命名为
`section_id + segment`；当所有者是顶层区域时，`section_id` 保存区域 id，`data_type` 保存
区域的 `data-role`。segment 不能脱离所有者独立存在。

### 2.3 Block

`block` 是 segment 内用于显示和定位的块级内容，例如：

- 段落
- 引用块
- 列表
- 图片或表格
- 代码块

程序按 DOM 顺序为 block 生成从 1 开始的 `block_index`。该编号不写回规范化 HTML，
但对同一个不可变规范化书籍包必须可重复生成。

`div[data-role="unit"]` 不单独成为阅读节点；它留在所属 segment 所有者的正文中。

### 2.4 Block v1 枚举算法

前后端必须使用同一个版本化实现，不能各自根据 CSS 或页面布局猜测 block。v1 规则如下：

1. 在一个 segment 内按 DOM 文档顺序深度优先遍历。
2. `p`、`pre`、`dt`、`dd`、`th`、`td` 是文本 block。
3. `li` 如果直接包含 `p`，由其中的 `p` 分别成为 block；否则该 `li` 的直接行内内容成为
   一个 block，嵌套列表另行遍历，不能重复计入父 `li`。
4. `figcaption` 如果直接包含 `p`，由其中的 `p` 成为 block；否则 figcaption 自身是文本 block。
5. `figure`、`audio`、`video` 是无文本媒体 block。figure 内的 figcaption 按上一条另行编号。
6. `div[data-role="separator|math|verse|unit|unknown"]` 只有在内部没有上述文本 block 时才
   自身成为 block；否则只枚举内部 block。
7. `blockquote`、`ul`、`ol`、`dl`、`table` 等结构容器自身不编号，只枚举其内部原子 block。
8. `strong`、`em`、`a`、`code`、`sup`、`sub` 等行内标签不创建新 block。
9. 同一段可见原文不得属于两个文本 block。无可见文本也无媒体的候选 block 忽略。

### 2.5 Block v1 标准文本

文本 block 的标准文本按以下规则生成：

- 使用 HTML 解析后的 Unicode 文本节点，实体按 DOM 解码后的字符计算
- 按 DOM 顺序拼接属于当前 block 的文本节点，不包含嵌套的其他 block
- `<br>` 映射为一个 `\n`；`\r\n` 和 `\r` 统一为 `\n`
- `strong`、`em` 等行内标签只贡献其内部文字，标签本身不贡献字符
- noteref 的可见编号属于原文文本；`href`、`id` 等属性不贡献字符
- 图片 alt 和媒体 URL 不属于可划线文本，另作为模型结构信息提供
- 不做 Unicode 规范化、自动 trim 或连续空白折叠

offset 是这份标准文本中的 UTF-16 code unit 位置，区间使用左闭右开 `[start, end)`。
实现必须同时保留标准文本字符到具体 DOM Text 节点位置的映射，供浏览器创建 Range。

---

## 3. `reading_manifest.json`

最小结构：

```json
{
  "version": "reading-nodes-1.0",
  "tailoring_eligibility_version": "tailoring-eligibility-1.0",
  "document": {
    "title": "哲学研究",
    "language": "zh-CN"
  },
  "outline": [
    {
      "section_id": "part-001",
      "data_type": "part",
      "title": "第一部分",
      "parent_section_id": null,
      "first_node_order": 1
    },
    {
      "section_id": "sub-0001",
      "data_type": "subsection",
      "title": "§1",
      "parent_section_id": "part-001",
      "first_node_order": 1
    }
  ],
  "nodes": [
    {
      "section_id": "sub-0001",
      "segment": 1,
      "order": 1,
      "region": "bodymatter",
      "data_type": "subsection",
      "title": "§1",
      "parent_section_id": "part-001",
      "character_count": 789,
      "block_count": 4,
      "tailoring_eligible": true,
      "exclusion_reason": null
    }
  ]
}
```

manifest 不复制节点 HTML。运行时根据 `section_id + segment` 从规范化 HTML 提取对应
的连续原文。阅读器、内容生成器、进度、问 AI 和用户笔记必须使用同一份 manifest。

`outline` 从规范化 HTML 的完整 `section[data-type]` 树确定性生成，包含没有自身正文节点的
纯分组 section。`first_node_order` 指向该 section 自身或首个后代的第一个阅读节点，用于连续
滚动阅读器的目录跳转。它是产品导航索引，不修改或伪造原书的 TOC；原 EPUB TOC 仍只由
规范化 HTML 中的 `nav[data-role="toc"]` 表达。

### 3.1 裁读资格 v1

裁读资格是产品硬边界，不是 AI 对内容价值的判断。v1 只有同时满足以下条件的节点具有资格：

1. `region = bodymatter`。
2. `data_type` 为 `chapter`、`section` 或 `subsection`。
3. 节点至少包含一个具有可见文本的标准文本 block。

不满足时保存 `tailoring_eligible = false` 和稳定、可枚举的 `exclusion_reason`，例如
`non_bodymatter`、`excluded_data_type` 或 `no_text_block`。前言、导论、序章、附录、后记、
结语、书目、索引、顶层区域直接正文和 `part` 自身 segment 即使包含大量文字，也不具有
裁读资格。

资格由 manifest 生成程序计算，书籍分析 Agent 和个性化策略均无权修改。没有资格的节点不
创建导读、裁读注或节后助读生成任务，但仍参与原文加载、连续滚动、进度、目录定位、划线、
笔记和问 AI。

资格规则必须有独立版本。改变资格规则时创建显式迁移，不能在已有试读、节点增强和阅读历史
的书籍包上静默重算。

源 EPUB 的 TOC 可能只到部或章，不能单独充当阅读节点清单。阅读节点按完整规范化
section 树和正文顺序生成，TOC 仍只表达源书实际提供的导航结构。

---

## 4. Block 文本与行内标签

发给模型的节点正文保留有意义的结构标签，例如 `p`、`strong`、`em`、`a`、列表、
引用和原书注释引用。程序同时为每个 block 提供用于精确定位的标准文本。

```html
<block index="1">
  <p>这是<strong>非常重要</strong>且<em>需要强调</em>的内容。</p>
</block>
```

对应文本：

```json
{
  "block_index": 1,
  "text": "这是非常重要且需要强调的内容。"
}
```

`strong`、`em` 等行内标签保留语义和显示效果，但标签本身不占字符位置。字符位置按
浏览器 JavaScript 字符串和 DOM Range 使用的 UTF-16 code unit 计算。浏览器可以把一个
Range 的起点和终点放在不同文本节点中，因此划线可以跨越 `strong`、`em` 等标签。

无属性、无语义的 `div` / `span` 必须在规范化阶段剥壳。结构性子 section 必须直接位于
父 section 下，不能藏在排版 wrapper 或 unknown 容器中。

---

## 5. 节点内容生成

节点内容生成使用普通 OpenAI 兼容模型调用，不使用 Agent。每次调用输入至少包括：

试读片段与正式阅读节点必须执行同一个固定生成脚本，使用同一套 prompt 模板、模型调用、
输出 schema、quote 到 UTF-16 range 的锚点解析、结果校验、重试和缓存实现。不得存在试读
专用生成器。`generation_scope` 只能决定输入原文范围、允许引用哪类策略版本及缓存命名空间，
不能改变裁读内容的生成逻辑或质量标准。

- 当前节点位置和生成范围；`trial` 为节点内连续 block range，`formal` 为完整节点
- 生成范围内的结构化 HTML
- 生成范围内的 block 标准文本；block index 仍使用完整节点中的稳定编号
- 当前节点相关原书注释
- 上级标题
- 上一个可裁读节点末尾和下一个可裁读节点开头的受限原文；截取的 block 数或字符数由程序
  的版本化配置决定，不能由模型扩大范围
- `book_profile.json`
- `reader_profile.json`
- `book_reader_profile.json`
- `generation_scope = trial` 时，使用用户已批准用于试读的 `strategy_draft_version`
- `generation_scope = formal` 时，使用最终生效的 `strategy_version`

试读片段不能跨阅读节点。每个片段保存 `section_id + segment + range`，range 起止点使用与
用户划线相同的 `block_index + UTF-16 offset` 模型。模型生成的裁读注只能锚定片段范围内原文。
导读和节后助读描述本次片段，不得伪装成对完整章节的处理结果。

首发不为不同节点设置处理强度。导读、裁读注、节后助读是全书统一支持的三种内容；
某项对当前节点没有价值时，模型返回 `null` 或空数组。

固定输出：

```json
{
  "guide": null,
  "annotations": [],
  "after_reading": null
}
```

`guide`、注释内容和 `after_reading` 使用 Markdown 字符串，由应用统一安全渲染。输出不得
包含重写后的完整原文。

应用必须按 `generation_scope` 校验策略引用：试读不得引用正式策略版本，正式阅读不得引用
未确认草稿。个性化缓存键必须始终包含 `user_id`，以及长期画像版本、本书画像版本、scope
和对应策略版本；画像版本不能代替 `user_id`。

---

## 6. 精确词句锚点

模型不直接计算字符偏移。它先返回 block 和从标准文本中原样复制的 quote：

```json
{
  "block_index": 2,
  "quote": "语言中的词语是对象的名称",
  "content": "这里描述的是一种指称论语言观。"
}
```

程序只接受在指定 block 中完全一致且唯一的 quote，不做模糊匹配。匹配成功后，程序计算
并保存稳定位置：

```json
{
  "range": {
    "start": {
      "block_index": 2,
      "offset": 15
    },
    "end": {
      "block_index": 2,
      "offset": 29
    }
  },
  "content": "这里描述的是一种指称论语言观。"
}
```

节点增强结果由外层记录关联 `section_id + segment`，不在每条注释中重复保存节点位置。
保存前必须验证 block、偏移和策略版本。任何锚点无效时整条注释拒绝保存并重试生成。

---

## 7. 用户划线与笔记

用户划线和 AI 裁读注共用同一种 range。用户选择可以跨多个 block，但首发不跨阅读节点。

```json
{
  "section_id": "sub-0001",
  "segment": 1,
  "range": {
    "start": {
      "block_index": 2,
      "offset": 15
    },
    "end": {
      "block_index": 3,
      "offset": 8
    }
  },
  "note": "用户填写的笔记"
}
```

规范化书籍包按源 EPUB 哈希不可变，因此保存位置即可重新取得被划线原文。问 AI 会话保存
发起问题时的划线范围；无划线时保存当前屏幕覆盖的 block 范围。

---

## 8. 图片与其他资源

首发不使用视觉模型。发给文本模型时：

- 删除图片真实 `src`
- 保留图片所在位置、figure 标识、原始 alt 和图注
- 没有 alt 或图注时只声明这里存在图片，不猜测图片内容
- 表格保留结构化 HTML 和文字，不转换成图片

阅读 UI 使用规范化书籍包中的真实资源。默认包结构为：

```text
normalized-book/
├── book.normalized.html
└── assets/
    ├── cover.jpg
    ├── fig-001.png
    └── audio-001.mp3
```

新生成的产品书籍包只保存 `assets/...` 逻辑相对路径。发布时整个书籍包按 EPUB 哈希写入统一存储接口；
生产环境可以使用 S3、R2、OSS、COS 或其他 S3 兼容对象存储，本地开发和小型服务器可以
使用文件系统实现。数据库不保存媒体二进制，也不把会过期的签名 URL 固化进 HTML。

应用在读取时把相对路径解析成经过权限检查的稳定资源路由或临时签名 URL。包级校验必须
确认资源存在，路径不能逃出书籍包根目录。规范化书籍包不得包含 data URI，必须使用
`assets/...` 相对路径引用媒体文件。
资源加载失败不阻止正文阅读，但 UI 必须显示缺失占位。

---

## 9. 阅读器要求

- 原文和 AI 内容分层保存，AI 内容不得写回规范化 HTML
- 正式阅读器采用连续滚动，按 manifest 节点顺序加载原文，不使用单节点翻页作为首发模式
- 用户可以通过 outline 任意跳转到全书位置，不设置顺序解锁；目标增强未完成时先显示纯原文
- 导读位于节点原文之前，节后助读位于原文之后，默认展开
- 原书注和裁读注在原文对应位置触发展开，并明确区分来源
- 阅读进度只按 manifest 中的原书位置计算，不计 AI 内容
- 阅读器同时显示全书百分比和当前目录位置
- 当前节点生成失败时仍立即显示纯原文
- 相同 manifest 位置用于跨设备同步进度、划线、笔记和问答入口

---

## 10. 规范化与校验前提

为了稳定生成 manifest 和精确锚点，规范化产物至少必须保证：

- 每个 `section[data-type]` 有稳定且全局唯一的 id
- `frontmatter`、`bodymatter`、`backmatter` 区域有稳定 id
- 结构性子 section 直接位于父 section 下
- 无属性、无语义的 `div` / `span` 已剥壳
- 段落、列表、引用、图、表和代码结构符合 `nb-1.0`
- 原书注释引用和正文可以双向解析
- 相对资源真实存在且路径安全

`nb_linter.py` 已检查 section/顶层区域 id、结构性 section 的直接父级和无属性
`div/span`。包级相对资源存在性仍需在接收完整规范化书籍包的校验步骤中补充。
