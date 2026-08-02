"""书源文本提取：TXT / EPUB / PDF（文本型），统一返回 (text, title, author)。"""
import os
import re

import fitz
import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

TEXT_TAGS = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre"]


def extract(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt":
        return _extract_txt(path)
    if ext == ".epub":
        return _extract_epub(path)
    if ext == ".pdf":
        return _extract_pdf(path)
    raise ValueError("unsupported file type: " + ext)


def _extract_txt(path):
    with open(path, "rb") as f:
        raw = f.read()
    text = None
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    return normalize(text), "", ""


def _extract_epub(path):
    book = epub.read_epub(path)
    title = _meta(book, "title")
    author = _meta(book, "creator")
    parts = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        paras = []
        for el in soup.find_all(TEXT_TAGS):
            t = el.get_text(" ", strip=True)
            if t:
                paras.append(t)
        if paras:
            parts.append("\n\n".join(paras))
    return normalize("\n\n".join(parts)), title, author


def _meta(book, key):
    try:
        vals = book.get_metadata("DC", key)
        if vals and vals[0][0]:
            return vals[0][0]
    except Exception:
        pass
    return ""


def _extract_pdf(path):
    doc = fitz.open(path)
    title = doc.metadata.get("title") or ""
    author = doc.metadata.get("author") or ""
    pages = []
    for page in doc:
        pages.append(_pdf_page_text(page))
    doc.close()
    return normalize("\n\n".join(pages)), title, author


def _pdf_page_text(page, tol=2.0):
    """按坐标提取并去重一页文本。

    部分 PDF（假粗体、重复文字层）会把文字绘制两遍：同一位置的
    单词会出现两份，按坐标去重后重建行与段落。
    """
    words = page.get_text("words", sort=True)
    if not words:
        return ""

    seen = {}
    deduped = []
    for w in words:
        x0, y0, _x1, _y1, word = w[0], w[1], w[2], w[3], w[4]
        key = (round(y0), word)
        px = seen.get(key)
        if px is not None and abs(px - x0) <= tol:
            continue
        seen[key] = x0
        deduped.append(w)

    line_map = {}
    for w in deduped:
        line_map.setdefault((w[5], w[6]), []).append(w)
    lines = []
    for (b, l) in sorted(line_map):
        ws = sorted(line_map[(b, l)], key=lambda w: w[0])
        lines.append((min(w[1] for w in ws), min(w[0] for w in ws),
                      " ".join(w[4] for w in ws)))

    if len(lines) < 2:
        return lines[0][2] if lines else ""

    gaps = [b - a for (a, _x, _t), (b, _x2, _t2) in zip(lines, lines[1:]) if b > a]
    if not gaps:
        return "\n".join(t for _y, _x, t in lines)
    gap_med = sorted(gaps)[len(gaps) // 2]
    para_gap = max(gap_med * 1.6, 12.0)

    paras = []
    cur = [lines[0]]
    for line in lines[1:]:
        if line[0] - cur[-1][0] > para_gap:
            paras.append(cur)
            cur = [line]
        else:
            cur.append(line)
    paras.append(cur)
    return "\n\n".join("\n".join(t for _y, _x, t in p) for p in paras)


def normalize(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
