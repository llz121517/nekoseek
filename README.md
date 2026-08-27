# NekoSeek —— 猫猫求索

DSH webui 的**透明反向代理网关**：在 DSH 前端之前叠加一层认证、配额计量与管理后台，对上游页面/接口/WebSocket 全量透传，同时注入一个右下角的用户信息面板。

本项目使用 LLM Agent 辅助编写。

## 功能特性

- **透明反代**：HTTP catch-all + WebSocket 隧道，原样转发 DSH webui 的全部页面、静态资源、SSE 与 RPC。
- **认证与会话**：邀请码注册制、PBKDF2-HMAC-SHA256 加盐口令、HTTP-only Cookie 会话（存 SQLite）、登录限流与时序侧信道防护。
- **配额计量**：全局池 + 单用户两级配额，窗口可切换（5h / 天 / 周 / 月）。输入按 prompt 文本估算，输出取 WS 下行帧的真实 `usage`。
- **用量统计**：独立 `stats.db` 按「小时 × 用户」聚合，与配额记账互不影响；后台提供概览、逐小时折线图、按用户排行。
- **管理后台**：用户 / 权限组 / 邀请码 / 配额设置 / DSH 进程托管 / DeepSeek 余额查询。
- **注入面板**：向 DSH 页面注入右下角悬浮卡片，实时显示用户名、窗口、个人与全局配额用量，支持收起与语言跟随（中/英）。

## 快速开始

```bash
# 1. 安装依赖（Python 3.11+）
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
#    至少设置 ADMIN_PASSWORD（首次启动播种初始管理员）
#    按需设置 DEEPSEEK_API_KEY（会自动同步到 DSH 工作区）

# 3. 启动网关
python run.py
```

默认监听 `0.0.0.0:8000`，访问 `/` 即进入登录页。首次启动会自动建库、播种 `admin`/`user` 权限组与初始管理员。

> 网关需能连到 DSH 上游（默认 `http://127.0.0.1:3080`）。设 `DSH_AUTOSTART=1` 可让网关在启动时按 `DSH_COMMAND` 自动拉起 DSH 进程。

## 配置项（.env）

| 变量 | 默认 | 说明 |
|------|------|------|
| `SERVER_HOST` / `SERVER_PORT` | `0.0.0.0` / `8000` | 网关监听地址 |
| `RELOAD` | `0` | `1` 开启热重载（开发用，单 worker） |
| `DSH_UPSTREAM` | `http://127.0.0.1:3080` | DSH webui 上游地址 |
| `DSH_COMMAND` | `dsh web` | 自动拉起 DSH 的完整命令 |
| `DSH_AUTOSTART` | `0` | `1` 启动网关时自动拉起 DSH |
| `DSH_HOME` | `./.dsh` | DSH 独立工作目录（隔离其 `.env`） |
| `DEEPSEEK_API_KEY` | — | 配置后同步到 `DSH_HOME/.env`，并用于余额查询 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / — | 首次建库的初始管理员凭据 |
| `SESSION_MAX_AGE` | `604800` | 会话有效期（秒，默认 7 天） |
| `SESSION_SECURE` | `0` | HTTPS 部署时置 `1` |
| `QUOTA_WINDOW` | `day` | 配额计量窗口种子值（`5h`/`day`/`week`/`month`，运行以后台设置为准） |
| `GLOBAL_QUOTA_LIMIT` | `0` | 全局配额上限（token 估算值，`0`=不限） |

启动时对 `DSH_UPSTREAM`、`SERVER_PORT`、`QUOTA_WINDOW` 等做 fail-fast 校验，配置非法会直接报错。

## 架构

```
浏览器 ──► NekoSeek (FastAPI) ──► DSH webui
              │
              ├─ /api/v1/auth     登录 / 注册 / 登出 / 状态
              ├─ /api/v1/admin    用户·权限组·邀请码·配额·统计·DSH 进程
              ├─ /api/v1/panel    注入面板数据
              ├─ /login /admin    自带页面
              ├─ WS /{path}       WebSocket 隧道（下行帧计输出 token）
              └─ HTTP /{path}     catch-all 透传（prompt 端点计输入 token）
```

**分层**：`app/api`（路由）→ `app/core`（认证/会话/配额/安全/DB）→ `app/services`（HTTP 与 WS 代理、面板注入、DSH 进程托管）。

**数据（SQLite，三库分离）**：
- `data.db` — 用户 / 权限组 / 邀请码 / 窗口化配额（`usage_records`，user_id=0 为全局池）
- `cache.db` — 会话
- `stats.db` — 详细用量统计（`usage_hourly`，永不被配额重置清空）

**配额计量口径**：输入在 HTTP prompt 端点（`/api/session.prompt`、`/api/subagent.prompt`）对 `content[].text` 粗略分词估算；输出在 WS 下行 `assistant/message` 帧取真实 `usage.outputTokens`，缺失时退化为文本估算。两侧互不重复。

## 安全说明

- 邀请码注册制，无公开注册入口；口令以 PBKDF2（20 万次迭代）加盐存储。
- 登录失败统一提示并对「用户不存在」走 dummy 凭据补齐计算耗时，降低枚举风险。
- 登录接口限流（按真实 IP + UA 前缀，`5 次 / 15 分钟`）。
- 仅顶层导航 HTML（`sec-fetch-dest=document`）注入面板，SPA 的 XHR 片段不注入，避免脚本重复执行。
- 会话为 HTTP-only Cookie，账号停用后立即失效；窗口超限时仅拦截 prompt 端点返回 429，不影响页面浏览。

## 目录结构

```
app/
  api/v1/       auth / admin / panel 路由
  core/         auth / session / quota / security / tokenize / db
  services/     proxy(HTTP) / ws_proxy(WS) / inject / dsh_process / dsh_env / ds_balance
frontend/       login / admin 页面 + 注入面板静态资源
tests/          pytest 测试（单元 + 集成，覆盖核心逻辑）
tools/          诊断脚本
data/db/        SQLite 数据（运行时生成）
.dsh/           DSH 独立工作目录
run.py          启动入口
```

## 测试

```bash
pip install -r requirements-dev.txt   # 含 pytest / pytest-asyncio
pytest                                 # 运行全部测试
```

测试通过 `tests/conftest.py` 把三个 SQLite 库重定向到临时目录、补丁 thread-local 连接，做到用例间完全隔离，不会读写真实 `data/db/`。HTTP/WS 上游用 mock 替身，无需真实 DSH 进程。

## 许可证

[MIT](LICENSE)
