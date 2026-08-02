"""ECDICT 离线词典查询：精确匹配 → 词形还原 → 无果返回 None。"""
import re
import sqlite3
import threading

_conns = threading.local()
_DICT_PATH = None

LV = {
    "zk": "中考",
    "gk": "高考",
    "cet4": "四级",
    "cet6": "六级",
    "ky": "考研",
    "toefl": "托福",
    "ielts": "雅思",
    "gre": "GRE",
}

EX = {
    "d": "过去式",
    "p": "过去分词",
    "i": "进行式",
    "3": "三单",
    "s": "复数",
    "r": "比较级",
    "t": "最高级",
    "0": "原形",
}

IRREG = {
    "am": "be", "are": "be", "is": "be", "was": "be", "were": "be", "been": "be",
    "has": "have", "had": "have", "having": "have",
    "does": "do", "did": "do", "doing": "do",
    "went": "go", "gone": "go", "going": "go",
    "said": "say", "saying": "say",
    "made": "make", "making": "make",
    "came": "come", "coming": "come",
    "took": "take", "taken": "take", "taking": "take",
    "saw": "see", "seen": "see", "seeing": "see",
    "thought": "think", "thinking": "think",
    "knew": "know", "known": "know", "knowing": "know",
    "got": "get", "gotten": "get", "getting": "get",
    "found": "find", "finding": "find",
    "gave": "give", "given": "give", "giving": "give",
    "told": "tell", "telling": "tell",
    "left": "leave", "leaving": "leave",
    "felt": "feel", "feeling": "feel",
    "kept": "keep", "keeping": "keep",
    "wrote": "write", "written": "write", "writing": "write",
    "ran": "run", "running": "run",
    "ate": "eat", "eaten": "eat", "eating": "eat",
    "drank": "drink", "drunk": "drink", "drinking": "drink",
    "bought": "buy", "buying": "buy",
    "brought": "bring", "bringing": "bring",
    "caught": "catch", "catching": "catch",
    "taught": "teach", "teaching": "teach",
    "built": "build", "building": "build",
    "sent": "send", "sending": "send",
    "spent": "spend", "spending": "spend",
    "paid": "pay", "paying": "pay",
    "lay": "lie", "laid": "lay", "lying": "lie",
    "children": "child", "men": "man", "women": "woman",
    "feet": "foot", "teeth": "tooth", "mice": "mouse",
    "better": "good", "best": "good", "worse": "bad", "worst": "bad",
    "using": "use", "being": "be",
}


def init(path):
    global _DICT_PATH
    _DICT_PATH = path
    _conns.c = _open(path)


def _open(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def _conn():
    c = getattr(_conns, "c", None)
    if c is None:
        _conns.c = _open(_DICT_PATH)
    return _conns.c


def count():
    c = _conn()
    if c is None:
        return 0
    return c.execute("SELECT COUNT(*) FROM stardict").fetchone()[0]


def _q(word):
    c = _conn()
    if c is None:
        return None
    return c.execute(
        "SELECT word, phonetic, definition, translation, pos, tag, exchange "
        "FROM stardict WHERE word = ?",
        (word,),
    ).fetchone()


def lookup(word):
    if _conn() is None:
        return None
    w = word.strip()
    if not w:
        return None
    row = _q(w) or _q(w.lower()) or _q(w.capitalize())
    if row:
        return _build(row)
    for cand in _morph(w.lower()):
        row = _q(cand)
        if row:
            r = _build(row)
            r["from"] = cand
            return r
    return None


def _build(row):
    tags = (row["tag"] or "").split()
    pos = row["pos"] or ""
    pos = re.sub(r":\d+$", "", pos)
    return {
        "word": row["word"],
        "phonetic": row["phonetic"] or "",
        "pos": pos,
        "translation": row["translation"] or "",
        "definition": row["definition"] or "",
        "levels": " ".join(LV[t] for t in tags if t in LV),
        "exchange": _exch(row["exchange"]),
    }


def _exch(raw):
    if not raw:
        return ""
    parts = []
    for item in raw.split("/"):
        k, _, v = item.partition(":")
        if k in EX and v:
            parts.append(EX[k] + " " + v)
    return " / ".join(parts[:4])


def _morph(w):
    out = []
    if w in IRREG:
        out.append(IRREG[w])
    if w.endswith("ies") and len(w) > 4:
        out.append(w[:-3] + "y")
    if w.endswith("es") and len(w) > 3 and not w.endswith("ss"):
        out.append(w[:-2])
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        out.append(w[:-1])
    if w.endswith("ied") and len(w) > 5:
        out.append(w[:-3] + "y")
    if w.endswith("ing") and len(w) > 5:
        stem = w[:-3]
        if stem.endswith("e"):
            out.append(stem[:-1])
        out.append(stem + "e")
        out.append(stem)
        if len(stem) > 2 and stem[-1] == stem[-2]:
            out.append(stem[:-1])
    if w.endswith("ed") and len(w) > 4:
        stem = w[:-2]
        if stem.endswith("e"):
            out.append(stem[:-1])
        out.append(stem + "e")
        out.append(stem)
        if len(stem) > 2 and stem[-1] == stem[-2]:
            out.append(stem[:-1])
    if w.endswith("er") and len(w) > 4:
        out.append(w[:-2])
        out.append(w[:-2] + "e")
    if w.endswith("est") and len(w) > 5:
        out.append(w[:-3])
        out.append(w[:-3] + "e")
    seen = set()
    return [c for c in out if not (c in seen or seen.add(c))]
