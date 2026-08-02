"""EngReadHelper：面向墨水屏的英语阅读辅助服务。"""
import hashlib
import json
import os
import re
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
_PAGES_MAX = 32
_TEXT_MAX = 3
_FS_MIN, _FS_MAX = 14, 30


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
    return render_template(
        "read.html",
        book=book,
        pg=pg,
        total=len(pages),
        fs=fs,
        prev=pg - 1 if pg > 1 else None,
        nxt=pg + 1 if pg < len(pages) else None,
        html=reader.render_page(pages[pg - 1]),
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
