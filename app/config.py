# app/config.py
"""
NekoSeek 配置：透明反代 DSH webui 的 MVP 配置。

只在 .env / 环境变量中读取本项目需要的最小配置集，并在启动时做
fail-fast 硬校验，避免带病运行。
"""
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

# 项目根目录（app/config.py 的上上级）
ROOT = Path(__file__).parent.parent

# ====== 服务启动配置 ======
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))
RELOAD = os.getenv("RELOAD", "0") == "1"
# 固定单进程：DSH 子进程托管与模块级 httpx client 都不允许多 worker 各自初始化。
WORKERS = 1
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")
RELOAD_DIR = ["app"]

TITLE = "NekoSeek"
VERSION = "0.1.0"
DESCRIPTION = "DSH 透明反向代理"

# ====== DSH 上游配置 ======
# DSH webui 监听地址（网关所有请求的转发目标）
DSH_UPSTREAM = os.getenv("DSH_UPSTREAM", "http://127.0.0.1:3080")
# 托管 dsh 进程时的完整启动命令（含参数，如 "dsh web"）
DSH_COMMAND = os.getenv("DSH_COMMAND", "dsh web")
# 网关启动时是否自动拉起 DSH（"1" 开）
DSH_AUTOSTART = os.getenv("DSH_AUTOSTART", "0") == "1"
# DSH 的独立工作目录（隔离 .env，避免 DSH 读到网关配置而崩溃），默认项目根目录下的 .dsh
_DSH_HOME_RAW = os.getenv("DSH_HOME", "")
DSH_HOME = _DSH_HOME_RAW.strip() if _DSH_HOME_RAW.strip() else str(ROOT / ".dsh")

# ====== fail-fast 硬校验 ======
_parsed = urlparse(DSH_UPSTREAM)
if _parsed.scheme not in ("http", "https") or not _parsed.netloc:
    raise RuntimeError(f"DSH_UPSTREAM 非法: {DSH_UPSTREAM}")
if _parsed.path not in ("", "/"):
    raise RuntimeError(f"DSH_UPSTREAM 不允许带路径前缀: {DSH_UPSTREAM}")
if DSH_AUTOSTART and not DSH_COMMAND.strip():
    raise RuntimeError("DSH_AUTOSTART=1 时 DSH_COMMAND 不能为空")
if not (0 < SERVER_PORT < 65536):
    raise RuntimeError(f"SERVER_PORT 非法: {SERVER_PORT}")

# 重写 Origin/Referer/Location 用的上游"原点"（scheme://host[:port]，不含路径）
DSH_ORIGIN = f"{_parsed.scheme}://{_parsed.netloc}"

# 关闭网关自身文档页，避免 /docs 等遮蔽 DSH 的同名路径
DOCS_URL = None
REDOC_URL = None
OPENAPI_URL = None
