"""会话管理的单元测试（基于隔离 cache.db）。"""
import time

from app.core import session


class TestSessionLifecycle:
    def test_create_and_resolve(self, make_user):
        user = make_user()
        sid = session.create_session(user["id"])
        assert sid
        assert session.get_session_user_id(sid) == user["id"]

    def test_unknown_sid_returns_none(self, isolated_db):
        assert session.get_session_user_id("no-such-sid") is None

    def test_empty_sid_returns_none(self, isolated_db):
        assert session.get_session_user_id("") is None
        assert session.get_session_user_id(None) is None

    def test_new_session_kicks_old(self, make_user):
        user = make_user()
        sid1 = session.create_session(user["id"])
        sid2 = session.create_session(user["id"])
        # 旧会话被踢掉
        assert session.get_session_user_id(sid1) is None
        assert session.get_session_user_id(sid2) == user["id"]

    def test_delete_session(self, make_user):
        user = make_user()
        sid = session.create_session(user["id"])
        session.delete_session(sid)
        assert session.get_session_user_id(sid) is None


class TestCleanup:
    def test_cleanup_removes_expired(self, make_user, monkeypatch):
        from app.core.db.db import get_cache_conn
        user = make_user()
        conn = get_cache_conn()
        # 手动插入一条已过期的会话
        conn.execute(
            "INSERT INTO sessions (sid, user_id, expire_ts) VALUES (?, ?, ?)",
            ("expired-sid", user["id"], time.time() - 10),
        )
        conn.commit()
        removed = session.cleanup_expired_sessions()
        assert removed >= 1
        assert session.get_session_user_id("expired-sid") is None
