# tools/diag_dsh.py
"""
DSH 启动诊断脚本：不启动完整网关，单独测试 DSH 子进程托管逻辑。

用法：
    .venv/Scripts/python tools/diag_dsh.py
    # 或
    python tools/diag_dsh.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from app.config import DSH_COMMAND, DSH_UPSTREAM, DSH_HOME
from app.services import dsh_process


def main():
    print("=" * 60)
    print("DSH 启动诊断")
    print("=" * 60)
    print(f"DSH_COMMAND  : {DSH_COMMAND!r}")
    print(f"DSH_UPSTREAM : {DSH_UPSTREAM!r}")
    print(f"DSH_HOME     : {DSH_HOME!r}")
    print(f"工作目录     : {dsh_process.DSH_WORKDIR}")
    print(f"日志文件     : {dsh_process.DSH_LOG_PATH}")

    cmd = dsh_process._build_cmd()
    print(f"\n解析后的命令: {cmd}")
    if cmd is None:
        print("错误：无法定位 dsh 可执行文件。")
        print("请确认 'dsh' 已在 PATH 中，或在 .env 里用绝对路径配置 DSH_COMMAND。")
        return 1

    print("\n尝试启动 DSH...")
    try:
        result = dsh_process.start()
    except dsh_process.DSHIsolationError as e:
        print(f"隔离不可用，无法启动：\n{e}")
        return 1
    print(f"start() 返回: {result}")

    if not result.get("running"):
        print("\n启动失败。日志最后 50 行：")
        print("-" * 60)
        log = dsh_process._tail_log(lines=50)
        print(log or "(日志为空或无法读取)")
        print("-" * 60)
        print("\n常见原因：")
        print("1. dsh 不在 PATH 里 → 配置 DSH_COMMAND=绝对路径\\dsh.exe web")
        print("2. Linux 下需配置 sudo 免密降权，例如 visudo：nekoseek ALL=(nekoseek-dsh) NOPASSWD: ... dsh web")
        print("3. 端口 3080 被占用 → 先杀掉占用进程，或改 DSH_UPSTREAM")
        return 1

    print("\nDSH 已启动，等待 2 秒后检查状态...")
    asyncio.run(asyncio.sleep(2))
    print(f"status(): {dsh_process.status()}")

    print("\n测试停止 DSH...")
    print(f"stop(): {dsh_process.stop()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
