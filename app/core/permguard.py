# app/core/permguard.py
"""
启动期敏感文件权限加固（仅 Linux）。

目的：网关的 .env（管理口令、DEEPSEEK_API_KEY 等）与 data/（SQLite 库：
口令哈希、会话 cookie、用量记录）只应属主可访问。Linux 下网关以 root 启动
并托管降权运行的 DSH 进程，若目录/文件按默认 umask 落成 755/644，同机其它
账户（包括 DSH 的降权账户）就能读走敏感数据。启动时强制收紧：

- <ROOT>/.env      → 0o600（文件）
- <ROOT>/data/     → 0o700（目录）

目录不用 600 的原因：目录必须保留属主的 x 位才能进入/寻址内部文件，
0o600 的目录连属主自己都无法遍历；0o700 已是对其它账户完全封闭。

data/ 收紧为 0o700 后，其中未来新建的库文件（即使自身 644）也一并不可达。
修复失败（非 root 且属主不是自己等）按项目惯例 fail-fast 中止启动，不静默带病运行。
"""
import logging
import os
import sys
from pathlib import Path

from app import config

logger = logging.getLogger("nekoseek.permguard")

_ENV_NAME = ".env"
_DATA_NAME = "data"
_FILE_MODE = 0o600
_DIR_MODE = 0o700


def _fix_mode(path: Path, mode: int) -> bool:
    """路径存在且权限不等于 mode 时 chmod 修复，返回是否发生改动。"""
    try:
        st = path.stat()
    except FileNotFoundError:
        return False
    if (st.st_mode & 0o777) == mode:
        return False
    os.chmod(path, mode)
    logger.info("权限加固 %s: %o -> %o", path, st.st_mode & 0o777, mode)
    return True


def harden() -> None:
    """
    把 .env 与 data/ 收紧为仅属主可读写。仅 Linux 生效，其它平台直接跳过。
    任一步失败抛 RuntimeError（需 root 才能修复时给出明确指引）。
    """
    if not sys.platform.startswith("linux"):
        return
    env_file = config.ROOT / _ENV_NAME
    data_dir = config.ROOT / _DATA_NAME
    try:
        if env_file.exists():
            _fix_mode(env_file, _FILE_MODE)
        # 首次启动可能还没有 data/：先建再收紧，保证后续落盘的库文件也被 700 覆盖。
        data_dir.mkdir(parents=True, exist_ok=True)
        _fix_mode(data_dir, _DIR_MODE)
    except OSError as e:
        raise RuntimeError(
            "敏感文件权限加固失败：.env 与 data/ 必须仅属主可读写。"
            f"请以 root 启动网关以便自动修复（当前错误: {e}）"
        ) from e