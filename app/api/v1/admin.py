# app/api/v1/admin.py
"""
管理后台接口：用户 / 权限组 / 邀请码 / DSH 进程（均需 admin）
"""
from typing import Optional
import time

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.auth import admin_required, get_current_user
from app.core import quota
from app.core.db import db_op, stats_op
from app.services import dsh_process, ds_balance

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin", "api_v1"],
    dependencies=[Depends(admin_required)],
)


# ---------- 权限组 ----------


class GroupIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=32)
    is_admin: bool = False
    quota_limit: int = 0


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    is_admin: Optional[bool] = None
    quota_limit: Optional[int] = None


@router.get("/groups")
async def list_groups():
    return {"code": 1, "msg": "ok", "data": db_op.list_groups()}


@router.post("/groups")
async def create_group(payload: GroupIn):
    if db_op.get_group_by_name(payload.name):
        return {"code": 0, "msg": "权限组已存在"}
    return {
        "code": 1,
        "msg": "创建成功",
        "data": db_op.create_group(payload.name, payload.is_admin, payload.quota_limit),
    }


@router.put("/groups/{group_id}")
async def update_group(group_id: int, payload: GroupUpdate):
    data = payload.model_dump(exclude_unset=True)
    if data.get("name"):
        existing = db_op.get_group_by_name(data["name"])
        if existing and existing["id"] != group_id:
            return {"code": 0, "msg": "权限组名已存在"}
    ok = db_op.update_group(group_id, **data)
    return {"code": 1 if ok else 0, "msg": "更新成功" if ok else "未变更"}


@router.delete("/groups/{group_id}")
async def delete_group(group_id: int):
    user_count = db_op.count_users_in_group(group_id)
    if user_count > 0:
        return {"code": 0, "msg": f"该权限组仍有 {user_count} 个用户，请先迁移后再删除"}
    invite_count = db_op.count_invites_in_group(group_id)
    if invite_count > 0:
        return {"code": 0, "msg": f"该权限组仍被 {invite_count} 个邀请码引用，请先删除或迁移后再删除"}
    ok = db_op.delete_group(group_id)
    return {"code": 1 if ok else 0, "msg": "删除成功" if ok else "权限组不存在"}


# ---------- 用户 ----------


class UserUpdate(BaseModel):
    password: Optional[str] = None
    group_id: Optional[int] = None
    quota_override: Optional[int] = None
    status: Optional[int] = None


def _safe_user(user: dict) -> dict:
    """剥离敏感字段后返回用户信息。"""
    out = dict(user)
    out.pop("pwd_hash", None)
    out.pop("salt", None)
    return out


@router.get("/users")
async def list_users():
    users = db_op.list_users()
    groups = {g["id"]: g for g in db_op.list_groups()}
    out = []
    for u in users:
        safe = _safe_user(u)
        safe["is_admin"] = bool(groups.get(u["group_id"], {}).get("is_admin"))
        out.append(safe)
    return {"code": 1, "msg": "ok", "data": out}


@router.get("/users/{user_id}")
async def get_user(user_id: int):
    user = db_op.get_user_by_id(user_id)
    if user is None:
        return {"code": 0, "msg": "用户不存在"}
    user = _safe_user(user)
    user["quota_limit"] = db_op.get_effective_user_quota(user)
    return {"code": 1, "msg": "ok", "data": user}


@router.put("/users/{user_id}")
async def update_user(user_id: int, payload: UserUpdate):
    # 不能用 exclude_unset：quota_override 显式传 null 表示"清除覆写、继承组"，
    # 必须区分"没传"和"传了 null"。
    data = payload.model_dump(exclude_unset=True)
    if "quota_override" in payload.model_fields_set:
        data["quota_override"] = payload.quota_override  # 可能是 None
    target = db_op.get_user_by_id(user_id)
    if target is None:
        return {"code": 0, "msg": "用户不存在"}

    new_group_id = data.get("group_id", target["group_id"])
    new_status = data.get("status", target["status"])
    if _would_remove_last_admin(user_id, new_group_id, new_status):
        return {"code": 0, "msg": "不能移除最后一个管理员"}

    ok = db_op.update_user(
        user_id,
        password=data.get("password"),
        group_id=data.get("group_id"),
        status=data.get("status"),
        quota_override=data["quota_override"] if "quota_override" in data else db_op.UNSET,
    )
    return {"code": 1 if ok else 0, "msg": "更新成功" if ok else "未变更"}


@router.delete("/users/{user_id}")
async def delete_user(request: Request, user_id: int):
    current = get_current_user(request)
    if current["id"] == user_id:
        return {"code": 0, "msg": "不能删除自己"}
    if _would_remove_last_admin(user_id, None, 0):
        return {"code": 0, "msg": "不能移除最后一个管理员"}
    ok = db_op.delete_user(user_id)
    return {"code": 1 if ok else 0, "msg": "删除成功" if ok else "用户不存在"}


def _would_remove_last_admin(user_id: int, new_group_id: int | None, new_status: int | None) -> bool:
    """
    若目标用户是 admin，且此操作会使其失去 admin 权限/停用/删除，
    且这是最后一个 admin，则返回 True。
    """
    target = db_op.get_user_by_id(user_id)
    if target is None:
        return False
    group = db_op.get_group_by_id(target["group_id"])
    if group is None or not group.get("is_admin"):
        return False

    still_admin = True
    if new_group_id is not None:
        g = db_op.get_group_by_id(new_group_id)
        still_admin = bool(g and g.get("is_admin"))
    if new_status is not None and not new_status:
        still_admin = False

    if still_admin:
        return False

    admins = 0
    for u in db_op.list_users():
        ug = db_op.get_group_by_id(u["group_id"])
        if ug and ug.get("is_admin") and u.get("status"):
            admins += 1
    return admins <= 1


# ---------- 邀请码 ----------


class InviteIn(BaseModel):
    group_id: int = Field(..., gt=0)
    max_uses: int = 1
    expires_at: Optional[str] = None


@router.get("/invites")
async def list_invites():
    invites = db_op.list_invites()
    for inv in invites:
        inv["used_by_users"] = db_op.list_invite_usernames(inv["id"])
    return {"code": 1, "msg": "ok", "data": invites}


@router.post("/invites")
async def create_invite(request: Request, payload: InviteIn):
    current = get_current_user(request)
    invite = db_op.create_invite(
        payload.group_id, current["id"], payload.max_uses, payload.expires_at
    )
    return {"code": 1, "msg": "创建成功", "data": invite}


@router.delete("/invites/{code}")
async def delete_invite(code: str):
    ok = db_op.delete_invite(code)
    return {"code": 1 if ok else 0, "msg": "删除成功" if ok else "邀请码不存在"}


# ---------- DSH 进程 ----------


@router.get("/dsh/status")
async def dsh_status():
    return {"code": 1, "msg": "ok", "data": dsh_process.status()}


@router.post("/dsh/start")
async def dsh_start():
    try:
        return {"code": 1, "msg": "ok", "data": dsh_process.start()}
    except dsh_process.DSHIsolationError as e:
        return {"code": 0, "msg": f"DSH 隔离不可用：{e}", "data": None}


@router.post("/dsh/stop")
async def dsh_stop():
    return {"code": 1, "msg": "ok", "data": dsh_process.stop()}


# ---------- 配额 ----------


class QuotaSettingsIn(BaseModel):
    window: Optional[str] = None          # 5h | day | week | month
    global_limit: Optional[int] = None    # 0 = 不限


@router.get("/quota/settings")
async def get_quota_settings():
    return {
        "code": 1,
        "msg": "ok",
        "data": {
            "window": quota.get_window_kind(),
            "global_limit": quota.get_global_limit(),
            "window_kinds": list(quota.WINDOW_KINDS),
        },
    }


@router.put("/quota/settings")
async def update_quota_settings(payload: QuotaSettingsIn):
    data = payload.model_dump(exclude_unset=True)
    if "window" in data and data["window"] is not None:
        try:
            quota.set_window_kind(data["window"])
        except ValueError as e:
            return {"code": 0, "msg": str(e)}
    if "global_limit" in data and data["global_limit"] is not None:
        quota.set_global_limit(data["global_limit"])
    return {
        "code": 1,
        "msg": "更新成功",
        "data": {
            "window": quota.get_window_kind(),
            "global_limit": quota.get_global_limit(),
        },
    }


@router.get("/quota/usage")
async def get_quota_usage():
    """
    当前窗口用量：全局池 + 各用户。
    """
    kind = quota.get_window_kind()
    ws = quota.current_window_start(kind)
    pool = db_op.get_usage(0, ws)
    users = db_op.list_window_usage(ws)
    # 附带用户名，便于后台展示
    name_by_id = {u["id"]: u["username"] for u in db_op.list_users()}
    for u in users:
        u["username"] = name_by_id.get(u["user_id"], f"#{u['user_id']}")
    return {
        "code": 1,
        "msg": "ok",
        "data": {
            "window": kind,
            "window_start": ws,
            "pool": pool,
            "users": users,
        },
    }


@router.post("/quota/reset")
async def reset_quota_usage():
    """
    清空当前窗口下的所有用量（用户 + 全局池）。
    """
    n = quota.reset_current_window_usage()
    return {"code": 1, "msg": f"已重置 {n} 行用量", "data": {"deleted": n}}


@router.get("/deepseek/balance")
async def deepseek_balance():
    """
    查询 DeepSeek 账户余额，供管理后台概览展示。
    """
    data = await ds_balance.fetch_balance()
    return {"code": 1 if data["ok"] else 0, "msg": "ok" if data["ok"] else (data["error"] or "查询失败"), "data": data}


# ---------- 详细用量统计（独立 stats.db，小时×用户明细） ----------


def _clamp_days(days: int | None) -> int:
    try:
        d = int(days) if days is not None else 1
    except (TypeError, ValueError):
        d = 1
    return max(1, min(30, d))


@router.get("/stats/overview")
async def get_stats_overview():
    """
    概览统计：今日用量 + 累计用量（含活跃用户数）。
    """
    now = int(time.time())
    today = stats_op.sum_range(stats_op.today_start_ts(), now + 1)
    all_time = stats_op.sum_all_time()
    return {"code": 1, "msg": "ok", "data": {"today": today, "all_time": all_time}}


@router.get("/stats/hourly")
async def get_stats_hourly(days: Optional[int] = None):
    """
    逐小时用量序列（全用户聚合），服务端零填充到整点，供折线/面积图直接使用。
    """
    d = _clamp_days(days)
    now_bucket = stats_op.hour_bucket()
    start_bucket = now_bucket - (d * 24 - 1) * 3600
    rows = stats_op.hourly_series(start_bucket, now_bucket + 3600)
    by_hour = {r["hour_start"]: r for r in rows}
    points = []
    for h in range(start_bucket, now_bucket + 1, 3600):
        r = by_hour.get(h)
        points.append({
            "ts": h,
            "input_tokens": r["input_tokens"] if r else 0,
            "output_tokens": r["output_tokens"] if r else 0,
            "total_tokens": r["total_tokens"] if r else 0,
        })
    return {
        "code": 1,
        "msg": "ok",
        "data": {"days": d, "start": start_bucket, "end": now_bucket, "points": points},
    }


@router.get("/stats/by_user")
async def get_stats_by_user(days: Optional[int] = None):
    """
    时间范围内按用户聚合的用量排行（含用户名）。
    """
    d = _clamp_days(days)
    now = int(time.time())
    users = stats_op.sum_by_user(now - d * 86400, now + 1)
    name_by_id = {u["id"]: u["username"] for u in db_op.list_users()}
    for u in users:
        u["username"] = name_by_id.get(u["user_id"], f"#{u['user_id']}")
    return {"code": 1, "msg": "ok", "data": {"days": d, "users": users}}
