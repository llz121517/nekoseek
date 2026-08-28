# app/services/dsh_process.py
"""
DSH 进程托管（subprocess 启停，无 profile 管理）

平台分支（fork 点）：
- Windows：维持现状 —— 以当前用户直接拉起，cwd 用 DSH_HOME（默认 ~/.dsh）。
- Linux：网关必须以 root 启动，先确保独立账户 DSH_RUN_AS_USER 存在（不存在则
  自动创建系统账户），再把 .env / patch 文件 / 工作目录的属主交给该账户，
  最后用 preexec_fn 在子进程中立即调用 initgroups()+setgid()+setuid() 降权，
  以该独立账户运行 DSH，使其 ~/.dsh 落在专用账户家目录下，与网关自身文件隔离，
  防止 DSH 被操控后改写网关文件。

  未以 root 启动时直接抛 DSHIsolationError，不静默回退到当前账户——宁可失败，
  也不在失去文件隔离的情况下运行。

  工作目录：若配置了 DSH_HOME 则创建并授权给目标账户后用作 cwd；未配置则不创建，
  cwd 落到目标账户家目录，由 dsh 在降权后自建 ~/.dsh（默认行为）。

注意：DSH 启动时会读取「当前工作目录」下的 .env 文件，且会校验其中的
DEEPSEEK_BASE_URL 等启动级变量只能来自启动 shell。因此必须给 DSH 一个
独立的 cwd（不含本项目 .env 的目录），避免读到网关自己的配置而崩溃。

DEEPSEEK_API_KEY 不再写入 .env，而是在拉起子进程时通过环境变量临时注入，
仅存在于该子进程的生命周期内，不落盘。
"""
import logging
import os
import shlex
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from app.config import (
    DSH_COMMAND,
    DSH_UPSTREAM,
    DSH_HOME,
    DSH_HOME_EXPLICIT,
    DSH_RUN_AS_USER,
    DSH_PATCH,
    ROOT,
)

logger = logging.getLogger("nekoseek.dsh_process")

IS_WINDOWS = os.name == "nt"


class DSHIsolationError(RuntimeError):
    """无法以独立账户托管 DSH（隔离不可用）时抛出，网关据此中止启动。"""

DSH_WORKDIR = Path(DSH_HOME)
# 日志固定放项目根目录，与 DSH 工作目录解耦：Linux 降权后网关（root）仍能读自己的日志。
DSH_LOG_PATH = ROOT / "dsh.log"
DSH_ENV_PATH = DSH_WORKDIR / ".env"
DS_KEY_NAME = "DEEPSEEK_API_KEY"
_process: subprocess.Popen | None = None


def _ensure_run_as_user() -> dict:
    """
    Linux 下解析（必要时自动创建）降权目标账户，返回 {"uid","gid","home","name"}。

    运行模型：网关必须以 root 启动，随后通过 setuid/setgid 把 DSH 子进程降权到
    该独立账户。账户不存在时用 useradd 自动创建系统账户（-r -m）。未以 root
    启动时抛 DSHIsolationError，不静默回退——隔离失效时宁可不启动。
    """
    import pwd  # 仅 POSIX 可用

    if os.geteuid() != 0:
        raise DSHIsolationError(
            "Linux 下托管 DSH 需要以 root 启动网关（用于创建/降权到独立账户 "
            f"{DSH_RUN_AS_USER!r}）。当前 euid={os.geteuid()}，请用 sudo 运行。"
        )

    try:
        pw = pwd.getpwnam(DSH_RUN_AS_USER)
    except KeyError:
        # 账户不存在：自动创建系统账户（-r 系统账户，-m 建家目录）。
        logger.info("DSH 独立账户 %r 不存在，自动创建", DSH_RUN_AS_USER)
        res = subprocess.run(
            ["useradd", "-r", "-m", "-s", "/usr/sbin/nologin", DSH_RUN_AS_USER],
            capture_output=True,
        )
        if res.returncode != 0:
            raise DSHIsolationError(
                f"自动创建 DSH 独立账户 {DSH_RUN_AS_USER!r} 失败："
                f"{res.stderr.decode(errors='replace').strip() or 'useradd 出错'}。"
                f"请手动创建：sudo useradd -r -m {DSH_RUN_AS_USER}"
            )
        pw = pwd.getpwnam(DSH_RUN_AS_USER)

    home = pw.pw_dir.rstrip("/") or "/"
    return {"uid": pw.pw_uid, "gid": pw.pw_gid, "home": home, "name": pw.pw_name}


def _resolve_run_as() -> dict | None:
    """
    Linux 返回降权目标账户信息（必要时创建账户并要求 root）；Windows 或未配置
    DSH_RUN_AS_USER 返回 None。仅在真正要拉起 DSH 的 start() 中调用——只读路径
    （如 status()）应直接用 DSH_RUN_AS_USER，避免触发建账户/root 校验。
    """
    if IS_WINDOWS or not DSH_RUN_AS_USER:
        return None
    return _ensure_run_as_user()


def _chown(path: Path, run_as: dict) -> None:
    """把路径属主交给降权账户，失败仅告警（不阻断启动）。"""
    try:
        os.chown(path, run_as["uid"], run_as["gid"])
    except OSError:
        logger.warning("chown %s 到 %s 失败", path, run_as["name"], exc_info=True)


def _grant_permissions(run_as: dict, workdir: Path | None) -> None:
    """
    降权前把 DSH 需要读写的资源属权交给目标账户：
    - .env 中 DSH_PATCH 配置的 patch 文件（dsh 需读取）；
    - 工作目录（仅当显式配置了 DSH_HOME 才创建并授权；未配置则不创建，
      由 dsh 在降权后基于目标账户 HOME 自建 ~/.dsh）。
    """
    if DSH_PATCH is not None:
        _chown(DSH_PATCH, run_as)
    if workdir is not None:
        workdir.mkdir(parents=True, exist_ok=True)
        _chown(workdir, run_as)


def _drop_privileges(run_as: dict):
    """
    返回 preexec_fn：在子进程 exec 之前立即降权到目标账户。
    顺序固定为 initgroups → setgid → setuid（先组后户，避免残留 root 组权限）。
    仅在 fork 出的子进程中运行，不影响网关主进程（仍为 root）。
    """
    def _preexec() -> None:
        os.initgroups(run_as["name"], run_as["gid"])
        os.setgid(run_as["gid"])
        os.setuid(run_as["uid"])

    return _preexec


def _build_child_env(run_as: dict | None) -> dict[str, str]:
    """
    构建 DSH 子进程的环境变量。

    - 继承当前进程环境。
    - 临时注入 DEEPSEEK_API_KEY（来自网关自身环境/.env），让密钥只存在于
      子进程生命周期内，不写入 .env。
    - Linux 降权时把 HOME 指向目标账户家目录，使其 ~/.dsh 落在该账户下。
    - 顺带清除旧 DSH_HOME/.env 中残留的 DEEPSEEK_API_KEY，避免历史上
      sync_dsh_env 写入的明文 key 继续留盘。
    """
    env = dict(os.environ)

    key = os.getenv(DS_KEY_NAME, "").strip()
    if key:
        env[DS_KEY_NAME] = key

    if run_as is not None:
        env["HOME"] = run_as["home"]
        env["USER"] = run_as["name"]
        env["LOGNAME"] = run_as["name"]
        if DSH_HOME_EXPLICIT:
            # 显式配置了 DSH_HOME：已创建并授权给目标账户，透传作强制指定。
            env["DSH_HOME"] = str(DSH_HOME)
        else:
            # 未配置：不透传，由 dsh 基于目标账户 HOME 自建 ~/.dsh。
            env.pop("DSH_HOME", None)
    elif DSH_HOME_EXPLICIT:
        # 未降权且用户显式配置了 DSH_HOME：透传，作强制指定（dsh 直接用作数据根）。
        env["DSH_HOME"] = str(DSH_HOME)
    else:
        # 未配置 DSH_HOME（默认）：不透传，由 dsh 基于当前账户 HOME 自建 ~/.dsh，
        # 避免它把 DSH_HOME 当父目录再拼一层导致嵌套。
        env.pop("DSH_HOME", None)

    # 清理旧的 .env 中残留的密钥行（幂等；文件不存在或无该行则不动）
    try:
        if DSH_ENV_PATH.exists():
            lines = DSH_ENV_PATH.read_text(encoding="utf-8").splitlines()
            kept = [
                ln for ln in lines
                if ln.strip() and not ln.strip().startswith(f"{DS_KEY_NAME}=")
            ]
            if len(kept) != len(lines):
                if kept:
                    DSH_ENV_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8")
                else:
                    DSH_ENV_PATH.unlink()
    except Exception:
        # 清理失败不影响启动，仅记录
        logger.warning(
            "清理 %s 中残留的 %s 失败", DSH_ENV_PATH, DS_KEY_NAME, exc_info=True
        )

    return env


def _common_install_paths(exe_name: str) -> list[Path]:
    """按平台补充一些常见安装位置。"""
    home = Path.home()
    candidates = [
        home / ".cargo" / "bin" / exe_name,
        home / ".cargo" / "bin" / f"{exe_name}.exe",
        home / "scoop" / "shims" / f"{exe_name}.exe",
        home / "scoop" / "shims" / exe_name,
        Path("C:") / "Program Files" / "dsh" / f"{exe_name}.exe",
        Path("C:") / "Program Files (x86)" / "dsh" / f"{exe_name}.exe",
        Path("C:") / "ProgramData" / "chocolatey" / "bin" / f"{exe_name}.exe",
    ]
    return [p for p in candidates if p.exists()]


def _build_cmd() -> list[str] | None:
    """
    构建可执行命令行：DSH_COMMAND 承载完整命令（含参数），
    按 shell 规则拆分；Windows 的 .cmd/.bat 需 cmd /c 包装。
    --patch（若配置 DSH_PATCH）插入为子命令（如 web）之后的第一个参数——
    位置错了会报 unknown option。其路径已按项目根目录解析为绝对路径，
    与子进程 cwd 无关。
    """
    parts = shlex.split(DSH_COMMAND)
    if not parts:
        return None
    if DSH_PATCH is not None:
        parts[2:2] = ["--patch", str(DSH_PATCH)]
    exe_name = parts[0]
    exe = shutil.which(exe_name)

    if exe is None and os.path.isfile(exe_name):
        exe = exe_name

    if exe is None:
        for p in _common_install_paths(exe_name):
            exe = str(p)
            break

    if exe is None:
        for suffix in (".exe", ".cmd", ".bat"):
            exe = shutil.which(exe_name + suffix)
            if exe:
                break

    if exe is None:
        return None

    if exe.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", exe, *parts[1:]]
    return [exe, *parts[1:]]


def _upstream_alive(timeout: float = 2.0) -> bool:
    """上游端口已监听即视为 DSH 在跑，用于 reload/多进程场景下避免重复拉起。"""
    p = urlparse(DSH_UPSTREAM)
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        with socket.create_connection((p.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def _tail_log(lines: int = 20) -> str:
    """读取 DSH 日志最后几行，用于启动失败时排错。"""
    if not DSH_LOG_PATH.exists():
        return ""
    try:
        text = DSH_LOG_PATH.read_text(encoding="utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except Exception:
        return ""


def start() -> dict:
    """启动 dsh 子进程。若已在运行（本进程或外部）则复用。"""
    global _process
    if _process is not None and _process.poll() is None:
        return status()

    if _upstream_alive():
        return {"running": True, "pid": None, "command": DSH_COMMAND, "spawned": False}

    cmd = _build_cmd()
    if cmd is None:
        return {
            "running": False,
            "error": f"command not found: {DSH_COMMAND}",
            "searched": [str(p) for p in _common_install_paths(shlex.split(DSH_COMMAND)[0])] if DSH_COMMAND else [],
        }

    # —— 平台 fork 点：Linux 以 root 启动并 setuid/setgid 降权到独立账户，Windows 维持当前用户 ——
    # 未以 root 启动时 _resolve_run_as 直接抛 DSHIsolationError，
    # 由调用方（main.lifespan）中止启动，不在失去隔离的情况下运行。
    run_as = _resolve_run_as()

    preexec_fn = None
    if run_as is not None:
        # 配置了 DSH_HOME 才创建工作目录并授权；未配置则 cwd 落到目标账户家目录，
        # 由 dsh 在降权后自建 ~/.dsh（默认行为，无需网关预建）。
        workdir = DSH_WORKDIR if DSH_HOME_EXPLICIT else Path(run_as["home"])
        _grant_permissions(run_as, workdir if DSH_HOME_EXPLICIT else None)
        preexec_fn = _drop_privileges(run_as)
    else:
        workdir = DSH_WORKDIR

    try:
        # 目录由当前进程创建（Windows / 未降权场景）。
        if run_as is None:
            workdir.mkdir(parents=True, exist_ok=True)

        log_file = open(DSH_LOG_PATH, "a", encoding="utf-8")
        _process = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            env=_build_child_env(run_as),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            preexec_fn=preexec_fn,
        )
    except FileNotFoundError:
        return {"running": False, "error": f"command not found: {DSH_COMMAND}", "cmd": cmd}
    except Exception as e:
        return {"running": False, "error": str(e), "cmd": cmd}

    # 等待端口上线，最多 10 秒
    for _ in range(50):
        if _upstream_alive(timeout=0.2):
            return status() | {"spawned": True}
        if _process.poll() is not None:
            break
        time.sleep(0.2)

    # 端口没上线：收集日志后清理
    log_tail = _tail_log()
    _terminate_process_tree(_process)
    _process = None

    return {
        "running": False,
        "error": "DSH 启动后未在预期时间内监听端口",
        "cmd": cmd,
        "workdir": str(workdir),
        "run_as": (run_as["name"] if run_as else None),
        "log_path": str(DSH_LOG_PATH),
        "log_tail": log_tail,
    }


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """
    终止进程树：Windows 使用 taskkill /F /T，其它平台使用 terminate/kill。
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                check=False,
                capture_output=True,
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
    except Exception:
        pass


def stop() -> dict:
    """停止由本模块拉起的 dsh 子进程（含可能产生的子进程）。"""
    global _process
    if _process is None or _process.poll() is not None:
        _process = None
        return {"running": False}
    _terminate_process_tree(_process)
    _process = None
    return {"running": False}


def status() -> dict:
    """返回当前进程运行状态（只读，不触发建账户/root 校验）。"""
    running = _process is not None and _process.poll() is None
    return {
        "running": running,
        "pid": _process.pid if running else None,
        "command": DSH_COMMAND,
        "workdir": str(DSH_WORKDIR),
        "run_as": (DSH_RUN_AS_USER or None),
        "platform": ("windows" if IS_WINDOWS else "posix"),
        "log_path": str(DSH_LOG_PATH),
    }
