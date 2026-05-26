#!/usr/bin/env python3
"""单脚本：从一本书生成「思辨版阅读器」，模拟正式管线逻辑（Route 1）。

这是导读(zx_guide.py)的姊妹件，做的是**真正的阅读页**：逐字原文 + 附加的
思辨标注层。核心约束是**无损**——正文一个字不改，标注只是叠加。

正式逻辑（程序做机械活+保无损，AI 只产标注数据、绝不碰正文）：
  1. 程序：epub --Step1(pandoc)--> raw --Step2(recover+plan_7)--> normalized HTML
  2. 程序：抽出某区间(默认 §1–§10)每节的**逐字段落**；含"——"的段落按破折号
           切成编号小段（供 AI 标注对话角色，但文本始终由程序持有）。
  3. AI  ：每节产一个**标注包 JSON**：开场语 / 动作标签 / 是否连接节 /
           对话各转折的角色 / 设问 / 确认 / 讲明白 / 旁注。AI 只贴标签和写导读，
           **不重写正文、不输出 HTML**。
  4. 程序：渲染 = 逐字原文(data-orig) + AI 标注层；对话按 AI 的角色把破折号小段
           分行。渲染后**逐节校验 正文==原文**（字符级），不过校验直接报错。
  5. 程序：写出单文件 HTML（手机优先、深色模式、▽ 折叠交互）。

AI 不可用时退回内置 fallback 标注（即手工打磨过的 §1–§10 版本）。

用法：
  AI_BASE_URL=... AI_API_KEY=... AI_MODEL=... python3 zx_reader.py 哲学研究.epub
  python3 zx_reader.py 哲学研究.epub --no-ai --range 1-10 -o reader.html
  python3 zx_reader.py --normalized norm.html --range 1-10
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

from bs4 import BeautifulSoup  # noqa: E402

# ----------------------------------------------------------------------------
# 1+2. 程序：epub -> normalized -> 逐字区间抽取
# ----------------------------------------------------------------------------

_SEC_RE = re.compile(r"^\s*§\s*(\d+)\s*$")


def epub_to_normalized(epub_path: str) -> str:
    from common.epub_recover import recover           # noqa: E402
    from plan_7_program_first import build_normalized   # noqa: E402
    raw = subprocess.run(
        ["pandoc", epub_path, "-f", "epub", "-t", "html", "-s", "-o", "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    augmented, _ = recover(epub_path, raw)
    t = BeautifulSoup(augmented, "html.parser").find("title")
    title = t.get_text(strip=True) if t else "Untitled"
    return build_normalized(augmented, {}, {"title": title, "language": "zh"})


def _inner_html(block) -> str:
    bb = BeautifulSoup(str(block), "html.parser").find(True)
    for a in bb.find_all("a", attrs={"data-role": "noteref"}):
        sup = BeautifulSoup("", "html.parser").new_tag("sup")
        sup["class"] = "note"
        sup.string = a.get_text(strip=True)
        a.replace_with(sup)
    return "".join(str(c) for c in bb.children)


def extract_range(norm_html: str, lo: int, hi: int):
    """返回 [(n, [para_text...]), ...]（§lo..§hi，逐字），按文档顺序、每号首次出现。"""
    soup = BeautifulSoup(norm_html, "html.parser")
    body = soup.select_one("#bodymatter") or soup
    blocks = body.find_all(["p", "h1", "h2", "h3", "h4", "blockquote"])
    out, seen = [], set()
    i = 0
    while i < len(blocks):
        m = _SEC_RE.match(blocks[i].get_text(" ", strip=True))
        if m:
            n = int(m.group(1))
            paras, j = [], i + 1
            while j < len(blocks) and not _SEC_RE.match(blocks[j].get_text(" ", strip=True)):
                paras.append(blocks[j])
                j += 1
            if lo <= n <= hi and n not in seen and paras:
                seen.add(n)
                out.append((n, [p.get_text("", strip=False) for p in paras]))
            i = j
        else:
            i += 1
    out.sort(key=lambda x: x[0])
    return out


# ----------------------------------------------------------------------------
# 3. AI：区间 -> 每节标注包 JSON
# ----------------------------------------------------------------------------

SYSTEM = """你是一位顶尖的哲学阅读引路人，帮**初次接触、觉得原著很难**的读者读维特根斯坦《哲学研究》这类书。
给你某区间每一节(§)的**逐字原文**（含"——"的段落已按破折号切成编号小段）。
你要为每一节产出**标注数据**（只产数据，绝不重写正文、不输出 HTML、不改一个字）。

每节输出一个对象，键为节号字符串，值字段：
- "is_connective": 这节是否只是过渡/铺垫（true 则只给简短 aside，不给动作/设问/确认）。
- "opener": 读这节前的一句引导。给观察指令、勾起好奇，**不要剧透结论**。
- "move_tag": 一句很短的"他这一步在做什么"（如"布饵→第一道裂缝"）。is_connective 时可为 null。
- "dialogue": 仅对"含编号小段"的段落。形如 {"段落序号": {"小段序号": "角色"}}，
              只列**开启新说话轮次**的小段。角色用："反方"/"设问"/"有人主张"=他要对付的声音；"维"=维特根斯坦本人的回应。narration 小段不要列。
- "setup": 读完这节后请读者自己先想一想的设问（一句，**不给答案**）。无则 null。
- "confirm": {"label":"按钮文字（以 ▽ 结尾）","text":"克制的确认，点破他这一步的招式"}。无则 null。
- "explain": **只在这节确实很难、值得一段把逻辑讲透时**给。{"label":"按钮文字（以 ▽ 结尾）","paras":["第一段","..."],"example":"一个书外的小例子让抽象点咔哒一下（务必忠实、不要瞎编）"}。否则 null。
- "aside": is_connective 时给一句轻量旁注；否则 null。

铁律：大白话、具体、有温度；不剧透留给设问/确认；explain 宁缺毋滥。
**严格输出一个 JSON 对象，不要 markdown 围栏、不要多余文字。**"""


def build_prompt(sections):
    lines = []
    for n, paras in sections:
        lines.append(f"===== §{n} =====")
        for pi, ptext in enumerate(paras):
            segs = ptext.split("——")
            if len(segs) >= 2:
                lines.append(f"段落[{pi}]（按破折号切成 {len(segs)} 小段）：")
                for si, s in enumerate(segs):
                    lines.append(f"  ({si}) {s.strip()}")
            else:
                lines.append(f"段落[{pi}]：{ptext.strip()}")
    return "下面是各节逐字原文，请产出每节的标注包 JSON。\n\n" + "\n".join(lines)


def ai_annotate(sections):
    if not os.environ.get("AI_API_KEY"):
        return None
    try:
        from ai_utils import call_ai  # noqa: E402
    except Exception as e:
        print(f"[warn] 无法加载 ai_utils: {e}", file=sys.stderr)
        return None
    try:
        # 推理模型(deepseek-v4-pro)会先烧大量 reasoning token，预算给足。
        resp, tokens = call_ai(build_prompt(sections), SYSTEM, max_tokens=32000)
    except Exception as e:
        print(f"[warn] AI 调用失败，退回 fallback：{e}", file=sys.stderr)
        return None
    data = _parse_json(resp)
    if data is None:
        print("[warn] AI 输出非合法 JSON，退回 fallback", file=sys.stderr)
        return None
    print(f"[ok] AI 标注 JSON（tokens={tokens}，{len(data)} 节）", file=sys.stderr)
    return data


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


# ----------------------------------------------------------------------------
# 4. 程序：渲染（逐字原文 + 标注层），并校验无损
# ----------------------------------------------------------------------------

_ROLECLS = {"反方": "q", "设问": "q", "有人主张": "q", "维": "a"}


def render_paragraph(ptext, dlg_for_para):
    """dlg_for_para: {seg_idx(int): role} 或 None。返回 (html, 该段 data-orig 拼回的文本)。"""
    segs = ptext.split("——")
    labels = {int(k): v for k, v in (dlg_for_para or {}).items()} if dlg_for_para else {}
    starts = sorted(set([0] + [i for i in labels if 0 <= i < len(segs)]))
    if len(starts) <= 1:  # 非对话：整段逐字
        esc = ptext  # 已是纯文本（区间抽取阶段取的 get_text）
        return f'<p class="orig" data-orig="1">{esc}</p>', ptext
    # 对话：按转折分行，所有"——"保留
    parts, recon = [], []
    bounds = starts + [len(segs)]
    for k, start in enumerate(starts):
        end = bounds[k + 1]
        role = labels.get(start, "")
        chunk = "——".join(segs[start:end])
        if role:
            parts.append(f'<span class="role {_ROLECLS.get(role, "a")}">{role}</span>')
        parts.append(f'<span data-orig="1">{chunk}</span>')
        recon.append(chunk)
        if end < len(segs):
            parts.append('<span data-orig="1">——</span>')
            recon.append("——")
        if k < len(starts) - 1:
            parts.append("<br>")
    return f'<p class="orig dialogue">{"".join(parts)}</p>', "——".join(recon).replace("——————", "——")


def render_section(n, paras, ann):
    ann = ann or {}
    conn = bool(ann.get("is_connective"))
    h = [f'<section class="{"remark connective" if conn else "remark"}" id="s{n}">',
         f'<span class="num">§{n}</span>']
    if ann.get("move_tag"):
        h.append(f'<span class="move">{ann["move_tag"]}</span>')
    if ann.get("opener"):
        h.append(f'<p class="opener">▸ {ann["opener"]}</p>')
    dlg = ann.get("dialogue") or {}
    recon_text = []
    for pi, ptext in enumerate(paras):
        php, rec = render_paragraph(ptext, dlg.get(str(pi)) or dlg.get(pi))
        h.append(php)
        recon_text.append(rec)
    ex = ann.get("explain")
    if ex and ex.get("paras"):
        body = "".join(f"<p>{x}</p>" for x in ex["paras"])
        if ex.get("example"):
            body += f'<p class="hex">{ex["example"]}</p>'
        h.append(f'<div class="help"><button>{ex.get("label", "讲明白 ▽")}</button>'
                 f'<div class="hbox">{body}</div></div>')
    if ann.get("setup"):
        h.append(f'<p class="setup">▷ {ann["setup"]}</p>')
    cf = ann.get("confirm")
    if cf and cf.get("text"):
        h.append(f'<div class="reveal"><button>{cf.get("label", "看他做了什么 ▽")}</button>'
                 f'<div class="confirm">{cf["text"]}</div></div>')
    if conn and ann.get("aside"):
        h.append(f'<p class="aside">{ann["aside"]}</p>')
    h.append("</section>")
    return "\n".join(h), "".join(recon_text)


def render_html(sections, ann_by_n, title):
    body, lossless = [], True
    for n, paras in sections:
        sec_html, recon = render_section(n, paras, ann_by_n.get(str(n)) or ann_by_n.get(n))
        body.append(sec_html)
        want = re.sub(r"\s+", "", "".join(paras))
        got = re.sub(r"\s+", "", recon)
        if want != got:
            lossless = False
            print(f"[ERR] §{n} 正文不无损：want {len(want)} got {len(got)}", file=sys.stderr)
    return _TEMPLATE.replace("{{TITLE}}", title).replace("{{BODY}}", "\n\n".join(body)), lossless


_TEMPLATE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{{TITLE}}</title><style>
:root{--bg:#f7f4ee;--card:#fffdf8;--ink:#23201b;--muted:#857c6f;--line:#e7e0d4;--accent:#9a5b34;--reveal:#f3ede0;--chip:#8a4f2c;--qbg:#efe9df}
@media(prefers-color-scheme:dark){:root{--bg:#15140f;--card:#1d1b15;--ink:#e9e4d8;--muted:#9c9384;--line:#332f26;--accent:#d79a6c;--reveal:#24211a;--chip:#d79a6c;--qbg:#262219}}
*{box-sizing:border-box}html,body{margin:0;padding:0;background:var(--bg);color:var(--ink)}
body{font-family:"Noto Serif CJK SC","Songti SC","Source Han Serif SC",Georgia,serif;font-size:18px;line-height:1.9;-webkit-text-size-adjust:100%;padding:env(safe-area-inset-top) 0 6rem}
.wrap{max-width:680px;margin:0 auto;padding:0 18px}
header{padding:30px 0 8px;text-align:center}header .t{font-size:1.5rem;font-weight:800}header .s{color:var(--muted);font-size:.85rem;margin-top:6px}
section.remark{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px 18px 18px;margin:20px 0}
.num{display:inline-block;font-weight:800;font-size:1.05rem;color:var(--accent);border-bottom:2px solid var(--accent);padding-bottom:1px;margin-bottom:10px}
.move{display:inline-block;margin:0 0 4px 10px;font-size:.72rem;font-weight:700;color:var(--card);background:var(--chip);padding:3px 9px;border-radius:999px;vertical-align:2px}
.opener{color:var(--accent);font-style:italic;font-size:.9rem;line-height:1.72;margin:4px 0 14px}
section.connective{background:transparent;border-style:dashed}
section.remark p{margin:.7em 0}
sup.note{color:var(--muted);font-size:.62em;vertical-align:super}
p.dialogue{line-height:2.05}
.role{display:inline-block;font-size:.66rem;font-weight:700;padding:1px 7px;border-radius:999px;margin:0 7px 0 0;vertical-align:2px}
.role.q{background:var(--qbg);color:var(--accent)}.role.a{background:transparent;color:var(--muted);border:1px solid var(--line)}
.setup{margin:16px 0 6px;font-style:italic;color:var(--accent);border-top:1px dashed var(--line);padding-top:14px;line-height:1.8}.setup b{font-style:normal}
.reveal,.help{margin-top:12px}
.reveal>button,.help>button{appearance:none;font-family:inherit;font-weight:700;font-size:.9rem;padding:9px 14px;border-radius:12px;cursor:pointer;width:100%;text-align:left;color:var(--accent)}
.reveal>button{border:1px solid var(--line);background:var(--reveal)}
.help>button{border:1px dashed var(--accent);background:transparent;font-size:.88rem}
.reveal .confirm,.help .hbox{display:none;margin-top:10px;padding:12px 15px;background:var(--reveal);border-radius:12px;font-size:.94rem;line-height:1.84}
.help .hbox{border-left:3px solid var(--accent);border-radius:0 10px 10px 0}
.reveal.open .confirm,.help.open .hbox{display:block}.reveal.open>button,.help.open>button{opacity:.55}
.help .hbox b,.reveal .confirm b,.setup b{color:var(--accent)}
.help .hbox .hex{background:var(--card);border:1px dashed var(--line);border-radius:8px;padding:9px 12px;margin:.7em 0}
.aside{color:var(--muted);font-size:.86rem;font-style:italic;margin-top:10px}
footer{color:var(--muted);font-size:.78rem;text-align:center;margin:30px 0 0;line-height:1.7}
</style></head><body><div class="wrap">
<header><div class="t">{{TITLE}}</div><div class="s">思辨阅读版 · 正文逐字取自规范化原文，标注为附加层</div></header>
{{BODY}}
<footer>正文经程序逐节校验：与规范化原文逐字一致。设问先自己想，▽ 点开再看。</footer>
</div><script>
document.querySelectorAll(".reveal>button,.help>button").forEach(function(x){x.addEventListener("click",function(){var p=x.parentElement;p.classList.toggle("open");x.textContent=p.classList.contains("open")?x.textContent.replace("▽","△"):x.textContent.replace("△","▽");});});
</script></body></html>"""


# ----------------------------------------------------------------------------
# fallback 标注（手工打磨的 §1–§10；AI 不可用时用）
# ----------------------------------------------------------------------------

FALLBACK = json.loads(r"""
{
 "1": {"is_connective": false, "opener": "把这幅“词＝物的名字”的图画看仔细——它越让你点头，这一组后面就越有戏。",
   "move_tag": "布饵 → 第一道裂缝",
   "dialogue": {"3": {"4":"反方","5":"维","6":"反方","7":"维"}},
   "setup": "看 §1 结尾——他没有回答“五的意义<b>是</b>什么”，他做了<b>别的</b>。他做了什么？",
   "confirm": {"label":"看他做了什么 ▽","text":"他把问题从“意义<b>是</b>什么”悄悄换成了“这个词怎么<b>被用</b>”。整本书的主题，在这一句里第一次、不声不响地出现了——这就是埋在奥古斯丁图画里的第一道裂缝。"},
   "explain": null, "aside": null},
 "2": {"is_connective": false, "opener": "他不急着反驳。读时想一想：让对手先赢一局，对他有什么好处？",
   "move_tag": "让步，而不是反驳", "dialogue": {},
   "setup": "他本可以直接说“奥古斯丁错了”。他偏不，反而给对手造了一个<b>完美合身的战场</b>。为什么？",
   "confirm": {"label":"看他的算盘 ▽","text":"因为他要让你<b>之后自己发现</b>——对的不是那幅图画，是图画<b>恰好合身的那一小块地方</b>。这一招贯穿全书。"},
   "explain": null, "aside": null},
 "3": {"is_connective": false, "opener": "他开始动手了。留意他不是说那幅图画“错”，而是给它“划地盘”。",
   "move_tag": "诊断：把局部当成了全体",
   "dialogue": {"1": {"0":"有人主张","1":"维"}},
   "setup": null,
   "confirm": {"label":"这条的诊断 ▽","text":"奥古斯丁的毛病<b>不是说了假话，是把一种特例过度推广</b>。这是维特根斯坦对几乎所有哲学错误的标准诊断。"},
   "explain": null, "aside": null},
 "4": {"is_connective": true, "opener": null, "move_tag": null, "dialogue": {},
   "setup": null, "confirm": null, "explain": null, "aside": "同一个诊断，换一个类比加固。不展开。"},
 "5": {"is_connective": true, "opener": null, "move_tag": null, "dialogue": {},
   "setup": null, "confirm": null, "explain": null,
   "aside": "末尾“训练，而非解释”是一颗远雷：要到后面讲“遵守规则”才引爆。先记下。"},
 "6": {"is_connective": false, "opener": "这节长，但只咬住一个念头：意义，是不是“心里浮现的那张图”？读到刹车杆那句停一下。",
   "move_tag": "拆“意义＝心里浮现的图画”",
   "dialogue": {"1": {"2":"设问","3":"维"}, "2": {"0":"设问","2":"维"}},
   "setup": "跟着刹车杆那句想——一根杆子<b>单独拿出来</b>，是刹车杆吗？那么一个词“在心里冒出的那张图”，单独拿出来，是它的意义吗？",
   "confirm": {"label":"看他在拆什么 ▽","text":"意义<b>不在</b>那张心里的图，而在这个词<b>在整套实践里扮演的角色</b>。脱了支架的杆子什么都不是。"},
   "explain": {"label":"卡住了？换个最具体的讲法 ▽",
     "paras":["一句话：<b>懂不懂一个词，跟你脑子里冒不冒出图无关；算数的是你拿它去干什么。</b>",
              "<b>第一步：</b>A 喊“石板！”，B 去搬石板。这词是用来<b>支使行动</b>的，不是在 B 脑里放图；图就算闪过也只是顺带的。",
              "<b>第二步：</b>谁算懂了“石板！”？是那个一听见就<b>把石板搬对</b>的人。懂没懂看行动，不看脑里有没有图。"],
     "example":"举个书外的：我指着一个<b>红球</b>说“红”。你可能学成“红＝这个球/这种颜色/圆的东西”——光这一指定不下来，是周围的训练定的。所以同样指石板说“石板”，配不同训练，理解可以完全不同。"},
   "aside": null},
 "7": {"is_connective": false, "opener": "留意一个词的登场——以及他在它的定义里，悄悄塞了什么进去。",
   "move_tag": "安装核心概念：语言游戏", "dialogue": {},
   "setup": null,
   "confirm": {"label":"这个词为什么重要 ▽","text":"全书最重要的词第一次出现。<b>注意定义里塞进了“行为”</b>——语言游戏不是“词”，是<b>词 + 用词的活动</b>拧成的整体。"},
   "explain": null, "aside": null},
 "8": {"is_connective": true, "opener": null, "move_tag": null, "dialogue": {},
   "setup": null, "confirm": null, "explain": null,
   "aside": "把游戏一点点做复杂，逼出：“d”“到那儿”“这个”根本不是“对象的名字”。先攒着。"},
 "9": {"is_connective": true, "opener": null, "move_tag": null, "dialogue": {},
   "setup": null, "confirm": null, "explain": null,
   "aside": "看这些新词怎么“教会”；注意“指”这个动作出现在哪。"},
 "10": {"is_connective": false, "opener": "开篇的收口。读完把它和 §1 的结尾叠在一起——他绕了一整圈，落回哪了？",
   "move_tag": "卸掉“词标示对象” · 开篇收束",
   "dialogue": {"0": {"0":"设问","1":"维"}},
   "setup": "他又一次问“这些词标示什么”。把这一节结尾和 <b>§1 结尾</b>那句叠在一起看——绕了一圈，他落在哪？",
   "confirm": {"label":"开篇十节，收在这里 ▽","text":"“标示／代表”这套说法<b>几乎不干活</b>，唯一用处是消除某个具体误会。真正的内容是<b>用法</b>——而用法哪怕描述长得一样，本身也可以完全不同。"},
   "explain": {"label":"卡住了？换个最具体的讲法 ▽",
     "paras":["一句话：<b>说“某词标示某物”几乎不带信息——真内容永远是“怎么用”；它只在纠错时才有用。</b>",
              "拿 §8 的词问“它标示什么”：石板＝那石料、a＝一个数、这个＝被指物——没超出已知的用法。",
              "最后那句最要紧：三句答案“格式一样、活儿完全不同”（命令／数数／配手指）。“标示”还会拿统一外表盖住真正的差别。接回 §1：词，不是物的名字。"],
     "example":"想个书外的：“红灯标示停”——这句只提醒你红灯的用法，没揭示什么神秘连接。要是有人以为红灯能走，它才有用；否则什么没加。"},
   "aside": null}
}
""")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------


def main(argv):
    ap = argparse.ArgumentParser(description="单脚本：生成思辨版阅读器（模拟正式管线）")
    ap.add_argument("epub", nargs="?")
    ap.add_argument("-o", "--out", default="思辨版.html")
    ap.add_argument("--normalized")
    ap.add_argument("--range", default="1-10", help="如 1-10")
    ap.add_argument("--no-ai", action="store_true")
    args = ap.parse_args(argv[1:])
    lo, hi = (int(x) for x in args.range.split("-"))

    if args.normalized:
        norm = open(args.normalized, encoding="utf-8").read()
        print(f"[1/4] 读入 normalized: {args.normalized}", file=sys.stderr)
    elif args.epub:
        print(f"[1/4] Step1+Step2: {args.epub} -> normalized …", file=sys.stderr)
        norm = epub_to_normalized(args.epub)
    else:
        ap.error("需要 EPUB 或 --normalized")

    sections = extract_range(norm, lo, hi)
    title = (BeautifulSoup(norm, "html.parser").find("title") or {})
    title = title.get_text(strip=True) if hasattr(title, "get_text") else "阅读器"
    print(f"[2/4] 抽出 §{lo}–§{hi}：{len(sections)} 节", file=sys.stderr)

    ann = None if args.no_ai else ai_annotate(sections)
    source = "ai"
    if ann is None:
        ann, source = FALLBACK, "fallback"
    print(f"[3/4] 标注来源：{source}", file=sys.stderr)

    html_out, lossless = render_html(sections, ann, title)
    if not lossless:
        print("[FATAL] 正文未通过无损校验，拒绝输出。", file=sys.stderr)
        return 1
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"[4/4] 无损校验通过；写出 {len(html_out)} 字节 -> {args.out}（{source}）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
