"""LLM 调用：openai 库统一访问 ollama / OpenAI 兼容 API，启动时探测可用后端。"""
import json
import re

from openai import OpenAI

STATE = {"client": None, "model": None, "backend": None, "msg": ""}

SENT_PROMPT = (
    "你是英语学习助手，面向中国中学生。{ctx}"
    "请将下面的英语句子翻译成通顺自然的中文，"
    "并解析其语法结构，给出 2~4 条要点（句型、从句、时态、固定搭配等，用中文，"
    "每条一句）。另外选出句中值得掌握的重点单词、固定搭配或习惯用语"
    "（数量不限，按重要性列出），给出词性和在本句中的意思。"
    "只输出一个 JSON 对象，不要输出任何其他内容，格式："
    '{{"translation": "...", "grammar": ["要点1", "要点2"], '
    '"words": [{{"w": "词或搭配", "pos": "词性", "m": "句中意思"}}]}}\n\n句子：'
)

WORD_PROMPT = (
    "你是英语学习助手，面向中国中学生。请解释单词“{w}”在下面句子语境中的具体含义"
    "（中文，取最贴合本句的词义），并说明词性和一句中文的“句中意”。"
    "只输出一个 JSON 对象，不要输出任何其他内容，格式："
    '{{"meaning": "...", "pos": "..."}}\n\n句子：{s}'
)

DISCUSS_PROMPT = (
    "你是学生的 AI 读书伙伴，面向中国中学生。下面是一本英文书的“{chapter}”章节开头"
    "部分原文（可能被截断）：\n\n{context}\n\n"
    "{history}"
    "学生的问题：{question}\n\n"
    "请结合章节内容用中文回答，条理清晰、贴合原文，一般不超过 500 字。"
    "如果问题与章节内容无关，礼貌说明并建议其他讨论方向。"
)

DISTILL_PROMPT = (
    "你是这本书的共读伙伴，正在记住读过的内容。下面是一个片段（英文原文，"
    "可能包含几个段落）。请用中文提炼它，按四个层级输出一个 JSON 对象：\n"
    '{{"gist": "一句话概括（15字以内，像人的记忆标签，能让人一眼想起这段）",'
    ' "summary": "具体概括（50-80字，保留情节要点、人物、因果，比概括详细）",'
    ' "details": ["故事细节要点1", "故事细节要点2", "…"（3-6条，写具体发生了什么：'
    '谁做了什么事、结果如何、关键事实，比如交易金额、是否骗局、人物反应）"],'
    ' "quotes": [{{"text": "值得记住的原句，必须逐字抄录原文、一字不差（1-3条）",'
    ' "speaker": "说话的角色（若不是对话则为空字符串"}}],'
    ' "entities": "片段中的关键人物/地点/概念（逗号分隔，中英皆可，最多8个）"}}\n\n'
    "片段：\n{text}"
)

RECALL_TOOL = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": "回忆起此前读到的某个情节/场景。当你需要确认某个细节、"
                       "引用书中的具体原句，或感觉记忆里应该有相关内容时调用。"
                       "默认返回记忆的概括层；需要更精确的细节（谁做了什么、"
                       "交易结果等）或更多原句时，设置 detail 为 true 再调用一次。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "想回忆的内容：人物、事件、情节关键词（可用中文）",
                },
                "detail": {
                    "type": "boolean",
                    "description": "是否要更详细的内容（故事细节+全部原句），默认 false",
                },
            },
            "required": ["query"],
        },
    },
}


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


def sentence(s, ctx=""):
    return _chat(SENT_PROMPT.format(ctx=ctx) + s)


def word_insent(w, s):
    return _chat(WORD_PROMPT.format(w=w, s=s))


def discuss(question, chapter_label, context, history_pairs, timeout=300):
    hist = ""
    if history_pairs:
        lines = []
        for q, a in history_pairs[-3:]:
            lines.append("此前提问：%s\n此前回答：%s" % (q, a[:300]))
        hist = "历史对话（供参考）：\n" + "\n\n".join(lines) + "\n\n"
    return _chat(
        DISCUSS_PROMPT.format(
            chapter=chapter_label, context=context,
            history=hist, question=question,
        ),
        timeout=timeout,
    )


def distill(text, timeout=180):
    """蒸馏一个场景为四层记忆：gist / summary / details / quotes，失败抛异常。"""
    content = _chat(DISTILL_PROMPT.format(text=text[:2000]), timeout=timeout)
    d = _parse_json(content)
    if d is None:
        raise RuntimeError("蒸馏输出无法解析")
    quotes = []
    for q in (d.get("quotes") or []):
        if isinstance(q, dict) and (q.get("text") or "").strip():
            quotes.append({
                "text": q["text"].strip(),
                "speaker": (q.get("speaker") or "").strip(),
            })
    details = [str(x).strip() for x in (d.get("details") or [])
               if isinstance(x, str) and x.strip()]
    return {
        "gist": (d.get("gist") or "").strip(),
        "summary": (d.get("summary") or "").strip(),
        "details": details[:6],
        "quotes": quotes,
        "entities": (d.get("entities") or "").strip(),
    }


def chat_with_tools(messages, tools, tool_executor, timeout=300):
    """带工具调用的对话循环：模型可多次调用工具（如 recall）。

    tool_executor(name, args) -> str（工具结果文本，会被回填给模型）。
    模型不支持 tools 时自动降级为普通一次调用（预检索片段已在上下文中）。
    """
    if not ready():
        raise RuntimeError(STATE["msg"] or "LLM 未配置")
    msgs = list(messages)
    try:
        return _tools_loop(msgs, tools, tool_executor, timeout)
    except Exception as e:
        msg = str(e)
        if "tool" in msg.lower() and ("support" in msg.lower()
                                      or "400" in msg or "404" in msg):
            r = STATE["client"].chat.completions.create(
                model=STATE["model"], messages=msgs,
                temperature=0.2, timeout=timeout,
            )
            return r.choices[0].message.content or ""
        raise


def _tools_loop(msgs, tools, tool_executor, timeout):
    for _ in range(6):
        r = STATE["client"].chat.completions.create(
            model=STATE["model"],
            messages=msgs,
            tools=tools,
            temperature=0.2,
            timeout=timeout,
        )
        msg = r.choices[0].message
        if not msg.tool_calls:
            return msg.content or ""
        msgs.append(msg)
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            try:
                result = tool_executor(tc.function.name, args)
            except Exception as e:
                result = "工具调用失败：" + str(e)
            msgs.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })
    raise RuntimeError("工具调用轮次过多")


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
        obj, _end = json.JSONDecoder().raw_decode(core)
        if isinstance(obj, dict):
            return obj
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
