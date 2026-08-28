"""粗略分词估算 token 的单元测试。"""
from app.core import tokenize


class TestEstimateTokens:
    def test_empty(self):
        assert tokenize.estimate_tokens("") == 0
        assert tokenize.estimate_tokens(None) == 0

    def test_cjk_counts_per_char(self):
        # 5 个汉字，每字权重 1.0
        assert tokenize.estimate_tokens("你好世界啊") == 5

    def test_latin_counts_per_word(self):
        # 4 个拉丁词，每词权重 1.3 → round(5.2)=5
        assert tokenize.estimate_tokens("hello world foo bar") == round(4 * 1.3)

    def test_mixed(self):
        # “你好”(2) + "hello"(1*1.3) → round(3.3)=3
        assert tokenize.estimate_tokens("你好hello") == round(2 * 1.0 + 1 * 1.3)

    def test_punctuation_ignored(self):
        assert tokenize.estimate_tokens("，。！？  ") == 0
