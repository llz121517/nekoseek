"""认证接口的集成测试（TestClient + 真实路由）。"""


class TestRegister:
    def test_register_requires_invite(self, auth_client):
        r = auth_client.post("/api/v1/auth/register", json={
            "username": "newbie", "password": "secret123",
        })
        assert r.status_code == 422  # 缺 invite_code 字段校验失败

    def test_register_with_invalid_invite(self, auth_client):
        r = auth_client.post("/api/v1/auth/register", json={
            "username": "newbie", "password": "secret123", "invite_code": "BADCODE",
        })
        assert r.json()["code"] == 0

    def test_register_consumes_invite(self, auth_client, admin_user, normal_group):
        from app.core.db import db_op
        inv = db_op.create_invite(normal_group["id"], admin_user["id"], max_uses=1)
        r = auth_client.post("/api/v1/auth/register", json={
            "username": "newbie", "password": "secret123", "invite_code": inv["code"],
        })
        assert r.json()["code"] == 1
        user = db_op.get_user_by_username("newbie")
        assert user is not None
        assert user["group_id"] == normal_group["id"]
        # 邀请码已被消费
        assert db_op.get_invite_by_code(inv["code"])["used_count"] == 1

    def test_register_duplicate_username(self, auth_client, make_user):
        make_user(username="taken")
        from app.core.db import db_op
        inv = db_op.create_invite(2, None, max_uses=5)
        r = auth_client.post("/api/v1/auth/register", json={
            "username": "taken", "password": "secret123", "invite_code": inv["code"],
        })
        assert r.json()["code"] == 0
        assert "已存在" in r.json()["msg"]


class TestLogin:
    def test_login_success_sets_cookie(self, auth_client, make_user):
        user = make_user(username="bob")
        r = auth_client.post("/api/v1/auth/login", json={
            "username": "bob", "password": "password-123",
        })
        assert r.json()["code"] == 1
        assert "session_id" in r.cookies

    def test_login_wrong_password(self, auth_client, make_user):
        make_user(username="carol")
        r = auth_client.post("/api/v1/auth/login", json={
            "username": "carol", "password": "wrong-pass",
        })
        assert r.json()["code"] == 0
        assert "session_id" not in r.cookies

    def test_login_unknown_user(self, auth_client):
        r = auth_client.post("/api/v1/auth/login", json={
            "username": "ghost", "password": "whatever1",
        })
        assert r.json()["code"] == 0

    def test_login_disabled_account(self, auth_client, make_user):
        from app.core.db import db_op
        user = make_user(username="dave")
        db_op.update_user(user["id"], status=0)
        r = auth_client.post("/api/v1/auth/login", json={
            "username": "dave", "password": "password-123",
        })
        assert r.json()["code"] == 0


class TestLogoutAndCheck:
    def test_check_logged_out(self, auth_client):
        r = auth_client.get("/api/v1/auth/check")
        assert r.json()["data"]["logged_in"] is False

    def test_check_logged_in(self, auth_client, make_user):
        user = make_user(username="erin")
        cookie = auth_client.login_cookie(user["id"])
        r = auth_client.get("/api/v1/auth/check", cookies=cookie)
        data = r.json()["data"]
        assert data["logged_in"] is True
        assert data["username"] == "erin"

    def test_admin_flag_in_check(self, auth_client, admin_user):
        cookie = auth_client.login_cookie(admin_user["id"])
        r = auth_client.get("/api/v1/auth/check", cookies=cookie)
        assert r.json()["data"]["is_admin"] is True

    def test_logout_clears_session(self, auth_client, make_user):
        user = make_user(username="frank")
        cookie = auth_client.login_cookie(user["id"])
        r = auth_client.post("/api/v1/auth/logout", cookies=cookie)
        assert r.json()["code"] == 1
        # 会话已删除，旧 cookie 不再有效
        r2 = auth_client.get("/api/v1/auth/check", cookies=cookie)
        assert r2.json()["data"]["logged_in"] is False
