# app/core/tokenize.py
"""
粗略分词估算 tokens：mux 事件帧缺失 usage 时的兜底估算。
仅用于 usage_meter 对 assistant 输出文本的近似计量（此时输入计 0）；
输入/输出的正式计量统一取真实 usage（见 usage_meter.py），不再用于请求体输入估算。
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
