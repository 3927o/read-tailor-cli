# Step 2 Normalize 实验记录（本轮）

记录范围：在 `tests/step2_approaches/` 已搭好 6 方案矩阵框架（plan_1..plan_6
+ common/* + 通用 evaluator + runner）之后，第一次接通真实 AI、跑出对比数据、
并迭代 facts_extractor 与 SCRIPT_SYSTEM prompt 的整轮过程。

实验时间：2026-05 期间（具体见 git history）
模型：`mimo-v2.5-pro`（推理模型，调用方为
`https://token-plan-cn.xiaomimimo.com/v1` 兼容 OpenAI 协议）
样本书：`查拉图斯特拉如是说.epub`、`毛泽东选集.epub`、`悲剧的诞生.epub`，
以及《查拉图斯特拉如是说》的另外 4 个 EPUB 变体（`_v2` / 3 个 z-library 版）

---

## 1. 起点状态

- 6 方案矩阵脚本（plan_1..plan_6）和共享工具（`common/outline_full.py`、
  `common/outline_trimmed.py`、`common/facts_extractor.py`、
  `common/ir_schema.py`、`common/ir_engine.py`、`common/script_runner.py`、
  `common/structure_summary.py`、`common/prompts.py`）已写好。
- `evaluator.py` 已重写为**通用结构契约校验**，无书特定常量。
- `runner.py` 支持 `--plan N` 与 `--eval-only`。
- 但还没接过真实 AI；`config.py` 的 `AI_*` 环境变量都为空。

## 2. 接通 AI 调用

### 2.1 推理模型踩坑：max_tokens 全被 reasoning 吃光

第一次跑 plan 1，120s 后失败，错误是 "无法解析 AI 响应"。诊断后发现：
`mimo-v2.5-pro` 是推理模型，最后一个 chunk 的 usage 显示
`completion_tokens=8000, reasoning_tokens=7999, finish_reason="length"` —
8000 token 的预算几乎全部被内部思考消耗，content 是空字符串。

**修复**（见 `ai_utils.py`）：
- 默认 `max_tokens` 从 8000 提到 32000
- 6 个 plan 文件统一改成 `max_tokens=32000`
- curl `--max-time` 从 180s 提到 600s，subprocess timeout 同步到 660s
- 当 `finish_reason="length"` 且 content 为空时，主动抛 RuntimeError
  而不是返回空字符串

### 2.2 推理拖慢调用，添加关闭推理参数

接着发现单次 plan 1 调用要 109s（推理 + 输出），6 个 plan 估算 30-60 分钟。
用户提供了 mimo provider 的扩展参数 `"thinking": {"type": "disabled"}`，
加进 payload 后单次调用降到 24s。

## 3. 第一次完整跑：查拉图斯特拉如是说

环境：
```bash
BOOK_NAME="查拉图斯特拉如是说"
AI_BASE_URL=...; AI_API_KEY=...; AI_MODEL=mimo-v2.5-pro
python3 runner.py
```

结果：

| # | 方案 | 耗时 | tokens | 章节 | noteref | note | orphan | 状态 |
|---|------|----:|-------:|----:|--------:|----:|-------:|-----|
| 1 | script + full outline | 60s | 161k | **1** | 0 | 0 | 0 | PASS |
| 2 | script + trimmed | 39s | 38k | 5 | 1266 | 0 | 1266 | FAIL |
| 3 | script + facts | 42s | 14k | **89** | 1373 | 1 | 1373 | PASS |
| 4 | IR + full outline | 1s | 0 | – | – | – | – | FAIL |
| 5 | IR + trimmed | 12s | 37k | 1 | 0 | 0 | 0 | PASS |
| 6 | IR + facts | 10s | 12k | 2 | 1 | 0 | 0 | PASS |

观察要点：
- **PASS ≠ 质量好**。本书实际约 89 章，但 plan_1 只识 1 章、plan_5/6 只识
  1-2 章 —— 它们在结构契约层"合规"但章节切分严重欠拟合。
- **plan_3 是唯一识出 89 章的方案**，但 1373 个 noteref 全部 orphan，注释体
  没迁过去，引用链断了。
- **plan_4 直接失败**："AI response contains no JSON object"。full outline
  30 万字符可能被截断或模型放弃。
- **token 量级**：full outline 系（plan_1/4）≈ 16 万 tokens 一次调用，
  trimmed 系 ≈ 4 万，facts 系 ≈ 1.2-1.4 万。**facts 路线便宜 10×+**。
- **耗时**：IR 路线（plan_5/6）10-12s，比 script 路线（40-60s）快 3-5 倍。
  不让模型现写代码，直接吐 JSON 决策快得多。

## 4. 第二次完整跑：毛泽东选集

毛选规模大得多（prompt ~50 万 tokens vs 查拉图斯特拉的 16 万）。结果：

| # | 方案 | 耗时 | tokens | 章节 | noteref | note | orphan | 状态 |
|---|------|----:|-------:|----:|--------:|----:|-------:|-----|
| 1 | script + full | 170s | 493k | **394** | 2653 | 2653 | 7 | PASS |
| 2 | script + trimmed | 244s | 494k | **396** | 2294 | 2653 | 6 | PASS |
| 3 | script + facts | 35s | 44k | 0 | 0 | 0 | 0 | FAIL |
| 4 | IR + full | 443s | 519k | 4 | 0 | 0 | 0 | PASS |
| 5 | IR + trimmed | 119s | 492k | 2 | 0 | 2655 | 0 | PASS |
| 6 | IR + facts | 17s | 0 | – | – | – | – | FAIL |

跨书对比的关键发现：

1. **方法在不同书上的表现严重不一致**
   - plan_1/2 在查拉图斯特拉只识 1-5 章，在毛选拿到 ~395 章 + 完整 note 配对
   - plan_3 在查拉图斯特拉识 89 章，在毛选 0 章 FAIL
   - plan_5/6（IR 路线）在两本上都不能正确切章节
2. **IR 路线在大书上塌方**。plan_4 跑 7 分钟、50 万 tokens，识 4 章。
   模型直接给结构化 JSON 决策时，规模一上去就 hold 不住。
3. **plan_6 失败原因**：日志显示 "IR resolved zero chapter titles in
   source" —— AI 给的 selector 在 raw HTML 里命中 0 次。

## 5. 深挖 plan_3：facts 优化的迭代

第二次跑后，最突出的反差是 **plan_3 在两本书上一对一错**：facts 给得对就
能成功（查拉图斯特拉），稍弱一点（毛选）就完全失败。诊断后发现初版
facts_extractor 的几个具体盲点。

### 5.1 优化 1：扩展 NOTE_KEYWORDS 关键字表

毛选的注释类名是 `zy`/`hl`（拼音简写），原 keyword 表只有英文
（footnote/endnote/rearnote/note/fn），命中 0 次。
扩展为：
```python
NOTE_KEYWORDS = (
    "footnote", "endnote", "rearnote", "note", "fn",
    "zhu", "zhushi", "zy", "zs", "hs", "bz", "jz",
)
```

### 5.2 优化 2：新增 `in_document_link_summary`

最关键的一条改进。原 facts 只看锚点本身的属性，不看锚点**指向哪**。但
判定"哪类元素是注释体"的最直接信号就是"被锚点指向的目标的 tag/class
分布"。新加：

```python
{
  "total_in_document_anchors": int,
  "unique_targets": int,
  "unresolved_targets": int,
  "href_prefix_top": [...],
  "target_tag_top": [...],
  "target_class_top": [...],          # ← 关键
  "target_parent_class_top": [...],
  "anchors_per_target_distribution": {...}  # 一对一/一对多
}
```

毛选拿到的结果：`target_class_top: [("hl", 2646), ("zy", 2639)]` —— 立刻告
诉 AI："hl 和 zy 就是注释体类"。

### 5.3 优化 3：新增 `body_children_profile`

毛选 raw 里 h3 不是 body 的直接子节点，而是被包在 `<section class="h3 div">`
里。AI 看不到这点，会去 `body.children` 里找 h3，永远找不到。新加：

```python
{
  "total_direct_children": 834,
  "tag_top": [("p", 417), ("section", 405), ...],
  "section_class_top": [("h3 div", 395), ...],  # ← 关键
  "section_inner_heading_top": [("h3", 394), ...]
}
```

### 5.4 优化 4：新增 `heading_wrapper_profile`

更细粒度的"每个 heading 是被什么包着的"。AI 看到 'h3 的 parent 100% 是
section.h3.div' 就知道 section.h3 才是章节单元。

### 5.5 优化 5：新增 `heading_profile`

按 tag 聚合的 heading 画像（count、top_classes、top_id_prefixes、
sample_texts、avg_text_len），帮 AI 快速判定章节级。比如毛选：
- h1: 9（卷）
- h2: 9（时期）
- h3: 394（章）
- h4: 253（节）

## 6. 深挖 plan_3：SCRIPT_SYSTEM prompt 的迭代

facts 给到位之后，AI 的策略对了，但脚本仍然有 bug。串行解决了三个 bug。

### 6.1 Bug 1：`elem.copy()` TypeError

AI 写出 `el_copy = elem.copy()`，bs4 的 Tag 没有 `.copy()` 方法。
**修复**：在 SCRIPT_SYSTEM 加 "Common bs4 pitfalls" 段落，明令使用
`copy.copy(node)`，列出 5 个常见 footgun（mutation during iteration、
extract vs decompose、`soup.children` 返回 html 元素而非 body 等）。

### 6.2 Bug 2：递归 dispatch 漏层级

AI 写一个 `process_node(elem)` 递归 walker，按 tag_name 分发处理。注释处理
代码确实存在且策略对，但 dispatcher 只递归到 section 子元素，从不进入 `<p>`
内部 —— 而 2653 个 `<a class="zy">` 全在 `<p>` 里。
**修复**：在 SCRIPT_SYSTEM 加 "Recommended script architecture — multi-pass
with global scans" 段落，强制改用三轮全文档扫描：
- Pass 1: 全局 `find_all` 拿章节锚点 list
- Pass 2: 全局 `find_all` 拿 noteref + note body
- Pass 3: 按 anchor 切片组装

### 6.3 Bug 3：pass 3 把 anchor 拿去 body.children 找位置

AI 在 pass 1 用 `body.find_all('h3')` 拿到 394 章节锚点（对的），但 pass 3
做切片时切回 iterate `body.children`，再用 `child in chapter_headings` 比对
—— h3 不是 body 子元素（被包在 section 里），比对永远不命中。
**第一版修复**（被 user 否决）：列出两种 slicing pattern (a) same-level walk
和 (b) wrapper-as-unit，要求 AI 二选一。被指出"太刚、举例太具体、可能误伤
其他书"。
**第二版修复**（采纳）：只讲不变量 —— "Pass 3 必须从 anchor 自身出发，
对每个 anchor 决定 chapter-unit 元素（自身/parent/更高 ancestor）；禁止
iterate body.children 再用 membership 比对"，不规定具体 pattern。

### 6.4 Bug 4：Pass 2 半截子收尾

第三次迭代后 AI 写出 `# For now, we'll collect note bodies separately`
然后没收尾，`all_notes` 永远空，`if all_notes: ... main_tag.append(notes_section)`
跳过。
**修复**：在 prompt 中明确 Pass 2 必须产出**两个具体数据结构**：
`note_id_map: dict[original_id -> new_id]` 和
`note_body_elements: list[Tag]`，并加 "Forbidden anti-pattern" 一段
明令禁止占位注释。

## 7. 优化后的 plan_3 结果

| 书 | 优化前 | 优化后 |
|----|--------|--------|
| 查拉图斯特拉如是说 | 89 章 / 1373 noteref / 1 note / **1373 orphan** / PASS（虚标） | 90 章 / 0 noteref / 0 note / 0 orphan / PASS |
| 毛泽东选集 | 0 章 / FAIL | **394 章 / 2321 noteref / 2637 note / 0 orphan / PASS** |

毛选这本是真的成功（结构 + 注释配对都对）。但**查拉图斯特拉的注释这次反而
是 0 / 0** —— 看似回归，实际 AI 这次根据 facts 看到所有 1377 个 noteref 的
target id 都 unresolved，干脆放弃。这暴露出更深的问题，引出了第 8 节。

## 8. 第三本书：悲剧的诞生

为评估 facts 路线的鲁棒性，引入第三本测试书。

### 8.1 第一次跑 plan_3

```
3: AI script + facts | 16s | 17k | 213 chapters | 0 noteref | 0 note | PASS
```

213 章是因为这本书每个 h2 是一篇短文（"一忧郁的小诗人"等），真就是 213 篇。
注释 0 识别需要诊断。

### 8.2 注释丢失诊断

源文件里每章末尾有：
```html
<p class="zhusi"><a href="../Text/chapter10.xhtml#w001" id="m001">[1]</a>
    大卫·施特劳斯（David Strauss，1808—1874），德国神学家、哲学家...</p>
```

pandoc 输出后变成：
```html
<hr />
<p><a href="#chapter10.xhtml_w001" id="chapter10.xhtml_m001">[1]</a>
    大卫·施特劳斯...</p>
```

注释**正文 100% 保留**，但 `class="zhusi"` 被 strip 掉了。AI 看到 facts 里
`target_class_top: []` 就判断"没有结构化注释"放弃。这本属于**信号削弱**
而不是数据丢失，理论上 AI 应该能从 id 前缀模式（`m001` ↔ `w001` 双向配对）
推断出注释关系。

## 9. pandoc EPUB reader 真 bug 的发现

回到查拉图斯特拉为什么 1377 个 noteref 全 orphan，深挖发现是 pandoc 阶段
就已经丢数据。

### 9.1 raw HTML 里直接验证

源 EPUB 的 `OEBPS/Text/part0091.xhtml` 包含 1377 个
`<aside id="rearnote_X" epub:type="rearnote">...完整注释正文...</aside>`。

但 pandoc 转完的 raw HTML 里：
- 0 个 `<aside>` 元素
- 0 个 `id="rearnote_X"`
- 0 处 rearnote 唯一文字（用 `类缘关系`、`大地与天上`、`前文圣者所说` 反查均 0）

注释体在转换中**整体消失**。

### 9.2 确认是 EPUB reader 阶段（AST 之前）丢的

```
pandoc 查拉图斯特拉如是说.epub -f epub -t native > /tmp/zara_native.txt
wc -l   # → 28867 行 AST
grep -c "类缘关系\|大地与天上\|前文圣者所说" /tmp/zara_native.txt  # → 0
```

AST 阶段就 0 处 rearnote 文本。意味着任何下游手段（HTML writer flag、
Lua filter、reference-location 配置）都救不回。

### 9.3 验证不是单一文件喂 pandoc 的问题

把同一份 `part0091.xhtml` 单独喂给 pandoc 跑 html→html：

```
pandoc /tmp/zara_check/OEBPS/Text/part0091.xhtml -f html -t html
```

输出包含完整的 `<section class="rearnotes" epub:type="rearnotes">` +
1377 个 `<p><a class="noteref">[N]</a>注释正文</p>`。

**bug 精准定位在 pandoc 的 EPUB reader**，不是 HTML reader、不是 HTML writer、
不是 `<aside epub:type="rearnote">` 这个标签本身。

### 9.4 试 pandoc 不同版本

| 版本 | 输出大小 | rearnote 文字 | aside | section.rearnotes |
|------|---------:|--------------:|------:|------------------:|
| 3.1.13 | 763591 | 0 | 0 | 0 |
| 3.5    | 763541 | 0 | 0 | 0 |
| 3.9    | 763541 | 0 | 0 | 0 |

3 个版本输出**字节级一致**（差异仅 metadata 时间戳），说明这不是 3.x 引入
的回归。pandoc 2.x 没有 arm64 binary、docker daemon 没起，本地没法测。

### 9.5 试 pandoc 各种 flag

```
[]                            size=763541 aside=0 section.rearnotes=0
[--file-scope]                size=763541 aside=0 section.rearnotes=0
[--reference-location=block]  size=763541 aside=0 section.rearnotes=0
[--reference-location=section] size=763541 aside=0 section.rearnotes=0
```

所有 flag 输出完全一致。

### 9.6 试 pandoc chunkedhtml 输出

输出 6 个 HTML 文件 + index。其中 `4-注释.html` 应该承载所有 rearnote，
实际：4955 字节，body 里只有空标题 `<h1>注释</h1>`，1377 条 rearnote 一条
都没活下来。

原因：chunkedhtml 只换 HTML writer，数据丢失发生在 EPUB reader 上游。

## 10. pandoc bug 的精确触发条件（关键发现）

用户提供了同一本书的 4 个 EPUB 变体，结果出乎意料：

| 变体 | epub:type | 注释体位置 | href 形式 | pandoc 结果 |
|------|-----------|------------|-----------|-------------|
| 原版（你最初的） | `rearnote` | 独立 `part0091.xhtml` | `part0091.xhtml#rearnote_X` 跨文件 | ❌ 0 条 |
| `_v2`（Calibre 重打） | `rearnote` | 独立 `part0091.xhtml`（与原版字节相同） | 同上 | ❌ 0 条 |
| zlib【德】尼采 | **`footnote`** | **同 noteref 文件**内联 | `#footnote-10-18` 同文件 | ✅ **2760 条** |
| zlib【德】威廉·尼采 | （同上） | 同上 | 同上 | ✅ 2752 条 |
| zlib 译文经典 | （同上） | 同上 | 同上 | ✅ 2752 条 |

z-library 的三个版本里，pandoc 把 `<aside epub:type="footnote">` 正确识别
为 native footnote AST node，HTML writer 输出为 `<section
class="footnotes">` + `<li>` 列表，每条带 ↩︎ 反向链接。

**触发 pandoc bug 的精确条件**：
- `epub:type="rearnote"`（不是 `footnote`）
- 注释体放在与 noteref **不同的 xhtml 文件**里
- noteref href 形式为 `otherfile.xhtml#anchor`（跨文件引用）

只要任一维度不满足，pandoc 都正常工作。

## 11. 三本书的现状汇总

| 书 | pandoc 表现 | plan_3 当前结果 | 真正的 root cause |
|---|---|---|---|
| 查拉图斯特拉如是说（原版） | ❌ rearnote 全丢 | 不可能恢复 | pandoc EPUB reader 跨文件 rearnote bug |
| 毛泽东选集 | ✅ 完整 | ✅ 394 ch / 2321 ref / 2637 note / 0 orphan | — |
| 悲剧的诞生 | ✅ 文字完整（class 被 strip） | ⚠️ 章节合理过分 + 注释漏识 | facts 信号弱 + AI 在弱信号下保守放弃 |

## 12. 工程结论与下一步

1. **plan_3（AI script + facts）目前是最强方案**：
   - token 成本最低（10×+ 优势）
   - 在 pandoc 输出完整时质量最好
   - 在毛选这种"内联高亮型注释"上拿到 0 orphan
   - 只要 prompt + facts 配套到位，跨书泛化能力足够

2. **plan_4/5/6（IR 路线）目前不实用**：
   - 大书上塌方（毛选 plan_4 跑 7 分钟、50 万 tokens、4 章节）
   - 模型在生成 selector 时 hallucinate
   - 暂时搁置，未来若要复活需重新设计 IR schema（让 AI 只描述"模式"
     而非"逐章定位"）

3. **plan_1/2（AI script + outline）适合大书**：
   - 在毛选这种结构清晰的大书上稳定（394+ 章）
   - 代价：token 巨贵（每次 ~50 万 tokens）、慢（170-244s）
   - 作为 fallback 路线保留

4. **pandoc EPUB reader bug 影响面比想象的小**：
   - 只对「跨文件 + epub:type=rearnote」组合触发
   - 同一本书换 EPUB 来源（z-library 等用 footnote 模式）就不触发
   - 估计语料库整体命中率 5%-15%（之前估的 15%-25% 偏高）

5. **应对 pandoc bug 的方案排序**（待决策）：
   - **方案 A：EPUB 预处理器**（推荐）—— 一个轻量 Python/Lua 脚本，
     把 `<aside epub:type="rearnote">` 改写为 `<aside epub:type="footnote">`
     并把跨文件 href 改为同文件（或者干脆把 rearnote 内联回 noteref 旁边）。
     既保留 pandoc 的所有好处，又规避 bug。30-50 行代码量级。
   - 方案 B：per-file pandoc + 自己拼。要处理 id 命名空间冲突 + 跨文件
     href 重写，复杂度高。
   - 方案 C：完全脱开 pandoc 自己读 EPUB。30-50 行 Python，但失去
     pandoc 的 HTML 清理能力。
   - 方案 D：给 pandoc 上游报 issue 并等修复 —— 长期方案，不能堵当下。

6. **悲剧的诞生这种"信号削弱"问题**的两条路：
   - 在 facts 里加 "id 前缀双向配对" 检测（target_id_prefix top + 反向
     href 检测），帮 AI 识别没有 class 标记的注释
   - 在 prompt 里鼓励 AI 在 target_class_top 空但有大量同长度 id 前缀对
     时仍尝试识别
   优先级：低于 pandoc bug 的修复

## 13. 累积的代码改动清单

- `tests/step2_approaches/ai_utils.py`
  - default `max_tokens` 8000 → 32000
  - curl 超时 180s → 600s，subprocess 超时 660s
  - payload 加 `"thinking": {"type": "disabled"}`
  - `finish_reason="length"` 且空 content 时主动抛错

- `tests/step2_approaches/config.py`
  - 引入 `BOOK_NAME` 环境变量，支持多书切换
  - 输出目录按书名加后缀（除查拉图斯特拉默认外）

- `tests/step2_approaches/common/facts_extractor.py`
  - 新增 `body_children_profile`
  - 新增 `heading_wrapper_profile`
  - 新增 `heading_profile`
  - 新增 `in_document_link_summary`（含 target_class/parent_class/桶分布）
  - `NOTE_KEYWORDS` 扩展拼音简写
  - `TOC_KEYWORDS` 加中文「目录」/「mulu」

- `tests/step2_approaches/common/prompts.py`
  - 新增 "Recommended script architecture — multi-pass with global scans"
  - 新增 Pass 1/2/3 的明确产物要求
  - 新增 "Common bs4 pitfalls"（5 条）
  - 新增 "Picking the right chapter-heading level — DO NOT assume"
  - 新增 "Pandoc wrapping (CONDITIONAL — only when present)"

- 6 个 plan_N 的 `max_tokens` 全部从 8000 改成 32000

## 14. 仍然 open 的问题

1. pandoc EPUB reader 真 bug 的最终修复方案选择（方案 A 倾向）
2. 悲剧的诞生这种"class 被 strip"型 facts 信号削弱的处理
3. 是否引入第 4、5 本书继续验证 plan_3 的泛化性
4. plan_4/5/6（IR 路线）的彻底放弃 vs 重新设计
5. 选定主路线后，把 prompt + facts + script 模式回写到 Rust pipeline
   `src/pipeline/steps.rs::step2_normalize`

---

# 第二轮：IR 反查方案 + 方案 7（程序优先 + AI 只做语义）

实验时间：2026-05-22
模型：`deepseek-v4-pro`（`https://api.deepseek.com`，OpenAI 兼容协议，
curl 流式）
样本书：《可能性的艺术：比较政治学30讲》（刘瑜）、
《置身事内：中国政府与经济发展》（兰小欢）

## 15. 起因：重新审视 IR 的"逐章/逐注 selector"模式

跑 plan_1-6 时确认了第一轮的结论：IR 路线（plan_4-6）让 AI 给"逐章
title_selector + 逐注 selector"，规模一上去就塌方，且注释经常 0 识别。
具体诊断：

- **章节数严重错**：plan_4 在某本上只识 3 章（selector 把多章塌缩成一个
  命中），plan_3 在另一本识到 117 章（把 h3 叶子节点全当章节）。两个极端
  的同一个 root cause —— **"挑一个扁平 heading level 当章节"本身就是错的**。
- **note ground-truth 厘清**：plan_3 报 66 条注释，实为虚标 —— 它把每对
  noteref 的两个锚点都算成 note，66 = 33×2，且 note body 是空壳。真实注释
  数是 **33 条**（每条 ~85 字正文）。

## 16. 给 IR 增加 by-ref-target（反查）注释方案

针对 pandoc-from-EPUB 常见的"注释体散落、无共享 wrapper"布局（每条 note
body 是紧跟章节正文的裸 `<p>`），原来的 `container_selector + item_selector`
完全失效。新增第二种策略：

- `ir_schema.py`：`notes.strategy` 增加 `"container" | "by-ref-target"`，
  `by-ref-target` 要求 `noterefs.selector`。
- `ir_engine.py`：新增 `_collect_notes_by_ref()` —— 跟随每个 in-text
  noteref 的 href 到带该 id 的目标元素，向上爬到最近的 block 祖先当 note
  body。**用属性精确相等匹配 id**（不是 CSS `#id` 选择器），因此 pandoc
  里含 `#`/`.` 的非 CSS-safe id 也能解析。
- 顺手修了 `_rewrite_noterefs`：原来的 href_pattern 正则 `[^"#]+` 把含
  `#` 的 pandoc id 排除了，导致 noteref 改写后 0 命中。改为用
  `note_id_map` 成员判定（在映射里就改写），去掉脆弱的正则门。
- `prompts.py`：IR_SYSTEM 增加 by-ref-target 的使用指引（pandoc EPUB
  优先用它；noterefs.selector 必须只匹配正文里的引用锚，不能匹配 note
  body 内部的回跳锚，否则正文段会被误当注释）。

修完后在《可能性的艺术》上本地验证：33 注释 / 33 noteref / 0 orphan，对了。

## 17. 架构转向：能枚举的分支用程序，语义判断才交给 AI

用户提出核心思路（原话）：

> "能用程序精确实现的尽可能用程序……分支有确定个数枚举就用程序，
> 需要 AI 判断的用 AI。"

落到 step2 上的拆分：

- **程序确定性做的**（分支有限、可枚举、机械）：
  - 骨架组装（html/head/meta/title/body/main#book/bodymatter/notes）
  - 标题树的**层级嵌套**（用栈：碰到 level L 就 pop 掉栈顶 level≥L 的，
    depth=栈深+1，depth==1 → chapter+h1，否则 section+h{depth}）
  - **注释配对**（利用脚注双向不变量：正文 `<a href="#X">` ↔ 注释体内
    `<a id="X" href="#refid">`，按 id 精确配对，note body = 其锚点为
    block 内首元素的那个 block）
- **只交给 AI 的**（非枚举、语义）：每个标题在书名之下的**逻辑层级**，
  以及 skip（重复书名）/ merge（被拆断的标题续行）。

这就是 **plan_7（program-first + AI labels）**。

## 18. 方案 7 的关键设计决策：AI 只输出 "level X"，不输出"篇/章/节"

中间版本让 AI 按 heading **签名**（tag+class）打标，结果在《置身事内》翻车：

- `篇`（class=parttitle-c）的 level 排到了 `章` 下面
- 同一个签名 `prefacetitle-c` 横跨多个角色（前言 / 上篇 / 下篇标题）

→ 说明**按签名打标根本不成立**：同一个 class 在书里可以是不同层级。

用户明确最终诉求（原话）：

> "我要的最终效果是：书名下面的第一层级就是 h1，第二层级就是 h2，
> 而不是局限在'篇''章'这种东西里。"
> "不要让 AI 输出是篇还是章，让 AI 输出 level x 就好了。"

**改为 Option A（按每个标题出现位置逐个打 level）**：

- 给 AI 的签名表（`_outline`）现在是按文档顺序的逐行清单，每行带
  `{i, tag, class, text[:60]}`，并**附上该标题所在的源码行号**
  （bs4 `sourceline`，应用户要求加入）。
- AI 返回 `{"document":{title,language}, "headings":[
  {"i":idx,"level":int} | {"i":idx,"skip":true} | {"i":idx,"merge":true}]}`。
  - level 1 = 书名直接下级；更深 = 父级 level + 1；不允许层级跳跃 >1。
  - skip = 丢弃重复的书名标题；merge = 与前一标题合并成完整标题（如
    被拆断的「第一章 / 全球视野」）。
  - 同一个 class 可以是不同 level；收尾性章节（结束语、参考文献）回到
    level 1。
- 程序按文档顺序索引 `{id(h): i}` 对齐 AI 决策，再用上面的栈算法把
  level 序列翻译成 h1>h2>h3>h4 的真实嵌套。

### merge 方向 bug

第一版 merge 把标题**向后**贴到了**前一个**标题上 —— 但「第一章」在前、
「全球视野」在后，结果「全球视野」漏给了上一节。改为**向前 merge**
（pending_prefix 前缀到下一个开启的 section），验证得到「第一章 全球视野」。

## 19. 方案 7 在两本书上的结果

| 书 | 耗时 | tokens | 顶层(h1) | sections | note/noteref | orphan | jump | 状态 |
|----|----:|-------:|--------:|---------:|:------------:|-------:|-----:|------|
| 可能性的艺术 | 38.5s | 11.1k | 8 | 147 | 33 / 33 | 0 | 0 | PASS |
| 置身事内 | 36.4s | 7.2k | 6 | 106 | 311 / 311 | 0 | 0 | PASS |

层级完全符合预期：

- **可能性的艺术**顶层 8 项：序言、（重复书名一项被识别）、第一~五章、
  参考书目；每章下的「节」正确降到 h2/h3，split-title「第一章 全球视野」
  合并成功。
- **置身事内**顶层 6 项：目录、前言、上篇、下篇、结束语、参考文献；
  第一~八章作为 h2 挂在所属「篇」下，「节」h3、「小标题」h4，
  结束语/参考文献正确 pop 回 h1。与用户截图里的目标目录逐项一致。

token 成本（7-11k）和耗时（~37s）都在可接受范围，且**注释 0 orphan 是程序
保证的**（不依赖 AI 的 selector），比 IR 路线稳得多。

## 20. 本轮结论

1. **plan_7 是目前最稳的方案**：把"会塌方"的部分（章节切分、注释配对）
   全部交给确定性程序，AI 只回答它真正擅长且无法枚举的问题（每个标题的
   逻辑层级 + skip/merge）。两本结构差异很大的书都拿到正确层级 + 0 orphan。
2. **"AI 输出 level 而非语义标签（篇/章）"是关键**：避免了同一 class 跨
   角色、以及把书特定词汇焊进逻辑的问题，泛化性明显更好。
3. IR 反查方案（by-ref-target）让 plan_4-6 的注释能跑通，但章节切分的根本
   缺陷仍在，IR 路线整体仍不如 plan_7。

## 21. 仍然 open 的问题（本轮新增）

1. plan_7 只在 2 本书上验证过，n 仍然太小 —— 需要更大、结构更杂的测试集
   （目录树深度 >4、混合 part/chapter/无 part 的书、纯散文集等）。
2. evaluator 偏松：不校验骨架完整性、不查层级 sanity、不查空注释，会把
   结构性损坏的输出判 PASS。需要补 skeleton + hierarchy + non-empty-note
   三类断言。
3. plan_7 依赖 bs4 `sourceline`，若上游 HTML 经过重排可能行号失真 —— 待
   确认行号信息对 AI 判断的实际增益有多大。
4. 选定 plan_7 为主路线后，回写到 Rust pipeline 的工作量评估（栈算法 +
   注释配对在 Rust 侧重写 vs 调 Python）。
