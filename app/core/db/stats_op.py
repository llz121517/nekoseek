# app/core/db/stats_op.py
"""
详细用量统计 CRUD：小时 × 用户 明细（stats.db，独立于窗口化配额库）。
只存明细行，全局/合计值一律 SQL 聚合，不写 user_id=0 的全局行。
"""
import time
from datetime import datetime

from app.core.db.db import get_stats_conn


def hour_bucket(ts: float | None = None) -> int:
    """epoch 秒向下取整到整点（%3600 对齐）。"""
    now = ts if ts is not None else time.time()
    return int(now - (now % 3600))


def today_start_ts() -> int:
    """本地今日 0 点的 epoch 秒。"""
    d = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp())


def add_hourly(user_id: int, hour_start: int, input_tokens: int, output_tokens: int) -> None:
    """累加某用户某小时桶用量（UPSERT）。"""
    total = input_tokens + output_tokens
    conn = get_stats_conn()
    conn.execute(
        """
        INSERT INTO usage_hourly (user_id, hour_start, input_tokens, output_tokens, total_tokens, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, hour_start) DO UPDATE SET
            input_tokens  = input_tokens  + excluded.input_tokens,
            output_tokens = output_tokens + excluded.output_tokens,
            total_tokens  = total_tokens  + excluded.total_tokens,
            updated_at    = excluded.updated_at
        """,
        (user_id, hour_start, input_tokens, output_tokens, total, int(time.time())),
    )
    conn.commit()


def sum_range(start_ts: int, end_ts: int) -> dict:
    """聚合 [start_ts, end_ts) 内全用户用量 + 活跃用户数。"""
    row = get_stats_conn().execute(
        """
        SELECT COALESCE(SUM(input_tokens), 0)  AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(total_tokens), 0)  AS total_tokens,
               COUNT(DISTINCT user_id)         AS active_users
        FROM usage_hourly
        WHERE hour_start >= ? AND hour_start < ?
        """,
        (start_ts, end_ts),
    ).fetchone()
    return dict(row)


def sum_all_time() -> dict:
    """聚合全部历史用量 + 活跃用户数 + 最早一条记录的小时起点 since。"""
    row = get_stats_conn().execute(
        """
        SELECT COALESCE(SUM(input_tokens), 0)  AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(total_tokens), 0)  AS total_tokens,
               COUNT(DISTINCT user_id)         AS active_users,
               MIN(hour_start)                 AS since
        FROM usage_hourly
        """
    ).fetchone()
    return dict(row)


def sum_by_user(start_ts: int, end_ts: int) -> list[dict]:
    """[start_ts, end_ts) 内按用户聚合，按合计降序。"""
    rows = get_stats_conn().execute(
        """
        SELECT user_id,
               SUM(input_tokens)  AS input_tokens,
               SUM(output_tokens) AS output_tokens,
               SUM(total_tokens)  AS total_tokens
        FROM usage_hourly
        WHERE hour_start >= ? AND hour_start < ?
        GROUP BY user_id
        ORDER BY total_tokens DESC
        """,
        (start_ts, end_ts),
    ).fetchall()
    return [dict(r) for r in rows]


def hourly_series(start_ts: int, end_ts: int, user_id: int | None = None) -> list[dict]:
    """
    逐小时聚合序列（默认全用户，可指定单用户）。只返回有数据的小时行；
    零填充由调用方负责。
    """
    sql = """
        SELECT hour_start,
               SUM(input_tokens)  AS input_tokens,
               SUM(output_tokens) AS output_tokens,
               SUM(total_tokens)  AS total_tokens
        FROM usage_hourly
        WHERE hour_start >= ? AND hour_start < ?
        {uf}
        GROUP BY hour_start
        ORDER BY hour_start
    """.format(uf="AND user_id = ?" if user_id is not None else "")
    params = (start_ts, end_ts, user_id) if user_id is not None else (start_ts, end_ts)
    rows = get_stats_conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]
