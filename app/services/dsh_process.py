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
import subprocess
from pathlib import Path

from app.config import DSH_COMMAND

# DSH 的独立工作目录（用于隔离 .env，避免与网关 .env 冲突）
DSH_WORKDIR = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh")))

_process: subprocess.Popen | None = None


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
        # 尝试 .cmd/.bat/.exe 后缀
        for suffix in (".cmd", ".bat", ".exe"):
            exe = shutil.which(exe_name + suffix)
            if exe:
                break
    if exe is None:
        return None
    if exe.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", exe, *parts[1:]]
    return [exe, *parts[1:]]


def start() -> dict:
    """
    启动 dsh 子进程。若已在运行则返回其状态。
    """
    global _process
    if _process is not None and _process.poll() is None:
        return status()

    cmd = _build_cmd()
    if cmd is None:
        return {"running": False, "error": f"command not found: {DSH_COMMAND}"}

    try:
        DSH_WORKDIR.mkdir(parents=True, exist_ok=True)
        _process = subprocess.Popen(
            cmd,
            cwd=str(DSH_WORKDIR),  # 关键：隔离 .env，避免读到网关配置
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return status()
    except FileNotFoundError:
        return {"running": False, "error": f"command not found: {DSH_COMMAND}"}
    except Exception as e:
        return {"running": False, "error": str(e)}


def stop() -> dict:
    """
    停止 dsh 子进程。
    """
    global _process
    if _process is None or _process.poll() is not None:
        _process = None
        return {"running": False}
    try:
        _process.terminate()
        _process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _process.kill()
    except Exception:
        pass
    _process = None
    return {"running": False}


def status() -> dict:
    """
    返回当前进程运行状态。
    """
    running = _process is not None and _process.poll() is None
    return {
        "running": running,
        "pid": _process.pid if running else None,
        "command": DSH_COMMAND,
    }
