# app/services/dsh_env.py
"""
DSH 工作区 .env 同步。

将项目 .env 中配置的 DEEPSEEK_API_KEY 自动同步到 DSH_HOME/.env，
保证 DSH 子进程启动时能从独立工作目录读到正确的 API key。
"""
import logging
import os
from pathlib import Path

from app.config import DSH_HOME

logger = logging.getLogger("nekoseek.dsh_env")

DS_KEY_NAME = "DEEPSEEK_API_KEY"
DSH_ENV_PATH = Path(DSH_HOME) / ".env"


def _parse_env(content: str) -> dict[str, str]:
    """解析 .env 文本，返回 key-value 字典；忽略空行、注释和无效行。"""
    result: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        result[key.strip()] = value.strip()
    return result


def _format_env(data: dict[str, str]) -> str:
    """将 key-value 字典格式化为标准 .env 文本。"""
    lines = [f"{key}={value}" for key, value in data.items()]
    return "\n".join(lines) + "\n"


def sync_dsh_env() -> None:
    """
    把项目 .env 中的 DEEPSEEK_API_KEY 同步到 DSH_HOME/.env。

    - 项目 .env 未配置 key 时跳过，避免覆盖用户手动写入的值。
    - 保留 .dsh/.env 中已有的其他变量。
    - 现有值与项目值相同时不写文件。
    """
    project_key = os.getenv(DS_KEY_NAME, "").strip()
    if not project_key:
        logger.debug("项目 .env 未配置 %s，跳过同步", DS_KEY_NAME)
        return

    DSH_HOME.mkdir(parents=True, exist_ok=True)

    current: dict[str, str] = {}
    if DSH_ENV_PATH.exists():
        try:
            current = _parse_env(DSH_ENV_PATH.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("读取 %s 失败，将重新创建", DSH_ENV_PATH, exc_info=True)

    if current.get(DS_KEY_NAME) == project_key:
        logger.debug("DSH 工作区 %s 已是最新，无需更新", DS_KEY_NAME)
        return

    current[DS_KEY_NAME] = project_key
    DSH_ENV_PATH.write_text(_format_env(current), encoding="utf-8")
    logger.info("已同步 %s 到 DSH 工作区: %s", DS_KEY_NAME, DSH_ENV_PATH)
