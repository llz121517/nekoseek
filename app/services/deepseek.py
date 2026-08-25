# app/services/deepseek.py
"""
DeepSeek platform 逆向接口封装（余额 / usage / 建 key）
与 tools/ds_new_key.ps1 使用同一套 platform API。
"""
import httpx

from app.config import DEEPSEEK_PLATFORM_TOKEN

PLATFORM_BASE = "https://platform.deepseek.com"
API_BASE = "https://api.deepseek.com"

_PLATFORM_HEADERS = {
    "Authorization": f"Bearer {DEEPSEEK_PLATFORM_TOKEN}" if DEEPSEEK_PLATFORM_TOKEN else "",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0"
    ),
    "Origin": PLATFORM_BASE,
    "Referer": f"{PLATFORM_BASE}/api_keys",
    "x-client-bundle-id": "com.deepseek.chat",
    "x-client-locale": "zh_CN",
    "x-client-platform": "web",
    "x-client-version": "1.0.0",
}


async def get_balance(api_key: str | None = None) -> dict:
    """
    查询 API 余额。返回原始 JSON，或 {"error": ...}。
    """
    headers = {}
    key = api_key or ""
    if key:
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{API_BASE}/user/balance", headers=headers)
    try:
        return resp.json()
    except ValueError:
        return {"error": f"HTTP {resp.status_code}", "raw": resp.text[:200]}


async def get_usage_cost(month: str, year: str) -> dict:
    """
    查询平台用量/花费（需 DEEPSEEK_PLATFORM_TOKEN）。
    """
    if not DEEPSEEK_PLATFORM_TOKEN:
        return {"error": "DEEPSEEK_PLATFORM_TOKEN not set"}
    headers = dict(_PLATFORM_HEADERS)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{PLATFORM_BASE}/api/v0/usage/cost",
            params={"month": month, "year": year},
            headers=headers,
        )
    try:
        return resp.json()
    except ValueError:
        return {"error": f"HTTP {resp.status_code}", "raw": resp.text[:200]}


async def create_api_key(name: str) -> dict:
    """
    通过 platform API 创建 API key（对应 ds_new_key.ps1）。
    """
    if not DEEPSEEK_PLATFORM_TOKEN:
        return {"error": "DEEPSEEK_PLATFORM_TOKEN not set"}
    headers = dict(_PLATFORM_HEADERS)
    headers["Content-Type"] = "application/json"
    body = {
        "action": "create",
        "name": name,
        "redacted_key": None,
        "created_at": None,
        "tracking_id": None,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{PLATFORM_BASE}/api/v0/users/edit_api_keys",
            json=body,
            headers=headers,
        )
    try:
        return resp.json()
    except ValueError:
        return {"error": f"HTTP {resp.status_code}", "raw": resp.text[:200]}
