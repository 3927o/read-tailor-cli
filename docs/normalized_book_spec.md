# 规范化书籍统一契约（Normalized Book Spec）

**版本**：`nb-1.0`
**修订**：2026-07-09（patch：hN 重算显式化、符号分隔符字符守恒、note 空壳禁令与容器边界、TOC 条目权威来源，见 §18.2；上一版 2026-07-08 patch 见 §18.1）
**状态**：normative（本项目内所有 ingester 的唯一规范目标）
**取代**：
- `tests/step2_approaches/html_standard.md`（已过时，仅保留通用最佳实践思想被本文吸收）
- `tests/step2_approaches/common/prompts.py` 中 `TARGET_SKELETON` 常量（该常量应改为从本文档抽取或引用）

---

## 0. 目的与范围

本规范定义一份**格式无关**的"规范化书籍"目标结构：任何源格式（EPUB、PDF、docx、Markdown、纯文本、mobi/azw、扫描 OCR 等）经各自 ingester 处理后，产物都应符合本规范定义的**统一 HTML 序列化形式**。

本规范只描述"产物长什么样"，**不规定"如何从源格式转过来"**。每种源格式的转换策略由各自 ingester 决定，但必须以本规范为目标。

---

## 1. 分层设计

规范分三层，各层职责严格分离，实现时不得相互侵入：

| 层 | 承载物 | 用途 |
|---|---|---|
| **结构语义层** | `data-role`、`data-type` | 描述"这是什么"（章、节、脚注、图、表、目录、未知兜底…）。词表基于 W3C EPUB 3 Structural Semantics Vocabulary（下称 **EPUB 3 SSV**），是跨格式共享的语言。 |
| **样式表现层** | `class` | 仅用于样式挂钩，**不得**承载语义。禁止 inline `style`。 |
| **溯源层** | `data-src-*` | 可选。记录该节点在原文档里的坐标（页码、原文件名、源标签名、原始 id 等），供反查、评估、回滚使用。 |

**唯一原则**：任何下游消费者查询语义时，**只能**依赖 `data-role` / `data-type`，永远不得依赖 `class` 名或 `id` 内容。

---

## 2. 文档骨架

```html
<!DOCTYPE html>
<html lang="[BCP-47 语言码，如 zh-CN / en / und]">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>[书名]</title>
  <meta name="author" content="[作者，未知留空]">
  <meta name="generator" content="[生产工具标识，如 read-tailor/nb-1.0]">
  <meta name="source-format" content="epub|pdf|docx|md|txt|mobi|ocr|html">
  <meta name="normalized-spec" content="nb-1.0">
</head>
<body>
  <main id="book" data-type="book">
    <!-- 可选：nav -->
    <!-- 可选：frontmatter -->
    <section id="bodymatter" data-role="bodymatter">
      <!-- 章节内容 -->
    </section>
    <!-- 可选：backmatter -->
    <!-- 可选：notes -->
  </main>
</body>
</html>
```

### 2.1 必需 head 字段
- `<meta charset="utf-8">`（强制）
- `<title>`（若无法解析可填与 `<h1>` 相同的内容，不得为空）
- `<meta name="source-format">`（枚举见上，未知源填 `unknown`）
- `<meta name="normalized-spec">`（当前统一为 `nb-1.0`）

### 2.2 可选但推荐 head 字段
- `<meta name="viewport">`
- `<meta name="author">`
- `<meta name="generator">`
- `<html lang>` 无法确定时填 `und`

---

## 3. 顶层区域词表（`main#book` 的直接子节点）

顺序按下列出现次序：

```html
<main id="book" data-type="book">
  <nav data-role="toc" id="toc">…</nav>                         <!-- 可选 -->
  <section data-role="frontmatter" id="frontmatter">…</section> <!-- 可选 -->
  <section data-role="bodymatter"  id="bodymatter">…</section>  <!-- 必需 -->
  <section data-role="backmatter"  id="backmatter">…</section>  <!-- 可选 -->
  <section data-role="notes"       id="book-notes">…</section>  <!-- 可选 -->
</main>
```

- `bodymatter` 是唯一必需的顶层区域；其他四项按源文档实际情况选择性生成，不得凭空捏造。
- 若源文档没有目录，**不得**自动生成 TOC；`data-role="toc"` 只用于反映源里已有的目录结构。
- 所有实际存在的顶层 `nav` / `section` 必须带稳定且全局唯一的 `id`。

---

## 4. 章节与标题

### 4.1 章节容器

书内所有章节以嵌套 `<section>` 表示，`data-type` 指明该 section 是哪种结构单元：

```html
<section data-type="chapter" id="ch-001">
  <h1>章标题</h1>
  <p>…</p>
  <section data-type="section" id="ch-001-sec-01">
    <h2>节标题</h2>
    <p>…</p>
  </section>
</section>
```

**`data-type` 词表分两类**（源自 EPUB 3 SSV，按需扩展）：

**容器型**（可嵌套子章节）：`book` | `part` | `chapter` | `section` | `subsection`

**叶节点型**（不再嵌套子章节，只装内容）：
`preface` | `foreword` | `introduction` | `epigraph` | `dedication` | `titlepage` | `colophon` | `preamble` | `prologue` | `epilogue` | `appendix` | `afterword` | `bibliography` | `glossary` | `index` | `acknowledgments` | `abstract`

**容器型的隐含层级顺序**（从粗到细）：

```
book ⊃ part ⊃ chapter ⊃ section ⊃ subsection
```

嵌套时**必须**从粗到细单调下降；允许跳过中间层级（如 `part` 直接嵌 `section` 可以），但反向嵌套（如 `chapter` 里嵌 `part`）严禁。

**结构性子章节必须是父章节的直接子元素。**`section[data-type]` 的直接父级只能是另一个
`section[data-type]`，或 `frontmatter` / `bodymatter` / `backmatter` 顶层区域。不得为了
保留源排版而把结构性 section 包在 `div`、`span` 或 `data-role="unknown"` 中间层里。

**选择原则**：
- 词表内**优先**使用语义最贴切的值。版权/出版信息用 `colophon`、题记用 `epigraph`、扉页用 `titlepage`、他人所写的前言用 `foreword`、作者自己的前言用 `preface`——**不得**因为省事统一用 `chapter`。
- 不在 EPUB 3 SSV 中的类型不得使用，除非有充分理由并在实现里记录。

### 4.2 标题层级

- 每个"章节容器"（`data-type` 为 §4.1 容器型词表内的值）**必须**以一个 `<hN>` 作为**首元素**，作为该容器的标题；**整个容器内不得再出现同层或更深层的 hN**。若需要更多标题层，必须嵌套子 `<section>` 承载。
- **豁免**：以下容器**不要求**带 hN 标题：
  - 顶层区域容器（`data-role` 为 `bodymatter`/`frontmatter`/`backmatter`/`notes`/`toc`）
  - `data-type` 为 `epigraph` / `dedication` 的容器（题记、献辞通常无标题）
- **深度到标签的映射（按 `<section>` 的 DOM 嵌套深度，不是语义深度）**：
  - `bodymatter` / `frontmatter` / `backmatter` 的直接子 section → `<h1>`
  - 深一层 → `<h2>`
  - 更深 → `<h3>` ... `<h6>`
  - 超过 6 层的深度全部封顶用 `<h6>`
- **源标题级别一律无效**：源文档里标题原本是 h1–h6 的哪一级不具有任何意义，**不得照抄**；N 必须按上述 DOM 嵌套深度重新计算。源为 `<h2>` 而产物深度为 1 时，输出 `<h1>`。
- **严禁跳级**：不得在源里有 `h1 → h4` 的情况下原样输出；必须按 section 嵌套深度重排。
- 采用 **HTML5 sectioning content 的多 h1 模型**：每个 `<section>` 里都可以有自己的 h1（如果它按 DOM 深度算属于顶层章）。不采用"h1 全局唯一"的旧教条。

### 4.2.1 同层同类型约束

**同一父容器下的、语义平级的兄弟章节容器，必须使用同一 `data-type` 值。**

反例：一本书的两个部分不得一个用 `part`、另一个用 `chapter`；一章下的两个节不得混用 `section` 和 `subsection`。

### 4.2.2 同类内容粒度一致

**同一本书内、语义上属于同一类的原子结构单元，必须用同一建模方式和同一 DOM 深度呈现。**

反例：一本书的两个部分，一个把每个 §编号评论建成独立 `<section>`（可锚点跳转），另一个把同类 §编号评论当同一父 section 内的 `<h3>` 兄弟处理（不能锚点跳转）—— 违反本条。修法：两个部分都独立成 section。

### 4.2.3 重复原子单元的建模

书中常出现的"重复原子结构单元"——编号评论/aphorisms、编号诗节、编号问答条目、词典词条、判例等——处理方式二选一：

**首选（推荐）**：每个原子单元独立成 `<section data-type="subsection">`（或按上下文的合适容器型），带 hN 标题（内容为编号或占位）和独立 `id`。这保证每个单元可锚点跳转、可被 TOC/交叉引用引到。

**次选**：若单元没有自然标题、且**明确**不需要单独锚点跳转，可用带 `data-role="unit"` 的容器块：
```html
<div data-role="unit" data-unit-num="7">…</div>
```

**严禁**把它们当同一父 section 内的 `<hN>` 兄弟处理——这会破坏 §4.2 的"容器内不得再出现同层 hN"约束。

一本书内所有同类原子单元必须选定同一种方式（详见 §4.2.2）。

### 4.3 章节 id 命名

**每个 `section[data-type]` 都必须带稳定 id。**下游阅读节点、目录定位、进度和精确锚点
均以 section id 为基础，不能依赖标题文本或临时 DOM 路径。

**id 全局唯一**：文档内所有 `id` **必须**全局唯一。虽然这是 HTML 硬性要求，但因跨格式 ingester 常在不同层各自机械命名导致冲突，本规范再次强调。

**命名规则**：
- 有稳定语义 id（源自源文档且被文档内其他锚点引用）→ **保留原 id**。
- 否则由 ingester 按 `data-type` 分家机械命名，前缀不得跨类型复用：

| `data-type` | 建议 id 前缀 | 示例 |
|---|---|---|
| `part` | `part-` | `part-001` |
| `chapter` | `ch-` | `ch-001` |
| `section` | `sec-` 或 `<父id>-sec-` | `part-001-sec-001` |
| `subsection` | `sub-` 或 `<父id>-sub-` | `ch-002-sub-005` |
| `preface` / `foreword` / `colophon` / `titlepage` / … | 类型英文名或缩写 | `preface-01`、`colophon`、`titlepage` |

前缀之后加三位零填数字。**同类型跨父的容器可以共用序号空间，也可以按父 id 命名空间化——但同一本书内必须选定其一并统一使用**（不得半新半旧）。

**id 改动同步**：命名后若原 id 被其他锚点引用，**必须**同步改写全部引用点，或在原位保留空锚点跳板（见 §12.2）。

### 4.4 段落

- 所有正文段落必须包裹在 `<p>` 中。
- **严禁**用 `<br><br>` 在 `<p>` 内模拟段落间距。
- **渲染后无可见文本内容**的 `<p>` 必须删除，无论内部是空、仅空白符、仅含 `<br>`、仅含空 `<span>` / `<a>` 空壳，还是任意组合。真的需要视觉留白的用 `<div data-role="separator">` 或样式层解决。

### 4.5 场景/意群分隔

```html
<div data-role="separator"></div>
```

原文档中 `<hr>`（表示场景切换）在规范化后**必须**转成上述结构标记，不得输出裸 `<hr>`（视觉分隔线让样式层处理）。

源用**纯符号段落**做场景分隔时（如 `<p>＊＊＊</p>`、`<p>* * *</p>`、`<p>———</p>`），同样转成 separator，但源里的可见字符**必须原样保留**在元素内：

```html
<div data-role="separator">＊＊＊</div>
```

这些符号是源的可见内容，受字符守恒约束，不得在转换中丢弃；`<hr>` 本身无字符，转换后保持空元素即可。

---

## 5. 图片与图注

```html
<figure data-role="figure" id="fig-001">
  <img src="assets/fig-001.png" alt="[源原样保留；源无 alt 时省略此属性]">
  <figcaption>可选的图注</figcaption>
</figure>
```

**判断是否需要 `<figure>` 包裹（纯结构判据，不猜"语义价值"）**：
- **必须包 `<figure>`**：`<img>` 是 `<section>` 或 `<div>` 的直接子；或独占一个 `<p>`（该 `<p>` 无其他实质文本内容）。
- **可裸露**：`<img>` 的父是 `<p>` / `<span>` / `<a>` / `<code>`，且该父块内**含其他文本节点**（即图片确实是行内元素，如公式里的 δ、章节前的花饰、内联表情符）。

**alt 规则（严格照抄源，不生成）**：
- 源有 `alt` 属性 → **原样保留**（包括源里的 `alt=""`——那表示源明确标为装饰性）。
- 源无 `alt` 属性 → **不加 `alt` 属性**。**严禁**由 ingester 根据"语义价值"生成描述——那是猜测、通常错、且不属于规范化范畴。

**其他规则**：
- 若源有图注，必须转成 `<figcaption>` 置于 `<img>` 之下。多段图注允许在 `<figcaption>` 内使用 `<p>`。
- **不得截断或擅自改写**任何资源路径。
- 网页产品书籍包必须把图片及其他媒体保存到 `assets/` 目录，HTML 使用 `assets/...`
  相对路径。规范化产物不得包含 data URI。
- 资源路径不得使用宿主机绝对路径，不得包含逃出书籍包根目录的 `..`，不得把临时签名 URL
  固化进规范化 HTML。相对资源必须在书籍包发布前验证实际存在。
- `<audio>` / `<video>` / `<source>` / `<track>` 的本地媒体 `src`，以及 `<video>` 的
  `poster`，使用相同的 `assets/...` 规则。
- 图片顺序编号：`fig-001`、`fig-002`…；若源有稳定 id，保留。

规范化书籍包的最小目录结构为：

```text
normalized-book/
├── book.normalized.html
└── assets/
    └── ...
```

---

## 6. 表格

```html
<table data-role="table" id="tbl-001">
  <caption>可选的表题</caption>
  <thead>
    <tr><th>表头1</th><th>表头2</th></tr>
  </thead>
  <tbody>
    <tr><td>数据1</td><td>数据2</td></tr>
  </tbody>
</table>
```

**规则**：
- 强制分区：即使源里没显式 thead/tbody，也必须至少有 `<tbody>`；有表头行则加 `<thead>`。
- **必须剥离**：`width`、`height`、`bgcolor`、`align`、`valign`、所有 inline `style`。
- 表格 class 可保留下游样式钩子（如 `class="numeric right-aligned"`），但**不得**用 class 承载语义。
- 合并单元格用 `rowspan` / `colspan`。
- 表格顺序编号：`tbl-001`、`tbl-002`…

---

## 7. 列表

- 无序列表：`<ul><li>…</li></ul>`
- 有序列表：`<ol><li>…</li></ol>`；若源列表从非 1 起始，保留 `start` 属性。
- 术语列表：`<dl>`，内部严格 `<dt>` / `<dd>` 配对。
- 允许嵌套列表；嵌套层级不做限制。
- 列表项内允许包含段落（`<p>`）、内嵌列表、图、表；**不得**用 `<br>` 模拟"多段列表项"。

---

## 8. 引用与代码

### 8.1 引用块

```html
<blockquote>
  <p>引用正文</p>
  <p class="attribution">— 出处（可选）</p>
</blockquote>
```

必须以 `<p>` 承载 `<blockquote>` 内的文本，不得裸文本。

### 8.2 行内代码

```html
<code>inline_code</code>
```

### 8.3 代码块

```html
<pre><code class="language-python">
code lines
</code></pre>
```

- 强制 `<pre><code>` 双层结构。
- 如果源明确标注了语言，附 `class="language-<lang>"` 于 `<code>` 上（不猜测）。
- **不得**用 `<pre>` 承载非代码内容（如诗歌）；诗歌用 `<p>` + `<br>`（见 §9.2 例外）或专门标记 `data-role="verse"`。

---

## 9. 行内文本

### 9.1 强调与格式

| 语义 | 必须使用 | 严禁使用 |
|---|---|---|
| 强调（重要）| `<strong>` | `<b>` |
| 语气（斜体强调）| `<em>` | `<i>` |
| 下划线 | `<u>` | inline style |
| 删除线 | `<s>` | inline style |
| 上标 | `<sup>` | inline style |
| 下标 | `<sub>` | inline style |

- ingester 遇到源里的 `<b>`/`<i>` 必须重命名为 `<strong>`/`<em>`。
- `<sup>` **不再**用于脚注引用（脚注引用见 §10）。`<sup>` 仅用于真的上标（如 x²、脚注编号数字）。

### 9.2 特殊换行

诗歌、地址、代码里保留原始换行时，使用 `<br>`。**其他任何场景**不得使用 `<br>` 模拟段落间距。

### 9.3 数学公式（可选支持）

若源含公式：

```html
<span data-role="math" data-math-format="latex">E = mc^2</span>
```

- `data-math-format` 允许 `latex` | `mathml` | `asciimath` | `text`。
- 复杂多行公式用 `<div data-role="math">`。

---

## 10. 脚注、尾注、章末注

统一使用**同一套**引用/正文分离结构，不区分脚注/尾注/章末注在文档骨架层面的差异，语义差异用 `data-note-kind` 记录。

### 10.1 正文中的引用（noteref）

```html
<a data-role="noteref"
   href="#note-0001"
   id="ref-00001">[1]</a>
```

- **必须**使用 `<a data-role="noteref">`，不得包裹 `<sup>`。
- `href` 必须指向对应 note 的 id。
- `id` 使用 `ref-<五位序号>` 机械命名；同一 note 被多次引用时，每个 noteref 各自 `id="ref-XXXXX"`，全部 `href` 指向**同一个** note id。

### 10.2 注释正文区

```html
<section data-role="notes" id="book-notes">
  <div data-role="note"
       id="note-0001"
       data-note-kind="footnote">
    <p>注释正文。<a href="#ref-00001" data-role="backref">↩</a></p>
  </div>
</section>
```

- 所有 note 正文**必须**汇总在文档尾部 `<section data-role="notes">` 里，不得分散在正文各章节内。
- 每条 note 是一个带 `data-role="note"` 的块级容器（推荐 `<div>`），带机械 id `note-<四位序号>`。
- `data-note-kind` 可选值：`footnote` | `endnote` | `chapter-note` | `sidenote`。识别不出时省略该属性。
- 回跳链接（backref）可选；若生成，用 `data-role="backref"`。
- 注释正文里允许出现 §5–§9 定义的任何内容元素（图、表、列表、代码、行内格式等）。
- **严禁空壳 note**：每条 note 必须包含该注释的实际正文（可见文本）。把注释正文留在正文章节里、notes 区只放无文本的结构占位，属于**违规**——这正是本条"正文必须汇总在 notes 区"要禁止的形态，不是它的替代实现。
- **note 容器边界**：语义上属于同一条注释的全部内容，包括紧随注释段落的兄弟级块（注释内插图的 `<div>`、续段等），必须一并包入同一个 `<div data-role="note">`，不得散落在容器外。

### 10.3 配对不变式

- 每个 note 至少被一个 noteref 引用；**未被引用的孤儿 note** 应当保留但打上 `data-orphan="true"`。
- 每个 noteref 必须能解析到一个 note；解析失败的引用**必须**打上 `data-broken="true"` 并保留原文可见文本。

---

## 11. 目录（TOC）

```html
<nav data-role="toc" id="toc">
  <ol>
    <li><a href="#ch-001">第一章 …</a>
      <ol>
        <li><a href="#ch-001-sec-01">第一节 …</a></li>
      </ol>
    </li>
    <li><a href="#ch-002">第二章 …</a></li>
  </ol>
</nav>
```

**规则**：
- 只有当**源文档存在目录**（EPUB 的 nav.xhtml、docx 的 Table of Contents field、md 的 `[TOC]` 等）时才生成；ingester 不得凭空造 TOC。
- **条目的权威来源是源的导航元数据**：EPUB 取 nav 文档（EPUB 3），缺失时取 NCX（EPUB 2）；正文里渲染出来的"目录页"（普通内容页）**不是**权威来源。渲染目录页与导航元数据条目不一致时，以导航元数据为准。
- 必须使用嵌套 `<ol>`（表达顺序），不得用 `<ul>`。
- 每个 `<li>` 的第一个子元素必须是 `<a href="#…">`，指向文档内实际存在的 id。
- **必须丢弃**：源 TOC 中的页码、点阵引导（`.....`）、纯排版对齐样式。
- TOC 位置：若源 TOC 明显在书首（如 EPUB nav）→ 放在 `frontmatter` 之前；若在书尾（如某些 PDF）→ 放在 `backmatter` 之后（但**总在** `notes` 之前）。

---

## 12. 链接与锚点

### 12.1 外部链接

```html
<a href="https://example.com" target="_blank" rel="noopener noreferrer">…</a>
```

- 所有外部链接**必须**强制注入 `target="_blank"` 和 `rel="noopener noreferrer"`。
- 判定外部链接：`href` 以 `http://` / `https://` / `mailto:` / `tel:` 开头。

### 12.2 内部锚点

- 所有内部锚点 `href` 必须以 `#` 开头，且目标 id 必须**存在于文档内**。
- 无法解析的内部锚点必须打上 `data-broken="true"` 并保留原始 `href`（供反查）。
- **id 命名改动的同步**：ingester 若把源 id `x` 改成 `ch-001`，必须（择一）：
  - a) 同步改写**所有**指向 `#x` 的 href；或
  - b) 在新 id 所在节点内保留空锚点跳板：`<span id="x"></span>`。

---

## 13. 溯源属性（`data-src-*`）

**全部可选**，但强烈建议 ingester 尽可能填写，供评估、反查、回滚使用。

| 属性 | 适用来源 | 说明 |
|---|---|---|
| `data-src-page` | PDF、扫描 OCR | 该节点内容所在的原始页码（1-based） |
| `data-src-file` | EPUB、多文件 HTML | 该节点内容所在的原文件名（不含路径） |
| `data-src-line` | Markdown、纯文本 | 该节点内容在源文件里的起始行号 |
| `data-src-tag` | HTML、EPUB | 该节点在源里原本的标签名（如 `div.calibre1`） |
| `data-src-id` | 任意 | 该节点在源里的原始 id（若被本规范改名） |
| `data-src-role` | 任意 | 源里对应的语义标记（如 EPUB 的 `epub:type="chapter"`） |

原则：**只标注够用即可**，粒度自行决定（可只标章、可标到段）。不得为了溯源而在每个 `<p>` 上叠一堆 `data-src-*`。

---

## 14. 不确定内容的兜底（unknown）

任何 ingester 无法可靠分类的内容**必须**保留而非删除，包裹为：

```html
<div data-role="unknown" data-reason="short_structural_reason">
  <!-- 原内容照抄进来（去内联样式，其他不改）-->
</div>
```

- `data-reason` 用短字符串说明为什么归为 unknown（如 `ambiguous_heading_level`、`unrecognized_block_wrapper`、`orphan_footnote_body`），**不得含书本具体文本**。
- 内容保真优先于分类正确：宁可打 unknown，不可静默丢内容。

---

## 15. 通用禁令与清理规则

以下规则对全体规范化 HTML 有效，不管出现在哪一节：

1. **严禁** inline `style="…"` —— ingester 必须剥离全部内联样式。
2. **严禁**无语义包裹 `<div>` / `<span>`。任何无属性的 `div` / `span`，即使内部含有
   可见正文，也必须剥壳并原样保留其内部内容；带有明确 `data-role`、`id`、有效样式挂钩
   或行内语义的容器不在此列。
3. **必须**清洗源转换工具带的冗余 class（如 pandoc 的 `calibre*`、`sgc*`；docx 的 `mso-*`；等）。样式 class 只保留 ingester 主动想留的。
4. **严禁**在规范化产物里出现源工具的 XML 命名空间前缀（如 `epub:type`、`opf:*`、`ncx:*`），有需要一律翻译成 `data-*`。
5. **严禁**空 `<a>`、空 `<p>`（见 §4.4 的完整定义）、空 `<li>`、空 `<td>`（除非它是 id 跳板 `<span id="…"></span>`）。
6. **严禁**保留 `<script>`、`<link rel="stylesheet">`、`<style>`、`<iframe>`、`<object>`、`<embed>`。
7. **`id` 必须全局唯一**（见 §4.3）——尤其注意跨 `data-type` 前缀分家，不得出现 part 和 chapter 共用同一 id。

---

## 16. 合规检查清单（面向 ingester 实现方）

一份产物只有全部满足下列条件才算符合本规范：

**骨架**
- [ ] 文档骨架（§2）齐全；`normalized-spec="nb-1.0"` 已声明。
- [ ] `<main id="book" data-type="book">` 直接子节点均在 §3 词表内。

**层级 & 类型**
- [ ] 每个"章节容器"（§4.1 容器型）首元素是 hN，且**该容器内无同层或更深层的额外 hN**（§4.2）。
- [ ] hN 的 N = 该 section 的 DOM 嵌套深度（`bodymatter` 直接子 = 1、深一层 = 2…）；源标题级别一律无效，必须重算（§4.2）。
- [ ] 无 hN 跳级（§4.2）。
- [ ] 词表选值贴近语义（不用 `chapter` 装版权页/题记/前言等）（§4.1）。
- [ ] 容器型嵌套单调从粗到细（`book ⊃ part ⊃ chapter ⊃ section ⊃ subsection`）（§4.1）。
- [ ] 结构性子 section 直接位于父 section 下，未藏在 `div`/`span`/`unknown` wrapper 中（§4.1）。
- [ ] **同一父下的平级兄弟容器使用同一 `data-type`**（§4.2.1）。
- [ ] **同一书内的同类原子单元用同一建模方式和同一 DOM 深度**（§4.2.2）。
- [ ] 编号评论等重复原子单元按 §4.2.3 处理（各自独立成 section 或 `<div data-role="unit">`；不得当兄弟 hN）。

**id**
- [ ] 所有顶层区域和每个 `section[data-type]` 均有稳定 id（§3、§4.3）。
- [ ] **所有 `id` 全局唯一**（§4.3、§15.7）。
- [ ] id 按 `data-type` 分家命名，前缀不跨类型复用（§4.3）。

**内容元素**
- [ ] 所有语义都由 `data-role` / `data-type` 承载；无一处语义搭在 class 上（§1）。
- [ ] 图片：符合 §5 结构判据的用 `<figure>` 包，行内小图标裸露；`alt` **原样保留自源，源无则不加**——**不得 ingester 生成**。
- [ ] 表格分区正确（至少 tbody；有表头则 thead）；已剥离 width/height/inline style（§6）。
- [ ] 空 `<p>`（渲染后无可见文本）已删除；`<br>` 未被滥用（§4.4、§15.5）。
- [ ] 分隔：无裸 `<hr>`；符号型分隔段转 separator 时可见字符原样保留（§4.5）。
- [ ] TOC：条目集合与源导航元数据一致（EPUB：nav，缺失时 NCX；渲染目录页不作数）（§11）。

**脚注 & 链接**
- [ ] 脚注：noteref 有 `data-role="noteref"` 且 href 可解析；note 用 `data-role="note"` + `data-role="notes"` 容器（§10）。
- [ ] 每条 note 含该注释的可见正文（无空壳）；属于同一注释的内容全部在 note 容器内（§10.2）。
- [ ] 外链有 `target="_blank" rel="noopener noreferrer"`（§12.1）。
- [ ] 内锚点全部可解析，或 `data-broken="true"` 明标（§12.2）。

**清洁度**
- [ ] 无属性 `div`/`span` 已剥壳；无 inline style；无 pandoc/calibre/mso 冗余 class；无 `<b>`/`<i>`；无 `<script>`/`<style>`/`<iframe>`；无 epub:/opf: 命名空间残留（§15）。

**内容保真**
- [ ] 信心不足处用 `<div data-role="unknown">` 兜底而非删除（§14）。
- [ ] 媒体引用均为安全的 `assets/...` 路径，文件真实存在，并通过源 EPUB 资源守恒对账；
      规范化产物中不存在 data URI（§5）。
- [ ] `char_recall`（输出可见字符 / 源可见字符）≥ 99.9%。

---

## 17. 与既有实现的差距摘要

| 实现 | 差距（相对本规范） | 建议动作 |
|---|---|---|
| `plan_7_program_first.py` 骨架 | 无 TOC；无 unknown 兜底；无 figure 包装；表格未清洗；无 `normalized-spec` meta；id 跳板未处理；重复原子单元建模指南未实现 | 逐项补齐（TOC 由 heading tree 直接生成、其余按 §5/§6/§14 落地） |
| `common/prompts.py::TARGET_SKELETON` | 覆盖窄；未涉及 §4.2.1–§4.2.3、§5、§6、§7、§8、§9、§11、§12、§13 | 改为引用本文档，不再重复维护 |
| `tests/step2_approaches/html_standard.md` | 架构不兼容（class 承载语义、h1 全局唯一、自造类名） | 标记 deprecated；其中通用最佳实践思想已被本文吸收 |
| `common/ir_schema.py` (`ir-1.0`) | 与本规范骨架兼容，但 selectors 里 role 集合窄；未涵盖 §4.1 容器/叶节点分类 | 下版本 `ir-2.0` 扩展 role 值域到 §3/§4.1 全集 |

---

## 18. 版本策略

- **补丁级改动**（增加词表值、澄清措辞、堵漏洞而不改变已合规产物的骨架）→ 仍为 `nb-1.0`，更新 `修订` 行注明日期与修订内容。
- **不兼容改动**（骨架变化、`data-role` 语义变化、已合规产物需要重新处理）→ 升级为 `nb-2.0`，并保留 `nb-1.0` 段落说明迁移路径。
- 每次改动**必须**同步更新第 16 节的合规清单，并在 `<meta name="normalized-spec">` 中体现新版本号（跨大版本时）。

### 18.1 2026-07-08 patch 修订清单

该次修订全部为**澄清/堵漏**，不改变已合规产物的骨架契约；已符合 nb-1.0 早期版本的产物不需要迁移，但**未来产物必须按新条款生成**。

| # | 修订点 | 影响 |
|---|---|---|
| 1 | §4.1 补：容器型 vs 叶节点型分家、隐含层级 `book ⊃ part ⊃ chapter ⊃ …` | 明确类型间关系，堵"平级用不同类型"漏洞 |
| 2 | §4.1 补：词表贴切度原则（不得 `chapter` 一把梭） | 明确类型选取指南 |
| 3 | §4.2 改：首元素 hN 唯一性表述（"整个容器内不得再出现同层或更深层 hN"） | 堵 R1 歧义 |
| 4 | §4.2 补：顶层区域和 epigraph/dedication 豁免 hN | 堵 R2 空缺 |
| 5 | §4.2 改：深度→N 明确为 DOM 嵌套深度 | 堵语义/DOM 歧义 |
| 6 | §4.2.1 新增：同层同类型约束 | 新增一致性 |
| 7 | §4.2.2 新增：同类内容粒度一致 | 新增一致性 |
| 8 | §4.2.3 新增：重复原子单元建模指南 | 新增建模指引 |
| 9 | §4.3 补：id 全局唯一、按 data-type 分家的命名前缀表 | 堵 id 冲突漏洞 |
| 10 | §4.4 改：空段落定义为"渲染后无可见文本内容" | 堵 `<p><br></p>` 漏洞 |
| 11 | §5 改：figure 包裹改为纯结构判据（父类型 + 是否含其他文本） | 堵 R3 主观词漏洞 |
| 12 | §5 改：alt 规则——**源有则原样保留，源无则不加 alt 属性**；严禁 ingester 生成 | 修正早期"源无 alt 置空"的语义错误设计 |
| 13 | §15 补：id 全局唯一列入通用禁令 | 强化 |
| 14 | §16 合规清单：全面补齐上述新条款的对应检查项 | 保持清单与正文同步 |

### 18.2 2026-07-09 patch 修订清单

来自三本书的 agent 实测反馈，全部为**澄清/堵漏**，不改变已合规产物的骨架契约。

| # | 修订点 | 影响 |
|---|---|---|
| 1 | §4.2 补：源标题级别一律无效，N 按产物 DOM 深度重算 | 两个不同 agent 首轮均照抄源 `<h2>` 被打回；显式化省一轮迭代 |
| 2 | §4.5 补：纯符号分隔段（＊＊＊ 等）转 separator 时保留可见字符 | 消除"语义转换 vs 字符守恒"的两难 |
| 3 | §10.2 补：严禁空壳 note；note 容器边界（兄弟级块一并包入） | 堵"正文留原位 + notes 区放空壳"绕过 §10.2 的漏洞（nb_linter 同步新增空壳检查） |
| 4 | §11 补：TOC 条目权威来源 = nav（缺失时 NCX），渲染目录页不作数 | 消除规范与校验器 TOC 对账判据之间的空白 |
| 5 | §16 合规清单：同步上述条款 | 保持清单与正文同步 |
