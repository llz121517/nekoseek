# app/core/replay.py
"""
prompt RPC 的窗口去重防重放。

catch-all 把已登录用户的 prompt 请求透明转发给 DSH，上游不做幂等。
抓包拿到一次合法 prompt 的（cookie + 请求体）后可无限整包重放，
每次都真实消耗模型费用与配额（经济型 DoS）。

这里对 prompt 端点做短窗口去重：对 (用户, method, path, body) 取指纹，
同一指纹在窗口期内再次出现即判为重放并拒绝。窗口期外的相同请求
（用户主动重发同一 prompt）不受影响。进程内内存实现，单进程网关足够。
"""
import hashlib
import threading
import time

_lock = threading.Lock()
# fingerprint -> 过期时间戳
_seen: dict[str, float] = {}

# 去重窗口（秒）：只挡"抓包后立即重放"，不影响用户稍后主动重发相同 prompt。
_REPLAY_TTL = 15.0


def _prune(now: float) -> None:
    for k in list(_seen):
        if _seen[k] <= now:
            del _seen[k]


def fingerprint(user_id: int, method: str, path: str, body: bytes) -> str:
    """请求指纹：用户 + 方法 + 路径 + 请求体 的哈希。"""
    h = hashlib.sha256()
    h.update(str(user_id).encode())
    h.update(b"|")
    h.update(method.encode())
    h.update(b"|")
    h.update(path.encode())
    h.update(b"|")
    h.update(body)
    return h.hexdigest()


def is_replay(fp: str) -> bool:
    """
    登记一个请求指纹；窗口期内已存在则返回 True（判重放），否则记录并返回 False。
    """
    now = time.time()
    with _lock:
        _prune(now)
        if fp in _seen:
            return True
        _seen[fp] = now + _REPLAY_TTL
        return False
