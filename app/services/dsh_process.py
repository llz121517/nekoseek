# app/services/dsh_process.py
"""
DSH 进程托管（subprocess 启停，无 profile 管理）

平台分支（fork 点）：
- Windows：维持现状 —— 以当前用户直接拉起，cwd 用 DSH_HOME（默认 ~/.dsh）。
- Linux：以独立账户（DSH_RUN_AS_USER，默认 nekoseek-dsh）降权运行，通过
  preexec_fn 在子进程 exec 前完成 setgid/initgroups/setuid，并把 HOME 指向
  该账户家目录，让 DSH 的 ~/.dsh 落在专用账户下，从而与网关自身文件隔离，
  防止 DSH 被操控后改写网关文件。需要 root 启动才能降权；非 root 时按当前
  用户运行（隔离不生效，仅警告）。

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

DSH_WORKDIR = Path(DSH_HOME)
# 日志固定放项目根目录，与 DSH 工作目录解耦：Linux 降权后网关（root）仍能读自己的日志。
DSH_LOG_PATH = ROOT / "dsh.log"
DSH_ENV_PATH = DSH_WORKDIR / ".env"
DS_KEY_NAME = "DEEPSEEK_API_KEY"
_process: subprocess.Popen | None = None


def _resolve_run_as() -> dict | None:
    """
    Linux 下解析降权目标账户，返回 {"uid","gid","home","name"}；不需要降权时返回 None。

    - Windows：恒为 None（维持当前逻辑）。
    - 未配置 DSH_RUN_AS_USER：None（按当前用户运行）。
    - 已是 root：返回目标账户信息，供 preexec_fn 降权。
    - 非 root：账户与当前一致则 None；否则降级为当前用户并告警。
    """
    if IS_WINDOWS or not DSH_RUN_AS_USER:
        return None

    import pwd  # 仅 POSIX 可用

    try:
        pw = pwd.getpwnam(DSH_RUN_AS_USER)
    except KeyError:
        logger.warning(
            "DSH_RUN_AS_USER=%s 不存在，按当前用户运行 DSH（隔离不生效）。"
            "可先 useradd -r -m %s 后重启。",
            DSH_RUN_AS_USER, DSH_RUN_AS_USER,
        )
        return None

    euid = os.geteuid()
    if euid == pw.pw_uid:
        # 已经是目标账户，无需降权
        return {"uid": pw.pw_uid, "gid": pw.pw_gid, "home": pw.pw_dir.rstrip("/") or "/", "name": pw.pw_name}
    if euid != 0:
        logger.warning(
            "非 root 启动，无法降权到 %s，按当前用户(uid=%d)运行 DSH（隔离不生效）。",
            DSH_RUN_AS_USER, euid,
        )
        return None

    # 去掉尾部斜杠，避免拼出 ".../.dsh" 时产生 "...//.dsh" 之类的路径
    return {"uid": pw.pw_uid, "gid": pw.pw_gid, "home": pw.pw_dir.rstrip("/") or "/", "name": pw.pw_name}


def _make_preexec(run_as: dict):
    """生成 preexec_fn：在子进程 exec 前切换到目标账户（setgid/initgroups/setuid）。"""
    def _demote():
        os.setgid(run_as["gid"])
        try:
            os.initgroups(run_as["name"], run_as["gid"])
        except OSError:
            pass
        os.setuid(run_as["uid"])
    return _demote


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

    # —— 平台 fork 点：Linux 降权到独立账户，Windows 维持当前用户 ——
    run_as = _resolve_run_as()

    # Linux 降权时，cwd 固定为目标账户的 ~/.dsh（与隔离 HOME 一致，不回退）；
    # Windows / 未降权时用 DSH_WORKDIR（默认当前用户 ~/.dsh）。
    if run_as is not None:
        workdir = Path(run_as["home"]) / ".dsh"
        preexec = _make_preexec(run_as)
    else:
        workdir = DSH_WORKDIR
        preexec = None

    try:
        # 目录由当前（可能是 root）进程创建，随后把属主交给目标账户，
        # 否则降权后的 DSH 无权写入该目录。
        workdir.mkdir(parents=True, exist_ok=True)
        if run_as is not None:
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
            preexec_fn=preexec,  # Windows 上为 None，忽略
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
