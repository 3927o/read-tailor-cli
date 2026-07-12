"""nb-1.0 产物统一校验入口（结构层 + 包资源层 + 保真层）。

用法：
    python tools/nb_check.py book.html                        # 结构层 + 包资源层
    python tools/nb_check.py book.html --baseline book.epub   # 三层完整校验

三层分工：
- 结构层（永远跑）：产物自身是否符合 docs/normalized_book_spec.md 的结构规则。
  规则实现在同目录 nb_linter.py。
- 包资源层（永远跑）：assets/... 路径安全且引用文件真实存在。
- 保真层（提供 --baseline 时跑）：产物相对源 EPUB 有没有丢内容。
  * char_recall  —— 可见字符召回率（阈值 ≥99.9%），并逐条列出丢失片段（diff）
  * img_recall   —— assets 图片按字节哈希做多重集守恒（同一张图用 3 次丢 1 次也能抓到）
  * note 守恒    —— EPUB 里的 epub:type noteref/footnote 数 ↔ 产物 noteref/note 数
  * TOC 对账     —— EPUB nav 文档条目数 ↔ 产物 nav[data-role=toc] 条目数

重要：没提供 --baseline 时，结构层全绿**不代表**内容没丢——报告会明确标注
"内容保真未验证"。给 agent 的终验必须带上源 EPUB。

退出码：0 = 通过；1 = 有 error；2 = 只有 warning。
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import html as html_mod
import os
import posixpath
import re
import sys
import warnings
import zipfile
from collections import Counter
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nb_linter import (  # noqa: E402
    MEDIA_RESOURCE_ATTRS,
    NbBookLinter,
    asset_reference_error,
)

WS_RE = re.compile(r"\s+")
CHAR_RECALL_THRESHOLD = 0.999
# 连续丢失 ≥ 该长度的片段按 error 报（一句话级别的丢失）；更短的按 warning。
DELETE_ERROR_LEN = 5
# 规范性变换白名单：EPUB → nb-1.0 过程中"应当"消失的文本模式。
# 注释体开头的 "[N]" 编号标记（§10：编号由 noteref 承载，note 体不保留）。
NOTE_NUM_RE = re.compile(r"^(\[\d+\])+$")


def norm_text(s: str) -> str:
    # 双侧统一：先把字面实体（含无分号 legacy 形式，如 "&nbsp"）解码成
    # Unicode，再删全部空白（\s 含 \xa0）。这样畸形实体不会污染字符对账；
    # 产物里若真残留字面实体，由结构层的 check_entity_residue 负责报错。
    return WS_RE.sub("", html_mod.unescape(html_mod.unescape(s)))


def _local_name(tag: Tag) -> str:
    return tag.name.split(":")[-1]


# ---------------------------------------------------------------------------
# EPUB 基准抽取（只读，不做任何 DOM 改写）
# ---------------------------------------------------------------------------

class EpubBaseline:
    def __init__(self, path: str):
        self.zf = zipfile.ZipFile(path)
        self.opf_path = self._find_opf()
        self.opf_dir = posixpath.dirname(self.opf_path)
        opf = BeautifulSoup(self._read(self.opf_path), "html.parser")

        # manifest: id -> (href, media-type, properties)
        self.manifest: dict[str, tuple[str, str, str]] = {}
        for item in opf.find_all(lambda t: _local_name(t) == "item"):
            iid = item.get("id")
            if iid:
                self.manifest[iid] = (
                    item.get("href") or "",
                    item.get("media-type") or "",
                    item.get("properties") or "",
                )

        # spine（linear 项）
        self.spine_hrefs: list[str] = []
        for iref in opf.find_all(lambda t: _local_name(t) == "itemref"):
            if (iref.get("linear") or "yes").lower() == "no":
                continue
            entry = self.manifest.get(iref.get("idref") or "")
            if entry:
                self.spine_hrefs.append(entry[0])

        # nav 文档（EPUB3）
        self.nav_href: Optional[str] = None
        for href, mtype, props in self.manifest.values():
            if "nav" in (props or "").split():
                self.nav_href = href
                break

        # 解析好的 spine 文档缓存
        self._docs: list[tuple[str, BeautifulSoup]] = []
        for href in self.spine_hrefs:
            raw = self._read(self._resolve(href))
            if raw is None:
                continue
            self._docs.append((href, BeautifulSoup(raw, "html.parser")))

    def _find_opf(self) -> str:
        container = BeautifulSoup(self._read("META-INF/container.xml"), "html.parser")
        rootfile = container.find(lambda t: _local_name(t) == "rootfile")
        return rootfile.get("full-path")

    def _resolve(self, href: str) -> str:
        href = href.split("#")[0]
        return posixpath.normpath(posixpath.join(self.opf_dir, href)) if self.opf_dir else href

    def _read(self, name: str) -> Optional[bytes]:
        try:
            return self.zf.read(name)
        except KeyError:
            return None

    # ---- 可见文本 -----------------------------------------------------------

    def visible_text(self) -> str:
        parts = []
        for href, doc in self._docs:
            body = doc.find("body") or doc
            parts.append(body.get_text())
        return norm_text("".join(parts))

    # ---- 图片引用（多重集） ---------------------------------------------------

    def image_refs(self) -> tuple[Counter, dict[str, list[str]]]:
        """返回 (哈希→引用次数, 哈希→引用它的 spine 文档列表)。"""
        counts: Counter = Counter()
        locations: dict[str, list[str]] = {}
        for href, doc in self._docs:
            doc_dir = posixpath.dirname(href)
            for img in doc.find_all("img"):
                src = img.get("src") or ""
                if not src or src.startswith("data:"):
                    continue
                target = posixpath.normpath(posixpath.join(doc_dir, src.split("#")[0]))
                data = self._read(self._resolve(target)) or self._read(target)
                if data is None:
                    continue
                h = hashlib.md5(data).hexdigest()
                counts[h] += 1
                locations.setdefault(h, []).append(href)
        return counts, locations

    # ---- note 标记计数 --------------------------------------------------------

    def note_counts(self) -> tuple[int, int]:
        n_refs = n_notes = 0
        for _, doc in self._docs:
            for el in doc.find_all(True):
                et = el.get("epub:type") or ""
                if "noteref" in et:
                    n_refs += 1
                if any(k in et for k in ("footnote", "rearnote", "endnote")):
                    n_notes += 1
        return n_refs, n_notes

    # ---- TOC 条目 --------------------------------------------------------------

    def toc_entries(self) -> Optional[list[str]]:
        # EPUB3：manifest 里 properties="nav" 的目录文档
        if self.nav_href:
            raw = self._read(self._resolve(self.nav_href))
            if raw is not None:
                nav_doc = BeautifulSoup(raw, "html.parser")
                nav = None
                for n in nav_doc.find_all("nav"):
                    if "toc" in (n.get("epub:type") or ""):
                        nav = n
                        break
                nav = nav or nav_doc.find("nav")
                if nav:
                    return [a.get_text(strip=True) for a in nav.find_all("a")]
        # EPUB2 fallback：NCX（media-type application/x-dtbncx+xml）
        for href, mtype, _props in self.manifest.values():
            if "dtbncx" in mtype:
                raw = self._read(self._resolve(href))
                if raw is None:
                    break
                ncx = BeautifulSoup(raw, "html.parser")
                labels = [
                    t.get_text(strip=True)
                    for np in ncx.find_all(lambda t: _local_name(t) == "navpoint")
                    for t in np.find_all(lambda t: _local_name(t) == "text")[:1]
                ]
                return labels or None
        return None


# ---------------------------------------------------------------------------
# 包资源层
# ---------------------------------------------------------------------------

def resolve_asset_path(package_root: Path, reference: str) -> Optional[Path]:
    """把已通过语法校验的 assets/... 引用解析为包内文件路径。"""
    if asset_reference_error(reference):
        return None
    root = package_root.resolve()
    target = (root / unquote(reference.strip())).resolve()
    try:
        common = os.path.commonpath((str(root), str(target)))
    except ValueError:
        return None
    return target if common == str(root) else None


class AssetChecker:
    def __init__(self, product_soup: BeautifulSoup, product_path: str):
        self.product = product_soup
        self.product_path = Path(product_path).resolve()
        self.package_root = self.product_path.parent
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.metrics: dict[str, object] = {}

    def run(self) -> None:
        checked = 0
        unique_files: set[Path] = set()
        for tag_name, attr_name in MEDIA_RESOURCE_ATTRS:
            for node in self.product.find_all(tag_name):
                if not node.has_attr(attr_name):
                    continue
                reference = str(node.get(attr_name) or "")
                if asset_reference_error(reference):
                    # 具体路径语法错误由结构层报告；这里不重复。
                    continue
                checked += 1
                target = resolve_asset_path(self.package_root, reference)
                if target is None:
                    self.errors.append(
                        f"[资源越界] {tag_name}.{attr_name}={reference!r} 无法安全解析到书籍包内"
                    )
                    continue
                unique_files.add(target)
                if not target.is_file():
                    self.errors.append(
                        f"[资源缺失] {tag_name}.{attr_name}={reference!r} 对应文件不存在"
                    )

        self.metrics["asset_references"] = checked
        self.metrics["asset_files"] = len(unique_files)


# ---------------------------------------------------------------------------
# 保真层
# ---------------------------------------------------------------------------

class FidelityChecker:
    def __init__(
        self,
        product_soup: BeautifulSoup,
        baseline: EpubBaseline,
        package_root: Path,
    ):
        self.product = product_soup
        self.baseline = baseline
        self.package_root = package_root.resolve()
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.metrics: dict[str, object] = {}

    def run(self) -> None:
        self.check_char_recall()
        self.check_image_recall()
        self.check_note_conservation()
        self.check_toc_reconciliation()

    # ---- char_recall + diff ---------------------------------------------------

    def _is_normative_loss(self, lost: str) -> Optional[str]:
        """规范要求的变换导致的"合法丢失"。返回归类名，非白名单返回 None。"""
        if NOTE_NUM_RE.match(lost):
            return "note 编号标记（§10 结构化后由 noteref 承载）"
        # TOC 移位：EPUB 的 HTML 目录页文本在产物里变成了 nav[data-role=toc]。
        # 丢失片段若（去掉"目录"字样后）出现在产物 TOC 文本里，判为移位而非丢失。
        if self._prod_toc_text:
            candidate = lost.replace("目录", "", 1)
            if candidate and (
                candidate in self._prod_toc_text or self._prod_toc_text in candidate
            ):
                return "TOC 移位（源目录页 → 产物 nav[data-role=toc]）"
        return None

    def check_char_recall(self) -> None:
        epub_text = self.baseline.visible_text()
        body = self.product.find("body")
        prod_text = norm_text(body.get_text()) if body else ""

        toc_nav = self.product.find("nav", attrs={"data-role": "toc"})
        self._prod_toc_text = norm_text(toc_nav.get_text()) if toc_nav else ""

        sm = difflib.SequenceMatcher(None, epub_text, prod_text, autojunk=False)
        matched = sum(b.size for b in sm.get_matching_blocks())

        # 逐条审片段：白名单内的丢失计回 effective recall，不算违规
        normative_losses: Counter = Counter()
        whitelisted_chars = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("delete", "replace") and i2 > i1:
                lost = epub_text[i1:i2]
                kind = self._is_normative_loss(lost)
                if kind:
                    normative_losses[kind] += 1
                    whitelisted_chars += len(lost)
                    continue
                ctx = epub_text[max(0, i1 - 20):i1]
                msg = f"丢失片段（{len(lost)} 字符）: {lost[:80]!r}  上文: …{ctx}"
                if len(lost) >= DELETE_ERROR_LEN:
                    self.errors.append("[内容丢失] " + msg)
                else:
                    self.warnings.append("[内容丢失] " + msg)
            if tag in ("insert", "replace") and j2 > j1:
                added = prod_text[j1:j2]
                if len(added) >= DELETE_ERROR_LEN:
                    # 产物 TOC 本身相对源目录页是移位不是新增
                    if self._prod_toc_text and (
                        added in self._prod_toc_text or self._prod_toc_text in added
                    ):
                        continue
                    ctx = prod_text[max(0, j1 - 20):j1]
                    self.warnings.append(
                        f"[多出内容] 产物比源多出（{len(added)} 字符）: {added[:80]!r}  上文: …{ctx}"
                    )

        recall = (matched + whitelisted_chars) / len(epub_text) if epub_text else 1.0
        self.metrics["char_recall"] = recall
        for kind, n in normative_losses.items():
            self.metrics.setdefault("normative_transforms", []).append(f"{kind} × {n}")

        if recall < CHAR_RECALL_THRESHOLD:
            self.errors.append(
                f"char_recall = {recall*100:.4f}% 低于阈值 {CHAR_RECALL_THRESHOLD*100}%"
                f"（已扣除规范性变换 {whitelisted_chars} 字符）"
            )

    # ---- img_recall（多重集守恒） ------------------------------------------------

    def check_image_recall(self) -> None:
        epub_counts, epub_locs = self.baseline.image_refs()

        prod_counts: Counter = Counter()
        for img in self.product.find_all("img"):
            src = img.get("src") or ""
            target = resolve_asset_path(self.package_root, str(src))
            if target is None or not target.is_file():
                continue  # 路径不合法或文件缺失由结构层/包资源层报告。
            try:
                data = target.read_bytes()
            except OSError as exc:
                self.errors.append(f"[图片读取失败] {src!r}: {exc}")
                continue
            prod_counts[hashlib.md5(data).hexdigest()] += 1

        total_epub = sum(epub_counts.values())
        total_prod = sum(prod_counts.values())
        self.metrics["img_refs_epub"] = total_epub
        self.metrics["img_refs_product"] = total_prod

        for h, n_epub in epub_counts.items():
            n_prod = prod_counts.get(h, 0)
            if n_prod < n_epub:
                locs = ", ".join(epub_locs.get(h, [])[:5])
                self.errors.append(
                    f"[图片丢失] 同一图片（md5={h[:10]}…）源中引用 {n_epub} 次、"
                    f"产物仅 {n_prod} 次；源引用位置: {locs}"
                )

        for h, n_prod in prod_counts.items():
            n_epub = epub_counts.get(h, 0)
            if n_prod > n_epub:
                self.warnings.append(
                    f"[图片新增或重复] 同一图片（md5={h[:10]}…）源中引用 {n_epub} 次、"
                    f"产物引用 {n_prod} 次"
                )

    # ---- note 守恒 ---------------------------------------------------------------

    def check_note_conservation(self) -> None:
        n_refs, n_notes = self.baseline.note_counts()
        if n_refs == 0 and n_notes == 0:
            return  # 源没有语义标记，无法对账
        p_refs = len(self.product.select('[data-role="noteref"]'))
        p_notes = len(self.product.select('[data-role="note"]'))
        self.metrics["noterefs_epub_vs_product"] = (n_refs, p_refs)
        self.metrics["notes_epub_vs_product"] = (n_notes, p_notes)
        if n_refs and p_refs != n_refs:
            self.warnings.append(
                f"[脚注] 源 epub:type=noteref 共 {n_refs} 个，产物 noteref {p_refs} 个"
            )
        if n_notes and p_notes != n_notes:
            self.warnings.append(
                f"[脚注] 源 footnote/rearnote 共 {n_notes} 个，产物 note {p_notes} 个"
            )

    # ---- TOC 对账 ----------------------------------------------------------------

    def check_toc_reconciliation(self) -> None:
        epub_toc = self.baseline.toc_entries()
        prod_nav = self.product.find("nav", attrs={"data-role": "toc"})
        if epub_toc is None:
            if prod_nav is not None:
                self.warnings.append(
                    "[TOC] 源 EPUB 未发现 nav 文档，但产物有 TOC——确认不是凭空生成（§11）"
                )
            return
        if prod_nav is None:
            self.errors.append(
                f"[TOC] 源 EPUB 有 nav 目录（{len(epub_toc)} 条），产物缺 <nav data-role=\"toc\">"
            )
            return
        prod_entries = [a.get_text(strip=True) for a in prod_nav.find_all("a")]
        self.metrics["toc_entries_epub_vs_product"] = (len(epub_toc), len(prod_entries))
        if len(prod_entries) != len(epub_toc):
            self.warnings.append(
                f"[TOC] 条目数不一致：源 {len(epub_toc)} 条 vs 产物 {len(prod_entries)} 条"
            )
        missing = [t for t in epub_toc if t and t not in prod_entries]
        if missing:
            self.warnings.append(
                f"[TOC] 源目录条目在产物 TOC 中缺失: {missing[:5]}"
            )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="nb-1.0 产物校验（结构层 + 保真层）")
    ap.add_argument("product", help="规范化产物 HTML 路径")
    ap.add_argument(
        "--baseline",
        help="源 EPUB 路径（提供后加跑保真层；也接受 raw.html 用于调试）",
    )
    args = ap.parse_args()

    with open(args.product, encoding="utf-8") as f:
        product_html = f.read()
    product_soup = BeautifulSoup(product_html, "html.parser")

    # ---- 结构层 ----
    linter = NbBookLinter(product_html)
    struct = linter.run_all_checks()

    print("════════ 结构层（nb-1.0 规范自检） ════════")
    for e in struct["errors"]:
        print(e)
    for w in struct["warnings"]:
        print(w)
    print(f"结构层小计：{len(struct['errors'])} error / {len(struct['warnings'])} warning")

    # ---- 包资源层 ----
    assets = AssetChecker(product_soup, args.product)
    assets.run()
    print()
    print("════════ 包资源层（assets 文件检查） ════════")
    for e in assets.errors:
        print("[错误] " + e)
    for w in assets.warnings:
        print("[警告] " + w)
    print(
        f"资源引用 {assets.metrics.get('asset_references', 0)} 次 / "
        f"实际文件 {assets.metrics.get('asset_files', 0)} 个"
    )
    print(f"包资源层小计：{len(assets.errors)} error / {len(assets.warnings)} warning")

    fid_errors: list[str] = []
    fid_warnings: list[str] = []

    # ---- 保真层 ----
    if args.baseline:
        print()
        print("════════ 保真层（对源 EPUB 守恒对账） ════════")
        if args.baseline.lower().endswith(".epub"):
            baseline = EpubBaseline(args.baseline)
            fc = FidelityChecker(product_soup, baseline, assets.package_root)
            fc.run()
            fid_errors, fid_warnings = fc.errors, fc.warnings
            for e in fid_errors:
                print("[错误] " + e)
            for w in fid_warnings:
                print("[警告] " + w)
            cr = fc.metrics.get("char_recall")
            if isinstance(cr, float):
                print(f"char_recall = {cr*100:.4f}%")
            print(
                f"图片引用：源 {fc.metrics.get('img_refs_epub')} 次 / "
                f"产物 {fc.metrics.get('img_refs_product')} 次"
            )
            print(f"保真层小计：{len(fid_errors)} error / {len(fid_warnings)} warning")
        else:
            print("（暂只支持 .epub 基准；raw.html 基准请用旧 evaluator）")
    else:
        print()
        print("⚠ 未提供 --baseline：内容保真【未验证】。结构层全绿不代表没丢内容；")
        print("  终验请带上源 EPUB：nb_check.py <product.html> --baseline <book.epub>")

    # ---- 汇总 ----
    n_err = len(struct["errors"]) + len(assets.errors) + len(fid_errors)
    n_warn = len(struct["warnings"]) + len(assets.warnings) + len(fid_warnings)
    print()
    print(f"—— 总计：{n_err} 个错误，{n_warn} 个警告 ——")
    if n_err:
        return 1
    if n_warn:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
