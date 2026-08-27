# app/services/dsh_process.py
"""
DSH 进程托管（subprocess 启停，无 profile 管理）

平台分支（fork 点）：
- Windows：维持现状 —— 以当前用户直接拉起，cwd 用 DSH_HOME（默认 ~/.dsh）。
- Linux：网关应以普通账户（如 nekoseek）常驻运行，通过 `sudo -u <DSH_RUN_AS_USER>`
  以独立账户拉起 DSH，使其 ~/.dsh 落在专用账户家目录下，与网关自身文件隔离，
  防止 DSH 被操控后改写网关文件。需要为该账户配置 sudo 免密授权，例如 visudo：

      nekoseek ALL=(nekoseek-dsh) NOPASSWD: /usr/local/bin/dsh web

  无法以独立账户托管（账户不存在 / sudo 不可用或未授权）时直接抛 DSHIsolationError，
  不静默回退到当前账户——宁可失败，也不在失去文件隔离的情况下运行。

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


def _resolve_run_as() -> dict | None:
    """
    Linux 下解析降权目标账户，返回 {"uid","gid","home","name"}；Windows 返回 None。

    运行模型：网关以普通账户常驻，经 `sudo -u <账户>` 把 DSH 拉起到独立账户。
    除「当前进程已是目标账户」这一正常情形外，其余无法降权的情况一律抛
    DSHIsolationError，不静默回退——隔离失效时宁可不启动。
    """
    if IS_WINDOWS or not DSH_RUN_AS_USER:
        return None

    import pwd  # 仅 POSIX 可用

    try:
        pw = pwd.getpwnam(DSH_RUN_AS_USER)
    except KeyError:
        raise DSHIsolationError(
            f"DSH 独立账户 {DSH_RUN_AS_USER!r} 不存在。"
            f"请先创建：sudo useradd -r -m {DSH_RUN_AS_USER}"
        )

    home = pw.pw_dir.rstrip("/") or "/"
    info = {"uid": pw.pw_uid, "gid": pw.pw_gid, "home": home, "name": pw.pw_name}

    euid = os.geteuid()
    if euid == pw.pw_uid:
        # 当前进程已是目标账户，sudo -u 仍是安全的恒等切换，直接降权即可。
        return info
    # 其余（root / 其他普通账户）统一走 sudo -u 降权，由 _build_sudo_cmd 校验授权。
    return info


def _build_sudo_cmd(cmd: list[str], run_as: dict) -> list[str]:
    """把 dsh 命令包装成 `sudo -u <账户> ...`，并先做非交互授权自检。"""
    sudo = shutil.which("sudo")
    if sudo is None:
        raise DSHIsolationError(
            "未找到 sudo，无法以独立账户托管 DSH。"
            "请安装 sudo 并配置免密授权，例如 visudo 添加：\n"
            f"    nekoseek ALL=(nekoseek-dsh) NOPASSWD: {DSH_COMMAND}"
        )
    # 非交互自检：sudo -n true 通过才说明 NOPASSWD 授权已配置。
    probe = subprocess.run(
        [sudo, "-n", "-u", run_as["name"], "true"],
        capture_output=True,
    )
    if probe.returncode != 0:
        raise DSHIsolationError(
            f"sudo 免密降权到 {run_as['name']!r} 失败（{probe.stderr.decode(errors='replace').strip() or '未被授权'}）。\n"
            "请用 visudo 为网关账户配置免密授权，例如：\n"
            f"    nekoseek ALL=({run_as['name']}) NOPASSWD: {DSH_COMMAND}"
        )
    return [sudo, "-n", "-u", run_as["name"], "--", *cmd]


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
        # 降权后由 dsh 基于目标账户的 HOME 自建 ~/.dsh，不透传网关的 DSH_HOME。
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
    """
    parts = shlex.split(DSH_COMMAND)
    if not parts:
        return None
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

    # —— 平台 fork 点：Linux 经 sudo 降权到独立账户，Windows 维持当前用户 ——
    # 无法隔离时 _resolve_run_as / _build_sudo_cmd 直接抛 DSHIsolationError，
    # 由调用方（main.lifespan）中止启动，不在失去隔离的情况下运行。
    run_as = _resolve_run_as()

    if run_as is not None:
        cmd = _build_sudo_cmd(cmd, run_as)
        workdir = Path(run_as["home"]) / ".dsh"
    else:
        workdir = DSH_WORKDIR

    try:
        # 目录由当前进程创建；降权时需把属主交给目标账户，
        # 否则降权后的 DSH 无权写入该目录。
        workdir.mkdir(parents=True, exist_ok=True)
        if run_as is not None and os.geteuid() == 0:
            try:
                os.chown(workdir, run_as["uid"], run_as["gid"])
            except OSError:
                logger.warning("chown %s 到 %s 失败", workdir, run_as["name"], exc_info=True)

        log_file = open(DSH_LOG_PATH, "a", encoding="utf-8")
        _process = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            env=_build_child_env(run_as),
            stdout=log_file,
            stderr=subprocess.STDOUT,
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
    """返回当前进程运行状态。"""
    running = _process is not None and _process.poll() is None
    run_as = _resolve_run_as()
    return {
        "running": running,
        "pid": _process.pid if running else None,
        "command": DSH_COMMAND,
        "workdir": str(DSH_WORKDIR),
        "run_as": (run_as["name"] if run_as else None),
        "platform": ("windows" if IS_WINDOWS else "posix"),
        "log_path": str(DSH_LOG_PATH),
    }
