# app/api/v1/admin.py
"""
管理后台接口：用户 / 权限组 / 邀请码 / DSH 进程（均需 admin）
"""
from typing import Optional
import logging
import os
import time
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from app.config import ROOT
from app.core import audit, quota, session
from app.core.auth import admin_required, get_current_user
from app.core.db import audit_op, db_op, stats_op
from app.services import dsh_process, ds_balance

logger = logging.getLogger("nekoseek.admin")

ENV_PATH = ROOT / ".env"

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin", "api_v1"],
    dependencies=[Depends(admin_required)],
)


# ---------- 权限组 ----------


class GroupIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=32)
    is_admin: bool = False
    quota_limit: int = Field(0, ge=0)  # 0 = 不限；负数无意义，拒绝


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    is_admin: Optional[bool] = None
    quota_limit: Optional[int] = Field(None, ge=0)


@router.get("/groups")
async def list_groups():
    return {"code": 1, "msg": "ok", "data": db_op.list_groups()}


@router.post("/groups")
async def create_group(request: Request, payload: GroupIn):
    if db_op.get_group_by_name(payload.name):
        audit.record(request, "group.create", f"创建权限组失败（已存在）：{payload.name}", level="warning")
        return {"code": 0, "msg": "权限组已存在"}
    group = db_op.create_group(payload.name, payload.is_admin, payload.quota_limit)
    audit.record(request, "group.create", f"创建权限组 {payload.name}（admin={payload.is_admin}, 限额={payload.quota_limit}）")
    return {"code": 1, "msg": "创建成功", "data": group}


@router.put("/groups/{group_id}")
async def update_group(request: Request, group_id: int, payload: GroupUpdate):
    data = payload.model_dump(exclude_unset=True)
    if data.get("name"):
        existing = db_op.get_group_by_name(data["name"])
        if existing and existing["id"] != group_id:
            audit.record(request, "group.update", f"更新权限组 #{group_id} 失败（名称已存在）：{data['name']}", level="warning")
            return {"code": 0, "msg": "权限组名已存在"}
    ok = db_op.update_group(group_id, **data)
    detail = f"更新权限组 #{group_id}：{data}" if ok else f"更新权限组 #{group_id}：未变更"
    audit.record(request, "group.update", detail, level="info" if ok else "warning")
    return {"code": 1 if ok else 0, "msg": "更新成功" if ok else "未变更"}


@router.delete("/groups/{group_id}")
async def delete_group(request: Request, group_id: int):
    user_count = db_op.count_users_in_group(group_id)
    if user_count > 0:
        audit.record(request, "group.delete", f"删除权限组 #{group_id} 被拒：仍有 {user_count} 个用户", level="warning")
        return {"code": 0, "msg": f"该权限组仍有 {user_count} 个用户，请先迁移后再删除"}
    invite_count = db_op.count_invites_in_group(group_id)
    if invite_count > 0:
        audit.record(request, "group.delete", f"删除权限组 #{group_id} 被拒：被 {invite_count} 个邀请码引用", level="warning")
        return {"code": 0, "msg": f"该权限组仍被 {invite_count} 个邀请码引用，请先删除或迁移后再删除"}
    ok = db_op.delete_group(group_id)
    audit.record(request, "group.delete", f"删除权限组 #{group_id}" if ok else f"删除权限组 #{group_id}：不存在", level="info" if ok else "warning")
    return {"code": 1 if ok else 0, "msg": "删除成功" if ok else "权限组不存在"}


# ---------- 用户 ----------


class UserUpdate(BaseModel):
    password: Optional[str] = None
    group_id: Optional[int] = None
    quota_override: Optional[int] = Field(None, ge=0)  # None = 清除覆写；负数拒绝
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
async def update_user(request: Request, user_id: int, payload: UserUpdate):
    # 不能用 exclude_unset：quota_override 显式传 null 表示"清除覆写、继承组"，
    # 必须区分"没传"和"传了 null"。
    data = payload.model_dump(exclude_unset=True)
    if "quota_override" in payload.model_fields_set:
        data["quota_override"] = payload.quota_override  # 可能是 None
    target = db_op.get_user_by_id(user_id)
    if target is None:
        audit.record(request, "user.update", f"更新用户 #{user_id} 失败：用户不存在", level="warning")
        return {"code": 0, "msg": "用户不存在"}

    new_group_id = data.get("group_id", target["group_id"])
    new_status = data.get("status", target["status"])
    if _would_remove_last_admin(user_id, new_group_id, new_status):
        audit.record(request, "user.update", f"更新用户 {target['username']} (#{user_id}) 被拒：不能移除最后一个管理员", level="warning")
        return {"code": 0, "msg": "不能移除最后一个管理员"}

    ok = db_op.update_user(
        user_id,
        password=data.get("password"),
        group_id=data.get("group_id"),
        status=data.get("status"),
        quota_override=data["quota_override"] if "quota_override" in data else db_op.UNSET,
    )
    # 改密或改权限组后吊销该用户全部既有会话，防止被窃取的旧 sid 继续重放。
    revoked = 0
    if ok and (data.get("password") or data.get("group_id") is not None):
        revoked = session.delete_user_sessions(user_id)
    # 细节里不回显明文密码，仅标注是否重置
    changed = [k for k in ("group_id", "status", "quota_override") if k in data]
    if data.get("password"):
        changed.append("password")
    if revoked:
        changed.append(f"revoked_sessions={revoked}")
    audit.record(
        request,
        "user.update",
        f"更新用户 {target['username']} (#{user_id})：字段 {changed}" if ok else f"更新用户 {target['username']} (#{user_id})：未变更",
        level="info" if ok else "warning",
    )
    return {"code": 1 if ok else 0, "msg": "更新成功" if ok else "未变更"}


@router.delete("/users/{user_id}")
async def delete_user(request: Request, user_id: int):
    current = get_current_user(request)
    if current["id"] == user_id:
        audit.record(request, "user.delete", f"删除用户 #{user_id} 被拒：不能删除自己", level="warning")
        return {"code": 0, "msg": "不能删除自己"}
    if _would_remove_last_admin(user_id, None, 0):
        audit.record(request, "user.delete", f"删除用户 #{user_id} 被拒：不能移除最后一个管理员", level="warning")
        return {"code": 0, "msg": "不能移除最后一个管理员"}
    target = db_op.get_user_by_id(user_id)
    ok = db_op.delete_user(user_id)
    name = target["username"] if target else f"#{user_id}"
    audit.record(request, "user.delete", f"删除用户 {name} (#{user_id})" if ok else f"删除用户 #{user_id}：不存在", level="info" if ok else "warning")
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

    @field_validator("expires_at")
    @classmethod
    def _check_expires_at(cls, v: Optional[str]) -> Optional[str]:
        """expires_at 必须是可解析的 ISO 8601 日期时间，否则拒绝（防任意串注入/存储型 XSS）。"""
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("expires_at 必须是 ISO 8601 日期时间格式")
        return v


@router.get("/invites")
async def list_invites():
    invites = db_op.list_invites()
    name_by_id = {u["id"]: u["username"] for u in db_op.list_users()}
    for inv in invites:
        inv["used_by_users"] = db_op.list_invite_usernames(inv["id"])
        # 记录并回显创建账户（created_by 已在生成时落库，此处解析成用户名）
        creator = inv.get("created_by")
        inv["created_by_username"] = name_by_id.get(creator, f"#{creator}") if creator else "-"
    return {"code": 1, "msg": "ok", "data": invites}


@router.post("/invites")
async def create_invite(request: Request, payload: InviteIn):
    current = get_current_user(request)
    invite = db_op.create_invite(
        payload.group_id, current["id"], payload.max_uses, payload.expires_at
    )
    audit.record(request, "invite.create", f"生成邀请码 {invite['code']}（组={payload.group_id}, 次数={payload.max_uses}）")
    return {"code": 1, "msg": "创建成功", "data": invite}


@router.delete("/invites/{code}")
async def delete_invite(request: Request, code: str):
    ok = db_op.delete_invite(code)
    audit.record(request, "invite.delete", f"删除邀请码 {code}" if ok else f"删除邀请码 {code}：不存在", level="info" if ok else "warning")
    return {"code": 1 if ok else 0, "msg": "删除成功" if ok else "邀请码不存在"}


# ---------- DSH 进程 ----------


@router.get("/dsh/status")
async def dsh_status():
    return {"code": 1, "msg": "ok", "data": dsh_process.status()}


@router.get("/dsh/log")
async def dsh_log(lines: Optional[int] = None):
    """读取 DSH 运行日志尾部 N 行，供后台「日志」页展示。"""
    n = 200 if lines is None else max(1, min(int(lines), 2000))
    return {"code": 1, "msg": "ok", "data": dsh_process.read_log_tail(n)}


@router.post("/dsh/start")
async def dsh_start(request: Request):
    try:
        result = dsh_process.start()
        audit.record(
            request,
            "dsh.start",
            "启动 DSH" if result.get("running") else f"启动 DSH 失败：{result.get('error', '未知')}",
            level="info" if result.get("running") else "error",
        )
        return {"code": 1, "msg": "ok", "data": result}
    except dsh_process.DSHIsolationError as e:
        audit.record(request, "dsh.start", f"启动 DSH 失败：隔离不可用：{e}", level="error")
        return {"code": 0, "msg": f"DSH 隔离不可用：{e}", "data": None}


@router.post("/dsh/stop")
async def dsh_stop(request: Request):
    result = dsh_process.stop()
    audit.record(request, "dsh.stop", "停止 DSH")
    return {"code": 1, "msg": "ok", "data": result}


# ---------- DeepSeek API Key ----------


class DsKeyIn(BaseModel):
    api_key: str = Field(..., min_length=1, max_length=256)


def _mask_key(key: str) -> str:
    """只露前 7 位（如 sk-xxxx）和末 4 位，其余打码，用于回显。"""
    if len(key) <= 11:
        return key[:2] + "****"
    return f"{key[:7]}****{key[-4:]}"


def _update_env_key(path, key_name: str, value: str) -> None:
    """在 .env 中更新或追加一个 key=value，保留其它行与注释。"""
    lines = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped and not stripped.startswith("#") and stripped.split("=", 1)[0].strip() == key_name:
            lines[i] = f"{key_name}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key_name}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@router.get("/deepseek/apikey")
async def get_deepseek_apikey():
    """返回当前生效的 DS key（打码），供后台回显。"""
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    return {
        "code": 1,
        "msg": "ok",
        "data": {"configured": bool(key), "masked": _mask_key(key) if key else ""},
    }


@router.put("/deepseek/apikey")
async def set_deepseek_apikey(request: Request, payload: DsKeyIn):
    """
    更新 DeepSeek API Key：写入 .env + 当前进程环境，并热重启 DSH 使新 key 生效。
    全程不回显完整明文。
    """
    key = payload.api_key.strip()
    if not key:
        return {"code": 0, "msg": "api_key 不能为空"}

    # 1) 写入 .env（持久化）
    try:
        _update_env_key(ENV_PATH, "DEEPSEEK_API_KEY", key)
    except Exception as e:
        logger.warning("写入 .env 失败: %r", e)
        audit.record(request, "deepseek.apikey", f"更新 DeepSeek API Key 失败：写入 .env 出错：{e}", level="error")
        return {"code": 0, "msg": f"写入 .env 失败：{e}"}

    # 2) 更新当前进程环境（余额查询 / 下次拉起子进程都会用到）
    os.environ["DEEPSEEK_API_KEY"] = key

    # 3) 热重启 DSH 让新 key 生效（隔离失败不影响 key 已保存这一事实）
    restart = {"attempted": False}
    try:
        dsh_process.stop()
        restart = dsh_process.start() | {"attempted": True}
    except dsh_process.DSHIsolationError as e:
        restart = {"attempted": True, "running": False, "error": f"隔离不可用：{e}"}
    except Exception as e:
        restart = {"attempted": True, "running": False, "error": str(e)}

    # 日志只记录打码后的 key，绝不落明文
    audit.record(
        request,
        "deepseek.apikey",
        f"更新 DeepSeek API Key 为 {_mask_key(key)}（热重启：{'成功' if restart.get('running') else '未运行/失败'}）",
        level="info" if restart.get("running") else "warning",
    )
    return {
        "code": 1,
        "msg": "已保存并尝试热重启 DSH" if restart.get("running") else "已保存（DSH 未运行或重启失败）",
        "data": {"masked": _mask_key(key), "restart": restart},
    }


# ---------- 配额 ----------


class QuotaSettingsIn(BaseModel):
    window: Optional[str] = None          # 5h | day | week | month
    global_limit: Optional[int] = Field(None, ge=0)    # 0 = 不限；负数拒绝


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
async def update_quota_settings(request: Request, payload: QuotaSettingsIn):
    data = payload.model_dump(exclude_unset=True)
    if "window" in data and data["window"] is not None:
        try:
            quota.set_window_kind(data["window"])
        except ValueError as e:
            audit.record(request, "quota.settings", f"更新配额设置失败：{e}", level="warning")
            return {"code": 0, "msg": str(e)}
    if "global_limit" in data and data["global_limit"] is not None:
        quota.set_global_limit(data["global_limit"])
    audit.record(
        request,
        "quota.settings",
        f"更新配额设置：窗口={quota.get_window_kind()}, 全局限额={quota.get_global_limit()}",
    )
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
async def reset_quota_usage(request: Request):
    """
    清空当前窗口下的所有用量（用户 + 全局池）。
    """
    n = quota.reset_current_window_usage()
    audit.record(request, "quota.reset", f"重置当前窗口用量：删除 {n} 行", level="warning")
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


# ---------- 操作日志 ----------


def _clamp_limit(limit: int | None) -> int:
    try:
        n = int(limit) if limit is not None else 50
    except (TypeError, ValueError):
        n = 50
    return max(1, min(200, n))


@router.get("/oplogs")
async def get_op_logs(
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    level: Optional[str] = None,
    keyword: Optional[str] = None,
):
    """
    操作日志分页查询（按时间倒序）。
    level: info / warning / error；keyword 在 操作者/动作/细节/IP 上模糊匹配。
    """
    n = _clamp_limit(limit)
    off = max(0, int(offset or 0))
    rows, total = audit_op.list_op_logs(limit=n, offset=off, level=level, keyword=keyword)
    return {
        "code": 1,
        "msg": "ok",
        "data": {"rows": rows, "total": total, "limit": n, "offset": off},
    }
