import re
from bs4 import BeautifulSoup, Comment

class PandocHtmlCleaner:
    def __init__(self, html_content):
        # 使用 lxml 解析以保证对混乱 DOM 的容错性
        self.soup = BeautifulSoup(html_content, 'lxml')
        # 构建符合规范的基础骨架
        self.new_soup = BeautifulSoup(
            '<!DOCTYPE html>\n'
            '<html lang="zh-CN">\n<head>\n'
            '    <meta charset="UTF-8">\n'
            '    <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
            '    <title>未知标题</title>\n'
            '    <meta name="author" content="">\n'
            '    <meta name="generator" content="YourProjectName-Pipeline">\n'
            '    <meta name="source-format" content="epub">\n'
            '</head>\n<body>\n'
            '    <article class="book-content"></article>\n'
            '</body>\n</html>', 
            'lxml'
        )
        self.article = self.new_soup.find('article')

    def run_pipeline(self):
        """执行所有清理和转换流程（注意执行顺序）"""
        self._extract_metadata()
        self._clean_meaningless_wrappers()
        self._process_images()
        self._process_tables()
        self._process_links()
        self._process_inline_tags()
        self._process_paragraphs()
        self._process_footnotes()
        self._process_toc()
        self._migrate_content()
        self._final_cleanup()
        return str(self.new_soup)

    def _extract_metadata(self):
        """提取标题和作者（如果有）"""
        title_tag = self.soup.find('title')
        if title_tag and title_tag.string:
            self.new_soup.title.string = title_tag.string

        # Pandoc 有时会将作者放在 meta name="author" 或特定的 h2/h3 中，这里简单提取 meta
        author_meta = self.soup.find('meta', {'name': 'author'})
        if author_meta and author_meta.get('content'):
            self.new_soup.find('meta', {'name': 'author'})['content'] = author_meta['content']

    def _clean_meaningless_wrappers(self):
        """规则 0 & 2: 剥离无意义的 div/span，删除内联样式，清理类名"""
        # 移除原文档的所有注释
        for comment in self.soup.find_all(text=lambda text: isinstance(text, Comment)):
            comment.extract()

        # 遍历所有元素，清除 style 和非标准 class
        for tag in self.soup.find_all(True):
            if tag.has_attr('style'):
                del tag['style']
            if tag.has_attr('class'):
                # 过滤掉以 calibre 或 sgc 开头的冗余类名
                clean_classes = [c for c in tag['class'] if not re.match(r'^(calibre|sgc).*', c)]
                if clean_classes:
                    tag['class'] = clean_classes
                else:
                    del tag['class']

        # 剥离无意义的 span 和 div，保留内容
        # 注意：排除带有 id（可能是锚点）或特定 role（如脚注容器）的标签
        for tag in self.soup.find_all(['span', 'div']):
            if tag.has_attr('id') or tag.has_attr('role') or tag.get('class'):
                continue
            tag.unwrap()

    def _process_images(self):
        """规则 4: 处理图片，从 SVG 中解包 Base64，构建 figure 结构"""
        # 应对 Pandoc 用 svg 包裹 image 的恶心操作
        for svg in self.soup.find_all('svg'):
            image_tag = svg.find('image')
            if image_tag and (image_tag.has_attr('href') or image_tag.has_attr('xlink:href')):
                src = image_tag.get('href') or image_tag.get('xlink:href')
                if src and src.startswith('data:image'):
                    new_img = self.soup.new_tag('img', src=src, alt="")
                    svg.replace_with(new_img)
            else:
                svg.decompose() # 无效 SVG 直接删除

        # 构建规范的 figure > img 结构
        for img in self.soup.find_all('img'):
            if not img.get('alt'):
                img['alt'] = "" # 强制空 alt
            
            # 寻找可能的图片说明 (Pandoc 通常会将说明放在紧跟的 <p class="caption"> 中)
            caption_text = ""
            next_p = img.find_next_sibling('p')
            if next_p and next_p.get('class') and 'caption' in next_p['class']:
                caption_text = next_p.get_text(strip=True)
                next_p.decompose()

            figure = self.soup.new_tag('figure', **{'class': 'media-image'})
            img.wrap(figure)
            
            if caption_text:
                figcaption = self.soup.new_tag('figcaption')
                figcaption.string = caption_text
                figure.append(figcaption)

    def _process_tables(self):
        """规则 5: 表格规范化"""
        for table in self.soup.find_all('table'):
            table['class'] = 'book-table'
            # 删除原有的 border, width 等属性
            for attr in ['border', 'width', 'cellpadding', 'cellspacing']:
                if table.has_attr(attr):
                    del table[attr]
            # 规范化 td/th 内的样式
            for cell in table.find_all(['td', 'th']):
                if cell.has_attr('width'):
                    del cell['width']

    def _process_links(self):
        """规则 6: 外部链接注入 _blank"""
        for a in self.soup.find_all('a'):
            href = a.get('href', '')
            if href.startswith('http://') or href.startswith('https://'):
                a['target'] = '_blank'
                a['rel'] = 'noopener noreferrer'

    def _process_inline_tags(self):
        """规则 3: 内联文本与代码替换"""
        replacements = {'b': 'strong', 'i': 'em'}
        for old_tag, new_tag in replacements.items():
            for tag in self.soup.find_all(old_tag):
                tag.name = new_tag

        # 处理代码语言标识
        for code in self.soup.find_all('code'):
            classes = code.get('class', [])
            for c in classes:
                if c.startswith('language-') or c == 'sourceCode':
                    # 假设 Pandoc 输出了 class="sourceCode python"
                    langs = [cls for cls in classes if cls != 'sourceCode']
                    if langs:
                        code['class'] = f"language-{langs[0]}"
                    break

    def _process_paragraphs(self):
        """规则 2: 清理空段落和段落内的连续 br"""
        for p in self.soup.find_all('p'):
            # 替换 <br><br> 为单个空格或直接删除
            for br in p.find_all('br'):
                next_sib = br.next_sibling
                if next_sib and next_sib.name == 'br':
                    br.extract()
                    next_sib.extract()
            
            # 删除空段落
            if not p.get_text(strip=True) and not p.find(['img', 'figure']):
                p.decompose()

    def _process_footnotes(self):
        """规则 8: 脚注转换"""
        # 1. 转换正文引用标记 (Pandoc 默认用 id="fnref1", class="footnote-ref")
        for ref in self.soup.find_all('a', role='doc-noteref'):
            fn_id = ref.get('href', '').replace('#', '')
            ref_id = ref.get('id', '')
            
            # 转换为 <sup><a href="#comment-X">[X]</a></sup>
            new_sup = self.soup.new_tag('sup', id=ref_id.replace('fnref', 'ref-'))
            new_a = self.soup.new_tag('a', href=f"#comment-{fn_id.replace('fn', '')}")
            new_a.string = ref.get_text()
            new_sup.append(new_a)
            ref.replace_with(new_sup)

        # 2. 转换底部注释区 (Pandoc 默认用 section role="doc-endnotes")
        footnotes_section = self.soup.find('section', role='doc-endnotes')
        if footnotes_section:
            aside = self.soup.new_tag('aside', **{'class': 'document-comments'})
            aside.append(self.soup.new_tag('hr', **{'class': 'comments-divider'}))
            ol = self.soup.new_tag('ol')
            aside.append(ol)

            for li in footnotes_section.find_all('li', role='doc-endnote'):
                old_id = li.get('id', '')
                new_id = old_id.replace('fn', 'comment-')
                li['id'] = new_id
                
                # 处理反向链接
                backlink = li.find('a', role='doc-backlink')
                if backlink:
                    backlink['class'] = 'backlink'
                    backlink['aria-label'] = '返回正文'
                    backlink['href'] = backlink['href'].replace('#fnref', '#ref-')
                    backlink.string = '↩'
                
                ol.append(li.extract())
            
            footnotes_section.replace_with(aside)

    def _process_toc(self):
        """规则 9: 目录处理"""
        # Pandoc 通常带有 id="TOC" 或 nav id="toc"
        toc = self.soup.find(id=re.compile('(?i)toc'))
        if toc:
            toc.name = 'nav'
            toc['id'] = 'toc'
            # 丢弃非 ol/ul/li/a 的其他修饰性标签
            for tag in toc.find_all(['span', 'div']):
                tag.unwrap()
            # 将 toc 移至文档最前方
            self.article.append(toc.extract())

    def _migrate_content(self):
        """将清洗后的 Body 内容整体迁移到规范的 <article> 中"""
        body = self.soup.find('body')
        if body:
            # 跳过已经被提取的 TOC，将剩下的元素全部放入 article
            for child in body.children:
                self.article.append(child.extract())

    def _final_cleanup(self):
        """收尾工作：合并连续的空行，美化输出"""
        # bs4 在 unwrap 后可能会留下多余的换行符，这里做简单清理
        pass


# ================= 使用示例 =================
if __name__ == "__main__":
    import sys

    # 模拟读取 Pandoc 生成的文件
    input_file = '/Users/richard/projects/read-tailor-cli/dist/查拉图斯特拉如是说/work/查拉图斯特拉如是说.raw.html'
    output_file = 'standard_book.html'

    # 实际使用时用这行代替：
    with open(input_file, 'r', encoding='utf-8') as f:
        html_content = f.read()

    cleaner = PandocHtmlCleaner(html_content)
    result_html = cleaner.run_pipeline()

    # 实际使用时写入文件：
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(result_html)
    
    print("转换完成")