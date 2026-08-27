"""管理后台接口的集成测试（TestClient + admin 会话）。"""
import pytest

from app.core.db import db_op


@pytest.fixture()
def admin_cookie(auth_client, admin_user):
    return auth_client.login_cookie(admin_user["id"])


class TestAdminGuard:
    def test_requires_auth(self, auth_client):
        r = auth_client.get("/api/v1/admin/users")
        assert r.status_code == 401

    def test_requires_admin(self, auth_client, make_user):
        user = make_user()
        cookie = auth_client.login_cookie(user["id"])
        r = auth_client.get("/api/v1/admin/users", cookies=cookie)
        assert r.status_code == 403


class TestGroupsApi:
    def test_list_groups(self, auth_client, admin_cookie):
        r = auth_client.get("/api/v1/admin/groups", cookies=admin_cookie)
        names = {g["name"] for g in r.json()["data"]}
        assert {"admin", "user"} <= names

    def test_create_group(self, auth_client, admin_cookie):
        r = auth_client.post("/api/v1/admin/groups", cookies=admin_cookie, json={
            "name": "vip", "is_admin": False, "quota_limit": 100,
        })
        assert r.json()["code"] == 1
        assert db_op.get_group_by_name("vip")["quota_limit"] == 100

    def test_create_duplicate_group(self, auth_client, admin_cookie):
        db_op.create_group("dup")
        r = auth_client.post("/api/v1/admin/groups", cookies=admin_cookie, json={"name": "dup"})
        assert r.json()["code"] == 0

    def test_delete_group_with_users_rejected(self, auth_client, admin_cookie, make_user, normal_group):
        make_user()
        r = auth_client.delete(f"/api/v1/admin/groups/{normal_group['id']}", cookies=admin_cookie)
        assert r.json()["code"] == 0

    def test_delete_empty_group(self, auth_client, admin_cookie):
        g = db_op.create_group("empty")
        r = auth_client.delete(f"/api/v1/admin/groups/{g['id']}", cookies=admin_cookie)
        assert r.json()["code"] == 1


class TestUsersApi:
    def test_list_users_strips_secrets(self, auth_client, admin_cookie):
        r = auth_client.get("/api/v1/admin/users", cookies=admin_cookie)
        for u in r.json()["data"]:
            assert "pwd_hash" not in u
            assert "salt" not in u

    def test_update_user_quota_override(self, auth_client, admin_cookie, make_user):
        user = make_user()
        r = auth_client.put(f"/api/v1/admin/users/{user['id']}", cookies=admin_cookie, json={
            "quota_override": 321,
        })
        assert r.json()["code"] == 1
        assert db_op.get_user_by_id(user["id"])["quota_override"] == 321

    def test_update_user_clear_quota_with_null(self, auth_client, admin_cookie, make_user):
        user = make_user(quota_override=321)
        r = auth_client.put(f"/api/v1/admin/users/{user['id']}", cookies=admin_cookie, json={
            "quota_override": None,
        })
        assert r.json()["code"] == 1
        assert db_op.get_user_by_id(user["id"])["quota_override"] is None

    def test_cannot_delete_self(self, auth_client, admin_cookie, admin_user):
        r = auth_client.delete(f"/api/v1/admin/users/{admin_user['id']}", cookies=admin_cookie)
        assert r.json()["code"] == 0

    def test_cannot_remove_last_admin(self, auth_client, admin_cookie, admin_user):
        # admin_user 是唯一管理员，降级其组应被拒绝
        normal = db_op.get_group_by_name("user")
        r = auth_client.put(f"/api/v1/admin/users/{admin_user['id']}", cookies=admin_cookie, json={
            "group_id": normal["id"],
        })
        assert r.json()["code"] == 0


class TestInvitesApi:
    def test_create_and_list_invite(self, auth_client, admin_cookie, normal_group):
        r = auth_client.post("/api/v1/admin/invites", cookies=admin_cookie, json={
            "group_id": normal_group["id"], "max_uses": 3,
        })
        assert r.json()["code"] == 1
        code = r.json()["data"]["code"]

        lst = auth_client.get("/api/v1/admin/invites", cookies=admin_cookie).json()["data"]
        found = next(i for i in lst if i["code"] == code)
        assert found["max_uses"] == 3
        assert found["used_by_users"] == []

    def test_list_invite_shows_usernames(self, auth_client, admin_cookie, normal_group, make_user):
        inv = db_op.create_invite(normal_group["id"], admin_cookie and 1, max_uses=2)
        user = make_user(username="invited")
        db_op.record_invite_use(inv["id"], user["id"])
        lst = auth_client.get("/api/v1/admin/invites", cookies=admin_cookie).json()["data"]
        found = next(i for i in lst if i["code"] == inv["code"])
        assert "invited" in found["used_by_users"]


class TestQuotaApi:
    def test_get_quota_settings(self, auth_client, admin_cookie):
        r = auth_client.get("/api/v1/admin/quota/settings", cookies=admin_cookie)
        data = r.json()["data"]
        assert data["window"] == "day"
        assert "window_kinds" in data

    def test_update_window(self, auth_client, admin_cookie):
        r = auth_client.put("/api/v1/admin/quota/settings", cookies=admin_cookie, json={
            "window": "week",
        })
        assert r.json()["code"] == 1
        assert r.json()["data"]["window"] == "week"

    def test_update_window_invalid(self, auth_client, admin_cookie):
        r = auth_client.put("/api/v1/admin/quota/settings", cookies=admin_cookie, json={
            "window": "year",
        })
        assert r.json()["code"] == 0

    def test_set_global_limit(self, auth_client, admin_cookie):
        auth_client.put("/api/v1/admin/quota/settings", cookies=admin_cookie, json={
            "global_limit": 999,
        })
        r = auth_client.get("/api/v1/admin/quota/settings", cookies=admin_cookie)
        assert r.json()["data"]["global_limit"] == 999

    def test_quota_usage_includes_usernames(self, auth_client, admin_cookie, make_user):
        from app.core import quota
        user = make_user(username="heavy")
        quota.record_usage(user["id"], input_tokens=50, output_tokens=10)
        r = auth_client.get("/api/v1/admin/quota/usage", cookies=admin_cookie)
        users = r.json()["data"]["users"]
        heavy = next(u for u in users if u["username"] == "heavy")
        assert heavy["total_tokens"] == 60

    def test_reset_quota(self, auth_client, admin_cookie, make_user):
        from app.core import quota
        user = make_user()
        quota.record_usage(user["id"], input_tokens=10)
        r = auth_client.post("/api/v1/admin/quota/reset", cookies=admin_cookie)
        assert r.json()["code"] == 1
        assert quota.get_user_usage(user["id"])["total_tokens"] == 0


class TestStatsApi:
    def test_overview_shape(self, auth_client, admin_cookie):
        r = auth_client.get("/api/v1/admin/stats/overview", cookies=admin_cookie)
        data = r.json()["data"]
        assert "today" in data and "all_time" in data
        assert "total_tokens" in data["today"]

    def test_hourly_zero_filled(self, auth_client, admin_cookie):
        r = auth_client.get("/api/v1/admin/stats/hourly?days=1", cookies=admin_cookie)
        points = r.json()["data"]["points"]
        assert len(points) == 24  # 1 天零填充 24 个整点

    def test_by_user(self, auth_client, admin_cookie, make_user):
        from app.core import quota
        user = make_user(username="statter")
        quota.record_usage(user["id"], input_tokens=7, output_tokens=3)
        r = auth_client.get("/api/v1/admin/stats/by_user?days=1", cookies=admin_cookie)
        users = r.json()["data"]["users"]
        row = next(u for u in users if u["username"] == "statter")
        assert row["total_tokens"] == 10
