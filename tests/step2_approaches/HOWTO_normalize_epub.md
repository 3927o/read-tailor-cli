# 如何用 plan7 normalize 一本 EPUB（两种前端方法）

本文是操作手册，记录把任意 EPUB 规范化成统一结构 HTML 的两条可行路线。
两条路线**共用同一个后端 plan7**（`plan_7_program_first.py`），区别只在**前端**
——怎么把 EPUB 变成喂给 plan7 的那份 `raw.html`。

- **方法 A：pandoc 前端**（实验验证过；当前中间文件会内嵌图片）
- **方法 B：direct 前端**（不经 pandoc、结构更忠实；当前中间文件也会内嵌图片）

> **注意：本文是 plan7 spike 的操作记录，不是现行产品规范。** 这些实验命令仍会在中间
> HTML 中生成 Base64 data URI；正式产物必须把媒体拆到 `assets/`，HTML 使用
> `assets/...` 相对路径，并通过包级资源校验。相关代码完成前，本流程的输出不能直接进入
> 产品 ready 状态。现行规范以 [`../../docs/normalized_book_spec.md`](../../docs/normalized_book_spec.md)
> 为准。

> 背景与设计动机见 [`experiment_log.md`](experiment_log.md)；本文只讲“怎么做”。

---

## 0. plan7 是什么（一句话）

> 程序做所有机械、可枚举的事（骨架、标题树嵌套、注释配对）；
> AI 只回答它唯一无法机械判断的语义问题：**每个标题在书名下的逻辑层级（level）**。

AI 的输出只有 `{i, level}` / `{i, skip}` / `{i, merge}`，**从不写 CSS、代码或内容，
也不输出“篇/章/节”这类词**。程序拿到 level 序列后用栈算法翻译成 `h1>h2>h3` 真实嵌套。

---

## 1. 前置条件

| 依赖 | 检查命令 | 说明 |
|---|---|---|
| pandoc | `pandoc --version` | 仅方法 A 需要。本项目生产版本 **3.9** |
| Python 3 + bs4 | `python3 -c "import bs4; print(bs4.__version__)"` | 两种方法都需要 |
| AI 凭证 | 见下 | plan7 **必须**调 AI，缺凭证直接报错 |

### 1.1 AI 凭证（必填）

plan7 通过 OpenAI 兼容协议调用，读三个环境变量。**只用环境变量临时传入，不要写进
任何仓库文件**：

```bash
export AI_BASE_URL="https://api.deepseek.com"   # 兼容 OpenAI 的 /chat/completions
export AI_API_KEY="sk-..."                       # 你的 key
export AI_MODEL="deepseek-v4-pro"                # 实测可用；mimo-v2.5-pro 也行
```

> 模型要求：能稳定吐 JSON 即可。推理模型（如 mimo）要注意 `max_tokens` 别被推理吃光
> ——`ai_utils.py` 已默认 `max_tokens=32000` 并带 `"thinking":{"type":"disabled"}`。

### 1.2 工作目录约定

所有命令都在 **`tests/step2_approaches/`** 下执行（脚本以 `common/` 为包导入）：

```bash
cd <repo>/tests/step2_approaches
```

`config.py` 用 `BOOK_NAME` 环境变量推导路径：

- raw 输入：`dist/$BOOK_NAME/work/$BOOK_NAME.raw.html`
- 输出目录：`outputs_$BOOK_NAME/`（书名为“查拉图斯特拉如是说”时无后缀，为历史默认）
- 结果文件：`results_$BOOK_NAME.json`

---

## 2. 方法 A：pandoc 前端（推荐默认，尤其图多的书）

四步：**pandoc → (按需) epub_recover → plan7 → 看结果**。

### 步骤 A1：pandoc 转 raw.html

参数与 Rust 管道 `step1_convert_epub` 完全一致。这里的 `-s --embed-resources` 会把图片
以 Base64 放进**中间文件**；后续产品打包步骤必须将其提取到 `assets/` 并改写引用：

```bash
export BOOK_NAME="你的书名"
EPUB="/绝对路径/你的书.epub"
WORK="../../dist/$BOOK_NAME/work"
mkdir -p "$WORK"
pandoc "$EPUB" -f epub -t html -s --embed-resources -o "$WORK/$BOOK_NAME.raw.html"
```

### 步骤 A2：（按需）epub_recover —— 补 pandoc 丢掉的东西

pandoc 的 EPUB reader 有已知信息损失，`epub_recover.py` 重新打开 EPUB 把它们捞回来。
它跑三个恢复器：**文件标题注入 / 跨文件注释体恢复 / id 修复**，原地改写并自动留 `.bak`：

```bash
python3 common/epub_recover.py "$EPUB" "../../dist/$BOOK_NAME/work/$BOOK_NAME.raw.html"
```

**什么时候需要它：** 典型是“跨文件 + `epub:type=rearnote`”型注释的书
（如《查拉图斯特拉如是说》，pandoc 会把全部 1377 条注释体丢光，只剩悬空 `[N]`）。
跑完应看到 `[notes] injected N (of N dangling refs)`、`aside` 数恢复。

**⚠️ 重要陷阱——标题注入可能帮倒忙：** 如果 EPUB 各文件的 `<title>` 是占位垃圾
（例如《穷查理宝典》里全是 `未知`），标题注入会塞进一堆 `<h1>未知</h1>` 噪声章节。
判断方法：看 recover 输出里 `[titles] injected` 后面跟的标题是不是有意义。
**若是垃圾，回滚 pristine pandoc 输出、跳过 recover：**

```bash
cp "../../dist/$BOOK_NAME/work/$BOOK_NAME.raw.html.bak" \
   "../../dist/$BOOK_NAME/work/$BOOK_NAME.raw.html"
```

**判断要不要 recover 的快速探针**（跑在 raw.html 上）：

```bash
python3 - "../../dist/$BOOK_NAME/work/$BOOK_NAME.raw.html" <<'PY'
import sys; from bs4 import BeautifulSoup
b = BeautifulSoup(open(sys.argv[1]).read(),"html.parser").find("body")
ids = {e.get("id") for e in b.find_all(id=True)}
dangling = sum(1 for a in b.find_all("a",href=True)
               if a["href"].startswith("#") and a["href"][1:] not in ids)
print("asides:", len(b.find_all("aside")), " dangling # refs:", dangling)
PY
```
- `asides=0` 且 `dangling` 很大 → 大概率是注释体被丢，**该 recover**。
- `dangling` 是个位数/几十 → 多半是源 EPUB 自带缺陷，recover 也救不回，可跳过。

### 步骤 A3：跑 plan7（含评估）

```bash
BOOK_NAME="$BOOK_NAME" \
AI_BASE_URL="$AI_BASE_URL" AI_API_KEY="$AI_API_KEY" AI_MODEL="$AI_MODEL" \
python3 runner.py --plan 7
```

产出在 `outputs_$BOOK_NAME/`：
- `plan_7.normalized.html` ← **成品**
- `plan_7.structure.json` ← 结构摘要
- `plan_7.ai.prompt.txt` / `plan_7.ai.response.txt` ← AI 调用 trace（只问 level）

`runner.py` 会顺带打印评估指标对比表（见 §4）。

---

## 3. 方法 B：direct 前端（不经 pandoc）

`common/epub_direct.py` 直接读 OPF spine，逐文件拼接，**给每个 id 命名空间化、
统一改写所有内部 href、按需注入文件标题、注释 `<aside>` 原样保留**——所以
**完全不需要 epub_recover**（没有任何东西被丢）。

### 步骤 B1：EPUB → direct.html

```bash
export BOOK_NAME="你的书名"
EPUB="/绝对路径/你的书.epub"
DIRECT="../../dist/$BOOK_NAME/work/$BOOK_NAME.direct.html"
python3 common/epub_direct.py "$EPUB" "$DIRECT"
```

### 步骤 B2：在 direct.html 上跑 plan7

`runner.py` 默认读 `*.raw.html`，所以 direct 路线用一个小驱动直接调
`plan_7_program_first.run()`，输出单独命名避免覆盖 pandoc 那次：

```bash
BOOK_NAME="$BOOK_NAME" \
AI_BASE_URL="$AI_BASE_URL" AI_API_KEY="$AI_API_KEY" AI_MODEL="$AI_MODEL" \
python3 - <<'PY'
import os, sys, time
STEP2 = os.path.abspath(".")
sys.path.insert(0, STEP2)
import config                       # reuse the same path convention as runner.py
import plan_7_program_first as plan7
from evaluator import evaluate, evaluate_structure_json, EvalResult

BOOK   = config.BOOK_NAME
DIRECT = os.path.join(os.path.dirname(config.RAW_HTML), f"{BOOK}.direct.html")
# 写到与方法 A 相同的输出目录（config.TEST_OUTPUT_DIR 在 import 时已 makedirs）：
OUT_H  = os.path.join(config.TEST_OUTPUT_DIR, "plan_7_direct.normalized.html")
OUT_S  = os.path.join(config.TEST_OUTPUT_DIR, "plan_7_direct.structure.json")

t = time.time()
tokens = plan7.run(DIRECT, OUT_H, OUT_S)
print(f"plan7(direct) done in {time.time()-t:.1f}s tokens={tokens}")

res = EvalResult(approach="7-direct")
ev = evaluate(OUT_H, res.approach, raw_html_path=DIRECT)   # 召回/坏链以 direct.html 为基准
res.errors += ev.errors; res.warnings += ev.warnings; res.metrics.update(ev.metrics)
evaluate_structure_json(OUT_S, res)
print(res.summary())
PY
```

成品：`outputs_$BOOK_NAME/plan_7_direct.normalized.html`。

### ⚠️ direct 前端目前的局限（spike，未生产化）

- 目前仍把图片内嵌到中间 HTML，尚未直接产出产品要求的 `assets/` 书籍包。
- **未做 EPUB2 / NCX 老式目录** 的鲁棒处理。
- **不带 CSS**。
- 会暴露一个 plan7 已知 bug：重建标题树时**丢标题 id**，导致 EPUB 自带的目录/标题
  导航链断裂（评估里体现为“坏链”）。注意这不是 direct 的锅——pandoc 路线“0 坏链”
  是靠上游把这些链接删光换来的。修 `open_section` 透传 `id` 即可让 direct 严格更优。

---

## 4. 看懂评估指标

`runner.py` / 驱动会打印这些（来自 `evaluator.py`）：

| 指标 | 含义 | 怎么看 |
|---|---|---|
| `char_recall` | 输出可见字符 / raw 可见字符 | **最可信的质量指标**，应 ≥99.9% |
| `chapter_count` | 顶层 h1 数 | 对照你对这本书的预期目录 |
| `noteref_count` / `note_count` | 配对的引用 / 注释体数 | 两者应相等 |
| `noteref_to_note_orphan_count` | 孤儿引用 | 应为 0 |
| `heading_jump_count` | 层级跳跃（h2→h4） | 应为 0 |
| `broken_anchor_count` | 指向不存在 id 的 `#` 链接 | 见下方说明 |
| `dead_filelink_count` | 相对路径整文件死链 | 见下方说明 |

**别被“完美指标”骗了**（详见 experiment_log §22）：
- `orphan=0`、`heading_jump=0`、`unknown=0` 部分是**构造出来的恒等式**（未配对的 `<a>`
  根本不算 noteref；栈重建天然不可能跳级）。**真正的内容保真看 `char_recall`。**
- **坏链要分清来源**：很多时候 raw 里**本来就有**这些悬空引用（pandoc 丢了目标 id，
  或源 EPUB 自带缺陷）。判断方法——在 plan7 之前就用 §2 A2 的探针数一遍 raw 的
  `dangling # refs`；如果数目和最终 `broken_anchor_count` 一致，说明 plan7 一条没新增，
  是上游/源头的问题，**如实暴露即可，不要粉饰**。

---

## 5. 两种方法怎么选

| 场景 | 选 | 原因 |
|---|---|---|
| 图文并茂、图片重要 | **方法 A（pandoc）** | EPUB 兼容性验证更多；但仍需后续拆分为 `assets/` |
| 跨文件 rearnote 注释的书 | **方法 A + epub_recover** | recover 专治这个 pandoc bug |
| EPUB 文件 `<title>` 是垃圾（“未知”等） | 方法 A，但**跳过 recover 的标题注入**（回滚 .bak） | 避免注入噪声章节 |
| 纯文本、想要最忠实的链接/目录 | **方法 B（direct）** | 零恢复代码，目录/标题链全保留 |
| 脏 EPUB / EPUB2-NCX | 方法 A | direct 的鲁棒性还没验证 |

经验法则：**拿不准就先用方法 A**（更稳、能处理图片）；想做链接/目录最忠实的实验再上
方法 B。

---

## 6. 完整示例（本仓库实跑记录）

### 《查拉图斯特拉如是说》——方法 A，需要 recover

```bash
cd tests/step2_approaches
export BOOK_NAME="查拉图斯特拉如是说"
export AI_BASE_URL="https://api.deepseek.com" AI_API_KEY="sk-..." AI_MODEL="deepseek-v4-pro"
EPUB="../../查拉图斯特拉如是说.epub"
W="../../dist/$BOOK_NAME/work"; mkdir -p "$W"
pandoc "$EPUB" -f epub -t html -s --embed-resources -o "$W/$BOOK_NAME.raw.html"
python3 common/epub_recover.py "$EPUB" "$W/$BOOK_NAME.raw.html"   # 恢复 1377 条注释
python3 runner.py --plan 7
# → 5 章 / 注释 1377↔1377 / 0 孤儿 / 0 坏链 / char_recall 100%
```

### 《穷查理宝典》——方法 A，跳过 recover（标题是“未知”垃圾）

```bash
export BOOK_NAME="穷查理宝典"
EPUB="$(ls ~/Downloads/穷查理宝典*.epub | head -1)"
W="../../dist/$BOOK_NAME/work"; mkdir -p "$W"
pandoc "$EPUB" -f epub -t html -s --embed-resources -o "$W/$BOOK_NAME.raw.html"
# recover 会注入 101 个 <h1>未知</h1> → 回滚，不用 recover：
python3 common/epub_recover.py "$EPUB" "$W/$BOOK_NAME.raw.html"
cp "$W/$BOOK_NAME.raw.html.bak" "$W/$BOOK_NAME.raw.html"
python3 runner.py --plan 7
# → 15 章 / 338 节 / 543 图内嵌 / char_recall 99.97% / 78 坏链(全是源/ pandoc 自带，非新增)
```

---

## 7. 看成品

normalized.html 是自包含单文件，直接用浏览器开：

```bash
open "outputs_$BOOK_NAME/plan_7.normalized.html"        # macOS 默认浏览器
# 或浏览器地址栏粘贴 file:///绝对路径/plan_7.normalized.html
```

图多的书文件可能很大（《穷查理宝典》65MB），首次加载等几秒。

---

## 8. 常见问题速查

| 现象 | 原因 / 处理 |
|---|---|
| `Missing required env vars for AI calls` | 没设 `AI_BASE_URL/AI_API_KEY/AI_MODEL` |
| AI 返回空、`finish_reason=length` | 推理模型把 token 吃光；用非推理模型或调大 `max_tokens` |
| 输出一堆 `未知` / 占位章节 | recover 标题注入帮倒忙；回滚 `.bak` 跳过 recover |
| `broken_anchor_count` 很大 | 先用探针看 raw 的 dangling 数；多半是上游/源头缺陷，非 plan7 引入 |
| direct 路线丢图 | 已知局限；图重的书改用方法 A |
| 章节数和预期差 1~2 | AI 对扉页/目录这类 level 的抽样噪声；temperature 已 0.1，可进一步设 0 复跑 |
