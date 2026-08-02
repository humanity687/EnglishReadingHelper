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
_DISC_TASKS = {}
_PAGES_MAX = 32
_TEXT_MAX = 3
_FS_MIN, _FS_MAX = 14, 30
_DISC_CPP = 600
_CHAPTER_CTX = 5000


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


@app.get("/read/<int:bid>/<int:pg>")
def read(bid, pg):
    book = store.get_book(bid)
    if book is None:
        abort(404)
    fs = max(_FS_MIN, min(_FS_MAX, int(request.args.get("fs", 18))))
    pages = _pages_for(bid, fs)
    if not pages:
        abort(404, "空书")
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
    if not llm.ready():
        return jsonify({"error": "AI 未配置：请编辑 config.json 后到 /config 重新加载", "configured": False}), 503
    key = _key(s)
    raw = store.get_cache("sent", key)
    if raw is None:
        try:
            raw = llm.sentence(s)
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
    llm.init(CFG.get("llm", {}))
    _pages_cache.clear()
    _TEXT_CACHE.clear()
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
            abort(404)
        _CHAPTERS_CACHE[book_id] = reader.split_chapters(_book_text(book))
        if len(_CHAPTERS_CACHE) > 8:
            _CHAPTERS_CACHE.pop(next(iter(_CHAPTERS_CACHE)))
    return _CHAPTERS_CACHE[book_id]


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


def _discuss_context(book_id, chapter_label, conv_id):
    chapters = chapters_for(book_id)
    label, s, e = chapters[0] if chapters else ("", 0, 0)
    for cl, cs, ce in chapters:
        if cl == chapter_label:
            label, s, e = cl, cs, ce
            break
    text = _book_text(store.get_book(book_id))
    return text[s:s + _CHAPTER_CTX], _history_pairs(conv_id)


def _discuss_worker(conv_id, book_id, chapter_label, question):
    try:
        context, history = _discuss_context(book_id, chapter_label, conv_id)
        answer = llm.discuss(question, chapter_label, context, history)
    except Exception as e:
        answer = "生成失败：" + str(e)
    store.add_msg(conv_id, "a", answer)
    task = _DISC_TASKS.get(conv_id)
    if task:
        task["done"] = True


def _start_task(conv_id, book_id, chapter_label, question, to_page):
    _DISC_TASKS[conv_id] = {"to": to_page, "done": False}
    t = threading.Thread(
        target=_discuss_worker,
        args=(conv_id, book_id, chapter_label, question),
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


@app.get("/discuss/<int:bid>")
def discuss_list(bid):
    book = store.get_book(bid)
    if book is None:
        abort(404)
    pg = int(request.args.get("pg", 1) or 1)
    fs = int(request.args.get("fs", 18) or 18)
    chapters = chapters_for(bid)
    cur_chap = _chapter_of_page(bid, pg, fs) or (chapters[0][0] if chapters else "")
    return render_template(
        "discuss.html",
        book=book,
        convs=store.list_convs(bid),
        chapters=[c[0] for c in chapters],
        cur_chap=cur_chap,
        pg=pg,
        fs=fs,
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
    conv_id = store.add_conv(bid, q[:40], chapter)
    store.add_msg(conv_id, "q", q)
    to_page = len(session_pages(store.list_msgs(conv_id))) + 1
    _start_task(conv_id, bid, chapter, q, to_page)
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
    return render_template(
        "conv.html",
        conv=conv,
        book=book,
        pg=page,
        total=len(pages),
        item=pages[page - 1],
        prev=page - 1 if page > 1 else None,
        nxt=page + 1 if page < len(pages) else None,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
