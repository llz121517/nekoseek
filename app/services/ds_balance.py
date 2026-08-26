# app/services/ds_balance.py
"""
DeepSeek 账户余额查询：GET https://api.deepseek.com/user/balance
"""
import logging
import os

import httpx

logger = logging.getLogger("nekoseek.ds_balance")

_BALANCE_URL = "https://api.deepseek.com/user/balance"
_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=None, pool=None)


async def fetch_balance() -> dict:
    """
    查询 DeepSeek 账户余额。返回统一结构：
      {
        "ok": bool,
        "is_available": bool | None,
        "balances": [{"currency": "...", "total": "...", "granted": "...", "topped_up": "..."}],
        "error": str | None,
      }
    未配置 DEEPSEEK_API_KEY 或网络/解析失败时 ok=False，error 给出原因。
    """
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return {"ok": False, "is_available": None, "balances": [], "error": "DEEPSEEK_API_KEY 未配置"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(_BALANCE_URL, headers={"Authorization": f"Bearer {api_key}"})
            r.raise_for_status()
            body = r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("查询余额 HTTP 错误: %s", e.response.status_code)
        return {"ok": False, "is_available": None, "balances": [], "error": f"HTTP {e.response.status_code}"}
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("查询余额失败: %r", e)
        return {"ok": False, "is_available": None, "balances": [], "error": str(e)}

    infos = body.get("balance_infos") or []
    balances = [
        {
            "currency": str(b.get("currency", "")),
            "total": str(b.get("total_balance", "0")),
            "granted": str(b.get("granted_balance", "0")),
            "topped_up": str(b.get("topped_up_balance", "0")),
        }
        for b in infos if isinstance(b, dict)
    ]
    return {
        "ok": True,
        "is_available": bool(body.get("is_available")),
        "balances": balances,
        "error": None,
    }
