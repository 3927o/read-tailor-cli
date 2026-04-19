# 统一规范 HTML (Unified Standard HTML) 描述文档

## 0. 核心原则 (Core Principles)
* **绝对的语义化**：只使用 HTML5 语义标签。**遇到无意义的包裹性 `<div>` 和 `<span>` 时，必须剥离这些标签，但强制保留其内部的文本和有效子元素**。
* **表现与内容彻底分离**：**严禁**任何内联样式（`style="..."`）。所有视觉呈现必须通过预定义的 `class` 配合外部 CSS 解决。
* **清理冗余**：必须清洗 Pandoc 默认生成的冗余类名（如 `calibre`、`sgc` 等）以及非标准的自定义属性。

## 1. 文档结构骨架 (Document Skeleton)
输出的 HTML 必须严格遵循以下基础结构：

```html
<!DOCTYPE html>
<html lang="[解析出的语言代码，默认 zh-CN]">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>[书籍/文档标题]</title>
    <meta name="author" content="[作者名，无法解析则留空]">
    <meta name="generator" content="YourProjectName-Pipeline">
    <meta name="source-format" content="epub">
</head>
<body>
    <article class="book-content">
        </article>
</body>
</html>
```

## 2. 块级元素与层级规范 (Block & Hierarchy)
* **章节划分**：使用 `<section>` 标签包裹每个独立的章节。必须带有 `id` 属性，`id` 命名优先使用原文档的有效语义 ID；若重命名为 `chapter-[序号]`，**必须同步修改全文中所有指向该锚点的引用链接**。
* **标题层级**：
    * `<h1>`：仅用于书名或顶级总标题，全局唯一。
    * `<h2>`：用于章标题 (Chapter Title)。
    * `<h3>`：用于节标题 (Section Title) 及以下。
    * **强制要求**：严禁跳跃使用标题层级（例如从 `<h2>` 直接跳到 `<h4>`），需要自行降级或合并冗余标题。
* **段落**：所有正文文本必须包裹在 `<p>` 中。**严禁在 `<p>` 内部使用 `<br><br>` 来模拟段落间距**。若遇到空段落 `<p></p>` 或仅包含空白符的段落，直接删除。
* **引用**：长篇引用必须使用 `<blockquote>`，内部必须嵌套 `<p>`。
* **场景分割线**：使用 `<hr class="scene-break">` 表示文本的场景切换或无标题分隔。

## 3. 内联文本与代码格式 (Inline Formatting & Code)
* **加粗/斜体**：强制使用 `<strong>` 和 `<em>`。**严禁**使用 `<b>` 和 `<i>`。
* **下划线/删除线**：分别使用 `<u>` 和 `<s>`。
* **代码**：
    * 行内代码使用 `<code>`。
    * 代码块强制使用 `<pre><code>...</code></pre>` 结构。
    * **语言标识**：如果 Pandoc 原生输出了带有语言标识的 class（如 `class="language-python"` 或 `class="sourceCode python"`），则必须提取为 `class="language-[lang]"` 附加在 `<code>` 上；如果没有，不应自行推测。

## 4. 媒体与资源规范 (Media & Assets)
* **图片 (Images - Base64 嵌入模式)**：
    * `<img>` 标签的 `src` 属性值为 `data:image/...` 格式的 Base64 数据，**必须完整保留，严禁截断或篡改**。
    * 忽略原文档中可能存在的 `<svg>` 或冗余 `<div>` 包装，直接提取核心 `<img>`。
    * 图片必须被 `<figure class="media-image">` 标签包裹（行内小图标除外）。
    * **必须**包含 `alt` 属性。如果源文件无描述，强制置为空字符串 `alt=""`。
    * 提取出的图片说明文字必须转换为 `<figcaption>`，置于 `<img>` 之下。
    * 结构示例：
        ```html
        <figure class="media-image">
            <img src="data:image/png;base64,..." alt="[图片描述或空]">
            <figcaption>[可选的图片说明]</figcaption>
        </figure>
        ```

## 5. 表格规范 (Tables) 【新增】
书籍内容若包含表格，必须规范化为以下结构，剥离所有原有的宽度控制（width）和内联样式：
```html
<table class="book-table">
    <thead>
        <tr>
            <th>表头1</th>
            <th>表头2</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td>数据1</td>
            <td>数据2</td>
        </tr>
    </tbody>
</table>
```

## 6. 链接与导航 (Links & Navigation)
* **内部链接**：锚点链接 `href` 必须以 `#` 开头，目标 ID 必须在文档中存在。
* **外部链接**：必须强制注入 `target="_blank"` 和 `rel="noopener noreferrer"` 属性。

## 7. 列表规范 (Lists)
* 无序列表：`<ul>` + `<li>`。
* 有序列表：`<ol>` + `<li>`。
* 术语定义：`<dl>`，内部严格使用 `<dt>`（术语）和 `<dd>`（定义）配对。

## 8. 注释与脚注规范 (Comments & Footnotes)
所有注释和脚注采用统一的结构处理，识别并转换 Pandoc 原有的注脚格式（如 `id="fn1"`），分为“正文引用标记”和“底部注释区”两部分。

* **正文引用标记 (Inline Reference)**：
    必须使用 `<sup>` 包裹 `<a>` 标签，跳转目标指向注释区的内容 ID。必须带有返回原处的锚点 ID。
    ```html
    <sup id="ref-1"><a href="#comment-1">[1]</a></sup>
    ```
* **底部注释区 (Comments Area)**：
    在 `<article>` 尾部，使用 `<aside>` 统一包裹所有注释内容。每一条注释必须包含返回正文对应标记的“反向链接 (Backlink)”。
    ```html
    <aside class="document-comments">
        <hr class="comments-divider">
        <ol>
            <li id="comment-1">
                <p>这里是具体的注释或脚注内容。<a href="#ref-1" class="backlink" aria-label="返回正文">↩</a></p>
            </li>
        </ol>
    </aside>
    ```

## 9. 目录处理 (Table of Contents - TOC)
如果源文档存在目录数据，将其放置在 `<article>` 的最前部，严格使用 `<nav id="toc">` 包裹，内部使用嵌套的 `<ol>` 结构呈现层级，链接指向文档内的章节 ID。**丢弃目录中所有用来排版页码的点阵符号或对齐样式**。