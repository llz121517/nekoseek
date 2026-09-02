# app/core/quota.py
"""
两级配额记账：全局池 + 单用户，按可切换窗口（5h/day/week/month）聚合。
超限统一由调用方处理（代理层返回 429）。
"""
import logging
import threading
import time
from datetime import datetime, timedelta

from app.core.db import db_op
from app.core.db import stats_op

logger = logging.getLogger("nekoseek.quota")

WINDOW_KINDS = ("5h", "day", "week", "month")
_FIVE_HOURS_SEC = 5 * 3600


# ---------- 短窗口预订（防并发 prompt 绕过预检） ----------
#
# 预检查的是"记账之前"的用量，而真实记账依赖 DSH 完成后的 mux 下行帧（异步、滞后）。
# 用户在检查通过后立即并发多个 prompt，会全部通过预检、事后才超扣——429 形同虚设。
# 这里在预检通过时写入一条短窗口预订（pending），后续并发检查会把 pending 计入，
# 从而在记账落库前就先挡住超发。预订到期自动失效（防泄漏），方向是"宁可保守多挡"。

_pending_lock = threading.Lock()
# user_id -> [(expire_ts, tokens), ...]；user_id=0 表示全局池
_pending: dict[int, list[tuple[float, int]]] = {}
_PENDING_TTL = 120.0  # 预订有效期（秒）：覆盖一轮 prompt 从发起到记账的典型时延


def _prune_pending(now: float) -> None:
    for uid in list(_pending):
        lst = [(e, t) for (e, t) in _pending[uid] if e > now]
        if lst:
            _pending[uid] = lst
        else:
            del _pending[uid]


def estimate_prompt_cost(body: bytes) -> int:
    """
    prompt 成本的保守预估，用于预检时的短窗口预订。
    真实成本在 DSH 完成后才知道；此处按请求体大小估算输入并附加基础输出余量。
    """
    return max(1024, len(body) // 4 + 1024)


def reserve_prompt(user_id: int, tokens: int) -> None:
    """预检通过后登记一条短窗口预订（个人 + 全局池各一份）。"""
    if tokens <= 0:
        return
    now = time.time()
    exp = now + _PENDING_TTL
    with _pending_lock:
        _prune_pending(now)
        _pending.setdefault(user_id, []).append((exp, tokens))
        _pending.setdefault(0, []).append((exp, tokens))


def _pending_sum(user_id: int) -> int:
    now = time.time()
    with _pending_lock:
        _prune_pending(now)
        return sum(t for (_, t) in _pending.get(user_id, ()))


# ---------- 窗口计算 ----------


def get_window_kind() -> str:
    """
    当前生效的窗口类型；settings 缺失/非法时回落到 day。
    """
    kind = (db_op.get_setting("quota_window", "day") or "day").strip().lower()
    return kind if kind in WINDOW_KINDS else "day"


def set_window_kind(kind: str) -> None:
    """
    切换窗口类型。会把旧窗口的用量"携带"到新窗口：把旧窗口累计值
    作为新窗口的初始量写入，避免用户跨窗口重复占用额度。
    """
    kind = (kind or "").strip().lower()
    if kind not in WINDOW_KINDS:
        raise ValueError(f"非法窗口类型: {kind}")

    old_kind = get_window_kind()
    if old_kind == kind:
        return

    now = time.time()
    old_ws = current_window_start(old_kind, now)
    new_ws = current_window_start(kind, now)

    # 把旧窗口的每一行（含全局 user_id=0）写入新窗口作为起点
    conn_rows = db_op.list_window_usage(old_ws) + [db_op.get_usage(0, old_ws)]
    for row in conn_rows:
        total = row.get("total_tokens", 0)
        if total <= 0:
            continue
        existing = db_op.get_usage(row["user_id"], new_ws)
        # 已存在则跳过（避免重复携带）；否则把旧值作为新窗口起点
        if existing.get("total_tokens", 0) > 0:
            continue
        db_op.add_usage(
            row["user_id"],
            new_ws,
            row.get("input_tokens", 0),
            row.get("output_tokens", 0),
        )

    db_op.set_setting("quota_window", kind)


def reset_current_window_usage() -> int:
    """
    清空当前窗口下所有用户与全局池的用量，返回清理的行数。
    """
    kind = get_window_kind()
    ws = current_window_start(kind)
    return db_op.delete_window_usage(ws)


def current_window_start(kind: str | None = None, now: float | None = None) -> int:
    """
    当前窗口起点 epoch 秒。
    - 5h:    固定 5 小时对齐 (now - now % 18000)
    - day:   本地当日 0 点
    - week:  本地周一 0 点
    - month: 本地当月 1 号 0 点
    """
    kind = kind or get_window_kind()
    now = now if now is not None else time.time()
    if kind == "5h":
        return int(now - (now % _FIVE_HOURS_SEC))
    dt = datetime.fromtimestamp(now)
    if kind == "day":
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elif kind == "week":
        monday = dt - timedelta(days=dt.weekday())
        start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    elif kind == "month":
        start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


# ---------- 限额 ----------


def get_global_limit() -> int:
    """
    全局池限额，0 = 不限。
    """
    try:
        return int(db_op.get_setting("global_quota_limit", "0") or 0)
    except ValueError:
        return 0


def set_global_limit(limit: int) -> None:
    db_op.set_setting("global_quota_limit", str(max(0, int(limit))))


def get_user_limit(user: dict) -> int:
    """
    用户有效窗口配额：quota_override → 组 quota_limit → 0（不限）。
    """
    return db_op.get_effective_user_quota(user)


# ---------- 用量读写 ----------


def _window_start_now() -> int:
    return current_window_start(get_window_kind())


def record_usage(user_id: int, input_tokens: int = 0, output_tokens: int = 0) -> None:
    """
    同一窗口下双写：用户行 + user_id=0 的全局聚合行。
    数据库不可用时静默降级（仅记日志），避免计量故障影响代理链路。
    """
    if input_tokens <= 0 and output_tokens <= 0:
        return
    ws = _window_start_now()
    try:
        db_op.add_usage(user_id, ws, input_tokens, output_tokens)
        db_op.add_usage(0, ws, input_tokens, output_tokens)
    except Exception as e:  # noqa: BLE001
        logger.warning("配额记账失败 user_id=%s: %r", user_id, e)
    # 详细用量统计：独立 stats.db，与配额记账互不影响（统计失败绝不拖累代理链路）
    try:
        stats_op.add_hourly(user_id, stats_op.hour_bucket(), input_tokens, output_tokens)
    except Exception as e:  # noqa: BLE001
        logger.warning("用量统计记账失败 user_id=%s: %r", user_id, e)


def record_global_usage(input_tokens: int = 0, output_tokens: int = 0) -> None:
    """
    无归属用量的兜底：只累计全局池（user_id=0），不计任何个人。
    不写 stats 明细——stats_op 设计上只存个人行，全局值由 SQL 聚合得出。
    数据库不可用时静默降级（仅记日志），避免计量故障影响代理链路。
    """
    if input_tokens <= 0 and output_tokens <= 0:
        return
    ws = _window_start_now()
    try:
        db_op.add_usage(0, ws, input_tokens, output_tokens)
    except Exception as e:  # noqa: BLE001
        logger.warning("全局池记账失败: %r", e)


def get_user_usage(user_id: int, window_start: int | None = None) -> dict:
    ws = window_start if window_start is not None else _window_start_now()
    return db_op.get_usage(user_id, ws)


def get_global_usage(window_start: int | None = None) -> dict:
    ws = window_start if window_start is not None else _window_start_now()
    return db_op.get_usage(0, ws)


def list_current_window_users() -> list[dict]:
    return db_op.list_window_usage(_window_start_now())


# ---------- 检查 ----------


def check_user_quota(user: dict) -> bool:
    """
    用户是否仍有剩余配额。limit 为 0 表示不限。
    已记账用量 + 在途预订（pending）一起计入，防止并发 prompt 绕过预检。
    """
    limit = get_user_limit(user)
    if limit <= 0:
        return True
    used = get_user_usage(user["id"])["total_tokens"] + _pending_sum(user["id"])
    return used < limit


def check_global_quota() -> bool:
    """
    全局池是否仍有剩余配额。limit 为 0 表示不限。
    已记账用量 + 在途预订（pending）一起计入，防止并发 prompt 绕过预检。
    """
    limit = get_global_limit()
    if limit <= 0:
        return True
    used = get_global_usage()["total_tokens"] + _pending_sum(0)
    return used < limit


# ---------- 面板数据 ----------


def quota_summary(user: dict) -> dict:
    """
    供注入 JS 轮询的配额概览：全局池 + 单用户 + 当前窗口。
    """
    kind = get_window_kind()
    ws = current_window_start(kind)
    user_usage = db_op.get_usage(user["id"], ws)
    pool_usage = db_op.get_usage(0, ws)
    user_limit = get_user_limit(user)
    pool_limit = get_global_limit()
    return {
        "window": kind,
        "user": {
            "used": user_usage["total_tokens"],
            "limit": user_limit,
            "remaining": max(0, user_limit - user_usage["total_tokens"]) if user_limit > 0 else None,
        },
        "pool": {
            "used": pool_usage["total_tokens"],
            "limit": pool_limit,
            "remaining": max(0, pool_limit - pool_usage["total_tokens"]) if pool_limit > 0 else None,
        },
    }
