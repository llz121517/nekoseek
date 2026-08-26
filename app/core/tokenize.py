# app/core/tokenize.py
"""
粗略分词估算 tokens（前端输入/输出文本 → token 估算）
"""
import re

from app.config import QUOTA_CJK_PER_CHAR, QUOTA_LATIN_PER_WORD

_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def estimate_tokens(text: str) -> int:
    """
    粗略估算文本 token 数：
    - 中日韩字符：每字计 QUOTA_CJK_PER_CHAR
    - 拉丁/数字单词：每词计 QUOTA_LATIN_PER_WORD
    - 其他字符（标点、空白）忽略，避免重复计
    """
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    latin_words = _LATIN_WORD_RE.findall(text)
    return int(round(cjk_count * QUOTA_CJK_PER_CHAR + len(latin_words) * QUOTA_LATIN_PER_WORD))


def estimate_prompt_parts(parts: list) -> int:
    """
    从 DSH PromptContentPart 列表估算输入 token：
    [{type:'text', text}, {type:'image', data}] —— image 部分不计。
    """
    total = 0
    for part in parts or []:
        if isinstance(part, dict) and part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str):
                total += estimate_tokens(text)
    return total
