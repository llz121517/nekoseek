# app/core/attribution.py
"""
用量归属映射：sessionId → 发起 prompt 的用户。

为何需要：DSH 的 mux 事件流（usage_meter 消费的 WS 下行）只带 sessionId，
不带用户身份（DSH 本身无用户概念）。而 prompt 只走 HTTP（DSH 协议规定
WS 下行是纯推送），代理层在转发 /api/session.prompt /api/subagent.prompt
时顺手记下 sessionId → user_id，计量时按帧里的 sessionId 找回真正发起者，
避免按"谁的连接收到帧"误记到闲置用户头上。

进程内内存态：网关重启即丢失，丢失后走 quota.record_global_usage 兜底
（只记全局池，不冤枉任何个人）。单 asyncio 循环内使用，无需锁。
"""
import time

# DSH 会话可长期存续并被继续，归属 TTL 给足 7 天。
_OWNER_TTL_SEC = 7 * 24 * 3600
# 惰性清扫阈值：条目数超过才在写入时扫一次过期项，避免每次写入全扫。
_SWEEP_THRESHOLD = 1024

# 归属转移策略（内部快捷开关，不进 config）：
# True  = 插话者接管——每次 prompt 覆盖归属（last writer wins）。
#         语义：插话者从当下起驱动会话，后续用量记到插话者。
# False = 先入为主——session 首个 prompt 的发起者永久拥有，他人插话不转移归属。
#         注意此时插话产生的真实消耗会记到首建者头上。
TRANSFER_ON_PROMPT = True

# sessionId -> (user_id, expires_at)
_owners: dict[str, tuple[int, float]] = {}


def record_prompt_session(session_id: str, user_id: int) -> None:
    """记录某次 prompt 的发起者，供后续该 session 的用量帧归属。"""
    if not session_id or not user_id:
        return
    # 先入为主模式：已有归属的 session 不被插话者接管（过期条目视为无归属，可重建）。
    if not TRANSFER_ON_PROMPT and resolve_owner(session_id) is not None:
        return
    now = time.time()
    if len(_owners) >= _SWEEP_THRESHOLD:
        expired = [k for k, (_, exp) in _owners.items() if exp <= now]
        for k in expired:
            del _owners[k]
    _owners[session_id] = (user_id, now + _OWNER_TTL_SEC)


def resolve_owner(session_id: str) -> int | None:
    """取 session 的发起者 user_id；无记录或已过期返回 None（走全局池兜底）。"""
    entry = _owners.get(session_id)
    if entry is None:
        return None
    user_id, expires_at = entry
    if expires_at <= time.time():
        del _owners[session_id]
        return None
    return user_id
