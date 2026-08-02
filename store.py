"""SQLite 持久层：books / cache / vocab / progress 四张表。"""
import os
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
        """
    )
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
                  COALESCE(p.page, 1) AS progress_page
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
