# EngReadHelper

面向墨水屏（电子书）设计的学生英文阅读辅助工具。点单词秒出释义、轻触或横划句子即可获得 AI 译文与句法解析，生词一键加入生词本。

## 特性

- **墨水屏优先**：服务端预渲染整页，翻页用纯链接（无 JS 也能读）；全站唯一 JS 只重绘底部解释栏，不碰正文 DOM
- **点词即查**：ECDICT 离线词典（340 万词条）毫秒级返回，含音标 / 词性 / 中英释义 / 考级标签 / 词形变化，支持词形还原（running → run）
- **句中意**：单词释义之外，可让 AI 根据语境解释该词在本句中的具体含义
- **句子解析**：轻触或横划句子 → AI 译文 + 语法要点 + 重点词，结果按句子哈希缓存进 SQLite，同一句终身只调一次
- **跨页句子衔接**：分页优先在句子边界切页，句子永不跨页（超长句自动降级单词切分）
- **生词下划线**：已加入生词本的词在正文中以下划线标出，一眼可见
- **AI 书籍讨论**：按章节切分全书，就任一章节目录提问，AI 结合章节原文回答；回复自动分页（600 字/页），跨页带 `(下页续)/(续)` 标记，提交后等待页自动刷新跳转
- **AI 共读（水循环记忆）**：AI 作为阅读进度与用户同步的共读伙伴——只记得已读内容（天然无剧透），读过的内容按"场景"蒸馏记忆（情节摘要 + verbatim 金句），旧的沉淀入云、提问时回想召回，能自然说出"写到某某情节时，某某说过'……'"，引用逐字校验防止幻觉
- **生词本**：点词即加，独立页面管理
- **字号切换**：切换后自动重新分页并保留阅读进度
- **书源格式**：TXT / EPUB / 文本型 PDF（自动去重重复文字层、重建段落）
- **LLM 双后端**：`openai` 库统一访问本地 ollama 或任意 OpenAI 兼容 API（DeepSeek / Kimi / GLM…），启动时自动探测

## 快速开始

需要 Python 3.10+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1. 离线词典（必装）

下载 [ECDICT](https://github.com/skywind3000/ECDICT) 的 SQLite 版本（约 800MB，含 340 万词条）：

```bash
mkdir -p data
# 下载 https://github.com/skywind3000/ECDICT/releases/download/1.0.28/ecdict-sqlite-28.zip
# 解压得到 stardict.db
mv stardict.db data/ecdict.db
```

> 可选优化：`stardict.db` 约 800MB，其中 `detail`/`audio` 两列体积很大。只保留查询需要的列可裁剪到约 437MB：
> ```python
> import sqlite3
> src = sqlite3.connect('data/ecdict.db')
> dst = sqlite3.connect('data/ecdict.pruned.db')
> dst.execute('''CREATE TABLE stardict (
>     word TEXT COLLATE NOCASE UNIQUE, sw TEXT, phonetic TEXT, definition TEXT,
>     translation TEXT, pos TEXT, collins INT, oxford INT, tag TEXT,
>     bnc INT, frq INT, exchange TEXT)''')
> cur = src.execute("SELECT word,sw,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange FROM stardict")
> while True:
>     rows = cur.fetchmany(200000)
>     if not rows: break
>     dst.executemany("INSERT OR IGNORE INTO stardict VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
> dst.commit(); dst.execute("VACUUM"); dst.commit()
> ```

### 2. 配置 LLM

```bash
cp config.example.json config.json
```

编辑 `config.json`：

| 字段 | 说明 |
|---|---|
| `llm.mode` | `auto`（本地 ollama 优先，失败自动切 API）/ `ollama` / `api` |
| `llm.ollama` | 本地 ollama 地址与模型（如 `gemma3:4b`） |
| `llm.api` | OpenAI 兼容 API 的 `base_url` / `api_key` / `model` |
| `page.chars` | 每页字符数（字号变化时按比例自动调整） |
| `co_read.hot_window` | 共读热窗口字符数（阅读位置附近保留原文） |
| `co_read.distill_every` | 每读 N 页触发一次后台记忆蒸馏 |
| `co_read.distill_batch` | 每次蒸馏的场景数上限 |
| `co_read.scene_chars` | 场景切分的字符粒度 |
| `co_read.recall_limit` | 一次回想最多召回的场景数 |

`config.json` 含密钥，已被 git 忽略，不会入库。

### 3. 启动

```bash
python app.py
```

浏览器打开 <http://127.0.0.1:5001>（5000 端口常被 macOS AirPlay 占用，故默认 5001）。

**墨水屏 / 手机**：与电脑同一局域网，访问 `http://<电脑IP>:5001`（如 `http://192.168.1.10:5001`）。

## 触屏操作

| 手势 | 功能 |
|---|---|
| 轻触单词 | 查词典（音标 / 释义 / 变形 / 考级） |
| 轻触句子空白处 | AI 句子解析 |
| **横向划过句子** | AI 句子解析（墨水屏友好手势） |
| 解释栏按钮 | AI 句中意 / 加入生词本 / 移出生词本 |
| 底部按钮 | 上一页 / 下一页（纵向滑动为正常滚动） |
| 顶栏字号 | 14–28，切换后重新分页并保留页码 |

## 项目结构

```
app.py                 Flask 路由与配置加载（讨论路由、共读记忆、蒸馏调度）
reader.py              分页（句子边界优先）/ 分词分句 / 章节与场景切分 / 服务端渲染
dictdb.py              ECDICT 查询 + 词形还原
llm.py                 ollama / OpenAI 兼容 API 统一调用 + 容错 JSON 解析 + 工具调用循环 + 蒸馏 prompt
extract.py             TXT / EPUB / PDF 文本提取（坐标去重、段落重建）
store.py               SQLite：books / cache / vocab / progress / convs / scenes
config.example.json    配置模板（复制为 config.json 使用）
static/reader.js       全站唯一 JS（原生 ES5，兼容老 WebView）
templates/             书架 / 阅读页 / 生词本 / 设置 / 共读 / 会话分页 / 等待页
data/                   运行时数据（git 忽略）
```

## 墨水屏适配设计

- **翻页零 JS**：上一页 / 下一页就是 `<a>` 链接，服务端整页重渲，契合墨水屏整页刷新特性
- **单点局部刷新**：解释栏固定在页面底部，查词 / 查句只重绘这一块，正文 DOM 零改动，利于墨水屏 partial refresh
- **极省资源**：无前端框架；词典毫秒级查询且离线可用；AI 结果全量缓存
- **兼容老浏览器**：JS 不用 `closest` / `classList` 等新 API，老 Android WebView 可运行

## 已知限制

- AI 共读的记忆机制是实验性的：场景检索用双字窗口评分（中英文免分词，场景库每本书几百条足够快），未使用向量/FTS5；召回质量取决于蒸馏金句的准确性（verbatim 校验已保证逐字，但可能漏选金句）
- 共读伙伴只记得"热窗口原文 + 已蒸馏场景"，读到一半的章节里尚未蒸馏的部分可能回忆不全；点击「立即记住已读内容」可手动补齐
- 章节识别基于常见标题行（`Chapter N` / `第N章` 等），无标题结构的书全书视为一章
- PDF 仅支持文本型（扫描版需先 OCR）
- 内置 Flask 开发服务器适合单机 / 局域网个人使用；多用户部署建议加认证并改用生产级 WSGI
- 依赖 LLM：本地小模型（如 gemma3:4b）速度慢、输出格式偶尔不规范（已有容错解析兜底），在线 API 体验最佳
