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

# 项目根目录（app/config.py 的上上级），用 resolve() 转成绝对路径
ROOT = Path(__file__).resolve().parent.parent

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
# 这里会解析成绝对路径并写回 os.environ，确保 DSH 子进程能真正读到 $DSH_HOME。
_DSH_HOME_RAW = os.getenv("DSH_HOME", "")
if _DSH_HOME_RAW.strip():
    DSH_HOME = Path(os.path.expanduser(_DSH_HOME_RAW.strip())).resolve()
else:
    DSH_HOME = (ROOT / ".dsh").resolve()
os.environ["DSH_HOME"] = str(DSH_HOME)

# ====== 认证 / 账户 ======
# 首次启动且 users 表为空时，使用以下凭据创建初始管理员；之后以数据库为准，可移除。
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# ====== Session / Cookie ======
SESSION_COOKIE_KEY = "session_id"
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", str(7 * 24 * 60 * 60)))
SESSION_CLEANUP_AGE = int(os.getenv("SESSION_CLEANUP_AGE", "600"))
SESSION_HTTPONLY = True
SESSION_SAMESITE = "lax"
SESSION_SECURE = os.getenv("SESSION_SECURE", "0") == "1"  # 仅 HTTPS 部署时启用

# ====== 数据库路径 ======
# 双库分离：data.db 存用户/权限组/邀请码，cache.db 存会话。
DB_DIR = ROOT / "data" / "db"
DATA_DB_PATH = DB_DIR / "data.db"
CACHE_DB_PATH = DB_DIR / "cache.db"
STATS_DB_PATH = DB_DIR / "stats.db"

# ====== 配额 ======
# 计量窗口：5h / day / week / month；仅作 DB settings 的初始种子，运行时以后台设置为准。
QUOTA_WINDOW = os.getenv("QUOTA_WINDOW", "day").strip().lower() or "day"
if QUOTA_WINDOW not in ("5h", "day", "week", "month"):
    raise RuntimeError(f"QUOTA_WINDOW 非法: {QUOTA_WINDOW}")
# 全局配额上限（token 估算值），0 = 不限
GLOBAL_QUOTA_LIMIT = int(os.getenv("GLOBAL_QUOTA_LIMIT", "0"))
# 粗略分词估算权重：CJK 每字 / 拉丁每词
QUOTA_CJK_PER_CHAR = float(os.getenv("QUOTA_CJK_PER_CHAR", "1.0"))
QUOTA_LATIN_PER_WORD = float(os.getenv("QUOTA_LATIN_PER_WORD", "1.3"))

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
