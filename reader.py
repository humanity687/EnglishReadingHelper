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


def _render_para(para, saved):
    sents, words = tokenize(para)
    if not sents:
        return html.escape(para).replace("\n", "<br>")

    events = []
    for a, b in sents:
        events.append((a, 1, "<span class=\"s\">"))
        events.append((b, 0, "</span>"))
    for a, b, w in words:
        cls = " saved" if w.lower() in saved else ""
        events.append((a, 2, "<span class=\"w" + cls + "\">"))
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


def render_page(text, saved=None):
    """把一页文本渲染为带 .s / .w span 的段落 HTML。

    saved：本书生词本中的词（小写集合），命中词加 saved 标记（下划线）。
    """
    saved = saved or frozenset()
    out = []
    for para in PARA_RE.split(text):
        if not para.strip():
            continue
        out.append("<p>" + _render_para(para, saved) + "</p>")
    return "\n".join(out)


_SENT_CUT_RE = re.compile(r"[.!?\u2026]+\s")


def _sentence_cut(cur, cpp):
    """在 cpp 前找最近的句子边界；找不到回退单词边界。"""
    best = 0
    for m in _SENT_CUT_RE.finditer(cur[:cpp + 1]):
        best = m.end()
    if best > 0:
        return best
    cut = cur.rfind(" ", 0, cpp + 1)
    return cut if cut > 0 else cpp


def paginate(text, cpp):
    """按每页字符数把全书切成页（句子边界优先，整段优先，单词边界兜底）。"""
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
            cut = _sentence_cut(cur, cpp)
            pages.append(cur[:cut].rstrip())
            cur = cur[cut:].lstrip()
    if cur:
        pages.append(cur)
    return pages


CHAP_RE = re.compile(
    r"^chapter\s+[IVX\d]+\b.*$|^part\s+[IVX\d]+\b.*$|"
    r"^第\s*[一二三四五六七八九十百零0-9]+\s*[章回节卷].*$",
    re.IGNORECASE | re.MULTILINE,
)


def split_chapters(text):
    """按常见章节标题切分全书，返回 [(label, start, end)]。

    无标题匹配时全书视为一个章节（label 取首行截断）。
    """
    spans = [m.span() for m in CHAP_RE.finditer(text)]
    if not spans:
        first = text.strip().split("\n", 1)[0].strip()
        return [((first[:40] or "全书"), 0, len(text))]
    chapters = []
    for i, (s, e) in enumerate(spans):
        label = text[s:e].strip().split("\n")[0][:60]
        end = spans[i + 1][0] if i + 1 < len(spans) else len(text)
        chapters.append((label, s, end))
    return chapters
