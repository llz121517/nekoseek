# app/api/v1/admin.py
"""
管理后台接口：用户 / 权限组 / 邀请码 / 配额 / DSH 进程（均需 admin）
"""
from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel, Field
from typing import Optional

from app.core.auth import admin_required
from app.core.db import db_op
from app.core import quota
from app.services import dsh_process, deepseek

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
    return {"code": 1, "msg": "创建成功", "data": db_op.create_group(payload.name, payload.is_admin, payload.quota_limit)}


@router.put("/groups/{group_id}")
async def update_group(group_id: int, payload: GroupUpdate):
    data = payload.model_dump(exclude_unset=True)
    if data.get("name") and db_op.get_group_by_name(data["name"]) and data["name"]:
        existing = db_op.get_group_by_name(data["name"])
        if existing and existing["id"] != group_id:
            return {"code": 0, "msg": "权限组名已存在"}
    ok = db_op.update_group(group_id, **data)
    return {"code": 1 if ok else 0, "msg": "更新成功" if ok else "未变更"}


@router.delete("/groups/{group_id}")
async def delete_group(group_id: int):
    ok = db_op.delete_group(group_id)
    return {"code": 1 if ok else 0, "msg": "删除成功" if ok else "权限组不存在"}


# ---------- 用户 ----------


class UserUpdate(BaseModel):
    password: Optional[str] = None
    group_id: Optional[int] = None
    quota_override: Optional[int] = None
    status: Optional[int] = None


@router.get("/users")
async def list_users():
    return {"code": 1, "msg": "ok", "data": db_op.list_users()}


@router.get("/users/{user_id}")
async def get_user(user_id: int):
    user = db_op.get_user_by_id(user_id)
    if user is None:
        return {"code": 0, "msg": "用户不存在"}
    user["quota_limit"] = db_op.get_effective_user_quota(user)
    user["usage"] = quota.get_user_usage(user_id)
    return {"code": 1, "msg": "ok", "data": user}


@router.put("/users/{user_id}")
async def update_user(user_id: int, payload: UserUpdate):
    # admin 之间可互相操作；禁止把最后一个 admin 降权/停用（基础保护）
    data = payload.model_dump(exclude_unset=True)
    target = db_op.get_user_by_id(user_id)
    if target is None:
        return {"code": 0, "msg": "用户不存在"}

    new_group_id = data.get("group_id", target["group_id"])
    new_status = data.get("status", target["status"])
    if _would_remove_last_admin(user_id, new_group_id, new_status):
        return {"code": 0, "msg": "不能移除最后一个管理员"}

    ok = db_op.update_user(user_id, **data)
    return {"code": 1 if ok else 0, "msg": "更新成功" if ok else "未变更"}


@router.delete("/users/{user_id}")
async def delete_user(request: Request, user_id: int):
    if request.state and getattr(request.state, "user_id", None) == user_id:
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
        return False  # 当前非 admin，不影响

    # 判断操作后是否仍为 admin
    still_admin = True
    if new_group_id is not None:
        g = db_op.get_group_by_id(new_group_id)
        still_admin = bool(g and g.get("is_admin"))
    if new_status is not None and not new_status:
        still_admin = False

    if still_admin:
        return False

    # 统计当前 admin 数量
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
    return {"code": 1, "msg": "ok", "data": db_op.list_invites()}


@router.post("/invites")
async def create_invite(request: Request, payload: InviteIn):
    cur = _current_user(request)
    invite = db_op.create_invite(
        payload.group_id, cur["id"] if cur else None, payload.max_uses, payload.expires_at
    )
    return {"code": 1, "msg": "创建成功", "data": invite}


@router.delete("/invites/{code}")
async def delete_invite(code: str):
    ok = db_op.delete_invite(code)
    return {"code": 1 if ok else 0, "msg": "删除成功" if ok else "邀请码不存在"}


# ---------- 配额 / 用量 ----------


@router.get("/quota/pool")
async def get_pool_quota():
    return {"code": 1, "msg": "ok", "data": {"usage": quota.get_pool_usage(), "limit": quota.POOL_QUOTA_LIMIT}}


@router.get("/usage")
async def get_usage():
    """全部用户的当日用量（管理视图）。"""
    data = []
    for u in db_op.list_users():
        data.append({
            "user_id": u["id"],
            "username": u["username"],
            "usage": quota.get_user_usage(u["id"]),
            "limit": db_op.get_effective_user_quota(u),
        })
    return {"code": 1, "msg": "ok", "data": data}


# ---------- DSH 进程 ----------


@router.get("/dsh/status")
async def dsh_status():
    return {"code": 1, "msg": "ok", "data": dsh_process.status()}


@router.post("/dsh/start")
async def dsh_start():
    return {"code": 1, "msg": "ok", "data": dsh_process.start()}


@router.post("/dsh/stop")
async def dsh_stop():
    return {"code": 1, "msg": "ok", "data": dsh_process.stop()}


# ---------- DeepSeek 逆向接口 ----------


@router.get("/deepseek/balance")
async def deepseek_balance():
    return {"code": 1, "msg": "ok", "data": await deepseek.get_balance()}


@router.post("/deepseek/keys")
async def deepseek_create_key(name: str):
    return {"code": 1, "msg": "ok", "data": await deepseek.create_api_key(name)}


def _current_user(request: Request) -> dict | None:
    from app.core.auth import get_current_user
    try:
        return get_current_user(request)
    except Exception:
        return None
