"""nb-1.0 规范化书籍统一契约的 Linter。

对着 docs/normalized_book_spec.md 逐节检查。修复了初版的 6 处 bug + 3 处误报，
补齐了 §16 合规清单里之前漏掉的约 20 项检查。

用法：
    python tools/nb_linter.py <path/to/book.html>

退出码：0 = 全部通过；1 = 有 error；2 = 只有 warning。
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from typing import Optional
from urllib.parse import unquote

from bs4 import BeautifulSoup, NavigableString, Tag


# ---------------------------------------------------------------------------
# 词表（源自 spec §3 / §4.1）
# ---------------------------------------------------------------------------

TOP_ROLES_ORDERED = ["toc", "frontmatter", "bodymatter", "backmatter", "notes"]
TOP_ROLES = set(TOP_ROLES_ORDERED)

# 容器型：可嵌套子章节。附带层级序号（book=0, part=1, chapter=2, section=3, subsection=4）
CONTAINER_TYPE_LEVEL = {
    "book": 0,
    "part": 1,
    "chapter": 2,
    "section": 3,
    "subsection": 4,
}
CONTAINER_TYPES = set(CONTAINER_TYPE_LEVEL.keys())

# 叶节点型：不再嵌套子章节。
LEAF_TYPES = {
    "preface", "foreword", "introduction", "epigraph", "dedication",
    "titlepage", "colophon", "preamble", "prologue", "epilogue",
    "appendix", "afterword", "bibliography", "glossary", "index",
    "acknowledgments", "abstract",
}
ALL_DATA_TYPES = CONTAINER_TYPES | LEAF_TYPES

# 无需 hN 标题的豁免（§4.2）
NO_HEADING_LEAF_TYPES = {"epigraph", "dedication"}

# source-format 枚举（§2.1）
SOURCE_FORMATS = {"epub", "pdf", "docx", "md", "txt", "mobi", "ocr", "html", "unknown"}

# id 前缀映射（§4.3；含 §5 §6 §10 §11 里定义的容器）
ID_PREFIX_MAP = {
    "part": "part-",
    "chapter": "ch-",
    "section": "sec-",
    "subsection": "sub-",
    "figure": "fig-",
    "table": "tbl-",
    "note": "note-",
}

# 通用禁令词表
BANNED_TAGS = {"script", "style", "iframe", "object", "embed", "b", "i"}
BANNED_LINK_REL = "stylesheet"
BANNED_NS_PREFIXES = ("epub:", "opf:", "ncx:", "dc:", "dcterms:", "calibre:")
JUNK_CLASS_PATTERNS = (
    re.compile(r"^calibre\d*$"),
    re.compile(r"^mso-"),
    re.compile(r"^sgc-"),
    re.compile(r"^Apple-"),
    re.compile(r"^page_?break"),
)
TABLE_FORBIDDEN_ATTRS = {"width", "height", "bgcolor", "align", "valign", "style", "border", "cellpadding", "cellspacing"}

# 书籍包内所有二进制媒体统一使用 assets/... 逻辑路径。
MEDIA_RESOURCE_ATTRS = (
    ("img", "src"),
    ("audio", "src"),
    ("video", "src"),
    ("video", "poster"),
    ("source", "src"),
    ("track", "src"),
)

HN_RE = re.compile(r"^h[1-6]$")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def css_path(node: Tag) -> str:
    """给出一个节点的类 CSS 定位路径，方便定位报错。"""
    parts = []
    cur: Optional[Tag] = node
    while isinstance(cur, Tag) and cur.name != "[document]":
        seg = cur.name
        if cur.get("id"):
            seg += f"#{cur['id']}"
        elif cur.get("data-role"):
            seg += f"[data-role={cur['data-role']}]"
        elif cur.get("data-type"):
            seg += f"[data-type={cur['data-type']}]"
        parts.append(seg)
        cur = cur.parent
        if len(parts) > 6:
            parts.append("…")
            break
    return " > ".join(reversed(parts))


def direct_child_tags(node: Tag) -> list[Tag]:
    return [c for c in node.children if isinstance(c, Tag)]


def first_child_tag(node: Tag) -> Optional[Tag]:
    for c in node.children:
        if isinstance(c, Tag):
            return c
    return None


def first_meaningful_child(node: Tag) -> Optional[Tag]:
    """首个"有意义"的子标签，跳过 §12.2 的 id 跳板 (`<span id=…></span>` 等)。"""
    for c in node.children:
        if not isinstance(c, Tag):
            continue
        if is_id_jump_span(c):
            continue
        return c
    return None


def hn_in_own_content(sec: Tag) -> list[Tag]:
    """在 sec 内、且不进入任何嵌套 <section>/<nav>/<article> 的 hN 元素。

    §4.2 的"容器内不得再出现同层或更深层 hN"指的是同一 DOM 层内的兄弟 hN；
    嵌套子 <section> 里的 hN 是它们自己的合法标题，不应计入。"""
    result: list[Tag] = []
    def walk(node: Tag) -> None:
        for child in node.children:
            if not isinstance(child, Tag):
                continue
            if child.name in ("section", "nav", "article"):
                continue
            if HN_RE.match(child.name):
                result.append(child)
            walk(child)
    walk(sec)
    return result


def is_visually_empty_p(p: Tag) -> bool:
    """§4.4：渲染后无可见文本内容的 <p>。允许 img/svg/audio/video/picture，允许 id 跳板 span。"""
    if p.get_text(strip=True):
        return False
    if p.find(["img", "svg", "picture", "audio", "video"]):
        return False
    for e in p.find_all(id=True):
        if e.name in ("span", "a") and not e.get_text(strip=True):
            return False  # id 跳板，允许保留
    return True


def is_id_jump_span(el: Tag) -> bool:
    return el.name in ("span", "a") and el.get("id") and not el.get_text(strip=True) and not list(el.children)


def asset_reference_error(value: str) -> Optional[str]:
    """返回不合规原因；None 表示是安全、规范的 assets/... 包内路径。"""
    ref = value.strip()
    if not ref:
        return "资源路径不能为空"

    decoded = unquote(ref)
    lowered = decoded.lower()
    if any(ord(char) < 32 or ord(char) == 127 for char in decoded):
        return "资源路径不得包含控制字符"
    if lowered.startswith("data:"):
        return "禁止 data URI；媒体必须保存为 assets/ 下的文件"
    if lowered.startswith(("http://", "https://", "//")):
        return "禁止外部或协议相对 URL；媒体必须使用 assets/... 包内路径"
    if re.match(r"^[a-z][a-z0-9+.-]*:", decoded, re.I):
        return "禁止带 URI scheme 的资源地址；媒体必须使用 assets/... 包内路径"
    if decoded.startswith(("/", "\\")) or re.match(r"^[a-z]:[\\/]", decoded, re.I):
        return "禁止宿主机绝对路径；媒体必须使用 assets/... 包内路径"
    if "\\" in decoded:
        return "资源路径必须使用正斜杠 /"
    if "?" in decoded or "#" in decoded:
        return "资源路径不得包含查询参数或片段"

    parts = decoded.split("/")
    if not decoded.startswith("assets/"):
        return "媒体资源路径必须以 assets/ 开头"
    if any(part in ("", ".", "..") for part in parts):
        return "资源路径不得包含空段、. 或 .."
    if len(parts) < 2:
        return "资源路径必须指向 assets/ 下的具体文件"
    return None


# ---------------------------------------------------------------------------
# Linter
# ---------------------------------------------------------------------------

class NbBookLinter:
    def __init__(self, html_content: str):
        self.soup = BeautifulSoup(html_content, "html.parser")
        self.errors: list[str] = []
        self.warnings: list[str] = []
        # 一次性预计算 id 集合，供锚点查询用（O(1) 查找而不是每次 find）
        self.all_ids: set[str] = {
            e.get("id") for e in self.soup.find_all(id=True) if e.get("id")
        }
        self.main_book: Optional[Tag] = self.soup.find("main", id="book")

    # ---- 报告 ----------------------------------------------------------------

    def log_error(self, message: str, node: Optional[Tag] = None) -> None:
        loc = f"  @ {css_path(node)}" if node else ""
        self.errors.append(f"[错误] {message}{loc}")

    def log_warning(self, message: str, node: Optional[Tag] = None) -> None:
        loc = f"  @ {css_path(node)}" if node else ""
        self.warnings.append(f"[警告] {message}{loc}")

    # ---- 入口 ---------------------------------------------------------------

    def run_all_checks(self) -> dict:
        self.check_skeleton_and_meta()
        self.check_top_level_regions()
        self.check_id_uniqueness()
        self.check_id_prefix_convention()
        self.check_sections_and_headings()
        self.check_section_parent_boundaries()
        self.check_container_hierarchy_monotonic()
        self.check_leaf_type_no_children()
        self.check_sibling_type_consistency()
        self.check_paragraphs()
        self.check_br_abuse()
        self.check_hr_stripped()
        self.check_images()
        self.check_media_resource_paths()
        self.check_figures()
        self.check_tables()
        self.check_lists_and_toc()
        self.check_links()
        self.check_footnotes()
        self.check_entity_residue()
        self.check_unknown_blocks()
        self.check_forbidden_elements()
        self.check_junk_classes()
        self.check_empty_wrappers()
        self.check_semantic_on_class()
        return {
            "is_valid": len(self.errors) == 0,
            "errors": self.errors,
            "warnings": self.warnings,
        }

    # ---- §2 骨架 & meta -----------------------------------------------------

    def check_skeleton_and_meta(self) -> None:
        html_tag = self.soup.find("html")
        if not html_tag or not html_tag.has_attr("lang"):
            self.log_error("<html> 标签缺失或未声明 lang 属性")

        head = self.soup.find("head")
        if not head:
            self.log_error("缺失 <head>")
            return

        # charset
        if not head.find("meta", attrs={"charset": True}):
            self.log_error("缺失强制 <meta charset>")

        # title
        title = head.find("title")
        if not title or not title.get_text(strip=True):
            self.log_error("缺失或为空的 <title>")

        # normalized-spec
        meta_spec = head.find("meta", attrs={"name": "normalized-spec"})
        if not meta_spec or meta_spec.get("content") != "nb-1.0":
            self.log_error("缺失强制 meta 属性 normalized-spec=\"nb-1.0\"")

        # source-format
        meta_fmt = head.find("meta", attrs={"name": "source-format"})
        if not meta_fmt or not meta_fmt.get("content"):
            self.log_error("缺失强制 meta 属性 source-format")
        elif meta_fmt.get("content") not in SOURCE_FORMATS:
            self.log_error(
                f"source-format 值 '{meta_fmt.get('content')}' 不在枚举 {sorted(SOURCE_FORMATS)} 内"
            )

        # <main id="book" data-type="book">
        if not self.main_book:
            self.log_error("缺失顶层容器 <main id=\"book\">")
        elif self.main_book.get("data-type") != "book":
            self.log_error("<main id=\"book\"> 的 data-type 必须为 'book'", self.main_book)

    # ---- §3 顶层区域 --------------------------------------------------------

    def check_top_level_regions(self) -> None:
        if not self.main_book:
            return

        seen_roles: list[str] = []
        for child in direct_child_tags(self.main_book):
            if child.name == "nav":
                role = child.get("data-role")
                if role != "toc":
                    self.log_error("main 下的 <nav> data-role 必须为 'toc'", child)
                else:
                    seen_roles.append("toc")
                if not child.get("id"):
                    self.log_error("顶层 nav[data-role=\"toc\"] 缺失必需的稳定 id", child)
            elif child.name == "section":
                role = child.get("data-role")
                if role not in TOP_ROLES:
                    self.log_error(
                        f"main 的直接子 section 包含非法 data-role: {role!r}", child
                    )
                else:
                    seen_roles.append(role)
                if not child.get("id"):
                    self.log_error(
                        f"顶层 section[data-role={role!r}] 缺失必需的稳定 id",
                        child,
                    )
            else:
                self.log_error(
                    f"main 的直接子节点只允许为 <section> 或 <nav>，发现 <{child.name}>",
                    child,
                )

        # 必须有 bodymatter
        if "bodymatter" not in seen_roles:
            self.log_error("缺失唯一必需的顶层区域 <section data-role=\"bodymatter\">")

        # 每种至多一个
        role_counts = Counter(seen_roles)
        for role, cnt in role_counts.items():
            if cnt > 1:
                self.log_error(f"顶层区域 data-role={role!r} 出现 {cnt} 次，应至多一个")

        # 顺序：按 TOP_ROLES_ORDERED 严格递增
        order_map = {r: i for i, r in enumerate(TOP_ROLES_ORDERED)}
        last = -1
        for role in seen_roles:
            idx = order_map[role]
            if idx < last:
                self.log_error(
                    f"顶层区域顺序违规：{role!r} 出现在应更靠前的位置之后"
                )
                break
            last = idx

    # ---- §4.3 id 唯一 & 前缀 -----------------------------------------------

    def check_id_uniqueness(self) -> None:
        all_ids = [e.get("id") for e in self.soup.find_all(id=True) if e.get("id")]
        cnt = Counter(all_ids)
        for id_val, n in cnt.items():
            if n > 1:
                dupes = [e for e in self.soup.find_all(id=id_val)]
                self.log_error(
                    f"id={id_val!r} 全局不唯一，共出现 {n} 次"
                    + f"（首例：{css_path(dupes[0])}；末例：{css_path(dupes[-1])}）"
                )

    def check_id_prefix_convention(self) -> None:
        """规范推荐前缀，但明确允许保留源 id。因此仅在 id 看起来像 ingester
        机械生成（形如 `<prefix>-<数字>`）时才检查前缀一致性——避免误报保留 id。
        """
        mechanical_id = re.compile(r"^[a-z]+-\d+$")
        for tag in self.soup.find_all(id=True):
            tag_id = tag.get("id") or ""
            if not mechanical_id.match(tag_id):
                continue  # 看起来像源保留 id，跳过
            d_type = tag.get("data-type") or tag.get("data-role")
            if d_type in ID_PREFIX_MAP:
                expected = ID_PREFIX_MAP[d_type]
                if not tag_id.startswith(expected):
                    self.log_warning(
                        f"机械命名的 id {tag_id!r} 与其 data-type/role={d_type!r} "
                        f"的推荐前缀 {expected!r} 不匹配",
                        tag,
                    )

    # ---- §4.1 & §4.2 章节 & 标题 ------------------------------------------

    def _region_of(self, sec: Tag) -> Optional[str]:
        """返回该 section 归属的顶层区域 data-role，如 'bodymatter'。"""
        cur = sec.parent
        while isinstance(cur, Tag):
            if cur.name == "section" and cur.get("data-role") in TOP_ROLES:
                return cur.get("data-role")
            cur = cur.parent
        return None

    def _section_depth(self, sec: Tag) -> int:
        """从顶层区域算起的 DOM section 嵌套深度（顶层区域直接子=1）。"""
        depth = 0
        cur: Optional[Tag] = sec
        while isinstance(cur, Tag):
            if cur.name == "section" and cur.get("data-role") in TOP_ROLES:
                break
            if cur.name == "section":
                depth += 1
            cur = cur.parent
        return depth

    def check_sections_and_headings(self) -> None:
        for sec in self.soup.find_all("section"):
            d_type = sec.get("data-type")
            d_role = sec.get("data-role")

            # 顶层区域跳过（自身不是章节容器）
            if d_role in TOP_ROLES:
                continue

            if not d_type:
                self.log_error("章节 <section> 缺失必需的 data-type 属性", sec)
                continue

            if not sec.get("id"):
                self.log_error("章节 <section data-type> 缺失必需的稳定 id", sec)

            if d_type not in ALL_DATA_TYPES:
                self.log_warning(
                    f"data-type={d_type!r} 不在 §4.1 词表内（若确有必要请在实现中记录）",
                    sec,
                )

            # 深度→N 映射（对所有非顶层 section 生效）
            depth = self._section_depth(sec)
            if depth <= 0:
                # 例：section 不在任何顶层区域里
                self.log_error(
                    "章节 <section> 未落在任何顶层区域（bodymatter/frontmatter/backmatter）内",
                    sec,
                )
                continue
            expected_n = min(depth, 6)
            first_hn = sec.find(HN_RE, recursive=False)

            # 容器型章节：首元素 hN & 同层唯一 hN（§4.2）
            if d_type in CONTAINER_TYPES:
                fc = first_meaningful_child(sec)  # 跳过 §12.2 的 id 跳板
                if not fc or not HN_RE.match(fc.name):
                    self.log_error(
                        f"容器型章节（data-type={d_type!r}）首个有意义子元素必须是 <hN> 标题",
                        sec,
                    )
                own_hn = hn_in_own_content(sec)  # 不含嵌套子 section 里的 hN
                if len(own_hn) > 1:
                    self.log_error(
                        f"容器型章节内同层出现 {len(own_hn)} 个 <hN>（首元素之外还有 {len(own_hn)-1} 个）；"
                        f"额外标题必须嵌套子 <section> 承载",
                        sec,
                    )

            # 叶节点型章节：hN 非强制，但 epigraph/dedication 明确豁免
            # 其他叶节点型（preface/foreword/…）若有 hN，也走深度→N 检查

            # 深度→N 校验
            if first_hn:
                actual_n = int(first_hn.name[1])
                if actual_n != expected_n:
                    self.log_error(
                        f"标题层级错误：DOM 深度为 {depth}，首标题应为 <h{expected_n}>，"
                        f"实际为 <{first_hn.name}>",
                        first_hn,
                    )

    def check_container_hierarchy_monotonic(self) -> None:
        """§4.1：容器型嵌套必须从粗到细（book ⊃ part ⊃ chapter ⊃ section ⊃ subsection）。"""
        for sec in self.soup.find_all("section"):
            d_type = sec.get("data-type")
            if d_type not in CONTAINER_TYPES:
                continue
            my_level = CONTAINER_TYPE_LEVEL[d_type]

            # 向上找最近的容器型祖先
            cur = sec.parent
            while isinstance(cur, Tag):
                if cur.name == "section" and cur.get("data-type") in CONTAINER_TYPES:
                    parent_level = CONTAINER_TYPE_LEVEL[cur.get("data-type")]
                    if parent_level >= my_level:
                        self.log_error(
                            f"容器嵌套反向：{cur.get('data-type')!r}（level {parent_level}）"
                            f"内嵌 {d_type!r}（level {my_level}），"
                            f"应从粗到细严格下降",
                            sec,
                        )
                    break
                cur = cur.parent

    def check_section_parent_boundaries(self) -> None:
        """§4.1：结构性 section 必须直接形成目录树，不得藏在排版 wrapper 中。"""
        content_roles = {"frontmatter", "bodymatter", "backmatter"}
        for sec in self.soup.find_all("section", attrs={"data-type": True}):
            parent = sec.parent
            if not isinstance(parent, Tag) or parent.name != "section":
                self.log_error(
                    "结构性 <section data-type> 必须直接位于父 section 下；"
                    "不得包在 div/span/unknown 等中间 wrapper 内",
                    sec,
                )
                continue

            if parent.get("data-type") or parent.get("data-role") in content_roles:
                continue

            self.log_error(
                "结构性 <section data-type> 的直接父级必须是另一个 "
                "section[data-type] 或 frontmatter/bodymatter/backmatter 区域",
                sec,
            )

    def check_leaf_type_no_children(self) -> None:
        """§4.1：叶节点型章节不得再嵌套子章节。"""
        for sec in self.soup.find_all("section"):
            d_type = sec.get("data-type")
            if d_type not in LEAF_TYPES:
                continue
            for descendant_sec in sec.find_all("section"):
                if descendant_sec.get("data-type") in ALL_DATA_TYPES:
                    self.log_error(
                        f"叶节点型章节（data-type={d_type!r}）不得再嵌套子章节，"
                        f"内含 data-type={descendant_sec.get('data-type')!r}",
                        descendant_sec,
                    )
                    break  # 一处报错即够

    def check_sibling_type_consistency(self) -> None:
        """§4.2.1：同一父容器下的语义平级兄弟章节容器必须同 data-type。"""
        for parent in self.soup.find_all("section"):
            # 只看有多个 section 子的父
            child_secs = [
                c for c in direct_child_tags(parent)
                if c.name == "section" and c.get("data-type") in ALL_DATA_TYPES
            ]
            if len(child_secs) < 2:
                continue
            types = {c.get("data-type") for c in child_secs}
            # frontmatter 里允许混（colophon+epigraph+preface 是不同种叶节点）；
            # 但同为容器型时必须一致
            container_children_types = {t for t in types if t in CONTAINER_TYPES}
            if len(container_children_types) > 1:
                self.log_error(
                    f"同一父容器下的容器型兄弟 section 混用了不同 data-type: "
                    f"{sorted(container_children_types)}，违反 §4.2.1",
                    parent,
                )

    # ---- §4.4 段落 & §15.5 <br> 滥用 --------------------------------------

    def check_paragraphs(self) -> None:
        for p in self.soup.find_all("p"):
            if is_visually_empty_p(p):
                self.log_error("禁止出现渲染后无可见文本内容的 <p> 段落", p)

    def check_br_abuse(self) -> None:
        """§4.4：同一 <p> 里出现连续 <br> 表明用于段间距。空 <p><br></p> 已由
        check_paragraphs 报过，此处不重复；只查连续 <br><br>。"""
        for p in self.soup.find_all("p"):
            children = direct_child_tags(p)
            for i in range(len(children) - 1):
                if children[i].name == "br" and children[i + 1].name == "br":
                    self.log_error("严禁在 <p> 内使用连续 <br><br> 模拟段落间距", p)
                    break

    # ---- §4.5 分隔 ---------------------------------------------------------

    def check_hr_stripped(self) -> None:
        for hr in self.soup.find_all("hr"):
            self.log_error(
                "严禁保留裸 <hr>；必须转成 <div data-role=\"separator\"></div>",
                hr,
            )

    # ---- §5 图片 -----------------------------------------------------------

    def check_images(self) -> None:
        for img in self.soup.find_all("img"):
            parent = img.parent
            if not isinstance(parent, Tag):
                continue

            # 判定"是否应被 <figure> 包"（纯结构判据）
            must_wrap = False
            if parent.name in ("section", "div") and parent.get("data-role") not in {"figure", "unknown"}:
                must_wrap = True
            elif parent.name == "p":
                # <p> 独占：p 里无其它实质文本节点
                text = "".join(
                    c.strip() for c in parent.strings if isinstance(c, str)
                ).strip()
                if not text and not parent.find(["a", "code", "span"], recursive=False):
                    must_wrap = True

            if must_wrap:
                # 检查外层是不是 <figure>
                container = parent if parent.name != "p" else parent.parent
                is_wrapped = (parent.name == "figure") or (
                    isinstance(container, Tag) and container.name == "figure"
                )
                if not is_wrapped:
                    if parent.name == "p":
                        self.log_error(
                            "块级独立图片不得独占裸 <p>；必须用 <figure> 包裹",
                            img,
                        )
                    else:
                        self.log_error(
                            "块级独立图片必须用 <figure data-role=\"figure\"> 包裹",
                            img,
                        )

            # alt 规则：linter 无法直接对比源，但可以警告"alt 是猜测生成"的常见
            # 模式（含"图"/"插图"/"照片"等 ingester 常用占位）。这条给 warning 而非
            # error，因为源里也可能就是这样。
            alt = img.get("alt")
            if alt is not None and alt.strip():
                if re.search(r"(插图|图片|照片|图\d+|figure|image)", alt, re.I) and len(alt) < 6:
                    self.log_warning(
                        f"alt={alt!r} 疑似 ingester 生成的占位描述；规范要求"
                        f"'源有则原样保留、源无则不加 alt 属性'（§5）",
                        img,
                    )

    def check_media_resource_paths(self) -> None:
        """§5：所有媒体都必须引用安全、稳定的 assets/... 包内路径。"""
        for tag_name, attr_name in MEDIA_RESOURCE_ATTRS:
            for node in self.soup.find_all(tag_name):
                if not node.has_attr(attr_name):
                    if tag_name in {"img", "source"} and attr_name == "src":
                        self.log_error(
                            f"<{tag_name}> 缺失必需的 {attr_name} 属性",
                            node,
                        )
                    continue
                value = str(node.get(attr_name) or "")
                reason = asset_reference_error(value)
                if reason:
                    shown = value if len(value) <= 120 else value[:117] + "…"
                    self.log_error(
                        f"<{tag_name} {attr_name}={shown!r}> 不符合资源规范：{reason}",
                        node,
                    )

    def check_figures(self) -> None:
        """§5：<figure> 必须带 data-role=\"figure\"。"""
        for fig in self.soup.find_all("figure"):
            if fig.get("data-role") != "figure":
                self.log_error(
                    "<figure> 必须带 data-role=\"figure\" 属性",
                    fig,
                )

    # ---- §6 表格 -----------------------------------------------------------

    def check_tables(self) -> None:
        for tbl in self.soup.find_all("table"):
            # data-role 必需
            if tbl.get("data-role") != "table":
                self.log_error("<table> 必须带 data-role=\"table\" 属性", tbl)

            # 至少要有 tbody
            if not tbl.find("tbody"):
                self.log_error("<table> 强制分区失败：未包含 <tbody>", tbl)

            # 有表头行时应有 <thead>
            # 判据：如果 tbody 之外/之前存在 <tr><th>，且没 <thead>，报错
            has_thead = bool(tbl.find("thead"))
            has_th_in_first_row = False
            first_tr = tbl.find("tr")
            if first_tr and first_tr.find("th"):
                has_th_in_first_row = True
            if has_th_in_first_row and not has_thead:
                self.log_warning(
                    "首行含 <th> 但未用 <thead> 分区；规范要求有表头行则加 <thead>",
                    tbl,
                )

            # 剥离原始排版属性——对 table 及**所有子节点**检查
            for elem in [tbl] + tbl.find_all(True):
                for attr in TABLE_FORBIDDEN_ATTRS:
                    if elem.has_attr(attr):
                        self.log_error(
                            f"<{elem.name}> 保留了禁止属性 {attr!r}={elem[attr]!r}",
                            elem,
                        )

    # ---- §11 TOC & §7 列表 ------------------------------------------------

    def check_lists_and_toc(self) -> None:
        toc_nav = self.soup.find("nav", attrs={"data-role": "toc"})
        if not toc_nav:
            return

        # TOC 必须用 <ol>，不得用 <ul>
        if toc_nav.find("ul"):
            self.log_error("<nav data-role=\"toc\"> 内不得使用 <ul>；必须用 <ol>", toc_nav)

        # 每个 <li> 首元素必须是 <a href="#…">
        for li in toc_nav.find_all("li"):
            fc = first_child_tag(li)
            if not fc:
                continue
            if fc.name != "a":
                self.log_error(
                    "TOC 中每个 <li> 的首个子元素必须是 <a>",
                    li,
                )
                continue
            href = fc.get("href") or ""
            if not href.startswith("#"):
                self.log_error(
                    f"TOC 中的锚点 href 必须以 '#' 开头，实际 {href!r}",
                    fc,
                )
            elif href[1:] not in self.all_ids:
                self.log_error(
                    f"TOC 锚点 {href!r} 指向不存在的 id",
                    fc,
                )

    # ---- §12 链接 ----------------------------------------------------------

    def check_links(self) -> None:
        for a in self.soup.find_all("a"):
            href = a.get("href", "")
            if not href:
                continue

            # 外链
            if re.match(r"^(https?|mailto|tel):", href):
                rel = a.get("rel") or []
                if isinstance(rel, str):
                    rel = rel.split()
                if a.get("target") != "_blank":
                    self.log_error(
                        f"外部链接 {href!r} 缺少 target=\"_blank\"", a
                    )
                if "noopener" not in rel or "noreferrer" not in rel:
                    self.log_error(
                        f"外部链接 {href!r} 的 rel 缺少 noopener 或 noreferrer "
                        f"（实际 rel={rel}）",
                        a,
                    )
            # 内链
            elif href.startswith("#"):
                target_id = href[1:]
                if target_id and target_id not in self.all_ids:
                    if a.get("data-broken") != "true":
                        self.log_error(
                            f"内锚点指向不存在的 id {target_id!r}，且未标记 "
                            f"data-broken=\"true\"",
                            a,
                        )

    # ---- §9.1 & §10 脚注 ---------------------------------------------------

    def check_footnotes(self) -> None:
        # 9.1：<sup> 不得包裹指向 note 的 <a>
        for sup in self.soup.find_all("sup"):
            a = sup.find("a", href=True)
            if a and (a.get("href") or "").startswith("#"):
                target = a["href"][1:]
                if target in self.all_ids:
                    tgt = self.soup.find(id=target)
                    if isinstance(tgt, Tag) and (
                        tgt.get("data-role") == "note"
                        or a.get("data-role") == "noteref"
                    ):
                        self.log_error(
                            "脚注引用不得用 <sup> 包裹 <a>；应直接用 "
                            "<a data-role=\"noteref\">",
                            sup,
                        )

        # 10.2：所有 data-role="note" 必须位于 <section data-role="notes"> 内
        notes_container = self.soup.find(attrs={"data-role": "notes"})
        for note in self.soup.find_all(attrs={"data-role": "note"}):
            if notes_container is None or note not in notes_container.find_all():
                self.log_error(
                    "data-role=\"note\" 元素必须位于 <section data-role=\"notes\"> 内",
                    note,
                )
            if not note.get("id"):
                self.log_error("data-role=\"note\" 缺少 id", note)
            if not note.get_text(strip=True):
                self.log_error(
                    "data-role=\"note\" 无可见注释正文（空壳）；注释正文必须汇总在 "
                    "note 容器内，不得留在正文章节里只放结构占位（§10.2）",
                    note,
                )

        # 10.3：noteref 拓扑—— href 必须指向 data-role="note"
        noterefs = self.soup.find_all(attrs={"data-role": "noteref"})
        for ref in noterefs:
            href = ref.get("href") or ""
            if not href.startswith("#"):
                self.log_error(
                    f"noteref 的 href 必须以 '#' 开头，实际 {href!r}", ref
                )
                continue
            target_id = href[1:]
            if target_id not in self.all_ids:
                self.log_error(
                    f"noteref 的 href {href!r} 指向不存在的 id",
                    ref,
                )
                continue
            tgt = self.soup.find(id=target_id)
            if not isinstance(tgt, Tag) or tgt.get("data-role") != "note":
                self.log_error(
                    f"noteref 的 href {href!r} 指向的元素不是 data-role=\"note\"",
                    ref,
                )

        # 10.3：孤儿 note（无 noteref 指向）应标 data-orphan="true"
        referenced_ids = {
            (ref.get("href") or "")[1:] for ref in noterefs
        }
        for note in self.soup.find_all(attrs={"data-role": "note"}):
            nid = note.get("id")
            if nid and nid not in referenced_ids:
                if note.get("data-orphan") != "true":
                    self.log_warning(
                        f"note id={nid!r} 无 noteref 引用（孤儿）；"
                        f"应标 data-orphan=\"true\"",
                        note,
                    )

    # ---- §15.4 实体残留（字面化的字符实体） ----------------------------------

    ENTITY_RESIDUE_RE = re.compile(
        r"&(?:nbsp|amp|lt|gt|quot|apos|hellip|mdash|ndash|ldquo|rdquo|lsquo|rsquo"
        r"|copy|reg|trade|shy|times|middot|#x?[0-9a-fA-F]{1,6});?"
    )

    def check_entity_residue(self) -> None:
        """可见文本里不得出现字面化的字符实体（如 "&nbsp"、"&amp;lt"）。

        典型成因：源里的畸形实体（无分号 legacy 写法）被解析器当普通文本读入，
        序列化时 & 被正确转义，实体就固化成了读者可见的乱码。"""
        body = self.soup.find("body")
        if not body:
            return
        for s in body.strings:
            for m in self.ENTITY_RESIDUE_RE.finditer(str(s)):
                ctx = str(s)[max(0, m.start() - 20):m.end() + 10].strip()
                self.log_error(
                    f"可见文本中残留字面字符实体 {m.group(0)!r}（上下文: …{ctx}…）；"
                    f"实体必须解码为 Unicode 字符输出",
                    s.parent if isinstance(s.parent, Tag) else None,
                )

    # ---- §14 unknown 兜底 --------------------------------------------------

    def check_unknown_blocks(self) -> None:
        for el in self.soup.find_all(attrs={"data-role": "unknown"}):
            if not el.get("data-reason"):
                self.log_error(
                    "data-role=\"unknown\" 兜底容器必须带 data-reason 属性",
                    el,
                )

    # ---- §15 通用禁令 ------------------------------------------------------

    def check_forbidden_elements(self) -> None:
        # inline style
        for tag in self.soup.find_all(style=True):
            self.log_error(
                f"违规内联 style={tag['style']!r}",
                tag,
            )

        # 黑名单标签
        for name in BANNED_TAGS:
            for tag in self.soup.find_all(name):
                self.log_error(
                    f"严禁在规范化产物中出现 <{name}>；请重命名或剔除",
                    tag,
                )

        # <link rel="stylesheet">
        for link in self.soup.find_all("link"):
            rel = link.get("rel") or []
            if isinstance(rel, str):
                rel = rel.split()
            if BANNED_LINK_REL in rel:
                self.log_error(
                    "严禁保留 <link rel=\"stylesheet\">",
                    link,
                )

        # XML 命名空间残留（精确匹配已知前缀，避免误伤 xml:lang / xlink:href）
        for tag in self.soup.find_all():
            for attr in list(tag.attrs.keys()):
                if any(attr.startswith(p) for p in BANNED_NS_PREFIXES):
                    self.log_error(
                        f"严禁保留 XML 命名空间属性 {attr!r}",
                        tag,
                    )

    def check_junk_classes(self) -> None:
        """§15.3：清洗源转换工具带的冗余 class。"""
        for tag in self.soup.find_all(class_=True):
            classes = tag.get("class") or []
            junk = [
                c for c in classes
                if any(pat.match(c) for pat in JUNK_CLASS_PATTERNS)
            ]
            if junk:
                self.log_error(
                    f"<{tag.name}> 保留了源工具冗余 class: {junk}",
                    tag,
                )

    def check_empty_wrappers(self) -> None:
        """§15.2 / §15.5：无语义 wrapper 与空元素。"""
        # 无属性 div/span 即使包含正文也只是排版壳，必须剥除但保留子内容。
        for tag in self.soup.find_all(["div", "span"]):
            if not tag.attrs:
                self.log_error(
                    f"无任何属性的 <{tag.name}> 不承载语义，必须剥壳并保留内部内容",
                    tag,
                )

        # 空 <a> / <li> / <td>（id 跳板除外）
        for tag in self.soup.find_all(["a", "li", "td"]):
            if is_id_jump_span(tag):
                continue
            if not tag.get_text(strip=True) and not tag.find(["img", "svg", "picture", "audio", "video"]):
                # <a> 若带 href 或 id 至少还是有意义的
                if tag.name == "a" and (tag.get("href") or tag.get("id")):
                    continue
                self.log_error(
                    f"空 <{tag.name}> 必须删除或补内容",
                    tag,
                )

    def check_semantic_on_class(self) -> None:
        """§1 唯一原则：语义只能由 data-role/data-type 承载，不得搭在 class 上。"""
        semantic_words = ALL_DATA_TYPES | TOP_ROLES | {"figure", "table", "note", "notes", "noteref", "backref"}
        for tag in self.soup.find_all(class_=True):
            classes = set(tag.get("class") or [])
            hits = classes & semantic_words
            if not hits:
                continue
            # 允许 class="chapter" 出现在同时也标了 data-type="chapter" 的节点上
            # （很多 ingester 保留 class 做样式），但不允许**只**靠 class 承载语义
            d_type = tag.get("data-type")
            d_role = tag.get("data-role")
            for hit in hits:
                if hit == d_type or hit == d_role:
                    continue
                self.log_warning(
                    f"class 含语义词 {hit!r}，但对应的 data-type/data-role 未设置；"
                    f"语义必须由 data-* 承载而不是 class",
                    tag,
                )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("用法：python tools/nb_linter.py <path/to/book.html>", file=sys.stderr)
        return 2

    with open(argv[1], "r", encoding="utf-8") as f:
        html = f.read()

    linter = NbBookLinter(html)
    result = linter.run_all_checks()

    for e in result["errors"]:
        print(e)
    for w in result["warnings"]:
        print(w)

    err_n = len(result["errors"])
    warn_n = len(result["warnings"])
    print()
    print(f"—— 汇总：{err_n} 个错误，{warn_n} 个警告 ——")
    if err_n:
        return 1
    if warn_n:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
