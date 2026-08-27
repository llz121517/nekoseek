"""口令哈希与邀请码生成的单元测试。"""
from app.core import security


class TestHashPassword:
    def test_deterministic_with_given_salt(self):
        h1, salt = security.hash_password("secret", salt="ab" * 16)
        h2, _ = security.hash_password("secret", salt=salt)
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex

    def test_random_salt_differs(self):
        h1, s1 = security.hash_password("secret")
        h2, s2 = security.hash_password("secret")
        assert s1 != s2 or h1 != h2  # 随机盐几乎必然不同

    def test_salt_length(self):
        _, salt = security.hash_password("x")
        assert len(salt) == security.SALT_BYTES * 2  # hex


class TestVerifyPassword:
    def test_correct_password(self):
        h, salt = security.hash_password("hunter2")
        assert security.verify_password("hunter2", h, salt) is True

    def test_wrong_password(self):
        h, salt = security.hash_password("hunter2")
        assert security.verify_password("hunter3", h, salt) is False


class TestInviteCode:
    def test_length_and_alphabet(self):
        code = security.generate_invite_code()
        assert len(code) == security.INVITE_LENGTH
        assert all(c in security.INVITE_ALPHABET for c in code)

    def test_custom_length(self):
        assert len(security.generate_invite_code(8)) == 8

    def test_no_confusing_chars(self):
        # 生成若干次，确认不含 0/O/1/I/L
        for _ in range(50):
            code = security.generate_invite_code()
            assert not set(code) & set("0O1IL")
