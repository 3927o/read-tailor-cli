#!/usr/bin/env python3
"""单脚本：从一本书生成「导读网页」，模拟正式管线逻辑（Route 1）。

正式逻辑（程序做机械活，AI 只生成内容、不碰结构/不碰原文）：
  1. 程序：epub --Step1(pandoc)--> raw --Step2(recover+plan_7)--> normalized HTML
  2. 程序：从 normalized 机械抽出「书摘要」= 元信息 + 章节 + 节(§)计数 + 采样文本
           （有界上下文，绝不把整本书塞进 prompt）
  3. AI  ：读「书摘要」，产出**结构化 JSON** 导读内容（怎么读 / 这本书在干嘛 /
           读法 / 地图 / 核心词 / 高光 / 排错）。AI 只产数据，不写 HTML、不碰原文。
  4. 程序：把 JSON 渲染进**固定 HTML 模板**；高光段的 § 编号由程序**回 normalized
           取真实原文**做引文（不信 AI 的引用，保证逐字无损）。
  5. 程序：写出单文件 HTML。

AI 不可用（无 AI_* 环境变量 / 网络受限 / 解析失败）时退回内置 fallback 内容，
保证脚本永远能跑出一个页面。

用法：
  AI_BASE_URL=... AI_API_KEY=... AI_MODEL=... \
      python3 zx_guide.py 哲学研究.epub -o 哲学研究_导读.html
  python3 zx_guide.py 哲学研究.epub --no-ai      # 跳过 AI，用 fallback
  python3 zx_guide.py --normalized norm.html      # 直接给 normalized，跳过 Step1/2
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bs4 import BeautifulSoup  # noqa: E402

# ----------------------------------------------------------------------------
# 1. 程序：epub -> normalized HTML（复用正式 Step1 / Step2 模块）
# ----------------------------------------------------------------------------


def epub_to_normalized(epub_path: str) -> str:
    """Step1 pandoc + Step2 (epub_recover.recover -> plan_7.build_normalized)."""
    from common.epub_recover import recover          # noqa: E402
    from plan_7_program_first import build_normalized  # noqa: E402

    raw = subprocess.run(
        ["pandoc", epub_path, "-f", "epub", "-t", "html", "-s", "-o", "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    augmented, _log = recover(epub_path, raw)
    # decisions={} ：不调 AI 排标题层级（导读不需要它，§ 本就是段落）
    title = _guess_title(BeautifulSoup(augmented, "html.parser"))
    return build_normalized(augmented, {}, {"title": title, "language": "zh"})


def _guess_title(soup: BeautifulSoup) -> str:
    t = soup.find("title")
    return t.get_text(strip=True) if t and t.get_text(strip=True) else "Untitled"


# ----------------------------------------------------------------------------
# 2. 程序：从 normalized 机械抽「书摘要」
# ----------------------------------------------------------------------------

_SEC_RE = re.compile(r"^\s*§\s*(\d+)\s*$")


def build_remark_index(norm_html: str) -> dict[int, str]:
    """§N -> 紧随其后的正文（截断）。只记每个编号的**首次**出现（= 第一部分）。"""
    soup = BeautifulSoup(norm_html, "html.parser")
    body = soup.select_one("#bodymatter") or soup
    blocks = body.find_all(["p", "h1", "h2", "h3", "h4", "blockquote"])
    index: dict[int, str] = {}
    i = 0
    while i < len(blocks):
        m = _SEC_RE.match(blocks[i].get_text(" ", strip=True))
        if m:
            n = int(m.group(1))
            buf = []
            j = i + 1
            while j < len(blocks) and not _SEC_RE.match(blocks[j].get_text(" ", strip=True)):
                buf.append(blocks[j].get_text(" ", strip=True))
                j += 1
            text = re.sub(r"\s+", " ", " ".join(buf)).strip()
            if n not in index and text:
                index[n] = text
            i = j
        else:
            i += 1
    return index


def build_digest(norm_html: str, index: dict[int, str], max_chars: int = 4000) -> dict:
    """机械摘要：标题 / 章节 / 节数 / 采样若干节文本（有界）。"""
    soup = BeautifulSoup(norm_html, "html.parser")
    title = ""
    tnode = soup.find("title")
    if tnode:
        title = tnode.get_text(strip=True)
    chapters = [h.get_text(" ", strip=True) for h in soup.select("section.chapter > h1")]
    nums = sorted(index)
    # 采样：开头 8 条 + 等距抽 8 条，给 AI 一点全书"口味"，但不塞全书
    sample_nums = nums[:8]
    if len(nums) > 16:
        step = len(nums) // 8
        sample_nums += nums[8::step][:8]
    samples = []
    budget = max_chars
    for n in sample_nums:
        t = index[n][:240]
        if budget - len(t) < 0:
            break
        samples.append(f"§{n}: {t}")
        budget -= len(t)
    return {
        "title": title,
        "chapters": chapters,
        "remark_count": len(nums),
        "remark_max": nums[-1] if nums else 0,
        "samples": samples,
    }


# ----------------------------------------------------------------------------
# 3. AI：书摘要 -> 导读 JSON（AI 只产数据）
# ----------------------------------------------------------------------------

GUIDE_SCHEMA = """{
  "title": "导读标题",
  "subtitle": "副标题（作者/译者 + 一句话定位）",
  "intro": "开篇定调：1-2 段，告诉读者这是怎样一本书、该带什么心态。可用 \\n\\n 分段。",
  "how_to_read": ["读法要点1", "读法要点2", "..."],
  "project": {
    "thesis": "用一句话概括全书到底在干嘛",
    "paragraphs": ["展开段落1", "展开段落2", "..."],
    "quote": "（可选）一句点题的原文或大意，没有就留空字符串"
  },
  "modes": [{"name": "读法名", "tag": "（可选）推荐标记，没有留空", "desc": "怎么操作"}],
  "map": [{"range": "大致编号范围", "title": "这一块叫什么", "desc": "一句话讲它在干嘛"}],
  "concepts": [{"term": "核心词", "ref": "（可选）大致出处编号", "desc": "大白话解释 + 为何重要"}],
  "highlights": [{"title": "名段名字", "ref": "出处标签", "section": 编号整数或null, "why": "为什么值得专门翻去读"}],
  "troubleshooting": ["卡住时的建议1", "建议2", "..."],
  "footer": "结尾一两句鼓励的话"
}"""

SYSTEM = """你是一位顶尖的「阅读导读」作者，专门帮**初次接触、且觉得原著很难**的读者进门。
你的任务：根据给定的「书摘要」，写出一份导读的**结构化内容**。

铁律：
- 目标是帮读者「换姿势」读，而**不是**替他读完。核心是：给全局、给方法、给地图、给信心。
- 反对完美主义读法：明确告诉读者「读不懂每一句是正常的」。
- 大白话，具体，有温度；忌空话套话、忌堆术语。
- 你**只输出内容数据**，不写 HTML、不写 CSS。
- highlights 里的 section 字段：若该名段有明确的节编号，填整数；否则填 null。
- 严格输出**一个 JSON 对象**，不要 markdown 代码围栏，不要任何额外说明文字。

输出必须严格符合这个 schema（键名固定，值用简体中文）：
""" + GUIDE_SCHEMA


def ai_generate_guide(digest: dict) -> dict | None:
    if not os.environ.get("AI_API_KEY"):
        return None
    try:
        from ai_utils import call_ai  # noqa: E402
    except Exception as e:  # pragma: no cover
        print(f"[warn] 无法加载 ai_utils: {e}", file=sys.stderr)
        return None

    user = (
        "下面是一本书的机械摘要（结构 + 采样，不是全书）。"
        "请据此写导读内容 JSON。\n\n"
        f"书名: {digest['title']}\n"
        f"章节: {digest['chapters']}\n"
        f"全书共有带编号的「节(§)」约 {digest['remark_count']} 条（最大编号 {digest['remark_max']}）。\n\n"
        "节文本采样：\n" + "\n".join(digest["samples"])
    )
    try:
        # deepseek-v4-pro 是推理模型，会先消耗大量 reasoning token 且忽略
        # thinking:disabled，预算给小了会全被推理吃光、content 为空，故放大。
        resp, tokens = call_ai(user, SYSTEM, max_tokens=24000)
    except Exception as e:
        print(f"[warn] AI 调用失败，退回 fallback：{e}", file=sys.stderr)
        return None
    data = _parse_json(resp)
    if data is None:
        print("[warn] AI 输出不是合法 JSON，退回 fallback", file=sys.stderr)
        return None
    print(f"[ok] AI 生成导读 JSON（tokens={tokens}）", file=sys.stderr)
    return data


def _parse_json(text: str) -> dict | None:
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)```", t, re.DOTALL)
    if m:
        t = m.group(1).strip()
    # 容错：截取第一个 { 到最后一个 }
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b != -1 and b > a:
        t = t[a:b + 1]
    try:
        d = json.loads(t)
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        return None


# ----------------------------------------------------------------------------
# 4. 程序：高光段的 § 回原文取真实引文（不信 AI 的引用）
# ----------------------------------------------------------------------------


def enrich_highlights(guide: dict, index: dict[int, str], quote_len: int = 80) -> None:
    for h in guide.get("highlights", []):
        sec = h.get("section")
        if isinstance(sec, int) and sec in index:
            teaser = index[sec][:quote_len].rstrip()
            if len(index[sec]) > quote_len:
                teaser += "…"
            h["_quote"] = teaser  # 程序填的真实原文片段


# ----------------------------------------------------------------------------
# 5. 程序：固定模板渲染（确定性、安全）
# ----------------------------------------------------------------------------

def _e(s) -> str:
    return html.escape(str(s or ""))


def _plist(items) -> str:
    return "".join(f"<li>{_e(x)}</li>" for x in (items or []))


def render_html(g: dict) -> str:
    secs = []
    # 01 怎么读
    secs.append(f'<h2 class="sec" id="how"><span class="n">01</span>怎么读</h2>'
                f'<ul class="clean">{_plist(g.get("how_to_read"))}</ul>')
    # 02 这本书在干嘛
    proj = g.get("project", {}) or {}
    proj_ps = "".join(f"<p>{_e(p)}</p>" for p in proj.get("paragraphs", []))
    quote = f'<blockquote>{_e(proj.get("quote"))}</blockquote>' if proj.get("quote") else ""
    secs.append(f'<h2 class="sec" id="what"><span class="n">02</span>这本书到底在干嘛</h2>'
                f'<p class="big">一句话：<b>{_e(proj.get("thesis"))}</b></p>{proj_ps}{quote}')
    # 03 读法
    modes = ""
    for m in g.get("modes", []):
        tag = f'<span class="tag">{_e(m.get("tag"))}</span>' if m.get("tag") else ""
        modes += (f'<div class="mode"><div class="h">{_e(m.get("name"))}{tag}</div>'
                  f'<div class="d">{_e(m.get("desc"))}</div></div>')
    secs.append(f'<h2 class="sec" id="mode"><span class="n">03</span>挑一种读法</h2>{modes}')
    # 04 地图
    mp = "".join(
        f'<li><b>{_e(x.get("range"))}　{_e(x.get("title"))}</b>：{_e(x.get("desc"))}</li>'
        for x in g.get("map", []))
    secs.append(f'<h2 class="sec" id="map"><span class="n">04</span>一张地图：全书在打哪几场仗</h2>'
                f'<ul class="clean">{mp}</ul>')
    # 05 核心词
    cc = ""
    for c in g.get("concepts", []):
        ref = f'<span class="sec-ref">{_e(c.get("ref"))}</span>' if c.get("ref") else ""
        cc += (f'<div class="card"><div class="h">{_e(c.get("term"))}{ref}</div>'
               f'<div class="d">{_e(c.get("desc"))}</div></div>')
    secs.append(f'<h2 class="sec" id="words"><span class="n">05</span>核心词（随身指南针）</h2>{cc}')
    # 06 高光
    hl = ""
    for h in g.get("highlights", []):
        ref = f'<span class="sec-ref">{_e(h.get("ref"))}</span>' if h.get("ref") else ""
        q = f'<blockquote>{_e(h.get("_quote"))}</blockquote>' if h.get("_quote") else ""
        hl += (f'<div class="card"><div class="h">{_e(h.get("title"))}{ref}</div>{q}'
               f'<div class="why"><b>为什么：</b>{_e(h.get("why"))}</div></div>')
    secs.append(f'<h2 class="sec" id="taste"><span class="n">06</span>先尝这几口（最值得直接翻去读的名段）</h2>'
                f'<p class="lead">每段附一句"为什么去读它"；引文由程序从原文取出。</p>{hl}')
    # 07 排错
    secs.append(f'<h2 class="sec" id="stuck"><span class="n">07</span>卡住了怎么办</h2>'
                f'<ul class="clean">{_plist(g.get("troubleshooting"))}</ul>')

    toc = ('<a href="#how">怎么读</a><a href="#what">在干嘛</a><a href="#mode">读法</a>'
           '<a href="#map">地图</a><a href="#words">核心词</a><a href="#taste">先尝几口</a>'
           '<a href="#stuck">卡住了</a>')
    body = "\n".join(secs)
    return _TEMPLATE.format(
        title=_e(g.get("title", "导读")),
        subtitle=_e(g.get("subtitle", "")),
        intro="".join(f"<p>{_e(p)}</p>" for p in (g.get("intro", "") or "").split("\n\n")),
        toc=toc, body=body,
        footer="<br>".join(_e(x) for x in (g.get("footer", "") or "").split("\n")),
    )


_TEMPLATE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title><style>
:root{{--bg:#f7f4ee;--card:#fffdf8;--ink:#23201b;--muted:#857c6f;--line:#e7e0d4;--accent:#9a5b34;--soft:#f1ebdf}}
@media(prefers-color-scheme:dark){{:root{{--bg:#15140f;--card:#1d1b15;--ink:#e9e4d8;--muted:#9c9384;--line:#332f26;--accent:#d79a6c;--soft:#23201a}}}}
*{{box-sizing:border-box}}html,body{{margin:0;padding:0;background:var(--bg);color:var(--ink)}}
body{{font-family:"Noto Serif CJK SC","Songti SC","Source Han Serif SC",Georgia,serif;font-size:18px;line-height:1.9;-webkit-text-size-adjust:100%;padding:env(safe-area-inset-top) 0 6rem}}
.wrap{{max-width:680px;margin:0 auto;padding:0 18px}}
header{{padding:34px 0 10px;text-align:center}}header .t{{font-size:1.7rem;font-weight:800}}header .s{{color:var(--muted);font-size:.86rem;margin-top:8px;line-height:1.6}}
.intro{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:14px;padding:16px 18px;margin:14px 0 6px;font-size:1rem;line-height:1.9}}
.intro b{{color:var(--accent)}}
nav.toc{{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 8px}}
nav.toc a{{font-size:.82rem;text-decoration:none;color:var(--accent);background:var(--soft);border:1px solid var(--line);padding:6px 11px;border-radius:999px}}
h2.sec{{font-size:1.28rem;font-weight:800;color:var(--accent);margin:34px 0 6px;scroll-margin-top:12px}}
h2.sec .n{{font-size:.8rem;color:var(--muted);font-weight:700;margin-right:8px}}
p{{margin:.7em 0}}.big{{font-size:1.08rem;line-height:1.95}}.lead{{color:var(--muted);font-style:italic;margin:.2em 0 1em}}
ul.clean{{list-style:none;padding:0;margin:.4em 0}}
ul.clean li{{position:relative;padding:9px 0 9px 22px;border-bottom:1px solid var(--line);line-height:1.78}}
ul.clean li:last-child{{border-bottom:0}}ul.clean li::before{{content:"\\203A";position:absolute;left:2px;top:8px;color:var(--accent);font-weight:800}}
ul.clean li b{{color:var(--accent)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin:12px 0}}
.card .h{{font-weight:800;color:var(--accent);font-size:1.05rem}}
.card .h .sec-ref{{font-size:.72rem;font-weight:700;background:var(--soft);color:var(--accent);padding:2px 8px;border-radius:999px;margin-left:8px;white-space:nowrap}}
.card .d{{margin-top:5px;line-height:1.82}}.card .why{{margin-top:7px;font-size:.9rem;color:var(--muted);font-style:italic}}.card .why b{{color:var(--accent);font-style:normal}}
blockquote{{margin:10px 0;padding:8px 14px;border-left:3px solid var(--accent);background:var(--soft);border-radius:0 10px 10px 0;font-style:italic;font-size:.96rem}}
.mode{{background:var(--card);border:1px dashed var(--accent);border-radius:14px;padding:13px 16px;margin:11px 0}}
.mode .h{{font-weight:800;color:var(--accent)}}.mode .h .tag{{font-size:.7rem;background:var(--accent);color:var(--card);padding:2px 8px;border-radius:999px;margin-left:8px}}
.mode .d{{margin-top:4px}}
hr.div{{border:0;border-top:1px solid var(--line);margin:30px 0}}
footer{{color:var(--muted);font-size:.9rem;text-align:center;margin:34px 0 0;line-height:1.85}}footer b{{color:var(--accent)}}
</style></head><body><div class="wrap">
<header><div class="t">{title}</div><div class="s">{subtitle}</div></header>
<div class="intro">{intro}</div>
<nav class="toc">{toc}</nav>
{body}
<hr class="div"><footer>{footer}</footer>
</div></body></html>"""


# ----------------------------------------------------------------------------
# fallback 内容（AI 不可用时；保证脚本能跑出页面）
# ----------------------------------------------------------------------------

FALLBACK = json.loads(r"""
{
 "title":"哲学研究 · 导读",
 "subtitle":"维特根斯坦 著 · 一份带你进门、而不是替你读完的地图",
 "intro":"先把一句话刻在心里：这不是一本要你读懂每一句的书。\n\n它像一本相册——东一张西一张的速写，不是一条从头推到尾的论证链。读不懂某一条是正常的；会读它的人都是反复读、跳着读、带着问题读。卡住不等于你不行。",
 "how_to_read":[
  "别想读懂每一条。读不动的做记号、往前走，意思常在后面几条才亮。",
  "带着一个问题读，而不是找一个答案。他几乎从不下定义、给结论。",
  "慢读、出声读。很多条是他在跟假想对手一问一答。",
  "读到心里咯噔一下的句子，停一会儿——那一下就是收获。",
  "把“读完”换成“逛过”，像逛美术馆只记住挪不动脚的那几幅。"
 ],
 "project":{
  "thesis":"它要把你从“词背后一定藏着一个东西”这个咒语里解放出来。",
  "paragraphs":[
   "传统哲学以为：词之所以有意义，是因为它背后对应着某个东西（对象、心里的图像、本质）。于是不停追问“什么是意义/理解/时间”。",
   "维特根斯坦说：你被语言的表面骗了。这些词底下并没有埋着一个东西，它们的全部内容就是我们在生活里怎么用它们。",
   "所以他不建理论、不下定义，而是反复把你拽回“我们实际上怎么用这个词”。他把这叫治疗：不是回答问题，而是解散问题。"
  ],
  "quote":"哲学是一场战斗：反对借助语言来蛊惑我们的理智。（§109 大意）"
 },
 "modes":[
  {"name":"A · 逛高光","tag":"最推荐先这样","desc":"只读下面“先尝几口”的名段，别的先跳过。先尝甜头。"},
  {"name":"B · 跟主线","tag":"","desc":"按地图的五大块走，每块只读开头几条 + 高光，卡住就跳。"},
  {"name":"C · 慢啃精读","tag":"","desc":"一条条来，但给自己读不懂的权利。适合第二、三遍。"}
 ],
 "map":[
  {"range":"§1–§64","title":"语言游戏与奥古斯丁图画","desc":"拆“词=物的名字”。工匠的“石板！”、指物教学。"},
  {"range":"§65–§88","title":"家族相似","desc":"各种游戏没有共同本质，只有交叉重叠的相似。别再找本质。"},
  {"range":"§89–§133","title":"哲学是什么","desc":"他停下来讲自己在干嘛：哲学是治疗，不是盖理论。"},
  {"range":"§143–§242","title":"遵守规则","desc":"规则决定不了它的应用（§201 悖论）；遵守规则是被训练出的实践。"},
  {"range":"§243–§315","title":"私人语言论证","desc":"只有我懂的私人语言不可能。高潮在 §293 甲虫盒子。"},
  {"range":"第二部分","title":"心理学哲学","desc":"知觉与概念；著名的“鸭兔图”（看作）在此。"}
 ],
 "concepts":[
  {"term":"语言游戏","ref":"","desc":"词 + 用词的活动拧成的整体。意义住在玩法里。"},
  {"term":"家族相似","ref":"§66–67","desc":"像一家人，谁都有点像谁，却没有一条共同特征。"},
  {"term":"生活形式","ref":"","desc":"语言扎根在共同的生活做法里。尽头是“我就是这么做的”。"},
  {"term":"遵守规则","ref":"§185–242","desc":"规则自己决定不了应用；靠公共实践，不靠心里的解释。"},
  {"term":"私人语言","ref":"§243+","desc":"只指我私有感觉的语言立不起来——没有公共的对错标准。"},
  {"term":"看作 / 相面","ref":"第二部分","desc":"同一张鸭兔图能看成鸭或兔。知觉里掺着概念。"}
 ],
 "highlights":[
  {"title":"奥古斯丁与五个红苹果","ref":"§1","section":1,"why":"全书起手式，“意义即使用”第一次冒头。"},
  {"title":"“一个词的意义就是它在语言中的使用”","ref":"§43","section":43,"why":"全书最有名的一句，整本书的纲。"},
  {"title":"“别想，而要看！” + 家族相似","ref":"§66–67","section":66,"why":"他整个方法的口号。"},
  {"title":"遵守规则的悖论","ref":"§201","section":201,"why":"全书最被引用的一条，后世吵了几十年。"},
  {"title":"盒子里的甲虫","ref":"§293","section":293,"why":"读着像小故事，其实是私人语言论证的高潮，可单独读。"},
  {"title":"“我抬起手臂”","ref":"§621 附近","section":621,"why":"一句话把“意志”问题问翻。"},
  {"title":"鸭兔图：“看作”","ref":"第二部分","section":null,"why":"最直观好玩、几乎不需前置知识，却直通深处。"}
 ],
 "troubleshooting":[
  "某条读不懂：做记号往前走，后面几条常会回头照亮它。",
  "连着卡：直接跳到“先尝几口”的高光，先回血。",
  "读得烦躁：合上，过几天再来。这书就是要反复读的。",
  "想要拐杖：配一本靠谱入门导读对照，或先看一段思路视频建立轮廓。"
 ],
 "footer":"把它当地图，不当考卷。\n慢一点，享受他绕圈子——圈子绕完，“啊”的那一下就来了。"
}
""")

# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="单脚本：从一本书生成导读网页（模拟正式管线）")
    ap.add_argument("epub", nargs="?", help="EPUB 路径")
    ap.add_argument("-o", "--out", default="导读.html", help="输出 HTML 路径")
    ap.add_argument("--normalized", help="直接给 normalized HTML，跳过 Step1/2")
    ap.add_argument("--no-ai", action="store_true", help="跳过 AI，直接用 fallback 内容")
    args = ap.parse_args(argv[1:])

    # 1. 程序：拿到 normalized
    if args.normalized:
        norm = open(args.normalized, encoding="utf-8").read()
        print(f"[1/5] 读入 normalized: {args.normalized}", file=sys.stderr)
    elif args.epub:
        print(f"[1/5] Step1+Step2: {args.epub} -> normalized …", file=sys.stderr)
        norm = epub_to_normalized(args.epub)
    else:
        ap.error("需要 EPUB 路径或 --normalized")

    # 2. 程序：抽结构 + 书摘要
    index = build_remark_index(norm)
    digest = build_digest(norm, index)
    print(f"[2/5] 摘要：{digest['remark_count']} 节(§)，章节 {len(digest['chapters'])}，"
          f"采样 {len(digest['samples'])} 条", file=sys.stderr)

    # 3. AI：生成导读 JSON（不可用则 fallback）
    guide = None if args.no_ai else ai_generate_guide(digest)
    source = "ai"
    if guide is None:
        guide, source = FALLBACK, "fallback"
        print(f"[3/5] 导读内容来源：{source}", file=sys.stderr)
    else:
        print(f"[3/5] 导读内容来源：{source}", file=sys.stderr)

    # 4. 程序：高光段回原文取真实引文
    enrich_highlights(guide, index)
    quoted = sum(1 for h in guide.get("highlights", []) if h.get("_quote"))
    print(f"[4/5] 高光引文回填：{quoted}/{len(guide.get('highlights', []))} 条取到真实原文",
          file=sys.stderr)

    # 5. 程序：渲染并写出
    out_html = render_html(guide)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(out_html)
    print(f"[5/5] 写出 {len(out_html)} 字节 -> {args.out}（内容来源：{source}）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
