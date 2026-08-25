# app/core/security.py
"""
密码哈希（PBKDF2-HMAC-SHA256，加盐）与邀请码生成
"""
import hashlib
import hmac
import secrets

PBKDF2_ITERATIONS = 200_000
SALT_BYTES = 16

# 邀请码字符集（去掉易混淆字符 0/O/1/I/L）
INVITE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
INVITE_LENGTH = 16


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """
    使用 PBKDF2-HMAC-SHA256 生成加盐密码哈希。

    :param password: 明文密码
    :param salt: 十六进制盐（缺省则随机生成）
    :return: (digest_hex, salt_hex)
    """
    if salt is None:
        salt = secrets.token_hex(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PBKDF2_ITERATIONS,
    ).hex()
    return digest, salt


def verify_password(password: str, pwd_hash: str, salt: str) -> bool:
    """
    校验密码是否匹配，使用常量时间比较防时序侧信道。
    """
    calc, _ = hash_password(password, salt)
    return hmac.compare_digest(calc, pwd_hash)


def generate_invite_code(length: int = INVITE_LENGTH) -> str:
    """
    生成可读邀请码（大写字母+数字，去易混淆字符）。
    """
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(length))
