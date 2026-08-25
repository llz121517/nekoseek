# app/services/dsh_process.py
"""
DSH 进程托管（subprocess 启停，无 profile 管理）
"""
import subprocess
from typing import Any

from app.config import DSH_COMMAND, DSH_ARGS

_process: subprocess.Popen | None = None


def start() -> dict:
    """
    启动 dsh web 子进程。若已在运行则返回其状态。
    """
    global _process
    if _process is not None and _process.poll() is None:
        return status()
    try:
        _process = subprocess.Popen(
            [DSH_COMMAND, *DSH_ARGS],
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
    停止 dsh web 子进程。
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
        "command": f"{DSH_COMMAND} {' '.join(DSH_ARGS)}",
    }
