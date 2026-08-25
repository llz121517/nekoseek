# app/core/tokenize.py
"""
粗略分词估算 tokens（前端输入/输出文本 → token 估算）
"""
import re

_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def estimate_tokens(text: str, cjk_per_char: float, latin_per_word: float) -> int:
    """
    粗略估算文本 token 数：
    - 中日韩字符：每字计 cjk_per_char
    - 拉丁/数字单词：每词计 latin_per_word
    - 其他字符（标点、空白）忽略，避免重复计
    """
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    latin_words = _LATIN_WORD_RE.findall(text)
    return int(round(cjk_count * cjk_per_char + len(latin_words) * latin_per_word))


def estimate_prompt(messages: list[dict], cjk_per_char: float, latin_per_word: float) -> int:
    """
    从 OpenAI 兼容 messages 列表估算输入 token（合并 role + content）。
    """
    total = 0
    for m in messages or []:
        content = m.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content, cjk_per_char, latin_per_word)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    total += estimate_tokens(part["text"], cjk_per_char, latin_per_word)
    return total
