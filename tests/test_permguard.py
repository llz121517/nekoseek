"""启动期权限加固（permguard）的测试。

POSIX 权限语义仅 Linux 生效，故真实 chmod 断言用例在非 Linux 上跳过；
noop / 失败中止两条逻辑与平台无关，全平台可跑。
"""
import os
import sys

import pytest

from app import config
from app.core import permguard

IS_LINUX = sys.platform.startswith("linux")


@pytest.fixture()
def linux_platform(monkeypatch):
    """把平台伪装成 Linux，使 harden() 进入真实逻辑分支。"""
    monkeypatch.setattr(sys, "platform", "linux")


def test_non_linux_is_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    # 不应抛错，也不应创建/改动任何东西
    permguard.harden()
    assert not (tmp_path / "data").exists()


@pytest.mark.skipif(not IS_LINUX, reason="POSIX 权限语义")
def test_env_fixed_to_600(linux_platform, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    env = tmp_path / ".env"
    env.write_text("ADMIN_PASSWORD=secret\n")
    os.chmod(env, 0o644)
    permguard.harden()
    assert (env.stat().st_mode & 0o777) == 0o600


@pytest.mark.skipif(not IS_LINUX, reason="POSIX 权限语义")
def test_data_dir_fixed_to_700(linux_platform, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    os.chmod(data, 0o755)
    permguard.harden()
    assert (data.stat().st_mode & 0o777) == 0o700


@pytest.mark.skipif(not IS_LINUX, reason="POSIX 权限语义")
def test_missing_env_skipped_and_data_created(linux_platform, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    # .env 不存在：跳过不报错；data/ 不存在：创建并收紧
    permguard.harden()
    data = tmp_path / "data"
    assert data.is_dir()
    assert (data.stat().st_mode & 0o777) == 0o700


@pytest.mark.skipif(not IS_LINUX, reason="POSIX 权限语义")
def test_already_correct_left_alone(linux_platform, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    env = tmp_path / ".env"
    env.write_text("x")
    os.chmod(env, 0o600)
    mtime_before = env.stat().st_mtime_ns
    permguard.harden()
    assert env.stat().st_mtime_ns == mtime_before  # 未被触碰


def test_fix_failure_raises(linux_platform, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)

    def _boom(path, mode):
        raise OSError("not owner")

    monkeypatch.setattr(os, "chmod", _boom)
    with pytest.raises(RuntimeError, match="root"):
        permguard.harden()