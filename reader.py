"""分页与分词分句：全部为纯文本处理，输出服务端预渲染的 HTML。"""
import html
import re

WORD_RE = re.compile(r"[A-Za-z]+(?:['\u2019][A-Za-z]+)*")
TERM_RE = re.compile(r"[.!?\u2026]+(?=\s+[A-Z0-9\u201c\u2018\"(\[]|\s*$)")
NL_RE = re.compile(r"\n+")
PARA_RE = re.compile(r"\n{2,}")

ABBR = frozenset(
    "mr mrs ms dr st vs etc e.g i.e no jan feb mar apr may jun jul aug sep "
    "oct nov dec cf al mt ft prof sr jr rev fig vol pp sec ch approx u.s u.k "
    "us uk mr. mrs. ms. dr. st. vs. etc. e.g. i.e. jan. feb. mar. apr. jun. "
    "jul. aug. sep. oct. nov. dec. prof. sr. jr. rev. fig. vol. sec. ch. "
    "approx. u.s. u.k.".split()
)


def _abbr_tail(frag):
    if re.search(r"\b[A-Za-z]\.[A-Za-z]\.$", frag):
        return True
    m = re.search(r"\b([A-Za-z]{1,3})\.$", frag)
    return bool(m and (m.group(1).lower() in ABBR or len(m.group(1)) == 1))


def _ends_term(s):
    s = s.rstrip("\u201d\u2019\"' ")
    return bool(s and s[-1] in ".!?\u2026")


def _starts_lower(s):
    s = s.lstrip(" \t\u201c\u2018\"'\u2014-")
    return bool(s and s[0].islower())


def tokenize(text):
    """返回 (句子区间列表, 单词区间列表)。

    句子边界 = 句末标点 或 换行；硬换行拆开的散文句按"上一段未以
    句号结尾且下一段小写开头"合并回退（PDF 硬换行、标题区等场景）。
    """
    starts = [0]
    for m in TERM_RE.finditer(text):
        starts.append(m.end())
    for m in NL_RE.finditer(text):
        starts.append(m.end())

    pieces = []
    prev = 0
    for pos in sorted(set(starts)):
        if pos > prev and text[prev:pos].strip():
            pieces.append((prev, pos))
        prev = pos
    if prev < len(text) and text[prev:].strip():
        pieces.append((prev, len(text)))

    merged = []
    for a, b in pieces:
        if merged and _abbr_tail(text[merged[-1][0]:merged[-1][1]]):
            prev = merged[-1]
            merged[-1] = (prev[0], b)
        elif (merged
              and not _ends_term(text[merged[-1][0]:merged[-1][1]])
              and _starts_lower(text[a:b])):
            prev = merged[-1]
            merged[-1] = (prev[0], b)
        else:
            merged.append((a, b))

    words = [(m.start(), m.end(), m.group(0)) for m in WORD_RE.finditer(text)]
    return merged, words


def _render_para(para):
    sents, words = tokenize(para)
    if not sents:
        return html.escape(para).replace("\n", "<br>")

    events = []
    for a, b in sents:
        events.append((a, 1, "<span class=\"s\">"))
        events.append((b, 0, "</span>"))
    for a, b, _w in words:
        events.append((a, 2, "<span class=\"w\">"))
        events.append((b, 3, "</span>"))
    events.sort(key=lambda e: (e[0], e[1]))

    out = []
    pos = 0
    for off, _order, tag in events:
        if off > pos:
            out.append(html.escape(para[pos:off]).replace("\n", "<br>"))
        out.append(tag)
        pos = off
    if pos < len(para):
        out.append(html.escape(para[pos:]).replace("\n", "<br>"))
    return "".join(out)


def render_page(text):
    """把一页文本渲染为带 .s / .w span 的段落 HTML。"""
    out = []
    for para in PARA_RE.split(text):
        if not para.strip():
            continue
        out.append("<p>" + _render_para(para) + "</p>")
    return "\n".join(out)


def paginate(text, cpp):
    """按每页字符数把全书切成页（单词边界截断，整段优先）。"""
    pages = []
    cur = ""
    for p in text.split("\n\n"):
        if not p.strip():
            continue
        if not cur:
            cur = p
        elif len(cur) + 2 + len(p) <= cpp:
            cur += "\n\n" + p
        else:
            if cur:
                pages.append(cur)
            cur = p
        while len(cur) > cpp:
            cut = cur.rfind(" ", 0, cpp + 1)
            cut = cut if cut > 0 else cpp
            pages.append(cur[:cut].rstrip())
            cur = cur[cut:].lstrip()
    if cur:
        pages.append(cur)
    return pages
