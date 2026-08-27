"""db_op 业务 CRUD 的单元测试（隔离 data.db）。"""
import time

from app.core.db import db_op


class TestGroups:
    def test_seed_groups_exist(self, isolated_db):
        names = {g["name"] for g in db_op.list_groups()}
        assert {"admin", "user"} <= names

    def test_create_and_get(self, isolated_db):
        g = db_op.create_group("vip", is_admin=False, quota_limit=500)
        assert db_op.get_group_by_id(g["id"])["quota_limit"] == 500
        assert db_op.get_group_by_name("vip")["id"] == g["id"]

    def test_update_group_partial(self, isolated_db):
        g = db_op.create_group("g1")
        assert db_op.update_group(g["id"], quota_limit=99) is True
        assert db_op.get_group_by_id(g["id"])["quota_limit"] == 99

    def test_update_group_noop_returns_false(self, isolated_db):
        g = db_op.create_group("g2")
        assert db_op.update_group(g["id"]) is False

    def test_delete_group(self, isolated_db):
        g = db_op.create_group("g3")
        assert db_op.delete_group(g["id"]) is True
        assert db_op.get_group_by_id(g["id"]) is None

    def test_count_users_in_group(self, make_user, normal_group):
        make_user()
        make_user()
        assert db_op.count_users_in_group(normal_group["id"]) == 2


class TestUsers:
    def test_create_and_get(self, make_user):
        user = make_user(username="alice")
        got = db_op.get_user_by_username("alice")
        assert got["id"] == user["id"]
        assert got["pwd_hash"] != "password-123"  # 已哈希
        assert got["salt"]

    def test_update_password_changes_hash(self, make_user):
        user = make_user()
        old_hash = user["pwd_hash"]
        db_op.update_user(user["id"], password="new-pass")
        assert db_op.get_user_by_id(user["id"])["pwd_hash"] != old_hash

    def test_update_user_unset_sentinel_leaves_quota(self, make_user):
        user = make_user(quota_override=55)
        # 不传 quota_override（UNSET）→ 保持不变
        db_op.update_user(user["id"], status=0)
        assert db_op.get_user_by_id(user["id"])["quota_override"] == 55

    def test_update_user_explicit_none_clears_quota(self, make_user):
        user = make_user(quota_override=55)
        db_op.update_user(user["id"], quota_override=None)
        assert db_op.get_user_by_id(user["id"])["quota_override"] is None

    def test_update_user_set_value(self, make_user):
        user = make_user()
        db_op.update_user(user["id"], quota_override=123)
        assert db_op.get_user_by_id(user["id"])["quota_override"] == 123

    def test_update_user_noop(self, make_user):
        user = make_user()
        assert db_op.update_user(user["id"]) is False

    def test_delete_user(self, make_user):
        user = make_user()
        assert db_op.delete_user(user["id"]) is True
        assert db_op.get_user_by_id(user["id"]) is None

    def test_effective_quota_override_wins(self, make_user, normal_group):
        db_op.update_group(normal_group["id"], quota_limit=10)
        user = make_user(quota_override=7)
        assert db_op.get_effective_user_quota(user) == 7

    def test_effective_quota_group_fallback(self, make_user, normal_group):
        db_op.update_group(normal_group["id"], quota_limit=10)
        user = make_user()
        assert db_op.get_effective_user_quota(user) == 10


class TestSettings:
    def test_get_default(self, isolated_db):
        assert db_op.get_setting("missing", "fallback") == "fallback"

    def test_set_and_get(self, isolated_db):
        db_op.set_setting("k", "v")
        assert db_op.get_setting("k") == "v"

    def test_upsert_overwrites(self, isolated_db):
        db_op.set_setting("k", "v1")
        db_op.set_setting("k", "v2")
        assert db_op.get_setting("k") == "v2"


class TestUsageRecords:
    def test_get_usage_empty(self, isolated_db):
        row = db_op.get_usage(1, 12345)
        assert row["total_tokens"] == 0
        assert row["user_id"] == 1

    def test_add_usage_upsert_accumulates(self, isolated_db):
        db_op.add_usage(1, 100, 5, 5)
        db_op.add_usage(1, 100, 1, 1)
        row = db_op.get_usage(1, 100)
        assert row["total_tokens"] == 12

    def test_list_window_excludes_global_pool(self, isolated_db):
        db_op.add_usage(0, 100, 3, 3)   # 全局池行
        db_op.add_usage(7, 100, 2, 2)   # 用户行
        rows = db_op.list_window_usage(100)
        assert all(r["user_id"] != 0 for r in rows)
        assert len(rows) == 1

    def test_delete_window_usage(self, isolated_db):
        db_op.add_usage(1, 100, 5, 5)
        db_op.add_usage(0, 100, 5, 5)
        n = db_op.delete_window_usage(100)
        assert n == 2
        assert db_op.get_usage(1, 100)["total_tokens"] == 0


class TestInvites:
    def test_create_and_get(self, isolated_db, normal_group):
        inv = db_op.create_invite(normal_group["id"], created_by=None, max_uses=2)
        got = db_op.get_invite_by_code(inv["code"])
        assert got["max_uses"] == 2
        assert got["used_count"] == 0

    def test_consume_increments(self, isolated_db, normal_group):
        inv = db_op.create_invite(normal_group["id"], None, max_uses=2)
        first = db_op.consume_invite(inv["code"])
        assert first["used_count"] == 1

    def test_consume_exhausted_returns_none(self, isolated_db, normal_group):
        inv = db_op.create_invite(normal_group["id"], None, max_uses=1)
        assert db_op.consume_invite(inv["code"]) is not None
        assert db_op.consume_invite(inv["code"]) is None

    def test_consume_expired_returns_none(self, isolated_db, normal_group):
        from datetime import datetime
        past = datetime.fromtimestamp(time.time() - 100).isoformat()
        inv = db_op.create_invite(normal_group["id"], None, max_uses=1, expires_at=past)
        assert db_op.consume_invite(inv["code"]) is None

    def test_consume_unknown_returns_none(self, isolated_db):
        assert db_op.consume_invite("NOPE") is None

    def test_record_use_and_list_usernames(self, isolated_db, normal_group, make_user):
        inv = db_op.create_invite(normal_group["id"], None, max_uses=3)
        u1 = make_user(username="u1")
        u2 = make_user(username="u2")
        db_op.record_invite_use(inv["id"], u1["id"])
        db_op.record_invite_use(inv["id"], u2["id"])
        names = db_op.list_invite_usernames(inv["id"])
        assert set(names) == {"u1", "u2"}

    def test_delete_invite(self, isolated_db, normal_group):
        inv = db_op.create_invite(normal_group["id"], None)
        assert db_op.delete_invite(inv["code"]) is True
        assert db_op.get_invite_by_code(inv["code"]) is None
