<img src="1.png" height = "580" alt="NekoSeek" align=right />

# NekoSeek —— 猫猫求索

DSH webui 的**透明反向代理网关**：在 DSH 前端之前叠加一层认证、配额计量与管理后台，对上游页面 / 接口 / WebSocket 全量透传，同时注入一个右下角的用户信息面板。

- 本项目由 LLM 驱动的 Agent 辅助开发

## 目录

- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [配置项（.env）](#配置项env)
- [架构](#架构)
- [DSH 进程托管与权限隔离](#dsh-进程托管与权限隔离)
- [安全说明](#安全说明)
- [技术专题](#技术专题)
  - [配额计量口径](#配额计量口径)
  - [注入 JS 手写 UUID polyfill（绕过安全上下文限制）](#注入-js-手写-uuid-polyfill绕过安全上下文限制)
  - [反代层 isLoopback 放行（修复局域网设置降级）](#反代层-isloopback-放行修复局域网设置降级)
  - [pin-browse.cordis.yml（固定 WebUI 内嵌目录选择器）](#pin-browsecordisyml固定-webui-内嵌目录选择器)
- [目录结构](#目录结构)
- [测试](#测试)
- [许可证](#许可证)
- [致谢](#致谢)

---

## 功能特性

- **透明反代**：HTTP catch-all + WebSocket 隧道，原样转发 DSH webui 的全部页面、静态资源、SSE 与 RPC。
- **认证与会话**：邀请码注册制、PBKDF2 加盐口令、HTTP-only Cookie 会话（存 SQLite）、登录限流。
- **配额计量**：全局池 + 单用户两级配额，窗口可切换（5h / 天 / 周 / 月）。输入与输出统一取网关常驻 mux 订阅（WS 下行）里的真实 `usage`（含缓存命中的上下文），不做请求体估算；按 HTTP prompt 记录的 sessionId 归属记账，与浏览器是否在线无关。详见「[配额计量口径](#配额计量口径)」。
- **用量统计**：独立 `stats.db` 按「小时 × 用户」聚合，与配额记账互不影响；后台提供概览、逐小时折线图、按用户排行。
- **管理后台**：用户 / 权限组 / 邀请码 / 配额设置 / DSH 进程托管 / DeepSeek 余额查询。
- **操作日志**：后台所有写操作（用户/权限组/邀请码/DSH 启停/API Key/配额）与登录/登出均落库审计（`op_logs` 表），记录操作者、动作、细节、IP 与级别；支持级别/关键词筛选与分页，后台常驻线程定期修剪防膨胀。
- **日志查看**：后台可直接查看 `dsh.log` 运行日志尾部（可选行数、高效回溯读取，不整文件加载）。
- **注入面板**：向 DSH 页面注入右下角悬浮卡片，实时显示用户名、窗口、个人与全局配额用量，支持收起与语言跟随（中/英）。
- **DSH 兼容修复**：注入 JS 手写 UUID polyfill 让 `http://IP`（非安全上下文）也能打开 DSH；改写 `isLoopback` 判定修复局域网设置降级；cordis patch 固定 WebUI 内嵌目录选择器。见「[技术专题](#技术专题)」。

## 快速开始

```bash
# 1. 安装依赖（Python 3.11+）
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
#    至少设置 ADMIN_PASSWORD（首次启动播种初始管理员）
#    按需设置 DEEPSEEK_API_KEY（临时注入 DSH 子进程，不落盘）

# 3. 启动网关
python run.py
```

默认监听 `0.0.0.0:8000`，访问 `/` 即进入登录页。首次启动会自动建库、播种 `admin`/`user` 权限组与初始管理员。

> 网关需能连到 DSH 上游（默认 `http://127.0.0.1:3080`）。设 `DSH_AUTOSTART=1` 可让网关在启动时按 `DSH_COMMAND` 自动拉起 DSH 进程，见「[DSH 进程托管与权限隔离](#dsh-进程托管与权限隔离)」。

## 配置项（.env）

| 变量 | 默认                      | 说明                                                        |
|------|-------------------------|-----------------------------------------------------------|
| `SERVER_HOST` / `SERVER_PORT` | `0.0.0.0` / `8000`      | 网关监听地址                                                    |
| `RELOAD` | `0`                     | `1` 开启热重载（开发用；固定单 worker）                                 |
| `DSH_UPSTREAM` | `http://127.0.0.1:3080` | DSH webui 上游地址（不允许带路径前缀）                                  |
| `DSH_COMMAND` | `dsh web`               | 自动拉起 DSH 的完整命令（含参数）                                       |
| `DSH_PATCH` | `pin-browse.cordis.yml` | cordis patch 文件，相对路径按项目根目录解析；留空不加 `--patch`               |
| `DSH_AUTOSTART` | `0`                     | `1` 启动网关时自动拉起 DSH                                         |
| `DSH_HOME` | — | DSH 工作目录；留空由 dsh 基于运行账户 HOME 自建，显式配置则创建并授权给降权账户后透传（Linux） |
| `DSH_RUN_AS_USER` | `nekoseek-dsh`          | Linux 降权运行 DSH 的独立账户（Windows 忽略）                          |
| `DEEPSEEK_API_KEY` | —                       | 通过环境变量临时注入 DSH 子进程（不落盘），并用于余额查询；可在后台在线修改并热重启生效            |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / —             | 首次建库的初始管理员凭据（之后可移除）                                       |
| `SESSION_MAX_AGE` | `604800`                | 会话有效期（秒，默认 7 天）                                           |
| `SESSION_SECURE` | `0`                     | HTTPS 部署时置 `1`（Secure cookie）                             |
| `QUOTA_WINDOW` | `day`                   | 配额计量窗口种子值（`5h`/`day`/`week`/`month`，运行以后台设置为准）            |
| `GLOBAL_QUOTA_LIMIT` | `0`                     | 全局配额上限（token 数，`0`=不限）                                  |

启动时对 `DSH_UPSTREAM`、`SERVER_PORT`、`QUOTA_WINDOW`、`DSH_PATCH` 等做 fail-fast 校验，配置非法会直接报错。

## 架构

```
浏览器 ──► NekoSeek (FastAPI) ──► DSH webui
              │
              ├─ /api/v1/auth     登录 / 注册 / 登出 / 状态
              ├─ /api/v1/admin    用户·权限组·邀请码·配额·统计·DSH 进程·操作日志
              ├─ /api/v1/panel    注入面板数据
              ├─ /login /admin    自带页面
              ├─ WS /{path}       WebSocket 隧道（纯透传，不计量）
              ├─ HTTP /{path}     catch-all 透传（prompt 端点记录 sessionId 归属）
              └─ 常驻任务          usage_meter：订阅上游 /api/events.mux（WS 下行），
                                   按归属映射记账（usage 真实值计输入+输出 token）
```

**分层**：`app/api`（路由）→ `app/core`（认证/会话/配额/安全/DB）→ `app/services`（HTTP 与 WS 代理、面板注入、DSH 进程托管）。

**数据（SQLite，三库分离）**：

- `data.db` — 用户 / 权限组 / 邀请码 / 窗口化配额（`usage_records`，user_id=0 为全局池）/ 运行时设置 / 操作日志（`op_logs`）
- `cache.db` — 会话
- `stats.db` — 详细用量统计（`usage_hourly`，永不被配额重置清空）

**配额计量口径**：输入与输出统一取自 mux 事件流 `assistant/message` 帧的真实 `usage`（输入 = `inputTokens` + `cacheReadTokens`，输出 = `outputTokens`），HTTP 侧不计量；计量由网关常驻的 `usage_meter` 完成，浏览器 WS 隧道纯透传。完整口径与归属规则见「[配额计量口径](#配额计量口径)」。

## DSH 进程托管与权限隔离

设 `DSH_AUTOSTART=1` 后，网关负责拉起/停止 DSH 子进程，分平台处理：

- **Windows**：以当前用户直接拉起，`cwd` 用 `DSH_HOME`。
- **Linux**：网关须以 **root** 启动。若 `DSH_RUN_AS_USER` 账户不存在则自动创建（`useradd -r -m`），把 patch 文件 / 工作目录属权交给它，再用 `preexec_fn` 在子进程 exec 前 `initgroups → setgid → setuid` 降权，使 DSH 以独立账户运行，其 `~/.dsh` 落在该账户家目录下，**与网关自身文件隔离**（防止 DSH 被操控后改写网关文件）。未以 root 启动时抛 `DSHIsolationError`，**宁可不启动也不静默回退**。

> DSH 启动时会读取**当前工作目录**下的 `.env`，并校验 `DEEPSEEK_BASE_URL` 等启动级变量只能来自启动 shell。因此必须给 DSH 一个**独立 cwd**（不含本项目 `.env`），避免误读网关配置而崩溃。`DEEPSEEK_API_KEY` 不写入任何 `.env`，而是拉起子进程时通过环境变量临时注入，仅存在于该子进程生命周期内。

目录选择器在 win32 + 绑定 127.0.0.1 时默认弹宿主机原生对话框；网关以 `DSH_PATCH`（默认 `pin-browse.cordis.yml`）在拉起 DSH 时传入 `--patch` 固定为 WebUI 内嵌选择器，详见「[pin-browse.cordis.yml](#pin-browsecordisyml固定-webui-内嵌目录选择器)」。

## 安全说明

- 邀请码注册制，无公开注册入口；口令以 PBKDF2（20 万次迭代）加盐存储。
- 登录失败统一提示，并对「用户不存在」走 dummy 凭据补齐 PBKDF2 耗时，配合随机延迟降低用户枚举/时序侧信道风险。
- 登录接口限流（按真实 IP + UA 前缀，`5 次 / 15 分钟`，防 `X-Forwarded-For` 伪造）。
- 仅顶层导航 HTML（`sec-fetch-dest=document`）注入面板与 polyfill，SPA 的 XHR 片段不注入，避免脚本重复执行。
- 会话为 HTTP-only Cookie，账号停用后立即失效；窗口超限时仅拦截 prompt 端点返回 429，不影响页面浏览。
- Linux 启动时强制收紧敏感路径权限：`.env` → `600`、`data/` → `700`（目录需保留属主 x 位方可遍历，700 已对其它账户完全封闭），防止同机其它账户（含降权运行的 DSH）读取口令与 SQLite 数据；修复失败即中止启动。

## 技术专题

以下四节深入剖析 NekoSeek 的核心设计：配额计量的口径与归属，以及针对 DSH 在「非安全上下文 / 局域网访问 / win32 回环绑定」场景下行为异常的三处兼容修复。它们都在网关侧以**非侵入**方式实现——不改动 DSH 安装目录的任何磁盘文件，升级 DSH 零影响。

### 配额计量口径

计量由网关常驻的 `usage_meter` 完成：自持一条 `/api/events.mux` WS 订阅（与浏览器 WS 下行同一广播源），是唯一记账来源。

- **为何不在浏览器 WS 上记账**：DSH 的 mux 事件流是**广播**（所有 session 的事件推给每条连接）且**不重放**——在浏览器 WS 上记账会把他人用量误记到闲置用户头上，关页面还可绕过配额。因此浏览器隧道纯透传、不记账。
- **归属**：mux 帧只带 `sessionId`；prompt 只走 HTTP（WS 下行是纯推送），代理在 `session.prompt` / `subagent.prompt` 端点记录 `sessionId → 发起者`，计量帧按 `sessionId` 找回真正发起者。无归属的会话（agent 自动派生的子代理、网关重启前的会话）只记全局池，不计个人。他人对同一会话插话时归属默认转移给插话者（`attribution.py` 的 `TRANSFER_ON_PROMPT` 常量可切换为先入为主）。
- **逐帧直接记账**：一轮回复只发一条 `assistant/message` 完整帧（`assistant/chunk` 流式分片不带 `usage`），无需增量去重。
- **输入 = `inputTokens` + `cacheReadTokens`**：`inputTokens` 是本轮新增的非缓存输入（用户当条 prompt），`cacheReadTokens` 是以缓存命中形式计入的 system prompt / 历史上下文 / 工具结果。两者相加才是模型本轮实际处理的完整输入——只取 `inputTokens` 会漏掉上下文大头。
- **输出 = `outputTokens`**；`usage` 缺失时退化为对 `message.content` 文本粗略分词估算（此时输入计 0）。

### 注入 JS 手写 UUID polyfill（绕过安全上下文限制）

DSH 前端在启动早期就调用 `crypto.randomUUID()` 生成会话/请求 ID。但 **`crypto.randomUUID` 只在安全上下文（Secure Context）中可用** —— 即 `https://` 或 `localhost`。一旦通过 `http://<IP>`（局域网 IP、反向代理后的裸 HTTP 等）访问，`window.crypto.randomUUID` 不存在，DSH 前端一调即抛 `crypto.randomUUID is not a function`。

NekoSeek 在向浏览器回传顶层导航 HTML 时，**在 `<head>` 起始处内联注入一段就地 polyfill**，先于 DSH 的任何 module/defer 脚本执行：

- 优先用 `crypto.getRandomValues` 取真随机源生成 UUID v4（含 version/variant 位）；
- `getRandomValues` 也不可用时退回 `Math.random`（仅保证 ID 不重复，安全性低于加密随机，但对会话/请求 ID 足够）；
- 顺带补齐 `crypto.getRandomValues` 本身缺失的情形；
- 用 `try`/`defineProperty` 包裹，`crypto` 只读或字段不可写时静默放弃，不影响页面。

配合 DSH 的 CSP（`script-src 'self'`）：面板逻辑用**外联** `/static` 脚本（同源可执行），而 polyfill 必须在 DSH 的 module loader 之前跑，故**内联**且越靠前越好。由此 `http://IP` 直连也能正常打开 DSH webui。

### 反代层 isLoopback 放行（修复局域网设置降级）

DSH 前端的「设置」是一个 settings mirror，据 `connection.isLoopback` 选两种模式：`host`（持久化到 DSH 服务端）/ `memory`（只读视图恒为 `undefined`）。而 `isLoopback` 只看 `location.hostname` 是否为 loopback——经局域网 IP（`http://192.168.x.x`）访问时误判为 `false`，settings mirror 降级为 memory 只读模式，于是：

- 模型设置页报「加载提供方目录失败： settings are unavailable in this browser」；
- 内测声明「不再提示」等设置写入即丢，每次刷新反复弹出。

NekoSeek 以**非侵入**方式在反代层修复（不改动 npm 安装目录任何文件，升级 DSH 零影响）：

1. 注入的 polyfill 在 `<head>` 起始置 `window.__DSH_LOCAL_APP__ = true`；
2. 反代透传 `dsh-client-connection` 浏览器 bundle（`/plugins/@deepseek-ai/dsh-client-connection/client.js`）时，将其中的 `isLoopback` 判定改写为额外认 `__DSH_LOCAL_APP__` 标记——只改线上字节，不碰磁盘。

由此局域网访问下 settings mirror 恢复 `host` 模式，设置真正持久化到服务端，两个症状一并消除。改写锚点预期在 bundle 中恰好出现一次，失配（上游改版）时原样透传并记日志告警，不静默失效。

### pin-browse.cordis.yml（固定 WebUI 内嵌目录选择器）

DSH web 的目录选择器默认是 `-auto` 行，按环境自动选 `native` 或 `browse` 后端；本机 win32 + 绑定 127.0.0.1 时恒选 `native`——**弹的是宿主机（服务器）上的原生 Win32 文件夹对话框**，经网关/局域网访问的用户根本看不到，目录选择直接不可用。

`pin-browse.cordis.yml` 是一份 cordis patch（仓库根目录，随项目分发）：

- 禁用 `directory-picker`（auto 行）；
- 直接挂 `dsh-host-directory-picker-browse` 后端 + `dsh-client-ui-directory-picker-browse` 浏览器端选择器。

由此目录选择固定在 WebUI 内嵌浏览框，与访问方式无关。网关通过 `DSH_PATCH`（默认即 `pin-browse.cordis.yml`，可在 `.env` 覆盖或留空禁用）在自动拉起 DSH 时以 `--patch` 传入；也可手动 `dsh web --patch <patch文件的绝对路径或与dsh工作目录的相对路径>`，或把文件内容合并进 `%USERPROFILE%\.dsh\profiles\web\cordis.patch.yml` 长期固定。patch 只作用于启动时加载，**不改动 DSH 安装目录任何文件**，升级 DSH 零影响。

## 目录结构

```
app/
  api/v1/       auth / admin / panel 路由
  core/         auth / session / quota / attribution(用量归属) / audit(操作日志埋点) / security / tokenize
  core/db/      db(连接) / db_op(业务CRUD) / stats_op(统计) / audit_op(操作日志) / init_db(建表播种)
  services/     proxy(HTTP) / ws_proxy(WS) / usage_meter(常驻计量) / inject(面板+polyfill) / dsh_process(托管) / ds_balance
frontend/       login / admin 页面 + 静态资源
  static/js     login / admin / common / ebui-panel(注入面板)
  static/css    login / admin / common / ebui-panel
  static/vendor bootstrap / chart 等第三方库
tests/          pytest 测试（单元 + 集成）
tools/          诊断脚本（diag_dsh.py 等）
data/db/        SQLite 数据（运行时生成）
run.py          启动入口
```

## 测试

```bash
pip install -r requirements-dev.txt   # 含 pytest / pytest-asyncio
pytest                                 # 运行全部测试
```

测试通过 `tests/conftest.py` 把三个 SQLite 库重定向到临时目录、补丁 thread-local 连接，做到用例间完全隔离，不读写真实 `data/db/`。HTTP/WS 上游用 mock 替身，无需真实 DSH 进程。

## 许可证

[MIT](LICENSE)

## 致谢

- 局域网 `isLoopback` 降级问题的根因分析与修复思路，参考自 [deepseek-harness-fpk issue #2](https://github.com/10000ge10000/deepseek-harness-fpk/issues/2)（@xiaoke799 的源码级排查）。
- 本项目由 **Kimi K3 与 DeepSeek V4 Flash** 辅助设计与编写。
- 感谢 **DeepSeek** 提供 DeepSeek Harness 与底层模型能力。