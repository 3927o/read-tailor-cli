#!/usr/bin/env python3
"""单脚本：把《查拉图斯特拉如是说·第三部》处理成手机可读的「领读版」。

这是 zx_reader.py 的姊妹件，但书不同、读法也不同：
  - 《哲学研究》是 §编号的思辨论证，靠"对话切分+设问/确认"领读。
  - 《查拉图斯特拉》第三部是先知体散文诗，靠**章节定位 + 意象解码 + 大白话**领读。
所以这里换了一套结构与标注，但仍是同一条 Route 1：

正式逻辑（程序做机械活+保无损，AI 只产标注数据、绝不碰正文）：
  1. 程序：epub --Step1(pandoc)--> raw --Step2(recover+plan_7)--> normalized HTML
  2. 程序：切出「第三部」(两个 <h1> 之间)，按 <h2> 分章；章内保留 <h3> 小节号；
           段落**逐字**保留，行内 <sup> 译注(zy-footnote)抽成可点的"译"标记——
           译注是原书自带的学术注，是免费的、可靠的解读层。
  3. AI  ：每章产一个**标注包 JSON**：导引(读前定位,不剧透) / 脉络标签 /
           意象解码(锚点必须是正文逐字片段) / 大白话(只给真正难的章)。
           AI 只产数据，不重写正文、不输出 HTML。
  4. 程序：渲染 = 逐字原文 + 行内译注 + AI 标注层；渲染后**逐章校验 正文==原文**
           (字符级，忽略空白)；不过校验直接拒绝输出。AI 给的意象锚点若不是正文
           逐字子串，程序丢弃(防瞎编)。
  5. 程序：写出单文件 HTML（手机优先、深色模式、▽ 折叠）。

AI 不可用时退回内置 fallback（手工打磨的 16 章导引 + 地标章大白话）。

用法：
  AI_BASE_URL=... AI_API_KEY=... AI_MODEL=... python3 zara_reader.py 查拉图斯特拉如是说（译文经典）.epub
  python3 zara_reader.py --normalized norm.html --no-ai -o out.html
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bs4 import BeautifulSoup, NavigableString, Tag  # noqa: E402


# ----------------------------------------------------------------------------
# 1. 程序：epub -> normalized
# ----------------------------------------------------------------------------

def epub_to_normalized(epub_path: str) -> str:
    from common.epub_recover import recover            # noqa: E402
    from plan_7_program_first import build_normalized    # noqa: E402
    raw = subprocess.run(
        ["pandoc", epub_path, "-f", "epub", "-t", "html", "-s", "-o", "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    augmented, _ = recover(epub_path, raw)
    t = BeautifulSoup(augmented, "html.parser").find("title")
    title = t.get_text(strip=True) if t else "Untitled"
    return build_normalized(augmented, {}, {"title": title, "language": "zh"})


# ----------------------------------------------------------------------------
# 2. 程序：切出第三部，逐字抽章（段落 + 行内译注 + 小节号）
# ----------------------------------------------------------------------------

def _clean_title(node: Tag) -> str:
    """标题里每个汉字是独立 <strong>，get_text 会带空格；去掉空白与脚注。"""
    n = BeautifulSoup(str(node), "html.parser").find(True)
    for sup in n.find_all("sup"):
        sup.decompose()
    return re.sub(r"\s+", "", n.get_text())


def _para_segments(p: Tag):
    """走 <p>，返回 [("text",s) | ("note",译注文本)]；text 段拼回即逐字正文。"""
    segs = []

    def walk(node):
        for c in node.children:
            if isinstance(c, NavigableString):
                segs.append(("text", str(c)))
            elif isinstance(c, Tag):
                if c.name == "sup":
                    img = c.find("img", attrs={"zy-footnote": True})
                    a = c.find("a")
                    note = None
                    if img and img.get("zy-footnote"):
                        note = img["zy-footnote"]
                    elif a and a.get("title"):
                        note = a["title"]
                    if note is not None:
                        segs.append(("note", note))
                    else:
                        walk(c)
                else:
                    walk(c)

    walk(p)
    return segs


def extract_part3(norm_html: str):
    """返回 (title, epigraph_paras, [discourse...])；discourse=
       {"title","note","blocks":[("sub",label)|("p",segs)...]}。"""
    soup = BeautifulSoup(norm_html, "html.parser")
    doc_title = soup.find("title")
    doc_title = doc_title.get_text(strip=True) if doc_title else "查拉图斯特拉如是说"
    body = soup.select_one("#bodymatter") or soup
    h1s = body.find_all("h1")
    start = end = None
    for i, h in enumerate(h1s):
        if "第三部" in h.get_text():
            start = h
            end = h1s[i + 1] if i + 1 < len(h1s) else None
            break
    if start is None:
        raise SystemExit("未找到「第三部」")

    epigraph, discourses, cur = [], [], None
    for el in start.find_all_next():
        if el is end:
            break
        if not isinstance(el, Tag):
            continue
        if el.name == "h2":
            note = None
            sup = el.find("sup")
            if sup:
                img = sup.find("img", attrs={"zy-footnote": True})
                if img:
                    note = img.get("zy-footnote")
            cur = {"title": _clean_title(el), "note": note, "blocks": []}
            discourses.append(cur)
        elif el.name == "h3":
            if cur is not None:
                cur["blocks"].append(("sub", _clean_title(el)))
        elif el.name == "p":
            segs = _para_segments(el)
            if not any(k == "text" and s.strip() for k, s in segs):
                continue
            if cur is None:
                epigraph.append(segs)
            else:
                cur["blocks"].append(("p", segs))
    return doc_title, epigraph, discourses


# ----------------------------------------------------------------------------
# 3. AI：第三部各章 -> 标注包 JSON
# ----------------------------------------------------------------------------

SYSTEM = """你是一位顶尖的领读人，帮**第一次读、且觉得很难**的读者在手机上读尼采《查拉图斯特拉如是说·第三部》。
第三部是先知体散文诗，不是论证；它是一段旅程（查拉图斯特拉返回山洞的归途与内心攀登），地标是"永恒轮回"。
给你每一章的**逐字原文**（段落已编号；原书自带的"译注"已经解释了大量典故，你不必重复）。
你只产出**标注数据**（只产数据，绝不重写正文、不输出 HTML、不改一个字）。

输出**一个 JSON 对象**，键为章序号字符串（"1".."N"，按我给的顺序），每章的值字段：
- "opener": 读这章前的一句导引。给三件事：①我们此刻在旅程的哪一段 ②这一章在做什么 ③读时留意什么。
            **不要剧透**情绪高潮或谜底；勾起好奇即可。一句话，大白话、有温度。
- "arc_tag": 一个很短的脉络标签（如"归途的决心""第一次正面说出永恒轮回""向旧价值开战"）。
- "decode": 至多 3 条意象解码，专挑**大意象、不解码就读不懂**的（小典故译注已覆盖，别抢）。
            每条 {"anchor":"从正文逐字复制的一个短语，必须和原文一模一样","gloss":"这个意象其实在说什么，大白话点破"}。
            anchor 必须是该章正文里**真实出现、可逐字匹配**的子串；拿不准就不要给。无则空数组 []。
- "explain": **只在这章确实是难啃的地标时**给（如讲永恒轮回、沉重之灵、新旧法版、康复、七个印这类）。
            {"label":"按钮文字（以 ▽ 结尾）","paras":["把这章的核心用大白话讲透,一段段来","..."],"example":"一个书外的小例子让抽象点咔哒一下（务必忠实,别瞎编）"}。
            否则 null。普通过渡章一律 null。

铁律：大白话、具体；不剧透留给读者自己撞见；explain 宁缺毋滥；anchor 一定逐字。
**严格输出一个 JSON 对象，不要 markdown 围栏、不要多余文字。**"""


def _disc_plaintext(disc) -> str:
    out = []
    for kind, val in disc["blocks"]:
        if kind == "p":
            out.append("".join(s for k, s in val if k == "text"))
    return "".join(out)


def build_prompt(discourses):
    lines = []
    for i, disc in enumerate(discourses, 1):
        lines.append(f"===== 第{i}章《{disc['title']}》 =====")
        pi = 0
        for kind, val in disc["blocks"]:
            if kind == "sub":
                lines.append(f"[小节 {val}]")
            else:
                txt = "".join(s for k, s in val if k == "text").strip()
                if txt:
                    lines.append(f"段[{pi}] {txt}")
                    pi += 1
    head = ("下面是《第三部》全部 %d 章的逐字原文，请按要求产出每章标注包 JSON。\n\n"
            % len(discourses))
    return head + "\n".join(lines)


def _parse_json(text):
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)```", t, re.DOTALL)
    if m:
        t = m.group(1).strip()
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b > a:
        t = t[a:b + 1]
    try:
        d = json.loads(t)
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        return None


def ai_annotate(discourses):
    if not os.environ.get("AI_API_KEY"):
        return None
    try:
        from ai_utils import call_ai  # noqa: E402
    except Exception as e:
        print(f"[warn] 无法加载 ai_utils: {e}", file=sys.stderr)
        return None
    try:
        # 推理模型(deepseek-v4-pro)会先烧大量 reasoning token，预算给足。
        resp, tokens = call_ai(build_prompt(discourses), SYSTEM, max_tokens=32000)
    except Exception as e:
        print(f"[warn] AI 调用失败，退回 fallback：{e}", file=sys.stderr)
        return None
    data = _parse_json(resp)
    if data is None:
        print("[warn] AI 输出非合法 JSON，退回 fallback", file=sys.stderr)
        return None
    print(f"[ok] AI 标注 JSON（tokens={tokens}，{len(data)} 章）", file=sys.stderr)
    return data


# ----------------------------------------------------------------------------
# 4. 程序：渲染（逐字原文 + 行内译注 + 标注层）+ 无损校验
# ----------------------------------------------------------------------------

def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _attr(s: str) -> str:
    return _esc(s).replace('"', "&quot;")


def render_paragraph(segs):
    """返回 (html, 逐字正文 recon)。译注抽成可点 <sup>；正文逐字。"""
    html, recon = [], []
    for kind, val in segs:
        if kind == "text":
            html.append(_esc(val))
            recon.append(val)
        else:  # note
            html.append(f'<sup class="zy" data-note="{_attr(val)}">译</sup>')
    return f'<p class="orig">{"".join(html)}</p>', "".join(recon)


def render_decode(decode, disc_text):
    items = []
    for d in decode or []:
        anchor = (d.get("anchor") or "").strip()
        gloss = (d.get("gloss") or "").strip()
        if not anchor or not gloss:
            continue
        # 防瞎编：锚点必须逐字出现在本章正文里（忽略空白）
        if re.sub(r"\s+", "", anchor) not in re.sub(r"\s+", "", disc_text):
            print(f"[warn] 丢弃非逐字锚点：{anchor[:20]}…", file=sys.stderr)
            continue
        items.append(f'<li><span class="anc">「{_esc(anchor)}」</span>{gloss}</li>')
    if not items:
        return ""
    return ('<div class="decode"><div class="dk">意象解码</div><ul>'
            + "".join(items) + "</ul></div>")


def render_discourse(idx, disc, ann):
    ann = ann or {}
    disc_text = _disc_plaintext(disc)
    h = [f'<section class="disc" id="c{idx}">']
    h.append(f'<div class="head"><span class="cidx">第三部 · {idx:02d}</span>'
             f'<h2>{_esc(disc["title"])}</h2></div>')
    if ann.get("arc_tag"):
        h.append(f'<span class="tag">{_esc(ann["arc_tag"])}</span>')
    if disc.get("note"):
        h.append('<div class="cnote"><button>原书译注：这一章在讲什么 ▽</button>'
                 f'<div class="nbox">{_esc(disc["note"])}</div></div>')
    if ann.get("opener"):
        h.append(f'<p class="opener">▸ {_esc(ann["opener"])}</p>')

    recon = []
    for kind, val in disc["blocks"]:
        if kind == "sub":
            h.append(f'<div class="sub">{_esc(val)}</div>')
        else:
            php, rec = render_paragraph(val)
            h.append(php)
            recon.append(rec)

    h.append(render_decode(ann.get("decode"), disc_text))

    ex = ann.get("explain")
    if ex and ex.get("paras"):
        body = "".join(f"<p>{x}</p>" for x in ex["paras"])
        if ex.get("example"):
            body += f'<p class="hex">{ex["example"]}</p>'
        h.append(f'<div class="help"><button>{_esc(ex.get("label", "卡住了？大白话讲一遍 ▽"))}</button>'
                 f'<div class="hbox">{body}</div></div>')
    h.append("</section>")
    return "\n".join(x for x in h if x), "".join(recon)


def render_html(doc_title, epigraph, discourses, ann_by_idx):
    body, lossless = [], True

    epi = []
    for segs in epigraph:
        php, _ = render_paragraph(segs)
        epi.append(php)
    if epi:
        body.append('<section class="epigraph">' + "".join(epi) + "</section>")

    for i, disc in enumerate(discourses, 1):
        ann = ann_by_idx.get(str(i)) or ann_by_idx.get(i)
        sec_html, recon = render_discourse(i, disc, ann)
        body.append(sec_html)
        want = re.sub(r"\s+", "", _disc_plaintext(disc))
        got = re.sub(r"\s+", "", recon)
        if want != got:
            lossless = False
            print(f"[ERR] 第{i}章《{disc['title']}》正文不无损：want {len(want)} got {len(got)}",
                  file=sys.stderr)
    nav = "".join(
        f'<a href="#c{i}">{i:02d} {_esc(d["title"])}</a>'
        for i, d in enumerate(discourses, 1))
    html = (_TEMPLATE.replace("{{TITLE}}", _esc(doc_title))
            .replace("{{NAV}}", nav)
            .replace("{{BODY}}", "\n\n".join(body)))
    return html, lossless


_TEMPLATE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{{TITLE}} · 第三部</title><style>
:root{--bg:#f6f2ea;--card:#fffdf7;--ink:#222019;--muted:#867c6c;--line:#e6ddcd;--accent:#8a5a2b;--reveal:#f1ead9;--chip:#7d4f25;--note:#5d6f4e}
@media(prefers-color-scheme:dark){:root{--bg:#13120d;--card:#1c1a13;--ink:#e9e3d4;--muted:#9b9182;--line:#322d22;--accent:#d59a5f;--reveal:#231f17;--chip:#d59a5f;--note:#9bb083}}
*{box-sizing:border-box}html,body{margin:0;padding:0;background:var(--bg);color:var(--ink)}
body{font-family:"Noto Serif CJK SC","Songti SC","Source Han Serif SC",Georgia,serif;font-size:18px;line-height:1.95;-webkit-text-size-adjust:100%;padding:env(safe-area-inset-top) 0 6rem}
.wrap{max-width:680px;margin:0 auto;padding:0 18px}
header{padding:34px 0 6px;text-align:center}header .t{font-size:1.5rem;font-weight:800}header .p{font-size:1.05rem;color:var(--accent);font-weight:700;margin-top:4px}header .s{color:var(--muted);font-size:.82rem;margin-top:8px;line-height:1.7}
.toc{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:12px 14px;margin:18px 0}
.toc .tk{font-size:.74rem;color:var(--muted);letter-spacing:.1em;margin-bottom:6px}
.toc a{display:inline-block;color:var(--accent);text-decoration:none;font-size:.86rem;margin:3px 12px 3px 0;white-space:nowrap}
.epigraph{margin:18px 0;padding:14px 16px;border-left:3px solid var(--accent);color:var(--muted);font-style:italic}
.epigraph p{margin:.4em 0}
section.disc{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px 18px;margin:22px 0}
.head{border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:6px}
.cidx{font-size:.7rem;color:var(--muted);letter-spacing:.12em}
.head h2{font-size:1.32rem;font-weight:800;margin:3px 0 0;color:var(--ink)}
.tag{display:inline-block;margin:10px 0 2px;font-size:.72rem;font-weight:700;color:var(--card);background:var(--chip);padding:3px 10px;border-radius:999px}
.opener{color:var(--accent);font-style:italic;font-size:.92rem;line-height:1.74;margin:12px 0 16px}
.sub{font-weight:800;color:var(--accent);font-size:.92rem;margin:18px 0 2px;opacity:.8}
section.disc p.orig{margin:.75em 0}
sup.zy{color:var(--note);font-size:.6em;font-weight:700;vertical-align:super;cursor:pointer;border:1px solid var(--note);border-radius:4px;padding:0 2px;margin:0 1px;line-height:1}
.zypop{position:fixed;left:0;right:0;bottom:0;z-index:50;background:var(--card);border-top:2px solid var(--note);box-shadow:0 -6px 24px rgba(0,0,0,.18);padding:16px 18px calc(16px + env(safe-area-inset-bottom));max-height:55vh;overflow:auto;transform:translateY(110%);transition:transform .22s}
.zypop.open{transform:translateY(0)}
.zypop .zk{font-size:.72rem;color:var(--note);font-weight:700;letter-spacing:.1em;margin-bottom:6px}
.zypop .zt{font-size:.96rem;line-height:1.8}
.zypop .zx{position:absolute;top:10px;right:14px;color:var(--muted);font-size:1.3rem;cursor:pointer;line-height:1}
.decode{margin:16px 0 4px;background:var(--reveal);border-radius:12px;padding:12px 15px}
.decode .dk{font-size:.74rem;font-weight:700;color:var(--accent);letter-spacing:.08em;margin-bottom:6px}
.decode ul{margin:0;padding-left:1.1em}.decode li{margin:.5em 0;font-size:.92rem;line-height:1.78}
.decode .anc{font-weight:700;color:var(--accent)}
.help{margin-top:14px}
.help>button{appearance:none;font-family:inherit;font-weight:700;font-size:.9rem;padding:9px 14px;border-radius:12px;cursor:pointer;width:100%;text-align:left;color:var(--accent);border:1px dashed var(--accent);background:transparent}
.help .hbox{display:none;margin-top:10px;padding:13px 15px;background:var(--reveal);border-radius:0 10px 10px 0;border-left:3px solid var(--accent);font-size:.94rem;line-height:1.86}
.help.open .hbox{display:block}.help.open>button{opacity:.55}
.help .hbox .hex{background:var(--card);border:1px dashed var(--line);border-radius:8px;padding:9px 12px;margin:.7em 0}
.cnote{margin:8px 0 4px}
.cnote>button{appearance:none;font-family:inherit;font-weight:700;font-size:.82rem;padding:7px 12px;border-radius:10px;cursor:pointer;width:100%;text-align:left;color:var(--note);border:1px solid var(--line);background:transparent}
.cnote .nbox{display:none;margin-top:8px;padding:11px 14px;background:var(--reveal);border-radius:10px;font-size:.88rem;line-height:1.8;color:var(--muted)}
.cnote.open .nbox{display:block}
b,strong{color:var(--accent)}
footer{color:var(--muted);font-size:.78rem;text-align:center;margin:34px 0 0;line-height:1.7}
</style></head><body><div class="wrap">
<header><div class="t">{{TITLE}}</div><div class="p">第三部</div>
<div class="s">领读版 · 正文逐字取自规范化原文，逐章经程序校验无损 · 标注与「译」均为附加层</div></header>
<nav class="toc"><div class="tk">第三部 · 十六章</div>{{NAV}}</nav>
{{BODY}}
<footer>「译」是原书译注，点字即看。导引与意象解码先别全信结论，自己撞见更爽。<br>正文与规范化原文逐字一致。</footer>
</div>
<div class="zypop" id="zypop"><span class="zx" id="zyx">×</span><div class="zk">译注</div><div class="zt" id="zyt"></div></div>
<script>
document.querySelectorAll(".help>button,.cnote>button").forEach(function(x){x.addEventListener("click",function(){var p=x.parentElement;p.classList.toggle("open");x.textContent=p.classList.contains("open")?x.textContent.replace("▽","△"):x.textContent.replace("△","▽");});});
var pop=document.getElementById("zypop"),pt=document.getElementById("zyt");
document.querySelectorAll("sup.zy").forEach(function(s){s.addEventListener("click",function(){pt.textContent=s.getAttribute("data-note");pop.classList.add("open");});});
document.getElementById("zyx").addEventListener("click",function(){pop.classList.remove("open");});
document.addEventListener("click",function(e){if(!pop.contains(e.target)&&!e.target.classList.contains("zy"))pop.classList.remove("open");});
</script></body></html>"""


# ----------------------------------------------------------------------------
# fallback 标注（手工打磨：16 章导引 + 地标章大白话；AI 不可用时用）
# ----------------------------------------------------------------------------

FALLBACK = json.loads(r"""
{
 "1": {"arc_tag":"归途的决心",
   "opener":"第三部开篇。查拉图斯特拉离开幸福岛，午夜独自翻山去赶船——这是一段回家的路。读时留意一个反复出现的念头：要登上最高处，他说必须先怎么做？",
   "decode":[{"anchor":"最高者必须从最深处升起","gloss":"全书的一句钥匙：想达到最高的肯定，必须先沉到最深的痛苦与厌恶里走一遭，不能绕过去。"}],
   "explain":null},
 "2": {"arc_tag":"第一次正面抛出永恒轮回",
   "opener":"全书最重要的一章之一。他对船员讲一个'幻影和谜'：和一个矮子（沉重之灵）一起登山，走到一道叫'此刻'的门前。读时盯住那道门、那两条路；最后还有个牧人和一条蛇。先别急着求解，让画面砸到你。",
   "decode":[
     {"anchor":"半侏儒，半鼹鼠","gloss":"压在他肩上的'沉重之灵'——让一切变重、往下拽的精神：怀疑、轻蔑、'反正没意义'。"},
     {"anchor":"两条路","gloss":"过去与未来两条无尽的路，在'此刻'这道门相遇。这是他抛出的谜：时间会不会是一个圈，万物永远回来？"}],
   "explain":{"label":"卡住了？把这章的谜大白话讲一遍 ▽",
     "paras":[
       "一句话：<b>他在问你——如果你这一生、连同所有痛苦和琐碎，要一模一样地无限次重来，你受得了吗？你还愿意吗？</b>这就是'永恒轮回'。",
       "那道叫'此刻'的门：往后是无尽的过去，往前是无尽的未来，两头都没有头。如果时间无限、东西有限，那么发生过的一切迟早还会再发生——包括此刻你读这句话。这不是科学结论，是一个<b>逼问</b>：你能不能对生命整个说'好，再来一次'？",
       "牧人被蛇钻进喉咙、咬住不放——这是'轮回'这个念头卡在喉咙里恶心人的样子。他咬下蛇头、跳起来大笑：这是<b>战胜</b>那个念头的画面——不是消灭它，是有能力对它笑着说'是'。这一笑，就是第三部要走到的地方。"],
     "example":"想象有人对你说：你今天经历的一切，好的坏的，要永远循环播放，永不结束。第一反应大概是窒息。尼采要的，正是让你修炼到能不窒息、甚至想要——那才叫真正爱这一生。"}},
 "3": {"arc_tag":"幸福追上来，他却往后躲",
   "opener":"船行海上。'幸福'像甩不掉的影子一直追他，他反而想推开它。读时留意：他为什么不肯安心享受幸福？他在等的、惦记的是什么？",
   "decode":[], "explain":null},
 "4": {"arc_tag":"对天空的颂歌：万物头上是'偶然'",
   "opener":"日出之前，一段写给天空的情话。这章是第三部里少见的明亮高处。留意他怎么把'偶然'当成好词来用——他在为这个世界'去罪化'。",
   "decode":[{"anchor":"偶然","gloss":"他反对'万物背后有目的、有审判'。说万物头上只有'偶然'这片纯净天空，是要解除压在生命上的罪与目的，让存在重新变得清白、可祝福。"}],
   "explain":null},
 "5": {"arc_tag":"回到人间：人都变小了",
   "opener":"他下山回到人群中，发现人和他们的德性都'变小了'。读时留意他的失望：这种'小'具体小在哪——是恶，还是别的什么？",
   "decode":[{"anchor":"变小","gloss":"不是变坏，是变得平庸、知足、怕风险——把'小小的幸福、小小的德性'当成全部。尼采觉得这比恶更让人绝望。"}],
   "explain":null},
 "6": {"arc_tag":"把幸福藏进冬天的沉默",
   "opener":"橄榄山上的冬天。他故意把自己的温暖和幸福藏在冰冷沉默的背后。读时留意：他为什么要'装冷'、要沉默？沉默对他是软弱还是力量？",
   "decode":[], "explain":null},
 "7": {"arc_tag":"在大城门口：学会'走开'",
   "opener":"大城门口，一个模仿他腔调的疯子/猴子拦住他大骂这座城。读时留意：尼采怎么和这个'像他、却只会咒骂'的声音划清界限？这一章的题眼是最后那句关于'爱'与'走开'。",
   "decode":[{"anchor":"走开","gloss":"在不能再爱的地方，就该走开、路过，而不是停下来咒骂。咒骂也是一种被缠住；真正的轻蔑是不屑、是离开。"}],
   "explain":null},
 "8": {"arc_tag":"背教者：又爬回信仰里去的人",
   "opener":"他看到曾经追随过新思想的人，又悄悄爬回旧信仰、旧上帝那里去了。读时留意他对'退回去'的诊断：人为什么会害怕自由、想要重新跪下？",
   "decode":[], "explain":null},
 "9": {"arc_tag":"还乡：孤独张开双臂迎他",
   "opener":"他回到山洞，回到孤独。这一章很温柔——孤独像家一样欢迎他。读时留意他怎么区分'孤独'和'寂寞'：在人群里反而更孤单，这是什么意思？",
   "decode":[{"anchor":"孤独","gloss":"对他不是惩罚，是回家：只有在这里他才能把话说完整、做回自己。和'在人群中的寂寞'正相反。"}],
   "explain":null},
 "10": {"arc_tag":"给三样'恶'翻案",
   "opener":"他把世人眼中的三样大'恶'放上秤称一称。读时先猜：哪三样？再留意他怎么给它们翻案——在他手里，它们对'强者'反而成了好东西。",
   "decode":[{"anchor":"肉欲、统治欲、自私自利","gloss":"世人诅咒的三样'恶'。尼采不否认它们危险，但反对一刀切地定罪：对强健、向上的人，这三样恰是生命力的表现，该重新估价。"}],
   "explain":null},
 "11": {"arc_tag":"点名头号敌人：沉重之灵",
   "opener":"这章把第三部的对手挑明了：'沉重之灵'——那个让一切变重、逼你低头、替你规定'善与恶'的精神。读时留意他给的解药：飞、笑，和'找到你自己的路'。",
   "decode":[
     {"anchor":"重压之魔","gloss":"也就是第2章那个矮子：让人自我轻蔑、被'你应该'压垮、不敢轻盈起舞的精神。它是查拉图斯特拉最大的敌人。"}],
   "explain":{"label":"卡住了？大白话讲一遍 ▽",
     "paras":[
       "一句话：<b>真正压垮人的不是哪条具体规矩，而是那种'凡事都很重、我必须低头、我不够好'的整体感觉——这就是'沉重之灵'。</b>",
       "它怎么得手？它从小就往你背上装'善与恶'的现成法版，让你背着别人的价值过一生，还以为是自己的。对付它，第一步是<b>学会爱自己</b>——不是自恋，是不再自我轻蔑地把自己当包袱。",
       "第二步是<b>飞和笑</b>：能轻盈，能对'重'发笑，就压不垮你。最后他说'这是我的路，你们的路呢？'——<b>那条人人适用的'正道'根本不存在</b>。沉重之灵最大的谎，就是让你相信只有一条路、且必须照走。"],
     "example":"像一个人扛着家人朋友社会塞给他的全部'应该'过日子，越走越喘。解法不是再找一套更对的'应该'，而是先放下'我必须按某条标准活'本身，找到自己走得动、走得笑出来的那条路。"}},
 "12": {"arc_tag":"向旧价值开战：砸碎旧法版，刻新法版",
   "opener":"第三部最长、最核心的一章，30 个小节，像一篇宣言。'法版'指刻着善恶戒律的石板（摩西十诫的回声）。读时不必一口气读完，挑打动你的小节停下来；留意一个反复的呼唤'哦,我的兄弟们'，和一个难念头：怎么救赎'过去'。",
   "decode":[
     {"anchor":"法版","gloss":"刻着'你应该/不应该'的价值石板（摩西十诫的意象）。旧法版＝过时的善恶；他要砸碎旧的、刻写新的。"}],
   "explain":{"label":"卡住了？抓住这章的两根主线 ▽",
     "paras":[
       "这章很长，但你只要抓两根线就不会乱。",
       "<b>第一根：破与立。</b>旧的'善恶'是前人刻下、如今变成枷锁的法版；他要亲手砸碎，并号召'兄弟们'一起去刻写新的价值。注意他不是要'没有价值'，而是要<b>由创造者重新立法</b>。",
       "<b>第二根：救赎'过去'（最难也最关键）。</b>人最大的怨恨，是对'木已成舟、它已经发生了'无能为力——这让人怨毒、想报复。解药是把'它曾经如此'转化成<b>'我愿它如此'</b>：对自己的过去整个说'是'。这正通往永恒轮回——能对一生说'再来一次'，过去就不再是包袱，而是你愿意的命运。"],
     "example":"像对自己最后悔的那段经历：与其一辈子'要是当初没那样就好了'，不如走到'正是这些，才长成现在的我，我认了、我要了'。前者被过去拖着，后者把过去变成自己的。"}},
 "13": {"arc_tag":"康复：直面最深的念头，挺过来",
   "opener":"地标章。查拉图斯特拉主动召唤他'最深渊的思想'，被它击倒，躺了七天像大病一场，然后康复。读时留意：他的动物（鹰和蛇）给了他一个称号——他是什么的老师？以及他病的、又挺过来的，到底是哪个念头。",
   "decode":[
     {"anchor":"深邃的思想","gloss":"就是永恒轮回。最难咽下的部分是：连最渺小、最让人厌恶的人和事，也要永远回来。这才是让他病倒的那一口。"}],
   "explain":{"label":"卡住了？大白话讲一遍他在'康复'什么 ▽",
     "paras":[
       "一句话：<b>他终于敢把永恒轮回完整地吞下去——包括最恶心的那部分——并且活了过来、能对它说'是'。这就是'康复'。</b>",
       "第2章他只是抛出谜；这里他<b>正面承受</b>它。最噎人的不是痛苦要重来，而是：那些渺小、平庸、让他厌恶的人和事，也要原封不动永远回来。这口气差点要了他的命。",
       "七天倒下、又起来，他的动物宣布他是'永恒轮回的老师'。康复的意思是：他不再被这个念头恶心、压垮，而是能把它当作自己的真理去教——能对整个存在（连同它的渺小）由衷说'好，再来'。这是第13章为最后的肯定（14-16章）铺好的台阶。"],
     "example":"像一个人终于接纳了自己人生最不堪的部分：不是假装它不存在，也不是被它压死，而是说'这些也是我，我整个要了'。接纳之后，人反而轻了、能笑了——这就是这章的'康复'。"}},
 "14": {"arc_tag":"对自己灵魂的告白：满到要溢出来",
   "opener":"康复之后，他转身对自己的'灵魂'说话——像清点一个满到溢出的宝库。读时留意语气的变化：从受难转向感恩与给予，他在为接下来的两首歌蓄势。",
   "decode":[], "explain":null},
 "15": {"arc_tag":"与'生命'共舞，午夜钟声响起",
   "opener":"他和'生命'（一个女子的形象）跳舞、调情、又怄气。读到最后，午夜的钟一下下敲响。留意那支著名的小调：'哦，人哪！留意！'——它在为下一章的大肯定起调。",
   "decode":[{"anchor":"午夜","gloss":"那支午夜小调说'世界很深，比白昼想的更深；痛苦说：消逝吧——可一切欢乐都要永恒、要深深的永恒'。这是把轮回从恐怖翻转成渴望的转折。"}],
   "explain":null},
 "16": {"arc_tag":"七次说'是'：与永恒成婚",
   "opener":"第三部的终曲，七个小节，每节都落在同一句呐喊上。读时把它当歌来读、一节节往上叠：他不再受难、不再求解，只剩纯粹的肯定。留意那个反复的称呼，和'永恒'这个词。",
   "decode":[
     {"anchor":"因为我爱你，永远","gloss":"七次盖章式的肯定（呼应《启示录》的七印），但内容相反：不是末日审判，而是为'永恒'背书。每节都收束于这句对永恒的爱的呐喊。"}],
   "explain":{"label":"卡住了？这章为什么是全书的'到达' ▽",
     "paras":[
       "一句话：<b>走完痛苦、厌恶、康复，他终于能毫无保留地对生命与永恒说七遍'是'——这是整个第三部的目的地。</b>",
       "前面所有的攀登、那条噎人的蛇、七天的病，都是为了能站到这里：每一节都以'因为我爱你，哦，永恒！'收尾。'永恒'就是永恒轮回——他现在不是忍受它，而是<b>娶它为妻、想要它</b>。",
       "这就是尼采说的'爱命运'（amor fati）的最高形态：不是认命，是热爱——对一生连同它的全部，发自心底地喊'再来一次，永远再来'。第三部到此圆满。"],
     "example":"想象一个人走过最难的一程后，不是松口气说'总算过去了'，而是张开手说'我爱这一切，一次次都要'。从咬牙忍受到放声去爱——这一步之差，就是查拉图斯特拉整个第三部走的路。"}}
}
""")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main(argv):
    ap = argparse.ArgumentParser(description="单脚本：生成《查拉图斯特拉·第三部》领读版")
    ap.add_argument("epub", nargs="?")
    ap.add_argument("-o", "--out", default="查拉图斯特拉_第三部.html")
    ap.add_argument("--normalized")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--dump-json", help="把 AI 原始标注 JSON 存到此路径")
    args = ap.parse_args(argv[1:])

    if args.normalized:
        norm = open(args.normalized, encoding="utf-8").read()
        print(f"[1/4] 读入 normalized: {args.normalized}", file=sys.stderr)
    elif args.epub:
        print(f"[1/4] Step1+Step2: {args.epub} -> normalized …", file=sys.stderr)
        norm = epub_to_normalized(args.epub)
    else:
        ap.error("需要 EPUB 或 --normalized")

    doc_title, epigraph, discourses = extract_part3(norm)
    print(f"[2/4] 切出第三部：{len(discourses)} 章，开篇引语 {len(epigraph)} 段",
          file=sys.stderr)

    ann = None if args.no_ai else ai_annotate(discourses)
    source = "ai"
    if ann is None:
        ann, source = FALLBACK, "fallback"
    elif args.dump_json:
        with open(args.dump_json, "w", encoding="utf-8") as f:
            json.dump(ann, f, ensure_ascii=False, indent=2)
        print(f"[ok] AI 原始 JSON -> {args.dump_json}", file=sys.stderr)
    print(f"[3/4] 标注来源：{source}", file=sys.stderr)

    html_out, lossless = render_html(doc_title, epigraph, discourses, ann)
    if not lossless:
        print("[FATAL] 正文未通过无损校验，拒绝输出。", file=sys.stderr)
        return 1
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"[4/4] 无损校验通过；写出 {len(html_out)} 字节 -> {args.out}（{source}）",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
