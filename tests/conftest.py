"""
pytest 共享基建：把三个 SQLite 库重定向到 tmp 目录，避免测试读写真实 data/db。

config.py 在 import 时就把 DB 路径固化成模块常量，db.py 又用 thread-local 缓存连接，
因此必须在测试里同时补丁「config 常量 + db 模块属性 + 清空 thread-local 连接」。
"""
import os
import uuid

import pytest

# 在任何 app 模块导入前，先提供播种初始管理员所需的口令。
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

from app import config  # noqa: E402
from app.core.db import db as db_module  # noqa: E402
from app.core.db import init_db  # noqa: E402
from app.core.db import db_op  # noqa: E402
from app.core.session import create_session  # noqa: E402
from app.config import SESSION_COOKIE_KEY  # noqa: E402


def _reset_thread_locals():
    """丢弃已缓存的 thread-local 连接，让下一次取用指向新的 tmp 库。"""
    for local in (db_module._data_local, db_module._cache_local, db_module._stats_local):
        if hasattr(local, "conn"):
            try:
                local.conn.close()
            except Exception:
                pass
            delattr(local, "conn")


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """
    把 DATA/CACHE/STATS 三个库路径指向 tmp_path，重建表并播种默认数据。
    返回 tmp 库目录。依赖此 fixture 的测试互相之间完全隔离。
    """
    db_dir = tmp_path / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    data = db_dir / "data.db"
    cache = db_dir / "cache.db"
    stats = db_dir / "stats.db"

    for target in (config, db_module, init_db):
        monkeypatch.setattr(target, "DATA_DB_PATH", data, raising=False)
        monkeypatch.setattr(target, "CACHE_DB_PATH", cache, raising=False)
        monkeypatch.setattr(target, "STATS_DB_PATH", stats, raising=False)
        monkeypatch.setattr(target, "DB_DIR", db_dir, raising=False)

    _reset_thread_locals()
    init_db.init_db()
    yield db_dir
    _reset_thread_locals()


@pytest.fixture()
def admin_user(isolated_db):
    """播种出来的初始管理员（init_db 已创建）。"""
    return db_op.get_user_by_username(config.ADMIN_USERNAME)


@pytest.fixture()
def normal_group(isolated_db):
    return db_op.get_group_by_name("user")


@pytest.fixture()
def admin_group(isolated_db):
    return db_op.get_group_by_name("admin")


@pytest.fixture()
def make_user(isolated_db, normal_group):
    """工厂：快速创建普通用户。"""
    def _make(username=None, group_id=None, quota_override=None):
        username = username or f"user-{uuid.uuid4().hex[:8]}"
        return db_op.create_user(
            username,
            "password-123",
            group_id if group_id is not None else normal_group["id"],
            quota_override=quota_override,
        )
    return _make


@pytest.fixture()
def auth_client(isolated_db, monkeypatch):
    """
    提供 FastAPI TestClient 与按用户登录的 cookie 工厂。
    禁用 DSH 自动拉起，避免测试触碰子进程；usage_meter 替换为不联网的空操作，
    避免 lifespan 起一条对 DSH_UPSTREAM 的真实重连循环。
    """
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import usage_meter

    monkeypatch.setattr(usage_meter, "start", lambda: None)
    async def _noop_stop():
        return None
    monkeypatch.setattr(usage_meter, "stop", _noop_stop)

    with TestClient(app) as client:
        def login_cookie(user_id: int) -> dict:
            sid = create_session(user_id)
            assert sid, "创建 session 失败"
            return {SESSION_COOKIE_KEY: sid}

        client.login_cookie = login_cookie  # type: ignore[attr-defined]
        yield client
