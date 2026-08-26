# app/services/dsh_process.py
"""
DSH 进程托管（subprocess 启停，无 profile 管理）

注意：DSH 启动时会读取「当前工作目录」下的 .env 文件，且会校验其中的
DEEPSEEK_BASE_URL 等启动级变量只能来自启动 shell。因此必须给 DSH 一个
独立的 cwd（不含本项目 .env 的目录），避免读到网关自己的配置而崩溃。
"""
import os
import shlex
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from app.config import DSH_COMMAND, DSH_UPSTREAM, DSH_HOME

DSH_WORKDIR = Path(DSH_HOME)
DSH_LOG_PATH = DSH_WORKDIR / "dsh.log"
_process: subprocess.Popen | None = None


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

    try:
        DSH_WORKDIR.mkdir(parents=True, exist_ok=True)
        log_file = open(DSH_LOG_PATH, "a", encoding="utf-8")
        _process = subprocess.Popen(
            cmd,
            cwd=str(DSH_WORKDIR),
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
        "workdir": str(DSH_WORKDIR),
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
    return {
        "running": running,
        "pid": _process.pid if running else None,
        "command": DSH_COMMAND,
        "workdir": str(DSH_WORKDIR),
        "log_path": str(DSH_LOG_PATH),
    }
