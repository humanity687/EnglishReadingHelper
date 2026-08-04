"""SQLite 持久层：books / cache / vocab / progress / convs / scenes。"""
import os
import re
import sqlite3
import threading

_conns = threading.local()
DB_PATH = None


def init(data_dir):
    global DB_PATH
    DB_PATH = os.path.join(data_dir, "app.db")
    os.makedirs(os.path.join(data_dir, "books"), exist_ok=True)
    c = _conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT DEFAULT '',
            text_path TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS cache (
            kind TEXT NOT NULL,
            key  TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (kind, key)
        );
        CREATE TABLE IF NOT EXISTS vocab (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            word TEXT NOT NULL,
            added_at TEXT DEFAULT (datetime('now')),
            UNIQUE (book_id, word)
        );
        CREATE TABLE IF NOT EXISTS progress (
            book_id INTEGER PRIMARY KEY,
            page INTEGER NOT NULL DEFAULT 1,
            font_size INTEGER NOT NULL DEFAULT 18,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS convs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            chapter_label TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS conv_msgs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conv_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS scenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            chapter_label TEXT DEFAULT '',
            start_pos INTEGER NOT NULL,
            end_pos INTEGER NOT NULL,
            summary TEXT DEFAULT '',
            quotes TEXT DEFAULT '[]',
            entities TEXT DEFAULT '',
            distilled_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_scenes_book ON scenes(book_id);
        """
    )
    for col, decl in (("gist", "TEXT DEFAULT ''"),
                      ("details", "TEXT DEFAULT '[]'")):
        try:
            c.execute("ALTER TABLE scenes ADD COLUMN %s %s" % (col, decl))
        except sqlite3.OperationalError:
            pass
    c.commit()


def _conn():
    c = getattr(_conns, "c", None)
    if c is None:
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _conns.c = c
    return c


def add_book(title, author=""):
    c = _conn()
    cur = c.execute("INSERT INTO books (title, author) VALUES (?, ?)", (title, author))
    c.commit()
    return cur.lastrowid


def set_book_path(book_id, path):
    c = _conn()
    c.execute("UPDATE books SET text_path = ? WHERE id = ?", (path, book_id))
    c.commit()


def get_book(book_id):
    return _conn().execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()


def list_books():
    return _conn().execute(
        """SELECT b.id, b.title, b.author, b.created_at,
                  COALESCE(p.page, 1) AS progress_page,
                  COALESCE(p.font_size, 18) AS progress_fs
           FROM books b LEFT JOIN progress p ON p.book_id = b.id
           ORDER BY b.id DESC"""
    ).fetchall()


def get_cache(kind, key):
    r = _conn().execute(
        "SELECT value FROM cache WHERE kind = ? AND key = ?", (kind, key)
    ).fetchone()
    return r["value"] if r else None


def put_cache(kind, key, value):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO cache (kind, key, value) VALUES (?, ?, ?)",
        (kind, key, value),
    )
    c.commit()


def is_saved(book_id, word):
    r = _conn().execute(
        "SELECT 1 FROM vocab WHERE book_id = ? AND word = ?", (book_id, word)
    ).fetchone()
    return r is not None


def add_vocab(book_id, word):
    c = _conn()
    c.execute(
        "INSERT OR IGNORE INTO vocab (book_id, word) VALUES (?, ?)", (book_id, word)
    )
    c.commit()


def remove_vocab(book_id, word):
    c = _conn()
    c.execute("DELETE FROM vocab WHERE book_id = ? AND word = ?", (book_id, word))
    c.commit()


def list_vocab(limit=500):
    return _conn().execute(
        """SELECT v.id, v.book_id, v.word, v.added_at, b.title
           FROM vocab v JOIN books b ON b.id = v.book_id
           ORDER BY v.added_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()


def get_progress(book_id):
    return _conn().execute(
        "SELECT page, font_size FROM progress WHERE book_id = ?", (book_id,)
    ).fetchone()


def set_progress(book_id, page, font_size=None):
    c = _conn()
    if font_size is None:
        c.execute(
            "UPDATE progress SET page = ?, updated_at = datetime('now') WHERE book_id = ?",
            (page, book_id),
        )
    else:
        c.execute(
            """INSERT OR REPLACE INTO progress (book_id, page, font_size, updated_at)
               VALUES (?, ?, ?, datetime('now'))""",
            (book_id, page, font_size),
        )
    c.commit()


def vocab_words(book_id):
    return [r["word"] for r in _conn().execute(
        "SELECT word FROM vocab WHERE book_id = ?", (book_id,)).fetchall()]


def add_conv(book_id, title, chapter_label=""):
    c = _conn()
    cur = c.execute(
        "INSERT INTO convs (book_id, title, chapter_label) VALUES (?, ?, ?)",
        (book_id, title, chapter_label),
    )
    c.commit()
    return cur.lastrowid


def get_conv(conv_id):
    return _conn().execute(
        "SELECT * FROM convs WHERE id = ?", (conv_id,)
    ).fetchone()


def list_convs(book_id):
    return _conn().execute(
        """SELECT c.id, c.title, c.chapter_label, c.created_at,
                  (SELECT content FROM conv_msgs m WHERE m.conv_id = c.id
                   ORDER BY m.id DESC LIMIT 1) AS last_msg
           FROM convs c WHERE c.book_id = ? ORDER BY c.id DESC LIMIT 50""",
        (book_id,),
    ).fetchall()


def add_msg(conv_id, role, content):
    c = _conn()
    cur = c.execute(
        "INSERT INTO conv_msgs (conv_id, role, content) VALUES (?, ?, ?)",
        (conv_id, role, content),
    )
    c.commit()
    return cur.lastrowid


def list_msgs(conv_id):
    return _conn().execute(
        "SELECT id, role, content FROM conv_msgs WHERE conv_id = ? ORDER BY id",
        (conv_id,),
    ).fetchall()


def add_scene(book_id, chapter_label, start_pos, end_pos, summary, quotes,
              entities, gist="", details=None):
    c = _conn()
    import json as _json
    quotes = quotes if isinstance(quotes, list) else []
    details = details if isinstance(details, list) else []
    cur = c.execute(
        "INSERT INTO scenes (book_id, chapter_label, start_pos, end_pos, "
        "summary, quotes, entities, gist, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (book_id, chapter_label, start_pos, end_pos,
         summary, _json.dumps(quotes, ensure_ascii=False), entities,
         gist, _json.dumps(details, ensure_ascii=False)),
    )
    c.commit()
    _idx_bump(book_id)
    return cur.lastrowid


def list_scenes(book_id):
    return _conn().execute(
        "SELECT * FROM scenes WHERE book_id = ? ORDER BY start_pos", (book_id,)
    ).fetchall()


def count_scenes(book_id):
    return _conn().execute(
        "SELECT COUNT(*) FROM scenes WHERE book_id = ?", (book_id,)
    ).fetchone()[0]


def scene_at(book_id, start_pos):
    return _conn().execute(
        "SELECT id FROM scenes WHERE book_id = ? AND start_pos = ?",
        (book_id, start_pos),
    ).fetchone()


_IDX = {}          # book_id -> (token, {gram: {scene_id}})
_IDX_TOKEN = {}    # book_id -> 版本号（add_scene 时递增）


def _idx_bump(book_id):
    _IDX_TOKEN[book_id] = _IDX_TOKEN.get(book_id, 0) + 1


def _blob_of(row):
    return " ".join([row["gist"], row["summary"], row["details"],
                     row["quotes"], row["entities"]]).lower()


def _get_index(book_id, rows):
    """构建/复用倒排索引：{双字gram: {scene_id}}，懒加载 + 版本失效。"""
    token = _IDX_TOKEN.get(book_id, 0)
    cached = _IDX.get(book_id)
    if cached and cached[0] == token:
        return cached[1]
    idx = {}
    for r in rows:
        b = re.sub(r"\s+", "", _blob_of(r))
        grams = {b[i:i + 2] for i in range(len(b) - 1)}
        for g in grams:
            idx.setdefault(g, set()).add(r["id"])
    _IDX[book_id] = (token, idx)
    return idx


def _ngrams(q):
    """查询 n-gram 集合：双字窗口（中文词基本双字，容错优于三字）。"""
    q = re.sub(r"\s+", "", (q or "").lower())
    if len(q) >= 2:
        return {q[i:i + 2] for i in range(len(q) - 1)}
    return {q} if q else set()


def search_scenes(book_id, query, chapter_label=None, limit=8):
    """按查询双字窗口评分检索场景记录（倒排索引加速，中英文免分词）。

    评分覆盖全部记忆层级（gist/summary/details/quotes/entities）。
    """
    import json as _json
    sql = ("SELECT id, book_id, chapter_label, start_pos, end_pos, gist, "
           "summary, details, quotes, entities FROM scenes WHERE book_id = ?")
    params = [book_id]
    if chapter_label:
        sql += " AND chapter_label = ?"
        params.append(chapter_label)
    rows = _conn().execute(sql, params).fetchall()
    if not rows:
        return []
    q = (query or "").strip()
    if not q:
        out = [dict(r) for r in rows[:limit]]
        for r in out:
            r["quotes"] = _json.loads(r["quotes"])
            r["details"] = _json.loads(r["details"])
        return out
    grams = _ngrams(q)
    if not grams:
        return []
    idx = _get_index(book_id, rows)
    scores = {}
    for g in grams:
        for sid in idx.get(g, ()):
            scores[sid] = scores.get(sid, 0) + 1
    if not scores:
        return []
    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    rowmap = {r["id"]: r for r in rows}
    out = [dict(rowmap[sid]) for sid, _score in ranked[:limit] if sid in rowmap]
    for r in out:
        r["quotes"] = _json.loads(r["quotes"])
        r["details"] = _json.loads(r["details"])
    return out
