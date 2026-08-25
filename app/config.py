# app/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# ====== .env 环境变量 ======
# 首启播种管理员（一次性；建库后即迁移入 DB）
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# DeepSeek 平台会话 token（可选，用于平台 usage/cost 与建 key 等逆向接口）
DEEPSEEK_PLATFORM_TOKEN = os.getenv("DEEPSEEK_PLATFORM_TOKEN", "")


# ====== 环境开关 ======
ONE_CLICK_PRODUCE = False

# 服务启动配置
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000
RELOAD = not ONE_CLICK_PRODUCE
WORKERS = 1 if RELOAD else 4
LOG_LEVEL = "debug" if not ONE_CLICK_PRODUCE else "info"
RELOAD_DIR = ["app"]


# FastAPI 基础配置
TITLE = "NekoSeek"
VERSION = "0.1.0"
DESCRIPTION = "DSH 多租户反向代理 / 管理器"
DEBUG = not ONE_CLICK_PRODUCE
# None 为关闭文档页
DOCS_URL = "/docs" if DEBUG else None
REDOC_URL = "/redoc" if DEBUG else None
OPENAPI_URL = "/openapi.json" if DEBUG else None


# CORS 跨域配置
if ONE_CLICK_PRODUCE:
    ALLOW_ORIGINS = ["https://your-domain.com"]
    if ALLOW_ORIGINS == ["https://your-domain.com"] or not ALLOW_ORIGINS:
        raise RuntimeError("ALLOW_ORIGINS 未配置")
else:
    ALLOW_ORIGINS = ["http://localhost:8000"]
ALLOW_CREDENTIALS = True
ALLOW_METHODS = ["*"]
ALLOW_HEADERS = ["*"]


# Session / Cookie 配置
SESSION_COOKIE_KEY = "session_id"
SESSION_MAX_AGE = 7 * 24 * 60 * 60  # /秒
SESSION_HTTPONLY = True
SESSION_SAMESITE = "lax"
SESSION_SECURE = ONE_CLICK_PRODUCE
SESSION_CLEANUP_AGE = 600  # /秒 循环清理过期 Session 的间隔


# ====== DSH 上游配置 ======
# DSH webui 监听地址（默认 127.0.0.1:3080）
DSH_UPSTREAM = os.getenv("DSH_UPSTREAM", "http://127.0.0.1:3080")
# 托管 dsh 进程时使用的完整启动命令（含参数，如 "dsh web"）
DSH_COMMAND = os.getenv("DSH_COMMAND", "dsh web")
# 是否在网关启动时自动拉起 DSH
DSH_AUTOSTART = os.getenv("DSH_AUTOSTART", "0") == "1"


# ====== DeepSeek LLM 上游配置 ======
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
# DSH 进程把 LLM 请求指回本网关的路径前缀（与 DEEPSEEK_BASE_URL 配合）
LLM_PROXY_PATH = "/llm"
# 是否要求流式响应携带 usage（stream_options.include_usage）
LLM_FORCE_USAGE = True


# ====== 配额配置 ======
# 全局池：周期额度（token 数），0 表示不限
POOL_QUOTA_LIMIT = int(os.getenv("POOL_QUOTA_LIMIT", "0"))
# 单用户：默认周期额度（token 数，粗略分词估算），可被组配额 / 用户覆写覆盖
DEFAULT_USER_QUOTA = int(os.getenv("DEFAULT_USER_QUOTA", "100000"))
# 配额周期（"day" | "month"），当前实现按自然日聚合
QUOTA_PERIOD = os.getenv("QUOTA_PERIOD", "day")

# 粗略分词折算系数（估算 tokens）
TOKEN_CJK_PER_CHAR = 1.0        # 中文/日文/韩文：每字约 1 token
TOKEN_LATIN_PER_WORD = 1.3      # 英文等：每词约 1.3 token
