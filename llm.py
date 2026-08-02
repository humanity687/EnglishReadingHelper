"""LLM 调用：openai 库统一访问 ollama / OpenAI 兼容 API，启动时探测可用后端。"""
import json
import re

from openai import OpenAI

STATE = {"client": None, "model": None, "backend": None, "msg": ""}

SENT_PROMPT = (
    "你是英语学习助手，面向中国中学生。请将下面的英语句子翻译成通顺自然的中文，"
    "并解析其语法结构，给出 2~4 条要点（句型、从句、时态、固定搭配等，用中文，"
    "每条一句）。另外选出句中 1~3 个值得掌握的重点词，给出词性和在本句中的意思。"
    "只输出一个 JSON 对象，不要输出任何其他内容，格式："
    '{"translation": "...", "grammar": ["要点1", "要点2"], '
    '"words": [{"w": "词", "pos": "词性", "m": "句中意思"}]}\n\n句子：'
)

WORD_PROMPT = (
    "你是英语学习助手，面向中国中学生。请解释单词“{w}”在下面句子语境中的具体含义"
    "（中文，取最贴合本句的词义），并说明词性和一句中文的“句中意”。"
    "只输出一个 JSON 对象，不要输出任何其他内容，格式："
    '{{"meaning": "...", "pos": "..."}}\n\n句子：{s}'
)


def init(llm_cfg):
    """探测并选定可用后端；mode: auto / ollama / api。"""
    llm_cfg = llm_cfg or {}
    mode = llm_cfg.get("mode", "auto")
    oll = llm_cfg.get("ollama", {})
    api = llm_cfg.get("api", {})
    STATE.update(client=None, model=None, backend=None, msg="")
    if mode == "ollama":
        _try(oll, "ollama")
    elif mode == "api":
        _try(api, "api")
    else:
        if not _try(oll, "ollama") and not _try(api, "api"):
            STATE["msg"] = (
                "未找到可用的 LLM（自动模式：ollama 不可达，"
                "且 api 未配置 api_key）"
            )


def _try(cfg, backend):
    base = cfg.get("base_url")
    key = cfg.get("api_key")
    model = cfg.get("model")
    if not base or not model:
        return False
    try:
        client = OpenAI(base_url=base, api_key=key or "ollama",
                        timeout=5, max_retries=0)
        client.models.list()
        STATE.update(client=client, model=model, backend=backend, msg="")
        return True
    except Exception:
        return False


def ready():
    return STATE["client"] is not None


def status():
    return {
        "backend": STATE["backend"],
        "model": STATE["model"],
        "msg": STATE["msg"],
    }


def _chat(content, timeout=180):
    if not ready():
        raise RuntimeError(STATE["msg"] or "LLM 未配置")
    r = STATE["client"].chat.completions.create(
        model=STATE["model"],
        messages=[{"role": "user", "content": content}],
        temperature=0.2,
        timeout=timeout,
    )
    return r.choices[0].message.content or ""


def sentence(s):
    return _chat(SENT_PROMPT + s)


def word_insent(w, s):
    return _chat(WORD_PROMPT.format(w=w, s=s))


def _parse_json(raw):
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b <= a:
        return None
    core = s[a:b + 1]
    try:
        return json.loads(core)
    except Exception:
        pass
    try:
        return json.loads(_tolerant_json(core))
    except Exception:
        return None


def _tolerant_json(core):
    """容错清理 LLM 输出：全角引号作定界符、字符串外残留全角标点、
    未闭合字符串。字符串内容里的全角引号/标点原样保留。"""
    out = []
    in_str = False
    close = None
    i = 0
    n = len(core)
    struct_after = set(",]}:\n\r\t \u3002\uff0c\uff1b\uff1a\u3001")
    while i < n:
        c = core[i]
        if in_str:
            if c == "\\" and i + 1 < n:
                out.append(c)
                out.append(core[i + 1])
                i += 1
            elif c == close:
                in_str = False
                out.append('"')
            elif c in '"\u201d' and (i + 1 >= n or core[i + 1] in struct_after):
                in_str = False
                out.append('"')
            else:
                out.append(c)
        elif c == '"' or c == "\u201c":
            in_str = True
            close = "\u201d" if c == "\u201c" else '"'
            out.append('"')
        elif c == "\u201d" or c == "\u3002" or c == "\u3001":
            pass
        elif c == "\uff0c":
            out.append(",")
        elif c == "\uff1b":
            out.append(";")
        elif c == "\uff1a":
            out.append(":")
        else:
            out.append(c)
        i += 1
    if in_str:
        out.append('"')
    return "".join(out)


def parse_sent(raw):
    d = _parse_json(raw)
    if d is None:
        return {"raw": raw}
    words = d.get("words") or []
    clean = []
    for item in words:
        if isinstance(item, dict) and item.get("w"):
            clean.append({
                "w": item["w"],
                "pos": item.get("pos", ""),
                "m": item.get("m", ""),
            })
    return {
        "translation": d.get("translation", ""),
        "grammar": [g for g in (d.get("grammar") or [])
                    if isinstance(g, str) and g.strip()],
        "words": clean,
    }


def parse_word(raw):
    d = _parse_json(raw)
    if d is None:
        return {"raw": raw}
    return {"meaning": d.get("meaning", ""), "pos": d.get("pos", "")}
