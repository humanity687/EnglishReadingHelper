"""EngReadHelper：面向墨水屏的英语阅读辅助服务。"""
import hashlib
import json
import os
import re
import threading
from uuid import uuid4

from flask import (Flask, abort, jsonify, redirect, render_template,
                   request, url_for)
from werkzeug.utils import secure_filename

import dictdb
import extract
import llm
import reader
import store

BASE = os.path.dirname(os.path.abspath(__file__))


def load_cfg():
    with open(os.path.join(BASE, "config.json"), encoding="utf-8") as f:
        return json.load(f)


CFG = load_cfg()
DATA = os.path.join(BASE, CFG.get("data_dir", "data"))
PAGE_CHARS = int(CFG.get("page", {}).get("chars", 1100))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

store.init(DATA)
llm.init(CFG.get("llm", {}))
dictdb.init(os.path.join(BASE, CFG.get("dict_db", "data/ecdict.db")))

_pages_cache = {}
_TEXT_CACHE = {}
_CHAPTERS_CACHE = {}
_SCENES_CACHE = {}
_DISC_TASKS = {}
_DISTILL_STATE = {}
_PAGES_MAX = 32
_TEXT_MAX = 3
_FS_MIN, _FS_MAX = 14, 30
_DISC_CPP = 600
_CO_READ = {
    "hot_window": 4000,
    "distill_every": 5,
    "distill_batch": 6,
    "scene_chars": 700,
    "recall_limit": 8,
}
_CO_READ.update(CFG.get("co_read", {}) or {})


def _data():
    j = request.get_json(silent=True)
    return j if isinstance(j, dict) else dict(request.form)


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def _key(s):
    return hashlib.sha256(_norm(s).lower().encode("utf-8")).hexdigest()


def _book_text(book):
    path = book["text_path"]
    text = _TEXT_CACHE.get(path)
    if text is None:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        _TEXT_CACHE[path] = text
        if len(_TEXT_CACHE) > _TEXT_MAX:
            _TEXT_CACHE.pop(next(iter(_TEXT_CACHE)))
    return text


def _pages_for(book_id, fs):
    cpp = max(200, int(PAGE_CHARS * (18.0 / fs) ** 2))
    key = (book_id, cpp)
    pages = _pages_cache.get(key)
    if pages is None:
        book = store.get_book(book_id)
        if book is None:
            abort(404)
        pages = reader.paginate(_book_text(book), cpp)
        _pages_cache[key] = pages
        if len(_pages_cache) > _PAGES_MAX:
            _pages_cache.pop(next(iter(_pages_cache)))
    return pages


@app.get("/")
def index():
    return render_template(
        "books.html",
        books=store.list_books(),
        llm_status=llm.status(),
        dict_words=dictdb.count(),
    )


@app.post("/upload")
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        abort(400, "未选择文件")
    name = secure_filename(f.filename)
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".txt", ".epub", ".pdf"):
        abort(400, "仅支持 .txt / .epub / .pdf")
    tmp = os.path.join(DATA, "tmp", str(uuid4()) + ext)
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    f.save(tmp)
    try:
        text, title, author = extract.extract(tmp)
    except Exception as e:
        os.remove(tmp)
        abort(400, "解析失败：" + str(e))
    os.remove(tmp)
    if not text.strip():
        abort(400, "未提取到文本内容")
    title = title or os.path.splitext(name)[0]
    bid = store.add_book(title, author)
    path = os.path.join(DATA, "books", str(bid) + ".txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    store.set_book_path(bid, path)
    return redirect(url_for("read", bid=bid, pg=1))


def _anchor_of(text, n=40):
    """取页面开头 n 字符作为定位锚点（空白归一化，单词边界截断）。"""
    t = re.sub(r"\s+", " ", text).strip()
    if len(t) <= n:
        return t
    cut = t.rfind(" ", 0, n + 1)
    return t[:cut if cut > n // 2 else n]


def _page_of_anchor(pages, anchor):
    """在分页中查找包含锚点（或其前缀）的页，返回 1 基页码或 None。"""
    if not anchor:
        return None
    cands = [anchor]
    for k in (32, 24, 16, 12):
        if len(anchor) > k:
            cands.append(anchor[:k])
    m = None
    for mm in re.finditer(r"[.!?\u2026]", anchor):
        m = mm
    if m:
        cands.append(anchor[:m.end()])
        cands.append(anchor[:m.start() + 1])
    for c in cands:
        for i, p in enumerate(pages):
            if c in p:
                return i + 1
    return None


def _page_of_offset(pages, offset):
    acc = 0
    for i, p in enumerate(pages):
        if acc <= offset < acc + len(p):
            return i + 1
        acc += len(p)
    return len(pages)


@app.get("/read/<int:bid>/<int:pg>")
def read(bid, pg):
    book = store.get_book(bid)
    if book is None:
        abort(404)
    fs = max(_FS_MIN, min(_FS_MAX, int(request.args.get("fs", 18))))
    pages = _pages_for(bid, fs)
    if not pages:
        abort(404, "空书")

    anchor = request.args.get("anchor")
    if anchor:
        target = _page_of_anchor(pages, anchor)
        if target is None:
            ofs = request.args.get("ofs")
            try:
                old_pages = _pages_for(bid, max(_FS_MIN, min(_FS_MAX, int(ofs))))
                offset = sum(len(p) for p in old_pages[:max(pg - 1, 0)])
                target = _page_of_offset(pages, offset)
            except (TypeError, ValueError):
                target = None
        if target is not None and target != pg:
            return redirect(url_for("read", bid=bid, pg=target, fs=fs))

    pg = max(1, min(pg, len(pages)))
    saved = {w.lower() for w in store.vocab_words(bid)}
    return render_template(
        "read.html",
        book=book,
        pg=pg,
        total=len(pages),
        fs=fs,
        prev=pg - 1 if pg > 1 else None,
        nxt=pg + 1 if pg < len(pages) else None,
        anchor=_anchor_of(pages[pg - 1]),
        html=reader.render_page(pages[pg - 1], saved),
    )


@app.post("/api/progress")
def api_progress():
    d = _data()
    try:
        bid = int(d.get("book"))
        page = int(d.get("page"))
        fs = int(d.get("fs") or 0) or None
    except (TypeError, ValueError):
        return jsonify({"error": "bad"}), 400
    store.set_progress(bid, page, fs)
    if fs:
        position = _pos_of_page(bid, page, fs)
        if (page % _CO_READ["distill_every"] == 0
                or _undistilled_count(bid, position) >= 4):
            threading.Thread(
                target=_distill_eligible, args=(bid, position), daemon=True
            ).start()
    return jsonify({"ok": True})


@app.post("/api/word")
def api_word():
    d = _data()
    w = _norm(d.get("w"))
    s = _norm(d.get("s"))
    if not w:
        return jsonify({"error": "empty"}), 400
    r = dictdb.lookup(w)
    insent = None
    if s:
        raw_ins = store.get_cache("wi", _key(w + "\x00" + s))
        if raw_ins is not None:
            p = llm.parse_word(raw_ins)
            insent = p.get("meaning") or (p.get("raw") or "")[:300]
    if r is None:
        return jsonify({"found": False, "word": w, "insent": insent})
    r["found"] = True
    r["insent"] = insent
    try:
        bid = int(d.get("book"))
    except (TypeError, ValueError):
        bid = None
    r["saved"] = bool(bid and store.is_saved(bid, r["word"]))
    return jsonify(r)


@app.post("/api/sentence")
def api_sentence():
    d = _data()
    s = _norm(d.get("s"))
    if not s:
        return jsonify({"error": "empty"}), 400
    try:
        bid = int(d.get("book") or 0)
        page = int(d.get("page") or 0)
        fs = int(d.get("fs") or 18)
    except (TypeError, ValueError):
        bid, page, fs = 0, 0, 18

    s = _canonical_sentence(bid, page, fs, s)

    ctx = ""
    if bid:
        book = store.get_book(bid)
        if book is not None:
            ctx = "这句话来自《%s》" % book["title"]
            chap = _chapter_of_pos(bid, _pos_of_page(bid, page, fs))
            if chap:
                ctx += "（章节：%s）" % chap
            ctx += "。"

    if not llm.ready():
        return jsonify({"error": "AI 未配置：请编辑 config.json 后到 /config 重新加载", "configured": False}), 503
    key = _key(("%d\x00" % bid) + s)
    raw = store.get_cache("sent", key)
    if raw is None:
        try:
            raw = llm.sentence(s, ctx)
        except Exception as e:
            return jsonify({"error": "AI 调用失败：" + str(e)}), 502
        store.put_cache("sent", key, raw)
    return jsonify({"s": s, **llm.parse_sent(raw)})


@app.post("/api/insent")
def api_insent():
    d = _data()
    w = _norm(d.get("w"))
    s = _norm(d.get("s"))
    if not w or not s:
        return jsonify({"error": "empty"}), 400
    if not llm.ready():
        return jsonify({"error": "AI 未配置：请编辑 config.json 后到 /config 重新加载", "configured": False}), 503
    key = _key(w + "\x00" + s)
    raw = store.get_cache("wi", key)
    if raw is None:
        try:
            raw = llm.word_insent(w, s)
        except Exception as e:
            return jsonify({"error": "AI 调用失败：" + str(e)}), 502
        store.put_cache("wi", key, raw)
    return jsonify({"w": w, **llm.parse_word(raw)})


@app.post("/api/vocab/add")
def vocab_add():
    d = _data()
    try:
        bid = int(d.get("book"))
    except (TypeError, ValueError):
        return jsonify({"error": "bad"}), 400
    w = _norm(d.get("word"))
    if not w:
        return jsonify({"error": "bad"}), 400
    store.add_vocab(bid, w)
    return jsonify({"ok": True})


@app.post("/api/vocab/remove")
def vocab_remove():
    d = _data()
    try:
        bid = int(d.get("book"))
    except (TypeError, ValueError):
        return jsonify({"error": "bad"}), 400
    w = _norm(d.get("word"))
    if not w:
        return jsonify({"error": "bad"}), 400
    store.remove_vocab(bid, w)
    return jsonify({"ok": True})


@app.get("/vocab")
def vocab():
    return render_template("vocab.html", words=store.list_vocab())


@app.get("/config")
def config_page():
    return render_template(
        "config.html", st=llm.status(), result=None, dict_words=dictdb.count()
    )


@app.post("/config/reload")
def config_reload():
    global CFG, DATA, PAGE_CHARS
    CFG = load_cfg()
    DATA = os.path.join(BASE, CFG.get("data_dir", "data"))
    PAGE_CHARS = int(CFG.get("page", {}).get("chars", 1100))
    _CO_READ.update(CFG.get("co_read", {}) or {})
    llm.init(CFG.get("llm", {}))
    _pages_cache.clear()
    _TEXT_CACHE.clear()
    _CHAPTERS_CACHE.clear()
    _SCENES_CACHE.clear()
    return redirect(url_for("config_page"))


@app.post("/config/test")
def config_test():
    st = llm.status()
    result = None
    if llm.ready():
        try:
            result = llm.sentence("The quick brown fox jumps over the lazy dog.")
        except Exception as e:
            result = "调用失败：" + str(e)
    return render_template(
        "config.html", st=st, result=result, dict_words=dictdb.count()
    )


def chapters_for(book_id):
    if book_id not in _CHAPTERS_CACHE:
        book = store.get_book(book_id)
        if book is None:
            return []
        _CHAPTERS_CACHE[book_id] = reader.split_chapters(_book_text(book))
        if len(_CHAPTERS_CACHE) > 8:
            _CHAPTERS_CACHE.pop(next(iter(_CHAPTERS_CACHE)))
    return _CHAPTERS_CACHE[book_id]


def scenes_for(book_id):
    """全书场景位置列表 [(chapter_label, start, end)]（仅位置，不落库）。"""
    if book_id not in _SCENES_CACHE:
        book = store.get_book(book_id)
        if book is None:
            return []
        text = _book_text(book)
        out = []
        for label, cs, ce in chapters_for(book_id):
            for ss, se in reader.split_scenes(text[cs:ce], _CO_READ["scene_chars"]):
                out.append((label, cs + ss, cs + se))
        _SCENES_CACHE[book_id] = out
        if len(_SCENES_CACHE) > 4:
            _SCENES_CACHE.pop(next(iter(_SCENES_CACHE)))
    return _SCENES_CACHE[book_id]


def _pos_of_page(book_id, page, fs):
    pages = _pages_for(book_id, fs)
    if not pages:
        return 0
    page = max(1, min(page, len(pages)))
    return sum(len(p) for p in pages[:page - 1])


def _reading_position(book_id):
    prog = store.get_progress(book_id)
    if not prog:
        return 0
    return _pos_of_page(book_id, prog["page"], prog["font_size"])


def _chapter_of_pos(book_id, pos):
    for label, s, e in chapters_for(book_id):
        if s <= pos < e:
            return label
    return ""


def _canonical_sentence(bid, page, fs, s):
    """句子规范化：与页面原文逐句比对，取相似度最高且达标的原句。

    防止浏览器/交互层造成的词粘连等微小失真传给 LLM 与缓存。
    """
    if not bid or not page or page < 1:
        return s
    pages = _pages_for(bid, max(_FS_MIN, min(_FS_MAX, fs)))
    if not pages or page > len(pages):
        return s
    region = pages[page - 1]
    sn = _norm(s)
    if sn in _norm(region):
        return sn
    try:
        import difflib
        best_ratio, best = 0.0, None
        for a, b in reader.tokenize(region)[0]:
            cand = _norm(region[a:b])
            if not cand or len(cand) < 6:
                continue
            ratio = difflib.SequenceMatcher(None, sn, cand).ratio()
            if ratio > 0.8 and ratio > best_ratio:
                best_ratio, best = ratio, cand
        if best:
            return best
    except Exception:
        pass
    return sn


def _distill_scene(book_id, text, label, start, end):
    """蒸馏一个场景：LLM 提炼 + verbatim 金句校验，写入云层。"""
    if store.scene_at(book_id, start) is not None:
        return
    raw = text[start:end]
    if len(raw) < 40:
        return
    try:
        out = llm.distill(raw)
    except Exception:
        return
    quotes = []
    for q in out["quotes"][:5]:
        t = q["text"]
        if len(t) >= 4 and t in raw:
            quotes.append({"text": t, "speaker": q["speaker"]})
    store.add_scene(book_id, label, start, end,
                    out["summary"][:120], quotes, out["entities"][:200])


def _distill_eligible(book_id, position, cap=None, force=False):
    """蒸馏热窗口之前尚未蒸馏的场景（后台线程）。

    force=True 时绕过单任务锁（讨论前补齐缺口用，scene_at 防重）。
    """
    if _DISTILL_STATE.get(book_id) and not force:
        return
    cap = cap if cap is not None else _CO_READ["distill_batch"]
    book = store.get_book(book_id)
    if book is None:
        return
    text = _book_text(book)
    hot_start = max(0, position - _CO_READ["hot_window"])
    done = {r["start_pos"] for r in store.list_scenes(book_id)}
    todo = [(label, s, e) for label, s, e in scenes_for(book_id)
            if s < hot_start and e <= position and s not in done]
    todo.sort(key=lambda x: x[1])
    todo = todo[:cap]
    if not todo:
        return
    _DISTILL_STATE[book_id] = {"running": True, "done": 0, "total": len(todo)}
    try:
        for label, s, e in todo:
            _distill_scene(book_id, text, label, s, e)
            st = _DISTILL_STATE.get(book_id)
            if st:
                st["done"] += 1
    finally:
        _DISTILL_STATE.pop(book_id, None)


def _undistilled_count(book_id, position):
    hot_start = max(0, position - _CO_READ["hot_window"])
    done = {r["start_pos"] for r in store.list_scenes(book_id)}
    return sum(1 for _l, s, e in scenes_for(book_id)
               if s < hot_start and e <= position and s not in done)



def _recall(book_id, query, chapter_label=None):
    results = store.search_scenes(
        book_id, query, chapter_label, _CO_READ["recall_limit"])
    if not results:
        return "未找到相关记忆。"
    lines = []
    for r in results:
        parts = ["情节：" + r["summary"]]
        for q in r["quotes"][:3]:
            s = "原句：" + q["text"]
            if q.get("speaker"):
                s += "（" + q["speaker"] + "）"
            parts.append(s)
        lines.append("【记忆片段】" + "；".join(parts))
    return "\n".join(lines)


def _chapter_of_page(book_id, pg, fs):
    pages = _pages_for(book_id, fs)
    if not pages or pg < 1 or pg > len(pages):
        return None
    offset = sum(len(p) for p in pages[:pg - 1])
    for label, s, e in chapters_for(book_id):
        if s <= offset < e:
            return label
    chapters = chapters_for(book_id)
    return chapters[-1][0] if chapters else ""


def _history_pairs(conv_id):
    pairs = []
    last_q = None
    for m in store.list_msgs(conv_id):
        if m["role"] == "q":
            last_q = m["content"]
        elif last_q is not None:
            pairs.append((last_q, m["content"]))
            last_q = None
    return pairs


def _co_read_answer(book_id, chapter_label, question, history_pairs,
                    position=None):
    """共读回答：补齐蒸馏缺口 → 热窗口原文 + 预检索记忆片段 + recall 循环。"""
    book = store.get_book(book_id)
    title = book["title"]
    if position is None:
        position = _reading_position(book_id)
    _distill_eligible(book_id, position, cap=15, force=True)

    text = _book_text(book)
    hot_start = max(0, position - _CO_READ["hot_window"])
    hot = text[hot_start:position]
    progress_label = _chapter_of_pos(book_id, position) or "开头"
    fragments = _recall(book_id, question, chapter_label)

    frag_block = ("【此前记住的场景】\n" + fragments) if fragments else ""
    system = (
        "你是《%s》的共读伙伴，正在和用户一起读这本书。你们读到%s为止，"
        "你只记得读过的内容，不得提及或编造未读部分（用户问未读内容时，"
        "就说“还没读到那里”）。\n\n"
        "你当前的记忆：\n"
        "【最近读到的片段】\n%s\n\n"
        "%s\n"
        "回答要求：像人一样自然交流；需要引用原文时，只能逐字引用上面记忆中的"
        "原句；可以说“我记得……”，想不起来就诚实说想不起来。"
    ) % (title, progress_label, hot or "（还没有读过任何内容）", frag_block)

    messages = [{"role": "system", "content": system}]
    for q, a in history_pairs[-3:]:
        messages.append({"role": "user", "content": q})
        messages.append({"role": "assistant", "content": a[:1500]})
    messages.append({"role": "user", "content": question})

    def executor(name, args):
        if name == "recall":
            return _recall(book_id, args.get("query") or question, chapter_label)
        return "未知工具：" + str(name)

    return llm.chat_with_tools(messages, [llm.RECALL_TOOL], executor)


def _discuss_worker(conv_id, book_id, chapter_label, question, position=None):
    try:
        answer = _co_read_answer(book_id, chapter_label, question,
                                 _history_pairs(conv_id), position)
    except Exception as e:
        answer = "生成失败：" + str(e)
    store.add_msg(conv_id, "a", answer)
    task = _DISC_TASKS.get(conv_id)
    if task:
        task["done"] = True


def _start_task(conv_id, book_id, chapter_label, question, to_page,
                position=None):
    _DISC_TASKS[conv_id] = {"to": to_page, "done": False}
    t = threading.Thread(
        target=_discuss_worker,
        args=(conv_id, book_id, chapter_label, question, position),
        daemon=True,
    )
    t.start()


def session_pages(msgs):
    """把会话消息展平为分页列表：问题一页，回答按 _DISC_CPP 分页。"""
    out = []
    for m in msgs:
        if m["role"] == "q":
            out.append({"role": "q", "text": m["content"],
                        "first": True, "last": True})
        else:
            parts = reader.paginate(m["content"], _DISC_CPP) or [m["content"]]
            for i, p in enumerate(parts):
                out.append({"role": "a", "text": p,
                            "first": i == 0, "last": i == len(parts) - 1})
    return out


def _progress_of(book_id):
    prog = store.get_progress(book_id)
    if prog:
        return prog["page"], prog["font_size"]
    return 1, 18


@app.get("/discuss/<int:bid>")
def discuss_list(bid):
    book = store.get_book(bid)
    if book is None:
        abort(404)
    pg = int(request.args.get("pg", 0) or 0)
    fs = int(request.args.get("fs", 0) or 0)
    if pg <= 0 or fs <= 0:
        saved_pg, saved_fs = _progress_of(bid)
        pg = pg if pg > 0 else saved_pg
        fs = fs if fs > 0 else saved_fs
    position = _pos_of_page(bid, pg, fs)
    chapters = chapters_for(bid)
    read_chapters = [label for label, s, _e in chapters if s <= position]
    cur_chap = _chapter_of_page(bid, pg, fs) or (read_chapters[0] if read_chapters else "")
    hot_start = max(0, position - _CO_READ["hot_window"])
    done = {r["start_pos"] for r in store.list_scenes(bid)}
    distillable = sum(1 for _l, s, e in scenes_for(bid)
                      if s < hot_start and e <= position and s not in done)
    return render_template(
        "discuss.html",
        book=book,
        convs=store.list_convs(bid),
        chapters=read_chapters,
        cur_chap=cur_chap,
        pg=pg,
        fs=fs,
        scenes=store.count_scenes(bid),
        total_chapters=len(chapters),
        distillable=distillable,
    )


@app.post("/discuss/distill")
def discuss_distill():
    d = _data()
    try:
        bid = int(d.get("book"))
    except (TypeError, ValueError):
        abort(400)
    if _DISTILL_STATE.get(bid):
        return redirect(url_for("discuss_distill_status", bid=bid))
    position = _reading_position(bid)
    threading.Thread(
        target=_distill_eligible, args=(bid, position, 30), daemon=True
    ).start()
    return redirect(url_for("discuss_distill_status", bid=bid))


@app.get("/discuss/distill/status/<int:bid>")
def discuss_distill_status(bid):
    st = _DISTILL_STATE.get(bid)
    if not st:
        return redirect(url_for("discuss_list", bid=bid))
    return render_template(
        "distill_status.html",
        book=store.get_book(bid),
        done=st["done"],
        total=st["total"],
    )


@app.post("/discuss/new")
def discuss_new():
    d = _data()
    try:
        bid = int(d.get("book"))
    except (TypeError, ValueError):
        abort(400)
    chapter = _norm(d.get("chapter")) or ""
    q = _norm(d.get("question"))
    if not q:
        abort(400, "问题不能为空")
    position = None
    try:
        pg = int(d.get("pg") or 0)
        fs = int(d.get("fs") or 0)
        if pg > 0 and fs > 0:
            position = _pos_of_page(bid, pg, fs)
    except (TypeError, ValueError):
        position = None
    conv_id = store.add_conv(bid, q[:40], chapter)
    store.add_msg(conv_id, "q", q)
    to_page = len(session_pages(store.list_msgs(conv_id))) + 1
    _start_task(conv_id, bid, chapter, q, to_page, position)
    return redirect(url_for("discuss_wait", conv_id=conv_id))


@app.post("/discuss/reply")
def discuss_reply():
    d = _data()
    try:
        cid = int(d.get("conv"))
    except (TypeError, ValueError):
        abort(400)
    conv = store.get_conv(cid)
    if conv is None:
        abort(404)
    q = _norm(d.get("question"))
    if not q:
        abort(400, "问题不能为空")
    store.add_msg(cid, "q", q)
    to_page = len(session_pages(store.list_msgs(cid))) + 1
    _start_task(cid, conv["book_id"], conv["chapter_label"], q, to_page)
    return redirect(url_for("discuss_wait", conv_id=cid))


@app.get("/discuss/wait/<int:conv_id>")
def discuss_wait(conv_id):
    task = _DISC_TASKS.get(conv_id)
    to = task["to"] if task else 1
    if task and task["done"]:
        return redirect(url_for("discuss_conv", conv_id=conv_id, page=to))
    return render_template("wait.html", conv_id=conv_id, to=to)


@app.get("/discuss/conv/<int:conv_id>/<int:page>")
def discuss_conv(conv_id, page):
    conv = store.get_conv(conv_id)
    if conv is None:
        abort(404)
    book = store.get_book(conv["book_id"])
    pages = session_pages(store.list_msgs(conv_id))
    if not pages:
        abort(404, "空会话")
    page = max(1, min(page, len(pages)))
    saved_pg, saved_fs = _progress_of(conv["book_id"])
    return render_template(
        "conv.html",
        conv=conv,
        book=book,
        pg=page,
        total=len(pages),
        item=pages[page - 1],
        prev=page - 1 if page > 1 else None,
        nxt=page + 1 if page < len(pages) else None,
        saved_pg=saved_pg,
        saved_fs=saved_fs,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
